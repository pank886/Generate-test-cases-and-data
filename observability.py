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
import re
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
    """生成新的 trace_id（32 位 hex，128-bit 熵）。"""
    return uuid.uuid4().hex


# ====== JSON 格式化器 ======

SENSITIVE_PATTERNS = [
    # API Key / Token / Secret 在 JSON 或 URL 参数中的各种表示
    (re.compile(r'(api[_-]?key|api[_-]?secret|access[_-]?token|auth[_-]?token)["\'\\s:=]+\S+', re.IGNORECASE),
     r'\1=***'),
    # Authorization header
    (re.compile(r'(Authorization|Bearer)\s+\S+', re.IGNORECASE),
     r'\1 ***'),
]


def _sanitize(msg: str) -> str:
    """脱敏日志中的敏感字段（API Key、Token、Secret 等）。"""
    for pattern, replacement in SENSITIVE_PATTERNS:
        msg = pattern.sub(replacement, msg)
    return msg


def _safe_get_message(record: logging.LogRecord) -> str:
    """安全获取日志消息，防止 args 与 format string 不匹配时崩溃。"""
    try:
        return record.getMessage()
    except Exception:
        return record.msg % {k: f"<unprintable {type(v).__name__}>" for k, v in (record.args or {}).items()} if isinstance(record.args, dict) else str(record.msg)


class JSONFormatter(logging.Formatter):
    """将日志记录格式化为 JSON 行（自动脱敏敏感字段）。"""

    def format(self, record: logging.LogRecord) -> str:
        entry: dict = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": _sanitize(_safe_get_message(record)),
            "trace_id": getattr(record, "trace_id", "") or "-",
        }
        if record.exc_info and record.exc_info[0]:
            entry["exception"] = _sanitize(self.formatException(record.exc_info))
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

    # ---- 控制台 handler（仅 logger 输出走 UTF-8，不修改全局 sys.stdout） ----
    import io as _io
    try:
        console_stream = _io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        console_stream = sys.stdout  # Linux / 非交互模式 fallback
    console = logging.StreamHandler(console_stream)
    console.setLevel(log_level)
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


# ====== 修复失败快照 Logger（RotatingFileHandler，自动轮转） ======

_repair_logger: logging.Logger | None = None


def get_error_snapshot_logger() -> logging.Logger:
    """返回专用于写入修复失败快照的 logger。

    使用 RotatingFileHandler:
      - 文件: {LOG_DIR}/repair_failures.log
      - 超过 5MB 自动轮转，保留最近 10 个归档
    """
    global _repair_logger
    if _repair_logger is not None:
        return _repair_logger

    if not _initialized:
        init_logging()

    logger = logging.getLogger("repair_failures")
    logger.propagate = False  # 不污染 root logger
    if not logger.handlers:
        h = logging.handlers.RotatingFileHandler(
            os.path.join(LOG_DIR, "repair_failures.log"),
            maxBytes=5 * 1024 * 1024,
            backupCount=10,
            encoding="utf-8",
        )
        h.setLevel(logging.DEBUG)
        h.setFormatter(logging.Formatter("%(asctime)s | %(message)s"))
        logger.addHandler(h)
        logger.setLevel(logging.DEBUG)

    _repair_logger = logger
    return logger


# ====== Thinking 节点日志（提示词调优专用） ======

_thinking_logger: logging.Logger | None = None


def get_thinking_logger() -> logging.Logger:
    """返回专用于记录 thinking 节点输出的 RotatingFileHandler logger。

    文件: {LOG_DIR}/thinking_trace.log，超过 5MB 自动轮转，保留 10 个归档。
    """
    global _thinking_logger
    if _thinking_logger is not None:
        return _thinking_logger

    if not _initialized:
        init_logging()

    tlog = logging.getLogger("thinking_trace")
    tlog.propagate = False
    if not tlog.handlers:
        h = logging.handlers.RotatingFileHandler(
            os.path.join(LOG_DIR, "thinking_trace.log"),
            maxBytes=5 * 1024 * 1024,
            backupCount=10,
            encoding="utf-8",
        )
        h.setLevel(logging.DEBUG)
        h.setFormatter(logging.Formatter("%(asctime)s | %(message)s"))
        tlog.addHandler(h)
        tlog.setLevel(logging.DEBUG)

    _thinking_logger = tlog
    return tlog


def log_phase_header(phase: str) -> None:
    """在 thinking 日志中写入醒目的阶段分隔线。

    格式: ==================== Phase A ========================

    Args:
        phase: 阶段名（如 Phase A / Phase B / Phase C）
    """
    tlog = get_thinking_logger()
    sep = "=" * 25
    tlog.info("\n%s %s %s", sep, phase, sep)


def log_thinking(node: str, user_input: str, output: str, prompt_label: str = "") -> None:
    """记录 thinking 节点的输入输出到专属日志。

    Args:
        node: 节点名（如 analyze_scenarios / analyze_test_points_raw / analyze_data_deps）
        user_input: 用户原始输入
        output: LLM 完整非结构化输出文本
        prompt_label: Prompt 标识（如 analyze_scenarios_prompt），方便定位使用的提示词模板
    """
    tlog = get_thinking_logger()

    # 构造分段日志，每个节点用醒目的 *** 分隔
    header = f"*** {node} ***"
    if prompt_label:
        header += f"  [prompt: {prompt_label}]"

    tlog.info(
        "%s\n"
        "用户输入: %s\n"
        "--- LLM 输出 (%d 字符) ---\n"
        "%s\n"
        "=== END %s ===\n\n\n",
        header,
        user_input[:500] if user_input else "（无）",
        len(output),
        output,
        node,
    )
