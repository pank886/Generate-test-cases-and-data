"""文档操作路由：chunks 查看、术语表、模块关联迁移、解绑。"""

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from database import get_session_ctx
from database.operations import DocOps, GlossaryOps, BindingOps
from database.models import Document as DocModel
from web.services.doc_binding import rebind_doc_to_module

router = APIRouter(prefix="/api/docs", tags=["docs"])


@router.get("/unassociated")
async def get_unassociated_docs():
    """获取所有未关联模块的文档。"""
    try:
        with get_session_ctx() as session:
            docs = DocOps.get_unassociated_docs(session)
            return {"success": True, "docs": [
                {"doc_id": d.id, "module": "", "type": d.doc_type,
                 "doc_type": d.doc_type,
                 "chunks": d.chunk_count, "file_name": d.file_name}
                for d in docs
            ]}
    except Exception as e:
        return JSONResponse(status_code=500,
                            content={"success": False, "message": str(e)})


@router.post("/disassociate")
async def disassociate_doc(data: dict):
    """解除文档的模块关联。"""
    doc_id = data.get("doc_id", "")
    if not doc_id:
        return JSONResponse(status_code=400,
                            content={"success": False, "message": "缺少 doc_id"})
    try:
        with get_session_ctx() as session:
            rebind_doc_to_module(session, doc_id, "")
            return {"success": True, "message": "已解除关联"}
    except Exception as e:
        return JSONResponse(status_code=500,
                            content={"success": False, "message": str(e)})


@router.post("/change-module")
async def change_doc_module(data: dict):
    """将文档迁移到另一个模块。"""
    doc_id = data.get("doc_id", "")
    new_module = data.get("module", "")
    if not doc_id or not new_module:
        return JSONResponse(status_code=400,
                            content={"success": False, "message": "缺少 doc_id 或 module"})
    try:
        with get_session_ctx() as session:
            rebind_doc_to_module(session, doc_id, new_module)
            return {"success": True, "message": f"已迁移到 {new_module}"}
    except Exception as e:
        return JSONResponse(status_code=500,
                            content={"success": False, "message": str(e)})


@router.get("/{doc_id}/chunks")
async def get_doc_chunks(doc_id: str):
    """获取文档的文本块内容（从 SQLite document_chunks 表读取）。"""
    from database import get_session_ctx
    from database.models import DocumentChunk
    try:
        with get_session_ctx() as session:
            chunks = session.query(DocumentChunk).filter(
                DocumentChunk.doc_id == doc_id
            ).order_by(DocumentChunk.chunk_index).all()
            result = [
                {
                    "chunk_id": str(c.id),
                    "chunk_index": c.chunk_index,
                    "content": c.content,
                    "type": c.chunk_type,
                    "page_name": c.page_name or "",
                    "summary": c.analyzed_summary or c.simple_summary or "",
                }
                for c in chunks
            ]
        return {"success": True, "chunks": result}
    except Exception as e:
        return JSONResponse(status_code=500,
                            content={"success": False, "message": str(e)})


@router.get("/{doc_id}/apis")
async def get_doc_apis(doc_id: str):
    """获取文档下的所有接口定义（从 SQLite documents.api_* 列读取）。"""
    import json as _json
    from database import get_session_ctx
    from database.models import Document as DocModel
    try:
        with get_session_ctx() as session:
            doc = session.query(DocModel).filter_by(id=doc_id).first()
            if not doc or doc.doc_type != "api":
                return {"success": True, "apis": []}
            api = {
                "api_name": doc.api_name or "",
                "content": _json.dumps({
                    "name": doc.api_name or "",
                    "url": doc.api_url or "",
                    "method": doc.api_method or "",
                    "description": doc.api_description or "",
                    "header": _json.loads(doc.api_headers) if doc.api_headers else {},
                    "body": _json.loads(doc.api_parameters) if doc.api_parameters else [],
                    "return": _json.loads(doc.api_returns) if doc.api_returns else [],
                    "annotations": _json.loads(doc.api_annotations) if doc.api_annotations else {},
                }, ensure_ascii=False),
            }
        return {"success": True, "apis": [api]}
    except Exception as e:
        return JSONResponse(status_code=500,
                            content={"success": False, "message": str(e)})


@router.get("/{doc_id}/glossary")
async def get_doc_glossary(doc_id: str):
    """获取文档的术语表。"""
    with get_session_ctx() as session:
        terms = GlossaryOps.get_terms(session, doc_id)
        # 在 session 关闭前取出所有属性，避免惰性加载
        result = [
            {"term": t.term, "definition": t.definition, "notes": t.notes or ""}
            for t in terms
        ]
    return {"success": True, "terms": result}


@router.post("/{doc_id}/glossary")
async def add_doc_glossary(doc_id: str, data: dict):
    """添加文档术语（幂等：同名先删后插）。"""
    term = data.get("term", "").strip()
    definition = data.get("definition", "").strip()
    if not term:
        return JSONResponse(status_code=400,
                            content={"success": False, "message": "术语名不能为空"})
    try:
        with get_session_ctx() as session:
            for t in GlossaryOps.get_terms(session, doc_id):
                if t.term == term:
                    GlossaryOps.delete_term(session, t.id)
            GlossaryOps.add_term(session, doc_id, term, definition)
            return {"success": True, "message": f"已保存: {term}"}
    except Exception as e:
        return JSONResponse(status_code=500,
                            content={"success": False, "message": str(e)})


@router.delete("/{doc_id}/glossary/{term_id}")
async def delete_doc_glossary(doc_id: str, term_id: str):
    """删除文档术语。"""
    try:
        with get_session_ctx() as session:
            ok = GlossaryOps.delete_term(session, int(term_id))
            return {"success": ok, "message": "已删除" if ok else "术语不存在"}
    except Exception as e:
        return JSONResponse(status_code=500,
                            content={"success": False, "message": str(e)})


@router.get("/{doc_id}/related-docs")
async def get_doc_related_docs(doc_id: str):
    """获取文档的关联文档（doc↔doc）。"""
    with get_session_ctx() as session:
        doc = session.get(DocModel, doc_id)
        if not doc:
            return {"success": True, "related": []}
        doc_types = ("product", "api", "axure")
        partners = BindingOps.get_partners(session, doc.doc_type, doc_id)
        # 批量查询所有关联文档，避免 N+1
        partner_ids = [pi for pt, pi in partners if pt in doc_types]
        if partner_ids:
            docs = {d.id: d for d in session.query(DocModel).filter(DocModel.id.in_(partner_ids)).all()}
        else:
            docs = {}
        related = [
            {"doc_id": pi, "doc_type": pt, "file_name": docs[pi].file_name if pi in docs else pi}
            for pt, pi in partners if pt in doc_types
        ]
        return {"success": True, "related": related}
