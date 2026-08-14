"""补偿 Worker：独立轮询线程，处理 simple_summary / chroma_rebuild / api_search_text 等异步任务。

2026-08-07 大文件拆分：从 ``web/tasks.py`` 810–998 行整体迁移，逻辑零改动。
``web/tasks.py`` 末尾 re-export 本模块全部符号，既有
``from web.tasks import _start_compensation_worker`` 用法不变。
"""

import threading
import time

import config as _config

from observability import get_logger

logger = get_logger(__name__)


# ========================================================================
# 补偿 Worker（独立轮询线程，处理 simple_summary / chroma_rebuild 等异步任务）
# ========================================================================

_compensation_stop = False
_compensation_thread = None


def _start_compensation_worker():
    """启动补偿 worker 后台线程（应用启动时调用一次）。"""
    global _compensation_thread, _compensation_stop
    if _compensation_thread is not None:
        return
    _compensation_stop = False
    _compensation_thread = threading.Thread(
        target=_compensation_loop, name="compensation-worker", daemon=True,
    )
    _compensation_thread.start()
    logger.info("补偿 worker 已启动（poll_interval=%ds）", _config.COMPENSATION_POLL_INTERVAL)


def _stop_compensation_worker():
    """停止补偿 worker（应用关闭时调用）。"""
    global _compensation_stop
    _compensation_stop = True
    logger.info("补偿 worker 已停止")


def _compensation_loop():
    """补偿 worker 主循环：轮询 pending 任务，按 task_type 分发处理。"""
    import config
    poll_interval = config.COMPENSATION_POLL_INTERVAL

    while not _compensation_stop:
        try:
            _process_pending_compensation()
        except Exception:
            logger.error("补偿 worker 异常", exc_info=True)
        # 轮询间隔（可被 _compensation_stop 提前打断）
        for _ in range(poll_interval):
            if _compensation_stop:
                break
            time.sleep(1)


def _process_pending_compensation():
    """处理一批 pending 补偿任务。"""
    from database import get_session_ctx
    from database.operations.compensation import CompensationOps

    with get_session_ctx() as session:
        tasks = CompensationOps.fetch_pending(session, limit=10)
        if not tasks:
            return
        logger.debug("补偿 worker: 发现 %d 个待处理任务", len(tasks))
        for task in tasks:
            if _compensation_stop:
                break
            CompensationOps.mark_running(session, task)
            try:
                if task.task_type == "simple_summary":
                    _compensate_simple_summary(session, task)
                elif task.task_type == "chroma_rebuild":
                    _compensate_chroma_rebuild(session, task)
                elif task.task_type == "api_search_text":
                    _compensate_api_search_text(session, task)
                CompensationOps.mark_success(session, task)
                logger.info("补偿任务完成: %s id=%s", task.task_type, task.id)
            except Exception as e:
                logger.warning("补偿任务失败: %s id=%s — %s", task.task_type, task.id, e)
                CompensationOps.mark_failed(session, task, str(e))
        session.commit()


