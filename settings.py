"""配置中心 — 按业务节点组织，手动修改即可。

修改方式（任选其一）:
  1. 直接改这个文件里的 default 值
  2. 在 .env 文件中覆盖（无需改代码）

启动时自动校验必填项（EMBEDDING_MODEL 等）。
"""

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ================================================================
    # 节点: 文件上传 (upload-file)
    # 影响范围: web_app.py → ingest_v2.py → dual_chroma.py
    # ================================================================

    # -- 文本切分参数（影响检索粒度） --
    chunk_size: int = Field(
        default=1000, ge=100, le=8000,
        description="文本块最大字符数。越小检索越精准但可能丢失上下文，越大覆盖越全但噪声越多",
    )
    chunk_overlap: int = Field(
        default=200, ge=0, le=2000,
        description="相邻文本块重叠字符数。防止关键信息被切在两块之间",
    )

    # -- 文档处理上限 --
    max_ingest_chars_per_batch: int = Field(
        default=30000, ge=5000, le=100000,
        description="接口文档 LLM 单批最大处理字符数。大文档自动分批，每批不超过此值",
    )

    # -- 向量库 --
    chroma_db_dir: str = Field(
        default="./vector_store/chroma_db",
        description="ChromaDB 持久化目录",
    )
    collection_product_docs: str = Field(
        default="product_docs",
        description="产品文档 Collection 名（Phase A 双集合）",
    )
    collection_api_defs: str = Field(
        default="api_defs",
        description="接口定义 Collection 名（Phase A 双集合）",
    )

    # ================================================================
    # 节点: 向量检索 (retrieve)
    # 影响范围: nodes.py → ChromaDB.similarity_search()
    # ================================================================

    # -- Embedding 模型（必填，服务启动时校验，为空时 lifespan 给出友好提示） --
    embedding_model: str = Field(
        default="",
        description="Ollama Embedding 模型名。必填，如 bge-m3 / nomic-embed-text",
    )
    embedding_url: str = Field(
        default="http://localhost:11434",
        description="Ollama 服务地址。如果 Ollama 跑在其他机器上，改这里",
    )

    # -- 检索召回数量 --
    retrieval_k: int = Field(
        default=50, ge=1, le=200,
        description="向量检索返回的文本块数量",
    )

    # -- 通用基础服务模块名（Hop 2b 检索接口定义时追加） --
    common_service_module: str = Field(
        default="公共基础服务",
        description="Hop 2b 中追加检索的通用模块名，不同项目可自定义",
    )

    # ================================================================
    # 节点: LLM 调用 (parse_api / excel_plan / py_file / yaml)
    # 影响范围: agents/nodes.py → _invoke_structured() → DeepSeekChatOpenAI
    # ================================================================

    # -- 模型选择（自动: DeepSeek 配了就用，否则本地） --
    deep_url: str | None = Field(default=None, description="DeepSeek API 地址")
    deep_api_key: str | None = Field(default=None, description="DeepSeek API Key")
    deep_model: str | None = Field(default=None, description="DeepSeek 模型名，如 deepseek-v4-pro")

    llm_model: str | None = Field(default=None, description="本地 LLM 模型名（DeepSeek 未配时使用）")
    llm_api_key: str | None = Field(default=None, description="本地 LLM API Key")
    llm_base_url: str | None = Field(
        default=None, validation_alias="LANGCHAIN_URL",
        description="本地 LLM 服务地址（兼容旧 .env 变量名 LANGCHAIN_URL）",
    )

    llm_temperature: float = Field(
        default=0.4, ge=0, le=2,
        description="LLM 温度。0=确定性最强，1=最随机。测试用例生成建议 0.3~0.5",
    )

    # -- 结构化输出重试 --
    max_retries: int = Field(
        default=2, ge=0, le=5,
        description="LLM 结构化输出校验失败后最大重试次数",
    )

    # -- 深度思考开关 --
    enable_thinking: bool = Field(
        default=False,
        description="启用 DeepSeek V4 深度思考模式。会增加 token 消耗和耗时",
    )

    # ================================================================
    # 节点: Excel 计划生成 (generate_excel_plan)
    # 影响范围: nodes.py → _generate_excel_plan_node()
    # ================================================================

    excel_repair_attempts: int = Field(
        default=3, ge=1, le=10,
        description="Excel 计划校验失败后自动修复的最大尝试次数",
    )

    # ================================================================
    # 节点: YAML 数据文件生成 (generate_all_yamls)
    # 影响范围: nodes.py → ThreadPoolExecutor
    # ================================================================

    yaml_concurrency: int = Field(
        default=5, ge=1, le=20,
        description="YAML 文件并发生成线程数",
    )

    # -- 后台任务线程池 --
    task_max_workers: int = Field(
        default=10, ge=1, le=50,
        description="后台任务线程池大小",
    )
    task_max_queue: int = Field(
        default=30, ge=1, le=200,
        description="后台任务队列上限（排队 + 运行 ≤ max_workers + max_queue）",
    )

    # ================================================================
    # 节点: Web 服务 (FastAPI)
    # 影响范围: web_app.py
    # ================================================================

    web_host: str = Field(default="0.0.0.0", description="FastAPI 绑定地址")
    web_port: int = Field(default=8000, ge=1, le=65535, description="FastAPI 绑定端口")

    # -- 上传限制 --
    upload_max_size_mb: int = Field(
        default=100, ge=1, le=1024,
        description="单文件上传大小上限（MB）",
    )

    # -- Phase C 工作流会话超时 --
    workflow_session_ttl: int = Field(
        default=1800, ge=60, le=86400,
        description="Phase C 会话超时时间（秒）。超时后用户需重新开始对话",
    )

    # -- 输出路径 --
    testcase_base: str = Field(
        default="./testcase_out",
        description="测试用例（Excel / .py / .yaml）输出根目录",
    )

    # -- 任务状态过期 --
    task_ttl_seconds: int = Field(
        default=3600, ge=60,
        description="后台任务状态保留时间（秒）。超时后 GET /task/{id} 返回 404",
    )

    # ================================================================
    # 节点: 日志 (observability)
    # 影响范围: observability.py
    # ================================================================

    log_dir: str = Field(default="./logs", description="日志文件目录")
    log_level: str = Field(default="INFO", description="日志级别: DEBUG/INFO/WARNING/ERROR")

    # ================================================================
    # 节点: 文件删除 (delete-file)
    # 影响范围: web/routes/files.py
    # ================================================================

    chroma_retry_delay: int = Field(
        default=300, ge=10, le=3600,
        description="ChromaDB 删除重试延迟（秒）。文件删除时若 ChromaDB 不可用，等待此时间后重试",
    )

    # ================================================================
    # 计算字段（自动推导，无需手动配置）
    # ================================================================

    @property
    def deepseek_ready(self) -> bool:
        """DeepSeek API 三个参数是否全部配置。"""
        return bool(self.deep_url and self.deep_api_key and self.deep_model)

    @property
    def llm_provider(self) -> str:
        """当前活跃的 LLM 提供商。"""
        return "deepseek" if self.deepseek_ready else "local"

    @property
    def active_llm_model(self) -> str:
        """当前活跃的 LLM 模型名。"""
        if self.deepseek_ready:
            return self.deep_model or ""
        return self.llm_model or ""

    @property
    def active_llm_base_url(self) -> str:
        """当前活跃的 LLM 服务地址。"""
        if self.deepseek_ready:
            return self.deep_url or ""
        return self.llm_base_url or ""

    @property
    def active_llm_api_key(self) -> str:
        """当前活跃的 LLM API Key。"""
        if self.deepseek_ready:
            return self.deep_api_key or ""
        return self.llm_api_key or ""


# 模块级单例 — import 时自动加载 .env + 校验必填项
settings = Settings()
