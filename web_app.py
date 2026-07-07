#!/usr/bin/env python3
"""Web 入口：智能测试助手 Web 版（FastAPI + BackgroundTasks）"""
import asyncio
import json
import os
import sys
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

# 强制 UTF-8 编码，防止 Windows 终端打印 emoji 时报 GBK 错误
sys.stdout.reconfigure(encoding="utf-8")

import uvicorn
from fastapi import FastAPI, UploadFile, File, Form, Request, BackgroundTasks
from fastapi.responses import HTMLResponse, JSONResponse
from jinja2 import Environment, FileSystemLoader, select_autoescape
from starlette.types import ASGIApp, Receive, Scope, Send

import config
from database import init_db, get_session
from database.models import Document, Binding, Module
from database.operations import BindingOps, DocOps, GlossaryOps, ModuleOps
from agent_components.dual_chroma import get_chroma_db
from agent_components.chromadb_file import ensure_directory, ReadersChromadb
from observability import get_logger, init_logging, set_trace_id, generate_trace_id

from agent_components.graph_builder import build_and_run_agent
from datetime import datetime

# ----------------------------------------------------------------
# 日志初始化
# ----------------------------------------------------------------
init_logging()
logger = get_logger(__name__)

# ----------------------------------------------------------------
# 应用初始化
# ----------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期：启动时一次性初始化所有重资源，关闭时清理。"""
    global _chroma_db, _chat_func, _components, _vector_ready, _imported_files

    # --- startup ---
    # 1. SQLite 初始化
    try:
        init_db()
        print("[startup] SQLite 表已就绪")
    except Exception as e:
        print(f"[startup] WARNING: init_db failed: {e}")

    # 2. Ollama + ChromaDB（带重试）
    for attempt in (1, 2, 3):
        try:
            _chroma_db = get_chroma_db()
            print("[startup] DualChromaDB + Ollama 连接已就绪")
            break
        except Exception as e:
            print(f"[startup] Ollama 连接失败 (第{attempt}次): {e}")
            if attempt < 3:
                print(f"[startup] 等待 3 秒后重试...")
                await asyncio.sleep(3)
            else:
                print("=" * 60)
                print("❌ Ollama 连接失败，请检查：")
                print("   1. Ollama 服务是否已启动（运行 ollama serve）")
                print("   2. Embedding 模型是否已拉取（ollama pull <model>）")
                print(f"   3. 连接地址是否正确（当前: {config.EMBEDDING_URL or 'http://localhost:11434'}）")
                print("=" * 60)
                raise RuntimeError("Ollama 连接失败，请检查 Ollama 服务状态后重启应用") from e

    # 3. Agent 初始化
    logger.info(">>> 启动智能测试助手 Web 服务 ...")
    _chat_func = build_and_run_agent()
    _components = _chat_func.components  # 保存实例用于后续生成

    # 4. 扫描 uploads/ 下各类型子目录，恢复已导入文件列表
    ext_to_type = {".pdf": "pdf", ".md": "md", ".docx": "docx", ".zip": "axure"}
    scan_dirs = [
        ("uploads/pdf", ".pdf"),
        ("uploads/md", ".md"),
        ("uploads/docx", ".docx"),
        ("uploads/axure", ".zip"),
        ("uploads", ".pdf"),  # 兼容旧版根目录的 PDF
    ]
    seen_names = set()
    for scan_dir, ext in scan_dirs:
        dir_path = Path(scan_dir)
        if dir_path.exists():
            files = sorted(dir_path.glob(f"*{ext}"), key=lambda p: p.stat().st_mtime, reverse=True)
            for f in files:
                if f.name in seen_names:
                    continue  # 去重（同一文件可能在多个目录出现）
                seen_names.add(f.name)
                size_kb = f.stat().st_size / 1024
                mtime = datetime.fromtimestamp(f.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")
                file_type = ext_to_type.get(f.suffix.lower(), "?")

                # 优先从 .meta.json 恢复块数和时间，没有则回退到文件时间
                chunks = "—"
                meta_path = str(f) + ".meta.json"
                if os.path.exists(meta_path):
                    try:
                        with open(meta_path, "r", encoding="utf-8") as _mf:
                            _meta = json.load(_mf)
                        chunks = _meta.get("chunks", "—")
                        mtime = _meta.get("time", mtime)
                    except Exception:
                        pass

                _imported_files.append({
                    "name": f.name,
                    "size": f"{size_kb:.1f} KB",
                    "chunks": chunks,
                    "time": mtime,
                    "type": file_type,
                })

    # 5. 判断向量库是否已就绪
    chroma_path = Path(config.CHROMA_DB_DIR)
    if chroma_path.exists() and any(chroma_path.iterdir()):
        _vector_ready = True
        logger.info("   ✅ 向量库已就绪 (%d 个文件)", len(_imported_files))
    else:
        logger.info("   ℹ️ 向量库为空，请上传 API 文档")

    yield  # 应用运行期间

    # --- shutdown ---
    # （暂无需要清理的资源）


app = FastAPI(title="智能测试助手", version="0.2", lifespan=lifespan)


# Jinja2 模板
_TEMPLATE_DIR = Path(__file__).parent / "templates"
_TEMPLATE_DIR.mkdir(exist_ok=True)
_env = Environment(
    loader=FileSystemLoader(str(_TEMPLATE_DIR)),
    autoescape=select_autoescape(["html", "xml"]),
)

# 全局状态（单用户模式）
_chat_func = None
_components = None  # ChatTestAgentGraph 实例，用于后续生成
_vector_ready = False
_imported_files = []  # 已导入文件列表：[{"name", "size", "chunks", "time"}]
_last_api_defs = None  # 最后一次聊天中的 API 定义
_last_user_input = None  # 最后一次用户输入
_chroma_db = None  # DualChromaDB 全局实例，startup 时初始化

# 并发保护锁
_state_lock = asyncio.Lock()

# 后台任务状态追踪 {task_id: {status, progress, message, result, error}}
_task_store: dict = {}
_task_store_lock = asyncio.Lock()

TASK_TTL_SECONDS = 3600  # 任务状态保留 1 小时


# ----------------------------------------------------------------
# trace_id 中间件（纯 ASGI，绕过 BaseHTTPMiddleware 的流式响应 bug）
# ----------------------------------------------------------------
class TraceMiddleware:
    """纯 ASGI 中间件，不经过 BaseHTTPMiddleware 的流式包装。

    在 ASGI 层面直接注入 X-Trace-Id 响应头，避免 Starlette 0.40+
    BaseHTTPMiddleware 在流式重发 HTMLResponse 时因多字节 UTF-8 字符
    导致的 Content-Length 不匹配 RuntimeError。
    """
    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        # 从请求头读取或生成 trace_id
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
                headers.append((b"x-trace-id", tid.encode()))
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_with_trace)


