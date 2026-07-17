"""Phase A: 智能文档处理入口（替代旧 ingest_file.py 的单 Collection 流程）

入库流程：
  文件 → LLM 提取 → 1. SQLite（文档元数据 + 绑定关系 + 术语）
                   → 2. ChromaDB（纯文本 + 向量，不含业务关系）

使用方式：
    from ingest_v2 import process_product_doc, process_api_doc
    process_product_doc("uploads/doc.pdf")
    process_api_doc("uploads/api.md")
"""

import os
import re

from langchain_text_splitters import RecursiveCharacterTextSplitter

from observability import get_logger
from agent_components.dual_chroma import get_chroma_db
from agent_components.nodes import ChatTestAgentGraph
from prompts.response_model import DocModuleExtract, ApiDefExtract
from prompts.extraction_prompts import (
    product_doc_extract_prompt,
    api_def_extract_prompt,
)
import config

logger = get_logger(__name__)


def _merge_api_defs(existing: dict, incoming: dict) -> dict:
    """合并同一接口的两个版本（method+url 相同），而非简单覆盖。

    合并策略：
      - parameters/returns: 取两套字段的并集，incoming 的字段优先
      - description: 取更详细（更长）的那一个
      - name/method/url: 保留 incoming（新版本为准）
    """
    merged = dict(incoming)  # 以新版本为基底
    # parameters 做字段级合并
    existing_params = existing.get("parameters", {}) or {}
    incoming_params = incoming.get("parameters", {}) or {}
    merged_params = dict(existing_params)
    merged_params.update(incoming_params)  # incoming 的字段覆盖
    merged["parameters"] = merged_params

    # returns 同理
    existing_returns = existing.get("returns", {}) or {}
    incoming_returns = incoming.get("returns", {}) or {}
    merged_returns = dict(existing_returns)
    merged_returns.update(incoming_returns)
    merged["returns"] = merged_returns

    # description 保留更详细的那个
    desc_existing = (existing.get("description") or "").strip()
    desc_incoming = (incoming.get("description") or "").strip()
    merged["description"] = desc_incoming if len(desc_incoming) >= len(desc_existing) else desc_existing

    return merged


def _extract_text(file_path: str) -> str:
    """通用文本提取（支持 PDF/MD/TXT/DOCX）。"""
    ext = os.path.splitext(file_path)[1].lower()
    if ext == ".pdf":
        from pypdf import PdfReader
        reader = PdfReader(file_path)
        texts = []
        for p in reader.pages:
            t = p.extract_text()
            if t: texts.append(t)
        return "\n\n".join(texts)
    elif ext in (".md", ".txt"):
        with open(file_path, "r", encoding="utf-8") as f:
            return f.read()
    elif ext == ".docx":
        return _extract_docx(file_path)
    else:
        raise ValueError(f"不支持的文件类型: {ext}")


def _extract_docx(file_path: str) -> str:
    """提取 Word 文档文本，附带图片占位标记（供后续多模态替换）。"""
    from docx import Document as DocxDocument
    doc = DocxDocument(file_path)

    parts = []
    img_index = 0

    for para in doc.paragraphs:
        text = para.text.strip()
        if text:
            parts.append(text)

    # 提取表格
    for table in doc.tables:
        rows = []
        for row in table.rows:
            cells = [cell.text.strip() for cell in row.cells]
            rows.append(" | ".join(cells))
        if rows:
            parts.append("[表格]\n" + "\n".join(rows))

    # 提取图片（先记录位置，内容暂用占位）
    from docx.opc.constants import RELATIONSHIP_TYPE as RT
    img_dir = _docx_img_dir(file_path)
    for rel in doc.part.rels.values():
        if "image" in str(rel.reltype):
            img_index += 1
            # 保存图片到临时目录（供后续多模态模型使用）
            img_data = rel.target_part.blob
            os.makedirs(img_dir, exist_ok=True)
            img_path = os.path.join(img_dir, f"{os.path.basename(file_path)}_{img_index}.png")
            with open(img_path, "wb") as f:
                f.write(img_data)
            parts.append(f"[图片_{img_index}: {os.path.basename(img_path)}]")

    result = "\n\n".join(parts)
    if img_index > 0:
        result += f"\n\n[本文档包含 {img_index} 张图片，已保存至 {os.path.basename(img_dir)}/ 目录]"
    return result


