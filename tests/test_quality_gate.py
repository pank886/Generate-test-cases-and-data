"""质量门禁单元测试（2026-08 修复）

覆盖规则：首轮通过率 < 50% → 全量重新跑一轮（真实重生成）；
再次 < 50% → 记录日志，终止生成并报错。

1. _quality_gate_decision：决策助手（regen / abort / None）
2. 端到端 wiring：首轮 <50% → 真实调用 _generate_excel_plan_thinking 一次；
   重试轮仍 <50% → raise RuntimeError（此前是假重生成循环 + 返回 requires_review）
"""
import contextlib
import os
import sys
import types
from unittest.mock import Mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from prompts.response_model import ExcelPlanV2
from prompts.response_model import TestCaseRow as _TestCaseRow
from agent_components.nodes import ChatTestAgentGraph, _quality_gate_decision


def _make_plan(n: int = 102) -> ExcelPlanV2:
    return ExcelPlanV2(
        shared_preconditions=[],
        test_cases=[
            _TestCaseRow(id=f"TC-{i:03d}", story="智慧用电", title=f"用例{i}",
                        preconditions=[], steps="1.调用接口", expected="1.[eq]成功")
            for i in range(1, n + 1)
        ],
    )


class TestQualityGateDecision:
    def test_first_round_low_pass_regen(self):
        """首轮 <50% → 触发全量重新生成。"""
        assert _quality_gate_decision(42, 102, False) == "regen"

    def test_regen_still_low_abort(self):
        """重试后仍 <50% → 终止并报错。"""
        assert _quality_gate_decision(42, 102, True) == "abort"

    def test_pass_returns_none(self):
        """达标 / 无需判断 → None。"""
        assert _quality_gate_decision(60, 100, True) is None
        assert _quality_gate_decision(50, 100, False) is None  # 恰好 50% 视为达标
        assert _quality_gate_decision(0, 0, False) is None     # 空计划不触发


class TestQualityGateWiring:
    """_generate_excel_plan_node 门禁路径端到端（绕过 __init__，不连 Ollama/Chroma）。"""

    def _make_agent(self, monkeypatch):
        agent = object.__new__(ChatTestAgentGraph)
        agent.prompt_factory = types.SimpleNamespace(
            generate_excel_plan_node=lambda: "prompt")
        # 模块树与 DB 会话置空，保持测试独立
        from database.operations import ModuleOps
        monkeypatch.setattr(ModuleOps, "get_tree", staticmethod(lambda session: []))
        monkeypatch.setattr("database.get_session_ctx", contextlib.nullcontext)
        return agent

    @staticmethod
    def _make_vr(confirmed: int, failed: int):
        class _VR:
            pass
        vr = _VR()
        vr.all_confirmed = [
            _TestCaseRow(id=f"TC-{i:03d}", story="智慧用电", title=f"用例{i}",
                        preconditions=[], steps="1.步骤", expected="1.[eq]成功")
            for i in range(confirmed)
        ]
        vr.failed_details = [(0, {"id": f"F-{i}"}, ["格式错误"]) for i in range(failed)]
        return vr

    def test_regen_once_then_abort(self, monkeypatch):
        """核心回归：首轮 <50% 真实重生成一次（注入警告）；重试仍 <50% → 终止报错。"""
        from agent_components.plan_validator import ExcelPlanValidator

        agent = self._make_agent(monkeypatch)
        # 两轮都 42/102 (<50%)
        monkeypatch.setattr(ExcelPlanValidator, "validate", Mock(
            side_effect=[self._make_vr(42, 60), self._make_vr(42, 60)]))

        regen_called = {"n": 0}
        regen_warnings = []

        def _fake_thinking(state, gen_warning=""):
            regen_called["n"] += 1
            regen_warnings.append(gen_warning)
            return {"excel_plan": _make_plan(102)}

        monkeypatch.setattr(agent, "_generate_excel_plan_thinking", _fake_thinking)

        state = {
            "excel_plan": _make_plan(102),
            "test_point_analysis": "分析",
            "api_definitions": [],
            "original_input": "生成智慧用电测试用例",
            "confirmed_module": "智慧用电",
            "related_modules": [],
            "output_dir": None,
            "user_input": "生成智慧用电测试用例",
        }
        with pytest.raises(RuntimeError, match="已终止"):
            agent._generate_excel_plan_node(state)

        # 真实重生成被调用恰好一次（不是假的 requires_review）
        assert regen_called["n"] == 1
        # 警告已注入
        assert regen_warnings[0] and "质量未达标" in regen_warnings[0]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
