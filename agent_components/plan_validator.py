"""Excel 计划校验/拦截模块（Phase B 生成/处理解耦后独立管理）。

背景：校验逻辑原先散落在 nodes.py 的两份重复副本（首轮 / 重试），
     2026-08 收敛为本模块，职责单一 + 便于单测。

职责：
  1. validate()               —— 对 ExcelPlanV2 做字段/前置引用/步骤对齐/断言格式/URL 有效性校验
  2. aggregate_block_reasons()—— 拦截原因按错误类型聚合：同类一条（含计数+受影响用例），
                                 不同类各自一条，供 repair_excel_plan_prompt 的
                                 {block_reasons} 占位符使用。

设计要点：
  - 8 类固定错误类型（ERR_TYPES），聚合按类型分组，杜绝逐条重复刷屏。
  - URL 有效性校验（invalid_url）：步骤中接口路径未命中 api_definitions 任一真实接口
    即视为疑似拼写错误；单段路径（如 /export、/login）同样不豁免。
    校验器按入参 api_urls 可选启用，不传则不检查（纯确定性代码，不依赖 LLM）。
"""

import re
from typing import Any, Optional

# 断言格式校验正则（与框架 generators._ASSERTION_PATTERN 一致：行内 [tag]，非行号前缀）
_ASSERT_OK = re.compile(r"\[(eq|contains|ne|db)\]", re.IGNORECASE)
_ASSERT_BAD_SPACE = re.compile(r"\[\s+(eq|contains|ne|db)\s*\]|\[\s*(eq|contains|ne|db)\s+\]")
_ASSERT_DOUBLE = re.compile(r"\[\[|\]\]")

# 步骤文本中的候选接口路径：仅匹配 ASCII 路径（排除中文步骤文本误报），如 /xxx上传 不会被当作 URL
_URL_RE = re.compile(r"/[A-Za-z][A-Za-z0-9_/{}.-]*")


def extract_url_paths(text: str) -> list:
    """从步骤文本提取候选接口路径。

    排除中文步骤文本误报；去除尾部标点（. , ; ) ] } 等）与 query string
    （? 不在字符类中，正则本身会在 ? 处截断）。
    """
    out = []
    for m in _URL_RE.finditer(text or ""):
        path = m.group(0).rstrip("./,;)]}")
        if path and path.count("/") >= 1:
            out.append(path)
    return out


def match_api_template(url: str, tpl: str) -> bool:
    """url 与接口路径模板匹配：{xxx} 视为任意单段通配，末尾 / 归一化。

    例：/payConfig/delete/ 命中模板 /payConfig/delete（末尾斜杠归一化）；
        /meter/ABC-123 命中模板 /meter/{code}。
    """
    u = [s for s in url.rstrip("/").split("/") if s]
    t = [s for s in tpl.rstrip("/").split("/") if s]
    if len(u) != len(t):
        return False
    for a, b in zip(u, t):
        if b.startswith("{") and b.endswith("}"):
            continue
        if a != b:
            return False
    return True


class ValidationResult:
    """一次校验的结果。"""

    __slots__ = ("failed_details", "all_confirmed", "block_reasons")

    def __init__(self, failed_details, all_confirmed, block_reasons):
        self.failed_details = failed_details   # [(idx, case_dict, errs)]
        self.all_confirmed = all_confirmed     # [TestCaseRow]
        self.block_reasons = block_reasons     # [str] 聚合后的拦截原因


