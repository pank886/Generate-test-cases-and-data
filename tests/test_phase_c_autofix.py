"""Phase C YAML 结构自动修正测试

覆盖:
1. TestData.auto_fix_top_level_structure — 顶层结构修正
2. StepData.auto_fix_step_structure — testCase dict→list, 字段泄漏
3. TestCase.auto_fix_validation_list — validation dict→list
"""

import pytest
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ============================================================
# 1. TestData 顶层结构自动修正
# ============================================================

class TestDataTopLevelFix:
    """TestData.auto_fix_top_level_structure 测试"""

    def _fix(self, raw: dict) -> dict:
        from prompts.response_model import TestData
        return TestData.auto_fix_top_level_structure(raw)

    def test_correct_structure_unchanged(self):
        """正确结构不应被修改"""
        raw = {
            "data": [
                {
                    "baseInfo": {"api_name": "test", "url": "/test", "method": "get", "header": {}},
                    "testCase": [{"case_name": "测试", "validation": [{"eq": {"code": 0}}]}],
                }
            ]
        }
        result = self._fix(raw)
        assert result == raw

    def test_testcase_at_top_level_as_list_of_steps(self):
        """LLM 输出 testCase: [...] 在顶层，内容是步骤"""
        raw = {
            "testCase": [
                {
                    "baseInfo": {"api_name": "新增创建", "url": "/meterDevice/add",
                                 "method": "post", "header": {"Content-Type": "application/json"}},
                    "testCase": [{"case_name": "test", "json": {},
                                  "validation": [{"eq": {"code": 0}}]}],
                }
            ]
        }
        result = self._fix(raw)
        assert "data" in result
        assert "testCase" not in result
        assert len(result["data"]) == 1
        assert result["data"][0]["baseInfo"]["api_name"] == "新增创建"

    def test_testcase_at_top_level_as_dict_with_baseinfo(self):
        """LLM 输出 testCase: {baseInfo: ..., testCase: [...]} 在顶层"""
        raw = {
            "testCase": {
                "baseInfo": {"api_name": "查询", "url": "/getPage",
                             "method": "post", "header": {"Content-Type": "application/json"}},
                "testCase": [{"case_name": "分页查询", "json": {"pageNum": 1},
                              "validation": [{"eq": {"$.retCode": 0}}]}],
            }
        }
        result = self._fix(raw)
        assert "data" in result
        assert isinstance(result["data"], list)
        assert result["data"][0]["baseInfo"]["api_name"] == "查询"

    def test_missing_data_wrapper_with_baseinfo(self):
        """LLM 输出 {baseInfo: ..., testCase: [...]}，缺少 data 包装"""
        raw = {
            "baseInfo": {"api_name": "新增创建", "url": "/meterDevice/add",
                         "method": "post", "header": {"Content-Type": "application/json"}},
            "testCase": [{"case_name": "新增电表", "json": {},
                          "validation": [{"eq": {"code": 0}}]}],
        }
        result = self._fix(raw)
        assert "data" in result
        assert isinstance(result["data"], list)
        assert result["data"][0]["baseInfo"]["api_name"] == "新增创建"

    def test_data_is_dict_not_list(self):
        """LLM 输出 {data: {baseInfo: ..., testCase: [...]}}"""
        raw = {
            "data": {
                "baseInfo": {"api_name": "查询", "url": "/getPage",
                             "method": "get", "header": {}},
                "testCase": [{"case_name": "查询", "params": {"id": 1},
                              "validation": [{"eq": {"code": 0}}]}],
            }
        }
        result = self._fix(raw)
        assert isinstance(result["data"], list)
        assert len(result["data"]) == 1
        assert result["data"][0]["baseInfo"]["api_name"] == "查询"

    def test_data_is_dict_with_nested_testcase(self):
        """LLM 输出 {data: {testCase: [...]}}，步骤内容在 testCase"""
        raw = {
            "data": {
                "testCase": [
                    {
                        "baseInfo": {"api_name": "查询", "url": "/getPage",
                                     "method": "get", "header": {}},
                        "case_name": "查询",
                        "params": {"id": 1},
                        "validation": [{"eq": {"code": 0}}],
                    }
                ]
            }
        }
        result = self._fix(raw)
        assert isinstance(result["data"], list)
        # 这种情况下，内层 testCase 列表的元素被提取为步骤
        assert len(result["data"]) >= 1

    def test_empty_input_unchanged(self):
        """非 dict 输入不变"""
        assert self._fix([1, 2, 3]) == [1, 2, 3]
        assert self._fix("string") == "string"

    def test_no_testcase_no_baseinfo_unchanged(self):
        """只有无关字段的不变"""
        raw = {"file_name": "test.yaml", "other": "value"}
        result = self._fix(raw)
        assert result == raw


