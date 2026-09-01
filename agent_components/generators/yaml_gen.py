"""Phase C: YAML 测试数据生成 + 轮次修复循环 Mixin

拆分自 generators/__init__.py（2026-08-07 大文件拆分）。

依赖宿主类提供: self.llm / self._invoke_think / self._invoke_structured /
                self._load_factory_methods / self._log_node_output
跨 Mixin 依赖: ExcelMixin / TranslationMixin / PyExportMixin / _helpers
生成流程后处理: _repair_helpers（setup 键注入/teardown 容错/阶段合并）
生成后静态校验: validation.case_validator / validation.yaml_validator（2026-09-01 归位）
"""
import json
import os
import re

import yaml

import infrastructure.config as config
from infrastructure.observability import get_logger
from prompts.response_model import TestData
from agent_components.generators._helpers import (
    _summarize_error_patterns,
    _extract_completion_snippet,
    _write_fail_detail,
    _format_post_issues_for_prompt,
)
from agent_components.generators._repair_helpers import (
    _parse_setup_extract_keys,
    _inject_setup_keys_note,
    _merge_stage_results,
    _collect_stage_errors,
    _filter_teardown_missing_pres,
    _relax_teardown_validation,
)
from agent_components.validation.yaml_validator import (
    _find_missing_yaml_refs,
    _scan_missing_key_refs,
)
from prompts.extraction_prompts import (
    SETUP_CAPTURE_RULE,
    YAML_ANALYSIS_GUIDE,
)

logger = get_logger(__name__)