def _docx_img_dir(file_path: str) -> str:
    """获取 docx 图片临时目录（含文件标识，防并发冲突）。"""
    stem = os.path.splitext(os.path.basename(file_path))[0]
    safe_stem = stem.replace(" ", "_").replace(".", "_")
    return os.path.join(os.path.dirname(file_path), f"_images_{safe_stem}")


def _safe_doc_id(prefix: str, *parts: str) -> str:
    """生成唯一的 doc_id（用于删除+写入的幂等操作）。

    幂等性要求：同一文件→同一 doc_id→delete_by_doc_id 清理旧数据→写入新数据。
    TODO(多用户): doc_id 追加 user_id 或 hash 后缀防跨用户碰撞，同时保持同文件幂等。
    """
    import hashlib
    sanitized = [p.replace('/', '_').replace('\\', '_').replace('$', '_') for p in parts if p]
    if not sanitized:
        return prefix
    raw = prefix + "_" + "_".join(sanitized)
    # 限制总长度 ≤ 180（String(200) 留余量给 ChromaDB 内部后缀）
    if len(raw) > 180:
        suffix = hashlib.md5(raw.encode()).hexdigest()[:8]
        logger.warning("doc_id 超长截断（%d > 180）: %s… → …_%s", len(raw), raw[:60], suffix)
        return raw[:172] + "_" + suffix
    return raw


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