# ============================================================
# 2. StepData testCase dict → list 修正
# ============================================================

class TestStepDataFix:
    """StepData.auto_fix_step_structure 测试"""

    def _fix(self, raw: dict) -> dict:
        from prompts.response_model import StepData
        return StepData.auto_fix_step_structure(raw)

    def test_testcase_dict_to_list(self):
        """testCase 是 dict 应包装为 list"""
        raw = {
            "baseInfo": {"api_name": "test", "url": "/test", "method": "get", "header": {}},
            "testCase": {"case_name": "测试", "json": {},
                         "validation": [{"eq": {"code": 0}}]},
        }
        result = self._fix(raw)
        assert isinstance(result["testCase"], list)
        assert result["testCase"][0]["case_name"] == "测试"

    def test_testcase_is_list_unchanged(self):
        """testCase 已是 list 不变"""
        raw = {
            "baseInfo": {"api_name": "test", "url": "/test", "method": "get", "header": {}},
            "testCase": [{"case_name": "测试", "validation": [{"eq": {"code": 0}}]}],
        }
        result = self._fix(raw)
        assert result == raw

    def test_case_name_leaked_to_step_level(self):
        """case_name/json 泄漏到 StepData 层级"""
        raw = {
            "baseInfo": {"api_name": "test", "url": "/test", "method": "get", "header": {}},
            "case_name": "测试用例",
            "json": {"code": "001"},
            "validation": [{"eq": {"code": 0}}],
            "extract": {"id": "$.data.id"},
        }
        result = self._fix(raw)
        assert "testCase" in result
        assert isinstance(result["testCase"], list)
        assert len(result["testCase"]) == 1
        assert result["testCase"][0]["case_name"] == "测试用例"
        assert result["testCase"][0]["json"] == {"code": "001"}
        # 泄漏字段应从顶层移除
        assert "case_name" not in result
        assert "json" not in result

    def test_case_name_leaked_with_empty_testcase(self):
        """泄漏字段 + testCase 列表为空"""
        raw = {
            "baseInfo": {"api_name": "test", "url": "/test", "method": "get", "header": {}},
            "case_name": "测试",
            "json": {"code": "001"},
            "testCase": [],
        }
        result = self._fix(raw)
        assert len(result["testCase"]) == 1
        assert result["testCase"][0]["case_name"] == "测试"

    def test_case_name_leaked_with_existing_testcase(self):
        """泄漏字段 + testCase 已有条目但缺字段"""
        raw = {
            "baseInfo": {"api_name": "test", "url": "/test", "method": "get", "header": {}},
            "case_name": "测试补充",
            "testCase": [{"validation": [{"eq": {"code": 0}}]}],
        }
        result = self._fix(raw)
        assert "case_name" in result["testCase"][0]
        assert result["testCase"][0]["case_name"] == "测试补充"

    def test_data_field_contains_testcase(self):
        """data 字段包含 testCase 列表（LLM 混淆）"""
        raw = {
            "baseInfo": {"api_name": "test", "url": "/test", "method": "get", "header": {}},
            "data": [
                {"case_name": "步骤1", "json": {}, "validation": [{"eq": {"code": 0}}]},
                {"case_name": "步骤2", "json": {}, "validation": [{"eq": {"code": 0}}]},
            ],
        }
        result = self._fix(raw)
        assert "testCase" in result
        assert "data" not in result
        assert len(result["testCase"]) == 2
        assert result["testCase"][0]["case_name"] == "步骤1"


