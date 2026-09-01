"""thinking 日志回归测试（2026-08-12 修复）

覆盖规则：主路径一步生成节点 `_generate_excel_plan_thinking` 必须将
LLM 原始输出写入 thinking_trace.log（调用 log_thinking）。

背景：2026-08-03 提交 4a61792 把主路径从 analyze_test_points_raw（会写
log_thinking）切到一步生成节点，该节点漏写 log_thinking，导致主路径生成
thinking 自 8-03 起未进 thinking_trace.log。本测试锁定该回归：

调用一次生成，断言 log_thinking 被调用且参数正确
（node=generate_plan_thinking，output=LLM 原始文本，
prompt_label=generate_excel_plan_thinking）。
"""
import contextlib
import os
import sys
from unittest.mock import Mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from langchain_core.prompts import ChatPromptTemplate

from prompts.response_model import ExcelPlanV2
from prompts.response_model import TestCaseRow as _TestCaseRow
from agent_components.graph.nodes import ChatTestAgentGraph


def _plan_json() -> str:
    plan = ExcelPlanV2(
        shared_preconditions=[],
        test_cases=[
            _TestCaseRow(id="TC-001", story="智慧用电", title="用例1",
                        preconditions=[], steps="1.调用接口", expected="1.[eq]成功")
        ],
    )
    return plan.model_dump_json()


class TestThinkingLog:
    def _make_agent(self, monkeypatch, llm_text: str):
        """绕过 __init__（不连 Ollama/Chroma），mock 掉 DB 与 LLM。"""
        agent = object.__new__(ChatTestAgentGraph)
        agent.llm = Mock()
        # 真实模板：覆盖一步生成节点的全部占位符，验证 format_messages 正常渲染
        tpl = ChatPromptTemplate.from_messages([
            ("system", "{json_schema}"),
            ("human", "{gen_warning}### M\n{module_analysis}\n{api_definitions}\n"
                      "{related_docs}\n{user_context}\n{db_schema}"),
        ])
        monkeypatch.setattr(
            "prompts.extraction_prompts.generate_excel_plan_thinking_prompt",
            lambda: tpl)
        agent._invoke_think = Mock(return_value=llm_text)

        from database.operations import ModuleOps
        monkeypatch.setattr(ModuleOps, "get_by_name", staticmethod(lambda session, name: None))
        monkeypatch.setattr(ModuleOps, "get_tree", staticmethod(lambda session: []))
        monkeypatch.setattr("database.get_session_ctx", contextlib.nullcontext)
        return agent

    def test_thinking_logged_with_raw_output(self, monkeypatch):
        """核心回归：一步生成节点把 LLM 原始输出写入 thinking 日志。"""
        import infrastructure.observability as observability
        mock_log = Mock()
        monkeypatch.setattr(observability, "log_thinking", mock_log)

        llm_text = _plan_json()
        agent = self._make_agent(monkeypatch, llm_text)
        state = {
            "confirmed_module": "智慧用电",
            "original_input": "生成智慧用电测试用例",
            "api_definitions": [],
            "related_modules": [],
        }
        result = agent._generate_excel_plan_thinking(state)

        # 正常返回 plan
        assert result["excel_plan"].test_cases[0].id == "TC-001"
        # log_thinking 被调用且参数正确
        assert mock_log.called, "一步生成节点未调用 log_thinking"
        args, kwargs = mock_log.call_args
        assert args[0] == "generate_plan_thinking"
        assert args[1] == "生成智慧用电测试用例"
        assert args[2] == llm_text
        assert kwargs["prompt_label"] == "generate_excel_plan_thinking"


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])