app.add_middleware(TraceMiddleware)


# ----------------------------------------------------------------
# 任务状态管理
# ----------------------------------------------------------------
async def _create_task() -> str:
    """创建一个新任务并返回 task_id。"""
    task_id = uuid.uuid4().hex[:12]
    async with _task_store_lock:
        _task_store[task_id] = {
            "status": "pending",
            "progress": 0,
            "message": "任务已提交",
            "result": None,
            "error": None,
            "created_at": datetime.now().isoformat(),
        }
    return task_id


async def _update_task(task_id: str, **kwargs):
    """更新任务状态。"""
    async with _task_store_lock:
        if task_id in _task_store:
            _task_store[task_id].update(kwargs)


def _cleanup_doc_to_doc_bindings(session, doc_id: str):
    """删除指定文档的所有 doc↔doc 级联绑定。换模块/解绑前调用。"""
    from database.models import Binding
    doc_types = ("product", "api", "axure")
    session.query(Binding).filter(
        Binding.left_type.in_(doc_types),
        Binding.right_type.in_(doc_types),
        ((Binding.left_id == doc_id) | (Binding.right_id == doc_id)),
    ).delete(synchronize_session=False)


# ----------------------------------------------------------------
# 后台任务函数
# ----------------------------------------------------------------
async def _process_file_bg(task_id: str, file_path: str, ext: str, file_size: int,
                            filename: str, file_type: str):
    """后台处理上传文件 -> 向量库入库。"""
    global _vector_ready, _imported_files
    set_trace_id(task_id)

    def _progress(pct: int, msg: str):
        """同步进度回调，供 ingest 函数使用"""
        import asyncio as _asyncio
        try:
            loop = _asyncio.get_event_loop()
            if loop.is_running():
                _asyncio.ensure_future(_update_task(task_id, progress=pct, message=msg))
        except Exception:
            pass

    try:
        await _update_task(task_id, status="running", progress=5, message="接收文件，开始处理...")

        if ext == ".zip":
            from ingest_v2 import process_axure_zip
            _progress(10, "解压 Axure 包，解析页面结构...")
            result = process_axure_zip(file_path, progress_cb=lambda p, m: _progress(10 + int(p * 0.8), m))
            count = result.get("chunks", 0)
            source = "Axure 原型"
        elif ext == ".md":
            from ingest_v2 import process_api_doc_extract
            _progress(10, "读取 Markdown，提取接口定义...")
            result = process_api_doc_extract(file_path, progress_cb=lambda p, m: _progress(10 + int(p * 0.8), m))
            apis = result.get("apis", [])
            count = len(apis)
            source = "API 文档"
            module_name = result.get("module_name")

            if count == 0:
                await _update_task(task_id, status="failed", error="未提取到接口定义，请检查文档格式。")
                return

            # API 文档不走直接入库，返回接口列表等待用户确认
            resp = {
                "success": True,
                "message": f"已提取 {count} 个接口定义，请确认后入库",
                "apis": apis,
                "file_path": file_path,
                "module_name": module_name or "Unknown",
            }
            await _update_task(task_id, status="completed", progress=100,
                               message="提取完成，等待确认", result=resp)
            return

        else:
            from ingest_v2 import process_product_doc
            _progress(10, "读取文档，提取模块信息...")
            result = process_product_doc(file_path, progress_cb=lambda p, m: _progress(10 + int(p * 0.8), m))
            count = result.get("chunks", 0)
            source = {".docx": "Word 文档", ".pdf": "PDF 文档"}.get(ext, "文档")

        await _update_task(task_id, progress=90, message=f"{source} 处理完成：{count} 个文本块")

        if count == 0:
            await _update_task(task_id, status="failed", error="文件解析后无内容，请检查文件是否有效。")
            return

        module_name = result.get("module_name")
        doc_id = result.get("doc_id")

        file_info = {
            "name": filename,
            "size": f"{file_size / 1024:.1f} KB",
            "chunks": count,
            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "type": file_type,
        }
        async with _state_lock:
            _vector_ready = True
            _imported_files.insert(0, file_info)

        logger.info("✅ %s 处理完成：%d 个文本块", source, count)

        # 写入元数据文件（供重启恢复块数/类型），失败不影响主流程
        try:
            import json as _json
            _meta = {"chunks": count, "type": file_type, "time": datetime.now().isoformat(),
                     "module": module_name or "", "doc_id": doc_id or ""}
            with open(file_path + ".meta.json", "w", encoding="utf-8") as _mf:
                _json.dump(_meta, _mf, ensure_ascii=False)
        except Exception:
            pass

        resp = {
            "success": True,
            "message": f"已处理 {count} 个文本块",
            "file": file_info,
        }
        if module_name:
            resp["module_name"] = module_name
            resp["doc_id"] = doc_id
            resp["related_modules"] = result.get("related_modules", [])

        await _update_task(task_id, status="completed", progress=100,
                           message="处理完成", result=resp)

    except FileNotFoundError:
        await _update_task(task_id, status="failed", error="上传文件不存在")
    except Exception as e:
        logger.error("❌ 文件处理失败: %s", e)
        await _update_task(task_id, status="failed", error=str(e))


