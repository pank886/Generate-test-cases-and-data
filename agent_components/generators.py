"""Phase C: PY/YAML 生成节点 Mixin"""
import os
import re
import json

import yaml
from openpyxl import Workbook
from openpyxl.styles import Font, Alignment, PatternFill, Border, Side
from openpyxl.utils import get_column_letter
from openpyxl import load_workbook

import config
from observability import get_logger
from prompts.response_model import ClassCode, TestData, TranslationResult

logger = get_logger(__name__)


# ---- Phase C 修复循环辅助（错误分类关键词与校验器报错文案对齐）----

_ERROR_PATTERN_LABELS = [
    ("B1 双花括号 {{}}", ("双花括号",)),
    ("B2 占位符运算/拼接/未闭合", ("禁止运算/拼接", "未闭合或嵌套")),
    ("B3 非注册表占位符函数", ("未知占位符函数",)),
    ("B4 占位符实参不合规", ("实参个数", "第1个参数仅支持")),
    ("B5/B10 提取字段值须为字符串(无需提取应省略)", ("Input should be a valid string",)),
    ("B6/B7 空列表输出", ("at least 1 item", "too_short")),
    ("B9 json/params/data 并存", ("三选一",)),
]


def _summarize_error_patterns(failures: list) -> str:
    """按 B 类别聚合本轮错误计数（跨文件模式反馈，注入修复轮 prompt）。"""
    counts: dict = {}
    for f in failures:
        err = f.get("error", "")
        matched = False
        for label, keywords in _ERROR_PATTERN_LABELS:
            if any(kw in err for kw in keywords):
                counts[label] = counts.get(label, 0) + 1
                matched = True
        if not matched:
            counts["B8 结构解析失败(缺字段/类型错/JSON坏)"] = \
                counts.get("B8 结构解析失败(缺字段/类型错/JSON坏)", 0) + 1
    if not counts:
        return "（无统计）"
    return "\n".join(f"- {label}: {n} 处" for label, n in counts.items())


def _extract_completion_snippet(err_text: str, limit: int = 500) -> str:
    """从结构化输出异常文本中截取 LLM 原始 completion 片段（修复轮自查材料）。"""
    m = re.search(r"from completion (.+?)(?:\. Got:|$)", err_text, re.DOTALL)
    snippet = m.group(1) if m else err_text
    return snippet[:limit]


def _write_fail_detail(output_base: str, pid: str, case_id: str,
                       yaml_path: str, round_no: int, err_text: str,
                       raw_snippet: str) -> None:
    """单文件生成失败时，将原文和错误点写入详细日志。"""
    import os as _os
    log_path = _os.path.join(output_base, "_generation_error_details.log")
    # 提取 Pydantic 校验错误（去掉 LangChain 框架包装）
    _parsed_err = err_text
    _m = re.search(r"Got: (.+?)(?:\nFor troubleshooting|$)", err_text, re.DOTALL)
    if _m:
        _parsed_err = _m.group(1).strip()
    with open(log_path, "a", encoding="utf-8") as _f:
        _f.write(f"{'=' * 60}\n")
        _f.write(f"[{pid}] ROUND={round_no} | {case_id} | {yaml_path}\n")
        _f.write(f"{'=' * 60}\n")
        _f.write(f"--- 校验错误 ---\n{_parsed_err[:3000]}\n\n")
        _f.write(f"--- LLM 原始输出 (前 2000 字符) ---\n{raw_snippet[:2000]}\n\n")


def _format_post_issues_for_prompt(issues: list | None) -> str:
    """将后校验问题列表格式化为修复轮 prompt 文本。"""
    if not issues:
        return ""
    lines = ["⚠️ YAML 后校验发现问题（请逐条修正）：", ""]
    for i, iss in enumerate(issues, 1):
        lines.append(f"{i}. [{iss['check']}] {iss['yaml_path']}")
        lines.append(f"   当前: {iss['current']}")
        lines.append(f"   期望: {iss['expected']}")
        lines.append(f"   修复指引: {iss['fix_hint']}")
        lines.append("")
    return "\n".join(lines)


