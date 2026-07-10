"""接口提取相关路由：上传 MD → LLM 提取 → 确认入库。"""

import os
import uuid
from datetime import datetime

from fastapi import APIRouter, UploadFile, File, Form
from fastapi.responses import JSONResponse

from observability import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/api/upload", tags=["api-extract"])


@router.post("/extract-api")
async def extract_api_doc(file: UploadFile = File(...),
                           module: str = Form("")):
    """上传接口 MD → LLM 提取接口列表 → 返回（不入库）。"""
    import asyncio
    from ingest_v2 import process_api_doc_extract
    # Windows 路径上限 260 字符，截断原始文件名防止超长
    raw_name = os.path.basename(file.filename)
    if len(raw_name) > 100:
        name_part, ext = os.path.splitext(raw_name)
        raw_name = name_part[:100] + ext
    safe_filename = f"{uuid.uuid4().hex[:8]}_{raw_name}"
    file_path = os.path.join("uploads", "md", safe_filename)
    os.makedirs(os.path.dirname(file_path), exist_ok=True)
    with open(file_path, "wb") as f:
        f.write(await file.read())
    try:
        result = await asyncio.to_thread(
            process_api_doc_extract, file_path,
            default_module=module or None,
        )
        result["file_path"] = file_path
        return {"success": True, **result}
    except Exception as e:
        # MD 是中间文件，提取失败后删除避免残留
        try:
            os.remove(file_path)
        except Exception:
            logger.warning("清理临时文件失败: %s", file_path, exc_info=True)
        return JSONResponse(status_code=500,
                            content={"success": False, "message": str(e)})


@router.post("/commit-api")
async def commit_api_endpoint(data: dict):
    """用户确认后，每个接口独立入库。仅当全部接口选中时才废弃原文件。"""
    from ingest_v2 import commit_api_docs as _commit

    file_path = data.get("file_path", "")
    module_name = data.get("module_name", "")
    apis = data.get("apis", [])
    all_selected = data.get("all_selected", False)
    if not file_path or not apis:
        return JSONResponse(status_code=400,
                            content={"success": False, "message": "缺少必要参数"})
    abs_path = os.path.abspath(file_path)
    uploads_root = os.path.abspath("uploads")
    if not abs_path.startswith(uploads_root):
        return JSONResponse(status_code=403,
                            content={"success": False, "message": "非法路径"})
    try:
        result = _commit(file_path, module_name, apis,
                          delete_original=all_selected)
        # 更新内存状态
        from web.app import _add_imported_file
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        for api in apis:
            await _add_imported_file({
                "name": f"{api.get('method', '?')} {api.get('url', '')}",
                "size": "—",
                "chunks": 1,
                "time": now_str,
                "type": "api",
            })
        return {"success": True, **result}
    except Exception as e:
        return JSONResponse(status_code=500,
                            content={"success": False, "message": str(e)})


@router.post("/retry-api")
async def retry_api_extract(data: dict):
    """用户拒绝拆分结果 → 重新 LLM 提取。"""
    import asyncio
    from ingest_v2 import process_api_doc_extract
    file_path = data.get("file_path", "")
    module = data.get("module_name", "")
    if not file_path:
        return JSONResponse(status_code=400,
                            content={"success": False, "message": "缺少 file_path"})
    try:
        result = await asyncio.to_thread(
            process_api_doc_extract, file_path,
            default_module=module or None,
        )
        result["file_path"] = file_path
        return {"success": True, **result}
    except Exception as e:
        return JSONResponse(status_code=500,
                            content={"success": False, "message": str(e)})
