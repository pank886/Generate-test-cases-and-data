#!/usr/bin/env python3
"""CLI 入口：智能测试助手命令行版"""
import sys

from observability import get_logger
from agent_components.graph_builder import build_workflow

logger = get_logger(__name__)


def main():
    logger.info("=== 智能测试助手 CLI（请使用 Web 界面 http://localhost:8000） ===")
    logger.info("CLI 交互模式已废弃，请启动 Web 服务: python web/app.py")


if __name__ == "__main__":
    main()
