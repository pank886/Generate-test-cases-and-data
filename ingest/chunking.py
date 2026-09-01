"""切块 + 摘要生成 + 检索文本构建。"""

from infrastructure.observability import get_logger

logger = get_logger(__name__)


def _parse_chunk_summaries(text: str, expected_count: int) -> list[str]:
    """正则解析 ===CHUNK_SUMMARY=== 分隔的摘要文本。

    返回长度等于 expected_count 的列表，不足补空字符串。
    """
    import re
    # 按 ===CHUNK_SUMMARY=== 拆分，取每段的第一行
    parts = re.split(r'===CHUNK_SUMMARY===', text)
    summaries = [p.strip().split('\n')[0].strip() for p in parts[1:]]  # 第一个是空
    # 补齐
    while len(summaries) < expected_count:
        summaries.append("")
    return summaries[:expected_count]


def _generate_batch_summaries(doc_id: str, chunks: list[str], file_name: str,
                               progress_cb=None, page_name: str = ""):
    """批量生成 simple_summary（5 chunks/批，同步等待，失败写补偿任务）。

    LLM 输出 ===CHUNK_SUMMARY=== 分隔词，正则匹配解析。
    """
    import re
    import infrastructure.config as config
    from agent_components.graph.nodes import _get_llm
    from prompts.extraction_prompts import batch_chunk_summary_prompt
    from database import get_session_ctx
    from database.models import DocumentChunk
    from database.operations.compensation import CompensationOps

    cb = progress_cb or (lambda p, m: None)
    total = len(chunks)
    batch_size = config.BATCH_SUMMARY_CHUNK_SIZE
    prompt = batch_chunk_summary_prompt()
    llm = _get_llm()

    failed_indices = []
    batch_count = (total + batch_size - 1) // batch_size

    for bi in range(0, total, batch_size):
        batch_end = min(bi + batch_size, total)
        batch_chunks = chunks[bi:batch_end]
        batch_num = bi // batch_size + 1
        pct = 70 + int((batch_num / batch_count) * 15)
        cb(pct, f"AI 生成摘要 ({batch_num}/{batch_count})...")

        chunks_text = "\n\n".join(
            f"[块{bi+j}] {c}" for j, c in enumerate(batch_chunks)
        )
        try:
            result = llm.invoke(prompt.format_messages(
                file_name=file_name,
                start_idx=bi,
                end_idx=batch_end - 1,
                total=total,
                page_name=page_name,
                chunks=chunks_text,
            ))
            llm_text = result.content if hasattr(result, "content") else str(result)
            summaries = _parse_chunk_summaries(llm_text, len(batch_chunks))
        except Exception as e:
            logger.warning("   [摘要] 批次 %d/%d LLM 调用失败: %s", batch_num, batch_count, e)
            # 整批失败 → 全部标记补偿
            summaries = [""] * len(batch_chunks)
            failed_indices.extend(range(bi, batch_end))

        # 写入 document_chunks.simple_summary
        try:
            with get_session_ctx() as session:
                for j, summary in enumerate(summaries):
                    if summary:  # 非空才写入
                        session.query(DocumentChunk).filter_by(
                            doc_id=doc_id, chunk_index=bi + j,
                        ).update({"simple_summary": summary})
                session.commit()
        except Exception as e:
            logger.error("   [摘要] 写入 SQLite 失败: %s", e)

        # 收集解析失败的索引（空摘要）
        for j, s in enumerate(summaries):
            if not s and (bi + j) not in failed_indices:
                failed_indices.append(bi + j)

    # 创建补偿任务（失败的 chunk）
    if failed_indices:
        try:
            with get_session_ctx() as session:
                CompensationOps.create(
                    session, "simple_summary",
                    {"doc_id": doc_id, "chunk_indices": failed_indices,
                     "file_name": file_name},
                    max_retries=config.COMPENSATION_MAX_RETRIES,
                )
                session.commit()
            logger.info("   [补偿] 已创建 simple_summary 补偿任务: %d 个 chunk", len(failed_indices))
        except Exception as e:
            logger.error("   [补偿] 创建补偿任务失败: %s", e)


def _build_api_search_text(api: dict) -> str:
    """构造 API 自然语言检索文本（替代 JSON 原文写入 ChromaDB）。

    格式: "{method} {url} {api_name}。{description}。
            参数: {param_name}{param_type}{'必填' if required else ''}{param_desc}; ...
            返回值: {ret_name}{ret_type}; ..."
    """
    name = api.get("name", "")
    from infrastructure.annotations.api_annotations import normalize_api_url
    url = normalize_api_url(api.get("url", ""))
    method = api.get("method", "?").upper()
    desc = api.get("description", name)

    parts = [f"{method} {url} {name}。{desc}。"]

    # 参数（新结构 body 六字段；兼容旧 key parameters）
    params = api.get("body", api.get("parameters", []))
    if isinstance(params, list) and params:
        param_strs = []
        for p in params[:20]:  # 最多 20 个参数，防过长
            pn = p.get("name", "")
            pt = p.get("type", "string")
            pr = "必填" if p.get("required") else ""
            pd = p.get("desc") or p.get("description", "")
            pv = p.get("value", "")
            param_strs.append(f"{pn}{pt}{pr}{pd}{pv}")
        parts.append("参数: " + "; ".join(param_strs))

    # 返回值（新结构 return）
    returns = api.get("return", api.get("returns", []))
    if isinstance(returns, list) and returns:
        ret_strs = [f"{r.get('name', '')}{r.get('type', '')}" for r in returns[:10]]
        parts.append("返回值: " + "; ".join(ret_strs))

    # 标签
    tags = []
    annotations = api.get("annotations", {})
    if isinstance(annotations, dict):
        if annotations.get("is_export", {}).get("active"):
            tags.append("导出接口")
        if annotations.get("has_path_params", {}).get("active"):
            tags.append("RESTful路径参数")
    if tags:
        parts.append("标签: " + " ".join(tags))

    return "。".join(parts)


def _build_doc_search_text(chunk: dict) -> str:
    """构造产品/Axure 文档检索文本。

    优先级: analyzed_summary > simple_summary > content 前 500 字
    """
    page = chunk.get("page_name", "")
    summary = chunk.get("analyzed_summary") or chunk.get("simple_summary") or ""
    if summary:
        return f"[{page}] {summary}" if page else summary
    # fallback: content 截断
    content = chunk.get("content", "")
    truncated = content[:500] if len(content) > 500 else content
    return f"[{page}] {truncated}" if page else truncated