def process_product_doc(file_path: str, progress_cb=None) -> dict:
    """处理产品文档：提取文本 -> LLM 提取模块关联 -> 存入 product_docs + SQLite。

    Args:
        progress_cb: 可选，进度回调 (0~100, message)
    """
    cb = progress_cb or (lambda p, m: None)
    from observability import log_phase_header
    log_phase_header("Phase A — 文档摄入与向量化")
    logger.info(f"\n{'=' * 60}")
    logger.info(f"[Phase A] 处理产品文档: {os.path.basename(file_path)}")

    db = get_chroma_db()
    graph = ChatTestAgentGraph()
    file_name = os.path.basename(file_path)
    file_type = os.path.splitext(file_path)[1].lstrip(".")

    # 1. 提取文本
    cb(5, "提取文本中...")
    full_text = _extract_text(file_path).strip()
    if not full_text:
        raise ValueError("文档内容为空")
    logger.info(f"   => 提取文本 {len(full_text)} 字符")

    # _extract_text 可能产生临时图片目录，统一在 finally 中清理
    _img_dir = _docx_img_dir(file_path) if os.path.splitext(file_path)[1].lower() == ".docx" else None
    try:
        # 2. 切块（前置，后续 LLM 提取和 ChromaDB 入库共用同一批块）
        cb(15, "文本切分中...")
        splitter = RecursiveCharacterTextSplitter(
            chunk_size=config.CHUNK_SIZE,
            chunk_overlap=config.CHUNK_OVERLAP,
            separators=["\n\n", "\n", "。", "，", " "],
        )
        chunks = splitter.split_text(full_text)
        logger.info(f"   => 切分为 {len(chunks)} 个文本块")

        # 将块打包为不超过 MAX_INGEST_CHARS_PER_BATCH 的批次
        def _group_chunks_into_batches(chunks: list[str], max_chars: int) -> list[str]:
            """分组拼接为每批不超过 max_chars 的文本段。"""
            out, batch = [], []
            for c in chunks:
                candidate = "\n\n".join(batch + [c]) if batch else c
                if len(candidate) > max_chars and batch:
                    out.append("\n\n".join(batch))
                    batch = [c]
                else:
                    batch.append(c)
            if batch:
                out.append("\n\n".join(batch))
            return out

        batch_limit = config.MAX_INGEST_CHARS_PER_BATCH
        text_batches = _group_chunks_into_batches(chunks, batch_limit) if len(full_text) > batch_limit else [full_text]
        logger.info(f"   => 打包为 {len(text_batches)} 批（每批 ≤ {batch_limit} 字符）")

        # 3. LLM 提取模块信息（分批处理，合并 related_modules / tags）
        cb(30, "AI 分析模块信息...")
        prompt = product_doc_extract_prompt()
        logger.info("   => LLM 提取模块信息...")
        module_name = ""
        related: set[str] = set()
        business_summary = ""
        tags: set[str] = set()
        for bi, batch_text in enumerate(text_batches, 1):
            result = graph._invoke_structured(
                prompt, DocModuleExtract,
                method="json_mode",
                doc_text=batch_text,
            )
            if not module_name and result.module_name:
                module_name = result.module_name
            if result.related_modules:
                related.update(result.related_modules)
            if not business_summary and result.business_summary:
                business_summary = result.business_summary
            if result.tags:
                tags.update(result.tags)
            if len(text_batches) > 1:
                logger.info(f"   [{bi}/{len(text_batches)}] 模块: {result.module_name or '?'}, "
                            f"+{len(result.related_modules or [])} 关联")
        module_name = module_name or "Unknown"
        related_list = sorted(related)
        logger.info(f"   => 模块: {module_name}, 关联: {related_list}, 标签: {sorted(tags)}")

        # 4. LLM 提取业务术语表（分批处理，合并去重）
        cb(50, "AI 提取术语表...")
        from prompts.response_model import GlossaryExtract
        from prompts.extraction_prompts import glossary_extract_prompt
        terms = []
        try:
            glossary_prompt = glossary_extract_prompt()
            seen_terms: set[str] = set()
            for bi, batch_text in enumerate(text_batches, 1):
                glossary_result = graph._invoke_structured(
                    glossary_prompt, GlossaryExtract,
                    method="json_mode",
                    doc_text=batch_text,
                )
                batch_terms = glossary_result.terms if hasattr(glossary_result, "terms") else []
                for t in batch_terms:
                    key = (t.get("term", t.get("name", "")).strip())
                    if key and key not in seen_terms:
                        seen_terms.add(key)
                        terms.append(t)
                if len(text_batches) > 1:
                    logger.info(f"   [{bi}/{len(text_batches)}] 术语: +{len(batch_terms)} 条（合并后 {len(terms)} 条）")
            if terms:
                logger.info(f"   => 术语表: {len(terms)} 条")
        except Exception as e:
            logger.warning("术语表提取跳过: %s", e, exc_info=True)

        # 5. 写入 SQLite（先写关系库，成功后写向量库）
        cb(85, "写入业务数据...")
        doc_id = _safe_doc_id("prod", file_name, module_name)
        _save_to_sqlite(
            doc_id=doc_id,
            file_name=file_name,
            file_type=file_type,
            doc_type="product",
            chunk_count=len(chunks),
            module_name=module_name,
            glossary_terms=terms,
        )
        logger.info(f"   [SQLite] 入库完成 (doc_id={doc_id})")

        # 6. 写入 ChromaDB（纯向量，失败时补偿回滚 SQLite）
        cb(90, "向量化入库中...")
        try:
            db.delete_by_doc_id(doc_id)
            db.add_product_doc_chunks(doc_id, chunks)
            logger.info(f"   [ChromaDB] 入库完成 (doc_id={doc_id})")
        except Exception:
            logger.error("   [ChromaDB] 写入失败，启动补偿回滚 SQLite", exc_info=True)
            _delete_sqlite_doc(doc_id)
            raise

        cb(95, "入库完成")
        return {
            "doc_id": doc_id,
            "module_name": module_name,
            "related_modules": related_list,
            "chunks": len(chunks),
        }
    finally:
        if _img_dir and os.path.isdir(_img_dir):
            import shutil as _su
            _su.rmtree(_img_dir, ignore_errors=True)
            logger.debug("已清理临时图片目录: %s", _img_dir)


