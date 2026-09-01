"""接口提取相关路由：上传 MD → LLM 提取 → 确认入库。"""

import asyncio
import os
import uuid
from datetime import datetime

import infrastructure.config as config
from fastapi import APIRouter, UploadFile, File, Form, BackgroundTasks
from fastapi.responses import JSONResponse

from infrastructure.observability import get_logger
from ingest_v2 import process_api_doc_extract, commit_api_docs as _commit

logger = get_logger(__name__)

router = APIRouter(prefix="/api/upload", tags=["api-extract"])


@router.post("/extract-api")
async def extract_api_doc(file: UploadFile = File(None),
                           file_path: str = Form(""),
                           module: str = Form("")):
    """LLM 提取接口列表 → 返回（不入库）。

    两种传参方式：
      - 直接上传文件：file + module
      - 指定已上传文件：file_path
    """
    # 处理直接上传的文件
    if file is not None and file.filename:
        raw_name = os.path.basename(file.filename)
        if len(raw_name) > 100:
            name_part, ext = os.path.splitext(raw_name)
            raw_name = name_part[:100] + ext
        safe_filename = f"{uuid.uuid4().hex[:8]}_{raw_name}"
        file_path = os.path.join(config.BASE_DIR, "uploads", "md", safe_filename)
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        with open(file_path, "wb") as f:
            f.write(await file.read())
        own_file = True
    else:
        own_file = False

    if not file_path or not os.path.exists(file_path):
        return JSONResponse(status_code=400,
                            content={"success": False, "message": "文件不存在"})
    try:
        result = await asyncio.to_thread(
            process_api_doc_extract, file_path,
            default_module=module or None,
        )
        result["file_path"] = file_path
        result["file_name"] = os.path.basename(file_path)
        return {"success": True, **result}
    except Exception as e:
        if own_file:
            try: os.remove(file_path)
            except Exception: pass
        return JSONResponse(status_code=500,
                            content={"success": False, "message": str(e)})


@router.post("/commit-api")
async def commit_api_endpoint(data: dict, background_tasks: BackgroundTasks = None):
    """用户确认后，后台逐条入库（含向量化），前端轮询进度。"""
    file_path = data.get("file_path", "")
    module_name = data.get("module_name", "")
    apis = data.get("apis", [])
    all_selected = data.get("all_selected", False)
    if not file_path or not apis:
        return JSONResponse(status_code=400,
                            content={"success": False, "message": "缺少必要参数"})
    abs_path = os.path.abspath(file_path)
    uploads_root = os.path.join(config.BASE_DIR, "uploads")
    if not abs_path.startswith(uploads_root):
        return JSONResponse(status_code=403,
                            content={"success": False, "message": "非法路径"})
    from web.state import _create_task
    task_id = await _create_task()
    from web.tasks import _commit_apis_bg
    background_tasks.add_task(
        _commit_apis_bg, task_id, file_path, module_name, apis, all_selected,
    )
    return {"success": True, "task_id": task_id,
            "message": f"开始入库 {len(apis)} 个接口..."}


@router.post("/retry-api")
async def retry_api_extract(data: dict):
    """用户拒绝拆分结果 → 重新 LLM 提取。"""
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


@router.post("/extract-api-code")
async def extract_api_code(file_path: str = Form(""), module_name: str = Form(""),
                           format: str = Form("md")):
    """纯代码提取接口，不走 LLM。

    读取类型（format）：
      - "md"（默认）   → YApi 导出的 MD 文档
      - "json"        → YApi 导出的 api.json（req_query/req_body_other/res_body 确定性解析）
    """
    from ingest_v2 import extract_apis_from_yapi_md, extract_apis_from_yapi_json, _extract_text
    if not file_path or not os.path.exists(file_path):
        return JSONResponse(status_code=400,
                            content={"success": False, "message": "文件不存在"})
    try:
        from infrastructure.annotations.api_annotations import ApiAnnotationRegistry
        full_text = _extract_text(file_path).strip()
        if not full_text:
            return JSONResponse(status_code=400,
                                content={"success": False, "message": "文档内容为空"})
        if format == "json":
            extracted = extract_apis_from_yapi_json(full_text)
            extract_method = "json"
        else:
            extracted = extract_apis_from_yapi_md(full_text)
            extract_method = "code"
        apis = extracted["apis"]
        if not module_name:
            module_name = extracted["module_name"] or (apis[0]["name"] if apis else "Unknown")
        # 自动标注（is_export/has_path_params/category 等）
        for api in apis:
            ApiAnnotationRegistry.apply_all(api)
        return {
            "success": True,
            "module_name": module_name,
            "apis": apis,
            "file_path": file_path,
            "file_name": os.path.basename(file_path),
            "extract_method": extract_method,
        }
    except Exception as e:
        return JSONResponse(status_code=500,
                            content={"success": False, "message": str(e)})
