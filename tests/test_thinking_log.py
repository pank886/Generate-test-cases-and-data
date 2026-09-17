"""thinking 日志回归测试（2026-08-12 修复 / 2026-09-02 两段式适配）

覆盖规则：Phase B 生成节点 `_generate_excel_plan_thinking` 两段式拆分后，
两阶段 LLM 输出都必须写入 thinking_trace.log（调用 log_thinking）：
  - Phase 1（analyze_test_points_raw，自由文本分析）
  - Phase 2（generate_plan_thinking，结构化 JSON）

背景：2026-08-03 提交 4a61792 把主路径切到一步生成节点，该节点漏写
log_thinking。2026-09-02 恢复两段式：thinking（无 JSON 约束）先出自由文本
分析、json_object + thinking enabled 再结构化（2026-09-08 改；8192 输出硬顶跟
thinking=off 走），两个阶段都需留痕。
"""
import contextlib
import os
import sys
from unittest.mock import Mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

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


ANALYSIS_TEXT = (
    "## 测试场景分析\n删除电表正常/异常路径覆盖。\n"
    "## 共享前置\nPRE-001 已创建测试电表\n"
    "## 测试用例\nTC-001 删除电表-正向"
)


class TestThinkingLog:
    def _make_agent(self, monkeypatch, side_effect):
        """绕过 __init__（不连 Ollama/Chroma），mock 掉 DB 与 LLM。

        使用真实两段式 prompt（Phase1/Phase2 占位符由方法内 format_messages
        全部补齐），仅 mock `_invoke_think` 两步返回与 DB 读取。
        """
        agent = object.__new__(ChatTestAgentGraph)
        agent.llm = Mock()
        agent._invoke_think = Mock(side_effect=side_effect)

        from database.operations import ModuleOps
        monkeypatch.setattr(ModuleOps, "get_by_name", staticmethod(lambda session, name: None))
        monkeypatch.setattr(ModuleOps, "get_tree", staticmethod(lambda session: []))
        monkeypatch.setattr("database.get_session_ctx", contextlib.nullcontext)
        return agent

    def test_both_phases_logged_with_raw_output(self, monkeypatch):
        """核心回归：两段式节点把两个阶段 LLM 原始输出写入 thinking 日志。"""
        import infrastructure.observability as observability
        mock_log = Mock()
        monkeypatch.setattr(observability, "log_thinking", mock_log)

        llm_text = _plan_json()
        agent = self._make_agent(monkeypatch, [ANALYSIS_TEXT, llm_text])
        state = {
            "confirmed_module": "智慧用电",
            "original_input": "生成智慧用电测试用例",
            "api_definitions": [],
            "related_modules": [],
        }
        result = agent._generate_excel_plan_thinking(state)

        # 正常返回 plan + 回写 Phase 1 分析
        assert result["excel_plan"].test_cases[0].id == "TC-001"
        assert result["test_point_analysis"] == ANALYSIS_TEXT
        assert result["plan_source"] == "thinking"

        # 两个阶段各写一条日志，参数正确
        calls = mock_log.call_args_list
        assert len(calls) == 2, "两段式生成节点应写两条 thinking 日志"

        # Phase 1：自由文本分析
        a0, k0 = calls[0].args, calls[0].kwargs
        assert a0[0] == "analyze_test_points_raw"
        assert a0[1] == "生成智慧用电测试用例"
        assert a0[2] == ANALYSIS_TEXT
        assert k0["prompt_label"] == "generate_excel_plan_thinking"

        # Phase 2：结构化 JSON
        a1, k1 = calls[1].args, calls[1].kwargs
        assert a1[0] == "generate_plan_thinking"
        assert a1[1] == "生成智慧用电测试用例"
        assert a1[2] == llm_text
        assert k1["prompt_label"] == "generate_excel_plan_format"


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
