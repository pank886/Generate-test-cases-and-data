"""FastAPI 应用实例、生命周期、中间件、共享状态。"""

import asyncio
import json
import os
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Form
from fastapi.responses import HTMLResponse, JSONResponse
from jinja2 import Environment, FileSystemLoader, select_autoescape
from starlette.types import ASGIApp, Receive, Scope, Send

import config
from database import init_db
from observability import get_logger, init_logging, set_trace_id, generate_trace_id

# ----------------------------------------------------------------
# 日志初始化
# ----------------------------------------------------------------
init_logging()
logger = get_logger(__name__)

# ----------------------------------------------------------------
# Jinja2 模板
# ----------------------------------------------------------------
_TEMPLATE_DIR = Path(__file__).parent.parent / "templates"
_TEMPLATE_DIR.mkdir(exist_ok=True)
_env = Environment(
    loader=FileSystemLoader(str(_TEMPLATE_DIR)),
    autoescape=select_autoescape(["html", "xml"]),
)

# ----------------------------------------------------------------
# 全局状态
# ----------------------------------------------------------------
# 只读（lifespan 初始化后不变）
_phase_b_graph = None
_phase_b_components = None
_chroma_db = None

# 读写共享状态（_state_lock 保护）
_vector_ready = False
# {user_id: [{name, size, chunks, time, type, doc_id, status}, ...]}
_imported_files: dict[str, list[dict]] = {}
_DEFAULT_USER = "default"
_state_lock = asyncio.Lock()

# 后台任务状态追踪 {task_id: {status, progress, message, result, error}}
_task_store: dict = {}
_task_store_lock = asyncio.Lock()

# Phase B 多轮工作流会话存储
# {session_id: {"state": dict, "created_at": float, "user_id": str}}
_workflow_sessions: dict = {}
_workflow_sessions_lock = asyncio.Lock()
WORKFLOW_SESSION_TTL = config.WORKFLOW_SESSION_TTL


# ====== 临时文件定时清理 ======

_TEMP_CLEANUP_INTERVAL = 3600  # 每小时检查一次
_TEMP_FILE_MAX_AGE = 86400     # 24 小时未修改即视为过期


# TODO(多用户): 用 Redis 的 temp_token + 自动过期替代定时扫描清理。
#   每用户上传 extract-api 时生成临时 token（含 file_path），token TTL=1h 自动过期，
#   用户 commit/retry 时续期或主动删除，彻底解决"用户关浏览器"场景下的文件残留。
#   见 web/routes/api_extract.py 中的 extract_api_doc 和 commit_api_endpoint。


async def _cleanup_temp_files_loop():
    """后台定时任务：清理 extract-api 产生的中间 MD 文件（带 UUID 前缀的临时文件）。"""
    import re
    import time as _time
    md_dir = os.path.join(config.BASE_DIR, "uploads", "md")
    pattern = re.compile(r'^[0-9a-f]{8,32}_')
    while True:
        try:
            await asyncio.sleep(_TEMP_CLEANUP_INTERVAL)
            if not os.path.isdir(md_dir):
                continue
            now = _time.time()
            for fname in os.listdir(md_dir):
                if not pattern.match(fname):
                    continue
                fpath = os.path.join(md_dir, fname)
                if not os.path.isfile(fpath):
                    continue
                age = now - os.path.getmtime(fpath)
                if age > _TEMP_FILE_MAX_AGE:
                    try:
                        os.remove(fpath)
                    except OSError:
                        logger.warning("清理临时文件失败（可能被锁定）: %s", fpath, exc_info=True)
                        continue
                    logger.info("已清理过期临时文件: %s (age=%.1fh)", fname,
                                age / 3600)
        except asyncio.CancelledError:
            break
        except Exception:
            logger.warning("临时文件清理异常", exc_info=True)


def _scan_orphan_files(known_names: set, base_dir: str) -> list[dict]:
    """扫描上传目录中的孤儿文件（磁盘有但 SQLite 无记录）。"""
    import time as _time
    orphans = []
    _scan_specs = [
        ("uploads/pdf", ".pdf"), ("uploads/docx", ".docx"),
        ("uploads/product", ".pdf"), ("uploads/product", ".docx"),
        ("uploads/axure", ".zip"), ("uploads/md", ".md"),
    ]
    for scan_dir, ext in _scan_specs:
        dp = Path(base_dir) / scan_dir
        if not dp.is_dir():
            continue
        for f in dp.glob(f"*{ext}"):
            if f.name not in known_names:
                try:
                    st = f.stat()
                except OSError:
                    continue
                orphans.append({
                    "path": str(f),
                    "age_days": (_time.time() - st.st_mtime) / 86400,
                    "meta_exists": os.path.exists(str(f) + ".meta.json"),
                })
    return orphans


