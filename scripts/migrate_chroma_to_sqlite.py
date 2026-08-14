#!/usr/bin/env python
"""存量数据迁移：从旧 ChromaDB collections 回灌到 SQLite + 重建 doc_search。

一次性的迁移脚本，步骤：
  1. API 迁移: api_defs collection → documents.api_* 列
  2. 产品/Axure 迁移: product_docs collection → document_chunks.content
  3. 批量生成 simple_summary（走 Phase 3 的批量摘要逻辑，失败走补偿）
  4. 重建 doc_search collection（用 SQLite 数据覆盖 ChromaDB 检索文本）

用法: python scripts/migrate_chroma_to_sqlite.py [--dry-run] [--skip-summary]

迁移前后数据量对照输出，确认成功后才可删除旧 collection。
"""

import argparse
import hashlib
import json
import os
import sys
import time
from datetime import datetime, timezone

# 确保项目根目录在 sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def migrate_api_defs(dry_run: bool = False) -> int:
    """Step 1: 从 ChromaDB api_defs collection 迁移 API 数据到 SQLite documents.api_* 列。

    Returns: 迁移的 API 文档数
    """
    from agent_components.dual_chroma import get_chroma_db
    from database import get_session_ctx
    from database.models import Document as DocModel

    chroma = get_chroma_db()
    count = 0

    print("\n=== Step 1: API 文档迁移 ===")

    try:
        results = chroma.api_store.get()
    except Exception as e:
        print(f"  [WARN] 旧 api_store 读取失败 (可能已为空): {e}")
        return 0

    if not results or not results.get("ids"):
        print("  [INFO] 旧 api_defs collection 为空，跳过")
        return 0

    ids = results["ids"]
    metadatas = results.get("metadatas") or [{}] * len(ids)
    documents = results.get("documents") or [""] * len(ids)

    print(f"  发现 {len(ids)} 条 API 记录")

    for i, mid in enumerate(ids):
        meta = metadatas[i] if i < len(metadatas) else {}
        doc_id = meta.get("doc_id", mid)
        content_str = documents[i] if i < len(documents) else ""

        # 解析 JSON content
        try:
            api = json.loads(content_str) if content_str else {}
        except (json.JSONDecodeError, TypeError):
            print(f"  [SKIP] {doc_id}: JSON 解析失败")
            continue

        api_json = json.dumps(api, sort_keys=True, ensure_ascii=False)
        content_hash = hashlib.sha256(api_json.encode()).hexdigest()

        if dry_run:
            print(f"  [DRY-RUN] {doc_id}: {api.get('method', '?')} {api.get('url', '')}")
            count += 1
            continue

        with get_session_ctx() as session:
            doc = session.query(DocModel).filter_by(id=doc_id).first()
            if not doc:
                # 创建新记录
                doc = DocModel(
                    id=doc_id,
                    file_name=api.get("name", f"api_{i}"),
                    file_type="md",
                    doc_type="api",
                    chunk_count=1,
                    status="pending",
                    upload_time=datetime.now(timezone.utc),
                )
                session.add(doc)
            # 填充 api_* 列
            doc.api_name = api.get("name", "")
            doc.api_url = api.get("url", "")
            doc.api_method = api.get("method", "")
            doc.api_description = (api.get("description") or "")[:500]
            # 归一化为新结构（header 映射 + body/return 六字段数组），兼容旧格式源数据
            api = _coerce_api_format(api)
            doc.api_headers = json.dumps(api.get("header") or {}, ensure_ascii=False)
            doc.api_parameters = json.dumps(api.get("body") or [], ensure_ascii=False)
            doc.api_returns = json.dumps(api.get("return") or [], ensure_ascii=False)
            doc.api_annotations = json.dumps(api.get("annotations") or {}, ensure_ascii=False)
            doc.content_hash = content_hash
        count += 1

    print(f"  迁移完成: {count} 条 API 文档")
    return count


def migrate_product_docs(dry_run: bool = False) -> int:
    """Step 2: 从 ChromaDB product_docs collection 迁移到 SQLite document_chunks。

    Returns: 迁移的 chunk 数
    """
    from agent_components.dual_chroma import get_chroma_db
    from database import get_session_ctx
    from database.models import DocumentChunk

    chroma = get_chroma_db()
    count = 0

    print("\n=== Step 2: 产品/Axure 文档迁移 ===")

    try:
        results = chroma.product_store.get()
    except Exception as e:
        print(f"  [WARN] 旧 product_store 读取失败 (可能已为空): {e}")
        return 0

    if not results or not results.get("ids"):
        print("  [INFO] 旧 product_docs collection 为空，跳过")
        return 0

    ids = results["ids"]
    metadatas = results.get("metadatas") or [{}] * len(ids)
    documents = results.get("documents") or [""] * len(ids)

    print(f"  发现 {len(ids)} 条 chunk 记录")

    # 按 doc_id 分组统计
    doc_ids_seen = set()
    for i, mid in enumerate(ids):
        meta = metadatas[i] if i < len(metadatas) else {}
        doc_id = meta.get("doc_id", mid)
        content = documents[i] if i < len(documents) else ""
        chunk_index = meta.get("chunk_index", i)
        page_name = meta.get("page_name", "")

        doc_ids_seen.add(doc_id)

        if dry_run:
            count += 1
            continue

        # 检查是否已存在
        with get_session_ctx() as session:
            existing = session.query(DocumentChunk).filter_by(
                doc_id=doc_id, chunk_index=chunk_index
            ).first()
            if existing:
                continue  # 已迁移过
            chunk = DocumentChunk(
                doc_id=doc_id,
                chunk_index=chunk_index,
                content=content,
                page_name=page_name,
                chunk_type="page" if page_name else "text",
                token_count=len(content),
            )
            session.add(chunk)
        count += 1

    print(f"  迁移完成: {count} chunks (来自 {len(doc_ids_seen)} 个文档)")
    return count