async def _run_chat_bg(task_id: str, user_input: str):
    """后台执行聊天 -> 测试计划生成。"""
    global _last_api_defs, _last_user_input
    set_trace_id(task_id)

    try:
        await _update_task(task_id, status="running", progress=5, message="正在检索知识库...")

        response = _chat_func(user_input)

        await _update_task(task_id, progress=80, message="生成完成，正在保存结果...")

        if response:
            result = {
                "success": True,
                "thinking": response.proper_thinking,
                "reply": response.final_response,
            }
            if hasattr(response, "excel_path") and response.excel_path:
                result["excel_path"] = response.excel_path
                result["excel_name"] = os.path.basename(response.excel_path)
                result["output_dir"] = getattr(response, "output_dir", os.path.dirname(response.excel_path))
            if hasattr(response, "requires_review") and response.requires_review:
                result["requires_review"] = True
                result["error_info"] = getattr(response, "error_info", [])

            async with _state_lock:
                if hasattr(response, "api_definition_list"):
                    _last_api_defs = response.api_definition_list
                _last_user_input = user_input

            await _update_task(task_id, status="completed", progress=100,
                               message="测试计划生成完成", result=result)
        else:
            await _update_task(task_id, status="failed", error="模型无响应")

    except Exception as e:
        logger.error("❌ 聊天处理失败: %s", e)
        await _update_task(task_id, status="failed", error=str(e))


async def _confirm_plan_bg(task_id: str, excel_path: str | None):
    """后台执行确认计划 -> 生成 .py + .yaml。"""
    global _components, _last_api_defs, _last_user_input
    set_trace_id(task_id)

    try:
        # 定位 Excel 文件
        if not excel_path:
            import glob
            excel_files = glob.glob(os.path.join(config.TESTCASE_BASE, "**", "test_plan.xlsx"), recursive=True)
            if excel_files:
                excel_path = max(excel_files, key=os.path.getmtime)

        if not excel_path:
            await _update_task(task_id, status="failed", error="未找到测试计划 Excel 文件")
            return

        if not _components:
            await _update_task(task_id, status="failed", error="组件未初始化")
            return

        await _update_task(task_id, status="running", progress=20, message="正在生成 .py 测试文件...")

        # Step A: 生成 .py 文件
        py_result = _components._generate_py_file(excel_path)

        await _update_task(task_id, progress=50, message="正在生成 YAML 数据文件...")

        # Step B: 生成 YAML 数据文件
        api_defs = _last_api_defs or []
        api_defs_json = json.dumps(
            [a.model_dump() if hasattr(a, "model_dump") else a for a in api_defs],
            indent=2, ensure_ascii=False,
        ) if api_defs else "[]"
        user_ctx = _last_user_input or ""
        yaml_result = _components._generate_all_yamls(excel_path, api_defs_json, user_ctx)

        msg = f".py: {py_result['py_file_name']}（{py_result['modules']}模块）"
        if yaml_result["total"] > 0:
            msg += f" | YAML: {yaml_result['success']}/{yaml_result['total']} 个"

        result = {
            "success": True,
            "message": msg,
            "py_file": py_result["py_file_name"],
            "yaml_success": yaml_result["success"],
            "yaml_total": yaml_result["total"],
        }

        await _update_task(task_id, status="completed", progress=100,
                           message="文件生成完成", result=result)

    except Exception as e:
        logger.error("❌ 确认计划失败: %s", e)
        await _update_task(task_id, status="failed", error=str(e))


# ----------------------------------------------------------------
# Chrome DevTools 探测请求 —— 静默忽略
# ----------------------------------------------------------------
@app.get("/.well-known/appspecific/com.chrome.devtools.json")
async def chrome_devtools_probe():
    # 204 不允许携带 Body，用 Response 而非 JSONResponse 避免
    # "Response content longer than Content-Length" 错误
    from fastapi.responses import Response
    return Response(status_code=204)


# ----------------------------------------------------------------
# 页面路由
# ----------------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
async def index():
    template = _env.get_template("index.html")
    return HTMLResponse(template.render(
        vector_ready=_vector_ready,
        imported_files=_imported_files,
    ))