class YamlMixin:
    """YAML 生成（thinking 分析 → json_mode 输出）+ 多轮修复循环"""


    # ------------------------------------------------------------------
    # 共享助手（单节点 _generate_one_yaml_single 专用；原两段式共用说明见 git 历史）
    # ------------------------------------------------------------------
    def _build_annotation_injector(self, api_defs_json: str):
        """构建 pre_validate 闭包：按 url 注入 _annotations + 处理 is_export 空断言。

        两段式（_invoke_structured pre_validate）与单节点（TestData.model_validate 前）
        共用同一注入逻辑。返回 _inject_annotations(parsed) -> parsed。
        """
        api_defs_list = (json.loads(api_defs_json)
                         if api_defs_json and api_defs_json.strip() != "[]" else [])

        def _lookup_api(url: str) -> dict | None:
            """按 url 匹配 API 定义（优先精确匹配，fallback 前缀匹配）。"""
            for api in api_defs_list:
                if api.get("url", "") == url:
                    return api
            for api in api_defs_list:
                api_url = api.get("url", "")
                if api_url and url.startswith(api_url.split("{")[0] if "{" in api_url else api_url):
                    return api
            return None

        def _inject_annotations(parsed: dict) -> dict:
            """pre_validate 回调：注入 _annotations，为 is_export 补齐占位断言。"""
            for step in parsed.get("data", []):
                url = step.get("baseInfo", {}).get("url", "")
                api = _lookup_api(url)
                if api and api.get("annotations"):
                    step["baseInfo"]["_annotations"] = api["annotations"]
                    # is_export: 补齐占位断言防止 Pydantic 校验拦截空 validation
                    if api["annotations"].get("is_export", {}).get("active"):
                        for tc in step.get("testCase", []):
                            if not tc.get("validation"):
                                tc["validation"] = [{"__placeholder_export": True}]
            return parsed

        return _inject_annotations

    def _write_yaml_result(self, result, output_path: str) -> str:
        """写盘前注入（路径参数替换 + 导出断言接管）+ 序列化原子写盘，返回 output_path。

        两段式与单节点共用同一写盘管线。
        """
        for step in result.data:
            annotations = step.baseInfo.get("_annotations", {})
            hp = annotations.get("has_path_params", {})
            if hp.get("active"):
                url = str(step.baseInfo.get("url", ""))
                for param in hp.get("path_params", []):
                    url = url.replace(f"{{{param}}}",
                                      f"${{get_extract_data({param})}}")
                step.baseInfo["url"] = url
        self._takeover_export_assertions(result.data)

        # 序列化写盘（去除 _annotations 元数据字段）
        _clean_steps = []
        for step in result.data:
            _d = step.model_dump(exclude_none=True, by_alias=True)
            # 递归清理 _annotations（在 baseInfo 嵌套 dict 内）
            _bi = _d.get("baseInfo")
            if isinstance(_bi, dict):
                _bi.pop("_annotations", None)
            _clean_steps.append(_d)
        yaml_text = yaml.dump(
            _clean_steps,
            allow_unicode=True, indent=2, default_flow_style=False,
        )
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        tmp_path = output_path + ".tmp"
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        with open(tmp_path, "w", encoding="utf-8") as f:
            f.write(yaml_text)
        os.replace(tmp_path, output_path)
        return output_path

    def _normalize_base_urls(self, result, api_defs_json: str) -> None:
        """URL 前缀保真（2026-09-01 task#15，决策「代码层接管」）。

        v9 实测 getParentList 生成 url 丢 /park-energy-electric-web/ 前缀 → 请求打到
        前端服务器返回 HTML。prompt 既有「url 逐字一致」规则已被 LLM 违反（2026-08-21
        也曾手工补前缀），故改为代码层确定性接管：对每个 baseInfo.url，在注入的
        api_defs 中找「以此为后缀」的接口 URL；命中唯一更长的 DB 版本时用 DB 版本替换
        （即补回 LLM 丢弃的业务前缀）。

        安全守则：
          - 产物 url 已是 DB 完整 url → 无更长的后缀候选 → 不改（幂等）。
          - 后缀命中多个候选（歧义，如裸路径本就存在）→ 跳过不改，宁缺毋滥。
          - 路径参数 url（含 {code} 字面量）后缀不匹配其它接口 → 不改。
        """
        if not api_defs_json or api_defs_json.strip() == "[]":
            return
        try:
            apis = json.loads(api_defs_json)
        except ValueError:
            return
        if not isinstance(apis, list):
            return
        db_urls = [
            str(a.get("url", "") or "").strip().rstrip("/")
            for a in apis if a.get("url")
        ]
        for step in result.data:
            cur = str(step.baseInfo.get("url", "") or "").strip().rstrip("/")
            if not cur:
                continue
            longer = [u for u in db_urls
                      if u != cur and u.endswith(cur)]
            if len(longer) == 1:
                step.baseInfo["url"] = longer[0]

    def _generate_one_yaml_single(self, row: dict, api_defs_json: str, user_ctx: str,
                                  output_path: str, repair_ctx: dict | None = None) -> str:
        """单节点 YAML 生成：thinking + json_object 一次调用生成 TestData。

        两段式 `_generate_one_yaml` 已注释（2026-08-24），本方法为唯一生成路径。
        与原两段式同产物/同写盘管线，仅 LLM 调用合并为一次：
          - thinking 走 reasoning_content（不走 `_invoke_structured` 的 json_mode，
            绕开 METHOD_FEATURES 强制 thinking off），经 `_invoke_think` 的
            reasoning_label 采集落 thinking_trace.log（思考内容监测）；
          - content 直接是 TestData JSON → json.loads + _inject_annotations +
            TestData.model_validate（max_retries=0 语义，失败进修复轮）。

        首轮 data_analysis = YAML_ANALYSIS_GUIDE；修复轮 = 引导 + 错误上下文。
        （原 YAML_SINGLE_NODE 两段式开关随两段式一并注释，`_generate_all_yamls` 恒走本方法。）
        """
        from prompts.extraction_prompts import generate_yaml_data_single_prompt
        from infrastructure.observability import log_thinking

        db_schema = config.DB_SCHEMA  # 数据库表结构（占位，为空禁 db 断言，2026-08-04 问题 2）
        factory_methods_text = self._load_factory_methods()
        test_case_logic = f"执行步骤: {row['steps']}\n预期结果: {row.get('expected', '')}"
        # yaml 格式 schema（TestData 模型 JSON-Schema）：固定内容，注入 system 段
        json_schema_text = json.dumps(TestData.model_json_schema(),
                                      ensure_ascii=False, indent=2)
        case_label = (
            f"{row.get('case_id') or os.path.basename(os.path.dirname(output_path))}"
            f" | {os.path.basename(os.path.dirname(output_path))}/{os.path.basename(output_path)}"
        )

        # data_analysis = 引导 + 修复上下文（首轮 / 修复轮）
        # 三阶段化 D3：setup 提取键注解（row._setup_keys_note）注入两分支；
        # repair 轮 row 原样透传（_run_yaml_rounds pending 保留同一 row 对象），注解不丢。
        keys_note = row.get("_setup_keys_note", "")
        if repair_ctx:
            data_analysis = (
                YAML_ANALYSIS_GUIDE
                + (f"\n\n{keys_note}" if keys_note else "")
                + f"\n\n### 你上一轮的输出（有错）\n{repair_ctx.get('prior_output', '')}"
                + f"\n### 校验错误明细\n{repair_ctx.get('error_detail', '')}"
                + f"\n### 全批次错误模式\n{repair_ctx.get('error_pattern_summary', '')}"
                + f"\n### 后校验问题\n{repair_ctx.get('post_check_issues', '')}"
            )
            node_label = f"repair_yaml_data_single_ROUND{repair_ctx.get('round_no', 2)}"
        else:
            data_analysis = YAML_ANALYSIS_GUIDE + (f"\n\n{keys_note}" if keys_note else "")
            node_label = "generate_yaml_data_single"
        prompt_label = "generate_yaml_data_single_prompt"

        format_prompt = generate_yaml_data_single_prompt()
        inject_annotations = self._build_annotation_injector(api_defs_json)

        # ── 单节点：thinking + json_object 一次调用（绕开 METHOD_FEATURES json_mode 强制 thinking off）──
        llm = self.llm.bind(temperature=0.4,
                            response_format={"type": "json_object"},
                            extra_body={"thinking": {"type": "enabled"}})
        raw = self._invoke_think(
            llm, format_prompt.format_messages(
                data_factory_methods=factory_methods_text,
                api_definitions=api_defs_json,
                data_analysis=data_analysis,
                test_case_logic=test_case_logic,
                user_context=user_ctx,
                db_schema=db_schema,
                json_schema=json_schema_text,
            ),
            label=node_label, reasoning_label=node_label,
        )
        # 单节点 thinking 在 reasoning_content（invoke_think 已按 reasoning_label 采集）；
        # content 即最终 TestData JSON，一并落 thinking_trace.log
        log_thinking(node_label, case_label, raw, prompt_label=prompt_label)

        parsed = json.loads(raw)
        parsed = inject_annotations(parsed)
        from prompts.response_model import set_db_schema_empty
        set_db_schema_empty(not bool(db_schema))
        result = TestData.model_validate(parsed)

        # 2026-09-01 task#15：URL 前缀代码层接管（LLM 丢业务前缀，prompt 规则不可靠）
        self._normalize_base_urls(result, api_defs_json)

        return self._write_yaml_result(result, output_path)

    def _generate_all_yamls(self, excel_path: str, api_defs_json: str, user_ctx: str) -> dict:
        """Phase C V2：按 feature/story/func 目录生成 YAML + setup_data。

        目录结构:
          testcase/<feature_en>/
            setup_data/setup_<class_slug>.yaml    ← LLM 生成（共享前置 → API 调用）
            setup_data/teardown_<class_slug>.yaml ← LLM 生成
            <func_en>/test_data.yaml              ← LLM 生成（含所有步骤）
        """
        logger.info("\n🔢 正在生成 YAML 测试数据...")

        _empty = {"total": 0, "success": 0, "failed": 0,
                  "repaired": 0, "rounds": 0, "errors_file": None}

        if not excel_path:
            logger.info("   ⚠️ 无 Excel 路径，跳过 YAML 生成")
            return dict(_empty)

        output_base = os.path.dirname(excel_path)
        # 新一轮全量生成开始：清理上次残留的终态错误清单
        try:
            os.remove(os.path.join(output_base, "_generation_errors.json"))
        except OSError:
            pass
        raw_rows = self._read_excel_rows(excel_path)
        translations = self._translate_to_en(excel_path, raw_rows)
        feature_en_map = translations["feature_en"]
        story_en_map = translations["story_en"]
        title_en_map = translations["title_en"]
        shared_pres = self._read_shared_preconditions(excel_path)

        # C6-1: 断言校验
        assertion_errors = []
        for r in raw_rows:
            expected = r.get("expected", "")
            if not expected:
                continue
            for step_idx, step_text in enumerate(expected.split("\n"), 1):
                step_text = step_text.strip()
                if not step_text:
                    continue
                try:
                    self._parse_assertion(step_text)
                except self.AssertionParseError as e:
                    assertion_errors.append(
                        f"{r.get('case_id', '?')} step{step_idx}: {e}"
                    )
        if assertion_errors:
            logger.warning("   ⚠️ 断言格式校验失败 %d 条（不阻断，继续生成）:", len(assertion_errors))
            for err in assertion_errors[:10]:
                logger.warning("     %s", err)
            if len(assertion_errors) > 10:
                logger.warning("     ... 共 %d 条错误", len(assertion_errors))

        # 按 feature → story 分组
        from collections import defaultdict
        feature_story_map = defaultdict(lambda: defaultdict(list))
        for r in raw_rows:
            feature_story_map[r["feature"]][r["story"]].append(r)

        # 2026-08-27 三阶段化：同循环分流三列表（setup/test/teardown），阶段间严格 barrier。
        # row 带 `_pre_ids` 元数据（test 沿用 excel preconditions），供下游 key 注入过滤（D3）。
        setup_tasks = []
        test_tasks = []
        teardown_tasks = []
        for feature_cn, stories in feature_story_map.items():
            feature_en = feature_en_map.get(feature_cn, self._sanitize_en(self._pinyin_fallback(feature_cn)))
            for story_cn, cases in stories.items():
                story_en = story_en_map.get(story_cn, self._sanitize_en(self._pinyin_fallback(story_cn)))
                class_slug = re.sub(r'(?<!^)(?=[A-Z])', '_', story_en).lower()

                # setup_data/ YAML（从共享前置生成）
                pre_ids = set()
                for c in cases:
                    pre_str = c.get("preconditions", "")
                    if pre_str and pre_str != "无":
                        for pid in pre_str.split(","):
                            pid = pid.strip()
                            if pid.startswith("PRE-"):
                                pre_ids.add(pid)

                setup_dir = os.path.join(output_base, feature_en, "setup_data")
                os.makedirs(setup_dir, exist_ok=True)

                if pre_ids:
                    setup_lines = []
                    teardown_lines = []
                    for pid in sorted(pre_ids):
                        pre = next((p for p in shared_pres if p["id"] == pid), None)
                        if pre:
                            setup_lines.append(f"# {pid}: {pre['name']}\n{pre['steps']}")
                            # 2026-08-27 三阶段化：teardown 不再拼接创建步骤（原逆向拼接导致
                            # teardown 被生成成 add 而非 delete），只给「清理谁」——删除细节由
                            # teardown 阶段按 setup 提取键 + delete 接口定义自行生成。
                            teardown_lines.append(f"# 清理 {pid}: {pre['name']}")

                    # 2026-08-27 v8 根因修复：setup 捕获键前置规则（见 SETUP_CAPTURE_RULE）。
                    # 注入 setup 任务 steps → LLM 可知「这是共享前置」且必须 output_extract。
                    setup_text = SETUP_CAPTURE_RULE + "\n" + "\n".join(setup_lines)
                    teardown_text = "\n".join(teardown_lines)

                    setup_yaml = os.path.join(setup_dir, f"setup_{class_slug}.yaml")
                    teardown_yaml = os.path.join(setup_dir, f"teardown_{class_slug}.yaml")
                    # LLM 生成可执行的 YAML 数据（各归其列表，阶段间 barrier）
                    setup_tasks.append((
                        {"steps": setup_text, "expected": "",
                         "case_id": f"setup_{class_slug}",
                         "_pre_ids": sorted(pre_ids)}, setup_yaml))
                    teardown_tasks.append((
                        {"steps": teardown_text, "expected": "",
                         "case_id": f"teardown_{class_slug}",
                         "_pre_ids": sorted(pre_ids)}, teardown_yaml))

                # func YAML（每个 TC 一个目录，含一个 test_data.yaml）
                for c in cases:
                    title_cn = c["title"]
                    func_en = title_en_map.get(
                        title_cn,
                        "test_" + self._sanitize_en(self._pinyin_fallback(title_cn))
                    )
                    if not func_en.startswith("test_"):
                        func_en = "test_" + func_en
                    func_dir = os.path.join(output_base, feature_en, func_en)
                    os.makedirs(func_dir, exist_ok=True)

                    # 合并所有 step 为一个 YAML（run_blocks 逐条执行）
                    yaml_path = os.path.join(func_dir, "test_data.yaml")
                    test_tasks.append((c, yaml_path))

        all_tasks = setup_tasks + test_tasks + teardown_tasks

        total = len(setup_tasks) + len(test_tasks) + len(teardown_tasks)
        if not total:
            logger.info("   ⚠️ 没有需要生成的 YAML")
            result = dict(_empty)
            self._log_node_output("generate_all_yamls", result)
            return result

        logger.info(f"   📋 共需生成 {total} 个 YAML 文件（setup {len(setup_tasks)} / "
                    f"test {len(test_tasks)} / teardown {len(teardown_tasks)}），"
                    f"并发 {config.YAML_CONCURRENCY} 个线程，"
                    f"修复轮上限 {config.YAML_REPAIR_ROUNDS}")

        # 单节点生成：thinking+json_object 一次调用（两段式已注释，2026-08-24 起恒走单节点）
        gen_func = self._generate_one_yaml_single
        # 2026-08-27 三阶段化：setup 全部完成 → test 全部完成 → teardown 全部完成，阶段间严格 barrier。
        # setup 结束后解析 input_extract 键写 _setup_extract_keys.json，test/teardown 据此注入键名注解（D3/D4）。
        stage_results = []
        stage_error_entries = []
        expected_pres = sorted({pid for row, _ in setup_tasks for pid in (row.get("_pre_ids") or [])})

        if setup_tasks:
            logger.info("   ▶ Stage 1/3: setup")
            r1 = self._run_yaml_rounds(setup_tasks, api_defs_json, user_ctx, output_base,
                                       gen_func=gen_func)
            stage_results.append(r1)
            _collect_stage_errors(stage_error_entries, r1)

        setup_keys = _parse_setup_extract_keys(output_base, expected_pres)
        # 2026-08-27 用户决策（task #10）：teardown 对缺失提取键的 PRE 跳过清理块
        # （占位删除必失败，从任务源过滤——steps 与 _pre_ids 同步剔除缺失 PRE）
        _filter_teardown_missing_pres(teardown_tasks, setup_keys)
        _inject_setup_keys_note(test_tasks, setup_keys, key_field="preconditions")
        _inject_setup_keys_note(teardown_tasks, setup_keys, key_field="_pre_ids")

        if test_tasks:
            logger.info("   ▶ Stage 2/3: test")
            r2 = self._run_yaml_rounds(test_tasks, api_defs_json, user_ctx, output_base,
                                       gen_func=gen_func)
            stage_results.append(r2)
            _collect_stage_errors(stage_error_entries, r2)

        if teardown_tasks:
            logger.info("   ▶ Stage 3/3: teardown")
            r3 = self._run_yaml_rounds(teardown_tasks, api_defs_json, user_ctx, output_base,
                                       gen_func=gen_func)
            stage_results.append(r3)
            _collect_stage_errors(stage_error_entries, r3)
        # 2026-08-27 用户决策（task #9）：teardown 删除块剥离严格断言（清理清扫幂等）
        _relax_teardown_validation(output_base)

        # 三阶段结果合并：counts 求和、rounds 取 max、终态错误清单合并重写（方案 A）
        result = _merge_stage_results(stage_results, stage_error_entries, output_base, total)

        # --- YAML 后校验（纯代码，不放 LLM）---
        from agent_components.validation.yaml_validator import YamlPostValidator
        validator = YamlPostValidator()
        post_issues = validator.validate_all(output_base)
        # D4 后校验（就地实现，选项 1）：扫描 test/teardown 引用 __MISSING_KEY__ 的用例 → P1
        post_issues.extend(_scan_missing_key_refs(output_base, setup_keys))
        _post_issues_path = os.path.join(output_base, "_post_validation_issues.json")
        if post_issues:
            import json as _json
            with open(_post_issues_path, "w", encoding="utf-8") as _f:
                _json.dump(post_issues, _f, ensure_ascii=False, indent=2)
            _d4_missing = [i for i in post_issues if i.get("check") == "missing_extract_key"]
            if _d4_missing:
                logger.warning("   ⚠️ D4: %d 个用例引用 setup 缺失键（__MISSING_KEY__），"
                               "已标 P1 需人工复核（不进修复轮）", len(_d4_missing))
            # P0/P1 问题注入修复轮（修复轮未耗尽时）；D4 missing_extract_key 除外
            # （根因是 setup 生成失败，重生成无用）
            _fixable = [i for i in post_issues
                        if i.get("severity") in ("P0", "P1")
                        and i.get("check") != "missing_extract_key"]
            if _fixable and result["rounds"] < config.YAML_REPAIR_ROUNDS:
                # 收集受影响的任务（三阶段合并清单）
                _affected_paths = {i["yaml_path"] for i in _fixable}
                _affected_tasks = [
                    (row, path) for row, path in all_tasks
                    if os.path.abspath(path) in {os.path.abspath(p) for p in _affected_paths}
                ]
                if _affected_tasks:
                    logger.info(f"   🔧 后校验发现 {len(_fixable)} 个 P0/P1 问题，"
                                f"追加一轮修复（{len(_affected_tasks)} 个文件）")
                    _post_result = self._run_yaml_rounds(
                        _affected_tasks, api_defs_json, user_ctx, output_base,
                        gen_func=gen_func,
                        post_check_issues=_fixable,
                        repair_rounds=1,
                    )
                    result["success"] = result["success"] - len(_affected_tasks) + _post_result["success"]
                    result["failed"] = _post_result["failed"]
                    result["repaired"] += _post_result["repaired"]
                    result["rounds"] += _post_result["rounds"]
            _p2_count = len([i for i in post_issues if i.get("severity") == "P2"])
            if _p2_count:
                logger.info(f"   📝 后校验发现 {_p2_count} 个 P2 问题（仅告警，见 {_post_issues_path}）")
        else:
            try:
                os.remove(_post_issues_path)
            except OSError:
                pass

        # --- 引用完整性检查（2026-08-13 P2）：.py 引用 vs 磁盘 yaml，缺失禁止静默放行 ---
        missing_refs = _find_missing_yaml_refs(
            output_base, config.PYCHARM_MISC or os.getcwd())
        if missing_refs:
            logger.warning(
                "   ⚠️ 引用完整性检查：%d 个 .py 引用的 YAML 磁盘缺失"
                "（未补生成，按设计跳过），禁止静默放行:",
                len(missing_refs))
            for m in missing_refs:
                logger.warning("     MISSING: %s", m)
            result["missing_refs"] = missing_refs
        else:
            logger.info("   ✅ 引用完整性检查：全部 %s 引用存在", "YAML")

        self._log_node_output("generate_all_yamls", result)
        return result

    def _run_yaml_rounds(self, yaml_tasks: list, api_defs_json: str, user_ctx: str,
                         output_base: str, gen_func=None, repair_rounds: int = None,
                         post_check_issues: list | None = None) -> dict:
        """YAML 生成轮次循环。

        第 1 轮全量并发生成；失败项登记占位（不写盘）→ 轮末汇总错误模式 →
        修复轮携带 {上轮原始输出 + 错误明细 + 全批次错误模式 + 后校验问题} 送思考节点自查重生成；
        超过修复轮上限仍失败 → 终态：计 failed + 写 _generation_errors.json，
        不写任何占位假文件。

        Args:
            gen_func: 可注入的单文件生成函数（单元测试用），签名同 _generate_one_yaml_single
            repair_rounds: 修复轮数覆盖（默认 config.YAML_REPAIR_ROUNDS）
            post_check_issues: YAML 后校验发现的问题列表（直接注入修复轮）
        """
        from infrastructure.observability import log_phase_header, log_thinking, get_thinking_logger
        from web.tasks import _BoundedThreadPoolExecutor
        from concurrent.futures import as_completed
        from prompts.response_model import ValidationInterceptor

        ValidationInterceptor.reset()

        gen = gen_func or self._generate_one_yaml_single
        max_repair = config.YAML_REPAIR_ROUNDS if repair_rounds is None else repair_rounds
        tlog = get_thinking_logger()

        total = len(yaml_tasks)
        success = 0
        repaired = 0
        rounds_run = 0
        fail_seq = 0
        registry: list = []      # 最近一轮的失败登记（循环结束即终态失败清单）
        pending = [(row, path, None) for row, path in yaml_tasks]

        for round_no in range(1, max_repair + 2):   # 1=全量轮, 2..=修复轮
            if not pending:
                break
            rounds_run = round_no
            label = "第1轮(全量)" if round_no == 1 else f"修复轮{round_no}"
            log_phase_header(f"Phase C — YAML 生成 {label} ({len(pending)} 个)")
            logger.info(f"   🔄 {label}: {len(pending)} 个任务")

            failures: list = []
            batch = len(pending)
            with _BoundedThreadPoolExecutor(
                    max_workers=config.YAML_CONCURRENCY,
                    max_queue=config.YAML_CONCURRENCY * 2) as executor:
                future_map = {
                    executor.submit(gen, row, api_defs_json, user_ctx, path, rctx):
                        (row, path)
                    for row, path, rctx in pending
                }
                done = 0
                for future in as_completed(future_map):
                    row, path = future_map[future]
                    done += 1
                    try:
                        future.result()
                        success += 1
                        if round_no > 1:
                            repaired += 1
                        if done % 20 == 0:
                            logger.info(f"      [{done}/{batch}] ...")
                    except Exception as e:
                        fail_seq += 1
                        pid = f"GEN-FAIL-R{round_no}-{fail_seq:03d}"
                        err_text = str(e)
                        rel_path = os.path.relpath(path, output_base).replace("\\", "/")
                        case_id = str(row.get("case_id")
                                      or os.path.basename(os.path.dirname(path)))
                        raw_snippet = _extract_completion_snippet(err_text)
                        failures.append({
                            "placeholder_id": pid,
                            "case_id": case_id,
                            "yaml_path": rel_path,
                            "round": round_no,
                            "error": err_text[:2000],
                            "raw_output_snippet": raw_snippet,
                            "row": row,
                            "path": path,
                        })
                        logger.info(f"      [{done}/{batch}] ❌ "
                                    f"{os.path.basename(path)} ({pid})")
                        # 失败标记落 thinking_trace.log（与 generate_excel_plan_FAILED 同风格）
                        log_thinking(
                            "generate_yaml_FAILED",
                            f"| {case_id} | {rel_path} | {pid} |",
                            err_text[:1500],
                            prompt_label="generate_yaml_data_single_prompt",
                        )
                        # 详细错误日志：原文 + 错误点，写入输出目录
                        _write_fail_detail(output_base, pid, case_id, rel_path,
                                           round_no, err_text, raw_snippet)

            ok = batch - len(failures)
            tlog.info(f"ROUND{round_no}: {ok}/{batch} 通过, {len(failures)} 登记")
            logger.info(f"   ✅ {label}: {ok}/{batch} 通过, {len(failures)} 失败登记")

            registry = failures
            if not failures or round_no >= max_repair + 1:
                break

            # 组装修复轮：全批次错误模式统计（跨文件反馈）+ 每项自查上下文
            pattern = _summarize_error_patterns(failures)
            pending = [(
                f["row"], f["path"],
                {"prior_output": f["raw_output_snippet"],
                 "error_detail": f["error"],
                 "error_pattern_summary": pattern,
                 "round_no": round_no + 1,
                 "post_check_issues": _format_post_issues_for_prompt(post_check_issues)
                    if post_check_issues else "",
                },
            ) for f in failures]

        failed = len(registry)
        errors_file = None
        if registry:
            errors_file = os.path.join(output_base, "_generation_errors.json")
            payload = [{
                "placeholder_id": r["placeholder_id"],
                "case_id": r["case_id"],
                "yaml_path": r["yaml_path"],
                "rounds_attempted": rounds_run,
                "error": r["error"],
                "raw_output_snippet": r["raw_output_snippet"],
            } for r in registry]
            with open(errors_file, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
            tlog.info(f"FINAL_FAILED: {failed} 个 → {errors_file}")
            logger.warning("   ⚠️ 终态失败 %d 个（不写占位文件），详见 %s",
                           failed, errors_file)

        logger.info(f"   ✅ 完成: {success}/{total}，修复 {repaired}，"
                    f"仍失败 {failed}，轮次 {rounds_run}")

        # 写入 Schema 校验拦截报告（独立于 _generation_errors.json，用于提示词优化）
        ValidationInterceptor.write_report("logs")

        return {"total": total, "success": success, "failed": failed,
                "repaired": repaired, "rounds": rounds_run,
                "errors_file": errors_file}
