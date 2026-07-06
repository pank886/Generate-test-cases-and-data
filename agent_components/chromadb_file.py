from dotenv import load_dotenv, find_dotenv
load_dotenv(find_dotenv())

import os
import re
from typing import List, Optional, Union
from pathlib import Path

# 第三方库
from pypdf import PdfReader
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_ollama import OllamaEmbeddings
from langchain_core.documents import Document
from langchain_chroma import Chroma


from config import CHROMA_DB_DIR


def ensure_directory(path: str) -> str:
    """
    确保目录存在，若不存在则自动创建
    :param path: 目录路径
    :return: 规范化后的路径
    """
    resolved = os.path.abspath(path)
    os.makedirs(resolved, exist_ok=True)
    return resolved


class ReadersChromadb:
    def __init__(
            self,
            persist_directory: str = None,
            collection_name: str = "my_rag_collection",
            embedding_model_name: str = None,
            base_url: Optional[str] = None
    ):
        """
        初始化工具类
        :param persist_directory: 向量数据库持久化路径（默认使用 vector_store/chroma_db）
        :param collection_name: 集合名称
        :param embedding_model_name: 嵌入模型名称
        :param base_url: Ollama 服务地址
        """
        if persist_directory is None:
            persist_directory = CHROMA_DB_DIR

        # 自动创建向量数据存储目录
        self.persist_directory = ensure_directory(persist_directory)
        self.collection_name = collection_name

        if base_url is None:
            base_url = os.environ.get("EMBEDDING_URL", "http://localhost:11434")

        final_model_name = embedding_model_name or os.environ.get("EMBEDDING_MODEL")
        if not final_model_name:
            raise ValueError(
                "Embedding 模型未指定！请在 .env 文件中设置 EMBEDDING_MODEL，"
                "或在环境变量中设置。\n"
                "例如: EMBEDDING_MODEL=nomic-embed-text"
            )

        # 3. 初始化 Embeddings
        try:
            self.embeddings = OllamaEmbeddings(
                model=final_model_name,
                base_url=base_url,
            )
            print(f"ℹ️ 使用 Embedding 模型: {final_model_name}")
        except Exception as e:
            raise RuntimeError(
                f"Embeddings 初始化失败，请检查 Ollama 服务是否已启动 (base_url={base_url})。\n"
                f"错误: {e}"
            ) from e

        # 4. 初始化向量数据库
        self.vector_store = Chroma(
            persist_directory=self.persist_directory,
            embedding_function=self.embeddings,
            collection_name=self.collection_name
        )

    def extract_text_from_pdf(self, pdf_path: Union[str, Path]) -> str:
        """
        提取 PDF 纯文本（仅用于不需要页码的场景，建议优先使用 process_pdf_to_docs）
        """
        if not os.path.exists(pdf_path):
            raise FileNotFoundError(f"文件不存在: {pdf_path}")

        try:
            reader = PdfReader(pdf_path)
            if not reader.pages:
                raise ValueError("PDF 文件为空或无法读取页面")

            full_text = "\n\n".join([page.extract_text() for page in reader.pages])

            if not full_text.strip():
                print("⚠️ 警告: 提取的文本为空，可能是扫描版 PDF。")

            return full_text
        except Exception as e:
            raise ValueError(f"PDF 解析错误: {e}") #from e

    def process_pdf_to_docs(self, pdf_path: Union[str, Path]) -> List[Document]:
        """
        【核心功能】读取 PDF 并转换为带元数据的 Document 列表
        推荐调用方式，因为它保留了页码信息。
        """
        if not os.path.exists(pdf_path):
            raise FileNotFoundError(f"文件不存在: {pdf_path}")

        documents = []
        reader = PdfReader(pdf_path)

        # 文本切分器配置
        text_splitter = RecursiveCharacterTextSplitter(
            chunk_size=1000,
            chunk_overlap=200,
            separators=["\n\n", "\n", "。", "，", " ", ""]  # 针对中文优化分隔符
        )

        print(f"📄 正在处理文件: {os.path.basename(pdf_path)} (共 {len(reader.pages)} 页)...")

        for i, page in enumerate(reader.pages):
            text = page.extract_text()
            # 过滤掉空白页
            if text and text.strip():
                doc = Document(
                    page_content=text,
                    metadata={
                        "source": str(pdf_path),
                        "page": i + 1,
                        "type": "pdf"
                    }
                )
                # 切分
                sub_docs = text_splitter.split_documents([doc])
                documents.extend(sub_docs)

        return documents

    # ---- Markdown 智能切分（按标题保留完整性） ----
    MD_MAX_CHARS = 4000  # 安全阈值

    @staticmethod
    def _recursive_split(block: str, patterns: list) -> list:
        """依次尝试每个分隔符切分，若所有子块都 <= 阈值则返回，否则降级到下一个分隔符"""
        for pat in patterns:
            if pat == "\n":
                sub = block.split("\n")
            else:
                sub = re.split(f'(?={re.escape(pat)})', block)
            sub = [s for s in sub if s.strip()]
            if all(len(s) <= ReadersChromadb.MD_MAX_CHARS for s in sub):
                return sub
        # 所有分隔符都试过仍超长 → 硬切兜底
        return [
            block[i:i + ReadersChromadb.MD_MAX_CHARS]
            for i in range(0, len(block), ReadersChromadb.MD_MAX_CHARS)
        ]

    @staticmethod
    def _split_md_by_headers(text: str) -> list:
        """
        以 `## 接口文档` 为主切分点，保留标题，超长块逐步降级切分
        """
        blocks = []
        # 1) 按主模式切分（零宽断言保留标题）
        parts = re.split(r'(?=## 接口文档)', text)
        for part in parts:
            if not part.strip():
                continue
            if len(part) <= ReadersChromadb.MD_MAX_CHARS:
                blocks.append(part)
            else:
                # 2) 降级：尝试按 h2 → h1 → --- → 换行切分
                sub = ReadersChromadb._recursive_split(
                    part,
                    patterns=["## ", "# ", "---\n", "\n"],
                )
                blocks.extend(sub)
        return blocks

    def process_md_to_docs(self, md_path: Union[str, Path]) -> List[Document]:
        """
        读取 Markdown 文件并转换为带元数据的 Document 列表
        使用按标题切分的智能策略，确保每个接口被完整保留。
        """
        if not os.path.exists(md_path):
            raise FileNotFoundError(f"文件不存在: {md_path}")

        with open(md_path, "r", encoding="utf-8") as f:
            text = f.read()

        if not text.strip():
            print("⚠️ 警告: MD 文件内容为空")
            return []

        raw_blocks = self._split_md_by_headers(text)

        documents = []
        for block in raw_blocks:
            block = block.strip()
            if not block:
                continue
            doc = Document(
                page_content=block,
                metadata={
                    "source": str(md_path),
                    "type": "md",
                }
            )
            documents.append(doc)

        print(f"📄 正在处理文件: {os.path.basename(md_path)} ({len(documents)} 个文本块)...")
        return documents

    def add_documents(self, documents: List[Document]):
        """
        将处理好的文档存入向量数据库
        """
        if not documents:
            print("⚠️ 没有文档需要存储。")
            return

        print(f"🚀 正在向量化并存储 {len(documents)} 个文本块...")
        self.vector_store.add_documents(documents=documents)
        print("✅ 存储完成！")

    def search_context(self, user_question_str: str, k: int = 50) -> str:
        """
        搜索最相关的上下文
        :param user_question_str: 用户问题
        :param k: 返回的片段数量
        :return: 拼接后的上下文文本
        """
        if not user_question_str.strip():
            return ""

        results = self.vector_store.similarity_search(user_question_str, k=k)

        if not results:
            return "未在知识库中找到相关内容。"

        context_parts = []
        for doc in results:
            # 格式化输出，包含来源和页码，方便 LLM 理解上下文来源
            source = doc.metadata.get("source", "未知来源")
            page = doc.metadata.get("page", "")

            # 添加头部信息
            header = f"[来源: {os.path.basename(source)}"
            if page:
                header += f" - 第 {page} 页"
            header += "]\n"

            context_parts.append(header + doc.page_content)

        # 使用明显的分隔符拼接
        return "\n\n---\n\n".join(context_parts)



class DualChromaDB:
    """Phase A: 双集合向量数据库封装。

    管理两个独立集合：product_docs（产品文档）和 api_defs（接口定义）。
    支持幂等更新（通过 doc_id 删除旧数据）、按模块过滤检索。
    """

    def __init__(self, persist_directory: str = None):
        from config import CHROMA_DB_DIR, COLLECTION_PRODUCT_DOCS, COLLECTION_API_DEFS
        from config import EMBEDDING_MODEL, EMBEDDING_URL
        import os
        from langchain_chroma import Chroma
        from langchain_ollama import OllamaEmbeddings

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

    def add_product_doc_chunks(self, doc_id: str, chunks: list, module: str,
                                related_modules: list = None):
        """添加产品文档分块。"""
        from langchain_core.documents import Document
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
        from langchain_core.documents import Document
        import json
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


