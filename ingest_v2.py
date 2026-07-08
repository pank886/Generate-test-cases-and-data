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


def _extract_text(file_path: str) -> str:
    """通用文本提取（支持 PDF/MD/TXT/DOCX）。"""
    ext = os.path.splitext(file_path)[1].lower()
    if ext == ".pdf":
        from pypdf import PdfReader
        reader = PdfReader(file_path)
        texts = [p.extract_text() for p in reader.pages if p.extract_text()]
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
    for rel in doc.part.rels.values():
        if "image" in str(rel.reltype):
            img_index += 1
            # 保存图片到临时目录（供后续多模态模型使用）
            img_data = rel.target_part.blob
            img_dir = os.path.join(os.path.dirname(file_path), "_images")
            os.makedirs(img_dir, exist_ok=True)
            img_path = os.path.join(img_dir, f"{os.path.basename(file_path)}_{img_index}.png")
            with open(img_path, "wb") as f:
                f.write(img_data)
            parts.append(f"[图片_{img_index}: {os.path.basename(img_path)}]")

    result = "\n\n".join(parts)
    if img_index > 0:
        result += f"\n\n[本文档包含 {img_index} 张图片，已保存至 {os.path.basename(os.path.dirname(file_path))}/_images/ 目录]"
    return result


def _safe_doc_id(prefix: str, *parts: str) -> str:
    """Sanitize and join parts into a safe doc_id."""
    sanitized = [p.replace('/', '_').replace('\\', '_').replace('$', '_') for p in parts if p]
    if sanitized:
        return prefix + "_" + "_".join(sanitized)
    return prefix


def _cascade_bind_to_module_docs(session, doc_type: str, doc_id: str, module_name: str):
    """级联关联：文档绑定模块时，自动与该模块下所有异类文档建立 doc↔doc 绑定。"""
    from database.operations import BindingOps
    bound_docs = BindingOps.get_bound_docs(session, module_name)
    for other_doc in bound_docs:
        if other_doc.doc_type != doc_type and other_doc.id != doc_id:
            BindingOps.bind(session, doc_type, doc_id, other_doc.doc_type, other_doc.id)


def _save_to_sqlite(doc_id: str, file_name: str, file_type: str, doc_type: str,
                    chunk_count: int, module_name: str = "",
                    glossary_terms: list = None):
    """写入 SQLite：文档记录 + 术语。

    在 ChromaDB 写入后调用，保证双库数据一致。
    module_name 仅用于日志，不做自动绑定（由用户在前端手动关联）。
    """
    from database import get_session
    from database.operations import DocOps, GlossaryOps

    session = get_session()
    try:
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

        session.commit()
    except Exception:
        session.rollback()
        logger.error("   [SQLite] 写入失败", exc_info=True)
        raise
    finally:
        session.close()


def process_product_doc(file_path: str, progress_cb=None) -> dict:
    """处理产品文档：提取文本 -> LLM 提取模块关联 -> 存入 product_docs + SQLite。

    Args:
        progress_cb: 可选，进度回调 (0~100, message)
    """
    cb = progress_cb or (lambda p, m: None)
    logger.info(f"\n{'=' * 60}")
    logger.info(f"[Phase A] 处理产品文档: {os.path.basename(file_path)}")

    db = get_chroma_db()
    graph = ChatTestAgentGraph(db_path=None)
    file_name = os.path.basename(file_path)
    file_type = os.path.splitext(file_path)[1].lstrip(".")

    # 1. 提取文本
    cb(5, "提取文本中...")
    full_text = _extract_text(file_path).strip()
    if not full_text:
        raise ValueError("文档内容为空")
    logger.info(f"   => 提取文本 {len(full_text)} 字符")

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
    def _chunk_batches(chunks: list[str], max_chars: int) -> list[str]:
        """将 chunks 拼接为每批不超过 max_chars 的文本段。"""
        out = []
        buf_parts = []
        buf_len = 0
        sep_len = len("\n\n")
        for c in chunks:
            c_len = len(c)
            if buf_len + c_len + (sep_len if buf_parts else 0) > max_chars and buf_parts:
                out.append("\n\n".join(buf_parts))
                buf_parts = [c]
                buf_len = c_len
            else:
                buf_parts.append(c)
                buf_len += c_len + (sep_len if len(buf_parts) > 1 else 0)
        if buf_parts:
            out.append("\n\n".join(buf_parts))
        return out

    batch_limit = config.MAX_INGEST_CHARS_PER_BATCH
    text_batches = _chunk_batches(chunks, batch_limit) if len(full_text) > batch_limit else [full_text]
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
        logger.info(f"   => 术语表提取跳过: {e}")

    # 5. 写入 ChromaDB（纯向量，不含 module/related_modules）
    cb(85, "向量化入库中...")
    doc_id = _safe_doc_id("prod", file_name, module_name)
    db.delete_by_doc_id(doc_id)
    db.add_product_doc_chunks(doc_id, chunks)
    logger.info(f"   [ChromaDB] 入库完成 (doc_id={doc_id})")

    # 6. 写入 SQLite（文档元数据 + 绑定关系 + 术语）
    cb(90, "写入业务数据...")
    _save_to_sqlite(
        doc_id=doc_id,
        file_name=file_name,
        file_type=file_type,
        doc_type="product",
        chunk_count=len(chunks),
        module_name=module_name,
        glossary_terms=terms,
    )
    logger.info(f"   [SQLite] 入库完成")

    cb(95, "入库完成")
    return {
        "doc_id": doc_id,
        "module_name": module_name,
        "related_modules": related_list,
        "chunks": len(chunks),
    }


