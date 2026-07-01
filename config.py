"""统一配置管理"""
import os
from dotenv import load_dotenv, find_dotenv

load_dotenv(find_dotenv())

# ====== 向量数据库 ======
VECTOR_STORE_DIR = os.environ.get("VECTOR_STORE_DIR", "./vector_store")
CHROMA_DB_DIR = os.environ.get("CHROMA_DB_DIR", f"{VECTOR_STORE_DIR}/chroma_db")
CHROMA_COLLECTION = os.environ.get("CHROMA_COLLECTION", "my_rag_collection")

# ====== Embedding 模型 ======
EMBEDDING_MODEL = os.environ.get("EMBEDDING_MODEL")
EMBEDDING_URL = os.environ.get("EMBEDDING_URL", "http://localhost:11434")

# ====== LLM 配置（原始值） ======
LLM_MODEL = os.environ.get("LLM_MODEL")
LLM_API_KEY = os.environ.get("LLM_API_KEY")
LLM_BASE_URL = os.environ.get("LANGCHAIN_URL")
LLM_TEMPERATURE = float(os.environ.get("LLM_TEMPERATURE", "0.7"))

# ====== 线上 配置（可选，优先级高于本地 LLM） ======
DEEP_URL = os.environ.get("DEEP_URL")
DEEP_API_KEY = os.environ.get("DEEP_API_KEY")
DEEP_MODEL = os.environ.get("DEEP_MODEL")

# ====== 活跃 LLM 配置（自动选择 线上 / 本地） ======
DEEPSEEK_READY = bool(DEEP_URL and DEEP_API_KEY and DEEP_MODEL)
if DEEPSEEK_READY:
    LLM_MODEL = DEEP_MODEL
    LLM_BASE_URL = DEEP_URL
    LLM_API_KEY = DEEP_API_KEY
    LLM_PROVIDER = "deepseek"
else:
    LLM_PROVIDER = "local"

# ====== Web 服务 ======
WEB_HOST = os.environ.get("WEB_HOST", "0.0.0.0")
WEB_PORT = int(os.environ.get("WEB_PORT", "8000"))

# ====== 目标项目路径（PyCharmMiscProject） ======
PYCHARM_MISC = os.environ.get("PYCHARM_MISC", r"C:\Users\damai\PycharmMiscProject")
TESTCASE_BASE = os.environ.get("TESTCASE_BASE", f"{PYCHARM_MISC}/testcase")
