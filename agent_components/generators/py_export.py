"""Phase C: pytest 测试文件生成 + 断言解析 Mixin

拆分自 generators/__init__.py（2026-08-07 大文件拆分）。

依赖宿主类提供: self._log_node_output；跨 Mixin 依赖 ExcelMixin/TranslationMixin
"""
import os
import re

import infrastructure.config as config
from infrastructure.observability import get_logger

logger = get_logger(__name__)


class PyExportMixin:
    """按 feature/story 生成 .py 测试文件 + 断言关键词解析"""

    # ==================== C6-1: 断言关键词解析 ====================

    class AssertionParseError(ValueError):
        """断言格式校验异常。"""

    _ASSERTION_PATTERN = re.compile(r'\[(eq|contains|ne|db)\]', re.IGNORECASE)
    _ASSERTION_INVALID_SPACE = re.compile(
        r'\[\s+(eq|contains|ne|db)\s*\]|\[\s*(eq|contains|ne|db)\s+\]',
        re.IGNORECASE,
    )  # 仅当关键词两侧至少有一处空格时命中

    @staticmethod
    def _takeover_export_assertions(steps) -> None:
        """通用断言规范化（写盘前兜底）。2026-08-13 泛化自导出接管。

        三层防御之一（prompt 铁律为核心、此处代码兜底、生成后检测扫描）：
          1. is_export 标注的步骤（2026-08-04 问题 3）：强制 validation =
             contains: {status_code: 200}（保留原导出兜底行为）
          2. 所有步骤 eq/ne 对 status_code 的断言改写为 contains（2026-08-12 问题 2）：
             status_code 特殊处理只在 contains_assert；eq/ne 按 JSONPath 解析，
             响应体无 status_code 字段必败
          3. contains 裸字符串 → contains: {message: <字符串>}（2026-08-12 问题 4）：
             框架 contains_assert 对 str 调用 .items() 抛 AttributeError，整方法崩溃
        steps 为 StepData 列表，就地修改。
        """
        for step in steps:
            ann = step.baseInfo.get("_annotations", {})
            for tc in step.testCase:
                if ann.get("is_export", {}).get("active"):
                    tc.validation = [{"contains": {"status_code": 200}}]
                    continue
                normalized = []
                for v in tc.validation:
                    if not (isinstance(v, dict) and len(v) == 1):
                        normalized.append(v)
                        continue
                    op, payload = next(iter(v.items()))
                    if op in ("eq", "ne") and isinstance(payload, dict) \
                            and any(str(k).lstrip("$.") == "status_code"
                                    for k in payload):
                        normalized.append({"contains": payload})
                    elif op == "contains" and not isinstance(payload, dict):
                        normalized.append({"contains": {"message": payload}})
                    else:
                        normalized.append(v)
                tc.validation = normalized

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

            # 计算 PY 文件中引用 YAML 的相对路径（pytest 从 PYCHARM_MISC 运行）
            pytest_root = config.PYCHARM_MISC or os.getcwd()
            try:
                rel_to_pytest = os.path.relpath(feature_dir, pytest_root).replace(os.sep, '/')
            except ValueError:
                # 跨盘符（Windows）fallback：仅用 feature_en
                rel_to_pytest = f"testcase/{feature_en}"
            testcase_rel = f"./{rel_to_pytest}"

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
                        f'    base.run_blocks(\n'
                        f'        \'{testcase_rel}/setup_data/setup_{class_slug}.yaml\')\n'
                        f'    yield\n'
                        f'    base.run_blocks(\n'
                        f'        \'{testcase_rel}/setup_data/teardown_{class_slug}.yaml\')\n'
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
                        f'            \'{testcase_rel}/{func_en}/test_data.yaml\')\n'
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