class ExcelPlanValidator:
    """Excel 计划校验器：字段 / 前置引用 / 步骤对齐 / 断言格式。"""

    # 9 类固定错误类型（聚合用），顺序即输出顺序
    ERR_TYPES = {
        "pre_missing": "引用前置不存在",
        "field_empty": "必填字段为空",
        "steps_expected_mismatch": "步骤与预期数量不一致",
        "expected_empty_line": "预期存在空行",
        "expected_double_bracket": "预期含双层括号",
        "expected_bad_space": "断言关键词含空格",
        "expected_missing_assert": "预期缺少断言关键词",
        "invalid_url": "疑似URL拼写错误",
        "db_forbidden": "db断言被禁止",
    }

    # ── URL 有效性校验：返回疑似拼写错误的路径列表 ──
    @staticmethod
    def check_urls(steps: str, api_urls: list) -> list:
        """校验步骤文本中的接口路径是否命中真实接口。

        单段路径（如 /export、/login）同样需要命中真实接口，不豁免；
        未命中任一真实接口模板即视为疑似拼写错误。api_urls 为空时不检查。
        """
        if not api_urls:
            return []
        bad = []
        for path in extract_url_paths(steps):
            if any(match_api_template(path, tpl) for tpl in api_urls):
                continue
            bad.append(path)
        return bad

    # ── 单用例校验：返回具体错误信息列表 ──
    @staticmethod
    def check_case(tc: Any, pre_ids: set, api_urls: Optional[list] = None,
                   db_schema: str = "") -> list:
        """校验单个用例，返回错误信息列表（空列表 = 通过）。

        db_schema: 数据库表结构信息（占位）；为空时拦截 expected 中的 [db] 断言
                   （无表结构无法写正确 SQL，2026-08-04 问题 2）。
        """
        errs = []

        # 1. 必填字段为空
        for fld, lbl in [("id", "编号"), ("story", "子模块"), ("title", "标题"),
                         ("steps", "步骤"), ("expected", "预期")]:
            if not getattr(tc, fld, ""):
                errs.append(f"{lbl}为空")

        # 2. 前置引用不存在
        for pid in tc.preconditions:
            if pid not in pre_ids:
                errs.append(f"引用前置 {pid} 不存在")

        # 3. 步骤/预期行数对齐 + 预期断言格式
        if tc.steps and tc.expected:
            ns = tc.steps.count("\n") + 1
            ne = tc.expected.count("\n") + 1
            if ns != ne:
                errs.append(f"步骤({ns}条)与预期({ne}条)数量不一致")
            for ei, line in enumerate(tc.expected.split("\n"), 1):
                line_s = line.strip()
                if not line_s:
                    errs.append(f"预期第{ei}条为空行")
                elif _ASSERT_DOUBLE.search(line_s):
                    errs.append(f"预期第{ei}条含双层括号: {line_s[:40]}")
                elif _ASSERT_BAD_SPACE.search(line_s):
                    errs.append(f"预期第{ei}条断言关键词含空格: {line_s[:40]}")
                elif not _ASSERT_OK.search(line_s):
                    errs.append(f"预期第{ei}条缺少断言关键词: {line_s[:40]}")

        # 4. 步骤 URL 有效性（疑似拼写错误，2026-08-03 建议 3）
        if api_urls and tc.steps:
            for bu in ExcelPlanValidator.check_urls(tc.steps, api_urls):
                errs.append(f"疑似URL拼写错误: {bu}（未匹配 api_definitions 中任一真实接口）")

        # 5. db 断言拦截（db_schema 为空时，2026-08-04 问题 2）
        if not db_schema and tc.expected:
            for ei, line in enumerate(tc.expected.split("\n"), 1):
                if re.search(r"\[db\]", line, re.IGNORECASE):
                    errs.append(
                        f"预期第{ei}条含 db 断言，但数据库表结构信息为空（db_schema 未提供），"
                        "无法生成正确 SQL，请改用 [eq]/[contains]/[ne]"
                    )

        return errs

    # ── 错误信息 → 错误类型 ──
    @staticmethod
    def classify(err: str) -> str:
        """把具体错误信息归类到 8 类固定错误类型。"""
        if "引用前置" in err:
            return "pre_missing"
        if "db 断言" in err:
            return "db_forbidden"
        if "为空" in err and any(k in err for k in ("编号", "子模块", "标题", "步骤", "预期")):
            return "field_empty"
        if "数量不一致" in err:
            return "steps_expected_mismatch"
        if "为空行" in err:
            return "expected_empty_line"
        if "双层括号" in err:
            return "expected_double_bracket"
        if "含空格" in err:
            return "expected_bad_space"
        if "缺少断言关键词" in err:
            return "expected_missing_assert"
        if "疑似URL拼写错误" in err:
            return "invalid_url"
        return "other"

    # ── 整体校验 ──
    @classmethod
    def validate(cls, plan, test_analysis: str = "",
                 pre_ids: Optional[set] = None,
                 api_urls: Optional[list] = None,
                 db_schema: str = "") -> ValidationResult:
        """校验整个 plan。

        Args:
            plan: ExcelPlanV2（含 shared_preconditions + test_cases）
            test_analysis: 测试分析文本（可空；当共享前置在分析报告里列出但
                           shared_preconditions 为空时，给出针对性提示）
            pre_ids: 前置 ID 集合（默认取 plan.shared_preconditions）
            api_urls: 真实接口路径模板列表（含 {xxx} 路径参数），非空时启用
                      步骤 URL 有效性校验（含共享前置 steps 一起校验）
            db_schema: 数据库表结构信息（占位）；为空时拦截 expected 中的 [db] 断言

        Returns:
            ValidationResult(failed_details, all_confirmed, block_reasons)
        """
        pres = plan.shared_preconditions
        if pre_ids is None:
            pre_ids = {p.id for p in pres}
        _missing_pres_in_plan = (not pre_ids) and bool(test_analysis) \
            and "## 共享前置" in test_analysis

        failed_details = []
        all_confirmed = []
        seen_ids = set()

        # 0. 共享前置步骤 URL 校验（与用例一起校验，2026-08-03 建议 3）
        #    前置失败行 id 为 PRE-xxx，走失败列表进入修复轮；修复轮输出修正后的
        #    shared_preconditions 按 id 合并落地。
        if api_urls:
            for pre in pres:
                bad = cls.check_urls(pre.steps, api_urls)
                if bad:
                    failed_details.append((
                        -1,
                        {"id": pre.id, "story": "共享前置", "title": pre.name,
                         "preconditions": [], "steps": pre.steps, "expected": pre.expected,
                         "mutates_data": False, "is_negative_test": False},
                        [f"疑似URL拼写错误: {u}（未匹配 api_definitions 中任一真实接口）" for u in bad],
                    ))

        for i, tc in enumerate(plan.test_cases, 1):
            if tc.id in seen_ids:
                continue
            errs = cls.check_case(tc, pre_ids, api_urls, db_schema)
            if _missing_pres_in_plan and any("引用前置" in e for e in errs):
                errs = [
                    e.replace(
                        "不存在",
                        "不存在——测试分析报告中已列出，但 shared_preconditions 为空，"
                        "请将其定义加入 shared_preconditions 数组")
                    for e in errs
                ]
            if errs:
                failed_details.append((i, tc.model_dump(), errs))
            else:
                all_confirmed.append(tc)
                seen_ids.add(tc.id)

        block_reasons = cls.aggregate_block_reasons(failed_details)
        return ValidationResult(failed_details, all_confirmed, block_reasons)

    # ── 拦截原因聚合（同类一条，不同类各自一条）──
    @staticmethod
    def aggregate_block_reasons(failed_details: list) -> list:
        """按错误类型聚合拦截原因。

        同一类型问题只返回一条（含计数 + 受影响用例列表 + 代表性信息）；
        不同类型各自一条。返回 list[str]。
        """
        type_buckets = {}  # type → {"count", "cases", "sample"}
        for _idx, case_dict, errs in failed_details:
            tc_id = case_dict.get("id", "?")
            for err in errs:
                etype = ExcelPlanValidator.classify(err)
                bucket = type_buckets.setdefault(
                    etype, {"count": 0, "cases": [], "sample": err})
                bucket["count"] += 1
                if tc_id not in bucket["cases"]:
                    bucket["cases"].append(tc_id)

        reasons = []
        for etype, label in ExcelPlanValidator.ERR_TYPES.items():
            bucket = type_buckets.get(etype)
            if not bucket:
                continue
            cases = bucket["cases"]
            case_txt = ", ".join(cases[:8]) + ("..." if len(cases) > 8 else "")
            reasons.append(
                f"被拦截：{label} —— 影响 {len(cases)} 条用例 ({case_txt})，"
                f"示例：{bucket['sample']}"
            )
        # 未归类错误兜底
        if "other" in type_buckets:
            bucket = type_buckets["other"]
            reasons.append(
                f"被拦截：其他 —— 影响 {len(bucket['cases'])} 条用例，示例：{bucket['sample']}"
            )
        return reasons
