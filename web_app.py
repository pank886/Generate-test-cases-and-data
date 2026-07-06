#!/usr/bin/env python3
"""Web 入口：智能测试助手 Web 版（FastAPI + BackgroundTasks）"""
import asyncio
import json
import os
import sys
import uuid
from pathlib import Path

# 强制 UTF-8 编码，防止 Windows 终端打印 emoji 时报 GBK 错误
sys.stdout.reconfigure(encoding="utf-8")

import uvicorn
from fastapi import FastAPI, UploadFile, File, Form, Request, BackgroundTasks
from fastapi.responses import HTMLResponse, JSONResponse
from jinja2 import Environment, FileSystemLoader, select_autoescape

import config
from observability import get_logger, init_logging, set_trace_id, generate_trace_id
from agent_components.chromadb_file import ensure_directory, ReadersChromadb
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
app = FastAPI(title="智能测试助手", version="0.2")

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

# 并发保护锁
_state_lock = asyncio.Lock()

# 后台任务状态追踪 {task_id: {status, progress, message, result, error}}
_task_store: dict = {}
_task_store_lock = asyncio.Lock()

TASK_TTL_SECONDS = 3600  # 任务状态保留 1 小时


# ----------------------------------------------------------------
# trace_id 中间件
# ----------------------------------------------------------------
@app.middleware("http")
async def trace_middleware(request: Request, call_next):
    """为每个 HTTP 请求生成 trace_id，注入到日志上下文。"""
    tid = request.headers.get("X-Trace-Id", generate_trace_id())
    set_trace_id(tid)
    response = await call_next(request)
    response.headers["X-Trace-Id"] = tid
    return response


# ----------------------------------------------------------------
# 生命周期
# ----------------------------------------------------------------
@app.on_event("startup")
async def startup():
    global _chat_func, _components, _vector_ready, _imported_files
    logger.info(">>> 启动智能测试助手 Web 服务 ...")
    _chat_func = build_and_run_agent()
    _components = _chat_func.components  # 保存实例用于后续生成

    # 扫描 uploads/ 下各类型子目录，恢复已导入文件列表
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
                        import json as _json
                        with open(meta_path, "r", encoding="utf-8") as _mf:
                            _meta = _json.load(_mf)
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

    # 判断向量库是否已就绪
    chroma_path = Path(config.CHROMA_DB_DIR)
    if chroma_path.exists() and any(chroma_path.iterdir()):
        _vector_ready = True
        logger.info("   ✅ 向量库已就绪 (%d 个文件)", len(_imported_files))
    else:
        logger.info("   ℹ️ 向量库为空，请上传 API 文档")


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
            from ingest_v2 import process_api_doc
            _progress(10, "读取 Markdown，提取接口定义...")
            result = process_api_doc(file_path, progress_cb=lambda p, m: _progress(10 + int(p * 0.8), m))
            count = result.get("api_count", 0)
            source = "API 文档"
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
    return JSONResponse(content={}, status_code=204)


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
    """删除已上传的文件及其向量库数据"""
    global _vector_ready, _imported_files

    # 1. 在所有上传目录中查找文件
    file_path = None
    for scan_dir in ["uploads/pdf", "uploads/md", "uploads/docx", "uploads/axure", "uploads"]:
        candidate = os.path.join(scan_dir, filename)
        if os.path.exists(candidate):
            file_path = os.path.abspath(candidate)
            break

    if not file_path:
        return JSONResponse(
            status_code=404,
            content={"success": False, "message": f"文件 '{filename}' 不存在"},
        )

    try:
        # 2. 从 ChromaDB 中删除
        db_client = ReadersChromadb(
            persist_directory=config.CHROMA_DB_DIR,
            collection_name=config.CHROMA_COLLECTION,
        )
        source_path = os.path.normpath(file_path)
        deleted_count = db_client.vector_store.delete(where={"source": source_path})
        logger.info("🗑️ 从向量库删除了 %s 个文本块 (source=%s)", deleted_count or 0, source_path)

        # 3. 删除物理文件及元数据
        os.remove(file_path)
        meta_path = file_path + ".meta.json"
        if os.path.exists(meta_path):
            os.remove(meta_path)
        logger.info("🗑️ 已删除文件: %s", file_path)

        # 3b. 同步删除关联术语
        try:
            from agent_components.module_tree import delete_glossary_by_doc
            delete_glossary_by_doc(filename)
        except Exception:
            pass

        # 4. 更新内存状态
        async with _state_lock:
            _imported_files = [f for f in _imported_files if f["name"] != filename]
            if not _imported_files:
                _vector_ready = False

        return {"success": True, "message": f"已删除 '{filename}' 及对应的向量数据"}
    except Exception as e:
        logger.error("❌ 删除失败: %s", e)
        return JSONResponse(
            status_code=500,
            content={"success": False, "message": f"删除失败: {str(e)}"},
        )


@app.get("/uploaded-files")
async def uploaded_files():
    """获取已导入文件列表"""
    return {"files": _imported_files, "vector_ready": _vector_ready}


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
    """审核确认/修改模块关联关系"""
    try:
        doc_id = data.get("doc_id")
        module_name = data.get("module_name")
        related_modules = data.get("related_modules", [])
        if not doc_id:
            return JSONResponse(status_code=400, content={"success": False, "message": "缺少 doc_id"})

        from agent_components.dual_chroma import DualChromaDB
        db = DualChromaDB()
        db.product_store.delete(where={"doc_id": doc_id})
        logger.info("   [Audit] doc_id=%s 更新为 module=%s, related=%s", doc_id, module_name, related_modules)
        return {"success": True, "message": f"模块信息已更新: {module_name}"}
    except Exception as e:
        return JSONResponse(status_code=500, content={"success": False, "message": str(e)})


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
        result = rename(module_id, data["name"])
        if result:
            return {"success": True, "message": f'{result["old_name"]} → {result["new_name"]}'}
    return JSONResponse(status_code=404, content={"success": False, "message": "模块不存在"})


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
        merge(source, target)
        return {"success": True, "message": "合并完成"}
    except ValueError as e:
        return JSONResponse(status_code=400, content={"success": False, "message": str(e)})


@app.get("/api/modules/{module_name}/docs")
async def get_module_docs(module_name: str):
    """获取模块关联的所有文档和接口。"""
    from agent_components.dual_chroma import DualChromaDB
    try:
        db = DualChromaDB()
        docs = db.get_module_docs(module_name)
        return {"success": True, "docs": docs}
    except Exception as e:
        return JSONResponse(status_code=500, content={"success": False, "message": str(e)})


@app.get("/api/docs/{doc_id}/apis")
async def get_doc_apis(doc_id: str):
    """获取文档下的所有接口定义。"""
    from agent_components.dual_chroma import DualChromaDB
    try:
        db = DualChromaDB()
        apis = db.get_doc_apis(doc_id)
        return {"success": True, "apis": apis}
    except Exception as e:
        return JSONResponse(status_code=500, content={"success": False, "message": str(e)})


@app.post("/api/docs/change-module")
async def change_doc_module(data: dict):
    """将文档迁移到另一个模块。"""
    from agent_components.dual_chroma import DualChromaDB
    doc_id = data.get("doc_id", "")
    new_module = data.get("module", "")
    if not doc_id or not new_module:
        return JSONResponse(status_code=400, content={"success": False, "message": "缺少 doc_id 或 module"})
    try:
        db = DualChromaDB()
        db.update_doc_module(doc_id, new_module)
        return {"success": True, "message": f"已迁移到 {new_module}"}
    except Exception as e:
        return JSONResponse(status_code=500, content={"success": False, "message": str(e)})


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
