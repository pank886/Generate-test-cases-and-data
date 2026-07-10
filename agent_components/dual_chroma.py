"""Phase A: 双集合向量数据库封装（纯检索引擎）。

ChromaDB 只存 chunk 文本、向量和检索必要的 metadata（doc_id, chunk_index, api_name）。
所有业务关系（模块、绑定、文档元数据）由 SQLite database/ 层管理。
"""

import json
import logging
import os
import threading

logger = logging.getLogger(__name__)

from langchain_chroma import Chroma
from langchain_core.documents import Document
from langchain_ollama import OllamaEmbeddings

from config import (
    CHROMA_DB_DIR,
    COLLECTION_PRODUCT_DOCS,
    COLLECTION_API_DEFS,
    EMBEDDING_MODEL,
    EMBEDDING_URL,
)


class DualChromaDB:
    """双集合向量数据库封装（纯向量检索，不含业务逻辑）。"""

    def __init__(self, persist_directory: str = None):
        persist = persist_directory or CHROMA_DB_DIR
        model = EMBEDDING_MODEL or os.environ.get("EMBEDDING_MODEL")
        if not model:
            raise ValueError("EMBEDDING_MODEL 未设置")
        url = EMBEDDING_URL or "http://localhost:11434"

        embeddings = OllamaEmbeddings(model=model, base_url=url)

        pd_dir = os.path.join(persist, "product_docs") if persist else None
        ad_dir = os.path.join(persist, "api_defs") if persist else None

        self.product_store = Chroma(
            persist_directory=pd_dir,
            embedding_function=embeddings,
            collection_name=COLLECTION_PRODUCT_DOCS,
        )
        self.api_store = Chroma(
            persist_directory=ad_dir,
            embedding_function=embeddings,
            collection_name=COLLECTION_API_DEFS,
        )

    # ---- 产品文档操作 ----

    def add_product_doc_chunks(self, doc_id: str, chunks: list):
        """添加产品文档分块（仅存 doc_id 和 chunk_index，不存业务关系）。"""
        docs = []
        for i, chunk in enumerate(chunks):
            docs.append(Document(
                page_content=chunk,
                metadata={
                    "doc_id": doc_id,
                    "chunk_index": i,
                    "type": "product_doc",
                }
            ))
        self.product_store.add_documents(docs)

    def search_product_docs(self, query: str, k: int = 10,
                            doc_ids: list[str] = None) -> list:
        """检索产品文档，可选按 doc_id 列表过滤。

        Args:
            doc_ids: 由 SQLite 层查出的 doc_id 列表，None 表示全库检索
        """
        kwargs = {"k": k}
        if doc_ids:
            kwargs["filter"] = {"doc_id": {"$in": doc_ids}}
        return self.product_store.similarity_search(query, **kwargs)

    # ---- 接口定义操作 ----

    def add_api_defs(self, doc_id: str, apis: list):
        """添加接口定义（仅存 doc_id / api_name，不存业务关系）。"""
        docs = []
        for i, api in enumerate(apis):
            api_text = json.dumps(api, ensure_ascii=False)
            docs.append(Document(
                page_content=api_text,
                metadata={
                    "doc_id": doc_id,
                    "api_name": api.get("name", ""),
                    "chunk_index": i,
                    "type": "api_def",
                }
            ))
        self.api_store.add_documents(docs)

    def search_api_defs(self, query: str, k: int = 10,
                        doc_ids: list[str] = None) -> list:
        """检索接口定义，可选按 doc_id 列表过滤。

        Args:
            doc_ids: 由 SQLite 层查出的 doc_id 列表，None 表示全库检索
        """
        kwargs = {"k": k}
        if doc_ids:
            kwargs["filter"] = {"doc_id": {"$in": doc_ids}}
        return self.api_store.similarity_search(query, **kwargs)

    # ---- 通用操作 ----

    def delete_by_doc_id(self, doc_id: str):
        """幂等更新：删除指定文档的所有记录。两个 store 独立执行，单侧失败不阻塞另一侧。"""
        for name, store in (("product_docs", self.product_store), ("api_defs", self.api_store)):
            try:
                store.delete(where={"doc_id": doc_id})
            except Exception:
                logger.error("ChromaDB %s delete_by_doc_id(%s) 失败", name, doc_id, exc_info=True)

    def get_doc_chunks(self, doc_id: str) -> list[dict]:
        """获取文档的所有文本块（供前端查看原文内容）。"""
        # 从两个集合中查找
        for store in (self.product_store, self.api_store):
            results = store.get(where={"doc_id": doc_id})
            if results and results.get("ids"):
                chunks = []
                for i, mid in enumerate(results["ids"]):
                    meta = results["metadatas"][i] if results.get("metadatas") else {}
                    chunks.append({
                        "chunk_id": mid,
                        "chunk_index": meta.get("chunk_index", i),
                        "content": results["documents"][i] if results.get("documents") else "",
                        "type": meta.get("type", ""),
                        "api_name": meta.get("api_name", ""),
                    })
                return sorted(chunks, key=lambda c: c["chunk_index"])
        return []

    def search_context(self, query: str, k: int = 50) -> str:
        """全库检索（两集合合并，用于 LLM 上下文构建）。"""
        pd = self.product_store.similarity_search(query, k=k)
        ad = self.api_store.similarity_search(query, k=k)
        # 交错合并，确保两类结果都能被召回
        combined = []
        for i in range(max(len(pd), len(ad))):
            if i < len(pd):
                combined.append(pd[i])
            if i < len(ad):
                combined.append(ad[i])
        combined = combined[:k]
        if not combined:
            return "未在知识库中找到相关内容。"
        parts = []
        for doc in combined:
            src = doc.metadata.get("doc_id", "?")
            parts.append(f"[{src}] {doc.page_content}")
        return "\n\n---\n\n".join(parts)

    # ---- 接口查询 ----
    def get_doc_apis(self, doc_id: str) -> list[dict]:
        """获取指定文档下的所有接口定义。"""
        results = self.api_store.get(where={"doc_id": doc_id})
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
