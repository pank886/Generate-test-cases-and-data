"""LLM 适配器单元测试

测试策略：直接构造 dict 作为 response 传入 ``_create_chat_result()``，
**不发起真实的 HTTP 调用**。三种 Mock 响应覆盖 V4 Pro 的格式变体。
"""

import copy
import pytest
from langchain_core.outputs import ChatResult

from infrastructure.llm.deepseek import DeepSeekChatOpenAI


# ======================================================================
#  Mock 响应
# ======================================================================

BASE_CHOICE = {
    "index": 0,
    "finish_reason": "tool_calls",
}

TOOL_CALL_ARGS = '{"field": "value", "count": 42}'

# -- Case 1：标准 OpenAI 格式（基线）----------------------------------

STANDARD_TOOL_CALL = {
    "id": "call_abc123",
    "type": "function",
    "function": {
        "name": "TestSchema",
        "arguments": TOOL_CALL_ARGS,
    },
}

STANDARD_RESPONSE = {
    "id": "chatcmpl-001",
    "model": "deepseek-v4-pro",
    "object": "chat.completion",
    "created": 1712345678,
    "choices": [
        {
            **BASE_CHOICE,
            "message": {
                "role": "assistant",
                "content": None,
                "tool_calls": [STANDARD_TOOL_CALL],
            },
        }
    ],
    "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
}

# -- Case 2：V4 Pro 多余 type 字段 -----------------------------------

EXTRA_TYPE_TOOL_CALL = {
    "id": "call_def456",
    "type": "function",
    "function": {
        "name": "TestSchema",
        "arguments": TOOL_CALL_ARGS,
        "type": "function",              # <-- V4 Pro 多出来的字段
    },
}

EXTRA_TYPE_RESPONSE = {
    **STANDARD_RESPONSE,
    "id": "chatcmpl-002",
    "choices": [
        {
            **BASE_CHOICE,
            "message": {
                "role": "assistant",
                "content": None,
                "tool_calls": [EXTRA_TYPE_TOOL_CALL],
            },
        }
    ],
}

# -- Case 3：V4 Pro 扁平结构（无嵌套 function） ------------------------

FLAT_TOOL_CALL = {
    "id": "call_ghi789",
    "type": "function",
    "name": "TestSchema",                # <-- 扁平，无 function 嵌套
    "arguments": TOOL_CALL_ARGS,
}

FLAT_RESPONSE = {
    **STANDARD_RESPONSE,
    "id": "chatcmpl-003",
    "choices": [
        {
            **BASE_CHOICE,
            "message": {
                "role": "assistant",
                "content": None,
                "tool_calls": [FLAT_TOOL_CALL],
            },
        }
    ],
}


# ======================================================================
#  辅助函数
# ======================================================================

@pytest.fixture(scope="module")
def adapter() -> DeepSeekChatOpenAI:
    """返回一个带哑凭证的适配器实例（不实际调用 API）。"""
    return DeepSeekChatOpenAI(
        model="dummy-model",
        api_key="dummy-key",
        base_url="http://dummy.local",
    )


def extract_tool_calls(result: ChatResult) -> list[dict]:
    """从 ChatResult 中提取归一化的 tool_calls 列表（用于断言比较）。"""
    return [dict(tc) for tc in result.generations[0].message.tool_calls]


# ======================================================================
#  测试用例
# ======================================================================

class TestNormalizeToolCalls:
    """验证三种 format 各自能正确解析出 tool_calls。"""

    def test_standard_format(self, adapter: DeepSeekChatOpenAI):
        result = adapter._create_chat_result(STANDARD_RESPONSE)
        tcs = extract_tool_calls(result)
        assert len(tcs) == 1
        assert tcs[0]["name"] == "TestSchema"
        assert tcs[0]["args"] == {"field": "value", "count": 42}
        assert tcs[0]["id"] == "call_abc123"

    def test_extra_type_in_function(self, adapter: DeepSeekChatOpenAI):
        """function 内部多出的 ``type`` 字段被安全清除。"""
        result = adapter._create_chat_result(EXTRA_TYPE_RESPONSE)
        tcs = extract_tool_calls(result)
        assert len(tcs) == 1
        assert tcs[0]["name"] == "TestSchema"
        assert tcs[0]["args"] == {"field": "value", "count": 42}
        assert tcs[0]["id"] == "call_def456"

    def test_flat_structure(self, adapter: DeepSeekChatOpenAI):
        """扁平 tool_call 被重建为嵌套结构。"""
        result = adapter._create_chat_result(FLAT_RESPONSE)
        tcs = extract_tool_calls(result)
        assert len(tcs) == 1
        assert tcs[0]["name"] == "TestSchema"
        assert tcs[0]["args"] == {"field": "value", "count": 42}
        assert tcs[0]["id"] == "call_ghi789"


