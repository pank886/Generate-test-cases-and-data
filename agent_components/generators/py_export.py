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

    # Sheet1「fixture等级」列的取值 = 「前置按编号逐个挂」（见 `_per_pre_fixtures`）。
    # 生产侧（判定表支线，2026-09-17 归档）的字面量在
    # `有作用/PhaseB/rule_tables/yaml_kernel.py` 的 `FIXTURE_LEVEL_PER_PRE` —— 取值一致。
    # 原先的跨模块回归锁 test_rule_table_fixture_split.py::TestNaming::test_py_export_agrees
    # 随支线归档消失；本侧常量值未变。
    _PER_PRE_LEVEL = "逐前置"

    # 「逐前置」模式才写入 .py 顶部的显示名助手。
    # Allure 只认**原始函数**上的 `__allure_display_name__`：pytest.fixture 包装后
    # 拿到的是 FixtureFunctionDefinition，在它上面赋值/传 name= 都不改显示名（实测）。
    _DISPLAY_NAME_HELPER = (
        "\n\n"
        "def _fixture_with_display_name(name_zh):\n"
        "    \"\"\"给前置 fixture 起中文显示名（须在 pytest.fixture 包装**之前**挂属性）。\"\"\"\n"
        "    def deco(fn):\n"
        "        fn.__allure_display_name__ = name_zh\n"
        "        return pytest.fixture(scope=\"class\")(fn)\n"
        "    return deco\n"
    )

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

    @staticmethod
    def _pre_id_slug(pre_id: str) -> str:
        """前置编号 → 文件名/fixture 名里的 slug（`PRE-RT-G-P1` → `pre_rt_g_p1`）。

        与生产侧（判定表支线，2026-09-17 归档）`有作用/PhaseB/rule_tables/yaml_kernel.py`
        的 `pre_id_slug` **同一条规则**：那边用它在
        `setup_data/` 下写 `setup_<slug>__<编号>.yaml`，这边用它拼 fixture 名与
        `run_blocks` 的路径 —— 要改就两边一起改，否则运行时找不到文件。
        """
        return str(pre_id).lower().replace("-", "_")

    def _per_pre_fixtures(self, class_slug: str, testcase_rel: str,
                          cases: list[dict], pre_ids: set,
                          pres: list[dict]) -> tuple[str, dict]:
        """「逐前置」模式：每个前置编号一个 class 级 fixture（顺序 = Sheet2）。

        为什么要按编号拆：Allure 的 fixture 容器 children = **所有引用它的用例**
        （`_test_fixtures` 走 `fixturenames` 传递闭包，无开关可关），所以
        「每条用例只显示自己的前置」只能靠每条用例引用不同的 fixture。

        依赖走 **fixture 参数** —— pytest 据此保证「清场 → 建场 → 车辆记录」的顺序，
        等价于原来靠块顺序保证的父子/他场关系；收尾 teardown 只挂在**依赖图的根**上
        （它最先建立、最后收尾），这样全文件只有一处 teardown 调用 ——
        导出侧靠这一点整段注释掉收尾，见判定表支线（2026-09-17 归档）
        `有作用/PhaseB/rule_tables/phase_c_export.py` 的 `_comment_out_teardown`。

        Returns:
            (fixture_code, {用例编号: [fixture 名…]})，后者挂到方法上。
        """
        by_id = {p["id"]: p for p in pres}

        def deps(pid: str) -> list[str]:
            raw = str(by_id[pid].get("depends_on") or "")
            return [d.strip() for d in raw.split(",") if d.strip()]

        # 依赖闭包：**只被依赖、没有用例直接引用**的编号（如 PO 组 —— 只有 P4 依赖它）
        # 也必须是真 fixture，否则 P4 的 fixture 参数指向不存在的名字 —— 用到它时
        # pytest 报 fixture not found（ERROR，不是断言失败；`--collect-only` 不报，跑起来才炸）。
        wanted, frontier = set(pre_ids), list(pre_ids)
        while frontier:
            pid = frontier.pop()
            if pid not in by_id:
                raise ValueError(
                    f"逐前置模式：引用的前置编号在 Sheet2 里没有定义 ['{pid}']"
                    f"（每个编号都得有一行，生成侧才知道它跑哪个 YAML、依赖谁）")
            for d in deps(pid):
                if d not in wanted:
                    wanted.add(d)
                    frontier.append(d)

        ordered = [p["id"] for p in pres if p["id"] in wanted]
        if not ordered:
            raise ValueError("逐前置模式：没有收集到任何前置编号")

        def fx(pid: str) -> str:
            return f"setup_{class_slug}__{self._pre_id_slug(pid)}"

        root = next((pid for pid in ordered if not deps(pid)), ordered[0])

        blocks = []
        for pid in ordered:
            lines = [
                "",
                f"@_fixture_with_display_name({by_id[pid]['name']!r})",
                f"def {fx(pid)}({', '.join(fx(d) for d in deps(pid))}):",
                "    read = ReadYamlData()",
                "    base = RequestsBase()",
                "    base.run_blocks(",
                f"        '{testcase_rel}/setup_data/{fx(pid)}.yaml')",
                "    yield",
            ]
            if pid == root:
                lines += [
                    "    base.run_blocks(",
                    f"        '{testcase_rel}/setup_data/teardown_{class_slug}.yaml')",
                ]
            blocks.append("\n".join(lines) + "\n")

        reqs = {}
        for r in cases:
            cid = r.get("case_id")
            if not cid:
                continue
            ids = [i.strip() for i in str(r.get("preconditions") or "").split(",")
                   if i.strip()]
            reqs[cid] = [fx(i) for i in ids]
        return "\n".join(blocks), reqs

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
        needs_display_helper = False

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
                per_pre = any((r.get("fixture_level") or "") == self._PER_PRE_LEVEL
                              for r in cases)
                method_reqs = {}
                if per_pre:
                    fixture_code, method_reqs = self._per_pre_fixtures(
                        class_slug, testcase_rel, cases, pre_ids, shared_pres)
                    needs_display_helper = True
                elif pre_ids:
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

                    # 逐前置模式：该用例自己那批前置挂到**方法**上（类上不挂）
                    req = method_reqs.get(c.get("case_id")) or []
                    method_marks = (
                        f'    @pytest.mark.usefixtures({", ".join(repr(n) for n in req)})\n'
                        if req else '')

                    func_lines.append(
                        f'    @allure.title(\'{title_cn}\')\n'
                        f'    @pytest.mark.order({i})\n'
                        f'{method_marks}'
                        f'    def {func_en}(self):\n'
                        f'        RequestsBase().run_blocks(\n'
                        f'            \'{testcase_rel}/{func_en}/test_data.yaml\')\n'
                    )

                # 组装 class
                if per_pre:
                    usefixtures = ''
                else:
                    usefixtures = (f'\n@pytest.mark.usefixtures("setup_{class_slug}")'
                                   if pre_ids else '')
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
            # 非「逐前置」模式不写显示名助手 → 内容与本改动之前逐字相同
            helper = self._DISPLAY_NAME_HELPER if needs_display_helper else ""
            full_content = import_header + helper + "\n" + "\n".join(class_blocks)
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
