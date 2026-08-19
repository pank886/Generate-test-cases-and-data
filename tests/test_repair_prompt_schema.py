"""2026-08-18 修复 prompt 注入完整 Schema 测试。

背景：修复节点用 method="json_mode"，langchain 只在输出侧挂 PydanticOutputParser，
schema 不进请求体 → 修复 LLM 看不到 SharedPrecondition 结构，漏掉必填 name。
本次修复：repair_excel_plan_prompt 注入完整 ExcelPlanV2.model_json_schema() +
具体 PRE 对象示例 + 字段硬约束。

覆盖：
- schema 注入
- PRE 示例非空（不再显示空数组）
- PRE 字段硬约束
- 畸形 PRE（TestCaseRow 形状）仍被拒绝（行为保护：schema 是引导、不是容错）
"""

import json
import os
import sys

import pytest
from pydantic import ValidationError

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from prompts.extraction_prompts import repair_excel_plan_prompt
from prompts.response_model import ExcelPlanV2


def _render() -> str:
    """渲染修复 prompt，返回 system 消息文本。"""
    p = repair_excel_plan_prompt()
    msgs = p.format_messages(
        json_schema=json.dumps(
            ExcelPlanV2.model_json_schema(), ensure_ascii=False, indent=2),
        analysis_section="A", shared_pre_section="S", cases_section="C",
        module_tree="[]", all_apis_info="{}", db_schema="",
        failed_test_cases="T", block_reasons="B",
    )
    return msgs[0].content


class TestRepairPromptSchemaInjection:
    """修复 prompt 注入完整 schema。"""

    def test_render_injects_full_schema(self):
        system = _render()
        assert "### JSON Schema" in system
        assert "SharedPrecondition" in system  # 完整模型 schema 已注入
        # schema 中 SharedPrecondition.name 必填
        assert '"name"' in system

    def test_render_accepts_json_schema_var(self):
        """新增 {json_schema} 占位符后，正常渲染不抛 KeyError。"""
        system = _render()
        assert "ExcelPlanV2" in system or "SharedPrecondition" in system


class TestRepairPromptPreExample:
    """PRE 结构示例（替代原空数组）。"""

    def test_pre_example_not_empty(self):
        system = _render()
        # 不再出现误导性的空数组示例
        assert '"shared_preconditions": [],' not in system
        # 出现具体 PRE 对象示例（含 name）
        assert '"name": "已创建测试电表"' in system
        assert "PRE-001" in system

    def test_pre_example_fields(self):
        system = _render()
        # PRE 示例覆盖 id/name/steps/expected
        assert '"id": "PRE-001"' in system
        assert '"steps"' in system
        assert '"expected"' in system


class TestRepairPromptConstraints:
    """PRE 字段硬约束补充。"""

    def test_pre_field_constraint_documented(self):
        system = _render()
        assert "name 必填" in system
        assert "禁止输出 story/title" in system
        assert "cloned_from" in system  # 明确禁止输出

    def test_no_leftover_template_braces(self):
        """{{ 双括号必须全部转义为字面量。"""
        system = _render()
        assert "{{" not in system
        assert "}}" not in system


class TestMalformedPreStillRejected:
    """行为保护：schema 是引导、不是容错——畸形 PRE 仍被显式拒绝。

    本次线上失败的真实畸形输出（TestCaseRow 形状，漏 name）：
    {"id": "PRE-002", "story": "共享前置", "title": "创建分时电表（未绑定、无数据）",
     "preconditions": [], "steps": "...", "expected": "...",
     "mutates_data": true, "is_negative_test": false}
    """

    def test_tc_shape_pre_missing_name_rejected(self):
        malformed = {
            "id": "PRE-002", "story": "共享前置",
            "title": "创建分时电表（未绑定、无数据）",
            "preconditions": [],
            "steps": "1.调用 POST /electricMeter/add。\n2.确认返回创建成功。",
            "expected": "1.[eq]返回200，创建成功。",
            "mutates_data": True, "is_negative_test": False,
        }
        with pytest.raises(ValidationError) as exc_info:
            ExcelPlanV2.model_validate({
                "shared_preconditions": [malformed],
                "test_cases": [],
                "file_name": "test_plan.xlsx",
            })
        assert "name" in str(exc_info.value)

    def test_valid_pre_still_accepted(self):
        """正常 PRE（含 name）校验通过——修复不收紧合法输出。"""
        ok = {
            "shared_preconditions": [
                {"id": "PRE-001", "name": "已创建测试电表",
                 "steps": "1.调用 POST /electricMeter/add。",
                 "expected": "1.[eq]返回200，创建成功。"}
            ],
            "test_cases": [],
            "file_name": "test_plan.xlsx",
        }
        plan = ExcelPlanV2.model_validate(ok)
        assert plan.shared_preconditions[0].name == "已创建测试电表"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
