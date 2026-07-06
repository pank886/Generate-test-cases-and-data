"""Phase A: 双集合向量数据库封装。

管理两个独立集合：product_docs（产品文档）和 api_defs（接口定义）。
支持幂等更新（通过 doc_id 删除旧数据）、按模块过滤检索。
"""

import json
import os

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
    """双集合向量数据库封装。"""

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

    def add_product_doc_chunks(self, doc_id: str, chunks: list,
                               module: str, related_modules: list = None):
        """添加产品文档分块。"""
        docs = []
        for i, chunk in enumerate(chunks):
            docs.append(Document(
                page_content=chunk,
                metadata={
                    "doc_id": doc_id,
                    "module": module,
                    "related_modules": ",".join(related_modules or []),
                    "chunk_index": i,
                    "type": "product_doc",
                }
            ))
        self.product_store.add_documents(docs)

    def search_product_docs(self, query: str, k: int = 10,
                            module: str = None) -> list:
        """检索产品文档，支持按模块过滤。"""
        kwargs = {"k": k}
        if module:
            kwargs["filter"] = {"module": module}
        return self.product_store.similarity_search(query, **kwargs)

    # ---- 接口定义操作 ----

    def add_api_defs(self, doc_id: str, apis: list, module: str):
        """添加接口定义。"""
        docs = []
        for i, api in enumerate(apis):
            api_text = json.dumps(api, ensure_ascii=False)
            docs.append(Document(
                page_content=api_text,
                metadata={
                    "doc_id": doc_id,
                    "module": module,
                    "api_name": api.get("name", ""),
                    "chunk_index": i,
                    "type": "api_def",
                }
            ))
        self.api_store.add_documents(docs)

    def search_api_defs(self, query: str, k: int = 10,
                        module: str = None) -> list:
        """检索接口定义，支持按模块过滤。"""
        kwargs = {"k": k}
        if module:
            kwargs["filter"] = {"module": module}
        return self.api_store.similarity_search(query, **kwargs)

    # ---- 通用操作 ----

    def delete_by_doc_id(self, doc_id: str):
        """幂等更新：删除指定文档的所有记录。"""
        self.product_store.delete(where={"doc_id": doc_id})
        self.api_store.delete(where={"doc_id": doc_id})

    def update_related_modules(self, doc_id: str, related_modules: list):
        """更新产品文档的关联模块元数据（人工审核后调用）。"""
        results = self.product_store.get(where={"doc_id": doc_id})
        if not results or not results.get("ids"):
            return
        new_meta = {"related_modules": ",".join(related_modules)}
        self.product_store.update(
            ids=results["ids"],
            metadatas=[new_meta] * len(results["ids"])
        )

    def search_context(self, query: str, k: int = 50) -> str:
        """兼容旧接口：全库搜索（两集合合并）。"""
        pd = self.product_store.similarity_search(query, k=k)
        ad = self.api_store.similarity_search(query, k=k)
        combined = (pd + ad)[:k]
        if not combined:
            return "未在知识库中找到相关内容。"
        parts = []
        for doc in combined:
            src = doc.metadata.get("module", "?")
            parts.append(f"[{src}] {doc.page_content}")
        return "\n\n---\n\n".join(parts)
