"""文件管理路由：上传、删除、列表、查看/编辑。"""

import os as _os
import time as _time

import config
from fastapi import APIRouter, UploadFile, File, Form, BackgroundTasks
from fastapi.responses import JSONResponse

from observability import get_logger

logger = get_logger(__name__)


def _win_remove(path: str, max_retries: int = 3):
    """Windows 安全删除（防 Defender 锁定重试）。PermissionError 时等 0.5s 重试。"""
    for attempt in range(max_retries):
        try:
            _os.remove(path)
            return
        except PermissionError:
            if attempt < max_retries - 1:
                _time.sleep(0.5)
            else:
                raise


router = APIRouter(prefix="/api/files", tags=["files"])


@router.post("/upload-file")
async def upload_file(file: UploadFile = File(...),
                       background_tasks: BackgroundTasks = None):
    """上传文件 → 立即返回 task_id，后台异步处理。"""
    from web.app import _chroma_db, _create_task

    raw_filename = file.filename
    if not raw_filename:
        return JSONResponse(status_code=400,
                            content={"success": False, "message": "文件名不能为空"})
    # 防路径遍历：只取纯文件名
    filename = _os.path.basename(raw_filename)
    ext = _os.path.splitext(filename)[1].lower()
    type_map = {".pdf": "product", ".md": "md", ".docx": "product", ".zip": "axure", ".yml": "md", ".yaml": "md"}
    file_type = type_map.get(ext)
    if file_type is None:
        supported = ", ".join(type_map.keys())
        return JSONResponse(status_code=400,
                            content={"success": False,
                                     "message": f"不支持的文件类型: {ext}。当前支持: {supported}"})

    type_dir_name = "md" if ext == ".md" else file_type
    type_dir = _os.path.join(config.BASE_DIR, "uploads", type_dir_name)
    _os.makedirs(type_dir, exist_ok=True)
    file_path = _os.path.join(type_dir, filename)

    # 同名文件覆盖：先清理旧数据
    if _os.path.exists(file_path):
        try:
            from database import get_session_ctx
            from database.models import Document
            from database.operations import BindingOps, DocOps
            with get_session_ctx() as old_session:
                old_doc = old_session.query(Document).filter(
                    Document.file_name == filename).first()
                if old_doc:
                    BindingOps.delete_bindings_for_doc(old_session, old_doc.id)
                    DocOps.delete_document(old_session, old_doc.id)
                    _chroma_db.delete_by_doc_id(old_doc.id)
        except Exception as e:
            logger.error("旧数据清理失败，中止上传: %s", e, exc_info=True)
            return JSONResponse(status_code=500,
                                content={"success": False,
                                         "message": f"旧数据清理失败: {e}"})

    MAX_UPLOAD_SIZE = config.UPLOAD_MAX_SIZE

    total_size = 0
    with open(file_path, "wb") as f:
        while True:
            chunk = await file.read(8192)
            if not chunk:
                break
            total_size += len(chunk)
            if total_size > MAX_UPLOAD_SIZE:
                f.close()
                try:
                    _win_remove(file_path)
                except OSError:
                    logger.warning("上传超限文件删除失败（Windows 可能被锁定）: %s", file_path)
                return JSONResponse(status_code=413,
                                    content={"success": False,
                                             "message": f"文件过大（超过 {config.UPLOAD_MAX_SIZE // (1024*1024)}MB），上传已中断"})
            f.write(chunk)

    task_id = await _create_task()
    from web.tasks import _process_file_bg
    background_tasks.add_task(
        _process_file_bg, task_id, file_path, ext,
        total_size, filename, file_type,
    )
    return {"success": True, "task_id": task_id,
            "message": "文件已接收，后台处理中"}


