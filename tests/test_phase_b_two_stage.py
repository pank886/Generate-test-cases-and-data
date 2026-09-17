"""Phase B 两段式结构（2026-09-02 恢复）回归测试。

覆盖：
  1. Phase 1 prompt（generate_excel_plan_thinking_prompt）：纯思考自由文本
     分析，无 JSON schema / 无「输出纯 JSON」字样；三段式输出要求保留；
     设计规则与柔性自检保留；不引入示例字面量。
  2. Phase 2 prompt（generate_excel_plan_format_prompt）：json_schema 注入 +
     字段硬约束 + 忠实转换；human 含 analysis_text/api_definitions/db_schema/gen_warning。
  3. 两段 wiring：mock `_invoke_think` 两步返回 → 解析取自 Phase 2 文本；
     返回回写 test_point_analysis；plan_source="thinking"。
  4. bind 参数正确性：Phase 1 无 response_format + thinking enabled；
     Phase 2 有 response_format=json_object + thinking enabled（2026-09-08 改：
     8192 输出硬顶跟 thinking=off 走，disabled 会导致大模块计划 JSON 截断）。

不依赖 LLM / DB（DB 读取已 mock），纯渲染 + mock 断言。
"""
import contextlib
import json
import os
import sys
from unittest.mock import Mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from prompts.extraction_prompts import (
    generate_excel_plan_thinking_prompt,
    generate_excel_plan_format_prompt,
)
from prompts.response_model import ExcelPlanV2
from prompts.response_model import TestCaseRow as _TestCaseRow
from agent_components.graph.nodes import ChatTestAgentGraph

_JSON_SCHEMA = json.dumps(
    {"type": "object", "properties": {"test_cases": {"type": "array"}}},
    ensure_ascii=False)


def _plan_json() -> str:
    plan = ExcelPlanV2(
        shared_preconditions=[],
        test_cases=[
            _TestCaseRow(id="TC-001", story="智慧用电", title="用例1",
                        preconditions=[], steps="1.调用接口", expected="1.[eq]成功")
        ],
    )
    return plan.model_dump_json()


ANALYSIS_TEXT = (
    "## 测试场景分析\n删除电表正常/异常路径覆盖。\n"
    "## 共享前置\nPRE-001 已创建测试电表\n"
    "## 测试用例\nTC-001 删除电表-正向"
)


def _render_p1() -> tuple[str, str]:
    msgs = generate_excel_plan_thinking_prompt().format_messages(
        module_analysis="分析",
        api_definitions="[]",
        related_docs="无",
        user_context="生成测试用例",
        db_schema="",
        gen_warning="",
    )
    return msgs[0].content, msgs[1].content


def _render_p2() -> tuple[str, str]:
    msgs = generate_excel_plan_format_prompt().format_messages(
        json_schema=_JSON_SCHEMA,
        analysis_text=ANALYSIS_TEXT,
        api_definitions="[]",
        db_schema="",
        gen_warning="",
    )
    return msgs[0].content, msgs[1].content


def _make_agent(monkeypatch, side_effect):
    """绕过 __init__，mock DB 读取与 `_invoke_think` 两步返回。"""
    agent = object.__new__(ChatTestAgentGraph)
    agent.llm = Mock()
    agent._invoke_think = Mock(side_effect=side_effect)
    from database.operations import ModuleOps
    monkeypatch.setattr(ModuleOps, "get_by_name", staticmethod(lambda session, name: None))
    monkeypatch.setattr(ModuleOps, "get_tree", staticmethod(lambda session: []))
    monkeypatch.setattr("database.get_session_ctx", contextlib.nullcontext)
    return agent


class TestPhase1PromptContent:
    """改动 1：Phase 1 为纯思考自由文本分析段。"""

    def test_no_json_schema_and_no_pure_json_output(self):
        sys_txt, human = _render_p1()
        assert "{json_schema}" not in sys_txt
        assert "只输出纯 JSON" not in sys_txt
        assert "请设计测试用例并输出 JSON" not in human

    def test_three_section_free_text_output_required(self):
        _, human = _render_p1()
        assert "## 测试场景分析" in human
        assert "## 共享前置" in human
        assert "## 测试用例" in human
        assert "不要输出 JSON" in human

    def test_design_rules_kept(self):
        sys_txt, human = _render_p1()
        for kw in ["### 思维链设计规范", "前置→执行→断言", "共享前置引用规范",
                   "枚举/取值字段不写死字面量", "禁止写死具体值"]:
            assert kw in sys_txt, "P1 规则缺失: " + kw
        assert "输出前柔性自检" in human
        assert "枚举/取值字段是否未写死具体值" in human

    def test_no_example_literal(self):
        """Phase 1 system 只给规则，不引入 JSON 示例/请求体字面量。"""
        sys_txt, _ = _render_p1()
        assert '{"' not in sys_txt
        assert "POST /" not in sys_txt