class GenerationMixin:
    """PY/YAML 测试文件生成节点"""

    def _analyze_data_deps(self, case_steps: str, api_defs_json: str,
                           user_ctx: str) -> str:
        """数据依赖分析（thinking on，自由文本）。"""
        from prompts.extraction_prompts import analyze_data_deps_prompt

        from observability import log_phase_header
        log_phase_header("Phase C — 数据依赖分析")
        logger.info("\n🧠 分析数据依赖（深度思考）...")
        prompt = analyze_data_deps_prompt()
        llm_kwargs = {}
        if config.ENABLE_THINKING:
            llm_kwargs["extra_body"] = {"thinking": {"type": "enabled"}}
        else:
            llm_kwargs["extra_body"] = {"thinking": {"type": "disabled"}}
        bound_llm = self.llm.bind(**llm_kwargs)
        result = bound_llm.invoke(prompt.format_messages(
            api_definitions=api_defs_json,
            test_case_steps=case_steps,
            user_context=user_ctx,
        ))
        analysis = result.content if hasattr(result, "content") else str(result)
        logger.info(f"   => 数据依赖分析完成（{len(analysis)} 字符）")
        from observability import log_thinking
        log_thinking("analyze_data_deps", user_ctx, analysis, prompt_label="analyze_data_deps_prompt")
        return analysis

    def _format_data_plan(self, data_analysis: str, case_steps: str,
                          api_defs_json: str, user_ctx: str) -> dict:
        """格式化数据规划（thinking off + json_mode）。"""
        from prompts.extraction_prompts import generate_data_plan_prompt
        from prompts.response_model import DataPlan

        logger.info("\n--- 生成结构化数据规划 ---")
        prompt = generate_data_plan_prompt()
        result = self._invoke_structured(prompt, DataPlan,
            method="json_mode",
            data_analysis=data_analysis,
            api_definitions=api_defs_json,
            test_case_steps=case_steps,
            user_context=user_ctx,
        )
        if isinstance(result, list):
            result = DataPlan(steps=result, scenario_name="")
        logger.info(f"   => 数据规划完成: {len(result.steps)} 步")
        return {"data_plan": result.model_dump()}

    @staticmethod
    def _read_excel_rows(excel_path: str, enabled_only: bool = False) -> list[dict]:
        """读取 Excel V2 测试计划（9 列双 Sheet），返回 dict 列表。

        Sheet1 列: @allure.epic, @allure.feature, @allure.story, @allure.title,
                   fixture等级, 用例编号, 前置步骤, 执行步骤, 预期结果
        Sheet2: 共享前置（由 _read_shared_preconditions 独立读取）
        """
        from openpyxl import load_workbook
        wb = load_workbook(excel_path)
        try:
            ws = wb.active  # Sheet1: 测试计划
            rows = []
            for row in ws.iter_rows(min_row=2, values_only=True):
                if row[0] is None:
                    continue
                rows.append({
                    "epic": row[0],          # @allure.epic
                    "feature": row[1],       # @allure.feature
                    "story": row[2],         # @allure.story
                    "title": row[3],         # @allure.title
                    "fixture_level": row[4], # fixture等级
                    "case_id": row[5],       # 用例编号 TC-xxx
                    "preconditions": row[6], # 前置步骤
                    "steps": row[7],         # 执行步骤
                    "expected": row[8],      # 预期结果
                })
            return rows
        finally:
            wb.close()

    @staticmethod
    def _read_shared_preconditions(excel_path: str) -> list[dict]:
        """读取 Excel V2 Sheet2（共享前置），返回 dict 列表。

        Sheet2 列: 前置编号, 前置名称, 详细步骤, 预期结果, 关联用例
        """
        from openpyxl import load_workbook
        wb = load_workbook(excel_path)
        try:
            if "共享前置" not in wb.sheetnames:
                return []
            ws = wb["共享前置"]
            rows = []
            for row in ws.iter_rows(min_row=2, values_only=True):
                if row[0] is None:
                    continue
                rows.append({
                    "id": row[0],           # 前置编号 PRE-xxx
                    "name": row[1],          # 前置名称
                    "steps": row[2],         # 详细步骤
                    "expected": row[3],      # 预期结果
                    "linked_cases": row[4],  # 关联用例（逗号分隔）
                })
            return rows
        finally:
            wb.close()

    # ==================== C4: 英文翻译 + C4-1: 幂等性保障 ====================

    @staticmethod
    def _sanitize_en(name: str) -> str:
        """LLM 输出后强制清洗，确保合法 Python identifier。"""
        sanitized = re.sub(r'[^a-zA-Z0-9_]', '', name.replace(' ', '_'))
        if not sanitized or sanitized[0].isdigit():
            sanitized = '_' + sanitized
        return sanitized

    @staticmethod
    def _load_translation_cache(excel_path: str) -> dict:
        """从 Excel 同级目录读取翻译缓存。"""
        cache_path = os.path.join(os.path.dirname(excel_path), "translation_cache.json")
        if os.path.exists(cache_path):
            try:
                with open(cache_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                logger.warning("读取翻译缓存失败: %s", cache_path, exc_info=True)
        return {}

    @staticmethod
    def _save_translation_cache(excel_path: str, cache: dict) -> None:
        """保存翻译缓存到 Excel 同级目录。"""
        cache_path = os.path.join(os.path.dirname(excel_path), "translation_cache.json")
        try:
            with open(cache_path, "w", encoding="utf-8") as f:
                json.dump(cache, f, ensure_ascii=False, indent=2)
        except Exception:
            logger.warning("保存翻译缓存失败: %s", cache_path, exc_info=True)

    @staticmethod
    def _pinyin_fallback(text: str) -> str:
        """拼音首字母缩写 Fallback（LLM 翻译失败时使用）。"""
        try:
            from pypinyin import lazy_pinyin
            return ''.join(w[0].upper() for w in lazy_pinyin(text) if w)
        except ImportError:
            # pypinyin 未安装时用简单 hash 兜底
            import hashlib
            return 'M' + hashlib.md5(text.encode()).hexdigest()[:7].upper()

    def _translate_to_en(self, excel_path: str, rows: list[dict]) -> dict:
        """批量翻译 feature/story/title 为英文，带缓存 + sanitize + 降级。

        Returns:
            {"feature_en": {中文: 英文}, "story_en": {...}, "title_en": {...}}
        """
        # 收集待翻译文本
        features = list(dict.fromkeys(r["feature"] for r in rows if r.get("feature")))
        stories = list(dict.fromkeys(r["story"] for r in rows if r.get("story")))
        titles = list(dict.fromkeys(r["title"] for r in rows if r.get("title")))

        # 查缓存
        cache = self._load_translation_cache(excel_path)
        cache_fe = cache.get("feature_en", {})
        cache_st = cache.get("story_en", {})
        cache_ti = cache.get("title_en", {})

        uncached_fe = [f for f in features if f not in cache_fe]
        uncached_st = [s for s in stories if s not in cache_st]
        uncached_ti = [t for t in titles if t not in cache_ti]

        all_uncached = uncached_fe + uncached_st + uncached_ti

        if all_uncached:
            logger.info("\n🌐 翻译 %d 条中文标识符...", len(all_uncached))
            try:
                from prompts.extraction_prompts import translate_to_en_prompt
                prompt = translate_to_en_prompt()
                result = self._invoke_structured(prompt, TranslationResult,
                    method="json_mode",
                    features=json.dumps(uncached_fe, ensure_ascii=False),
                    stories=json.dumps(uncached_st, ensure_ascii=False),
                    titles=json.dumps(uncached_ti, ensure_ascii=False),
                )
            except Exception as e:
                logger.warning("LLM 翻译失败，全部使用拼音 Fallback: %s", e)
                result = None

            if result and isinstance(result, TranslationResult):
                for cn, en in result.feature_en.items():
                    cache_fe[cn] = self._sanitize_en(en)
                for cn, en in result.story_en.items():
                    cache_st[cn] = self._sanitize_en(en)
                for cn, en in result.title_en.items():
                    cache_ti[cn] = self._sanitize_en(en)

            # 拼音 Fallback：LLM 未返回或翻译缺失的条目
            for cn in uncached_fe:
                if cn not in cache_fe:
                    cache_fe[cn] = self._sanitize_en(self._pinyin_fallback(cn))
                    logger.warning("拼音 Fallback: feature '%s' → '%s'", cn, cache_fe[cn])
            for cn in uncached_st:
                if cn not in cache_st:
                    cache_st[cn] = self._sanitize_en(self._pinyin_fallback(cn))
                    logger.warning("拼音 Fallback: story '%s' → '%s'", cn, cache_st[cn])
            for cn in uncached_ti:
                if cn not in cache_ti:
                    cache_ti[cn] = self._sanitize_en(self._pinyin_fallback(cn))
                    logger.warning("拼音 Fallback: title '%s' → '%s'", cn, cache_ti[cn])

            # 存缓存
            cache["feature_en"] = cache_fe
            cache["story_en"] = cache_st
            cache["title_en"] = cache_ti
            self._save_translation_cache(excel_path, cache)

        return {
            "feature_en": cache_fe,
            "story_en": cache_st,
            "title_en": cache_ti,
        }

    # ==================== C6-1: 断言关键词解析 ====================

    class AssertionParseError(ValueError):
        """断言格式校验异常。"""

    _ASSERTION_PATTERN = re.compile(r'\[(eq|contains|ne|db)\]', re.IGNORECASE)
    _ASSERTION_INVALID_SPACE = re.compile(
        r'\[\s+(eq|contains|ne|db)\s*\]|\[\s*(eq|contains|ne|db)\s+\]',
        re.IGNORECASE,
    )  # 仅当关键词两侧至少有一处空格时命中

    @classmethod
    def _parse_assertion(cls, expected_text: str) -> tuple[str, str]:
        """从预期结果文本解析断言关键词。返回 (keyword_lower, rest_of_text)。

        Raises:
            AssertionParseError: 格式非法时抛出。
        """
        if re.search(r'\[\[|\]\]', expected_text):
            raise cls.AssertionParseError(f"断言格式非法（双层括号）: {expected_text[:60]}")
        if cls._ASSERTION_INVALID_SPACE.search(expected_text):
            raise cls.AssertionParseError(f"断言关键词含空格: {expected_text[:60]}")
        m = cls._ASSERTION_PATTERN.search(expected_text)
        if not m:
            raise cls.AssertionParseError(f"未找到断言关键词 [eq/contains/ne/db]: {expected_text[:60]}")
        keyword = m.group(1).lower()
        rest = expected_text[m.end():].strip()
        return keyword, rest

    def _generate_py_file(self, excel_path: str, project_name: str = None) -> dict:
        """Phase C V2: 按 feature 生成 .py 文件，fixture + parametrize 结构。

        同一 feature → 一个 .py 文件
        同一 story   → 一个 class（含 fixture + test functions）
        """
        logger.info("\n🐍 正在生成 Python 测试文件...")

        if not excel_path:
            logger.info("   ⚠️ 无 Excel 路径，跳过 .py 生成")
            return {"py_path": "", "py_file_name": "", "modules": 0, "cases": 0}

        from collections import defaultdict
        expanded_rows = self._read_excel_rows(excel_path)

        if not expanded_rows:
            raise ValueError("Excel 中无数据")

        # C4: 英文翻译
        translations = self._translate_to_en(excel_path, expanded_rows)
        feature_en_map = translations["feature_en"]
        story_en_map = translations["story_en"]
        title_en_map = translations["title_en"]

        # C5: 读取共享前置（Sheet2）
        shared_pres = self._read_shared_preconditions(excel_path)
        pre_by_id = {p["id"]: p for p in shared_pres}

        # 按 feature → story → cases 分组
        features = defaultdict(lambda: defaultdict(list))
        for r in expanded_rows:
            features[r["feature"]][r["story"]].append(r)

        import_header = (
            "import pytest\n"
            "import allure\n"
            "from common.readyaml import ReadYamlData, get_testcase_yaml\n"
            "from common.sendrequests import SendRequests\n"
            "from common.recordlog import logs\n"
            "from base.apiutil import RequestsBase\n"
        )

        output_base = os.path.dirname(excel_path)
        total_modules = 0
        total_cases = 0
        py_files = []

        for feature_cn, stories in features.items():
            feature_en = feature_en_map.get(feature_cn, self._sanitize_en(self._pinyin_fallback(feature_cn)))
            feature_dir = os.path.join(output_base, feature_en)
            os.makedirs(feature_dir, exist_ok=True)

            # __init__.py
            init_path = os.path.join(feature_dir, "__init__.py")
            if not os.path.exists(init_path):
                with open(init_path, "w", encoding="utf-8") as f:
                    f.write("# auto-generated\n")

            class_blocks = []
            for story_cn, cases in stories.items():
                story_en = story_en_map.get(story_cn, self._sanitize_en(self._pinyin_fallback(story_cn)))
                class_slug = re.sub(r'(?<!^)(?=[A-Z])', '_', story_en).lower()
                total_modules += 1

                # 收集该 story 的共享前置引用
                pre_ids = set()
                for c in cases:
                    pre_str = c.get("preconditions", "")
                    if pre_str and pre_str != "无":
                        for pid in pre_str.split(","):
                            pid = pid.strip()
                            if pid.startswith("PRE-"):
                                pre_ids.add(pid)

                # 生成 fixture
                fixture_code = ""
                if pre_ids:
                    fixture_code = (
                        f'\n@pytest.fixture(scope="class")\n'
                        f'def setup_{class_slug}():\n'
                        f'    read = ReadYamlData()\n'
                        f'    base = RequestsBase()\n'
                        f'    base.specification_yaml(get_testcase_yaml(\n'
                        f'        \'./testcase/{feature_en}/setup_data/setup_{class_slug}.yaml\'))\n'
                        f'    yield\n'
                        f'    base.specification_yaml(get_testcase_yaml(\n'
                        f'        \'./testcase/{feature_en}/setup_data/teardown_{class_slug}.yaml\'))\n'
                    )
                else:
                    fixture_code = (
                        f'\n@pytest.fixture(scope="class")\n'
                        f'def setup_{class_slug}():\n'
                        f'    pass\n'
                        f'    yield\n'
                    )

                # 生成 test functions — run_blocks 加载单个 YAML（含所有 step）
                func_lines = []
                for i, c in enumerate(cases, 1):
                    title_cn = c["title"]
                    func_en = title_en_map.get(
                        title_cn,
                        "test_" + self._sanitize_en(self._pinyin_fallback(title_cn))
                    )
                    if not func_en.startswith("test_"):
                        func_en = "test_" + func_en
                    total_cases += 1

                    func_lines.append(
                        f'    @allure.title(\'{title_cn}\')\n'
                        f'    @pytest.mark.order({i})\n'
                        f'    def {func_en}(self):\n'
                        f'        RequestsBase().run_blocks(\n'
                        f'            \'./testcase/{feature_en}/{func_en}/test_data.yaml\')\n'
                    )

                # 组装 class
                usefixtures = f'\n@pytest.mark.usefixtures("setup_{class_slug}")' if pre_ids else ''
                class_code = (
                    f'{fixture_code}\n'
                    f'@allure.story(\'{story_cn}\')\n'
                    f'@pytest.mark.danyuan'
                    f'{usefixtures}\n'
                    f'class Test{story_en}:\n'
                    + '\n'.join(func_lines)
                )
                class_blocks.append(class_code)

            # 写 .py 文件
            file_name = f"test_{feature_en}.py"
            full_content = import_header + "\n" + "\n".join(class_blocks)
            py_path = os.path.join(feature_dir, file_name)
            tmp_path = py_path + ".tmp"
            try:
                os.remove(tmp_path)
            except OSError:
                pass
            with open(tmp_path, "w", encoding="utf-8", newline="\r\n") as f:
                f.write(full_content)
            os.replace(tmp_path, py_path)
            py_files.append(py_path)
            logger.info(f"   📄 {file_name} ({len(stories)} classes, {sum(len(v) for v in stories.values())} cases)")

        logger.info(f"   📦 {len(py_files)} 个 .py 文件, {total_modules} 个 class, {total_cases} 条用例")

        result = {
            "py_path": py_files[0] if py_files else "",
            "py_file_name": ", ".join(os.path.basename(p) for p in py_files),
            "modules": total_modules,
            "cases": total_cases,
        }
        self._log_node_output("generate_py_file", result)
        return result

    def _generate_one_yaml(self, row: dict, api_defs_json: str, user_ctx: str,
                           output_path: str, repair_ctx: dict | None = None) -> str:
        """Phase C V2 两段式 YAML 生成：thinking 分析 → json_mode 单次输出。

        与 Phase B 的 analyze_test_points_raw → generate_excel_plan 模式一致：
          - 第一阶段：thinking on，自由文本分析用例数据需求（全文落 thinking_trace.log）
          - 第二阶段：thinking off + json_mode，输出结构化 YAML

        校验失败不做 inline 重试（json_mode 无思考，原地重打无法纠正"信念型错误"）—— 直接抛异常，由 _run_yaml_rounds 登记后
        进入轮末思考自查修复循环。

        repair_ctx（修复轮时非 None）:
          {prior_output, error_detail, error_pattern_summary, round_no, post_check_issues}
        """
        from prompts.extraction_prompts import (
            analyze_yaml_data_prompt, format_yaml_data_prompt, repair_yaml_data_prompt,
        )
        from observability import log_thinking

        factory_methods_text = self._load_factory_methods()
        test_case_logic = f"执行步骤: {row['steps']}\n预期结果: {row.get('expected', '')}"
        case_label = (
            f"{row.get('case_id') or os.path.basename(os.path.dirname(output_path))}"
            f" | {os.path.basename(os.path.dirname(output_path))}/{os.path.basename(output_path)}"
        )

        # === 阶段 1：thinking 分析（首轮=需求分析 / 修复轮=带错误上下文自查） ===
        if repair_ctx:
            think_prompt = repair_yaml_data_prompt()
            prompt_vars = dict(
                api_definitions=api_defs_json,
                test_case_logic=test_case_logic,
                user_context=user_ctx,
                data_factory_methods=factory_methods_text,
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
                api_definitions=api_defs_json,
                test_case_logic=test_case_logic,
                user_context=user_ctx,
                data_factory_methods=factory_methods_text,
            )
            node_label = "analyze_yaml_data"
            prompt_label = "analyze_yaml_data_prompt"

        llm_kwargs = {"extra_body": {"thinking": {"type": "enabled"}}}
        bound_llm = self.llm.bind(**llm_kwargs)
        analysis_result = bound_llm.invoke(think_prompt.format_messages(**prompt_vars))
        analysis = analysis_result.content if hasattr(analysis_result, "content") else str(analysis_result)

        # Phase C 思考全文与 Phase B 同规格写入 thinking_trace.log
        log_thinking(node_label, case_label, analysis, prompt_label=prompt_label)

        # === 阶段 2：json_mode 结构化输出（max_retries=0，失败即抛给登记器） ===
        format_prompt = format_yaml_data_prompt()
        result = self._invoke_structured(format_prompt, TestData,
            max_retries=0,
            method="json_mode",
            data_analysis=analysis,
            api_definitions=api_defs_json,
            test_case_logic=test_case_logic,
            user_context=user_ctx,
            data_factory_methods=factory_methods_text,
        )

        yaml_text = yaml.dump(
            [step.model_dump(exclude_none=True, by_alias=True) for step in result.data],
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

        yaml_tasks = []
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
                            teardown_lines.append(
                                f"# 清理 {pid}: {pre['name']}\n"
                                f"根据 {pid} 的创建步骤逆向操作：{pre['steps'][:200]}"
                            )

                    setup_text = "\n".join(setup_lines)
                    teardown_text = "\n".join(teardown_lines)

                    setup_yaml = os.path.join(setup_dir, f"setup_{class_slug}.yaml")
                    teardown_yaml = os.path.join(setup_dir, f"teardown_{class_slug}.yaml")
                    # LLM 生成可执行的 YAML 数据
                    yaml_tasks.append((
                        {"steps": setup_text, "expected": "",
                         "case_id": f"setup_{class_slug}"}, setup_yaml))
                    yaml_tasks.append((
                        {"steps": teardown_text, "expected": "",
                         "case_id": f"teardown_{class_slug}"}, teardown_yaml))

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
                    yaml_tasks.append((c, yaml_path))

        total = len(yaml_tasks)
        if not total:
            logger.info("   ⚠️ 没有需要生成的 YAML")
            result = dict(_empty)
            self._log_node_output("generate_all_yamls", result)
            return result

        logger.info(f"   📋 共需生成 {total} 个 YAML 文件（含 setup/teardown），"
                    f"并发 {config.YAML_CONCURRENCY} 个线程，"
                    f"修复轮上限 {config.YAML_REPAIR_ROUNDS}")

        result = self._run_yaml_rounds(yaml_tasks, api_defs_json, user_ctx, output_base)

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
                        _affected_tasks, api_defs_json, user_ctx, output_base,
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

    def _run_yaml_rounds(self, yaml_tasks: list, api_defs_json: str, user_ctx: str,
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