# ====== 文件列表辅助函数（user-scoped） ======

async def _get_imported_files(user_id: str = None) -> list[dict]:
    """获取指定用户的已导入文件列表（线程安全读）。"""
    uid = user_id or _DEFAULT_USER
    async with _state_lock:
        return list(_imported_files.get(uid, []))


async def _add_imported_file(file_info: dict, user_id: str = None):
    """添加已导入文件记录（线程安全写）。"""
    uid = user_id or _DEFAULT_USER
    async with _state_lock:
        if uid not in _imported_files:
            _imported_files[uid] = []
        _imported_files[uid].insert(0, file_info)
        global _vector_ready
        _vector_ready = True


async def _remove_imported_file(filename: str, user_id: str = None):
    """删除已导入文件记录（线程安全写）。"""
    uid = user_id or _DEFAULT_USER
    async with _state_lock:
        _imported_files[uid] = [
            f for f in _imported_files.get(uid, []) if f["name"] != filename
        ]
        if not _imported_files.get(uid):
            global _vector_ready
            _vector_ready = bool(any(v for v in _imported_files.values()))


# ----------------------------------------------------------------
# trace_id 中间件
# ----------------------------------------------------------------
class TraceMiddleware:
    """纯 ASGI 中间件，注入 X-Trace-Id 响应头。"""

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        tid = None
        for key, value in scope.get("headers", []):
            if key == b"x-trace-id":
                tid = value.decode()
                break
        if not tid:
            tid = generate_trace_id()
        set_trace_id(tid)

        async def send_with_trace(message):
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                # 防重复注入：链式中间件可能已追加过 trace_id
                if not any(k == b"x-trace-id" for k, _ in headers):
                    headers.append((b"x-trace-id", tid.encode()))
                    message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_with_trace)


# ----------------------------------------------------------------
# 任务状态管理
# ----------------------------------------------------------------
async def _create_task() -> str:
    """创建一个新任务并返回 task_id（顺带清理过期任务）。"""
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
                expired.append(tid)  # 无法解析的时间戳也清理
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


async def _cleanup_expired_sessions():
    """清理超过 WORKFLOW_SESSION_TTL 的 Phase B 工作流会话。"""
    import time
    now = time.time()
    async with _workflow_sessions_lock:
        expired = [
            sid for sid, s in _workflow_sessions.items()
            if now - s.get("created_at", 0) > WORKFLOW_SESSION_TTL
        ]
        for sid in expired:
            del _workflow_sessions[sid]


# _cleanup_doc_to_doc_bindings 已移至 web/services/doc_binding.py，从这里 re-export 保持兼容
from web.services.doc_binding import _cleanup_doc_to_doc_bindings  # noqa: F401