# ----------------------------------------------------------------
# API 接口
# ----------------------------------------------------------------
@app.post("/upload-file")
async def upload_file(file: UploadFile = File(...), background_tasks: BackgroundTasks = None):
    """上传文件 -> 立即返回 task_id，后台异步处理。"""
    # 1. 识别文件类型
    filename = file.filename
    ext = os.path.splitext(filename)[1].lower()
    type_map = {
        ".pdf": "pdf",
        ".md": "md",
        ".docx": "docx",
        ".zip": "axure",
    }
    file_type = type_map.get(ext)
    if file_type is None:
        supported = ", ".join(type_map.keys())
        return JSONResponse(
            status_code=400,
            content={"success": False, "message": f"不支持的文件类型: {ext}。当前支持: {supported}"},
        )

    # 2. 保存到类型专属子目录
    type_dir = ensure_directory(f"./uploads/{file_type}")
    file_path = os.path.join(type_dir, filename)
    # 同名文件覆盖：先清理旧数据的 ChromaDB + SQLite 记录
    if os.path.exists(file_path):
        try:
            old_session = get_session()
            try:
                old_doc = old_session.query(Document).filter(Document.file_name == filename).first()
                if old_doc:
                    BindingOps.delete_bindings_for_doc(old_session, old_doc.id)
                    DocOps.delete_document(old_session, old_doc.id)
                    old_session.commit()
                    _chroma_db.delete_by_doc_id(old_doc.id)
            finally:
                old_session.close()
        except Exception:
            pass
    content = await file.read()
    with open(file_path, "wb") as f:
        f.write(content)

    # 3. 创建后台任务
    task_id = await _create_task()
    background_tasks.add_task(
        _process_file_bg, task_id, file_path, ext,
        len(content), filename, file_type,
    )

    return {"success": True, "task_id": task_id, "message": "文件已接收，后台处理中"}


@app.post("/delete-file")
async def delete_file(filename: str = Form(...)):
    """删除文件：先解绑 doc↔doc + doc↔module，再删向量+文档，产品文档级联删术语。"""
    global _vector_ready, _imported_files

    # 1. 查找文件路径
    file_path = None
    for scan_dir in ["uploads/pdf", "uploads/md", "uploads/docx", "uploads/axure", "uploads"]:
        candidate = os.path.join(scan_dir, filename)
        if os.path.exists(candidate):
            file_path = os.path.abspath(candidate)
            break

    if not file_path:
        return JSONResponse(status_code=404,
                            content={"success": False, "message": f"文件 '{filename}' 不存在"})

    try:
        # 2. 查 SQLite 获取 doc_id 和 doc_type
        from database import get_session
        from database.models import Document
        from database.operations import BindingOps, DocOps
        session = get_session()
        try:
            doc = session.query(Document).filter(Document.file_name == filename).first()
        finally:
            session.close()

        if not doc:
            # 文件存在但无 DB 记录，直接删除物理文件
            os.remove(file_path)
            return {"success": True, "message": f"已删除 '{filename}'（无数据库记录）"}

        doc_id = doc.id
        doc_type = doc.doc_type

        # 3. 解除该文档所有 doc↔doc 绑定 + doc↔module 绑定
        sql_ok = True
        session = get_session()
        try:
            _cleanup_doc_to_doc_bindings(session, doc_id)
            BindingOps.delete_bindings_for_doc(session, doc_id)

            # 5. 删除文档（产品文档自动级联删除术语 via FK）
            DocOps.delete_document(session, doc_id)
            session.commit()
        except Exception as e:
            session.rollback()
            logger.error("SQLite 清理失败: %s", e)
            sql_ok = False
        finally:
            session.close()

        if not sql_ok:
            return JSONResponse(status_code=500,
                content={"success": False, "message": "数据库清理失败，文件未删除"})

        # 6. ChromaDB 删向量
        _chroma_db.delete_by_doc_id(doc_id)

        # 7. 删除物理文件
        os.remove(file_path)
        meta_path = file_path + ".meta.json"
        if os.path.exists(meta_path):
            os.remove(meta_path)
        logger.info("已删除文件: %s (doc_id=%s)", file_path, doc_id)

        # 8. 更新内存状态
        async with _state_lock:
            _imported_files = [f for f in _imported_files if f["name"] != filename]
            if not _imported_files:
                _vector_ready = False

        return {"success": True, "message": f"已删除 '{filename}'"}
    except Exception as e:
        logger.error("删除失败: %s", e)
        return JSONResponse(status_code=500, content={"success": False, "message": str(e)})


@app.get("/uploaded-files")
async def uploaded_files():
    """获取已导入文件列表（以 SQLite 为准，合并内存中的文件大小）。"""
    from database import get_session  # noqa: F811
    from database.operations import DocOps

    # 1. 从 SQLite 查询所有文档
    db_files = []
    try:
        session = get_session()
        try:
            for d in DocOps.get_all_documents(session):
                db_files.append({
                    "name": d.file_name,
                    "type": d.doc_type,
                    "chunks": d.chunk_count,
                    "time": d.upload_time.strftime("%Y-%m-%d %H:%M:%S") if d.upload_time else "",
                    "doc_id": d.id,
                    "status": d.status or "",
                })
        finally:
            session.close()
    except Exception:
        logger.warning("无法查询 SQLite 文档列表", exc_info=True)

    # 2. 内存中补充文件大小（只有物理文件存在时才有）
    mem_by_name = {f["name"]: f for f in _imported_files}
    merged = []
    seen = set()
    for d in db_files:
        mem = mem_by_name.get(d["name"], {})
        d["size"] = mem.get("size", "—")
        merged.append(d)
        seen.add(d["name"])
    # 补充内存中有但 DB 中没有的（刚上传尚未入库完成的）
    for f in _imported_files:
        if f["name"] not in seen:
            merged.append({**f, "doc_id": "", "status": ""})

    return {"files": merged, "vector_ready": _vector_ready}


@app.post("/chat")
async def chat(user_input: str = Form(...), background_tasks: BackgroundTasks = None):
    """接收用户需求 -> 立即返回 task_id，后台异步生成测试计划。"""
    if not _vector_ready:
        return JSONResponse(
            status_code=400,
            content={"success": False, "message": "请先上传 PDF 文档"},
        )

    task_id = await _create_task()
    background_tasks.add_task(_run_chat_bg, task_id, user_input)

    return {"success": True, "task_id": task_id, "message": "任务已提交，后台处理中"}


