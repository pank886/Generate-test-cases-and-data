"""文档操作路由：chunks 查看、术语表、模块关联迁移、解绑。"""

from fastapi import APIRouter
from fastapi.responses import JSONResponse

router = APIRouter(prefix="/api/docs", tags=["docs"])


@router.get("/unassociated")
async def get_unassociated_docs():
    """获取所有未关联模块的文档。"""
    from database import get_session
    from database.operations import DocOps

    session = get_session()
    try:
        docs = DocOps.get_unassociated_docs(session)
        return {"success": True, "docs": [
            {"doc_id": d.id, "module": "", "type": d.doc_type,
             "chunks": d.chunk_count, "file_name": d.file_name}
            for d in docs
        ]}
    except Exception as e:
        return JSONResponse(status_code=500,
                            content={"success": False, "message": str(e)})
    finally:
        session.close()


@router.post("/disassociate")
async def disassociate_doc(data: dict):
    """解除文档的模块关联。"""
    from database import get_session

    doc_id = data.get("doc_id", "")
    if not doc_id:
        return JSONResponse(status_code=400,
                            content={"success": False, "message": "缺少 doc_id"})
    session = get_session()
    try:
        from web.services.doc_binding import rebind_doc_to_module
        rebind_doc_to_module(session, doc_id, "")
        session.commit()
        return {"success": True, "message": "已解除关联"}
    except Exception as e:
        session.rollback()
        return JSONResponse(status_code=500,
                            content={"success": False, "message": str(e)})
    finally:
        session.close()


@router.post("/change-module")
async def change_doc_module(data: dict):
    """将文档迁移到另一个模块。"""
    from database import get_session

    doc_id = data.get("doc_id", "")
    new_module = data.get("module", "")
    if not doc_id or not new_module:
        return JSONResponse(status_code=400,
                            content={"success": False, "message": "缺少 doc_id 或 module"})
    session = get_session()
    try:
        from web.services.doc_binding import rebind_doc_to_module
        rebind_doc_to_module(session, doc_id, new_module)
        session.commit()
        return {"success": True, "message": f"已迁移到 {new_module}"}
    except Exception as e:
        session.rollback()
        return JSONResponse(status_code=500,
                            content={"success": False, "message": str(e)})
    finally:
        session.close()


@router.get("/{doc_id}/chunks")
async def get_doc_chunks(doc_id: str):
    """获取文档的文本块内容。"""
    from web.app import _chroma_db
    try:
        db = _chroma_db
        chunks = db.get_doc_chunks(doc_id)
        return {"success": True, "chunks": chunks}
    except Exception as e:
        return JSONResponse(status_code=500,
                            content={"success": False, "message": str(e)})


@router.get("/{doc_id}/apis")
async def get_doc_apis(doc_id: str):
    """获取文档下的所有接口定义。"""
    from web.app import _chroma_db
    try:
        db = _chroma_db
        apis = db.get_doc_apis(doc_id)
        return {"success": True, "apis": apis}
    except Exception as e:
        return JSONResponse(status_code=500,
                            content={"success": False, "message": str(e)})


@router.get("/{doc_id}/glossary")
async def get_doc_glossary(doc_id: str):
    """获取文档的术语表。"""
    from agent_components.module_tree import get_glossary_by_doc
    terms = get_glossary_by_doc(doc_id)
    return {"success": True, "terms": terms}


@router.post("/{doc_id}/glossary")
async def add_doc_glossary(doc_id: str, data: dict):
    """添加文档术语（幂等：同名先删后插）。"""
    from database import get_session
    from database.operations import GlossaryOps

    term = data.get("term", "").strip()
    definition = data.get("definition", "").strip()
    if not term:
        return JSONResponse(status_code=400,
                            content={"success": False, "message": "术语名不能为空"})
    session = get_session()
    try:
        for t in GlossaryOps.get_terms(session, doc_id):
            if t.term == term:
                GlossaryOps.delete_term(session, t.id)
        GlossaryOps.add_term(session, doc_id, term, definition)
        session.commit()
        return {"success": True, "message": f"已保存: {term}"}
    except Exception as e:
        session.rollback()
        return JSONResponse(status_code=500,
                            content={"success": False, "message": str(e)})
    finally:
        session.close()


@router.delete("/{doc_id}/glossary/{term_id}")
async def delete_doc_glossary(doc_id: str, term_id: str):
    """删除文档术语。"""
    from database import get_session
    from database.operations import GlossaryOps

    session = get_session()
    try:
        ok = GlossaryOps.delete_term(session, int(term_id))
        session.commit()
        return {"success": ok, "message": "已删除" if ok else "术语不存在"}
    except Exception as e:
        session.rollback()
        return JSONResponse(status_code=500,
                            content={"success": False, "message": str(e)})
    finally:
        session.close()


@router.get("/{doc_id}/related-docs")
async def get_doc_related_docs(doc_id: str):
    """获取文档的关联文档（doc↔doc）。"""
    from database import get_session
    from database.operations import BindingOps
    from database.models import Document as DocModel

    session = get_session()
    try:
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