# ----------------------------------------------------------------
# 应用初始化
# ----------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期：启动时一次性初始化所有重资源。"""
    global _chroma_db, _vector_ready
    global _phase_b_graph, _phase_b_components
    # _imported_files 通过辅助函数访问，不再需要 global 声明
    # 0. 前置校验：必填配置项
    if not config.EMBEDDING_MODEL:
        print("=" * 60)
        print("❌ 缺少必填配置：EMBEDDING_MODEL")
        print("   请在项目根目录的 .env 文件中添加：")
        print("   EMBEDDING_MODEL=bge-m3")
        print("   （如未安装模型，先执行 ollama pull bge-m3）")
        print("=" * 60)
        raise RuntimeError("EMBEDDING_MODEL 未配置，请在 .env 中设置后重启")

    if not config.TESTCASE_BASE:
        missing = []
        if not config.PYCHARM_MISC: missing.append("PYCHARM_MISC=C:\\path\\to\\your\\pycharm\\project")
        if not config.TESTCASE_SUBDIR: missing.append("testcase_base=pytest_test_data")
        print("=" * 60)
        print("❌ 缺少必填的输出路径配置，请在 settings.py 中设置：")
        for m in missing: print(f"   {m}")
        print("=" * 60)
        raise RuntimeError("输出路径未配置，请在 settings.py 中设置 PYCHARM_MISC 和 testcase_base 后重启")

    # 1. SQLite
    try:
        init_db()
        print("[startup] SQLite 表已就绪")
    except Exception as e:
        print(f"[startup] WARNING: init_db failed: {e}")

    # 2. Ollama 启动检查（CPU 模式，GTX 1050 显存不足）
    import httpx as _httpx
    _ollama_url = config.EMBEDDING_URL or "http://localhost:11434"
    try:
        _resp = _httpx.get(f"{_ollama_url}/", timeout=3)
        _resp.raise_for_status()
        print(f"[startup] Ollama 已在运行: {_ollama_url}")
    except Exception:
        print(f"[startup] Ollama 未响应，尝试自动启动（CPU 模式）...")
        import subprocess as _sp, os as _os
        _env = {**_os.environ, "CUDA_VISIBLE_DEVICES": ""}
        try:
            if _os.name == "nt":
                _sp.Popen(["ollama", "serve"], env=_env,
                          creationflags=_sp.CREATE_NO_WINDOW)
            else:
                _sp.Popen(["ollama", "serve"], env=_env,
                          stdout=_sp.DEVNULL, stderr=_sp.DEVNULL)
            print("[startup] 已触发 Ollama 启动，等待就绪...")
            for _ in range(8):
                await asyncio.sleep(2)
                try:
                    _httpx.get(f"{_ollama_url}/", timeout=3)
                    print("[startup] Ollama 启动成功")
                    break
                except Exception:
                    pass
            else:
                print("[startup] ⚠️ Ollama 启动超时，继续尝试连接...")
        except FileNotFoundError:
            print("[startup] ⚠️ 未找到 ollama 命令，请确认 Ollama 已安装")

    # 3. ChromaDB + Ollama（带重试）
    for attempt in (1, 2, 3):
        try:
            from agent_components.dual_chroma import get_chroma_db
            _chroma_db = get_chroma_db()
            print("[startup] DualChromaDB + Ollama 连接已就绪")
            break
        except Exception as e:
            print(f"[startup] ChromaDB/Ollama 连接失败 (第{attempt}次): {e}")
            if attempt < 3:
                print("[startup] 等待 3 秒后重试...")
                await asyncio.sleep(3)
            else:
                print("=" * 60)
                print("❌ Ollama 连接失败，请检查：")
                print("   1. 运行 .\\infra\\start_ollama.bat 手动启动")
                print(f"   2. Embedding 模型是否已拉取（ollama pull {config.EMBEDDING_MODEL or 'bge-m3'}）")
                print(f"   3. 连接地址是否正确（当前: {_ollama_url}）")
                print("=" * 60)
                raise RuntimeError("Ollama 连接失败") from e

    # 3. Agent 初始化（Phase A + Phase B）
    logger.info(">>> 启动智能测试助手 Web 服务 ...")
    from agent_components.graph_builder import build_workflow
    _phase_b_graph, _phase_b_components = build_workflow()

    # 4. 恢复已导入文件列表（以 SQLite 为唯一数据源）
    from database import get_session, get_session_ctx
    from database.operations import DocOps
    db_files: dict[str, dict] = {}
    try:
        with get_session_ctx() as session:
            for d in DocOps.get_all_documents(session):
                size_str = "—"
                for scan_dir_base in ["uploads/pdf", "uploads/docx", "uploads/product",
                                      "uploads/axure", "uploads/md"]:
                    candidate = Path(config.BASE_DIR) / scan_dir_base / d.file_name
                    if candidate.is_file():
                        size_str = f"{candidate.stat().st_size / 1024:.1f} KB"
                        break
                db_files[d.file_name] = {
                    "name": d.file_name,
                    "type": d.doc_type,
                    "chunks": d.chunk_count or "—",
                    "time": d.upload_time.strftime("%Y-%m-%d %H:%M:%S")
                    if d.upload_time else "—",
                    "doc_id": d.id,
                    "status": d.status or "",
                    "size": size_str,
                }
    except Exception:
        logger.warning("SQLite 查询失败，无法恢复文件列表", exc_info=True)

    _imported_files[_DEFAULT_USER] = list(db_files.values())
    logger.info("已恢复 %d 个文件（全部来自 SQLite）", len(db_files))

    # 4.5 启动诊断：扫描磁盘孤儿文件
    _known_names = {f["name"] for f in db_files.values()}
    orphans = _scan_orphan_files(_known_names, config.BASE_DIR)
    if orphans:
        for o in orphans:
            logger.warning("⚠️ 孤儿文件（磁盘有但 SQLite 无记录）: %s (%.1f 天前)", o["path"], o["age_days"])
            if o["meta_exists"]:
                try: os.remove(o["path"] + ".meta.json")
                except OSError: logger.warning("清理孤儿 meta.json 失败: %s", o["path"], exc_info=True)
        logger.warning("⚠️ 检测到 %d 个孤儿文件，已列出。请手动清理。", len(orphans))
    else:
        logger.info("   ✅ 磁盘文件与 SQLite 一致，无孤儿文件")

    # 5. 判断向量库是否已就绪
    chroma_path = Path(config.CHROMA_DB_DIR)
    default_files = _imported_files.get(_DEFAULT_USER, [])
    if chroma_path.exists() and any(chroma_path.iterdir()):
        _vector_ready = True
        logger.info("   ✅ 向量库已就绪 (%d 个文件)", len(default_files))
    else:
        logger.info("   ℹ️ 向量库为空，请上传 API 文档")

    # 6. 启动定时清理临时文件后台任务
    _cleanup_task = asyncio.create_task(_cleanup_temp_files_loop())

    yield

    # --- shutdown ---
    _cleanup_task.cancel()
    from web.tasks import _executor
    _executor.shutdown(wait=True)