def _split_text_by_headers(text: str, max_chars: int) -> list:
    """按 # 标题切分文本，每段不超过 max_chars 字符。"""
    import re
    parts = re.split(r'(?=\n#+ )', text)
    batches = []
    current = ""
    for part in parts:
        part = part.strip()
        if not part:
            continue
        # 单个 part 超限时直接截断
        if len(part) > max_chars:
            if current:
                batches.append(current)
                current = ""
            for i in range(0, len(part), max_chars):
                batches.append(part[i:i + max_chars])
            continue
        if len(current) + len(part) > max_chars and current:
            batches.append(current)
            current = part
        else:
            current = (current + "\n\n" + part).strip()
    if current:
        batches.append(current)
    return batches


def process_api_doc(file_path: str, default_module: str = None, progress_cb=None) -> dict:
    """处理接口文档：提取 → 入库（委托给新版函数）。

    .. deprecated::
       使用 ``process_api_doc_extract()`` + ``commit_api_docs()`` 替代。
       当前保留仅用于 CLI 兼容，内部已委托新版函数。
    """
    logger.warning("process_api_doc() 已弃用，请使用 process_api_doc_extract() + commit_api_docs()")
    cb = progress_cb or (lambda p, m: None)

    # 委托新版提取
    cb(5, "提取接口定义...")
    extracted = process_api_doc_extract(file_path, default_module=default_module)
    apis = extracted.get("apis", [])
    module = extracted.get("module_name") or default_module or "Unknown"
    if not apis:
        logger.warning("未提取到接口定义")
        return {"doc_id": "", "module_name": module, "api_count": 0}

    # 委托新版入库（已含 SQLite 先写 + ChromaDB 补偿逻辑）
    cb(80, "入库中...")
    result = commit_api_docs(file_path, module, apis)
    logger.info("   => 委托入库完成: %d 个接口", result["api_count"])
    return result


def process_api_doc_extract(file_path: str, default_module: str = None,
                             progress_cb=None) -> dict:
    """Phase 1: 提取接口列表（不入库），返回给前端确认。

    Returns: {"module_name": str, "apis": [dict], "file_name": str}
    """
    cb = progress_cb or (lambda p, m: None)
    logger.info(f"[Phase A] 提取接口: {os.path.basename(file_path)}")

    graph = ChatTestAgentGraph()
    file_name = os.path.basename(file_path)

    cb(5, "读取文档...")
    full_text = _extract_text(file_path).strip()
    if not full_text:
        raise ValueError("文档内容为空")

    batch_limit = config.MAX_INGEST_CHARS_PER_BATCH
    batches = _split_text_by_headers(full_text, batch_limit) if len(full_text) > batch_limit else [full_text]

    all_apis = []
    module = default_module

    for bi, batch_text in enumerate(batches, 1):
        pct = int(10 + (bi / len(batches)) * 70)
        cb(pct, f"AI 提取接口定义 ({bi}/{len(batches)})...")
        prompt = api_def_extract_prompt()
        result = graph._invoke_structured(
            prompt, ApiDefExtract, method="json_mode", doc_text=batch_text,
        )
        if not module:
            module = result.module_name
        apis_raw = result.apis if hasattr(result, "apis") else []
        apis = [a.model_dump() if hasattr(a, "model_dump") else a for a in apis_raw]
        all_apis.extend(apis)

    # 合并去重：method+url 相同时合并参数和返回值，而非简单覆盖
    # TODO(多用户): 在前端展示两个版本的 diff，让用户选择保留哪个
    merged = {}
    dup_count = 0
    for api in all_apis:
        key = f"{api.get('method', '')} {api.get('url', '')}"
        if key in merged:
            dup_count += 1
            merged[key] = _merge_api_defs(merged[key], api)
        else:
            merged[key] = api
    if dup_count:
        logger.warning("检测到 %d 个重复接口（method+url 相同），已合并参数/返回值/描述", dup_count)
    apis = list(merged.values())

    module = module or "Unknown"
    return {"module_name": module, "apis": apis, "file_name": file_name}


