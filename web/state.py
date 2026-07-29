"""全局共享状态（独立模块，无项目内依赖，避免循环导入）。

原位置: web/app.py
迁移目的: web/routes/* 和 web/tasks.py 直接 import，不再需要 from web.app import _xxx
"""

import asyncio
import time as _time
import uuid
from datetime import datetime

from observability import get_logger

logger = get_logger(__name__)

# ============================================================
# LLM / 工作流组件实例（lifespan 初始化后不变）
# ============================================================

_phase_b_graph = None
_phase_b_components = None
_chroma_db = None

# ============================================================
# 文件导入状态（_state_lock 保护）
# ============================================================

_vector_ready = False
_imported_files: dict[str, list[dict]] = {}   # {user_id: [{name, size, chunks, time, type, doc_id, status}, ...]}
_DEFAULT_USER = "default"
_state_lock = asyncio.Lock()


async def _get_imported_files(user_id: str = None) -> list[dict]:
    """获取指定用户的已导入文件列表（线程安全读）。"""
    uid = user_id or _DEFAULT_USER
    async with _state_lock:
        return list(_imported_files.get(uid, []))


async def _add_imported_file(file_info: dict, user_id: str = None):
    """添加已导入文件记录（线程安全写）。"""
    global _vector_ready
    uid = user_id or _DEFAULT_USER
    async with _state_lock:
        if uid not in _imported_files:
            _imported_files[uid] = []
        _imported_files[uid].insert(0, file_info)
        _vector_ready = True


async def _remove_imported_file(filename: str, user_id: str = None):
    """删除已导入文件记录（线程安全写）。"""
    global _vector_ready
    uid = user_id or _DEFAULT_USER
    async with _state_lock:
        _imported_files[uid] = [
            f for f in _imported_files.get(uid, []) if f["name"] != filename
        ]
        if not _imported_files.get(uid):
            _vector_ready = bool(any(v for v in _imported_files.values()))

# ============================================================
# 后台任务状态追踪  {task_id: {status, progress, message, result, error}}
# ============================================================

_task_store: dict = {}
_task_store_lock = asyncio.Lock()


async def _create_task() -> str:
    """创建一个新任务并返回 task_id，顺带清理过期任务。"""
    import config as _config
    now = datetime.now()
    ttl = _config.TASK_TTL_SECONDS
    task_id = uuid.uuid4().hex
    async with _task_store_lock:
        expired = []
        for tid, t in _task_store.items():
            try:
                created = datetime.fromisoformat(t.get("created_at", ""))
                if (now - created).total_seconds() > ttl:
                    expired.append(tid)
            except (ValueError, TypeError):
                expired.append(tid)
        for tid in expired:
            del _task_store[tid]
        _task_store[task_id] = {
            "status": "pending",
            "progress": 0,
            "message": "任务已提交",
            "result": None,
            "error": None,
            "created_at": now.isoformat(),
        }
    return task_id


async def _update_task(task_id: str, **kwargs):
    """更新任务状态。"""
    async with _task_store_lock:
        if task_id in _task_store:
            _task_store[task_id].update(kwargs)


# ============================================================
# Phase B 多轮工作流会话
#   {session_id: {"state": dict, "created_at": float, "user_id": str}}
# ============================================================

_workflow_sessions: dict = {}
_workflow_sessions_lock = asyncio.Lock()

# WORKFLOW_SESSION_TTL 从 config 读取，在 web/app.py 模块级定义
WORKFLOW_SESSION_TTL = 3600  # 默认值，实际由 web/app.py 的 config.WORKFLOW_SESSION_TTL 覆盖


async def _cleanup_expired_sessions():
    """清理过期的 Phase B 工作流会话。"""
    now = _time.time()
    async with _workflow_sessions_lock:
        expired = [
            sid for sid, s in _workflow_sessions.items()
            if now - s.get("created_at", 0) > WORKFLOW_SESSION_TTL
        ]
        for sid in expired:
            del _workflow_sessions[sid]
    if expired:
        logger.info("清理 %d 个过期工作流会话", len(expired))
