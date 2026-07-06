#!/usr/bin/env python3
"""⚠️ 已弃用：统一文件处理入口。

请改用 ingest_v2.py 的双集合流程（process_product_doc / process_api_doc / process_axure_zip）。
此文件仅在需要旧版单集合 ChromaDB 流程时保留，计划后续版本移除。
"""
import os
import sys
import argparse

from agent_components.chromadb_file import ReadersChromadb
from config import CHROMA_DB_DIR, CHROMA_COLLECTION


# ==================== 文件类型 → 处理器映射 ====================
# 在此注册新的文件类型处理器，便于扩展
_FILE_PROCESSORS: dict = {}


def _register_handler(ext: str):
    """装饰器：注册文件类型处理器"""
    def wrapper(func):
        _FILE_PROCESSORS[ext] = func
        return func
    return wrapper


# ==================== 具体处理器实现 ====================

@_register_handler(".pdf")
def _process_pdf(file_path: str, db_client: ReadersChromadb) -> int:
    """处理 PDF 文件"""
    print(f"📄 正在读取 PDF: {os.path.basename(file_path)}")
    documents = db_client.process_pdf_to_docs(file_path)
    if not documents:
        print("⚠️ 警告: PDF 解析后未获取到任何内容，可能是扫描版或加密文件。")
        return 0
    db_client.add_documents(documents)
    return len(documents)


@_register_handler(".md")
def _process_md(file_path: str, db_client: ReadersChromadb) -> int:
    """处理 Markdown 文件"""
    print(f"📄 正在读取 MD: {os.path.basename(file_path)}")
    documents = db_client.process_md_to_docs(file_path)
    if not documents:
        print("⚠️ 警告: MD 文件内容为空。")
        return 0
    db_client.add_documents(documents)
    return len(documents)


# ==================== 统一入口 ====================

def build_vector_store(file_path: str) -> int:
    """
    识别文件类型并构建向量数据库

    Args:
        file_path: 文件路径（支持 .pdf, .md 等）

    Returns:
        处理的文本块数量，0 表示失败

    Raises:
        FileNotFoundError: 文件不存在
        ValueError: 不支持的文件类型
        Exception: 其他处理错误
    """
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"文件不存在: {file_path}")

    ext = os.path.splitext(file_path)[1].lower()
    processor = _FILE_PROCESSORS.get(ext)

    if processor is None:
        supported = ", ".join(_FILE_PROCESSORS.keys())
        raise ValueError(
            f"不支持的文件类型: {ext}。当前支持: {supported}。"
        )

    print(f"🔗 正在连接数据库: {CHROMA_DB_DIR} ...")
    db_client = ReadersChromadb(
        persist_directory=CHROMA_DB_DIR,
        collection_name=CHROMA_COLLECTION,
    )

    chunk_count = processor(file_path, db_client)

    print(f"\n✅ === 数据库构建完成 ===")
    print(f"💡 共处理 {chunk_count} 个文本块")
    return chunk_count


# ==================== CLI 入口 ====================

def main():
    parser = argparse.ArgumentParser(description="构建向量数据库（支持 PDF/MD）")
    parser.add_argument("file_path", help="要处理的文件路径（.pdf / .md）")
    args = parser.parse_args()

    try:
        build_vector_store(args.file_path)
    except Exception as e:
        print(f"\n❌ 发生异常: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()