# ============================================================
# 3. TestCase validation dict → list 修正
# ============================================================

class TestCaseValidationFix:
    """TestCase.auto_fix_validation_list 测试"""

    def _fix(self, raw: dict) -> dict:
        from prompts.response_model import TestCase
        return TestCase.auto_fix_validation_list(raw)

    def test_validation_single_dict_to_list(self):
        """validation 是单断言 dict → list"""
        raw = {
            "case_name": "测试",
            "json": {},
            "validation": {"eq": {"code": 0}},
        }
        result = self._fix(raw)
        assert isinstance(result["validation"], list)
        assert result["validation"] == [{"eq": {"code": 0}}]

    def test_validation_multi_assert_dict_to_list(self):
        """validation 是多断言 dict → 拆分为 list"""
        raw = {
            "case_name": "测试",
            "json": {},
            "validation": {"eq": {"code": 0}, "contains": {"msg": "成功"}},
        }
        result = self._fix(raw)
        assert isinstance(result["validation"], list)
        assert len(result["validation"]) == 2

    def test_validation_is_list_unchanged(self):
        """validation 已是 list 不变"""
        raw = {
            "case_name": "测试",
            "json": {},
            "validation": [{"eq": {"code": 0}}, {"contains": {"msg": "成功"}}],
        }
        result = self._fix(raw)
        assert result == raw

    def test_validation_empty_dict_unchanged(self):
        """空 dict 不变"""
        raw = {"case_name": "测试", "validation": {}}
        result = self._fix(raw)
        assert result == raw

    def test_validation_non_assert_dict(self):
        """非断言类型的 dict value 整体作为一个条目"""
        raw = {
            "case_name": "测试",
            "json": {},
            "validation": {"ne": {"code": 0}, "db": {"table": "xxx", "field": "yyy"}},
        }
        result = self._fix(raw)
        assert isinstance(result["validation"], list)
        assert len(result["validation"]) == 2

    def test_no_validation_unchanged(self):
        """无 validation 字段不变"""
        raw = {"case_name": "测试", "json": {}}
        result = self._fix(raw)
        assert result == raw


# ============================================================
# 4. 端到端：真实错误日志中的 LLM 输出 → 自动修正 → 校验通过
# ============================================================

