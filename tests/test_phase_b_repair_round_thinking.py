"""Phase B 修复轮 json_mode 放行 thinking 回归测试（2026-09-09 方案A）。

背景：修复轮 `_invoke_structured(method="json_mode")` 走 METHOD_FEATURES 门，
此前 json_mode/json_schema 被标 supports_thinking=False → client.py 强制
thinking=disabled → 8192 输出硬顶跟 thinking=off 走 → 修复重出完整
ExcelPlanV2 时被截断（LengthFinishReasonError）。

本文件验证：
  1. METHOD_FEATURES 门：json_mode/json_schema 已放行 thinking（True）、
     function_calling 保持 False（回归守卫）。
  2. client 门：invoke_structured method="json_mode" + thinking=True →
     with_structured_output 的 extra_body = {"thinking": {"type": "enabled"}}；
     thinking 未请求（默认 False）→ 不附加 extra_body（维持服务端 off 默认）。

不依赖 LLM / DB / 真实 chain，纯 mock 断言（参照 test_phase_b_two_stage
TestPhase2BindParams 风格，改从 with_structured_output 侧抓 extra_body——
修复轮走的是 with_structured_output(**llm_kwargs) 而非 bind）。
"""
import os
import sys
from unittest.mock import Mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import infrastructure.config as config
from infrastructure.llm import client as llm_client
from prompts.response_model import ExcelPlanV2
from agent_components.graph.nodes import METHOD_FEATURES

# 最小可构造 ExcelPlanV2 dict（json_mode 返回 dict → client 内部 model_class(**result)）
_RESULT_DICT = {
    "shared_preconditions": [],
    "test_cases": [{
        "id": "TC-001", "story": "模块", "title": "用例",
        "preconditions": [], "steps": "1.调用接口", "expected": "1.[eq]成功",
    }],
}


# ============================================================
# 1. METHOD_FEATURES 门（nodes.py）——结构化输出放行 thinking
# ============================================================

class TestMethodFeaturesGate:
    """json_mode/json_schema 支持 thinking；function_calling 保持关闭。"""

    def test_json_mode_supports_thinking(self):
        # 2026-09-09：修复轮走 json_mode，放行后不再被 8192 顶死
        assert METHOD_FEATURES["json_mode"]["supports_thinking"] is True

    def test_json_schema_supports_thinking(self):
        # 与 json_mode 同族（同样可承载大 JSON 输出）
        assert METHOD_FEATURES["json_schema"]["supports_thinking"] is True

    def test_function_calling_still_off(self):
        # 函数调用 + 深度思考可能冲突，暂无启用诉求 → 保持 False
        assert METHOD_FEATURES["function_calling"]["supports_thinking"] is False

    def test_free_text_supports_thinking(self):
        assert METHOD_FEATURES["free_text"]["supports_thinking"] is True


# ============================================================
# 2. client 门（invoke_structured）——json_mode + thinking 走 enabled
# ============================================================

class _FakeLLM:
    """记录 with_structured_output 的 kwargs，返回哑绑定对象。"""

    def __init__(self, store):
        self._store = store

    def with_structured_output(self, schema, method=None, **kwargs):
        self._store["schema"] = schema
        self._store["method"] = method
        self._store["wso_kwargs"] = kwargs
        return object()


class _FakePrompt:
    """充当 prompt 与 chain：| 运算记录绑定对象，invoke 返回固定 dict。"""

    def __init__(self, store, result):
        self._store = store
        self._result = result

    def __or__(self, other):
        self._store["bound"] = other
        return self

    def invoke(self, _vars):
        return self._result


def _run_invoke(thinking: bool, monkeypatch) -> dict:
    """以 json_mode 调 invoke_structured，返回捕获的 with_structured_output 状态。"""
    store = {}
    monkeypatch.setattr(config, "ENABLE_THINKING", True)
    plan = llm_client.invoke_structured(
        llm=_FakeLLM(store),
        prompt=_FakePrompt(store, _RESULT_DICT),
        model_class=ExcelPlanV2,
        method_features=METHOD_FEATURES,
        method="json_mode",
        thinking=thinking,
    )
    assert isinstance(plan, ExcelPlanV2), "json_mode dict 结果应被构造为 ExcelPlanV2"
    return store


class TestJsonModeThinkingGate:
    """json_mode 下 thinking=True → extra_body enabled；未请求 → 不附加。"""

    def test_thinking_true_enables_extra_body(self, monkeypatch):
        # 2026-09-09 生产修复：修复轮 thinking=True → enabled，绕开 8192 硬顶
        store = _run_invoke(thinking=True, monkeypatch=monkeypatch)
        assert store["method"] == "json_mode"
        assert store["schema"] is None, "json_mode 不绑 pydantic（client.py 约定）"
        assert store["wso_kwargs"]["extra_body"] == {"thinking": {"type": "enabled"}}

    def test_thinking_false_adds_no_extra_body(self, monkeypatch):
        # 其余 json_mode 调用方未传 thinking（意图识别/入库提取/翻译）→ 不附加
        # extra_body，维持服务端 off 默认（行为不变）
        store = _run_invoke(thinking=False, monkeypatch=monkeypatch)
        assert "extra_body" not in store["wso_kwargs"]

    def test_thinking_true_respects_enable_thinking_switch(self, monkeypatch):
        # config.ENABLE_THINKING=False 时即使 thinking=True 也不 enabled
        store = {}
        monkeypatch.setattr(config, "ENABLE_THINKING", False)
        llm_client.invoke_structured(
            llm=_FakeLLM(store),
            prompt=_FakePrompt(store, _RESULT_DICT),
            model_class=ExcelPlanV2,
            method_features=METHOD_FEATURES,
            method="json_mode",
            thinking=True,
        )
        assert "extra_body" not in store["wso_kwargs"]
