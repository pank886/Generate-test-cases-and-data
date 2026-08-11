"""Phase C: YAML 测试数据生成 + 轮次修复循环 Mixin

拆分自 generators/__init__.py（2026-08-07 大文件拆分）。

依赖宿主类提供: self.llm / self._invoke_think / self._invoke_structured /
                self._load_factory_methods / self._log_node_output
跨 Mixin 依赖: ExcelMixin / TranslationMixin / PyExportMixin / _helpers
"""
import json
import os
import re

import yaml

import config
from observability import get_logger
from prompts.response_model import TestData
from agent_components.generators._helpers import (
    _summarize_error_patterns,
    _extract_completion_snippet,
    _write_fail_detail,
    _format_post_issues_for_prompt,
)

logger = get_logger(__name__)


def _parse_api_sequence(seq) -> list[tuple[str, str, str]]:
    """解析锚条目 → [(步骤名, METHOD, url)]。

    兼容两种格式（2026-08-12 json_schema 后 LLM 可能不带"步骤名:"前缀）:
      - "创建订单:POST /order/create" → ("创建订单", "POST", "/order/create")
      - "POST /order/create"           → ("", "POST", "/order/create")
    """
    out = []
    for item in seq:
        s = str(item).strip()
        if not s:
            continue
        if ":" in s:
            name, _, rest = s.partition(":")
        else:
            name, rest = "", s
        rest = rest.strip()
        method, _, url = rest.partition(" ")
        out.append((name.strip(), method.strip().upper(), url.strip()))
    return out


def _build_api_context(entries: list[tuple[str, str, str]], details: dict) -> str:
    """锚（有序步骤）+ 详情 → prompt 的 api_definitions 文本（D4 按需取源）。"""
    lines = []
    if entries:
        lines.append("### 该用例接口序列（按下标对齐，第 i 步的 url/method 必须与第 i 条一致）")
        for i, (name, method, url) in enumerate(entries, 1):
            lines.append(f"{i}. {name} {method} {url}")
    if details:
        lines.append("### 接口详情（按需取源，参数/返回值用于填充 testCase）")
        for nurl, d in details.items():
            lines.append(
                f"- {d.get('api_method')} {d.get('api_url')}（{d.get('api_name')}）\n"
                f"  参数: {json.dumps(d.get('api_parameters') or [], ensure_ascii=False)}\n"
                f"  返回值: {json.dumps(d.get('api_returns') or [], ensure_ascii=False)}"
            )
    return "\n".join(lines)


def _validate_against_anchor(data, entries: list[tuple[str, str, str]]) -> None:
    """数量 + 各步 url 按锚校验；不符抛 ValueError（_run_yaml_rounds 捕获进修复轮）。"""
    from agent_components.api_annotations import normalize_api_url
    if len(data) != len(entries):
        raise ValueError(
            f"步骤数量与锚不符: 锚定 {len(entries)} 步，实际输出 {len(data)} 步"
            f"（锚: {[f'{m} {u}' for _, m, u in entries]}）")
    for i, (step, (name, method, url)) in enumerate(zip(data, entries)):
        step_url = str(step.baseInfo.get("url", ""))
        step_method = str(step.baseInfo.get("method", "")).lower()
        if normalize_api_url(step_url) != normalize_api_url(url):
            raise ValueError(
                f"第 {i + 1} 步 url 与锚不符: 期望 {method} {url}（{name}），实际 {step_url}")
        if step_method != method.lower():
            raise ValueError(
                f"第 {i + 1} 步 method 与锚不符: 期望 {method}（{url}），实际 {step_method}")


# 单节点生成引导（第四节）：浓缩两段式 analyze 的分析要点，引导模型在 thinking 里分析
YAML_ANALYSIS_GUIDE = (
    "请先在思考中完成以下分析，再严格按本 prompt 的 JSON 结构输出：\n"
    "1. 接口匹配：每个步骤对应哪个接口（url/method 与接口定义一致）\n"
    "2. 请求参数：来源（用例指定/上游提取/工厂方法）\n"
    "3. 数据传递：哪些返回值需要 extract 供下游引用\n"
    "4. 断言设计：断言字段与期望值\n"
    "5. 动态值：用哪个工厂函数，还是固定字面量"
)


