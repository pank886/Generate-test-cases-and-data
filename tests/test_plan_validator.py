"""ExcelPlanValidator 单元测试：URL 有效性校验（invalid_url，2026-08-03 建议 3）。

不依赖 LLM / ChromaDB / 服务端，纯逻辑测试。

覆盖：
  1. extract_url_paths  — 步骤文本提取候选接口路径（排除中文误报/尾部标点/query string）
  2. match_api_template — {xxx} 通配 + 末尾 / 归一化
  3. check_urls         — 真实/拼写错误/单段路径判定
  4. check_case         — 单用例步骤 URL 校验（api_urls 可选）
  5. validate           — 共享前置 steps URL 一起校验 + 聚合分类
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from prompts.response_model import TestCaseRow as PlanTestCaseRow, SharedPrecondition, ExcelPlanV2
from agent_components.plan_validator import (
    ExcelPlanValidator,
    extract_url_paths,
    match_api_template,
)


REAL_URLS = [
    "/electricMeter/getPage",
    "/electricMeter/detail",
    "/payConfig/delete",
    "/meter/{code}/query",
    "/export",
    "/importTemplate/download",
]


# ============================================================
# 1. extract_url_paths
# ============================================================

class TestExtractUrlPaths:
    def test_extracts_multi_segment_ascii(self):
        assert extract_url_paths("1.调用 /payConfig/delete 接口删除配置") == ["/payConfig/delete"]

    def test_excludes_chinese_only_steps(self):
        assert extract_url_paths("1.调用新增电表接口，名称测试电表A") == []

    def test_strips_trailing_slash(self):
        # 尾部 / 被 rstrip 去掉；匹配模板时末尾 / 归一化，不影响命中
        assert extract_url_paths("1.调用 /payConfig/delete/ 接口") == ["/payConfig/delete"]

    def test_stops_at_query_string(self):
        # ? 不在字符类中，正则天然截断 → query 参数名不被当作路径
        assert extract_url_paths("1.调用 /electricMeter/getPage?pageNum=1") == ["/electricMeter/getPage"]

    def test_strips_trailing_punctuation(self):
        assert extract_url_paths("调用 /export 导出。") == ["/export"]

    def test_path_param_literal_kept(self):
        assert extract_url_paths("1.调用 /meter/{code}/query") == ["/meter/{code}/query"]


# ============================================================
# 2. match_api_template
# ============================================================

class TestMatchApiTemplate:
    def test_exact_match(self):
        assert match_api_template("/electricMeter/getPage", "/electricMeter/getPage")

    def test_trailing_slash_normalized(self):
        # changelog round-4 案例：/payConfig/delete/（trailing slash）应命中 /payConfig/delete
        assert match_api_template("/payConfig/delete/", "/payConfig/delete")
        assert match_api_template("/payConfig/delete", "/payConfig/delete/")

    def test_path_param_wildcard(self):
        assert match_api_template("/meter/ABC-123", "/meter/{code}/query") is False
        assert match_api_template("/meter/ABC/query", "/meter/{code}/query")

    def test_segment_count_mismatch(self):
        assert match_api_template("/electricMeter/getPage/extra", "/electricMeter/getPage") is False

    def test_segment_value_mismatch(self):
        # changelog round-3 案例：/electrictMeter/getPage（多打一个 t）应不命中
        assert match_api_template("/electrictMeter/getPage", "/electricMeter/getPage") is False


# ============================================================
# 3. check_urls
# ============================================================

class TestCheckUrls:
    def test_real_url_not_flagged(self):
        assert ExcelPlanValidator.check_urls("1.调用 /electricMeter/getPage 查询", REAL_URLS) == []

    def test_typo_flagged(self):
        # 多打一个 t → 未命中任一真实接口
        assert "/electrictMeter/getPage" in ExcelPlanValidator.check_urls(
            "1.调用 /electrictMeter/getPage", REAL_URLS)

    def test_single_segment_match_not_flagged(self):
        # 单段路径命中真实接口 → 不误报
        assert ExcelPlanValidator.check_urls("1.调用 /export 导出数据", REAL_URLS) == []

    def test_single_segment_mismatch_flagged(self):
        # 单段路径未命中真实接口 → 判错（2026-08-03 决定：单路径也判错）
        assert "/exportx" in ExcelPlanValidator.check_urls("1.调用 /exportx 接口", REAL_URLS)

    def test_empty_api_urls_skips_check(self):
        assert ExcelPlanValidator.check_urls("1.调用 /nonexistent/foo", []) == []

    def test_multiple_bad_urls(self):
        bad = ExcelPlanValidator.check_urls(
            "1.调用 /electrictMeter/getPage 2.调用 /payConfig/delet 删除", REAL_URLS)
        assert "/electrictMeter/getPage" in bad and "/payConfig/delet" in bad


# ============================================================
# 4. check_case（单用例）
# ============================================================

class TestCheckCaseUrl:
    @staticmethod
    def _tc(steps: str) -> PlanTestCaseRow:
        return PlanTestCaseRow(
            id="TC-001", story="模块", title="用例", preconditions=[],
            steps=steps, expected="1.[eq]成功",
        )

    def test_bad_url_adds_error(self):
        errs = ExcelPlanValidator.check_case(self._tc("1.调用 /electrictMeter/getPage"), set(), REAL_URLS)
        assert any("疑似URL拼写错误" in e for e in errs)

    def test_good_url_no_error(self):
        errs = ExcelPlanValidator.check_case(self._tc("1.调用 /electricMeter/getPage"), set(), REAL_URLS)
        assert not any("疑似URL拼写错误" in e for e in errs)

    def test_no_api_urls_skips(self):
        # 不传 api_urls → 不启用 URL 校验，仅常规字段校验
        errs = ExcelPlanValidator.check_case(self._tc("1.调用 /whatever/foo"), set())
        assert not any("疑似URL拼写错误" in e for e in errs)

    def test_classify_invalid_url(self):
        assert ExcelPlanValidator.classify("疑似URL拼写错误: /x/y（未匹配 api_definitions 中任一真实接口）") == "invalid_url"


# ============================================================
# 5. validate（共享前置一起校验）
# ============================================================

class TestValidateUrl:
    @staticmethod
    def _plan(pre_steps: str | None = None) -> ExcelPlanV2:
        pres = []
        if pre_steps is not None:
            pres.append(SharedPrecondition(
                id="PRE-001", name="创建电表",
                steps=pre_steps, expected="创建成功",
            ))
        return ExcelPlanV2(
            shared_preconditions=pres,
            test_cases=[PlanTestCaseRow(
                id="TC-001", story="模块", title="用例", preconditions=["PRE-001"],
                steps="1.调用 /electricMeter/getPage", expected="1.[eq]成功",
            )],
        )

    def test_precondition_bad_url_flagged(self):
        vr = ExcelPlanValidator.validate(self._plan("1.调用 /electrictMeter/getPage 查询"), api_urls=REAL_URLS)
        pre_fail = [f for f in vr.failed_details if f[1].get("id", "").startswith("PRE-")]
        assert len(pre_fail) == 1
        assert pre_fail[0][1]["id"] == "PRE-001"
        assert any("疑似URL拼写错误" in e for e in pre_fail[0][2])

    def test_precondition_good_url_not_flagged(self):
        vr = ExcelPlanValidator.validate(self._plan("1.调用 /electricMeter/getPage 查询"), api_urls=REAL_URLS)
        assert not [f for f in vr.failed_details if f[1].get("id", "").startswith("PRE-")]

    def test_no_api_urls_precondition_not_flagged(self):
        vr = ExcelPlanValidator.validate(self._plan("1.调用 /electrictMeter/getPage 查询"))
        assert not [f for f in vr.failed_details if f[1].get("id", "").startswith("PRE-")]

    def test_case_url_error_aggregated(self):
        plan = ExcelPlanV2(
            shared_preconditions=[],
            test_cases=[PlanTestCaseRow(
                id="TC-001", story="模块", title="用例", preconditions=[],
                steps="1.调用 /electrictMeter/getPage", expected="1.[eq]成功",
            )],
        )
        vr = ExcelPlanValidator.validate(plan, api_urls=REAL_URLS)
        reasons = vr.block_reasons
        assert any("疑似URL拼写错误" in r for r in reasons)
        assert any("被拦截" in r and "疑似URL拼写错误" in r for r in reasons)