def commit_api_docs(file_path: str, module_name: str, apis: list[dict],
                    progress_cb=None, delete_original: bool = False) -> dict:
    """Phase 2: 用户确认后，接口批量入库。

    所有 API 先批量写入 SQLite（同一事务），再逐条写入 ChromaDB。
    ChromaDB 任一条失败时补偿回滚所有 SQLite 记录。
    仅 delete_original=True 时删除原文件。
    """
    cb = progress_cb or (lambda p, m: None)
    logger.info(f"[Phase A] 入库 {len(apis)} 个接口文档")

    db = get_chroma_db()
    file_name = os.path.basename(file_path)
    file_type = os.path.splitext(file_path)[1].lstrip(".")
    doc_ids = []

    # ---- Phase 1: 批量写入 SQLite（同一事务）----
    from database import get_session_ctx
    from database.models import Document
    from datetime import datetime, timezone

    cb(10, "写入业务数据...")
    docs_to_insert = []
    for i, api in enumerate(apis):
        api_name = api.get("name", f"api_{i}")
        url = api.get("url", "")
        method = api.get("method", "?")
        doc_id = _safe_doc_id("api", file_name, module_name, method, url, api_name)
        doc_ids.append(doc_id)
        docs_to_insert.append(Document(
            id=doc_id,
            file_name=f"{api.get('method', '?')} {api.get('url', '')}",
            file_type=file_type,
            doc_type="api",
            chunk_count=1,
            status="pending",
            upload_time=datetime.now(timezone.utc),
        ))

    try:
        with get_session_ctx() as session:
            for d in docs_to_insert:
                session.merge(d)
    except Exception:
        logger.error("   [SQLite] 批量写入失败，无数据需要补偿", exc_info=True)
        raise

    logger.info(f"   [SQLite] 批量入库完成: {len(doc_ids)} 条")

    # ---- Phase 2: 逐条写入 ChromaDB（失败时补偿回滚 SQLite）----
    cb(50, "向量化入库中...")
    try:
        for i, api in enumerate(apis):
            api_name = api.get("name", f"api_{i}")
            url = api.get("url", "")
            method = api.get("method", "?")
            doc_id = doc_ids[i]
            cb(int(50 + (i / len(apis)) * 40), f"入库 {method} {url}")
            db.delete_by_doc_id(doc_id)
            db.add_api_defs(doc_id, [api])
    except Exception:
        logger.error("   [ChromaDB] 写入失败，启动补偿回滚所有 SQLite 记录", exc_info=True)
        for did in doc_ids:
            _delete_sqlite_doc(did)
        raise

    logger.info(f"   [ChromaDB] 入库完成: {len(doc_ids)} 条")

    # 仅当全部接口选中时才废弃原文件
    if delete_original:
        try:
            os.remove(file_path)
            meta_path = file_path + ".meta.json"
            if os.path.exists(meta_path):
                os.remove(meta_path)
            logger.info(f"   => 已删除原文件: {file_name}")
        except OSError:
            logger.warning("原文件删除失败: %s", file_name, exc_info=True)
    else:
        logger.info(f"   => 保留原文件（部分接口未入库）: {file_name}")

    cb(95, "入库完成")
    return {"doc_ids": doc_ids, "module_name": module_name, "api_count": len(apis)}