# ----------------------------------------------------------------
# FastAPI 实例
# ----------------------------------------------------------------
app = FastAPI(title="智能测试助手", version="0.3", lifespan=lifespan)
app.add_middleware(TraceMiddleware)


# ----------------------------------------------------------------
# 页面路由
# ----------------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
async def index():
    files = await _get_imported_files()
    template = _env.get_template("index.html")
    return HTMLResponse(template.render(
        vector_ready=_vector_ready,
        imported_files=files,
    ))


@app.get("/favicon.ico")
async def favicon():
    from fastapi.responses import Response
    return Response(status_code=204)

@app.get("/.well-known/appspecific/com.chrome.devtools.json")
async def chrome_devtools_probe():
    from fastapi.responses import Response
    return Response(status_code=204)


# ----------------------------------------------------------------
# 模块审核路由（非标准前缀，放在 app 层）
# ----------------------------------------------------------------
@app.post("/update-module")
async def audit_module(data: dict):
    """审核确认/修改模块关联关系。"""
    from database import get_session, get_session_ctx
    from database.operations import DocOps
    from web.services.doc_binding import rebind_doc_to_module

    doc_id = data.get("doc_id")
    module_name = data.get("module_name")
    related_modules = data.get("related_modules", [])
    if not doc_id:
        return JSONResponse(status_code=400,
                            content={"success": False, "message": "缺少 doc_id"})
    try:
        with get_session_ctx() as session:
            rebind_doc_to_module(session, doc_id, module_name or "")
            # 额外关联模块（不同名才绑定）
            from ingest_v2 import _cascade_bind_to_module_docs
            doc = DocOps.get_document(session, doc_id)
            doc_type = doc.doc_type if doc else "product"
            for rmod in related_modules:
                if rmod != module_name:
                    from database.operations import BindingOps
                    BindingOps.bind(session, doc_type, doc_id, "module", rmod)
                    _cascade_bind_to_module_docs(session, doc_type, doc_id, rmod)
            return {"success": True, "message": f"模块信息已更新: {module_name}"}
    except Exception as e:
        logger.error("update-module 失败: %s", e, exc_info=True)
        return JSONResponse(status_code=500,
                            content={"success": False, "message": str(e)})


# ----------------------------------------------------------------
# 注册子路由
# ----------------------------------------------------------------
from web.routes.files import router as files_router
from web.routes.modules import router as modules_router
from web.routes.docs import router as docs_router
from web.routes.bindings import router as bindings_router
from web.routes.chat import router as chat_router
from web.routes.api_extract import router as api_extract_router

app.include_router(files_router)
app.include_router(modules_router)
app.include_router(docs_router)
app.include_router(bindings_router)
app.include_router(chat_router)
app.include_router(api_extract_router)

# 静态文件（CSS / JS）—— 必须在路由注册之后，确保路由优先匹配
from fastapi.staticfiles import StaticFiles
_static_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static")
os.makedirs(_static_dir, exist_ok=True)
app.mount("/static", StaticFiles(directory=_static_dir), name="static")
