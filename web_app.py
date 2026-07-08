#!/usr/bin/env python3
"""Web 入口：智能测试助手 Web 版（FastAPI + BackgroundTasks）

启动方式：python web_app.py
实际逻辑分布在 web/ 目录下：
  web/app.py            — FastAPI 实例 + lifespan + 中间件 + 状态
  web/routes/*.py       — 路由分组
  web/tasks.py          — 后台异步任务
  web/services/         — 可复用业务逻辑
"""

import sys

# 强制 UTF-8 编码，防止 Windows 终端打印 emoji 时报 GBK 错误
sys.stdout.reconfigure(encoding="utf-8")

import config
from web.app import app, logger


if __name__ == "__main__":
    import threading
    import uvicorn

    local_url = f"http://{config.WEB_HOST}:{config.WEB_PORT}"

    server_config = uvicorn.Config(
        app, host=config.WEB_HOST, port=config.WEB_PORT,
    )
    server = uvicorn.Server(server_config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    logger.info("\n🌐 本地访问地址: %s", local_url)
    logger.info("   如果 0.0.0.0 无法访问，尝试: http://localhost:%d",
                config.WEB_PORT)
    logger.info("\n💡 输入 q 并回车可停止服务\n")

    try:
        while True:
            cmd = input().strip().lower()
            if cmd == "q":
                logger.info(">>> 正在停止服务 ...")
                server.should_exit = True
                break
    except (KeyboardInterrupt, EOFError):
        logger.info("\n>>> 正在停止服务 ...")
        server.should_exit = True