class TestConsistency:
    """三种格式归一化后结构字段完全一致（id 因 mock 不同而异）。"""

    @staticmethod
    def _strip_id(tc: dict) -> dict:
        return {k: v for k, v in tc.items() if k != "id"}

    def test_three_variants_equal(self, adapter: DeepSeekChatOpenAI):
        """核心断言：三种格式归一化后 ``name + args + type`` 完全一致。"""
        baseline = extract_tool_calls(adapter._create_chat_result(STANDARD_RESPONSE))

        for label, resp in [
            ("extra_type", EXTRA_TYPE_RESPONSE),
            ("flat", FLAT_RESPONSE),
        ]:
            result = extract_tool_calls(adapter._create_chat_result(resp))
            assert self._strip_id(result[0]) == self._strip_id(baseline[0]), (
                f"[{label}] normalized structural fields differ\n"
                f"  baseline: {self._strip_id(baseline[0])}\n"
                f"  got:      {self._strip_id(result[0])}"
            )


class TestEdgeCases:
    """边界场景。"""

    def test_mixed_tool_calls(self, adapter: DeepSeekChatOpenAI):
        """同一 choices 中一个标准 + 一个扁平 -> 两个都被正确解析。"""
        resp = copy.deepcopy(STANDARD_RESPONSE)
        resp["choices"][0]["message"]["tool_calls"] = [
            STANDARD_TOOL_CALL,
            FLAT_TOOL_CALL,
        ]

        result = adapter._create_chat_result(resp)
        tcs = extract_tool_calls(result)
        assert len(tcs) == 2
        assert tcs[0]["id"] == "call_abc123"
        assert tcs[1]["id"] == "call_ghi789"

    def test_no_tool_calls_key(self, adapter: DeepSeekChatOpenAI):
        """纯文本回复（无 tool_calls 键）-> 不报错。"""
        resp = copy.deepcopy(STANDARD_RESPONSE)
        del resp["choices"][0]["message"]["tool_calls"]
        resp["choices"][0]["message"]["content"] = "plain text response"

        result = adapter._create_chat_result(resp)
        msg = result.generations[0].message
        assert msg.content == "plain text response"
        assert msg.tool_calls == []

    def test_empty_tool_calls_list(self, adapter: DeepSeekChatOpenAI):
        """tool_calls=[] -> 不报错。"""
        resp = copy.deepcopy(STANDARD_RESPONSE)
        resp["choices"][0]["message"]["tool_calls"] = []

        result = adapter._create_chat_result(resp)
        assert result.generations[0].message.tool_calls == []

    def test_parsed_not_in_additional_kwargs(self, adapter: DeepSeekChatOpenAI):
        """传入 dict 时（非 pydantic model），``parsed`` 不应出现在
        additional_kwargs 中（策略 A 仅在原始响应为 BaseModel 时触发）。"""
        resp = copy.deepcopy(STANDARD_RESPONSE)
        resp["choices"][0]["message"]["content"] = '{"field": "value"}'

        result = adapter._create_chat_result(resp)
        assert "parsed" not in result.generations[0].message.additional_kwargs


