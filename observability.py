"""日志与可观测性模块。

功能:
  1. 结构化 JSON 日志 → 写入文件 (logs/app.log)
  2. 控制台输出保留（StreamHandler，用户可见）
  3. 每个 HTTP 请求自动生成 trace_id，贯穿所有日志

用法:
    from observability import get_logger
    logger = get_logger(__name__)
    logger.info("处理完成: %d 个文本块", count)

不需要再手动 print() —— 日志同时输出到控制台和文件。
"""

import json
import logging
import logging.handlers
import os
import sys
import uuid
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from config import LOG_DIR, LOG_LEVEL

# ====== trace_id 上下文变量 ======
_trace_id_var: ContextVar[str] = ContextVar("trace_id", default="")


def get_trace_id() -> str:
    """获取当前请求的 trace_id。"""
    return _trace_id_var.get()


def set_trace_id(tid: str) -> None:
    """设置当前请求的 trace_id（由中间件调用）。"""
    _trace_id_var.set(tid)


def generate_trace_id() -> str:
    """生成新的 trace_id（12 位 hex）。"""
    return uuid.uuid4().hex[:12]


# ====== JSON 格式化器 ======

class JSONFormatter(logging.Formatter):
    """将日志记录格式化为 JSON 行。"""

    def format(self, record: logging.LogRecord) -> str:
        entry: dict = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "trace_id": getattr(record, "trace_id", "") or "-",
        }
        if record.exc_info and record.exc_info[0]:
            entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(entry, ensure_ascii=False)


# ====== trace_id 注入 Filter ======

class TraceFilter(logging.Filter):
    """将 ContextVar 中的 trace_id 注入到每条日志记录。"""

    def filter(self, record: logging.LogRecord) -> bool:
        record.trace_id = get_trace_id() or "-"
        return True


# ====== 初始化 ======

_initialized: bool = False


def init_logging(
    log_dir: Optional[str] = None,
    level: Optional[str] = None,
) -> None:
    """初始化日志系统（幂等，仅首次调用生效）。

    - 文件输出: {log_dir}/app.log (JSON 格式)
    - 控制台输出: stdout (保留 print 可见性)
    """
    global _initialized
    if _initialized:
        return

    log_dir = log_dir or LOG_DIR
    level = level or LOG_LEVEL

    # ---- Windows 终端 GBK 编码修复 ----
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        pass  # Linux / 非交互模式 静默跳过

    os.makedirs(log_dir, exist_ok=True)
    log_level = getattr(logging, level.upper(), logging.INFO)

    root = logging.getLogger()
    root.setLevel(log_level)

    # 清除已有的 handler（防止重复添加）
    root.handlers.clear()

    # ---- 文件 handler (JSON) ----
    file_handler = logging.FileHandler(
        os.path.join(log_dir, "app.log"), encoding="utf-8"
    )
    file_handler.setLevel(log_level)
    file_handler.setFormatter(JSONFormatter())
    file_handler.addFilter(TraceFilter())
    root.addHandler(file_handler)

    # ---- 控制台 handler (保留 print 可见性) ----
    console = logging.StreamHandler(sys.stdout)
    console.setLevel(log_level)
    # 控制台格式：只输出消息本身（模拟 print 行为），开发时可加前缀
    console.setFormatter(logging.Formatter("%(message)s"))
    console.addFilter(TraceFilter())
    root.addHandler(console)

    _initialized = True

    # 记录启动日志 (仅文件，因为 stdout 可能还没准备好 emoji)
    root.info("日志系统初始化完成 | log_dir=%s | level=%s", log_dir, level)


# ====== 便捷获取 logger ======

def get_logger(name: str) -> logging.Logger:
    """获取模块级 logger。

    如果日志系统尚未初始化，自动以默认配置初始化。
    """
    if not _initialized:
        init_logging()
    return logging.getLogger(name)