def _split_text_by_headers(text: str, max_chars: int) -> list:
    """按 # 标题切分文本，每段不超过 max_chars 字符。"""
    import re
    parts = re.split(r'(?=\n# )', text)
    batches = []
    current = ""
    for part in parts:
        part = part.strip()
        if not part:
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
    """处理接口文档：提取文本 -> 分批 LLM 提取接口 -> 合并去重 -> 存入 api_defs + SQLite。

    Args:
        default_module: 调用方指定的默认模块（如从产品文档继承）
        progress_cb: 可选，进度回调 (0~100, message)
    """
    cb = progress_cb or (lambda p, m: None)
    logger.info(f"\n{'=' * 60}")
    logger.info(f"[Phase A] 处理接口文档: {os.path.basename(file_path)}")

    db = get_chroma_db()
    graph = ChatTestAgentGraph(db_path=None)
    file_name = os.path.basename(file_path)
    file_type = os.path.splitext(file_path)[1].lstrip(".")

    cb(5, "读取文档...")
    full_text = _extract_text(file_path).strip()
    if not full_text:
        raise ValueError("文档内容为空")
    logger.info(f"   => 提取文本 {len(full_text)} 字符")

    # 大文档分批处理
    batch_limit = config.MAX_INGEST_CHARS_PER_BATCH
    batches = _split_text_by_headers(full_text, batch_limit) if len(full_text) > batch_limit else [full_text]
    logger.info(f"   => 分为 {len(batches)} 批处理（每批 ≤ {batch_limit} 字符）")

    all_apis = []
    module = default_module

    for bi, batch_text in enumerate(batches, 1):
        pct = int(10 + (bi / len(batches)) * 70)  # 10%~80% 按批次比例
        cb(pct, f"AI 提取接口定义 ({bi}/{len(batches)})...")
        prompt = api_def_extract_prompt()
        logger.info(f"   [{bi}/{len(batches)}] LLM 提取接口...")
        result = graph._invoke_structured(
            prompt, ApiDefExtract,
            method="json_mode",
            doc_text=batch_text,
        )
        if not module:
            module = result.module_name
        apis_raw = result.apis if hasattr(result, "apis") else []
        # Pydantic 对象转 dict
        apis = [a.model_dump() if hasattr(a, "model_dump") else a for a in apis_raw]
        logger.info(f"   [{bi}/{len(batches)}] 提取到 {len(apis)} 个接口")
        all_apis.extend(apis)

    # URL 去重（后出现的覆盖先出现的）
    cb(85, "去重合并...")
    seen = {}
    for api in all_apis:
        key = f"{api.get('method', '')} {api.get('url', '')}"
        seen[key] = api
    apis = list(seen.values())

    module = module or "Unknown"
    logger.info(f"   => 汇总: 模块={module}, 接口数={len(apis)}（去重后）")

    for a in apis:
        logger.info(f"      - {a.get('method', '?')} {a.get('url', '')}")

    # 写入 ChromaDB（纯向量，不含 module）
    cb(90, "向量化入库中...")
    doc_id = _safe_doc_id("api", file_name, module)
    db.delete_by_doc_id(doc_id)
    db.add_api_defs(doc_id, apis)
    logger.info(f"   [ChromaDB] 入库完成 (doc_id={doc_id})")

    # 写入 SQLite（文档元数据 + 绑定关系）
    cb(92, "写入业务数据...")
    _save_to_sqlite(
        doc_id=doc_id,
        file_name=file_name,
        file_type=file_type,
        doc_type="api",
        chunk_count=len(apis),
        module_name=module,
    )
    logger.info(f"   [SQLite] 入库完成")

    cb(95, "入库完成")
    return {"doc_id": doc_id, "module_name": module, "api_count": len(apis)}


