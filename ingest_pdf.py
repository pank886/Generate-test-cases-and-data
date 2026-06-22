#!/usr/bin/env python3
"""独立脚本：构建向量数据库

用法:
    python ingest_pdf.py <pdf文件路径>

  或从其他模块调用:
    from ingest_pdf import build_vector_store
    build_vector_store("path/to/doc.pdf")
"""
import os
import sys
import argparse

from agent_components.chromadb_file import ReadersChromadb
from config import CHROMA_DB_DIR, CHROMA_COLLECTION


def build_vector_store(pdf_path: str) -> int:
    """
    读取 PDF 并构建向量数据库

    Args:
        pdf_path: PDF 文件路径

    Returns:
        处理的文本块数量，0 表示失败

    Raises:
        FileNotFoundError: PDF 文件不存在
        Exception: 其他处理错误
    """
    if not os.path.exists(pdf_path):
        raise FileNotFoundError(f"文件不存在: {pdf_path}")

    print(f"🔗 正在连接数据库: {CHROMA_DB_DIR} ...")
    db_client = ReadersChromadb(
        persist_directory=CHROMA_DB_DIR,
        collection_name=CHROMA_COLLECTION,
    )

    print(f"📄 正在读取 PDF: {os.path.basename(pdf_path)}")
    documents = db_client.process_pdf_to_docs(pdf_path)

    if not documents:
        print("⚠️ 警告: PDF 解析后未获取到任何内容，可能是扫描版或加密文件。")
        return 0

    db_client.add_documents(documents)

    print("\n✅ === 数据库构建完成 ===")
    print(f"💡 共处理 {len(documents)} 个文本块")
    return len(documents)


def main():
    parser = argparse.ArgumentParser(description="构建向量数据库")
    parser.add_argument("pdf_path", help="要处理的 PDF 文件路径")
    args = parser.parse_args()

    try:
        build_vector_store(args.pdf_path)
    except Exception as e:
        print(f"\n❌ 发生异常: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