@app.get("/task/{task_id}")
async def get_task_status(task_id: str):
    """轮询查询后台任务进度。"""
    async with _task_store_lock:
        task = _task_store.get(task_id)
    if not task:
        return JSONResponse(status_code=404, content={"success": False, "message": "任务不存在或已过期"})
    return {"success": True, "task": task}


@app.post("/update-module")
async def audit_module(data: dict):
    """审核确认/修改模块关联关系（已迁移到 SQLite）。"""
    from database import get_session  # noqa: F811 (already imported at top)
    doc_id = data.get("doc_id")
    module_name = data.get("module_name")
    related_modules = data.get("related_modules", [])
    if not doc_id:
        return JSONResponse(status_code=400, content={"success": False, "message": "缺少 doc_id"})
    session = get_session()
    try:
        doc = DocOps.get_document(session, doc_id)
        doc_type = doc.doc_type if doc else "product"
        # 清理旧的 doc↔doc 级联 + doc↔module 绑定
        _cleanup_doc_to_doc_bindings(session, doc_id)
        for b in BindingOps.get_bindings(session, doc_type, doc_id):
            if b.left_type == "module" or b.right_type == "module":
                BindingOps.unbind(session, b.id)
        # 绑定新模块 + 重建级联
        from ingest_v2 import _cascade_bind_to_module_docs
        if module_name:
            BindingOps.bind(session, doc_type, doc_id, "module", module_name)
            _cascade_bind_to_module_docs(session, doc_type, doc_id, module_name)
        for rmod in related_modules:
            if rmod != module_name:
                BindingOps.bind(session, doc_type, doc_id, "module", rmod)
                _cascade_bind_to_module_docs(session, doc_type, doc_id, rmod)
        session.commit()
        return {"success": True, "message": f"模块信息已更新: {module_name}"}
    except Exception as e:
        session.rollback()
        return JSONResponse(status_code=500, content={"success": False, "message": str(e)})
    finally:
        session.close()


# ==================== 模块目录树 API ====================

@app.get("/api/modules")
async def get_modules():
    """获取模块树。"""
    from agent_components.module_tree import get_tree
    return {"success": True, "tree": get_tree()}


@app.post("/api/modules")
async def create_module(data: dict):
    """创建模块。"""
    from agent_components.module_tree import create
    name = data.get("name", "").strip()
    parent_id = data.get("parent_id", "root")
    if not name:
        return JSONResponse(status_code=400, content={"success": False, "message": "模块名不能为空"})
    module = create(name, parent_id)
    return {"success": True, "module": module}


@app.put("/api/modules/{module_id}")
async def update_module(module_id: str, data: dict):
    """更新模块（重命名 / 移动）。"""
    from agent_components.module_tree import rename, get_by_id
    if "name" in data:
        ok, msg = rename(module_id, data["name"])
        if ok:
            return {"success": True, "message": msg}
        return JSONResponse(status_code=400, content={"success": False, "message": msg})
    return JSONResponse(status_code=400, content={"success": False, "message": "缺少 name 参数"})


@app.delete("/api/modules/{module_id}")
async def delete_module(module_id: str):
    """删除模块。"""
    from agent_components.module_tree import delete
    try:
        delete(module_id)
        return {"success": True, "message": "已删除"}
    except ValueError as e:
        return JSONResponse(status_code=400, content={"success": False, "message": str(e)})


@app.post("/api/modules/merge")
async def merge_modules(data: dict):
    """合并模块。"""
    from agent_components.module_tree import merge
    source = data.get("source_id")
    target = data.get("target_id")
    try:
        ok, msg = merge(source, target)
        if ok:
            return {"success": True, "message": msg}
        return JSONResponse(status_code=400, content={"success": False, "message": msg})
    except ValueError as e:
        return JSONResponse(status_code=400, content={"success": False, "message": str(e)})


@app.get("/api/modules/{module_name}/docs")
async def get_module_docs(module_name: str):
    """获取模块关联的所有文档和接口。"""
    from database import get_session  # noqa: F811 (already imported at top)
    session = get_session()
    try:
        docs = BindingOps.get_bound_docs(session, module_name)
        chroma = _chroma_db
        result = []
        for d in docs:
            item = {
                "doc_id": d.id, "module": module_name,
                "doc_type": d.doc_type, "type": d.doc_type,
                "chunks": d.chunk_count, "file_name": d.file_name,
            }
            if d.doc_type == "api":
                apis = chroma.get_doc_apis(d.id)
                item["api_count"] = len(apis)
                item["api_names"] = [a["api_name"] for a in apis if a.get("api_name")]
            result.append(item)
        return {"success": True, "docs": result}
    except Exception as e:
        return JSONResponse(status_code=500, content={"success": False, "message": str(e)})
    finally:
        session.close()


@app.get("/api/docs/{doc_id}/apis")
async def get_doc_apis(doc_id: str):
    """获取文档下的所有接口定义。"""
    try:
        db = _chroma_db
        apis = db.get_doc_apis(doc_id)
        return {"success": True, "apis": apis}
    except Exception as e:
        return JSONResponse(status_code=500, content={"success": False, "message": str(e)})