class YamlMixin:
    """YAML 生成（thinking 分析 → json_mode 输出）+ 多轮修复循环"""

    def _generate_one_yaml(self, row: dict, dep_map, user_ctx: str,
                           output_path: str, repair_ctx: dict | None = None) -> str:
        """D4 按锚生成：api_sequence 锚 + ApiOps.get_by_url 详情注入 prompt，生成后按锚校验。

        锚取自 row["_api_sequence"]（_generate_all_yamls 从 dep_map 附加，格式
        "步骤名:METHOD /url" 有序列表）。prompt 只注入该用例的锚 + 详情（不灌整批索引）。

        两段式：thinking 分析 → json_mode 输出。校验失败不做 inline 重试——直接抛异常，
        由 _run_yaml_rounds 登记后进修复轮（repair_ctx 带错误上下文 + 锚重打）。

        repair_ctx（修复轮时非 None）:
          {prior_output, error_detail, error_pattern_summary, round_no, post_check_issues}
        """
        from prompts.extraction_prompts import (
            analyze_yaml_data_prompt, format_yaml_data_prompt, repair_yaml_data_prompt,
        )
        from observability import log_thinking
        from agent_components.api_annotations import normalize_api_url
        from database import get_session_ctx
        from database.operations import ApiOps

        db_schema = config.DB_SCHEMA  # 数据库表结构（占位，为空禁 db 断言，2026-08-04 问题 2）
        factory_methods_text = self._load_factory_methods()
        test_case_logic = f"执行步骤: {row['steps']}\n预期结果: {row.get('expected', '')}"
        case_label = (
            f"{row.get('case_id') or os.path.basename(os.path.dirname(output_path))}"
            f" | {os.path.basename(os.path.dirname(output_path))}/{os.path.basename(output_path)}"
        )

        # ── 解析锚 + 按需取详情（D4：不灌整批索引，只取该用例涉及接口）──
        entries = _parse_api_sequence(row.get("_api_sequence") or [])
        details = {}
        for _n, _m, _u in entries:
            nurl = normalize_api_url(_u)
            if nurl in details:
                continue
            with get_session_ctx() as session:
                d = ApiOps.get_by_url(session, _m, _u)
            if d:
                details[nurl] = d
        api_definitions_text = _build_api_context(entries, details)

        # === 阶段 1：thinking 分析（首轮=需求分析 / 修复轮=带错误上下文自查） ===
        if repair_ctx:
            think_prompt = repair_yaml_data_prompt()
            prompt_vars = dict(
                api_definitions=api_definitions_text,
                test_case_logic=test_case_logic,
                user_context=user_ctx,
                data_factory_methods=factory_methods_text,
                db_schema=db_schema,
                error_pattern_summary=repair_ctx.get("error_pattern_summary", ""),
                prior_output=repair_ctx.get("prior_output", ""),
                error_detail=repair_ctx.get("error_detail", ""),
                post_check_issues=repair_ctx.get("post_check_issues", ""),
            )
            node_label = f"repair_yaml_data_ROUND{repair_ctx.get('round_no', 2)}"
            prompt_label = "repair_yaml_data_prompt"
        else:
            think_prompt = analyze_yaml_data_prompt()
            prompt_vars = dict(
                api_definitions=api_definitions_text,
                test_case_logic=test_case_logic,
                user_context=user_ctx,
                data_factory_methods=factory_methods_text,
                db_schema=db_schema,
            )
            node_label = "analyze_yaml_data"
            prompt_label = "analyze_yaml_data_prompt"

        llm_kwargs = {"extra_body": {"thinking": {"type": "enabled"}}}
        bound_llm = self.llm.bind(**llm_kwargs)
        # 空 content 有限重试（公共方法 _invoke_think，复用同一输入重试 config.MAX_RETRIES 次）
        analysis = self._invoke_think(
            bound_llm, think_prompt.format_messages(**prompt_vars), label=node_label)

        # Phase C 思考全文与 Phase B 同规格写入 thinking_trace.log
        log_thinking(node_label, case_label, analysis, prompt_label=prompt_label)

        # === 阶段 2：json_mode 结构化输出（max_retries=0，失败即抛给登记器） ===
        format_prompt = format_yaml_data_prompt()

        # ── 构建 pre_validate 闭包：按锚 url 注入 _annotations + 处理 is_export 空断言 ──
        def _lookup_api(url: str) -> dict | None:
            return details.get(normalize_api_url(url))

        def _inject_annotations(parsed: dict) -> dict:
            """pre_validate 回调：注入 _annotations，为 is_export 补齐占位断言。"""
            for step in parsed.get("data", []):
                url = step.get("baseInfo", {}).get("url", "")
                api = _lookup_api(url)
                if api and api.get("api_annotations"):
                    step["baseInfo"]["_annotations"] = api["api_annotations"]
                    # is_export: 补齐占位断言防止 Pydantic 校验拦截空 validation
                    if api["api_annotations"].get("is_export", {}).get("active"):
                        for tc in step.get("testCase", []):
                            if not tc.get("validation"):
                                tc["validation"] = [{"__placeholder_export": True}]
            return parsed

        # db_schema 为空 → 禁 db 断言（TestData.validate_no_db_when_no_schema，2026-08-04 问题 2）
        from prompts.response_model import set_db_schema_empty
        set_db_schema_empty(not bool(db_schema))

        result = self._invoke_structured(format_prompt, TestData,
            max_retries=0,
            method="json_mode",
            pre_validate=_inject_annotations,
            data_analysis=analysis,
            api_definitions=api_definitions_text,
            test_case_logic=test_case_logic,
            user_context=user_ctx,
            data_factory_methods=factory_methods_text,
            db_schema=db_schema,
        )

        # ── 按锚校验（数量 + 各步 url）→ 不符抛异常进修复轮 ──
        if entries:
            _validate_against_anchor(result.data, entries)

        # ── 写盘前注入：路径参数替换 + 导出接口断言接管（兜底） ──
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

        # ── 序列化写盘（去除 _annotations 元数据字段） ──
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

    def _generate_one_yaml_single(self, row: dict, dep_map, user_ctx: str,
                                  output_path: str, repair_ctx: dict | None = None) -> str:
        """D4 锚定 + 单节点：thinking + json_object 一次调用生成 TestData（第四节）。

        签名/锚定/校验/写盘与 `_generate_one_yaml` 完全一致，仅 LLM 调用从两段式合并为一次：
          - thinking 走 reasoning_content（不走 `_invoke_structured` 的 json_mode，
            绕开 METHOD_FEATURES 强制 thinking off），经 `_invoke_think` 的
            reasoning_label 采集落 thinking_trace.log；
          - content 直接是 TestData JSON → json.loads + `_inject_annotations` +
            `TestData.model_validate`（max_retries=0 语义，失败进修复轮）。

        首轮 data_analysis = YAML_ANALYSIS_GUIDE；修复轮 = 引导 + 错误上下文。
        """
        from prompts.extraction_prompts import format_yaml_data_prompt
        from agent_components.api_annotations import normalize_api_url
        from database import get_session_ctx
        from database.operations import ApiOps
        from observability import log_thinking

        db_schema = config.DB_SCHEMA
        factory_methods_text = self._load_factory_methods()
        test_case_logic = f"执行步骤: {row['steps']}\n预期结果: {row.get('expected', '')}"
        case_label = (
            f"{row.get('case_id') or os.path.basename(os.path.dirname(output_path))}"
            f" | {os.path.basename(os.path.dirname(output_path))}/{os.path.basename(output_path)}"
        )

        # ── 解析锚 + 按需取详情（与旧节点一致）──
        entries = _parse_api_sequence(row.get("_api_sequence") or [])
        details = {}
        for _n, _m, _u in entries:
            nurl = normalize_api_url(_u)
            if nurl in details:
                continue
            with get_session_ctx() as session:
                d = ApiOps.get_by_url(session, _m, _u)
            if d:
                details[nurl] = d
        api_definitions_text = _build_api_context(entries, details)

        # data_analysis = 引导 + 修复上下文（首轮 / 修复轮）
        if repair_ctx:
            data_analysis = (
                YAML_ANALYSIS_GUIDE
                + f"\n\n### 你上一轮的输出（有错）\n{repair_ctx.get('prior_output', '')}"
                + f"\n### 校验错误明细\n{repair_ctx.get('error_detail', '')}"
                + f"\n### 全批次错误模式\n{repair_ctx.get('error_pattern_summary', '')}"
                + f"\n### 后校验问题\n{repair_ctx.get('post_check_issues', '')}"
            )
            node_label = f"repair_yaml_data_single_ROUND{repair_ctx.get('round_no', 2)}"
        else:
            data_analysis = YAML_ANALYSIS_GUIDE
            node_label = "generate_yaml_data_single"
        prompt_label = "format_yaml_data_prompt"

        format_prompt = format_yaml_data_prompt()

        def _inject_annotations(parsed: dict) -> dict:
            for step in parsed.get("data", []):
                url = step.get("baseInfo", {}).get("url", "")
                api = details.get(normalize_api_url(url))
                if api and api.get("api_annotations"):
                    step["baseInfo"]["_annotations"] = api["api_annotations"]
                    if api["api_annotations"].get("is_export", {}).get("active"):
                        for tc in step.get("testCase", []):
                            if not tc.get("validation"):
                                tc["validation"] = [{"__placeholder_export": True}]
            return parsed

        # ── 单节点：thinking + json_object 一次调用（绕开 METHOD_FEATURES json_mode）──
        llm = self.llm.bind(temperature=0.4,
                            response_format={"type": "json_object"},
                            extra_body={"thinking": {"type": "enabled"}})
        raw = self._invoke_think(
            llm, format_prompt.format_messages(
                data_factory_methods=factory_methods_text,
                api_definitions=api_definitions_text,
                data_analysis=data_analysis,
                test_case_logic=test_case_logic,
                user_context=user_ctx,
                db_schema=db_schema,
            ),
            label=node_label, reasoning_label=node_label,
        )
        # 单节点 thinking 在 reasoning_content（invoke_think 已采 log）；content 即最终 JSON
        log_thinking(node_label, case_label, raw, prompt_label=prompt_label)

        parsed = json.loads(raw)
        parsed = _inject_annotations(parsed)
        from prompts.response_model import set_db_schema_empty
        set_db_schema_empty(not bool(db_schema))
        result = TestData.model_validate(parsed)

        # ── 按锚校验（数量 + url + method）→ 不符抛异常进修复轮 ──
        if entries:
            _validate_against_anchor(result.data, entries)

        # ── 写盘管线（与旧节点一致）──
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

        _clean_steps = []
        for step in result.data:
            _d = step.model_dump(exclude_none=True, by_alias=True)
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

    def _generate_all_yamls(self, excel_path: str, dep_map, user_ctx: str) -> dict:
        """Phase C V2（D4 锚定）：按 feature/story/func 目录生成 YAML + setup_data。

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

        # dep_map 按 story_name 建索引（D4 锚定来源）
        story_map = {
            s.get("story_name"): s
            for s in ((dep_map or {}).get("stories") or [])
            if isinstance(s, dict)
        }

        yaml_tasks = []
        skipped_issues = []  # [(case_id, reason)]
        for feature_cn, stories in feature_story_map.items():
            feature_en = feature_en_map.get(feature_cn, self._sanitize_en(self._pinyin_fallback(feature_cn)))
            for story_cn, cases in stories.items():
                story_en = story_en_map.get(story_cn, self._sanitize_en(self._pinyin_fallback(story_cn)))
                class_slug = re.sub(r'(?<!^)(?=[A-Z])', '_', story_en).lower()
                story = story_map.get(story_cn) or {}

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
                            teardown_lines.append(
                                f"# 清理 {pid}: {pre['name']}\n"
                                f"根据 {pid} 的创建步骤逆向操作：{pre['steps'][:200]}"
                            )

                    setup_text = "\n".join(setup_lines)
                    teardown_text = "\n".join(teardown_lines)

                    setup_yaml = os.path.join(setup_dir, f"setup_{class_slug}.yaml")
                    teardown_yaml = os.path.join(setup_dir, f"teardown_{class_slug}.yaml")
                    pre_seq = story.get("story_pre_api_sequence") or []
                    teardown_seq = story.get("teardown_api_sequence") or []
                    if not pre_seq:
                        # D4 定稿：setup 锚不应为空——有前置必有 story_pre 序列，空 = dep_map 质量异常
                        raise ValueError(
                            f"story [{story_cn}] 有共享前置但 dep_map 的 "
                            "story_pre_api_sequence 为空——setup 锚不应为空（dep_map 质量异常）")
                    if not teardown_seq:
                        # 2026-08-11 定稿：有前置必有其清理序列，teardown 空锚 = 异常（绝不接受空数据）
                        raise ValueError(
                            f"story [{story_cn}] 有共享前置但 dep_map 的 "
                            "teardown_api_sequence 为空——teardown 锚不应为空"
                            "（有前置必有其清理序列）")
                    # LLM 生成可执行的 YAML 数据（D4：锚 = story_pre / teardown 序列）
                    yaml_tasks.append((
                        {"steps": setup_text, "expected": "",
                         "case_id": f"setup_{class_slug}",
                         "_api_sequence": pre_seq,
                         "_kind": "setup"}, setup_yaml))
                    yaml_tasks.append((
                        {"steps": teardown_text, "expected": "",
                         "case_id": f"teardown_{class_slug}",
                         "_api_sequence": teardown_seq,
                         "_kind": "teardown"}, teardown_yaml))

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
                    row = dict(c)
                    row["_api_sequence"] = (story.get("case_api_sequences") or {}).get(
                        c.get("case_id"), [])
                    row["_kind"] = "func"
                    yaml_tasks.append((row, yaml_path))

        # ── 前置拦截（D4）：func 无锚 / 锚接口在 SQLite 查不到 → 跳过（不跑 LLM）──
        from database import get_session_ctx
        from database.operations import ApiOps
        prefiltered = []
        with get_session_ctx() as session:
            for row, path in yaml_tasks:
                kind = row.get("_kind", "func")
                entries = _parse_api_sequence(row.get("_api_sequence") or [])
                if kind == "func" and not entries:
                    skipped_issues.append(
                        (row.get("case_id") or os.path.basename(os.path.dirname(path)),
                         "dep_map 无锚"))
                    continue
                missing = []
                for _n, _m, _u in entries:
                    if ApiOps.get_by_url(session, _m, _u) is None:
                        missing.append(f"{_m} {_u}")
                if missing:
                    skipped_issues.append(
                        (row.get("case_id") or os.path.basename(os.path.dirname(path)),
                         f"接口缺失: {missing}"))
                    continue
                prefiltered.append((row, path))
        yaml_tasks = prefiltered
        issues_path = None
        if skipped_issues:
            issues_path = os.path.join(output_base, "_api_not_found_issues.json")
            with open(issues_path, "w", encoding="utf-8") as _f:
                json.dump([{"case_id": cid, "reason": reason}
                           for cid, reason in skipped_issues],
                          _f, ensure_ascii=False, indent=2)
            logger.warning("   ⚠️ 前置拦截跳过 %d 个（不生成、不跑 LLM）: %s",
                           len(skipped_issues), issues_path)
            for cid, reason in skipped_issues[:15]:
                logger.warning("     - %s: %s", cid, reason)
            if len(skipped_issues) > 15:
                logger.warning("     ... 共 %d 个", len(skipped_issues))

        total = len(yaml_tasks)
        if not total:
            logger.info("   ⚠️ 没有需要生成的 YAML")
            result = dict(_empty)
            result["skipped_api_missing"] = len(skipped_issues)
            result["issues_file"] = issues_path
            self._log_node_output("generate_all_yamls", result)
            return result

        logger.info(f"   📋 共需生成 {total} 个 YAML 文件（含 setup/teardown），"
                    f"并发 {config.YAML_CONCURRENCY} 个线程，"
                    f"修复轮上限 {config.YAML_REPAIR_ROUNDS}")

        # 单节点开关：YAML_SINGLE_NODE=True → thinking+json_object 一次调用（第四节）
        gen_func = self._generate_one_yaml_single if config.YAML_SINGLE_NODE else None
        result = self._run_yaml_rounds(yaml_tasks, dep_map, user_ctx, output_base,
                                       gen_func=gen_func)
        result["skipped_api_missing"] = len(skipped_issues)
        result["issues_file"] = issues_path

        # --- YAML 后校验（纯代码，不放 LLM）---
        from agent_components.post_validator import YamlPostValidator
        validator = YamlPostValidator()
        post_issues = validator.validate_all(output_base)
        _post_issues_path = os.path.join(output_base, "_post_validation_issues.json")
        if post_issues:
            import json as _json
            with open(_post_issues_path, "w", encoding="utf-8") as _f:
                _json.dump(post_issues, _f, ensure_ascii=False, indent=2)
            # P0/P1 问题注入修复轮（修复轮未耗尽时）
            _fixable = [i for i in post_issues if i.get("severity") in ("P0", "P1")]
            if _fixable and result["rounds"] < config.YAML_REPAIR_ROUNDS:
                # 收集受影响的 yaml_tasks
                _affected_paths = {i["yaml_path"] for i in _fixable}
                _affected_tasks = [
                    (row, path) for row, path in yaml_tasks
                    if os.path.abspath(path) in {os.path.abspath(p) for p in _affected_paths}
                ]
                if _affected_tasks:
                    logger.info(f"   🔧 后校验发现 {len(_fixable)} 个 P0/P1 问题，"
                                f"追加一轮修复（{len(_affected_tasks)} 个文件）")
                    _post_result = self._run_yaml_rounds(
                        _affected_tasks, dep_map, user_ctx, output_base,
                        gen_func=gen_func,
                        post_check_issues=_fixable,
                        repair_rounds=1,
                    )
                    result["success"] = result["success"] - len(_affected_tasks) + _post_result["success"]
                    result["failed"] = _post_result["failed"]
                    result["repaired"] += _post_result["repaired"]
                    result["rounds"] += _post_result["rounds"]
            _p2_count = len(post_issues) - len(_fixable)
            if _p2_count:
                logger.info(f"   📝 后校验发现 {_p2_count} 个 P2 问题（仅告警，见 {_post_issues_path}）")
        else:
            try:
                os.remove(_post_issues_path)
            except OSError:
                pass

        self._log_node_output("generate_all_yamls", result)
        return result

    def _run_yaml_rounds(self, yaml_tasks: list, dep_map, user_ctx: str,
                         output_base: str, gen_func=None, repair_rounds: int = None,
                         post_check_issues: list | None = None) -> dict:
        """YAML 生成轮次循环。

        第 1 轮全量并发生成；失败项登记占位（不写盘）→ 轮末汇总错误模式 →
        修复轮携带 {上轮原始输出 + 错误明细 + 全批次错误模式 + 后校验问题} 送思考节点自查重生成；
        超过修复轮上限仍失败 → 终态：计 failed + 写 _generation_errors.json，
        不写任何占位假文件。

        Args:
            gen_func: 可注入的单文件生成函数（单元测试用），签名同 _generate_one_yaml
            repair_rounds: 修复轮数覆盖（默认 config.YAML_REPAIR_ROUNDS）
            post_check_issues: YAML 后校验发现的问题列表（直接注入修复轮）
        """
        from observability import log_phase_header, log_thinking, get_thinking_logger
        from web.tasks import _BoundedThreadPoolExecutor
        from concurrent.futures import as_completed
        from prompts.response_model import ValidationInterceptor

        ValidationInterceptor.reset()

        gen = gen_func or self._generate_one_yaml
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
                    executor.submit(gen, row, dep_map, user_ctx, path, rctx):
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
                            prompt_label="format_yaml_data_prompt",
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
