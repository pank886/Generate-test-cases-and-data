"""Phase B 思维链 prompt 内容规范测试（2026-08-13）。

覆盖 generate_excel_plan_thinking 的思维链三段式（前置→执行→断言）内容约定：
- 规范段确实渲染出来（结构说明，非示例）
- {method}/{url}/{业务动作} 等字面占位符被正确转义（模板渲染不报 KeyError）
- 禁止内嵌完整 JSON 请求体、强令使用上下文等铁律存在
"""
import os
import sys
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from prompts.definitions import PromptFactory


def _render_thinking_messages() -> tuple[str, str]:
    """渲染 generate_excel_plan_thinking 的 system + human 消息。"""
    pt = PromptFactory().generate_excel_plan_thinking()
    msgs = pt.format_messages(
        json_schema=json.dumps({"type": "object"}, ensure_ascii=False),
        module_analysis="【模块场景与接口分析】\n- /electricMeter/delete 删除电表",
        api_definitions='[{"name":"删除电表","method":"POST","url":"/electricMeter/delete"}]',
        related_docs="关联模块: 电表管理",
        user_context="生成删除电表的测试用例",
        db_schema="",
        gen_warning="",
    )
    return msgs[0].content, msgs[1].content


class TestThinkingChainStructure:
    """思维链三段式结构规范必须存在于 prompt。"""

    def test_chain_section_present(self):
        sys_txt, _ = _render_thinking_messages()
        assert "### 思维链设计规范" in sys_txt
        assert "前置→执行→断言" in sys_txt

    def test_preconditions_require_prep_operation(self):
        sys_txt, _ = _render_thinking_messages()
        assert "创建/初始化" in sys_txt
        assert "禁止用自由文本" in sys_txt

    def test_steps_format_template(self):
        """每行步骤 = 调用 {method} {url} 做{业务动作}，且是字面量而非模板占位。"""
        sys_txt, _ = _render_thinking_messages()
        assert "调用 {method} {url}，做{业务动作}" in sys_txt

    def test_no_raw_json_body_rule(self):
        sys_txt, _ = _render_thinking_messages()
        assert "禁止在 steps 中内嵌完整 JSON 请求体" in sys_txt
        # 用户要求「实际例子不要」：不得出现任何 JSON 请求体示例
        assert '{"code"' not in sys_txt

    def test_mandate_use_context(self):
        sys_txt, _ = _render_thinking_messages()
        assert "强令使用上下文" in sys_txt
        assert "禁止凭空编造接口/步骤" in sys_txt

    def test_only_use_provided_info(self):
        sys_txt, _ = _render_thinking_messages()
        assert "只使用提供的信息分析，禁止瞎编" in sys_txt
        assert "禁止引入提供范围之外的知识、接口、字段或数据" in sys_txt

    def test_negative_steps_rule_no_json_body(self):
        sys_txt, _ = _render_thinking_messages()
        assert "具体操作（如省略必填字段、传入错误类型值），禁止内嵌完整 JSON 请求体" in sys_txt


class TestThinkingChainSelfCheck:
    """human 消息尾部自检清单必须覆盖思维链项。"""

    def test_self_check_items_present(self):
        _, human = _render_thinking_messages()
        assert "三段式设计" in human
        assert "是否误内嵌了 JSON 请求体" in human
        assert "删除/修改后是否经查询接口验证" in human
        assert "未引入范围外知识/接口/数据瞎编" in human


class TestTemplateEscaping:
    """{method}/{url}/{业务动作} 必须以字面量渲染，不能触发 KeyError/ValueError。"""

    def test_render_does_not_raise(self):
        # 若转义缺失，format_messages 会抛 KeyError('method')，测试即失败
        _render_thinking_messages()

    def test_literal_placeholders_rendered(self):
        sys_txt, human = _render_thinking_messages()
        assert "调用 {method} {url} 创建/初始化 {实体}" in sys_txt
        assert "调用 {method} {url} 做{业务动作}" in human


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
