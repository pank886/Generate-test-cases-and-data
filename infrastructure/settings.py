"""配置中心 — 按业务节点组织。

配置分为两类：
  A. 模型地址 / API Key  →  在项目根目录 .env 文件中配置（敏感信息 / 环境差异）
  B. 其余所有可调参数     →  直接修改本文件各 Field 的 default 值

启动时自动校验必填项（EMBEDDING_MODEL 等）。

Field 参数速查:
  default   = 默认值
  ge        = 最小值（greater or equal）
  le        = 最大值（less or equal）
"""

from dotenv import load_dotenv
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# .env 仅用于模型地址和 API Key，加载到 os.environ 供 pydantic-settings 读取
load_dotenv()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    # ================================================================
    # .env 可配置项：Embedding 模型（必填，为空时启动报错）
    # ================================================================

    embedding_model: str = Field(
        default="",
        description="Ollama Embedding 模型名。必填，如 bge-m3 / nomic-embed-text。CPU 模式建议 bge-m3-cpu",
    )
    embedding_url: str = Field(
        default="http://localhost:11434",
        description="Ollama 服务地址。如果 Ollama 跑在其他机器上，改这里",
    )

    # ================================================================
    # .env 可配置项：LLM 模型（自动: DeepSeek 配了就用，否则本地）
    # ================================================================

    deep_url: str | None = Field(
        default=None,
        description="DeepSeek API 地址。配了即启用；设为空则降级本地 LLM",
    )
    deep_api_key: str | None = Field(
        default=None,
        description="DeepSeek API Key。从 platform.deepseek.com 获取",
    )
    deep_model: str | None = Field(
        default=None,
        description="DeepSeek 模型名，如 deepseek-v4-pro。不同模型价格和能力差异大",
    )

    llm_model: str | None = Field(
        default=None,
        description="本地 LLM 模型名（DeepSeek 未配时使用），如 qwen2.5:7b",
    )
    llm_api_key: str | None = Field(
        default=None,
        description="本地 LLM API Key。Ollama 等本地服务可填任意值",
    )
    llm_base_url: str | None = Field(
        default=None,
        description="本地 LLM 服务地址。Ollama 通常为 http://localhost:11434/v1",
    )

    # ================================================================
    # 以下所有参数仅在 settings.py 中修改默认值，.env 不生效
    # ================================================================

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
        description="ChromaDB 持久化目录。存储所有文档的 embedding 向量，删除后需重新导入",
    )
    collection_product_docs: str = Field(
        default="product_docs",
        description="[已废弃，保留兼容] 产品文档 Collection 名。合并后统一使用 collection_doc_search",
    )
    collection_api_defs: str = Field(
        default="api_defs",
        description="[已废弃，保留兼容] 接口定义 Collection 名。合并后统一使用 collection_doc_search",
    )
    collection_doc_search: str = Field(
        default="doc_search",
        description="统一文档检索 Collection 名（合并 product_docs + api_defs）。跨类型语义检索",
    )

    # ================================================================
    # 节点: 向量检索 (retrieve)
    # 影响范围: nodes.py → ChromaDB.similarity_search()
    # ================================================================

    embedding_timeout: int = Field(
        default=180, ge=10, le=600,
        description="单次 Embedding HTTP 请求超时（秒）。CPU 模式或长文本时需调大；过小导致超时回滚，过大则异常时等待久",
    )

    # -- 检索召回数量 --
    retrieval_k: int = Field(
        default=200, ge=1, le=200,
        description="向量检索返回的文本块数量。增大可提升召回覆盖率，但 LLM 上下文消耗增加",
    )

    # -- 通用基础服务模块名 --
    common_service_module: str = Field(
        default="公共基础服务",
        description="检索接口定义时额外追加的通用模块名（如登录、文件上传等公共接口）。按项目命名习惯修改",
    )

    # ================================================================
    # 节点: LLM 调用 (parse_api / excel_plan / py_file / yaml)
    # 影响范围: agents/nodes.py → _invoke_structured() → DeepSeekChatOpenAI
    # ================================================================

    llm_temperature: float = Field(
        default=0.4, ge=0, le=2,
        description="LLM 温度。0=确定性最强，1=最随机。测试用例生成建议 0.3~0.5",
    )

    llm_max_tokens: int = Field(
        default=393216, ge=1024, le=393216,
        description="LLM API max_tokens。大 Excel 计划 JSON 输出可能超 8192 默认值导致截断，"
                    "此处显式设置并在每次 API 调用时 bind，防止 LangChain 链式 bind() 丢失该参数",
    )

    # -- 结构化输出重试 --
    max_retries: int = Field(
        default=2, ge=0, le=5,
        description="LLM 结构化输出校验失败后最大重试次数。增大可提高成功率但增加 token 消耗和延迟",
    )

    # -- 深度思考开关 --
    enable_thinking: bool = Field(
        default=False,
        description="启用 DeepSeek V4 深度思考模式。会增加 token 消耗和耗时。结构化输出节点自动禁用",
    )

    # ================================================================
    # 节点: Excel 计划生成 (generate_excel_plan)
    # 影响范围: nodes.py → _generate_excel_plan_node()
    # ================================================================

    excel_repair_attempts: int = Field(
        default=3, ge=1, le=10,
        description="Excel 计划校验失败后自动修复的最大尝试次数。每轮仅重填失败行，增大可提高通过率但增加耗时",
    )

    # -- Phase B-2 依赖映射生成 --
    dependency_repair_attempts: int = Field(
        default=2, ge=0, le=5,
        description="dependency_map.json 校验失败后自动修复的最大尝试次数。与 EXCEL_REPAIR_ATTEMPTS 独立，仅影响 dep_map 生成节点",
    )

    # -- 思考节点 LLM 调用超时（Phase B-2 / Phase C thinking） --
    thinking_timeout: int | None = Field(
        default=None, ge=10, le=1200,
        description="thinking LLM 调用超时（秒）。None=沿用 LangChain 客户端默认（约 600s）。Phase B-2 和 Phase C _thinking_per_story 共用此配置",
    )

    # -- 数据库表结构信息（2026-08-04 问题 2：占位，暂不传值） --
    db_schema: str = Field(
        default="",
        description="数据库表结构信息（占位，当前不传实际值）。为空时禁止生成 db/SQL 断言"
                    "（无表结构无法写正确 SQL），用例生成与 YAML 数据生成两节点均拦截，改用 eq/contains/ne",
    )

    # -- Phase B 资源冲突消解关键词 --
    resource_mutate_keywords: list[str] = Field(
        default=[
            # 删除类
            "删除", "移除", "销毁", "删掉", "清空",
            "DELETE", "/del", "/remove", "/delete",
            # 修改类
            "修改", "更新", "编辑", "调为", "变更",
            "UPDATE", "PUT", "PATCH", "/modify", "/edit",
            # 新增类
            "新增", "添加", "创建", "增加",
            "POST", "/add", "/create",
        ],
        description="写操作关键词（中英文）。LLM 漏标 mutates_data 时代码兜底匹配。可追加业务特定动词",
    )

    # ================================================================
    # 节点: YAML 数据文件生成 (generate_all_yamls)
    # 影响范围: generators.py → ThreadPoolExecutor
    # ================================================================

    yaml_concurrency: int = Field(
        default=5, ge=1, le=20,
        description="YAML 文件并发生成线程数。受 LLM API 并发限制约束",
    )

    yaml_repair_rounds: int = Field(
        default=1, ge=0, le=3,
        description="YAML 修复轮数。失败项集中送思考节点自查后重生成；0=不修复直接计失败",
    )

    yaml_single_node: bool = Field(
        default=True,
        description="YAML 单节点生成开关：thinking + json_object 一次调用生成 TestData（省一半 LLM 调用），"
                    "thinking 走 reasoning_content。2026-08-12 灰度通过（single 87/92 > two_stage 83/92），"
                    "默认开启；置 False 可回退两段式",
    )

    # -- Phase C 全局熔断 --
    yaml_failure_circuit_breaker: float = Field(
        default=0.5, ge=0.1, le=1.0,
        description="YAML 全量轮失败率熔断阈值。首轮失败率超过此值 → 终止并报错（防止 prompt/骨架缺陷导致批量失败、token 失控）",
    )

    # -- 后台任务线程池 --
    task_max_workers: int = Field(
        default=9, ge=1, le=50,
        description="后台任务线程池大小（预留1个给补偿worker）。超过此数的新任务排队等待",
    )
    task_max_queue: int = Field(
        default=30, ge=1, le=200,
        description="后台任务队列上限。排队任务数超过此值则拒绝新任务（背压保护）",
    )

    # -- 批量摘要（讨论6） --
    llm_global_concurrency: int = Field(
        default=5, ge=1, le=20,
        description="LLM 全局并发调用上限。多 API key 可适当增加",
    )
    batch_summary_chunk_size: int = Field(
        default=5, ge=1, le=20,
        description="每批 chunks 数量（一次 LLM 调用处理的 chunk 数）",
    )

    # -- 补偿 worker（讨论5） --
    compensation_poll_interval: int = Field(
        default=30, ge=5, le=600,
        description="补偿 worker 轮询间隔（秒）",
    )
    compensation_max_retries: int = Field(
        default=3, ge=1, le=10,
        description="补偿任务最大重试次数",
    )

    # ================================================================
    # Axure 解析器 (页面块四段)
    # 影响范围: agent_components/axure_parser.py
    # ================================================================

    axure_form_markers: list[str] = Field(
        default=["确定", "取消", "新增", "添加", "编辑", "查看"],
        description="Axure 弹窗判定表单标记。ax_default_hidden 容器内状态内容含任一标记，"
                    "或状态标签本身有业务含义（非 状态N/StateN 占位），即视为弹窗子块",
    )

    # ================================================================
    # 节点: Web 服务 (FastAPI)
    # 影响范围: web_app.py
    # ================================================================

    web_host: str = Field(
        default="0.0.0.0",
        description="FastAPI 绑定地址。0.0.0.0=所有网卡可访问，127.0.0.1=仅本机",
    )
    web_port: int = Field(
        default=8000, ge=1, le=65535,
        description="FastAPI 绑定端口。冲突时改这里",
    )

    # -- 上传限制 --
    upload_max_size_mb: int = Field(
        default=100, ge=1, le=1024,
        description="单文件上传大小上限（MB）。超过此值前端拦截 + 服务端拒绝",
    )

    # -- Phase C 工作流会话超时 --
    workflow_session_ttl: int = Field(
        default=1800, ge=60, le=86400,
        description="Phase C 会话超时时间（秒）。超时后用户需重新开始对话",
    )

    # -- 输出路径（必须配置！未配置则启动报错） --
    testcase_base: str = Field(
        default="testcase",
        description="测试用例输出子目录名（相对于 PYCHARM_MISC），必填，如 pytest_test_data",
    )
    pycharm_misc: str = Field(
        default="C:\\Users\\damai\\PyCharmMiscProject",
        description="目标 PyCharm 项目根路径，必填，如 C:\\Users\\damai\\PycharmMiscProject。Excel/PY/YAML 均输出到其下的 testcase_base 目录",
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

    log_dir: str = Field(
        default="./logs",
        description="日志文件目录。app.log / thinking_trace.log / repair_failures.log 均存于此",
    )
    log_level: str = Field(
        default="INFO",
        description="日志级别。DEBUG 输出所有细节，WARNING 仅警告和错误，生产环境建议 INFO",
    )

    # ================================================================
    # 节点: 文件删除 (delete-file)
    # 影响范围: web/routes/files.py → dual_chroma.py
    # ================================================================

    chroma_retry_delay: int = Field(
        default=300, ge=10, le=3600,
        description="ChromaDB 删除失败后延迟重试时间（秒）。文件删除时若 ChromaDB 不可用，异步等待后自动重试",
    )

    # ================================================================
    # 计算字段（自动推导，无需手动配置）
    # ================================================================

    @property
    def deepseek_ready(self) -> bool:
        """DeepSeek API 三个参数是否全部配置。True 则优先使用 DeepSeek。"""
        return bool(self.deep_url and self.deep_api_key and self.deep_model)

    @property
    def llm_provider(self) -> str:
        """当前活跃的 LLM 提供商：deepseek 或 local。"""
        return "deepseek" if self.deepseek_ready else "local"

    @property
    def active_llm_model(self) -> str:
        """当前生效的 LLM 模型名。"""
        if self.deepseek_ready:
            return self.deep_model or ""
        return self.llm_model or ""

    @property
    def active_llm_base_url(self) -> str:
        """当前生效的 LLM 服务地址。"""
        if self.deepseek_ready:
            return self.deep_url or ""
        return self.llm_base_url or ""

    @property
    def active_llm_api_key(self) -> str:
        """当前生效的 LLM API Key。"""
        if self.deepseek_ready:
            return self.deep_api_key or ""
        return self.llm_api_key or ""


# 模块级单例 — import 时自动加载 .env（仅模型地址 / Key）+ 校验必填项
settings = Settings()