class TestEndToEndAutoFix:
    """真实 LLM 输出场景端到端测试"""

    def test_fix_r1_001_tc003(self):
        """GEN-FAIL-R1-001: testCase 在顶层，缺少 data"""
        from prompts.response_model import TestData
        raw = {
            "testCase": {
                "baseInfo": {
                    "api_name": "新增创建", "url": "/meterDevice/add",
                    "method": "post",
                    "header": {"Content-Type": "application/json;charset=UTF-8"},
                },
                "data": [
                    {
                        "case_name": "新增电表-电表编号已存在",
                        "json": {"code": "EXISTING_METER_CODE_001", "name": "自动化测试电表_${random_plates(1)}"},
                        "validation": [{"ne": {"$.code": 0}}, {"contains": {"$.msg": "电表编号已存在"}}],
                    }
                ],
            }
        }
        result = TestData.auto_fix_top_level_structure(raw)
        assert "data" in result
        assert isinstance(result["data"], list)
        # 验证完整校验链路
        validated = TestData(**result)
        assert len(validated.data) == 1
        assert validated.data[0].testCase[0].case_name == "新增电表-电表编号已存在"

    def test_fix_r1_002_tc011(self):
        """GEN-FAIL-R1-002: testCase 在顶层, 步骤嵌套 testCase"""
        from prompts.response_model import TestData
        raw = {
            "testCase": {
                "baseInfo": {
                    "api_name": "按企业统计：分页查询", "url": "/electricBillEnterprise/getPage",
                    "method": "post",
                    "header": {"Content-Type": "application/json;charset=UTF-8"},
                },
                "testCase": [
                    {
                        "case_name": "按企业统计分页查询-按月统计周期",
                        "json": {"pageNum": 1, "pageSize": 10, "month": "2024-01", "orderKey": "relateEnterpriseName"},
                        "validation": [{"eq": {"$.retCode": 0}}, {"ne": {"$.data.records[0].electricity": None}}],
                    }
                ],
            }
        }
        result = TestData.auto_fix_top_level_structure(raw)
        assert "data" in result
        # 验证完整校验链路
        validated = TestData(**result)
        assert len(validated.data) == 1
        assert validated.data[0].testCase[0].case_name == "按企业统计分页查询-按月统计周期"

    def test_fix_r1_003_tc002(self):
        """GEN-FAIL-R1-003: testCase 是 dict 非 list, validation 是 dict 非 list

        注意: validation dict→list 的修正只在 TestCase 模型校验时生效
        (auto_fix_validation_list 是 TestCase 的 model_validator)，
        不会在 StepData 的 auto_fix_step_structure 中触发。
        因此这个测试使用完整的 TestData 校验链路。

        另外 input_extract 需要 $. 前缀（语义约束，非结构修正范围），
        测试数据中使用合法的 JSONPath 值。
        """
        from prompts.response_model import TestData
        raw = {
            "data": [
                {
                    "case_name": "新增单一费率电表并验证分页查询",
                    "baseInfo": {
                        "api_name": "新增创建", "url": "/meterDevice/add",
                        "method": "post",
                        "header": {"Content-Type": "application/json;charset=UTF-8"},
                    },
                    "testCase": {
                        "json": {"name": "测试电表_${get_current_time(hms)}"},
                        "input_extract": {"meterName": "$.data.name"},
                        "validation": {"eq": {"$.code": 200}, "contains": {"$.msg": "成功"}},
                    },
                }
            ]
        }
        # 完整校验链路: TestData → StepData(含 auto_fix) → TestCase(含 auto_fix)
        validated = TestData(**raw)
        assert len(validated.data) == 1
        assert len(validated.data[0].testCase) == 1
        assert len(validated.data[0].testCase[0].validation) == 2

    def test_fix_r1_005_tc007(self):
        """GEN-FAIL-R1-005: 两个步骤，第二个用 params 而非 json (POST)

        这是语义错误（POST 请求用 params 传参）——自动修正不会处理，
        应由 validate_method_body_match 拦截并进入修复轮。
        """
        from prompts.response_model import TestData
        from pydantic import ValidationError
        raw = {
            "data": [
                {
                    "baseInfo": {
                        "api_name": "批量添加绑定电表", "url": "/payConfig/bindMeters/insertBatch",
                        "method": "post",
                        "header": {"Content-Type": "application/json;charset=UTF-8"},
                    },
                    "testCase": [
                        {
                            "case_name": "绑定电表到方案",
                            "json": {"payConfigCode": "PRE-002", "meterCodes": ["PRE-001"]},
                            "validation": [{"eq": {"retCode": 200}}],
                        }
                    ],
                },
                {
                    "baseInfo": {
                        "api_name": "删除计费方案", "url": "/payConfig/delete",
                        "method": "post",
                        "header": {"Content-Type": "application/json;charset=UTF-8"},
                    },
                    "testCase": [
                        {
                            "case_name": "删除已绑定电表的方案",
                            "params": {"code": "PRE-002"},
                            "validation": [{"ne": {"retCode": 0}}, {"contains": {"msg": "已绑定资源，无法删除"}}],
                        }
                    ],
                },
            ]
        }
        # POST 用 params → validate_method_body_match 拦截
        with pytest.raises(ValidationError, match="params 而非 json"):
            TestData(**raw)

    def test_fix_r1_008_setup(self):
        """GEN-FAIL-R1-008: setup 文件，baseInfo/testCase 在顶层缺少 data 包装"""
        from prompts.response_model import TestData
        raw = {
            "baseInfo": {
                "api_name": "新增计费方案", "url": "/payConfig/insert",
                "method": "post",
                "header": {"Content-Type": "application/json;charset=UTF-8"},
            },
            "testCase": [
                {
                    "case_name": "新增固定定价计费方案",
                    "json": {"code": "${random_plates(1)}", "payConfigName": "自动化测试-固定定价方案"},
                    "extract": {"payConfigCode": "$.data.code"},
                    "validation": [{"eq": {"retCode": 0}}, {"contains": {"msg": "成功"}}],
                }
            ],
        }
        result = TestData.auto_fix_top_level_structure(raw)
        assert "data" in result
        validated = TestData(**result)
        assert len(validated.data) == 1
        assert validated.data[0].baseInfo["api_name"] == "新增计费方案"