def _compensate_simple_summary(session, task):
    """补偿 simple_summary 生成：重试 LLM 调用。"""
    import json as _json
    import config
    from agent_components.nodes import _get_llm
    from prompts.extraction_prompts import batch_chunk_summary_prompt
    from database.models import DocumentChunk
    from agent_components.dual_chroma import get_chroma_db

    payload = _json.loads(task.payload)
    doc_id = payload["doc_id"]
    chunk_indices = payload.get("chunk_indices", [])
    file_name = payload.get("file_name", "")

    # 从 document_chunks 读原文
    chunks = session.query(DocumentChunk).filter_by(
        doc_id=doc_id).order_by(DocumentChunk.chunk_index).all()
    if not chunks:
        raise ValueError(f"document_chunks 无记录: {doc_id}")

    llm = _get_llm()
    prompt = batch_chunk_summary_prompt()
    batch_size = config.BATCH_SUMMARY_CHUNK_SIZE
    db = get_chroma_db()

    for bi in range(0, len(chunk_indices), batch_size):
        batch_idxs = chunk_indices[bi:bi + batch_size]
        batch_chunks = [(idx, chunks[idx].content) for idx in batch_idxs if idx < len(chunks)]
        if not batch_chunks:
            continue

        chunks_text = "\n\n".join(
            f"[块{idx}] {content}" for idx, content in batch_chunks
        )
        result = llm.invoke(prompt.format_messages(
            file_name=file_name,
            start_idx=batch_chunks[0][0],
            end_idx=batch_chunks[-1][0],
            total=len(chunks),
            page_name=chunks[batch_chunks[0][0]].page_name if batch_chunks[0][0] < len(chunks) else "",
            chunks=chunks_text,
        ))
        llm_text = result.content if hasattr(result, "content") else str(result)
        from ingest_v2 import _parse_chunk_summaries
        summaries = _parse_chunk_summaries(llm_text, len(batch_chunks))

        for (idx, _), summary in zip(batch_chunks, summaries):
            if summary:
                session.query(DocumentChunk).filter_by(
                    doc_id=doc_id, chunk_index=idx,
                ).update({"simple_summary": summary})

        # 更新 ChromaDB 检索文本
        try:
            all_chunks = session.query(DocumentChunk).filter_by(
                doc_id=doc_id).order_by(DocumentChunk.chunk_index).all()
            from ingest_v2 import _build_doc_search_text
            texts = [_build_doc_search_text({
                "content": c.content,
                "simple_summary": c.simple_summary,
                "analyzed_summary": c.analyzed_summary,
                "page_name": c.page_name,
            }) for c in all_chunks]
            db.delete_by_doc_id(doc_id)
            db.add_product_doc_chunks(doc_id, texts)
        except Exception as e:
            logger.warning("   [补偿] ChromaDB 更新失败（非致命）: %s", e)

    logger.info("补偿 simple_summary 完成: doc_id=%s, %d chunks", doc_id, len(chunk_indices))


def _compensate_chroma_rebuild(session, task):
    """补偿 ChromaDB 重建：从 SQLite 全量重建 collection。"""
    import json as _json
    from database.models import DocumentChunk
    from agent_components.dual_chroma import get_chroma_db
    from ingest_v2 import _build_doc_search_text

    payload = _json.loads(task.payload)
    doc_id = payload.get("doc_id", "")

    db = get_chroma_db()
    if doc_id:
        chunks = session.query(DocumentChunk).filter_by(doc_id=doc_id).order_by(DocumentChunk.chunk_index).all()
        texts = [_build_doc_search_text({
            "content": c.content,
            "simple_summary": c.simple_summary,
            "analyzed_summary": c.analyzed_summary,
            "page_name": c.page_name,
        }) for c in chunks]
        db.delete_by_doc_id(doc_id)
        db.add_product_doc_chunks(doc_id, texts)
    logger.info("补偿 chroma_rebuild 完成: doc_id=%s", doc_id or "all")


def _compensate_api_search_text(session, task):
    """补偿 API 检索文本重建。"""
    import json as _json
    from database.models import Document
    from agent_components.dual_chroma import get_chroma_db
    from ingest_v2 import _build_api_search_text

    payload = _json.loads(task.payload)
    doc_id = payload.get("doc_id", "")

    db = get_chroma_db()
    if doc_id:
        doc = session.query(Document).filter_by(id=doc_id).first()
        if doc and doc.doc_type == "api" and doc.api_url:
            api = {
                "name": doc.api_name, "url": doc.api_url, "method": doc.api_method,
                "description": doc.api_description,
                "header": _json.loads(doc.api_headers or "{}"),
                "body": _json.loads(doc.api_parameters or "[]"),
                "return": _json.loads(doc.api_returns or "[]"),
                "annotations": _json.loads(doc.api_annotations or "{}"),
                "_search_text": _build_api_search_text({}),
            }
            # 重新构造检索文本
            api["_search_text"] = _build_api_search_text(api)
            db.delete_by_doc_id(doc_id)
            db.add_api_defs(doc_id, [api])
    logger.info("补偿 api_search_text 完成: doc_id=%s", doc_id)