@router.post("/delete-file")
async def delete_file(filename: str = Form(...)):
    """删除文件：清理 SQLite + ChromaDB + 物理文件 + 内存状态。

    文件不存在于磁盘时仍会清理数据库和内存记录。
    """
    from web.app import _get_imported_files, _remove_imported_file, _chroma_db

    # 防路径遍历：只取纯文件名
    safe_filename = _os.path.basename(filename)
    # 查找物理文件（不存在也不阻断流程，仅跳过磁盘删除）
    file_path = None
    for scan_dir_raw in ["uploads/pdf", "uploads/md", "uploads/docx",
                      "uploads/axure", "uploads/product", "uploads"]:
        candidate = _os.path.join(config.BASE_DIR, scan_dir_raw, safe_filename)
        if _os.path.exists(candidate):
            file_path = candidate
            break

    try:
        from database import get_session_ctx
        from database.models import Document
        from database.operations import BindingOps, DocOps
        from web.app import _cleanup_doc_to_doc_bindings

        with get_session_ctx() as session:
            doc = session.query(Document).filter(
                Document.file_name == filename).first()
            doc_id = doc.id if doc else None

        if not doc:
            # SQLite 无记录，仍须尝试清理 ChromaDB + .meta.json（防孤儿向量数据）
            if file_path:
                meta_path = file_path + ".meta.json"
                _doc_id = None
                if _os.path.exists(meta_path):
                    try:
                        import json as _json
                        with open(meta_path, "r", encoding="utf-8") as _mf:
                            _doc_id = _json.load(_mf).get("doc_id")
                    except Exception:
                        logger.warning("读取 meta.json 失败，跳过 ChromaDB 孤儿清理: %s", meta_path, exc_info=True)
                if _doc_id and _chroma_db is not None:
                    try:
                        _chroma_db.delete_by_doc_id(_doc_id)
                        logger.info("已清理 ChromaDB 孤儿数据: doc_id=%s (来自 meta.json)", _doc_id)
                    except Exception:
                        logger.warning("ChromaDB 孤儿数据清理失败: doc_id=%s", _doc_id, exc_info=True)
                # 物理文件 + meta.json
                try:
                    _win_remove(file_path)
                except (FileNotFoundError, PermissionError):
                    pass
                if _os.path.exists(meta_path):
                    try: _win_remove(meta_path)
                    except (FileNotFoundError, PermissionError): pass
            await _remove_imported_file(filename)
            return {"success": True,
                    "message": f"已删除 '{filename}'"}

        # 清理 SQLite
        try:
            with get_session_ctx() as session:
                _cleanup_doc_to_doc_bindings(session, doc_id)
                BindingOps.delete_bindings_for_doc(session, doc_id)
                DocOps.delete_document(session, doc_id)
        except Exception as e:
            from observability import get_logger
            get_logger(__name__).error("SQLite 清理失败: %s", e)
            return JSONResponse(status_code=500,
                                content={"success": False,
                                         "message": "数据库清理失败，文件未删除"})

        # 清理 ChromaDB（不可用时创建延迟重试任务）
        if _chroma_db is not None:
            _chroma_db.delete_by_doc_id(doc_id)
        else:
            import asyncio
            async def _retry_chroma_delete(did: str):
                await asyncio.sleep(config.CHROMA_RETRY_DELAY)
                try:
                    from agent_components.dual_chroma import get_chroma_db
                    db = get_chroma_db()
                    db.delete_by_doc_id(did)
                    print(f"[delete-file] ChromaDB 延迟删除成功: {did}")
                except Exception as e:
                    print(f"[delete-file] ❌ ChromaDB 延迟删除失败（Ollama 可能未启动）: {did} - {e}")
                    logger.error("ChromaDB 延迟删除失败: %s - %s", did, e)
            asyncio.create_task(_retry_chroma_delete(doc_id))
            print(f"[delete-file] ChromaDB 不可用，5 分钟后将尝试延迟删除: {doc_id}")
            logger.info("ChromaDB 不可用，已创建延迟删除任务: %s", doc_id)

        # 清理物理文件（不存在时静默跳过）
        if file_path:
            try:
                _win_remove(file_path)
            except (FileNotFoundError, PermissionError):
                pass
            meta_path = file_path + ".meta.json"
            if _os.path.exists(meta_path):
                try:
                    _win_remove(meta_path)
                except (FileNotFoundError, PermissionError):
                    pass

        await _remove_imported_file(filename)
        return {"success": True, "message": f"已删除 '{filename}'"}
    except Exception as e:
        from observability import get_logger
        get_logger(__name__).error("删除失败: %s", e)
        return JSONResponse(status_code=500,
                            content={"success": False, "message": str(e)})


@router.get("/uploaded-files")
async def uploaded_files():
    """获取已导入文件列表（以 SQLite 为准，合并内存中的文件大小）。"""
    from database import get_session_ctx
    from database.operations import DocOps
    from web.app import _get_imported_files
    from observability import get_logger

    db_files = []
    try:
        with get_session_ctx() as session:
            for d in DocOps.get_all_documents(session):
                db_files.append({
                    "name": d.file_name,
                    "type": d.doc_type,
                    "chunks": d.chunk_count,
                    "time": d.upload_time.strftime("%Y-%m-%d %H:%M:%S")
                    if d.upload_time else "",
                    "doc_id": d.id,
                    "status": d.status or "",
                })
    except Exception:
        get_logger(__name__).warning("无法查询 SQLite 文档列表", exc_info=True)

    mem = await _get_imported_files()
    mem_by_name = {f["name"]: f for f in mem}
    merged = []
    seen = set()
    for d in db_files:
        m = mem_by_name.get(d["name"], {})
        d["size"] = m.get("size", "—")
        merged.append(d)
        seen.add(d["name"])
    for f in mem:
        if f["name"] not in seen:
            merged.append({**f, "doc_id": "", "status": ""})

    # _vector_ready 由 helper 内部的 _add_imported_file 自动维护，
    # 这里只需判断文件列表是否为空
    return {"files": merged, "vector_ready": bool(mem)}