# ============================================================
# 5. 现有的 TestData/StepData/TestCase 校验不变
# ============================================================

class TestExistingValidatorsStillWork:
    """确保现有校验器不受影响"""

    def test_testdata_rejects_empty_data(self):
        """TestData 拒绝空 data"""
        from prompts.response_model import TestData
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            TestData(data=[])

    def test_stepdata_rejects_empty_testcase(self):
        """StepData 拒绝空 testCase"""
        from prompts.response_model import StepData, TestCase
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            StepData(
                baseInfo={"api_name": "test", "url": "/test", "method": "get", "header": {}},
                testCase=[],
            )

    def test_testcase_rejects_empty_validation(self):
        """TestCase 拒绝空 validation"""
        from prompts.response_model import TestCase
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            TestCase(case_name="test", validation=[])

    def test_testcase_validation_not_empty_pass(self):
        """TestCase 接受有内容的 validation"""
        from prompts.response_model import TestCase
        tc = TestCase(case_name="test", validation=[{"eq": {"code": 0}}])
        assert tc.case_name == "test"

    def test_full_correct_structure(self):
        """完整的正确结构可以通过所有校验"""
        from prompts.response_model import TestData
        raw = {
            "data": [
                {
                    "baseInfo": {
                        "api_name": "新增创建", "url": "/meterDevice/add",
                        "method": "post",
                        "header": {"Content-Type": "application/json;charset=UTF-8"},
                    },
                    "testCase": [
                        {
                            "case_name": "新增电表-正向",
                            "json": {"code": "${random_plates(1)}", "name": "测试电表"},
                            "extract": {"meterCode": "$.data.code"},
                            "validation": [
                                {"eq": {"$.retCode": 0}},
                                {"contains": {"$.msg": "成功"}},
                            ],
                        }
                    ],
                },
                {
                    "baseInfo": {
                        "api_name": "分页查询", "url": "/meterDevice/getPage",
                        "method": "post",
                        "header": {"Content-Type": "application/json;charset=UTF-8"},
                    },
                    "testCase": [
                        {
                            "case_name": "查询验证新增",
                            "json": {"pageNum": 1, "pageSize": 10},
                            "validation": [
                                {"eq": {"$.retCode": 0}},
                                {"contains": {"$.data.records[0].meterName": "${get_extract_data(meterName)}"}},
                            ],
                        }
                    ],
                },
            ]
        }
        validated = TestData(**raw)
        assert len(validated.data) == 2
        assert len(validated.data[0].testCase) == 1
        assert len(validated.data[1].testCase) == 1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
