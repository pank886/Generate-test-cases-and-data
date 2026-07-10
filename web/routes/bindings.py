"""绑定关系路由。"""

from fastapi import APIRouter
from fastapi.responses import JSONResponse

router = APIRouter(prefix="/api/bindings", tags=["bindings"])


@router.post("")
async def create_binding(data: dict):
    """创建绑定（含级联：文档绑模块时自动关联同模块异类文档）。"""
    from database import get_session_ctx
    from database.operations import BindingOps
    from ingest_v2 import _cascade_bind_to_module_docs

    st, si = data.get("source_type"), data.get("source_id")
    tt, ti = data.get("target_type"), data.get("target_id")
    if not all([st, si, tt, ti]):
        return JSONResponse(status_code=400,
                            content={"success": False, "message": "缺少参数"})
    try:
        with get_session_ctx() as session:
            ok, msg = BindingOps.bind(session, st, si, tt, ti)
            if not ok:
                return {"success": False, "message": msg}
            doc_types = ("product", "api", "axure")
            if st in doc_types and tt == "module":
                _cascade_bind_to_module_docs(session, st, si, ti)
            elif tt in doc_types and st == "module":
                _cascade_bind_to_module_docs(session, tt, ti, si)
            return {"success": True, "message": "绑定成功"}
    except Exception as e:
        return JSONResponse(status_code=500,
                            content={"success": False, "message": str(e)})


@router.delete("")
async def delete_binding(data: dict):
    """解除绑定。"""
    from database import get_session_ctx
    from database.operations import BindingOps

    a_type, a_id = data.get("a_type"), data.get("a_id")
    b_type, b_id = data.get("b_type"), data.get("b_id")
    if not all([a_type, a_id, b_type, b_id]):
        return JSONResponse(status_code=400,
                            content={"success": False, "message": "缺少参数"})
    try:
        with get_session_ctx() as session:
            ok = BindingOps.unbind_by_pair(session, a_type, a_id, b_type, b_id)
            return {"success": ok, "message": "已解除" if ok else "绑定不存在"}
    except Exception as e:
        return JSONResponse(status_code=500,
                            content={"success": False, "message": str(e)})


@router.get("")
async def get_bindings(entity_type: str = "", entity_id: str = ""):
    """查询实体的所有关联。"""
    from database import get_session_ctx
    from database.operations import BindingOps

    with get_session_ctx() as session:
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
