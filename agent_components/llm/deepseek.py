"""DeepSeek V4 Pro LLM 适配器

处理 V4 Pro 的 tool_calls 格式差异：
  1. function 对象内部多余字段（如 ``"type": "function"``）→ 归一化为仅保留 name/arguments
  2. 扁平结构（无嵌套 function）→ 重建 function 嵌套
"""

import logging
from typing import Any

import openai
from langchain_core.outputs import ChatResult

from agent_components.llm.base import BaseCompatibleChatOpenAI

logger = logging.getLogger(__name__)


class DeepSeekChatOpenAI(BaseCompatibleChatOpenAI):
    """适配 DeepSeek V4 Pro 的 ChatOpenAI 子类。

    覆写 _create_chat_result：
      - dict 路径：原地归一化 tool_calls → 委托基类
      - pydantic 模型路径：dump → 归一化 → 委托 → 回补 parsed/refusal
    """

    # ------------------------------------------------------------------
    #  归一化核心
    # ------------------------------------------------------------------

    @staticmethod
    def normalize_tool_calls(response_dict: dict) -> None:
        """原地归一化 tool_calls 结构。

        处理三种模式（互斥，按优先级尝试）：

        **A) function 对象内部多余字段**
          原始: ``{"function": {"name": "...", "arguments": "...", "type": "function"}}``
          结果: ``{"function": {"name": "...", "arguments": "..."}}``

        **B) 扁平结构（无 function 键）**
          原始: ``{"name": "...", "arguments": "...", "id": "...", "type": "function"}``
          结果: ``{"id": "...", "type": "function", "function": {"name": "...", "arguments": "..."}}``

        **C) 标准结构** → 不做额外操作。
        """
        for choice in response_dict.get("choices", []):
            message = choice.get("message", {})
            raw_tool_calls: list[Any] | None = message.get("tool_calls")
            if not raw_tool_calls:
                continue

            for tc in raw_tool_calls:
                if not isinstance(tc, dict):
                    continue

                fn = tc.get("function")

                # A) function 是 dict → 只保留期望字段
                if isinstance(fn, dict):
                    tc["function"] = {
                        k: v
                        for k, v in fn.items()
                        if k in ("name", "arguments")
                    }

                # B) 扁平结构 → 重建 function 嵌套
                elif "function" not in tc and tc.get("name"):
                    tc["function"] = {
                        "name": tc.pop("name", ""),
                        "arguments": tc.pop("arguments", "{}"),
                    }

                # C) 其他 → 不做操作

    # ------------------------------------------------------------------
    #  核心覆写
    # ------------------------------------------------------------------

    def _create_chat_result(
        self,
        response: dict | openai.BaseModel,
        generation_info: dict | None = None,
    ) -> ChatResult:
        # ----- dict 路径：原地归一化后委托基类 -----
        if isinstance(response, dict):
            self.normalize_tool_calls(response)
            return super()._create_chat_result(response, generation_info)

        # ----- Pydantic 模型路径 -----
        resp_dict = response.model_dump(
            exclude={"choices": {"__all__": {"message": {"parsed"}}}}
        )
        self.normalize_tool_calls(resp_dict)

        # 委托（传 dict → 基类 guard → ChatOpenAI._create_chat_result）
        result = super()._create_chat_result(resp_dict, generation_info)

        # 回补 parsed / refusal（父类因传入 dict 跳过了此步骤）
        self._restore_parsed_and_refusal(result, response)

        return result

    # ------------------------------------------------------------------
    #  parsed / refusal 回补（仅策略 A，不降级）
    # ------------------------------------------------------------------

    @staticmethod
    def _restore_parsed_and_refusal(
        result: ChatResult,
        original_response: openai.BaseModel | dict,
    ) -> None:
        """从原始 pydantic 模型回补 parsed 与 refusal。

        **策略（唯一路径）**：
          直接从 ``original_response.choices[0].message`` 读取。
          不存在或为 None 则静默跳过（**不降级、不警告**）。

        .. note::
          只有 ``method="json_schema"`` 模式下 SDK 才会在 message 中注入
          ``parsed`` 字段，``json_mode`` / ``function_calling`` 模式下此
          方法无副作用。
        """
        if not result.generations:
            return
        if not isinstance(original_response, openai.BaseModel):
            return

        try:
            msg = original_response.choices[0].message  # type: ignore[attr-defined]
        except (AttributeError, IndexError, TypeError):
            return

        if hasattr(msg, "parsed") and msg.parsed is not None:
            result.generations[0].message.additional_kwargs["parsed"] = msg.parsed

        if hasattr(msg, "refusal") and msg.refusal is not None:
            result.generations[0].message.additional_kwargs["refusal"] = msg.refusal
