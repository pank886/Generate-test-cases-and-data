"""统一向量检索引擎封装（纯检索引擎，供 Phase B 使用）。

ChromaDB 只存 chunk 检索文本、向量和检索必要的 metadata（doc_id, chunk_index, doc_type）。
所有业务关系（模块、绑定、文档元数据）由 SQLite database/ 层管理。
正文以 SQLite 为唯一真相源，ChromaDB 损坏时可从 SQLite 全量重建。

单一 Collection（doc_search）替代旧双集合架构，靠 metadata.doc_type 区分 api / product / axure。
"""

import json
import logging
import os
import threading

logger = logging.getLogger(__name__)

from langchain_chroma import Chroma
from langchain_core.documents import Document
from agent_components.fallback_embeddings import FallbackOllamaEmbeddings

from config import (
    CHROMA_DB_DIR,
    COLLECTION_DOC_SEARCH,
    EMBEDDING_MODEL,
    EMBEDDING_URL,
    EMBEDDING_TIMEOUT,
)


class DualChromaDB:
    """统一向量数据库封装（单一 doc_search Collection，纯向量检索，不含业务逻辑）。"""

    def __init__(self, persist_directory: str = None):
        persist = persist_directory or CHROMA_DB_DIR
        model = EMBEDDING_MODEL or os.environ.get("EMBEDDING_MODEL")
        if not model:
            raise ValueError("EMBEDDING_MODEL 未设置")
        url = EMBEDDING_URL or "http://localhost:11434"

        embeddings = FallbackOllamaEmbeddings(
            model=model, base_url=url,
            client_kwargs={"timeout": EMBEDDING_TIMEOUT},
        )

        ds_dir = os.path.join(persist, "doc_search") if persist else None

        self.doc_store = Chroma(
            persist_directory=ds_dir,
            embedding_function=embeddings,
            collection_name=COLLECTION_DOC_SEARCH,
        )

    # ---- 产品/Axure 文档操作 ----

    def add_product_doc_chunks(self, doc_id: str, chunks: list,
                                doc_type: str = "product"):
        """添加产品/Axure 文档分块（检索文本为 page_content）。

        metadata: doc_id / chunk_index / doc_type / source / page_name。
        source 标记检索文本来源：simple_summary / analyzed_summary / content_fallback。
        """
        docs = []
        for i, chunk in enumerate(chunks):
            if isinstance(chunk, str):
                content = chunk
                source = "raw_legacy"
                page_name = ""
            else:
                content = chunk.get("content", "")
                page_name = chunk.get("page_name", "")
                if chunk.get("analyzed_summary"):
                    source = "analyzed_summary"
                elif chunk.get("simple_summary"):
                    source = "simple_summary"
                else:
                    source = "content_fallback"

            docs.append(Document(
                page_content=str(content),
                metadata={
                    "doc_id": doc_id,
                    "chunk_index": i,
                    "doc_type": doc_type,
                    "source": source,
                    "page_name": page_name,
                }
            ))
        self.doc_store.add_documents(docs)

    def search_product_docs(self, query: str, k: int = 10,
                            doc_ids: list[str] = None) -> list:
        """检索产品/Axure 文档，可选按 doc_id 列表过滤。

        Args:
            doc_ids: 由 SQLite 层查出的 doc_id 列表，None 表示全库检索
        """
        filter_dict = {"doc_type": {"$in": ["product", "axure"]}}
        if doc_ids:
            # ChromaDB where 顶层只能一个操作符，多条件须用 $and 包裹（2026-08-05 修复）
            filter_dict = {"$and": [filter_dict, {"doc_id": {"$in": doc_ids}}]}
        return self.doc_store.similarity_search(query, k=k, filter=filter_dict)

    # ---- 接口定义操作 ----

    def add_api_defs(self, doc_id: str, apis: list):
        """添加接口定义（检索文本优先，存 doc_id / api_name / doc_type / source）。

        page_content: _search_text（自然语言检索文本）> JSON 原文（降级）。
        metadata: 含 doc_type='api'、source='api_search_text' 标记检索文本来源。
        """
        docs = []
        for i, api in enumerate(apis):
            search_text = api.get("_search_text", "")
            api_text = search_text if search_text else json.dumps(api, ensure_ascii=False)
            docs.append(Document(
                page_content=api_text,
                metadata={
                    "doc_id": doc_id,
                    "api_name": api.get("name", ""),
                    "chunk_index": i,
                    "doc_type": "api",
                    "source": "api_search_text" if search_text else "api_json_fallback",
                }
            ))
        self.doc_store.add_documents(docs)

    def search_api_defs(self, query: str, k: int = 10,
                        doc_ids: list[str] = None) -> list:
        """检索接口定义，可选按 doc_id 列表过滤。

        Args:
            doc_ids: 由 SQLite 层查出的 doc_id 列表，None 表示全库检索
        """
        filter_dict = {"doc_type": "api"}
        if doc_ids:
            # ChromaDB where 顶层只能一个操作符，多条件须用 $and 包裹（2026-08-05 修复）
            filter_dict = {"$and": [filter_dict, {"doc_id": {"$in": doc_ids}}]}
        return self.doc_store.similarity_search(query, k=k, filter=filter_dict)

    # ---- 通用操作 ----

    def delete_by_doc_id(self, doc_id: str):
        """幂等更新：删除指定文档的所有记录。"""
        try:
            self.doc_store.delete(where={"doc_id": doc_id})
        except Exception:
            logger.error("ChromaDB delete_by_doc_id(%s) 失败", doc_id, exc_info=True)

    def _chunks_from_chroma(self, doc_id: str) -> list[dict]:
        """从 ChromaDB 读取 chunks（降级/即时补偿路径）。"""
        try:
            results = self.doc_store.get(where={"doc_id": doc_id})
            if results and results.get("ids"):
                chunks = []
                for i, mid in enumerate(results["ids"]):
                    meta = results["metadatas"][i] if results.get("metadatas") else {}
                    chunks.append({
                        "chunk_id": mid,
                        "chunk_index": meta.get("chunk_index", i),
                        "content": results["documents"][i] if results.get("documents") else "",
                        "simple_summary": "",
                        "page_name": meta.get("page_name", ""),
                        "type": meta.get("doc_type", ""),
                        "api_name": meta.get("api_name", ""),
                    })
                return sorted(chunks, key=lambda c: c["chunk_index"])
        except Exception:
            logger.debug("ChromaDB get 失败: %s", doc_id, exc_info=True)
        return []

    # ---- 接口查询 ----

    def get_doc_apis(self, doc_id: str) -> list[dict]:
        """获取指定文档下的所有接口定义（优先 SQLite documents.api_* 列，降级 ChromaDB）。"""
        import json as _json
        # 优先：SQLite documents 表
        try:
            from database import get_session_ctx
            from database.models import Document as DocModel
            with get_session_ctx() as session:
                docs = session.query(DocModel).filter_by(id=doc_id, doc_type="api").all()
                if docs:
                    apis = []
                    for d in docs:
                        if d.api_url:  # api_* 列已填充
                            api = {
                                "name": d.api_name, "url": d.api_url,
                                "method": d.api_method, "description": d.api_description,
                                "header": _json.loads(d.api_headers or "{}"),
                                "body": _json.loads(d.api_parameters or "[]"),
                                "return": _json.loads(d.api_returns or "[]"),
                                "annotations": _json.loads(d.api_annotations or "{}"),
                            }
                            apis.append({
                                "api_name": d.api_name,
                                "content": _json.dumps(api, ensure_ascii=False),
                                "_doc_id": doc_id,
                            })
                    if apis:
                        return apis
        except Exception:
            logger.debug("documents 查询失败，降级 ChromaDB: %s", doc_id, exc_info=True)

        # 降级：ChromaDB
        return self._apis_from_chroma(doc_id)

    def _apis_from_chroma(self, doc_id: str) -> list[dict]:
        """从 ChromaDB 读取 API 定义（降级/即时补偿路径）。"""
        try:
            results = self.doc_store.get(where={"doc_id": doc_id})
            if not results or not results.get("ids"):
                return []
            apis = []
            for i, mid in enumerate(results["ids"]):
                meta = results["metadatas"][i] if results.get("metadatas") else {}
                apis.append({
                    "api_name": meta.get("api_name", "?"),
                    "content": results["documents"][i] if results.get("documents") else "",
                })
            return apis
        except Exception:
            logger.debug("ChromaDB get(api) 失败: %s", doc_id, exc_info=True)
        return []


# 模块级单例（避免每次请求都重新连接 Ollama）
_chroma_instance = None
_chroma_lock = threading.Lock()


def get_chroma_db() -> DualChromaDB:
    """获取全局 DualChromaDB 单例（模块级双检锁）。"""
    global _chroma_instance
    if _chroma_instance is None:
        with _chroma_lock:
            if _chroma_instance is None:
                _chroma_instance = DualChromaDB()
    return _chroma_instance