@app.post("/api/docs/change-module")
async def change_doc_module(data: dict):
    """将文档迁移到另一个模块（改写 SQLite bindings）。"""
    from database import get_session  # noqa: F811 (already imported at top)
    doc_id = data.get("doc_id", "")
    new_module = data.get("module", "")
    if not doc_id or not new_module:
        return JSONResponse(status_code=400, content={"success": False, "message": "缺少 doc_id 或 module"})
    session = get_session()
    try:
        doc = DocOps.get_document(session, doc_id)
        doc_type = doc.doc_type if doc else "product"
        # 删除旧的模块绑定（过滤查询）
        _cleanup_doc_to_doc_bindings(session, doc_id)
        for b in BindingOps.get_bindings(session, doc_type, doc_id):
            if b.left_type == "module" or b.right_type == "module":
                BindingOps.unbind(session, b.id)
        # 绑定到新模块 + 重建级联
        BindingOps.bind(session, doc_type, doc_id, "module", new_module)
        from ingest_v2 import _cascade_bind_to_module_docs
        _cascade_bind_to_module_docs(session, doc_type, doc_id, new_module)
        session.commit()
        return {"success": True, "message": f"已迁移到 {new_module}"}
    except Exception as e:
        session.rollback()
        return JSONResponse(status_code=500, content={"success": False, "message": str(e)})
    finally:
        session.close()


@app.get("/api/docs/unassociated")
async def get_unassociated_docs():
    """获取所有未关联模块的文档。"""
    from database import get_session
    from database.operations import DocOps
    session = get_session()
    try:
        docs = DocOps.get_unassociated_docs(session)
        return {"success": True, "docs": [
            {"doc_id": d.id, "module": "", "type": d.doc_type, "chunks": d.chunk_count, "file_name": d.file_name}
            for d in docs
        ]}
    except Exception as e:
        return JSONResponse(status_code=500, content={"success": False, "message": str(e)})
    finally:
        session.close()


@app.post("/api/docs/disassociate")
async def disassociate_doc(data: dict):
    """解除文档的模块关联（删除 SQLite bindings 表中该文档的模块绑定）。"""
    from database import get_session
    from database.operations import BindingOps
    doc_id = data.get("doc_id", "")
    if not doc_id:
        return JSONResponse(status_code=400, content={"success": False, "message": "缺少 doc_id"})
    session = get_session()
    try:
        from database.models import Binding  # noqa: F811, Document
        doc = session.get(Document, doc_id)
        doc_type = doc.doc_type if doc else "product"
        # 清理旧 doc↔doc 级联 + doc↔module 绑定
        _cleanup_doc_to_doc_bindings(session, doc_id)
        for b in BindingOps.get_bindings(session, doc_type, doc_id):
            if b.left_type == "module" or b.right_type == "module":
                BindingOps.unbind(session, b.id)
        session.commit()
        return {"success": True, "message": "已解除关联"}
    except Exception as e:
        session.rollback()
        return JSONResponse(status_code=500, content={"success": False, "message": str(e)})
    finally:
        session.close()


# ==================== 术语表 API ====================

@app.get("/api/modules/{module_name}/glossary")
async def get_glossary(module_name: str):
    """获取模块术语表。"""
    from agent_components.module_tree import get_glossary
    return {"success": True, "terms": get_glossary(module_name)}


@app.post("/api/modules/{module_name}/glossary")
async def add_glossary_term(module_name: str, data: dict):
    """添加/更新模块术语。"""
    from agent_components.module_tree import add_glossary_term
    term = data.get("term", "").strip()
    definition = data.get("definition", "").strip()
    notes = data.get("notes", "").strip()
    if not term:
        return JSONResponse(status_code=400, content={"success": False, "message": "术语名不能为空"})
    ok = add_glossary_term(module_name, term, definition, notes)
    if ok:
        return {"success": True, "message": f"已保存: {term}"}
    return JSONResponse(status_code=404, content={"success": False, "message": "模块不存在"})


@app.delete("/api/modules/{module_name}/glossary/{term}")
async def delete_glossary_term(module_name: str, term: str):
    """删除模块术语。"""
    from agent_components.module_tree import delete_glossary_term
    from urllib.parse import unquote
    ok = delete_glossary_term(module_name, unquote(term))
    if ok:
        return {"success": True, "message": f"已删除: {term}"}
    return JSONResponse(status_code=404, content={"success": False, "message": "模块或术语不存在"})


@app.post("/open-file")
async def open_file(file_path: str = Form(...)):
    """打开本地文件（调用系统默认应用）"""
    import os as _os
    try:
        _os.startfile(file_path)
        return {"success": True}
    except Exception as e:
        return JSONResponse(status_code=500, content={"success": False, "message": str(e)})


# ========================================================================
# 新增: 接口文档拆分 + 关联管理 + 文档内容
# ========================================================================

@app.post("/api/upload/extract-api")
async def extract_api_doc(file: UploadFile = File(...), module: str = Form("")):
    """上传接口 MD → LLM 提取接口列表 → 返回（不入库）"""
    from ingest_v2 import process_api_doc_extract
    file_path = os.path.join("uploads", "md", file.filename)
    os.makedirs(os.path.dirname(file_path), exist_ok=True)
    with open(file_path, "wb") as f:
        f.write(await file.read())
    try:
        result = process_api_doc_extract(file_path, default_module=module or None)
        # 保存原文件路径，后续 commit/retry 时需要
        result["file_path"] = file_path
        return {"success": True, **result}
    except Exception as e:
        return JSONResponse(status_code=500, content={"success": False, "message": str(e)})


