"""文件管理路由：上传、删除、列表、查看/编辑。"""

import os as _os

from fastapi import APIRouter, UploadFile, File, Form, BackgroundTasks
from fastapi.responses import JSONResponse

from observability import get_logger

logger = get_logger(__name__)

router = APIRouter(tags=["files"])


@router.post("/upload-file")
async def upload_file(file: UploadFile = File(...),
                       background_tasks: BackgroundTasks = None):
    """上传文件 → 立即返回 task_id，后台异步处理。"""
    from agent_components.chromadb_file import ensure_directory
    from web.app import _chroma_db, _create_task

    filename = file.filename
    ext = _os.path.splitext(filename)[1].lower()
    type_map = {".pdf": "product", ".md": "md", ".docx": "product", ".zip": "axure"}
    file_type = type_map.get(ext)
    if file_type is None:
        supported = ", ".join(type_map.keys())
        return JSONResponse(status_code=400,
                            content={"success": False,
                                     "message": f"不支持的文件类型: {ext}。当前支持: {supported}"})

    type_dir_name = "md" if ext == ".md" else file_type
    type_dir = ensure_directory(f"./uploads/{type_dir_name}")
    file_path = _os.path.join(type_dir, filename)

    # 同名文件覆盖：先清理旧数据
    if _os.path.exists(file_path):
        try:
            from database import get_session
            from database.models import Document
            from database.operations import BindingOps, DocOps
            old_session = get_session()
            try:
                old_doc = old_session.query(Document).filter(
                    Document.file_name == filename).first()
                if old_doc:
                    BindingOps.delete_bindings_for_doc(old_session, old_doc.id)
                    DocOps.delete_document(old_session, old_doc.id)
                    old_session.commit()
                    _chroma_db.delete_by_doc_id(old_doc.id)
            finally:
                old_session.close()
        except Exception as e:
            logger.warning("旧数据清理失败: %s（物理文件已覆盖，可能存在残留数据）", e, exc_info=True)

    content = await file.read()
    with open(file_path, "wb") as f:
        f.write(content)

    task_id = await _create_task()
    from web.tasks import _process_file_bg
    background_tasks.add_task(
        _process_file_bg, task_id, file_path, ext,
        len(content), filename, file_type,
    )
    return {"success": True, "task_id": task_id,
            "message": "文件已接收，后台处理中"}


@router.post("/delete-file")
async def delete_file(filename: str = Form(...)):
    """删除文件：清理 SQLite + ChromaDB + 物理文件 + 内存状态。"""
    from web.app import _get_imported_files, _remove_imported_file, _chroma_db

    file_path = None
    for scan_dir in ["uploads/pdf", "uploads/md", "uploads/docx",
                      "uploads/axure", "uploads/product", "uploads"]:
        candidate = _os.path.join(scan_dir, filename)
        if _os.path.exists(candidate):
            file_path = _os.path.abspath(candidate)
            break

    if not file_path:
        return JSONResponse(status_code=404,
                            content={"success": False,
                                     "message": f"文件 '{filename}' 不存在"})

    try:
        from database import get_session
        from database.models import Document
        from database.operations import BindingOps, DocOps
        from web.app import _cleanup_doc_to_doc_bindings

        session = get_session()
        try:
            doc = session.query(Document).filter(
                Document.file_name == filename).first()
        finally:
            session.close()

        if not doc:
            _os.remove(file_path)
            return {"success": True,
                    "message": f"已删除 '{filename}'（无数据库记录）"}

        doc_id = doc.id

        sql_ok = True
        session = get_session()
        try:
            _cleanup_doc_to_doc_bindings(session, doc_id)
            BindingOps.delete_bindings_for_doc(session, doc_id)
            DocOps.delete_document(session, doc_id)
            session.commit()
        except Exception as e:
            session.rollback()
            from observability import get_logger
            get_logger(__name__).error("SQLite 清理失败: %s", e)
            sql_ok = False
        finally:
            session.close()

        if not sql_ok:
            return JSONResponse(status_code=500,
                                content={"success": False,
                                         "message": "数据库清理失败，文件未删除"})

        _chroma_db.delete_by_doc_id(doc_id)
        _os.remove(file_path)
        meta_path = file_path + ".meta.json"
        if _os.path.exists(meta_path):
            _os.remove(meta_path)

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
    from database import get_session
    from database.operations import DocOps
    from web.app import _get_imported_files
    from observability import get_logger

    db_files = []
    try:
        session = get_session()
        try:
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
        finally:
            session.close()
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


@router.get("/api/file-content")
async def get_file_content(path: str = ""):
    """读取文件内容（供前端查看/编辑）。"""
    import config
    if not path or not _os.path.exists(path):
        return JSONResponse(status_code=404,
                            content={"success": False, "message": "文件不存在"})
    try:
        allowed_dirs = [
            _os.path.abspath(config.TESTCASE_BASE),
            _os.path.abspath("uploads"),
        ]
        abs_path = _os.path.abspath(path)
        if not any(abs_path.startswith(d) for d in allowed_dirs):
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


@router.post("/api/file-save")
async def save_file_content(data: dict):
    """保存修改后的文件内容。"""
    import config
    path = data.get("path", "")
    content = data.get("content", "")
    if not path:
        return JSONResponse(status_code=400,
                            content={"success": False, "message": "缺少文件路径"})
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
