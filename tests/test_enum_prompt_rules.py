"""枚举字段不写死字面量 + 接口映射分析枚举标注：prompt 渲染要点测试。

覆盖三处 prompt（2026-08-25 方案「前置条件不写死枚举字面量」）：
  - analyze_api_mapping_prompt：Step 3 接口映射分析（api_analysis）标注枚举字段及合法取值
  - generate_excel_plan_thinking：Phase B 首轮生成前置/步骤时不写死枚举字面量
  - repair_excel_plan_prompt：Phase B 修复轮同规则，不重新引入字面量

不依赖 LLM / DB，纯渲染断言。
"""
import os
import sys
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from prompts.extraction_prompts import (
    analyze_api_mapping_prompt,
    repair_excel_plan_prompt,
)
from prompts.extraction_prompts import generate_excel_plan_thinking_prompt


def _render_api_mapping() -> str:
    msgs = analyze_api_mapping_prompt().format_messages(
        module_name="智慧用电",
        scenario_analysis="场景分析",
        ui_flow_analysis="交互逻辑",
        api_definitions='[{"name": "添加电表", "method": "POST", '
                        '"url": "/electricMeter/add"}]',
        module_tree="[]",
        cross_module_relations="无",
    )
    return msgs[0].content + msgs[1].content


def _render_plan_thinking() -> str:
    pt = generate_excel_plan_thinking_prompt()
    msgs = pt.format_messages(
        json_schema=json.dumps({"type": "object"}, ensure_ascii=False),
        module_analysis="【模块场景与接口分析】",
        api_definitions="[]",
        related_docs="无",
        user_context="生成测试用例",
        db_schema="",
        gen_warning="",
    )
    return msgs[0].content + msgs[1].content


def _render_repair() -> str:
    msgs = repair_excel_plan_prompt().format_messages(
        json_schema=json.dumps({"type": "object"}, ensure_ascii=False),
        analysis_section="分析",
        shared_pre_section="PRE-001: 创建电表",
        cases_section="TC-001: 新增电表",
        module_tree="[]",
        all_apis_info="[]",
        db_schema="",
        failed_test_cases="TC-001",
        block_reasons="",
    )
    return msgs[0].content + msgs[1].content


class TestApiMappingEnumAnnotation:
    """analyze_api_mapping_prompt：api_analysis 必须标注枚举字段及合法取值。"""

    def test_enum_annotation_requirement_present(self):
        text = _render_api_mapping()
        assert "枚举/取值字段" in text
        assert "字段名：枚举值1/枚举值2" in text

    def test_example_annotation_present(self):
        text = _render_api_mapping()
        assert "meterDeviceType：单相/双相/三相" in text
        assert "accessMethod：网关接入/电表直连/平台对接" in text

    def test_enum_source_from_desc(self):
        text = _render_api_mapping()
        assert "desc" in text
        assert "不臆造" in text


class TestPlanThinkingEnumRule:
    """generate_excel_plan_thinking：枚举字段不写死字面量规则落位。"""

    def test_enum_literal_rule_present(self):
        text = _render_plan_thinking()
        assert "枚举/取值字段不写死字面量" in text
        assert "禁止写死具体值" in text

    def test_identifier_fields_exempt(self):
        """标识/编码字段（跨用例引用）不受约束、可保留具体值。"""
        text = _render_plan_thinking()
        assert "标识/编码字段" in text
        assert "保留具体值" in text

    def test_gt2_exception_present(self):
        """仅当需多个用例（>2）以不同取值区分时才允许写出具体值。"""
        text = _render_plan_thinking()
        assert ">2" in text

    def test_self_check_enum_item(self):
        """human 段柔性自检含枚举字段检查项。"""
        text = _render_plan_thinking()
        assert "枚举/取值字段是否未写死具体值" in text


class TestRepairEnumRule:
    """repair_excel_plan_prompt：修复轮不重新引入枚举字面量。"""

    def test_repair_rule_present(self):
        text = _render_repair()
        assert "枚举/取值字段" in text
        assert "禁止写死具体值" in text

    def test_identifier_exempt(self):
        text = _render_repair()
        assert "标识/编码字段" in text
        assert "保留具体值" in text

    def test_gt2_exception_present(self):
        text = _render_repair()
        assert ">2" in text
