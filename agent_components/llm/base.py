"""LLM 适配器基类

所有模型适配器统一继承此类，方便扩展通用防御逻辑。

当前基类能力：
  - content 为空时兜底（None -> ""），防止下游意外崩溃

各模型特有逻辑在各子类中实现（如 DeepSeek 的 tool_calls 归一化）。
"""

from typing import Optional, Union

import openai
from langchain_openai import ChatOpenAI


class BaseCompatibleChatOpenAI(ChatOpenAI):
    """LLM 适配器基类。

    统一防御逻辑放在此处，各模型适配器继承此类。
    """

    @staticmethod
    def _guard_empty_content(response_dict: dict) -> None:
        """确保 content 不为 None。

        LangChain 内部在 _convert_dict_to_message 中也会执行
        content = _dict.get("content", "") or "" 转换，
        此处为显式前置防御，使 dict 内容一致，方便下游 codec 安全读取。
        """
        for choice in response_dict.get("choices", []):
            msg = choice.get("message", {})
            if msg.get("content") is None and not msg.get("tool_calls"):
                msg["content"] = ""

    def _create_chat_result(
        self,
        response: Union[dict, openai.BaseModel],
        generation_info: Optional[dict] = None,
    ):
        """覆写父类：在标准解析前执行通用防御逻辑。

        接收 dict（DeepSeek 归一化后）或 openai.BaseModel（标准 SDK 路径），
        isinstance 检查确保两种路径都安全。
        """
        if isinstance(response, dict):
            self._guard_empty_content(response)
        return super()._create_chat_result(response, generation_info)
