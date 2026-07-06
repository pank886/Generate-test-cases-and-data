"""统一配置入口（薄包装层）。

所有配置由 settings.py 集中管理，本文件保留原有变量名以兼容现有 import。
用法保持不变:
    from config import CHROMA_DB_DIR, LLM_MODEL
    import config
    print(config.WEB_PORT)
"""

from settings import settings

# ====== 向量数据库 ======
VECTOR_STORE_DIR = settings.vector_store_dir
CHROMA_DB_DIR = settings.chroma_db_dir
CHROMA_COLLECTION = settings.chroma_collection

# ====== Embedding 模型 ======
EMBEDDING_MODEL = settings.embedding_model
EMBEDDING_URL = settings.embedding_url

# ====== LLM 配置 ======
LLM_MODEL = settings.active_llm_model
LLM_API_KEY = settings.active_llm_api_key
LLM_BASE_URL = settings.active_llm_base_url
LLM_TEMPERATURE = settings.llm_temperature
LLM_PROVIDER = settings.llm_provider

# ====== 线上 LLM（原始值，供直接引用） ======
DEEP_URL = settings.deep_url
DEEP_API_KEY = settings.deep_api_key
DEEP_MODEL = settings.deep_model
DEEPSEEK_READY = settings.deepseek_ready

# ====== Phase A 双集合配置 ======
COLLECTION_PRODUCT_DOCS = settings.collection_product_docs
COLLECTION_API_DEFS = settings.collection_api_defs
CHUNK_SIZE = settings.chunk_size
CHUNK_OVERLAP = settings.chunk_overlap
MAX_INGEST_CHARS_PER_BATCH = settings.max_ingest_chars_per_batch

# ====== 工作流特性开关 ======
ENABLE_THINKING = settings.enable_thinking

# ====== Web 服务 ======
WEB_HOST = settings.web_host
WEB_PORT = settings.web_port

# ====== 目标项目路径 ======
TESTCASE_BASE = settings.testcase_base
PYCHARM_MISC = settings.testcase_base  # 兼容旧变量名

# ====== 日志 ======
LOG_DIR = settings.log_dir
LOG_LEVEL = settings.log_level

# ====== 节点可调参数（供各节点读取，替换硬编码） ======
RETRIEVAL_K = settings.retrieval_k
MAX_RETRIES = settings.max_retries
YAML_CONCURRENCY = settings.yaml_concurrency
EXCEL_REPAIR_ATTEMPTS = settings.excel_repair_attempts
TASK_TTL_SECONDS = settings.task_ttl_seconds
