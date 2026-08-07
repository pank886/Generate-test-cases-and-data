"""SQLite 文档/块/术语持久化 + 补偿回滚。"""

from observability import get_logger

logger = get_logger(__name__)


def _cascade_bind_to_module_docs(session, doc_type: str, doc_id: str, module_name: str):
    """级联关联：文档绑定模块时，自动与该模块下所有异类文档建立 doc↔doc 绑定。"""
    from database.operations import BindingOps
    bound_docs = BindingOps.get_bound_docs(session, module_name)
    for other_doc in bound_docs:
        if other_doc.doc_type != doc_type and other_doc.id != doc_id:
            BindingOps.bind(session, doc_type, doc_id, other_doc.doc_type, other_doc.id)


def _delete_sqlite_doc(doc_id: str):
    """删除 SQLite 中的文档记录（作为 ChromaDB 写入失败的补偿动作）。"""
    from database import get_session_ctx
    from database.operations import DocOps
    try:
        with get_session_ctx() as session:
            DocOps.delete_document(session, doc_id)
            logger.info("   [补偿] 已回滚 SQLite 记录: %s", doc_id)
    except Exception as e:
        logger.error("   [补偿] SQLite 回滚失败（需人工清理）: %s - %s", doc_id, e, exc_info=True)


def _save_to_sqlite(doc_id: str, file_name: str, file_type: str, doc_type: str,
                    chunk_count: int, module_name: str = "",
                    glossary_terms: list = None):
    """写入 SQLite：文档记录 + 术语。

    必须在 ChromaDB 写入**之前**调用。若后续 ChromaDB 写入失败，
    由调用方通过 _delete_sqlite_doc() 执行补偿回滚。
    module_name 仅用于日志，不做自动绑定（由用户在前端手动关联）。
    """
    from database import get_session_ctx
    from database.operations import DocOps, GlossaryOps

    try:
        with get_session_ctx() as session:
            # 1. 文档记录（session.merge() 不触发 column default，显式设置 upload_time）
            from database.models import Document
            from datetime import datetime, timezone
            doc = Document(
                id=doc_id, file_name=file_name, file_type=file_type,
                doc_type=doc_type, chunk_count=chunk_count, status="pending",
                upload_time=datetime.now(timezone.utc),
            )
            session.merge(doc)

            # 2. 术语（如果提供了）
            if glossary_terms:
                GlossaryOps.replace_terms(
                    session, doc_id, glossary_terms,
                    source_doc=file_name,
                )

            if module_name:
                logger.debug(f"   [SQLite] 文档 {doc_id} 关联模块: {module_name}")
    except Exception:
        logger.error("   [SQLite] 写入失败", exc_info=True)
        raise


def _save_single_chunk(doc_id: str, chunk_index: int, content: str,
                        page_name: str = ""):
    """写入单条 document_chunk 记录（供 Axure 逐页写入）。"""
    from database import get_session_ctx
    from database.models import DocumentChunk
    try:
        with get_session_ctx() as session:
            chunk = DocumentChunk(
                doc_id=doc_id,
                chunk_index=chunk_index,
                content=content,
                page_name=page_name,
                token_count=len(content),
            )
            session.add(chunk)
            session.commit()
    except Exception:
        logger.error("   [document_chunks] 单条写入失败: %s[%d]", doc_id, chunk_index, exc_info=True)
        raise


def _save_document_chunks(doc_id: str, chunks: list[str], page_name: str = ""):
    """写入 document_chunks 表（原文 + 摘要占位）。"""
    from database import get_session_ctx
    from database.models import DocumentChunk
    try:
        with get_session_ctx() as session:
            for i, content in enumerate(chunks):
                chunk = DocumentChunk(
                    doc_id=doc_id,
                    chunk_index=i,
                    content=content,
                    page_name=page_name,
                    token_count=len(content),
                )
                session.add(chunk)
            session.commit()
        logger.info(f"   [document_chunks] 写入 {len(chunks)} 条")
    except Exception:
        logger.error("   [document_chunks] 写入失败", exc_info=True)
        raise


def _delete_document_chunks(doc_id: str):
    """删除 document_chunks 记录（ChromaDB 写入失败时的补偿动作）。"""
    from database import get_session_ctx
    from database.models import DocumentChunk
    try:
        with get_session_ctx() as session:
            session.query(DocumentChunk).filter_by(doc_id=doc_id).delete()
            session.commit()
        logger.info("   [补偿] 已回滚 document_chunks: %s", doc_id)
    except Exception as e:
        logger.error("   [补偿] document_chunks 回滚失败: %s - %s", doc_id, e, exc_info=True)