@router.post("/open-file")
async def open_file(file_path: str = Form(...)):
    """打开本地文件（仅限 TESTCASE_BASE 目录下）。"""
    import config
    base = _os.path.abspath(config.TESTCASE_BASE)
    abs_path = _os.path.abspath(file_path)
    if not abs_path.startswith(base):
        return JSONResponse(status_code=403,
                            content={"success": False, "message": "无权访问该路径"})

    try:
        _os.startfile(abs_path)
        return {"success": True}
    except Exception as e:
        return JSONResponse(status_code=500,
                            content={"success": False, "message": str(e)})


@router.get("/download-file")
async def download_file(path: str = ""):
    """下载生成的文件（Excel / PY / YAML）。"""
    import config
    from fastapi.responses import FileResponse
    if not path or not _os.path.exists(path):
        return JSONResponse(status_code=404,
                            content={"success": False, "message": "文件不存在"})
    abs_path = _os.path.abspath(path)
    allowed_dirs = [
        _os.path.abspath(config.TESTCASE_BASE),
        _os.path.abspath(_os.path.join(config.BASE_DIR, "uploads")),
    ]
    # 路径包含检查（commonpath 防 sibling 绕过 + try/except 防跨盘符 ValueError）
    _safe = False
    for d in allowed_dirs:
        try:
            _safe = _os.path.commonpath([abs_path, d]) == d
        except ValueError:
            continue  # 跨盘符路径 → 拒绝
        if _safe: break
    if not _safe:
        return JSONResponse(status_code=403,
                            content={"success": False, "message": "无权访问该路径"})
    filename = _os.path.basename(abs_path)
    return FileResponse(abs_path, filename=filename)


@router.get("/file-content")
async def get_file_content(path: str = ""):
    """读取文件内容（供前端查看/编辑）。"""
    import config
    if not path or not _os.path.exists(path):
        return JSONResponse(status_code=404,
                            content={"success": False, "message": "文件不存在"})
    try:
        abs_path = _os.path.abspath(path)

        # 禁止读取系统敏感文件
        _blocked_suffixes = (".env", ".key", ".pem", "settings.local.json")
        if any(abs_path.endswith(s) for s in _blocked_suffixes):
            return JSONResponse(status_code=403,
                                content={"success": False,
                                         "message": "禁止读取系统文件"})

        allowed_dirs = [
            _os.path.abspath(config.TESTCASE_BASE),
            _os.path.abspath(_os.path.join(config.BASE_DIR, "uploads")),
        ]
        if not any(_os.path.commonpath([abs_path, d]) == d for d in allowed_dirs):
            return JSONResponse(status_code=403,
                                content={"success": False,
                                         "message": "无权访问该路径"})

        ext = _os.path.splitext(path)[1].lower()
        if ext in (".xlsx", ".zip", ".png", ".jpg"):
            return {"success": True, "binary": True,
                    "message": "二进制文件，请使用打开编辑"}

        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
        return {"success": True, "content": content, "path": path, "ext": ext}
    except UnicodeDecodeError:
        return {"success": True, "binary": True,
                "message": "二进制文件，请使用打开编辑"}
    except Exception as e:
        return JSONResponse(status_code=500,
                            content={"success": False, "message": str(e)})


@router.post("/file-save")
async def save_file_content(data: dict):
    """保存修改后的文件内容。"""
    import config
    path = data.get("path", "")
    content = data.get("content", "")
    if not path:
        return JSONResponse(status_code=400,
                            content={"success": False, "message": "缺少文件路径"})
    MAX_SAVE_SIZE = 10 * 1024 * 1024
    if len(content) > MAX_SAVE_SIZE:
        return JSONResponse(status_code=413,
                            content={"success": False, "message": "内容过大（超过 10MB）"})
    allowed_dirs = [
        _os.path.abspath(config.TESTCASE_BASE),
        _os.path.abspath("uploads"),
    ]
    abs_path = _os.path.abspath(path)
    if not any(abs_path.startswith(d) for d in allowed_dirs):
        return JSONResponse(status_code=403,
                            content={"success": False, "message": "无权写入该路径"})
    try:
        _os.makedirs(_os.path.dirname(abs_path), exist_ok=True)
        with open(abs_path, "w", encoding="utf-8") as f:
            f.write(content)
        return {"success": True, "message": "保存成功"}
    except Exception as e:
        return JSONResponse(status_code=500,
                            content={"success": False, "message": str(e)})
