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
import sys

from langchain_text_splitters import RecursiveCharacterTextSplitter

from observability import get_logger
from agent_components.dual_chroma import DualChromaDB
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


def _save_to_sqlite(doc_id: str, file_name: str, file_type: str, doc_type: str,
                    chunk_count: int, module_name: str = "",
                    related_modules: list = None,
                    glossary_terms: list = None):
    """写入 SQLite：文档记录 + 绑定关系 + 术语。

    在 ChromaDB 写入后调用，保证双库数据一致。
    """
    from database import get_session
    from database.operations import DocOps, GlossaryOps, BindingOps

    session = get_session()
    try:
        # 1. 文档记录（幂等：先删旧数据再写入）
        existing = DocOps.get_document(session, doc_id)
        if existing:
            DocOps.delete_document(session, doc_id)
            session.flush()

        DocOps.add_document(
            session,
            doc_id=doc_id,
            file_name=file_name,
            file_type=file_type,
            doc_type=doc_type,
            chunk_count=chunk_count,
            status="pending",
        )

        # 2. 术语（如果提供了）
        if glossary_terms:
            GlossaryOps.replace_terms(
                session, doc_id, glossary_terms,
                source_doc=file_name,
            )

        # 3. 绑定关系到模块
        if module_name:
            BindingOps.bind(session, doc_type, doc_id, "module", module_name)
        for rel_mod in (related_modules or []):
            if rel_mod and rel_mod != module_name:
                BindingOps.bind(session, doc_type, doc_id, "module", rel_mod)

        session.commit()
    except Exception as e:
        session.rollback()
        logger.warning(f"   [SQLite] 写入失败: {e}")
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

    db = DualChromaDB()
    graph = ChatTestAgentGraph(db_path=None)
    file_name = os.path.basename(file_path)
    file_type = os.path.splitext(file_path)[1].lstrip(".")

    # 1. 提取文本
    cb(5, "提取文本中...")
    full_text = _extract_text(file_path).strip()
    if not full_text:
        raise ValueError("文档内容为空")
    logger.info(f"   => 提取文本 {len(full_text)} 字符")

    # 2. LLM 提取模块信息
    cb(20, "AI 分析模块信息...")
    prompt = product_doc_extract_prompt()
    logger.info("   => LLM 提取模块信息...")
    result = graph._invoke_structured(
        prompt, DocModuleExtract,
        method="json_mode",
        doc_text=full_text[:4000],
    )
    module_name = result.module_name
    related = result.related_modules or []
    logger.info(f"   => 模块: {module_name}, 关联: {related}")

    # 2b. LLM 提取业务术语表
    cb(50, "AI 提取术语表...")
    from prompts.response_model import GlossaryExtract
    from prompts.extraction_prompts import glossary_extract_prompt
    terms = []
    try:
        glossary_prompt = glossary_extract_prompt()
        glossary_result = graph._invoke_structured(
            glossary_prompt, GlossaryExtract,
            method="json_mode",
            doc_text=full_text[:4000],
        )
        terms = glossary_result.terms if hasattr(glossary_result, "terms") else []
        if terms:
            logger.info(f"   => 术语表: {len(terms)} 条")
    except Exception as e:
        logger.info(f"   => 术语表提取跳过: {e}")

    # 3. 切分
    cb(70, "文本切分中...")
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=config.CHUNK_SIZE,
        chunk_overlap=config.CHUNK_OVERLAP,
        separators=["\n\n", "\n", "。", "，", " "],
    )
    chunks = splitter.split_text(full_text)
    logger.info(f"   => 切分为 {len(chunks)} 个文本块")

    # 4. 写入 ChromaDB（纯向量，不含 module/related_modules）
    cb(85, "向量化入库中...")
    doc_id = f"prod_{file_name}_{module_name}"
    db.delete_by_doc_id(doc_id)
    db.add_product_doc_chunks(doc_id, chunks)
    logger.info(f"   [ChromaDB] 入库完成 (doc_id={doc_id})")

    # 5. 写入 SQLite（文档元数据 + 绑定关系 + 术语）
    cb(90, "写入业务数据...")
    _save_to_sqlite(
        doc_id=doc_id,
        file_name=file_name,
        file_type=file_type,
        doc_type="product",
        chunk_count=len(chunks),
        module_name=module_name,
        related_modules=related,
        glossary_terms=terms,
    )
    logger.info(f"   [SQLite] 入库完成")

    cb(95, "入库完成")
    return {
        "doc_id": doc_id,
        "module_name": module_name,
        "related_modules": related,
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

    db = DualChromaDB()
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
    doc_id = f"api_{file_name}_{module}"
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

    db = DualChromaDB()
    file_name = os.path.basename(file_path)

    cb(5, "解压 Axure 包...")
    parser = AxureParser(file_path)
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

    # 估计关联模块（从页面内容的关键词简单判断）
    cb(70, "分析关联模块...")
    related = set()
    for url, detail in page_details.items():
        text = (detail.get("ui_text", "") or "") + str(detail.get("interactions", []))
        for mod_keyword in ["管理", "模块", "系统设置", "报表"]:
            if mod_keyword in text:
                related.add(mod_keyword)
    related.discard(module)

    # 写入 ChromaDB（纯向量，不含 module/related_modules）
    cb(85, "向量化入库中...")
    doc_id = f"axure_{file_name}_{module}"
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
        related_modules=list(related),
    )
    logger.info(f"   [SQLite] 入库完成")

    cb(95, "入库完成")
    return {"doc_id": doc_id, "module_name": module, "chunks": len(chunks)}


if __name__ == "__main__":
    if len(sys.argv) < 2:
        logger.info("用法: python ingest_v2.py <文件路径> [--type product|api|axure] [--module 模块名]")
        sys.exit(1)

    path = sys.argv[1]
    doc_type = "product"
    module = None

    for i, arg in enumerate(sys.argv):
        if arg == "--type" and i + 1 < len(sys.argv):
            doc_type = sys.argv[i + 1]
        if arg == "--module" and i + 1 < len(sys.argv):
            module = sys.argv[i + 1]

    if doc_type == "api":
        result = process_api_doc(path, default_module=module)
    elif doc_type == "axure":
        result = process_axure_zip(path, module_name=module)
    else:
        result = process_product_doc(path)

    logger.info(f"\n结果: {result}")
