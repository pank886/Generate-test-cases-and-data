#!/usr/bin/env python3
"""CLI 入口：智能测试助手命令行版"""
import sys

from observability import get_logger
from agent_components.graph_builder import build_and_run_agent

logger = get_logger(__name__)


def main():
    chat_func = build_and_run_agent()

    logger.info("=== 智能测试助手启动 (输入 'quit' 退出) ===")
    while True:
        user_input = input("\n用户: ").strip()
        if not user_input:
            continue
        if user_input.lower() in ["quit", "exit", "q"]:
            break

        response = chat_func(user_input)
        if response:
            logger.info("🤖 AI 思考: %s", response.proper_thinking)
            logger.info("💬 AI 回复: %s", response.final_response)


if __name__ == "__main__":
    main()