class TestPhase2PromptContent:
    """改动 2：Phase 2 为结构化转换段（json_object + thinking enabled）。"""

    def test_schema_injected_and_hard_constraints(self):
        sys_txt, _ = _render_p2()
        assert "### JSON Schema" in sys_txt
        assert '"test_cases"' in sys_txt  # schema 注入生效
        assert "字段硬约束" in sys_txt
        assert "五字段缺一不可" in sys_txt
        assert "PRE 编号数组" in sys_txt

    def test_faithful_conversion_rule(self):
        sys_txt, _ = _render_p2()
        assert "忠实转换" in sys_txt
        assert "禁止省略、合并或凭空新增用例" in sys_txt

    def test_human_placeholders(self):
        _, human = _render_p2()
        assert "### 待转换分析报告" in human
        assert ANALYSIS_TEXT in human
        assert "### 接口定义" in human
        assert "### 数据库表结构信息" in human
        assert "只输出 JSON" in human

    def test_no_example_literal(self):
        sys_txt, _ = _render_p2()
        assert '{"code"' not in sys_txt
        assert "POST /electricMeter" not in sys_txt


class TestTwoStageWiring:
    """改动 3：`_generate_excel_plan_thinking` 拆两次顺序调用。"""

    def test_two_calls_and_result_from_phase2(self, monkeypatch):
        llm_text = _plan_json()
        agent = _make_agent(monkeypatch, [ANALYSIS_TEXT, llm_text])
        result = agent._generate_excel_plan_thinking({
            "confirmed_module": "智慧用电",
            "original_input": "生成智慧用电测试用例",
            "api_definitions": [],
            "related_modules": [],
        })

        # 解析取自第 2 次（Phase 2）文本
        assert result["excel_plan"].test_cases[0].id == "TC-001"
        assert result["plan_source"] == "thinking"
        # Phase 1 分析回写
        assert result["test_point_analysis"] == ANALYSIS_TEXT

        # 两次调用 label / reasoning_label 正确
        calls = agent._invoke_think.call_args_list
        assert len(calls) == 2
        assert calls[0].kwargs["label"] == "analyze_test_points_raw"
        assert calls[0].kwargs["reasoning_label"] == "analyze_test_points_raw"
        assert calls[1].kwargs["label"] == "generate_plan_thinking"
        assert calls[1].kwargs.get("reasoning_label") is None


class TestPhase2BindParams:
    """改动 3：bind 参数正确性（Phase 2 = json_object + thinking enabled）。

    2026-09-08：8192 输出硬顶跟 thinking=off 走（与 json_object/max_tokens 无关），
    Phase 2 原 disabled 会导致大模块计划 JSON 单发截断 → 改回 enabled 绕开上限。
    """

    def test_bind_kwargs(self, monkeypatch):
        bound_log = []

        def fake_bind(**kwargs):
            bound_log.append(kwargs)
            return Mock()

        agent = _make_agent(monkeypatch, [ANALYSIS_TEXT, _plan_json()])
        agent.llm.bind.side_effect = fake_bind
        agent._generate_excel_plan_thinking({
            "confirmed_module": "智慧用电",
            "original_input": "生成智慧用电测试用例",
            "api_definitions": [],
            "related_modules": [],
        })

        assert len(bound_log) == 2
        import infrastructure.config as config
        # 两段都显式 bind max_tokens（惯例；thinking=off 时才需要它防 8192，off 下仍被服务端钉死）
        for b in bound_log:
            assert b.get("max_tokens") == config.LLM_MAX_TOKENS
        # Phase 1：无 response_format，thinking enabled
        assert "response_format" not in bound_log[0]
        assert bound_log[0]["extra_body"] == {"thinking": {"type": "enabled"}}
        # Phase 2：response_format=json_object，thinking enabled（2026-09-08 改）
        assert bound_log[1]["response_format"] == {"type": "json_object"}
        assert bound_log[1]["extra_body"] == {"thinking": {"type": "enabled"}}


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