@app.post("/api/upload/commit-api")
async def commit_api_docs(data: dict):
    """用户确认后，每个接口独立入库。仅当全部接口选中时才废弃原文件。"""
    from ingest_v2 import commit_api_docs
    file_path = data.get("file_path", "")
    module_name = data.get("module_name", "")
    apis = data.get("apis", [])
    all_selected = data.get("all_selected", False)
    if not file_path or not apis:
        return JSONResponse(status_code=400,
                            content={"success": False, "message": "缺少必要参数"})
    # 路径安全校验：必须在 uploads/ 目录下
    abs_path = os.path.abspath(file_path)
    uploads_root = os.path.abspath("uploads")
    if not abs_path.startswith(uploads_root):
        return JSONResponse(status_code=403,
                            content={"success": False, "message": "非法路径"})
    try:
        result = commit_api_docs(file_path, module_name, apis, delete_original=all_selected)
        # 更新内存状态：接口入库后刷新 _imported_files 和 _vector_ready
        global _vector_ready, _imported_files
        async with _state_lock:
            _vector_ready = True
            now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            for api in apis:
                _imported_files.insert(0, {
                    "name": f"{api.get('method', '?')} {api.get('url', '')}",
                    "size": "—",
                    "chunks": 1,
                    "time": now_str,
                    "type": "api",
                })
        return {"success": True, **result}
    except Exception as e:
        return JSONResponse(status_code=500, content={"success": False, "message": str(e)})


@app.post("/api/upload/retry-api")
async def retry_api_extract(data: dict):
    """用户拒绝拆分结果 → 重新 LLM 提取"""
    from ingest_v2 import process_api_doc_extract
    file_path = data.get("file_path", "")
    module = data.get("module_name", "")
    if not file_path:
        return JSONResponse(status_code=400,
                            content={"success": False, "message": "缺少 file_path"})
    try:
        result = process_api_doc_extract(file_path, default_module=module or None)
        result["file_path"] = file_path
        return {"success": True, **result}
    except Exception as e:
        return JSONResponse(status_code=500, content={"success": False, "message": str(e)})


@app.post("/api/bindings")
async def create_binding(data: dict):
    """创建绑定（含级联：文档绑模块时自动关联同模块异类文档）"""
    from database import get_session
    from database.operations import BindingOps
    from ingest_v2 import _cascade_bind_to_module_docs
    st, si = data.get("source_type"), data.get("source_id")
    tt, ti = data.get("target_type"), data.get("target_id")
    if not all([st, si, tt, ti]):
        return JSONResponse(status_code=400,
                            content={"success": False, "message": "缺少参数"})
    session = get_session()
    try:
        ok, msg = BindingOps.bind(session, st, si, tt, ti)
        if not ok:
            return {"success": False, "message": msg}
        # 级联：如果是文档↔模块，自动关联同模块异类文档
        doc_types = ("product", "api", "axure")
        if st in doc_types and tt == "module":
            _cascade_bind_to_module_docs(session, st, si, ti)
        elif tt in doc_types and st == "module":
            _cascade_bind_to_module_docs(session, tt, ti, si)
        session.commit()
        return {"success": True, "message": "绑定成功"}
    except Exception as e:
        session.rollback()
        return JSONResponse(status_code=500, content={"success": False, "message": str(e)})
    finally:
        session.close()


@app.delete("/api/bindings")
async def delete_binding(data: dict):
    """解除绑定"""
    from database import get_session
    from database.operations import BindingOps
    a_type, a_id = data.get("a_type"), data.get("a_id")
    b_type, b_id = data.get("b_type"), data.get("b_id")
    if not all([a_type, a_id, b_type, b_id]):
        return JSONResponse(status_code=400,
                            content={"success": False, "message": "缺少参数"})
    session = get_session()
    try:
        ok = BindingOps.unbind_by_pair(session, a_type, a_id, b_type, b_id)
        session.commit()
        return {"success": ok, "message": "已解除" if ok else "绑定不存在"}
    except Exception as e:
        session.rollback()
        return JSONResponse(status_code=500, content={"success": False, "message": str(e)})
    finally:
        session.close()


@app.get("/api/bindings")
async def get_bindings(entity_type: str = "", entity_id: str = ""):
    """查询实体的所有关联"""
    from database import get_session
    from database.operations import BindingOps
    session = get_session()
    try:
        bindings = BindingOps.get_bindings(
            session,
            entity_type=entity_type or None,
            entity_id=entity_id or None,
        )
        result = []
        for b in bindings:
            result.append({
                "left_type": b.left_type, "left_id": b.left_id,
                "right_type": b.right_type, "right_id": b.right_id,
            })
        return {"success": True, "bindings": result}
    finally:
        session.close()


@app.get("/api/docs/{doc_id}/chunks")
async def get_doc_chunks(doc_id: str):
    """获取文档的文本块内容（向量化前原文）"""
    try:
        db = _chroma_db
        chunks = db.get_doc_chunks(doc_id)
        return {"success": True, "chunks": chunks}
    except Exception as e:
        return JSONResponse(status_code=500, content={"success": False, "message": str(e)})


@app.get("/api/docs/{doc_id}/glossary")
async def get_doc_glossary(doc_id: str):
    """获取文档的术语表"""
    from agent_components.module_tree import get_glossary_by_doc
    terms = get_glossary_by_doc(doc_id)
    return {"success": True, "terms": terms}


@app.post("/api/docs/{doc_id}/glossary")
async def add_doc_glossary(doc_id: str, data: dict):
    """添加文档术语"""
    from database import get_session
    from database.operations import GlossaryOps
    term = data.get("term", "").strip()
    definition = data.get("definition", "").strip()
    if not term:
        return JSONResponse(status_code=400,
                            content={"success": False, "message": "术语名不能为空"})
    session = get_session()
    try:
        # 幂等：先删同名再插入
        for t in GlossaryOps.get_terms(session, doc_id):
            if t.term == term:
                GlossaryOps.delete_term(session, t.id)
        GlossaryOps.add_term(session, doc_id, term, definition)
        session.commit()
        return {"success": True, "message": f"已保存: {term}"}
    except Exception as e:
        session.rollback()
        return JSONResponse(status_code=500, content={"success": False, "message": str(e)})
    finally:
        session.close()