def process_axure_zip(file_path: str, module_name: str = None, progress_cb=None) -> dict:
    """处理 Axure HTML 演示包：解析页面树 + UI 文本 + 交互 -> 存入 product_docs + SQLite。

    Args:
        file_path: Axure 导出的 .zip 文件路径
        module_name: 所属模块（如不指定则从 sitemap 自动提取）
        progress_cb: 可选，进度回调 (0~100, message)
    """
    from agent_components.axure_parser import AxureParser

    cb = progress_cb or (lambda p, m: None)
    logger.info(f"\n{'=' * 60}")
    logger.info(f"[Phase A] 处理 Axure 原型: {os.path.basename(file_path)}")

    db = get_chroma_db()
    file_name = os.path.basename(file_path)

    cb(5, "解压 Axure 包...")
    parser = AxureParser(file_path)
    try:
        cb(15, "解析页面树和 sitemap...")
        parsed = parser.parse()
        project_name = parsed.get("project_name", "Unknown")
        module = module_name or project_name
        cb(40, "提取 UI 文本和交互...")
        chunks = parser.to_product_doc_chunks(parsed)

        page_details = parsed.get("page_details", {})
        logger.info(f"   => 项目: {project_name}, 页面: {len(page_details)}")

        if not chunks:
            logger.warning(f"   ⚠️ Axure 解析后无内容（0 个页面），跳过入库")
            return {"doc_id": "", "module_name": module, "chunks": 0}

        # LLM 提取关联模块（复用 product_doc_extract_prompt 的语义分析能力）
        cb(70, "AI 分析关联模块...")
        graph = ChatTestAgentGraph()
        related = set()
        try:
            from prompts.extraction_prompts import product_doc_extract_prompt
            from prompts.response_model import DocModuleExtract
            prompt = product_doc_extract_prompt()
            # 取页面详情文本拼接，控制在单批上限内
            page_items = list(page_details.items())
            if len(page_items) > 50:
                logger.warning("Axure 页面数 %d > 50，已截断至 50 页用于 LLM 提取", len(page_items))
            detail_text = "\n".join(
                f"[{url}] {detail.get('ui_text', '')}"
                for url, detail in page_items[:50]
            )
            batch_limit = config.MAX_INGEST_CHARS_PER_BATCH
            if len(detail_text) > batch_limit:
                logger.warning("Axure 页面详情文本 %d 字符 > %d，已截断用于 LLM 提取",
                               len(detail_text), batch_limit)
                detail_text = detail_text[:batch_limit]  # Python str 切片，UTF-8 安全
            result = graph._invoke_structured(
                prompt, DocModuleExtract,
                method="json_mode",
                doc_text=detail_text,
            )
            related = set(result.related_modules or [])
            logger.info(f"   => LLM 识别关联模块: {related}")
        except Exception as e:
            logger.error("   => 关联模块分析失败: %s", e, exc_info=True)
        related.discard(module)

        # 写入 SQLite（先写关系库，成功后写向量库）
        cb(85, "写入业务数据...")
        doc_id = _safe_doc_id("axure", file_name, module)
        _save_to_sqlite(
            doc_id=doc_id,
            file_name=file_name,
            file_type="zip",
            doc_type="axure",
            chunk_count=len(chunks),
            module_name=module,
        )
        logger.info(f"   [SQLite] 入库完成 (doc_id={doc_id})")

        # 写入 ChromaDB（纯向量，失败时补偿回滚 SQLite）
        cb(90, "向量化入库中...")
        try:
            db.delete_by_doc_id(doc_id)
            db.add_product_doc_chunks(doc_id, chunks)
            logger.info(f"   [ChromaDB] 入库完成 (doc_id={doc_id}), {len(chunks)} 块")
        except Exception:
            logger.error("   [ChromaDB] 写入失败，启动补偿回滚 SQLite", exc_info=True)
            _delete_sqlite_doc(doc_id)
            raise

        cb(95, "入库完成")
        return {"doc_id": doc_id, "module_name": module, "chunks": len(chunks)}
    finally:
        parser.cleanup()


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="文档入库工具（CLI）")
    parser.add_argument("path", help="文档路径")
    parser.add_argument("--type", choices=["product", "api", "axure"], default="product",
                        help="文档类型（默认 product）")
    parser.add_argument("--module", default=None, help="所属模块名（可选）")
    args = parser.parse_args()

    if args.type == "api":
        result = process_api_doc(args.path, default_module=args.module)
    elif args.type == "axure":
        result = process_axure_zip(args.path, module_name=args.module)
    else:
        result = process_product_doc(args.path)

    logger.info(f"\n结果: {result}")
