"""Phase A: 智能文档处理入口（兼容层）

2026-08-07 大文件拆分：实现已迁移至 ``ingest/`` 包，本文件仅 re-export
全部对外符号并保留 CLI 入口，保证既有 ``from ingest_v2 import ...`` 与
``import ingest_v2`` 用法零改动。

使用方式（不变）：
    from ingest_v2 import process_product_doc, process_api_doc
    process_product_doc("uploads/doc.pdf")
    process_api_doc("uploads/api.md")
"""
from ingest import (
    # extractors
    _extract_text,
    _extract_docx,
    _docx_img_dir,
    _safe_doc_id,
    # storage
    _cascade_bind_to_module_docs,
    _delete_sqlite_doc,
    _save_to_sqlite,
    _save_single_chunk,
    _save_document_chunks,
    _delete_document_chunks,
    # api_parser
    _merge_api_defs,
    _extract_valid_api_paths,
    extract_apis_from_yapi_md,
    extract_apis_from_yapi_json,
    _split_text_by_headers,
    # chunking
    _parse_chunk_summaries,
    _generate_batch_summaries,
    _build_api_search_text,
    _build_doc_search_text,
    # pipelines
    process_product_doc,
    process_api_doc,
    process_api_doc_extract,
    commit_api_docs,
    process_axure_zip,
)
# ChromaDB 单例（测试通过 _ing.get_chroma_db 打 monkeypatch，需保持同引用）
from infrastructure.vector_store.dual_chroma import get_chroma_db

from infrastructure.observability import get_logger

logger = get_logger(__name__)


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