@app.delete("/api/docs/{doc_id}/glossary/{term_id}")
async def delete_doc_glossary(doc_id: str, term_id: str):
    """删除文档术语"""
    from database import get_session
    from database.operations import GlossaryOps
    session = get_session()
    try:
        ok = GlossaryOps.delete_term(session, int(term_id))
        session.commit()
        return {"success": ok, "message": "已删除" if ok else "术语不存在"}
    except Exception as e:
        session.rollback()
        return JSONResponse(status_code=500, content={"success": False, "message": str(e)})
    finally:
        session.close()


@app.get("/api/modules/{module_name}/related")
async def get_module_related(module_name: str):
    """获取模块的关联模块（module↔module）"""
    from database import get_session
    from database.operations import BindingOps
    session = get_session()
    try:
        partners = BindingOps.get_partners(session, "module", module_name, "module")
        return {"success": True, "related": [{"name": p[1]} for p in partners]}
    finally:
        session.close()


@app.get("/api/docs/{doc_id}/related-docs")
async def get_doc_related_docs(doc_id: str):
    """获取文档的关联文档（doc↔doc）"""
    from database import get_session
    from database.operations import BindingOps
    session = get_session()
    try:
        from database.models import Document as DocModel
        doc = session.get(DocModel, doc_id)
        if not doc:
            return {"success": True, "related": []}
        doc_types = ("product", "api", "axure")
        partners = BindingOps.get_partners(session, doc.doc_type, doc_id)
        related = []
        for pt, pi in partners:
            if pt in doc_types:
                related_doc = session.get(DocModel, pi)
                related.append({
                    "doc_id": pi,
                    "doc_type": pt,
                    "file_name": related_doc.file_name if related_doc else pi,
                })
        return {"success": True, "related": related}
    finally:
        session.close()


@app.get("/api/file-content")
async def get_file_content(path: str = ""):
    """读取文件内容（供前端查看/编辑）"""
    import os as _os
    if not path or not _os.path.exists(path):
        return JSONResponse(status_code=404, content={"success": False, "message": "文件不存在"})
    try:
        # 安全检查：只允许读取 testcase_out / uploads 目录下的文件
        allowed_dirs = [
            _os.path.abspath(config.TESTCASE_BASE),
            _os.path.abspath("uploads"),
        ]
        abs_path = _os.path.abspath(path)
        if not any(abs_path.startswith(d) for d in allowed_dirs):
            return JSONResponse(status_code=403, content={"success": False, "message": "无权访问该路径"})

        ext = _os.path.splitext(path)[1].lower()
        if ext in (".xlsx", ".zip", ".png", ".jpg"):
            return {"success": True, "binary": True, "message": "二进制文件，请使用打开编辑"}

        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
        return {"success": True, "content": content, "path": path, "ext": ext}
    except UnicodeDecodeError:
        return {"success": True, "binary": True, "message": "二进制文件，请使用打开编辑"}
    except Exception as e:
        return JSONResponse(status_code=500, content={"success": False, "message": str(e)})


@app.post("/api/file-save")
async def save_file_content(data: dict):
    """保存修改后的文件内容"""
    import os as _os
    path = data.get("path", "")
    content = data.get("content", "")
    if not path:
        return JSONResponse(status_code=400, content={"success": False, "message": "缺少文件路径"})
    # 安全检查
    allowed_dirs = [
        _os.path.abspath(config.TESTCASE_BASE),
        _os.path.abspath("uploads"),
    ]
    abs_path = _os.path.abspath(path)
    if not any(abs_path.startswith(d) for d in allowed_dirs):
        return JSONResponse(status_code=403, content={"success": False, "message": "无权写入该路径"})
    try:
        _os.makedirs(_os.path.dirname(abs_path), exist_ok=True)
        with open(abs_path, "w", encoding="utf-8") as f:
            f.write(content)
        return {"success": True, "message": "保存成功"}
    except Exception as e:
        return JSONResponse(status_code=500, content={"success": False, "message": str(e)})


@app.post("/confirm-plan")
async def confirm_plan(excel_path: str = Form(None), background_tasks: BackgroundTasks = None):
    """确认测试计划 -> 立即返回 task_id，后台异步生成 .py 和 .yaml 文件。"""
    logger.info(">>> 测试计划已确认，开始生成测试文件...")

    # 优先使用前端传入的路径
    if not excel_path:
        import glob
        excel_files = glob.glob(os.path.join(config.TESTCASE_BASE, "**", "test_plan.xlsx"), recursive=True)
        if excel_files:
            excel_path = max(excel_files, key=os.path.getmtime)

    if not excel_path:
        return JSONResponse(
            status_code=400,
            content={"success": False, "message": "未找到测试计划 Excel 文件"},
        )

    if not _components:
        return JSONResponse(
            status_code=500,
            content={"success": False, "message": "组件未初始化"},
        )

    task_id = await _create_task()
    background_tasks.add_task(_confirm_plan_bg, task_id, excel_path)

    return {"success": True, "task_id": task_id, "message": "确认计划已提交，后台生成中"}


# ----------------------------------------------------------------
# 启动
# ----------------------------------------------------------------
if __name__ == "__main__":
    import threading

    local_url = f"http://{config.WEB_HOST}:{config.WEB_PORT}"

    # 在子线程中启动 uvicorn
    server_config = uvicorn.Config(app, host=config.WEB_HOST, port=config.WEB_PORT)
    server = uvicorn.Server(server_config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    # 输出访问提示
    logger.info("\n🌐 本地访问地址: %s", local_url)
    logger.info("   如果 0.0.0.0 无法访问，尝试: http://localhost:%d", config.WEB_PORT)
    logger.info("\n💡 输入 q 并回车可停止服务\n")

    # 监听键盘输入 "q" 停止服务
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