def process_api_doc_extract(file_path: str, default_module: str = None,
                             progress_cb=None) -> dict:
    """Phase 1: 提取接口列表（不入库），返回给前端确认。

    Returns: {"module_name": str, "apis": [dict], "file_name": str}
    """
    cb = progress_cb or (lambda p, m: None)
    logger.info(f"[Phase A] 提取接口: {os.path.basename(file_path)}")

    graph = ChatTestAgentGraph(db_path=None)
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

    # URL 去重
    seen = {}
    for api in all_apis:
        key = f"{api.get('method', '')} {api.get('url', '')}"
        seen[key] = api
    apis = list(seen.values())

    module = module or "Unknown"
    return {"module_name": module, "apis": apis, "file_name": file_name}


def commit_api_docs(file_path: str, module_name: str, apis: list[dict],
                    progress_cb=None, delete_original: bool = False) -> dict:
    """Phase 2: 用户确认后，每个接口独立入库 + 级联关联。

    为每个 API 创建独立的 documents 行和 ChromaDB 向量。
    仅 delete_original=True 时删除原文件。
    """
    cb = progress_cb or (lambda p, m: None)
    logger.info(f"[Phase A] 入库 {len(apis)} 个接口文档")

    db = get_chroma_db()
    file_name = os.path.basename(file_path)
    file_type = os.path.splitext(file_path)[1].lstrip(".")
    doc_ids = []

    for i, api in enumerate(apis):
        api_name = api.get("name", f"api_{i}")
        # doc_id 必须包含 method+url 才能保证唯一性，纯用 name 会导致
        # 同名接口（如多个 GET 接口都叫"查询"）后写入的覆盖前面的
        url = api.get("url", "")
        method = api.get("method", "?")
        doc_id = _safe_doc_id("api", file_name, module_name, method, url, api_name)

        cb(int(10 + (i / len(apis)) * 80), f"入库 {api.get('method', '?')} {api.get('url', '')}")

        # ChromaDB: 每个接口一个文档
        db.delete_by_doc_id(doc_id)
        db.add_api_defs(doc_id, [api])

        # SQLite: 每个接口一条记录
        _save_to_sqlite(
            doc_id=doc_id,
            file_name=f"{api.get('method', '?')} {api.get('url', '')}",
            file_type=file_type,
            doc_type="api",
            chunk_count=1,
            module_name=module_name,
        )
        doc_ids.append(doc_id)

    # 仅当全部接口选中时才废弃原文件
    if delete_original:
        try:
            os.remove(file_path)
            meta_path = file_path + ".meta.json"
            if os.path.exists(meta_path):
                os.remove(meta_path)
            logger.info(f"   => 已删除原文件: {file_name}")
        except OSError:
            pass
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
        graph = ChatTestAgentGraph(db_path=None)
        related = set()
        try:
            from prompts.extraction_prompts import product_doc_extract_prompt
            from prompts.response_model import DocModuleExtract
            prompt = product_doc_extract_prompt()
            # 取页面详情文本拼接，控制在单批上限内
            detail_text = "\n".join(
                f"[{url}] {detail.get('ui_text', '')}"
                for url, detail in list(page_details.items())[:50]
            )
            batch_limit = config.MAX_INGEST_CHARS_PER_BATCH
            result = graph._invoke_structured(
                prompt, DocModuleExtract,
                method="json_mode",
                doc_text=detail_text[:batch_limit],
            )
            related = set(result.related_modules or [])
            logger.info(f"   => LLM 识别关联模块: {related}")
        except Exception as e:
            logger.warning(f"   => 关联模块分析跳过: {e}")
        related.discard(module)

        # 写入 ChromaDB（纯向量，不含 module/related_modules）
        cb(85, "向量化入库中...")
        doc_id = _safe_doc_id("axure", file_name, module)
        db.delete_by_doc_id(doc_id)
        db.add_product_doc_chunks(doc_id, chunks)
        logger.info(f"   [ChromaDB] 入库完成 (doc_id={doc_id}), {len(chunks)} 块")

        # 写入 SQLite（文档元数据 + 绑定关系）
        cb(90, "写入业务数据...")
        _save_to_sqlite(
            doc_id=doc_id,
            file_name=file_name,
            file_type="zip",
            doc_type="axure",
            chunk_count=len(chunks),
            module_name=module,
        )
        logger.info(f"   [SQLite] 入库完成")

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