class TestReasoningContent:
    """DeepSeek 思考模式的 reasoning_content 不被 langchain 转换丢弃。"""

    def test_dict_path_restores_reasoning(self, adapter: DeepSeekChatOpenAI):
        """dict 响应含 reasoning_content → 回补到 additional_kwargs（供 invoke_think 记录）。"""
        resp = copy.deepcopy(STANDARD_RESPONSE)
        resp["choices"][0]["message"]["content"] = '{"answer": 2}'
        resp["choices"][0]["message"]["reasoning_content"] = "先思考，再输出 JSON。"

        result = adapter._create_chat_result(resp)
        assert result.generations[0].message.additional_kwargs.get(
            "reasoning_content") == "先思考，再输出 JSON。"

    def test_no_reasoning_no_key(self, adapter: DeepSeekChatOpenAI):
        """无 reasoning_content → additional_kwargs 不新增键（静默跳过）。"""
        resp = copy.deepcopy(STANDARD_RESPONSE)
        resp["choices"][0]["message"]["content"] = "plain text"
        del resp["choices"][0]["message"]["tool_calls"]

        result = adapter._create_chat_result(resp)
        assert "reasoning_content" not in result.generations[0].message.additional_kwargs

    def test_empty_reasoning_no_key(self, adapter: DeepSeekChatOpenAI):
        """reasoning_content 为空串/None → 不新增键。"""
        resp = copy.deepcopy(STANDARD_RESPONSE)
        resp["choices"][0]["message"]["content"] = "plain text"
        del resp["choices"][0]["message"]["tool_calls"]
        resp["choices"][0]["message"]["reasoning_content"] = None

        result = adapter._create_chat_result(resp)
        assert "reasoning_content" not in result.generations[0].message.additional_kwargs


class TestInvokeThinkReasoning:
    """invoke_think 对 reasoning_content 的监测采集（单节点 thinking 观测）。

    reasoning_content 藏在 additional_kwargs（不进 content）；reasoning_label
    传入时按 ``{label}_thinking`` 打标落 thinking_trace.log，且不再走
    ``{label} 思考内容`` 通用路径（防双写）。
    """

    @staticmethod
    def _fake_llm(content: str, reasoning: str | None):
        from unittest.mock import Mock
        from langchain_core.messages import AIMessage
        llm = Mock()
        llm.invoke.return_value = AIMessage(
            content=content,
            additional_kwargs={"reasoning_content": reasoning} if reasoning else {},
        )
        return llm

    def test_reasoning_label_logs_tagged_node(self, monkeypatch):
        """有 reasoning_label → 落 ``{label}_thinking`` 节点，且不落 ``{label} 思考内容``。"""
        from unittest.mock import Mock
        import infrastructure.llm.client as llm_client
        llm = self._fake_llm('{"data": []}', "先思考，再输出 JSON。")
        mock_log = Mock()
        monkeypatch.setattr(llm_client, "log_thinking", mock_log)

        text = llm_client.invoke_think(
            llm, [{"role": "user", "content": "hi"}],
            label="generate_yaml_data_single",
            reasoning_label="generate_yaml_data_single")

        assert text == '{"data": []}'
        nodes = [c.args[0] for c in mock_log.call_args_list]
        assert "generate_yaml_data_single_thinking" in nodes
        assert "generate_yaml_data_single 思考内容" not in nodes

    def test_no_reasoning_label_keeps_generic_path(self, monkeypatch):
        """无 reasoning_label → 维持 ``{label} 思考内容`` 通用路径（行为不变）。"""
        from unittest.mock import Mock
        import infrastructure.llm.client as llm_client
        llm = self._fake_llm("ok", None)
        mock_generic = Mock()
        monkeypatch.setattr(llm_client, "_log_reasoning_content", mock_generic)

        text = llm_client.invoke_think(
            llm, [{"role": "user", "content": "hi"}], label="analyze_yaml_data")

        assert text == "ok"
        mock_generic.assert_called_once()

    def test_reasoning_label_empty_content_retries(self, monkeypatch):
        """content 空但 reasoning 非空 → 仍触发空响应重试；监测照常记录。"""
        from unittest.mock import Mock
        import infrastructure.llm.client as llm_client
        calls = {"n": 0}

        def invoke(messages):
            calls["n"] += 1
            from langchain_core.messages import AIMessage
            if calls["n"] == 1:
                return AIMessage(content="", additional_kwargs={"reasoning_content": "想一下"})
            return AIMessage(content="ok", additional_kwargs={"reasoning_content": "想一下"})

        llm = Mock()
        llm.invoke.side_effect = invoke
        mock_log = Mock()
        monkeypatch.setattr(llm_client, "log_thinking", mock_log)

        text = llm_client.invoke_think(
            llm, [{"role": "user", "content": "hi"}],
            label="node_x", reasoning_label="node_x", max_retries=1)

        assert text == "ok"
        assert calls["n"] == 2
        nodes = [c.args[0] for c in mock_log.call_args_list]
        assert nodes.count("node_x_thinking") == 2
