"""五大流程入口编排（product / api / axure）。"""

import os
import re
from pathlib import Path

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

from ingest.extractors import _extract_text, _docx_img_dir, _safe_doc_id
from ingest.storage import (
    _delete_sqlite_doc,
    _save_to_sqlite,
    _save_single_chunk,
    _save_document_chunks,
    _delete_document_chunks,
)
from ingest.api_parser import (
    _merge_api_defs,
    _extract_valid_api_paths,
    _split_text_by_headers,
    _coerce_api_format,
)
from ingest.chunking import (
    _generate_batch_summaries,
    _build_api_search_text,
    _build_doc_search_text,
)

logger = get_logger(__name__)


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

        # 5. 写入 SQLite document_chunks 表（原文 + 摘要占位）
        cb(60, "写入文档块...")
        doc_id = _safe_doc_id("prod", file_name, module_name)
        _save_document_chunks(doc_id, chunks, page_name="")
        # 写 documents 表（元数据）
        _save_to_sqlite(
            doc_id=doc_id,
            file_name=file_name,
            file_type=file_type,
            doc_type="product",
            chunk_count=len(chunks),
            module_name=module_name,
            glossary_terms=terms,
        )
        logger.info(f"   [SQLite] document_chunks + documents 入库完成 (doc_id={doc_id})")

        # 6. 批量生成 simple_summary（同步等待，5 chunks/批）
        cb(70, "AI 生成摘要...")
        _generate_batch_summaries(doc_id, chunks, file_name, progress_cb=cb)

        # 7. 写入 ChromaDB（检索文本，失败时补偿回滚 SQLite）
        cb(90, "向量化入库中...")
        try:
            from database import get_session_ctx
            from database.models import DocumentChunk
            # 从 document_chunks 读摘要构造检索文本
            chunk_records = []
            with get_session_ctx() as session:
                chunk_records = session.query(DocumentChunk).filter_by(
                    doc_id=doc_id).order_by(DocumentChunk.chunk_index).all()
                # detach from session
                chunk_records = [{
                    "content": c.content,
                    "simple_summary": c.simple_summary,
                    "page_name": c.page_name,
                } for c in chunk_records]

            search_texts = [_build_doc_search_text(c) for c in chunk_records]
            db.delete_by_doc_id(doc_id)
            db.add_product_doc_chunks(doc_id, search_texts)
            logger.info(f"   [ChromaDB] 入库完成 (doc_id={doc_id}, 检索文本)")
        except Exception:
            logger.error("   [ChromaDB] 写入失败，启动补偿回滚 SQLite", exc_info=True)
            _delete_sqlite_doc(doc_id)
            _delete_document_chunks(doc_id)
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
    total = len(batches)
    done = [0]  # 列表包装，实现跨线程计数

    import concurrent.futures
    from threading import Lock
    _lock = Lock()

    def _extract_one(batch_text: str) -> tuple:
        """单个 API 提取（在线程池中并发执行）。"""
        prompt = api_def_extract_prompt()
        result = graph._invoke_structured(
            prompt, ApiDefExtract, method="json_mode", doc_text=batch_text,
        )
        mod = result.module_name
        apis_raw = result.apis if hasattr(result, "apis") else []
        apis = [
            _coerce_api_format(a.model_dump(by_alias=True) if hasattr(a, "model_dump") else a)
            for a in apis_raw
        ]
        with _lock:
            done[0] += 1
            pct = int(10 + (done[0] / total) * 70)
            cb(pct, f"AI 提取接口定义 ({done[0]}/{total})...")
        return mod, apis

    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        futures = [executor.submit(_extract_one, b) for b in batches]
        for f in concurrent.futures.as_completed(futures):
            mod, apis = f.result()
            if not module:
                module = mod
            all_apis.extend(apis)

    # ---- D1: 提取后统一规范化 url（白名单/合并去重/入库共用同形）----
    from agent_components.api_annotations import normalize_api_url
    for a in all_apis:
        a["url"] = normalize_api_url(a.get("url", ""))

    # ---- 白名单校验：过滤 LLM 幻觉的接口 ----
    # 扫描原始文档，提取所有 **Path：** + **Method：** 对作为白名单。
    # LLM 可能因参数字段名（如 deviceStatus→编造 /device/info）或记忆污染
    # （跨项目接口混淆）而输出不存在的接口，白名单提供独立防线。
    valid_paths = _extract_valid_api_paths(full_text)
    if valid_paths:
        before = len(all_apis)
        hallucinated = []
        filtered_apis = []
        for a in all_apis:
            key = (a.get("method", "").strip().upper(), a.get("url", "").rstrip("/"))
            if key in valid_paths:
                filtered_apis.append(a)
            else:
                hallucinated.append(key)
        all_apis = filtered_apis
        removed = before - len(all_apis)
        if removed:
            logger.warning(
                "⚠️ 白名单校验: 过滤 %d 个文档中不存在的接口: %s",
                removed, [f"{m} {u}" for m, u in hallucinated[:15]],
            )
    # ---- 白名单校验结束 ----

    # 合并去重：method+url 相同时合并参数和返回值，而非简单覆盖
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

    所有 API 先批量写入 SQLite（同一事务，含 api_* 结构化列），再逐条写入 ChromaDB。
    ChromaDB 写入检索文本（自然语言），不再存原始 JSON。
    ChromaDB 任一条失败时补偿回滚所有 SQLite 记录。
    仅 delete_original=True 时删除原文件。
    """
    import json as _json
    cb = progress_cb or (lambda p, m: None)
    logger.info(f"[Phase A] 入库 {len(apis)} 个接口文档")

    # D1: 写入前统一规范化 url（SQLite api_url / file_name / doc_id / 检索文本共用同形）
    from agent_components.api_annotations import normalize_api_url
    for api in apis:
        api["url"] = normalize_api_url(api.get("url", ""))

    db = get_chroma_db()
    file_name = os.path.basename(file_path)
    file_type = os.path.splitext(file_path)[1].lstrip(".")
    # 归一化为新结构（幂等；兼容前端缓存的旧格式）
    apis = [_coerce_api_format(a) for a in apis]
    doc_ids = []

    # ---- Phase 1: 批量写入 SQLite（同一事务，含 api_* 结构化列）----
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
        # ★ 自动标注 API 异常标识（is_export / has_path_params 等）
        from agent_components.api_annotations import ApiAnnotationRegistry
        ApiAnnotationRegistry.apply_all(api)
        doc_ids.append(doc_id)
        docs_to_insert.append(Document(
            id=doc_id,
            file_name=f"{api.get('method', '?')} {api.get('url', '')}",
            file_type=file_type,
            doc_type="api",
            chunk_count=1,
            status="pending",
            upload_time=datetime.now(timezone.utc),
            # ── API 结构化列 ──
            api_name=api_name,
            api_url=url,
            api_method=method.upper() if method else "?",
            api_description=api.get("description", api_name),
            api_headers=_json.dumps(api.get("header", {}), ensure_ascii=False),
            api_parameters=_json.dumps(api.get("body", []), ensure_ascii=False),
            api_returns=_json.dumps(api.get("return", []), ensure_ascii=False),
            api_annotations=_json.dumps(api.get("annotations", {}), ensure_ascii=False),
        ))

    try:
        with get_session_ctx() as session:
            for d in docs_to_insert:
                session.merge(d)
    except Exception:
        logger.error("   [SQLite] 批量写入失败，无数据需要补偿", exc_info=True)
        raise

    logger.info(f"   [SQLite] 批量入库完成: {len(doc_ids)} 条（含 api_* 结构化列）")

    # ---- Phase 2: 逐条写入 ChromaDB（检索文本，失败时补偿回滚 SQLite）----
    try:
        for i, api in enumerate(apis):
            api_name = api.get("name", f"api_{i}")
            url = api.get("url", "")
            method = api.get("method", "?")
            doc_id = doc_ids[i]
            pct = int(50 + (i / len(apis)) * 40)
            cb(pct, f"向量化入库 {i+1}/{len(apis)}...")

            # 构造检索文本并写入 ChromaDB
            search_text = _build_api_search_text(api)
            # 包装为兼容现有 add_api_defs 的格式：单个 dict 含检索文本
            api_for_chroma = dict(api)
            api_for_chroma["_search_text"] = search_text
            api_for_chroma["name"] = api_name
            api_for_chroma["_doc_id"] = doc_id

            db.delete_by_doc_id(doc_id)
            db.add_api_defs(doc_id, [api_for_chroma])
    except Exception:
        logger.error("   [ChromaDB] 写入失败，启动补偿回滚所有 SQLite 记录", exc_info=True)
        for did in doc_ids:
            _delete_sqlite_doc(did)
        raise

    logger.info(f"   [ChromaDB] 入库完成: {len(doc_ids)} 条（检索文本）")

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

        # ── 提取每块 page_name（从 "## 页面: xxx" 行中解析）──
        import re as _re
        def _extract_page_name(chunk: str) -> str:
            m = _re.search(r'##\s*页面[：:]\s*(.+)', chunk)
            return m.group(1).strip() if m else ""

        # 5a. 写入 SQLite document_chunks 表（含 page_name）
        cb(75, "写入文档块...")
        doc_id = _safe_doc_id("axure", file_name, module)
        # to_product_doc_chunks 返回 list[dict]（{content, page_name}），统一解包为字符串
        # （2026-08-03 修复：原代码把 dict 当 str 传给 _extract_page_name → re 报
        #   "expected string or bytes-like object, got 'dict'"）
        chunk_texts = [
            c.get("content", c) if isinstance(c, dict) else c for c in chunks
        ]
        chunk_pages = [
            c.get("page_name", "") if isinstance(c, dict) else _extract_page_name(c)
            for c in chunks
        ]
        # 幂等：重导同一 zip 前先清空旧块，避免 document_chunks 重复堆积
        #   （旧实现直接 append，重复导入 225 行 vs 应有 85 行）
        _delete_document_chunks(doc_id)
        for i, content in enumerate(chunk_texts):
            _save_single_chunk(doc_id, i, content, page_name=chunk_pages[i])

        # 5a2. 页面块 → 术语表（复用 glossary 展示；kind=required/filter/explanation）
        # notes 用 page_path（RP9 树含父级，如 企业预付费管理/企业公摊生成），区分同名页
        glossary_terms = []
        for url, detail in page_details.items():
            page_path = detail.get("page_path") or detail.get("page_name") or url
            # ② 必填字段
            for rf in detail.get("required_fields", []):
                field = (rf.get("field") or "").strip()
                if field:
                    glossary_terms.append({
                        "term": field,
                        "definition": "必填",
                        "kind": "required",
                        "notes": page_path,
                    })
            # ③ 筛选项（definition = 选项以 \ 分隔）
            for f in detail.get("filters", []):
                field = (f.get("field") or "").strip()
                options = [o for o in f.get("options", []) if o]
                if field and options:
                    glossary_terms.append({
                        "term": field,
                        "definition": "\\".join(options),
                        "kind": "filter",
                        "notes": page_path,
                    })
            # ④ 页面说明（整体复制，保留原结构）
            expl = (detail.get("page_explanation") or "").strip()
            if expl:
                glossary_terms.append({
                    "term": page_path,
                    "definition": expl,
                    "kind": "explanation",
                    "notes": page_path,
                })
            # 弹窗子块 → 术语（notes=块标题，含弹窗名，如 电表管理/添加）
            for d in detail.get("dialogs", []):
                d_title = d.get("title") or f"{page_path}/{d.get('state', '')}"
                for rf in d.get("required_fields", []):
                    field = (rf.get("field") or "").strip()
                    if field:
                        glossary_terms.append({
                            "term": field,
                            "definition": "必填",
                            "kind": "required",
                            "notes": d_title,
                        })
                for f in d.get("filters", []):
                    field = (f.get("field") or "").strip()
                    options = [o for o in f.get("options", []) if o]
                    if field and options:
                        glossary_terms.append({
                            "term": field,
                            "definition": "\\".join(options),
                            "kind": "filter",
                            "notes": d_title,
                        })
                d_expl = (d.get("explanation") or "").strip()
                if d_expl:
                    glossary_terms.append({
                        "term": d_title,
                        "definition": d_expl,
                        "kind": "explanation",
                        "notes": d_title,
                    })
        if glossary_terms:
            from collections import Counter
            _kind_counts = ", ".join(f"{k}={v}" for k, v in Counter(t["kind"] for t in glossary_terms).items())
            logger.info(f"   => 页面块术语: {len(glossary_terms)} 条（{_kind_counts} / {len(page_details)} 页）")

        # 5b. 写入 documents 元数据
        _save_to_sqlite(
            doc_id=doc_id,
            file_name=file_name,
            file_type="zip",
            doc_type="axure",
            chunk_count=len(chunk_texts),
            module_name=module,
            glossary_terms=glossary_terms,
        )
        logger.info(f"   [SQLite] document_chunks + documents 入库完成 (doc_id={doc_id})")

        # 6. 批量生成 simple_summary（同步等待，失败写补偿任务）
        cb(80, "AI 生成摘要...")
        _generate_batch_summaries(doc_id, chunk_texts, file_name,
                                   progress_cb=cb,
                                   page_name=chunk_pages[0] if chunk_texts else "")

        # 7. 写入 ChromaDB（检索文本，失败时补偿回滚 SQLite）
        cb(90, "向量化入库中...")
        try:
            from database import get_session_ctx
            from database.models import DocumentChunk
            chunk_records = []
            with get_session_ctx() as session:
                chunk_records = session.query(DocumentChunk).filter_by(
                    doc_id=doc_id).order_by(DocumentChunk.chunk_index).all()
                chunk_records = [{
                    "content": c.content,
                    "simple_summary": c.simple_summary,
                    "page_name": c.page_name,
                } for c in chunk_records]
            search_texts = [_build_doc_search_text(c) for c in chunk_records]
            db.delete_by_doc_id(doc_id)
            db.add_product_doc_chunks(doc_id, search_texts, doc_type="axure")
            logger.info(f"   [ChromaDB] 入库完成 (doc_id={doc_id}), {len(chunks)} 块（检索文本）")
        except Exception:
            logger.error("   [ChromaDB] 写入失败，启动补偿回滚 SQLite", exc_info=True)
            _delete_sqlite_doc(doc_id)
            _delete_document_chunks(doc_id)
            raise

        # 8. 兜底页面内嵌图片：仅无可提取内容且 HTML 内有 <img> 的页面复制图片
        #    （文件本身无图片 → 置空，不生成记录；失败静默降级不阻断入库）
        cb(92, "提取页面内嵌图片...")
        try:
            _img_n = _save_embedded_page_images(doc_id, page_details, file_path)
            if _img_n:
                logger.info(f"   [图片] 兜底页面内嵌图片入库 {_img_n} 张（data/page_images/{doc_id}/）")
        except Exception as e:
            logger.warning("   [图片] 页面内嵌图片处理失败（静默降级）: %s", e, exc_info=True)

        cb(95, "入库完成")
        return {"doc_id": doc_id, "module_name": module, "chunks": len(chunks)}
    finally:
        parser.cleanup()


def _page_has_extractable_content(detail: dict) -> bool:
    """页面是否有可提取文本内容（②必填 / ③筛选 / ④说明 任一非空）。

    页面内嵌图片仅作兜底：页面无可提取内容时才展示页面本身的图片。
    """
    return bool(detail.get("required_fields") or detail.get("filters")
                or (detail.get("page_explanation") or "").strip())


def _locate_embedded_image(root: Path, src: str) -> Path | None:
    """在解压目录中定位 zip 内图片文件（src 相对项目根，后缀匹配）。

    找不到返回 None。
    """
    src_norm = src.replace("\\", "/").lstrip("/")
    for p in root.rglob("*"):
        if p.is_file() and p.as_posix().replace("\\", "/").endswith(src_norm):
            return p
    return None


def _save_embedded_page_images(doc_id: str, page_details: dict, zip_path: str) -> int:
    """兜底提取页面内嵌图片：仅**无可提取内容**的页面，其 HTML 内 <img> 原图字节直存 SQLite。

    存储：PageImage.image_data 存原图字节（多模态可直接取图分析），image_path 仅存原文件名。
    重导时先删该 doc_id 旧记录与旧图文件，再写入新记录。
    文件本身无图片 → 置空，不生成任何记录。
    失败静默降级（仅记日志），不阻断入库。返回成功保存图片数。
    """
    import shutil
    import tempfile
    import zipfile
    from pathlib import Path
    from urllib.parse import unquote

    from database import get_session_ctx
    from database.models import PageImage

    # 仅需兜底图片：无可提取内容（②③④ 全空）且 HTML 内嵌图片非空的页面
    targets = []
    for url, d in page_details.items():
        if not _page_has_extractable_content(d) and d.get("embedded_images"):
            targets.append((url, d))
    if not targets:
        return 0

    out_dir = os.path.join(config.PAGE_IMAGES_DIR, doc_id)
    os.makedirs(out_dir, exist_ok=True)

    # 幂等清理：删除旧记录与旧图（重导覆盖）
    try:
        with get_session_ctx() as session:
            for rec in session.query(PageImage).filter_by(doc_id=doc_id).all():
                session.delete(rec)
            session.commit()
    except Exception as e:
        logger.warning("   [图片] 清理旧 page_images 失败: %s", e)
    for f in os.listdir(out_dir):
        p = os.path.join(out_dir, f)
        if os.path.isfile(p):
            try:
                os.remove(p)
            except OSError:
                pass

    tmp = tempfile.mkdtemp(prefix="axure_img_")
    saved = 0
    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(tmp)
        root = Path(tmp)

        from agent_components.axure_parser import AxureParser
        records = []
        for url, detail in targets:
            html_path = AxureParser._find_page_html_impl(root, unquote(url))
            if html_path is None:
                logger.warning("   [图片] 未找到页面 HTML: %s", unquote(url))
                continue
            page_path = detail.get("page_path") or detail.get("page_name") or url
            used_names = set()
            for src in detail.get("embedded_images", []):
                img_file = _locate_embedded_image(root, src)
                if img_file is None:
                    logger.warning("   [图片] 图片未找到: %s", src)
                    continue
                # 原名写入，重名追加序号
                base = Path(src).name or "image"
                fname, ext = os.path.splitext(base)
                candidate = base
                idx = 1
                while candidate in used_names:
                    candidate = f"{fname}_{idx}{ext}"
                    idx += 1
                used_names.add(candidate)
                # 原图字节直存 SQLite（多模态可直接取字节分析补提字段）
                try:
                    image_bytes = img_file.read_bytes()
                except OSError as e:
                    logger.warning("   [图片] 读取字节失败 %s: %s", img_file, e)
                    continue
                if not image_bytes:
                    logger.warning("   [图片] 空文件跳过 %s", img_file)
                    continue
                records.append({
                    "doc_id": doc_id,
                    "page_path": page_path,
                    "page_url": url,
                    # image_path 仅作原文件名（重名去重 / 展示 alt），字节在 image_data
                    "image_path": candidate,
                    "image_data": image_bytes,
                })
                saved += 1

        if records:
            with get_session_ctx() as session:
                for r in records:
                    session.add(PageImage(**r))
                session.commit()
        return saved
    except Exception as e:
        logger.warning("   [图片] 页面内嵌图片处理失败（静默降级）: %s", e)
        return saved
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
