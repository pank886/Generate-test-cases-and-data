"""YAML 生成 db 断言拦截 + 导出接口断言校验（2026-08-04 问题 2/3）。

覆盖：
  1. TestData.validate_no_db_when_no_schema — db_schema 空时 db 断言回炉
  2. TestData.validate_export_assertion — is_export 接口 eq 检查状态码回炉
  3. GenerationMixin._takeover_export_assertions — 代码接管兜底
"""
import os
import sys

import pytest
from pydantic import ValidationError

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from prompts.response_model import TestData, StepData, TestCase, set_db_schema_empty
from agent_components.generators import GenerationMixin


def _step(base_url="/x/query", method="get", annotations=None,
          validation=None) -> dict:
    base = {"api_name": "查询", "url": base_url, "method": method, "header": {}}
    if annotations is not None:
        base["_annotations"] = annotations
    return {
        "baseInfo": base,
        "testCase": [{"case_name": "t", "params": {},
                      "validation": validation or [{"eq": {"code": 0}}]}],
    }


# ============================================================
# 1. db 断言拦截（db_schema 空）
# ============================================================

class TestDbAssertionBlocked:
    def teardown_method(self):
        set_db_schema_empty(True)  # 默认空，防泄漏

    def test_db_assertion_rejected_when_no_schema(self):
        """db_schema 为空 → db 断言回炉。"""
        set_db_schema_empty(True)
        with pytest.raises(ValidationError, match="db"):
            TestData(data=[_step(validation=[{"db": "SELECT 1 FROM t"}])])

    def test_db_assertion_allowed_when_schema_provided(self):
        """db_schema 非空 → 允许 db 断言（未来接入表结构后）。"""
        set_db_schema_empty(False)
        td = TestData(data=[_step(validation=[{"db": "SELECT 1 FROM t"}])])
        assert td.data[0].testCase[0].validation == [{"db": "SELECT 1 FROM t"}]

    def test_eq_assertion_not_affected(self):
        """非 db 断言不受影响。"""
        set_db_schema_empty(True)
        td = TestData(data=[_step(validation=[{"eq": {"code": 0}}])])
        assert td.data[0].testCase[0].validation == [{"eq": {"code": 0}}]


# ============================================================
# 2. 导出接口断言校验（is_export）
# ============================================================

class TestExportAssertion:
    def test_export_eq_status_code_rejected(self):
        """is_export 接口用 eq 检查状态码 → 回炉。"""
        ann = {"is_export": {"active": True}}
        with pytest.raises(ValidationError, match="is_export"):
            TestData(data=[_step(base_url="/electricMeter/export", method="get",
                                 annotations=ann,
                                 validation=[{"eq": {"$.status_code": 200}}])])

    def test_export_contains_status_code_allowed(self):
        """is_export 接口用 contains 状态码 → 通过。"""
        ann = {"is_export": {"active": True}}
        td = TestData(data=[_step(base_url="/electricMeter/export", method="get",
                                  annotations=ann,
                                  validation=[{"contains": {"status_code": 200}}])])
        assert td.data[0].testCase[0].validation == [{"contains": {"status_code": 200}}]

    def test_non_export_eq_status_code_allowed(self):
        """非 is_export 接口不受影响。"""
        td = TestData(data=[_step(validation=[{"eq": {"$.status_code": 200}}])])
        assert td.data[0].testCase[0].validation == [{"eq": {"$.status_code": 200}}]

    def test_export_eq_data_field_allowed(self):
        """is_export 接口 eq 检查业务字段（非状态码）→ 不拦截（仅拦状态码）。"""
        ann = {"is_export": {"active": True}}
        td = TestData(data=[_step(base_url="/x/export", annotations=ann,
                                  validation=[{"eq": {"$.data.rows": 0}}])])
        assert td.data[0].testCase[0].validation == [{"eq": {"$.data.rows": 0}}]


# ============================================================
# 3b. validation 元素/contains 操作数必须为 dict（2026-08-12 问题 4）
# ============================================================

