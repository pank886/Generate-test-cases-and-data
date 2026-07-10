"""模块管理路由：树查询 + CRUD + 合并 + 术语表。"""

from fastapi import APIRouter
from fastapi.responses import JSONResponse

router = APIRouter(prefix="/api/modules", tags=["modules"])


# ── 模块树查询 ──

@router.get("")
async def get_modules():
    """获取模块树。"""
    from database import get_session_ctx
    from agent_components.module_tree import get_tree

    with get_session_ctx() as session:
        return {"success": True, "tree": get_tree(session)}


@router.get("/{module_name}/docs")
async def get_module_docs(module_name: str):
    """获取模块关联的所有文档和接口。"""
    from database import get_session_ctx
    from database.operations import BindingOps
    from web.app import _chroma_db

    try:
        with get_session_ctx() as session:
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
                    item["api_names"] = [a["api_name"] for a in apis
                                         if a.get("api_name")]
                result.append(item)
            return {"success": True, "docs": result}
    except Exception as e:
        return JSONResponse(status_code=500,
                            content={"success": False, "message": str(e)})


@router.get("/{module_name}/related")
async def get_module_related(module_name: str):
    """获取模块的关联模块（module↔module）。"""
    from database import get_session_ctx
    from database.operations import BindingOps

    with get_session_ctx() as session:
        partners = BindingOps.get_partners(
            session, "module", module_name, "module",
        )
        return {"success": True,
                "related": [{"name": p[1]} for p in partners]}


# ── 模块 CRUD ──

@router.post("")
async def create_module(data: dict):
    """创建模块。"""
    from database import get_session_ctx
    from agent_components.module_tree import create

    name = data.get("name", "").strip()
    parent_id = data.get("parent_id", "root")
    if not name:
        return JSONResponse(status_code=400,
                            content={"success": False, "message": "模块名不能为空"})
    with get_session_ctx() as session:
        module = create(name, session, parent_id)
    return {"success": True, "module": module}


@router.put("/{module_id}")
async def update_module(module_id: str, data: dict):
    """更新模块（重命名）。"""
    from database import get_session_ctx
    from agent_components.module_tree import rename

    if "name" in data:
        with get_session_ctx() as session:
            ok, msg = rename(module_id, data["name"], session)
        if ok:
            return {"success": True, "message": msg}
        return JSONResponse(status_code=400,
                            content={"success": False, "message": msg})
    return JSONResponse(status_code=400,
                        content={"success": False, "message": "缺少 name 参数"})


@router.delete("/{module_id}")
async def delete_module(module_id: str):
    """删除模块。"""
    from database import get_session_ctx
    from agent_components.module_tree import delete

    try:
        with get_session_ctx() as session:
            delete(module_id, session)
        return {"success": True, "message": "已删除"}
    except ValueError as e:
        return JSONResponse(status_code=400,
                            content={"success": False, "message": str(e)})


@router.post("/merge")
async def merge_modules(data: dict):
    """合并模块。"""
    from database import get_session_ctx
    from agent_components.module_tree import merge

    source = data.get("source_id")
    target = data.get("target_id")
    try:
        with get_session_ctx() as session:
            ok, msg = merge(source, target, session)
        if ok:
            return {"success": True, "message": msg}
        return JSONResponse(status_code=400,
                            content={"success": False, "message": msg})
    except ValueError as e:
        return JSONResponse(status_code=400,
                            content={"success": False, "message": str(e)})


# ── 术语表 ──

@router.get("/{module_name}/glossary")
async def get_glossary(module_name: str):
    """获取模块术语表。"""
    from database import get_session_ctx
    from agent_components.module_tree import get_glossary

    with get_session_ctx() as session:
        return {"success": True, "terms": get_glossary(module_name, session)}


@router.post("/{module_name}/glossary")
async def add_glossary_term(module_name: str, data: dict):
    """添加/更新模块术语。"""
    from database import get_session_ctx
    from agent_components.module_tree import add_glossary_term

    term = data.get("term", "").strip()
    definition = data.get("definition", "").strip()
    notes = data.get("notes", "").strip()
    if not term:
        return JSONResponse(status_code=400,
                            content={"success": False, "message": "术语名不能为空"})
    with get_session_ctx() as session:
        ok = add_glossary_term(module_name, term, definition, session, notes=notes)
    if ok:
        return {"success": True, "message": f"已保存: {term}"}
    return JSONResponse(status_code=404,
                        content={"success": False, "message": "模块不存在"})


@router.delete("/{module_name}/glossary/{term}")
async def delete_glossary_term(module_name: str, term: str):
    """删除模块术语。"""
    from database import get_session_ctx
    from agent_components.module_tree import delete_glossary_term
    from urllib.parse import unquote

    with get_session_ctx() as session:
        ok = delete_glossary_term(module_name, unquote(term), session)
    if ok:
        return {"success": True, "message": f"已删除: {term}"}
    return JSONResponse(status_code=404,
                        content={"success": False, "message": "模块或术语不存在"})
