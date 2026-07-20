"""统一配置入口（薄包装层）。

所有配置由 settings.py 集中管理，本文件保留原有变量名以兼容现有 import。
用法保持不变:
    from config import CHROMA_DB_DIR, LLM_MODEL
    import config
    print(config.WEB_PORT)
"""

import os
from settings import settings

# 项目根目录绝对路径（供文件路径拼接使用，不受运行时 os.chdir() 影响）
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# 辅助：相对路径 → 基于 BASE_DIR 的绝对路径（空字符串拒绝处理，防止静默指向根目录）
def _resolve_path(path: str) -> str:
    if not path:
        raise ValueError("路径配置不能为空，请检查 settings.py 中的对应配置项")
    return os.path.normpath(path if os.path.isabs(path) else os.path.join(BASE_DIR, path))

# ====== 向量数据库 ======
CHROMA_DB_DIR = _resolve_path(settings.chroma_db_dir)

# ====== Embedding 模型 ======
EMBEDDING_MODEL = settings.embedding_model
EMBEDDING_URL = settings.embedding_url
EMBEDDING_TIMEOUT = settings.embedding_timeout

# ====== LLM 配置 ======
LLM_MODEL = settings.active_llm_model
LLM_BASE_URL = settings.active_llm_base_url


def LLM_API_KEY() -> str:
    """获取 LLM API Key（运行时读取，避免模块级变量暴露敏感信息）"""
    return settings.active_llm_api_key
LLM_TEMPERATURE = settings.llm_temperature
LLM_PROVIDER = settings.llm_provider

# ====== 线上 LLM（原始值，供直接引用） ======
DEEP_URL = settings.deep_url
DEEP_MODEL = settings.deep_model
DEEPSEEK_READY = settings.deepseek_ready


def DEEP_API_KEY() -> str:
    """获取 DeepSeek API Key（运行时读取，避免模块级变量暴露敏感信息）"""
    return settings.deep_api_key

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

# ====== 输出路径 ======
PYCHARM_MISC = _resolve_path(settings.pycharm_misc) if settings.pycharm_misc else ""
TESTCASE_SUBDIR = settings.testcase_base
if not PYCHARM_MISC or not TESTCASE_SUBDIR:
    TESTCASE_BASE = ""
else:
    TESTCASE_BASE = _resolve_path(os.path.join(PYCHARM_MISC, TESTCASE_SUBDIR))

# ====== 日志 ======
LOG_DIR = _resolve_path(settings.log_dir)
LOG_LEVEL = settings.log_level

# ====== 节点可调参数（供各节点读取，替换硬编码） ======
RETRIEVAL_K = settings.retrieval_k
COMMON_SERVICE_MODULE = settings.common_service_module
MAX_RETRIES = settings.max_retries
YAML_CONCURRENCY = settings.yaml_concurrency
YAML_REPAIR_ROUNDS = settings.yaml_repair_rounds
EXCEL_REPAIR_ATTEMPTS = settings.excel_repair_attempts
RESOURCE_MUTATE_KEYWORDS = settings.resource_mutate_keywords
CHROMA_RETRY_DELAY = settings.chroma_retry_delay
TASK_TTL_SECONDS = settings.task_ttl_seconds
TASK_MAX_WORKERS = settings.task_max_workers
TASK_MAX_QUEUE = settings.task_max_queue
UPLOAD_MAX_SIZE = settings.upload_max_size_mb * 1024 * 1024
WORKFLOW_SESSION_TTL = settings.workflow_session_ttl