def rebuild_doc_search(dry_run: bool = False) -> int:
    """Step 4: 用 SQLite 数据重建 doc_search collection。

    Returns: 重建的 chunk 数
    """
    from agent_components.dual_chroma import get_chroma_db
    from database import get_session_ctx
    from database.models import DocumentChunk, Document as DocModel
    from ingest_v2 import _build_doc_search_text, _build_api_search_text
    from ingest.api_parser import _coerce_api_format

    chroma = get_chroma_db()
    count = 0

    print("\n=== Step 4: 重建 doc_search collection ===")

    # 检查是否有数据
    with get_session_ctx() as session:
        total_chunks = session.query(DocumentChunk).count()
        total_apis = session.query(DocModel).filter(DocModel.doc_type == "api").count()
    print(f"  SQLite 中有 {total_chunks} chunks + {total_apis} API 记录")

    if dry_run:
        print(f"  [DRY-RUN] 将重建 {total_chunks + total_apis} 条检索文本")
        return total_chunks + total_apis

    # 清空并重建 doc_search
    try:
        chroma.doc_search.delete(where={})
    except Exception as e:
        print(f"  [WARN] 清空 doc_search 失败: {e}")

    # 重建 product/axure chunks
    batch_size = 100
    offset = 0
    while True:
        with get_session_ctx() as session:
            chunks = session.query(DocumentChunk).order_by(
                DocumentChunk.id
            ).offset(offset).limit(batch_size).all()
            if not chunks:
                break

            chroma_docs = []
            for c in chunks:
                search_text = _build_doc_search_text(c)
                source = "analyzed_summary" if c.analyzed_summary else (
                    "simple_summary" if c.simple_summary else "content_fallback")
                chroma_docs.append({
                    "search_text": search_text,
                    "page_name": c.page_name or "",
                    "source": source,
                    "chunk_index": c.chunk_index,
                })

            chroma.add_product_doc_chunks(chunks[0].doc_id, chroma_docs)
            count += len(chroma_docs)
            offset += batch_size
            print(f"  [{count}/{total_chunks}] chunks...")

    # 重建 API 检索文本
    with get_session_ctx() as session:
        api_records = session.query(DocModel).filter(DocModel.doc_type == "api").all()
        for rec in api_records:
            api_dict = {
                "name": rec.api_name or "",
                "url": rec.api_url or "",
                "method": rec.api_method or "",
                "description": rec.api_description or "",
                "header": json.loads(rec.api_headers) if rec.api_headers else {},
                "body": json.loads(rec.api_parameters) if rec.api_parameters else [],
                "return": json.loads(rec.api_returns) if rec.api_returns else [],
                "annotations": json.loads(rec.api_annotations) if rec.api_annotations else {},
            }
            search_text = _build_api_search_text(api_dict)
            chroma.update_api_def(
                doc_id=rec.id,
                page_content=search_text,
                metadata_update={"source": "simple_summary"},
            )
            count += 1
    print(f"  API 重建完成: {len(api_records)} 条")

    print(f"  重建完成: {count} 条检索文本")
    return count


def main():
    parser = argparse.ArgumentParser(description="存量数据迁移: ChromaDB → SQLite")
    parser.add_argument("--dry-run", action="store_true", help="仅检查，不实际写入")
    parser.add_argument("--skip-summary", action="store_true", help="跳过批量摘要生成")
    parser.add_argument("--step", type=int, choices=[1, 2, 4], help="仅执行指定步骤")
    args = parser.parse_args()

    print("=" * 60)
    print("存量数据迁移: ChromaDB → SQLite")
    if args.dry_run:
        print(">>> DRY RUN 模式（仅预览，不写入）<<<")
    print("=" * 60)

    # 初始化
    from database import init_db
    init_db()

    if args.step is None or args.step == 1:
        api_count = migrate_api_defs(dry_run=args.dry_run)
    else:
        api_count = 0

    if args.step is None or args.step == 2:
        chunk_count = migrate_product_docs(dry_run=args.dry_run)
    else:
        chunk_count = 0

    if not args.skip_summary and (args.step is None):
        print("\n=== Step 3: 批量摘要生成（跳过，请在上传后自然触发）===")
        print("  提示: 存量 chunks 的 simple_summary 可在下次入库时由补偿 worker 补齐")
        print("  或手动调用 _batch_generate_summaries() 批量生成")

    if args.step is None or args.step == 4:
        rebuild_count = rebuild_doc_search(dry_run=args.dry_run)
    else:
        rebuild_count = 0

    print("\n" + "=" * 60)
    print("迁移完成！")
    print(f"  API 文档: {api_count} 条")
    print(f"  Chunk:   {chunk_count} 条")
    if not args.skip_summary and args.step is None:
        print(f"  doc_search 重建: {rebuild_count} 条")
    print()
    print("下一步: 验证迁移前后数据量一致，确认后手动删除旧 ChromaDB collection")
    if not args.dry_run:
        print("  旧 collection 路径: vector_store/chroma_db/product_docs/")
        print("                      vector_store/chroma_db/api_defs/")
    print("=" * 60)


if __name__ == "__main__":
    main()
