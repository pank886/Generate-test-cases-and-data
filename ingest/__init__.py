"""Phase A: 智能文档处理包（拆分自 ingest_v2.py，2026-08-07 大文件拆分）

子模块职责：
  - extractors: 文件文本提取（PDF/MD/TXT/DOCX）+ doc_id 生成
  - storage:    SQLite 文档/块/术语持久化 + 补偿回滚
  - api_parser: YAPI MD 纯代码解析（无外部依赖）
  - chunking:   切块 + 摘要生成 + 检索文本构建
  - pipelines:  五大流程入口编排

顶层 ingest_v2.py 为兼容层，re-export 全部对外符号。
"""
from ingest.extractors import (
    _extract_text,
    _extract_docx,
    _docx_img_dir,
    _safe_doc_id,
)
from ingest.storage import (
    _cascade_bind_to_module_docs,
    _delete_sqlite_doc,
    _save_to_sqlite,
    _save_single_chunk,
    _save_document_chunks,
    _delete_document_chunks,
)
from ingest.api_parser import (
    _merge_api_defs,
    _extract_valid_api_paths,
    extract_apis_from_yapi_md,
    extract_apis_from_yapi_json,
    _split_text_by_headers,
)
from ingest.chunking import (
    _parse_chunk_summaries,
    _generate_batch_summaries,
    _build_api_search_text,
    _build_doc_search_text,
)
from ingest.pipelines import (
    process_product_doc,
    process_api_doc,
    process_api_doc_extract,
    commit_api_docs,
    process_axure_zip,
)