class TestValidationElementDict:
    def test_bare_string_element_rejected(self):
        """validation 元素为裸字符串 → 字段类型 List[Dict] 直接拒绝回炉。"""
        with pytest.raises(ValidationError, match="valid dictionary"):
            TestData(data=[_step(validation=["收费名称不能为空"])])

    def test_contains_bare_string_operand_rejected(self):
        """contains: <裸字符串>（_27 实际崩溃形态）→ 校验失败回炉。"""
        with pytest.raises(ValidationError, match="操作数不是 dict"):
            TestData(data=[_step(validation=[{"contains": "收费名称不能为空"}])])

    def test_contains_dict_operand_allowed(self):
        """contains 值为 dict → 通过。"""
        td = TestData(data=[_step(validation=[{"contains": {"message": "收费名称不能为空"}}])])
        assert td.data[0].testCase[0].validation == [{"contains": {"message": "收费名称不能为空"}}]

    def test_eq_bare_string_operand_rejected(self):
        """eq: <裸字符串> 同样拒绝。"""
        with pytest.raises(ValidationError, match="操作数不是 dict"):
            TestData(data=[_step(validation=[{"eq": "成功"}])])

    def test_eq_dict_operand_allowed(self):
        """eq 值为 dict → 通过。"""
        td = TestData(data=[_step(validation=[{"eq": {"$.retCode": 0}}])])
        assert td.data[0].testCase[0].validation == [{"eq": {"$.retCode": 0}}]


# ============================================================
# 3. 代码接管兜底 _takeover_export_assertions
# ============================================================

class TestExportTakeover:
    @staticmethod
    def _step(ann, validation, url="/x/export", method="get"):
        base = {"url": url, "method": method, "header": {}}
        if ann is not None:
            base["_annotations"] = ann
        return StepData(
            baseInfo=base,
            testCase=[{"case_name": "t", "validation": validation}],
        )

    @staticmethod
    def _step_unsafe(ann, validation, url="/x/export", method="get"):
        """绕过 schema 校验构造 StepData（模拟漏过校验的数据，验证代码接管兜底）。"""
        base = {"url": url, "method": method, "header": {}}
        if ann is not None:
            base["_annotations"] = ann
        return StepData.model_construct(
            baseInfo=base,
            testCase=[TestCase.model_construct(case_name="t", validation=validation)],
        )

    def test_export_takeover_converts_to_contains(self):
        """is_export 标注 → 强制改为 contains status_code。"""
        step = self._step({"is_export": {"active": True}},
                          [{"eq": {"status_code": 200}}])
        GenerationMixin._takeover_export_assertions([step])
        assert step.testCase[0].validation == [{"contains": {"status_code": 200}}]

    def test_export_takeover_handles_empty_steps(self):
        GenerationMixin._takeover_export_assertions([])  # 不应抛异常

    # ---- 2026-08-12 问题 2：eq/ne 对 status_code 的断言泛化接管 ----

    def test_non_export_eq_status_code_normalized(self):
        """非 is_export 的 eq: {status_code} → contains（通用规则）。"""
        step = self._step(None, [{"eq": {"status_code": 200}}], url="/x/query")
        GenerationMixin._takeover_export_assertions([step])
        assert step.testCase[0].validation == [{"contains": {"status_code": 200}}]

    def test_non_export_ne_status_code_normalized(self):
        """非 is_export 的 ne: {status_code} → contains（负向 HTTP 码断言）。"""
        step = self._step(None, [{"ne": {"status_code": 404}}], url="/x/query")
        GenerationMixin._takeover_export_assertions([step])
        assert step.testCase[0].validation == [{"contains": {"status_code": 404}}]

    def test_non_export_eq_business_field_unchanged(self):
        """非 status_code 的业务字段 eq 断言不受影响。"""
        step = self._step(None, [{"eq": {"$.data.rows": 0}}], url="/x/query")
        GenerationMixin._takeover_export_assertions([step])
        assert step.testCase[0].validation == [{"eq": {"$.data.rows": 0}}]

    # ---- 2026-08-12 问题 4：contains 裸字符串接管 ----

    def test_contains_bare_string_normalized(self):
        """contains: <裸字符串> → contains: {message: <字符串>}（schema 漏过的兜底）。"""
        step = self._step_unsafe(None, [{"contains": "收费名称不能为空"}], url="/x/query")
        GenerationMixin._takeover_export_assertions([step])
        assert step.testCase[0].validation == [{"contains": {"message": "收费名称不能为空"}}]

    def test_contains_dict_unchanged(self):
        """contains 值为 dict 时不动。"""
        step = self._step(None, [{"contains": {"message": "成功"}}], url="/x/query")
        GenerationMixin._takeover_export_assertions([step])
        assert step.testCase[0].validation == [{"contains": {"message": "成功"}}]

    def test_mixed_validation_normalized(self):
        """混合断言只规范化需改写的条目，其余保留。"""
        step = self._step_unsafe(None, [
            {"eq": {"status_code": 200}},
            {"eq": {"$.retCode": 0}},
            {"contains": "收费名称不能为空"},
        ], url="/x/query")
        GenerationMixin._takeover_export_assertions([step])
        assert step.testCase[0].validation == [
            {"contains": {"status_code": 200}},
            {"eq": {"$.retCode": 0}},
            {"contains": {"message": "收费名称不能为空"}},
        ]
