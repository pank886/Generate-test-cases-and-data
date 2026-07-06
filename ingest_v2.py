"""Phase A: 智能文档处理入口（替代旧 ingest_file.py 的单 Collection 流程）

使用方式：
    from ingest_v2 import process_product_doc, process_api_doc
    process_product_doc("uploads/doc.pdf")
    process_api_doc("uploads/api.md")
"""

import os
import sys

from langchain_text_splitters import RecursiveCharacterTextSplitter

from agent_components.dual_chroma import DualChromaDB
from agent_components.nodes import ChatTestAgentGraph
from prompts.response_model import DocModuleExtract, ApiDefExtract
from prompts.extraction_prompts import (
    product_doc_extract_prompt,
    api_def_extract_prompt,
)
import config


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
    from docx import Document
    doc = Document(file_path)

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


def process_product_doc(file_path: str) -> dict:
    """处理产品文档：提取文本 -> LLM 提取模块关联 -> 存入 product_docs。

    Returns:
        {"doc_id": ..., "module_name": ..., "related_modules": [...], "chunks": int}
    """
    print(f"\n{'=' * 60}")
    print(f"[Phase A] 处理产品文档: {os.path.basename(file_path)}")

    db = DualChromaDB()
    graph = ChatTestAgentGraph(db_path=None)

    # 1. 提取文本
    full_text = _extract_text(file_path).strip()
    if not full_text:
        raise ValueError("文档内容为空")
    print(f"   => 提取文本 {len(full_text)} 字符")

    # 2. LLM 提取模块信息
    prompt = product_doc_extract_prompt()
    print("   => LLM 提取模块信息...")
    result = graph._invoke_structured(
        prompt, DocModuleExtract,
        method="json_mode",
        doc_text=full_text[:4000],
    )
    module_name = result.module_name
    related = result.related_modules or []
    print(f"   => 模块: {module_name}, 关联: {related}")

    # 2b. LLM 提取业务术语表
    from prompts.response_model import GlossaryExtract
    from prompts.extraction_prompts import glossary_extract_prompt
    try:
        glossary_prompt = glossary_extract_prompt()
        glossary_result = graph._invoke_structured(
            glossary_prompt, GlossaryExtract,
            method="json_mode",
            doc_text=full_text[:4000],
        )
        terms = glossary_result.terms if hasattr(glossary_result, "terms") else []
        if terms:
            print(f"   => 术语表: {len(terms)} 条")
            glossary_text = "## 业务术语表\n" + "\n".join(
                f"- {t.get('term', '?')}: {t.get('definition', '')}"
                for t in terms if isinstance(t, dict)
            )
        else:
            glossary_text = ""
    except Exception as e:
        print(f"   => 术语表提取跳过: {e}")
        glossary_text = ""

    # 3. 切分
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=config.CHUNK_SIZE,
        chunk_overlap=config.CHUNK_OVERLAP,
        separators=["\n\n", "\n", "。", "，", " "],
    )
    chunks = splitter.split_text(full_text)
    if glossary_text:
        chunks.insert(0, glossary_text)
        print(f"   => 切分为 {len(chunks)} 个文本块（含术语表）")
    else:
        print(f"   => 切分为 {len(chunks)} 个文本块")

    # 4. 幂等入库
    doc_id = f"prod_{os.path.basename(file_path)}_{module_name}"
    db.delete_by_doc_id(doc_id)
    db.add_product_doc_chunks(doc_id, chunks, module_name, related)
    print(f"   [OK] 入库完成 (doc_id={doc_id})")

    return {
        "doc_id": doc_id,
        "module_name": module_name,
        "related_modules": related,
        "chunks": len(chunks),
    }


def process_api_doc(file_path: str, default_module: str = None) -> dict:
    """处理接口文档：提取文本 -> LLM 提取接口 -> 存入 api_defs。

    Args:
        default_module: 调用方指定的默认模块（如从产品文档继承）
    """
    print(f"\n{'=' * 60}")
    print(f"[Phase A] 处理接口文档: {os.path.basename(file_path)}")

    db = DualChromaDB()
    graph = ChatTestAgentGraph(db_path=None)

    full_text = _extract_text(file_path).strip()
    if not full_text:
        raise ValueError("文档内容为空")
    print(f"   => 提取文本 {len(full_text)} 字符")

    # LLM 提取接口
    prompt = api_def_extract_prompt()
    print("   => LLM 提取接口定义...")
    result = graph._invoke_structured(
        prompt, ApiDefExtract,
        method="json_mode",
        doc_text=full_text[:6000],
    )
    module = default_module or result.module_name
    apis = result.apis if hasattr(result, "apis") else []
    print(f"   => 模块: {module}, 接口数: {len(apis)}")

    for a in apis:
        print(f"      - {a.get('method', '?')} {a.get('url', '')}")

    # 幂等入库
    doc_id = f"api_{os.path.basename(file_path)}_{module}"
    db.delete_by_doc_id(doc_id)
    db.add_api_defs(doc_id, apis, module)
    print(f"   [OK] 入库完成 (doc_id={doc_id})")

    return {"doc_id": doc_id, "module_name": module, "api_count": len(apis)}


def process_axure_zip(file_path: str, module_name: str = None) -> dict:
    """处理 Axure HTML 演示包：解析页面树 + UI 文本 + 交互 -> 存入 product_docs。

    Args:
        file_path: Axure 导出的 .zip 文件路径
        module_name: 所属模块（如不指定则从 sitemap 自动提取）
    """
    from agent_components.axure_parser import AxureParser
    from agent_components.dual_chroma import DualChromaDB

    print(f"\n{'=' * 60}")
    print(f"[Phase A] 处理 Axure 原型: {os.path.basename(file_path)}")

    parser = AxureParser(file_path)
    parsed = parser.parse()
    project_name = parsed.get("project_name", "Unknown")
    module = module_name or project_name
    chunks = parser.to_product_doc_chunks(parsed)

    page_details = parsed.get("page_details", {})
    print(f"   => 项目: {project_name}, 页面: {len(page_details)}")

    # 估计关联模块（从页面内容的关键词简单判断）
    related = set()
    for url, detail in page_details.items():
        text = (detail.get("ui_text", "") or "") + str(detail.get("interactions", []))
        for mod_keyword in ["管理", "模块", "系统设置", "报表"]:
            if mod_keyword in text:
                related.add(mod_keyword)
    related.discard(module)

    # 入库
    db = DualChromaDB()
    doc_id = f"axure_{os.path.basename(file_path)}_{module}"
    db.delete_by_doc_id(doc_id)
    db.add_product_doc_chunks(doc_id, chunks, module, related_modules=list(related))
    print(f"   [OK] 入库完成 (doc_id={doc_id}), {len(chunks)} 块")

    return {"doc_id": doc_id, "module_name": module, "chunks": len(chunks)}


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法: python ingest_v2.py <文件路径> [--type product|api|axure] [--module 模块名]")
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

    print(f"\n结果: {result}")

    print(f"\n结果: {result}")
