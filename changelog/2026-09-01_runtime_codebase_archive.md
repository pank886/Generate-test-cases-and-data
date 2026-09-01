# 2026-09-01 全仓运行时代码档案

> 本文件为**全仓代码档案（方法级）**，用途：在决定整体目录结构重构前，把除 `tests/`、`.env`、`.claude`（另排除 `.venv`、`backups`、`logs`、`__pycache__`）之外的所有运行时 `.py` 文件逐文件建档，记录每个文件「做什么、依赖谁、被谁引用、有哪些类/方法/函数」。全部为只读扫描，未改动任何代码。

## 扫描范围

- **纳入**：根级 8 文件 + `prompts/` + `data_factory/` + `database/` + `ingest/` + `agent_components/`（含 generators/llm/validation 子包）+ `web/` + `scripts/`，共 **~61 个 .py 文件，~12,000 行**
- **排除**：`tests/`、`.env`、`.claude`、`.venv`、`backups/`、`logs/`、`__pycache__`、`.git/`
- **特殊登记**：`apikey.py` 不提交 git，仅登记存在与用途，不展开方法级细节

## 总览（按行数降序）

| 文件 | 行数 | 一句话用途 | 域 |
|---|---|---|---|
| prompts/response_model.py | 1274 | LLM 输出 Pydantic 模型唯一权威定义 + schema 校验自动修复 | prompts |
| agent_components/axure_parser.py | 1416 | Axure 演示包解析 → 四段结构产品文档块 | agent_components |
| web/tasks.py | 926 | 后台异步任务（上传入库/计划生成/工作流恢复/接口提交） | web |
| ingest/api_parser.py | 855 | 接口文档纯代码解析（YApi MD/纯 MD/YApi JSON） | ingest |
| agent_components/nodes.py | 809 | LangGraph 节点容器类 ChatTestAgentGraph | agent_components |
| ingest/pipelines.py | 801 | 五条入库流水线编排中枢 | ingest |
| agent_components/generators/yaml_gen.py | 639 | Phase C YAML 三阶段生成 Mixin | agent_components |
| agent_components/retrievers.py | 625 | Phase B 多跳检索 Mixin | agent_components |
| prompts/extraction_prompts.py | 622 | 全部 LLM 提示词构建函数集 | prompts |
| web/routes/modules.py | 433 | 模块管理路由 | web |
| web/app.py | 428 | FastAPI 应用装配层 | web |
| settings.py | 379 | 配置中心（pydantic-settings + .env） | 根 |
| web/routes/files.py | 378 | 文件管理路由 | web |
| agent_components/validation/case_validator.py | 350 | 用例生成检测（Excel 计划校验） | agent_components |
| prompts/definitions.py | 321 | PromptFactory | prompts |
| scripts/migrate_chroma_to_sqlite.py | 315 | ChromaDB→SQLite 一次性迁移脚本 | scripts |
| web/routes/docs.py | 309 | 文档操作路由 | web |
| database/models.py | 296 | 8 个 ORM 模型 | database |
| agent_components/validation/yaml_validator.py | 257 | YAML 数据生成检测 | agent_components |
| observability.py | 246 | 结构化 JSON 日志 + trace_id + thinking 日志 | 根 |
| agent_components/generators/py_export.py | 245 | pytest .py 测试文件生成 Mixin | agent_components |
| web/routes/chat.py | 241 | 测试生成路由 | web |
| agent_components/dual_chroma.py | 238 | 统一向量检索引擎封装 | agent_components |
| agent_components/generators/excel.py | 230 | Excel 用例读取 + 依赖映射生成 Mixin | agent_components |
| agent_components/generators/_repair_helpers.py | 228 | Phase C 生成后处理辅助函数 | agent_components |
| database/operations/bindings.py | 219 | 绑定关系 CRUD + 关联模块发现 | database |
| web/compensation.py | 214 | 补偿 Worker 线程 | web |
| agent_components/llm_client.py | 214 | LLM 客户端单例 + 通用调用封装 | agent_components |
| database/operations/modules.py | 182 | 模块树 CRUD | database |
| agent_components/graph_logging.py | 182 | 工作流日志辅助 | agent_components |
| agent_components/llm/deepseek.py | 178 | DeepSeek LLM 适配器 | agent_components |
| agent_components/api_annotations.py | 178 | API 异常标识注册表 | agent_components |
| ingest/chunking.py | 166 | 切块 + 摘要 + 检索文本构建 | ingest |
| database/__init__.py | 156 | SQLite 引擎/会话/建表/迁移 | database |
| web/routes/api_extract.py | 150 | 接口提取路由 | web |
| agent_components/generators/translation.py | 133 | 中文标识符英文翻译 Mixin | agent_components |
| web/state.py | 129 | 全局共享状态 | web |
| data_factory/registry.py | 126 | 数据工厂方法注册表加载 | data_factory |
| ingest/storage.py | 120 | SQLite 持久化 + 补偿回滚 | ingest |
| database/operations/glossary.py | 117 | 术语表 CRUD | database |
| agent_components/graph_builder.py | 116 | LangGraph 图构建 | agent_components |
| ingest/extractors.py | 103 | 文件文本提取 + doc_id 生成 | ingest |
| config.py | 102 | 配置薄包装层 | 根 |
| apikey.py | 93 | DeepSeek 余额查询脚本（不提交） | 根 |
| agent_components/fallback_embeddings.py | 92 | Ollama 端点降级适配 | agent_components |
| database/operations/analysis.py | 91 | 模块场景分析 CRUD | database |
| database/operations/api_ops.py | 90 | API 查询（method+url 模板匹配） | database |
| database/operations/compensation.py | 78 | 补偿任务 CRUD | database |
| agent_components/generators/_helpers.py | 71 | 修复循环错误分类辅助 | agent_components |
| database/init_db.py | 70 | 建表初始化脚本 | database |
| web/routes/bindings.py | 69 | 绑定关系路由 | web |
| ingest_v2.py | 67 | Phase A 入口兼容层 | 根 |
| database/operations/docs.py | 63 | 文档 CRUD | database |
| web_app.py | 51 | Web 启动入口 | 根 |
| agent_components/llm/base.py | 48 | LLM 适配器基类 | agent_components |
| web/services/doc_binding.py | 45 | 文档-模块绑定服务 | web |
| agent_components/prompt_builder.py | 39 | prompt 变量构建单一数据源 | agent_components |
| agent_components/state.py | 34 | LangGraph 状态类型定义 | agent_components |
| agent_components/generators/__init__.py | 25 | GenerationMixin 组合层 | agent_components |
| database/operations/__init__.py | 20 | 操作类统一导出 | database |
| prompts/__init__.py | 18 | prompts 包 re-export | prompts |
| main.py | 17 | CLI 入口（已废弃） | 根 |

---

# 一、根级模块

## config.py（103 行）
**用途**：统一配置入口（薄包装层），全部配置由 settings.py 集中管理，本文件以模块级常量 re-export 兼容既有 `from config import XXX` 用法；提供 BASE_DIR 与路径解析辅助。
**依赖**：os, settings
**被谁引用**：全项目广谱引用（31 处）
**对外暴露**：一组模块级常量（向量库/Embedding/LLM/工作流开关/Web/输出路径/日志/Axure/节点可调参数），`LLM_API_KEY()` 运行时读取（避免 import 时暴露敏感信息），`_resolve_path()` 相对路径转 BASE_DIR 绝对路径，`BASE_DIR` 基于 `__file__`。

## settings.py（380 行）
**用途**：配置中心，按业务节点组织。A 类（.env 配置）模型地址/API Key；B 类可调参数改 Field 默认值。启动时实例化模块级单例并校验必填项。
**依赖**：python-dotenv, pydantic, pydantic-settings
**对外暴露**：`Settings` 类 + 单例 `settings`
**类**：
- `Settings(BaseSettings)` — 全系统配置模型，字段按节点分组：Embedding（embedding_model 必填）、DeepSeek（deep_url/deep_api_key/deep_model）、LLM 降级（llm_model/api_key/base_url）、文件上传（chunk_size/chunk_overlap/collection_doc_search）、向量检索（retrieval_k/common_service_module）、LLM 调用（temperature/max_tokens/max_retries/enable_thinking）、Excel 生成（excel_repair_attempts/db_schema/resource_mutate_keywords）、YAML 生成（yaml_concurrency/yaml_repair_rounds/yaml_single_node/yaml_failure_circuit_breaker/task_max_workers）、Axure、Web（host/port/upload_max_size_mb/workflow_session_ttl）、日志
  - `deepseek_ready` / `llm_provider` / `active_llm_model` / `active_llm_base_url` / `active_llm_api_key` — 计算属性：当前生效提供商与模型参数

## observability.py（246 行）
**用途**：结构化 JSON 日志写 `logs/app.log` + 控制台输出，每个 HTTP 请求经 ContextVar 贯穿 trace_id；thinking 节点专属日志 + 敏感字段脱敏。
**依赖**：json, logging, contextvars, uuid, config
**被谁引用**：广谱引用（27 处）
**对外暴露**：get_trace_id / set_trace_id / generate_trace_id / init_logging / get_logger / get_thinking_logger / log_phase_header / log_thinking，类 JSONFormatter、TraceFilter
**类**：`JSONFormatter`（JSON 行 + 脱敏）、`TraceFilter`（注入 trace_id）
**函数**：`_sanitize`（按 SENSITIVE_PATTERNS 脱敏 Key/Token/Secret）、`init_logging`（幂等初始化，UTF-8 包装 stdout 防 Windows emoji 崩溃）、`get_thinking_logger`（thinking_trace.log 5MB 轮转保留 10）、`log_thinking`（记录 thinking 节点输入输出，用户输入截断 500 字）

## main.py（17 行）
**用途**：CLI 入口，交互模式已废弃，仅打印提示引导用 Web 界面。
**依赖**：sys, observability, graph_builder（import 未用）

## web_app.py（51 行）
**用途**：Web 入口，启动时强制 stdout UTF-8，daemon 线程运行 uvicorn.Server，主线程 `input()` 等待 `q`/Ctrl+C 优雅停止。
**依赖**：sys, config, web.app, threading, uvicorn

## ingest_v2.py（67 行）
**用途**：Phase A 入口兼容层，2026-08-07 拆分后实现迁至 ingest/ 包，本文件仅 re-export 全部对外符号 + 保留 CLI 入口。
**被谁引用**：16 处（web/tasks.py、web/routes/*、web/app.py、web/compensation.py、scripts/migrate_chroma_to_sqlite.py 等）
**对外暴露**：私有 `_extract_text`/`_extract_docx`/`_docx_img_dir`/`_safe_doc_id`/`_cascade_bind_to_module_docs`/`_delete_sqlite_doc`/`_save_to_sqlite`/`_save_single_chunk`/`_save_document_chunks`/`_delete_document_chunks`/`_merge_api_defs`/`_extract_valid_api_paths`/`_split_text_by_headers`/`_parse_chunk_summaries`/`_generate_batch_summaries`/`_build_api_search_text`/`_build_doc_search_text`；公有 `extract_apis_from_yapi_md`/`extract_apis_from_yapi_json`/`process_product_doc`/`process_api_doc`/`process_api_doc_extract`/`commit_api_docs`/`process_axure_zip`；另 `get_chroma_db`

## data_factory/registry.py（126 行）
**用途**：数据工厂方法注册表加载，单一事实源 `data_factory/methods.yaml`（v2 分类结构），三消费方：prompt 渲染（render_for_prompt）、占位符校验器（load_methods/get_validation_rules，import 方向 prompts→data_factory 无环）、单元测试。
**依赖**：os, threading, yaml（config 惰性 import）
**被谁引用**：prompts/response_model.py, agent_components/llm_client.py, tests
**对外暴露**：reset_cache / load_methods / get_validation_rules / render_for_prompt

---

# 二、prompts 层

## prompts/response_model.py（1274 行）
**用途**：LLM 结构化输出模型唯一权威定义 + 校验层自动修复（防御体系核心）。覆盖对话/接口提取/Excel 计划 V1/V2/YAML 测试数据/依赖映射全链路；对 LLM 常见结构错误确定性自动修复，无法修复的抛 ValueError 进重生成。
**依赖**：pydantic, re, logging（惰性 import agent_components.api_annotations、data_factory.registry）
**被谁引用**：nodes/state/retrievers/generators 各文件、ingest/pipelines、web/tasks、web/compensation 及大量测试
**对外暴露**（经 prompts/__init__.py）：ProperResponse、ApiDefinition、TestData、ExcelRow、ExcelPlan
**类**：
- `ValidationInterceptor` — schema 校验拦截统计器（reset/record/write_report → logs/VALIDATION_INTERCEPT.md）
- `ProperResponse` — 对话回复（proper_thinking/final_response/worth_to_remember）
- `ApiDefinition` — 接口定义，`normalize_old_keys` 兼容旧 key（return/headers/parameters）
- `SharedPrecondition` / `TestCaseRow` — Excel 双 Sheet 行模型（前置/用例，含 mutates_data、is_negative_test 元数据）
- `ExcelPlanV2` / `ExcelRow` / `ExcelPlan` — Excel 计划 V2/V1
- `TestCase` — YAML data 元素，**校验最重**（10 个 validator）：migrate_data_to_json（data→json 漂移迁移+统计）、auto_fix_validation_list、validate_body_exclusivity（json/params/data 三选一）、strip_empty_optional_dicts、merge_same_type_validations、validate_validation_element_is_dict、validate_no_neq_operator（只认 eq/contains/ne/db）、validate_extract_jsonpath、auto_fix_extract_jsonpath、validate_validation_not_empty
- `StepData` — 单步测试数据：auto_fix_step_structure、normalize_base_info、validate_url_no_placeholder、validate_header_exists、validate_no_params_in_baseinfo、validate_method_body_match
- `TestData` — 顶层：auto_fix_top_level_structure（6 种结构漂移）、validate_placeholders（B1-B4）、validate_no_db_when_no_schema、validate_export_assertion、validate_no_fuzzy_jsonpath
- `DataPlanStep` / `TestPointItem` / `IntentConfirmation` / `GlossaryExtract` / `DocModuleExtract` / `ApiDefExtract` / `TranslationResult` — Phase C/提取各产物
- `InternalDependency` / `CrossModuleDep` / `StoryDependencyMap` / `DependencyMap` — 数据依赖映射（validate_key_consistency 三表 key 一致）
**函数/常量**：`set_db_schema_empty`、`_PLACEHOLDER_RE`、`_PLACEHOLDER_CALL_RE`、`_DB_SCHEMA_EMPTY`（默认 True）、`_drift_total/_drift_count`（漂移统计）

## prompts/extraction_prompts.py（622 行）
**用途**：Phase A/B/C 全部 LLM 提示词构建函数集（每函数返回 ChatPromptTemplate）。按约束只含规则表述、不含示例。
**被谁引用**：nodes、yaml_gen、excel、translation、pipelines、chunking、web/tasks、web/compensation
**函数**：product_doc_extract_prompt / glossary_extract_prompt / api_def_extract_prompt / repair_excel_plan_prompt / translate_to_en_prompt / generate_yaml_data_single_prompt（v3 结构，# 角色/数据工厂/schema/15 条铁律）/ generate_dependency_map_prompt / repair_dependency_map_prompt / batch_chunk_summary_prompt / analyze_product_scenarios_prompt / analyze_axure_ui_flow_prompt / analyze_api_mapping_prompt
**常量**：`SETUP_CAPTURE_RULE`（共享前置 setup 块铁律：资源标识必须 input_extract 捕获）、`YAML_ANALYSIS_GUIDE`（单节点生成 5 条分析要点引导）
**注**：旧两段式 prompt（analyze/repair/format_yaml_data_prompt）已整段注释（2026-08-24 收敛单节点）

## prompts/definitions.py（321 行）
**用途**：PromptFactory——业务节点 prompt 方法封装；字段 JSON Schema 已全部迁至 response_model.py，本文件仅维护 prompt 文本。
**被谁引用**：nodes、retrievers
**类**：`PromptFactory` — generate_excel_plan_node / analyze_test_points_raw / generate_excel_plan_thinking（新版一步生成，json_schema 注入，含逆向用例比例、共享前置引用规范、枚举不写死）/ confirm_user_intent

## prompts/__init__.py（18 行）
**用途**：re-export `__all__ = ["ProperResponse", "ApiDefinition", "TestData", "ExcelRow", "ExcelPlan", "PromptFactory"]`

---

# 三、database 层

## database/models.py（296 行）
**用途**：8 个 ORM 模型，SQLite 存储层数据结构蓝图。模块↔文档↔术语生命周期与绑定规则（不可重复、A→B 禁 B→A）在头注释说明。
**依赖**：sqlalchemy, uuid, datetime（from database import Base）
**被谁引用**：几乎整个系统
**类**：
- `Module` — 模块树节点（parent_id 邻接表，children lazy=selectin）
- `Document` — 三种文档（product/api/axure）统一抽象，api 有专属 api_* 列，glossary_terms 级联删除
- `Binding` — 绑定关系，`normalize` 静态方法防 A→B/B→A 重复，UNIQUE 四字段
- `GlossaryTerm` — 业务术语，kind 区分 required/filter/explanation
- `PageImage` — Axure 页面原图 image_data BLOB 直存 SQLite
- `ModuleAnalysis` — 模块场景+接口映射分析
- `DocumentChunk` — 文档切块（原文 + 两阶段摘要）
- `CompensationTask` — 后台补偿任务队列（status 索引）

## database/__init__.py（156 行）
**用途**：数据层核心入口——SQLite 引擎/会话（线程安全单例 + WAL + 外键）、get_session_ctx、init_db 幂等建表 + 自动迁移缺失列 + 首次播种。
**对外暴露**：`Base` / `DB_DIR` / `DB_PATH` / `get_engine()` / `get_session()` / `get_session_ctx()` / `init_db()`
**函数**：`_migrate_db`（自动 ADD COLUMN 历次迁移）、`_ensure_columns`、`_seed_from_json_if_empty`（空库从 data/modules.json 播种）

## database/init_db.py（70 行）
**用途**：独立初始化脚本，UTF-8 强制 + init_db 建表 + inspect 打印表清单/模型关系 ASCII 图。

## database/operations/__init__.py（20 行）
**用途**：统一导出 `__all__ = ["DocOps", "ModuleOps", "BindingOps", "GlossaryOps", "AnalysisOps", "CompensationOps", "ApiOps"]`

## database/operations/bindings.py（220 行）
**用途**：绑定关系 CRUD + 关联模块发现共享函数。Phase B/C 复用同一关联模块口径。
**类**：`BindingOps` — bind / unbind / unbind_by_pair / get_bindings / get_partners / get_partners_batch（批量防 N+1）/ delete_bindings_for_doc / get_bound_docs，全部 staticmethod 以 session 为第一参数
**函数**：`discover_related_modules(session, module_name)` — 三路召回（module↔module、product/axure 文档→其他模块、API 文档→其他模块），被 web/tasks.py、retrievers.py、tests 引用

## database/operations/modules.py（182 行）
**用途**：模块树 CRUD + 层级路径计算（BFS 批量刷新）+ 重命名/删除/合并对 bindings 引用联动更新。
**类**：`ModuleOps` — create_module / get_by_name / get_all / get_tree（递归树 dict）/ rename_module / delete_module（非叶子禁止）/ merge_modules / get_descendants；私有 _calc_path、_refresh_paths（BFS O(N) 防递归 O(N×D)）

## database/operations/glossary.py（117 行）
**用途**：术语表 CRUD，随产品文档生命周期；支持按 source_doc 批量替换、按模块聚合视图。
**类**：`GlossaryOps` — add_term / update_term / delete_term / get_terms / replace_terms（先删旧再插新）/ get_terms_for_module（聚合绑定文档术语）

## database/operations/analysis.py（91 行）
**用途**：module_analysis 表 CRUD，旧版 JSON 单列 upsert + 新版三步分析（scenario/ui_flow/api）独立列 upsert。
**类**：`AnalysisOps` — get_by_module_id / upsert（version++）/ delete_by_module_id / upsert_3step（空字符串跳过不覆盖）

## database/operations/api_ops.py（90 行）
**用途**：API 查询（单一事实源 documents 表 api_* 列），method+url 精确匹配 + L3 段级模板通配回退（多候选歧义放弃）。
**类**：`ApiOps.get_by_url(session, method, url)`
**函数**：`_deserialize`、`_template_match`（`{param}` 段通配）

## database/operations/compensation.py（78 行）
**用途**：补偿任务 CRUD（simple_summary/analyzed_summary/chroma_rebuild/api_search_text）。
**类**：`CompensationOps` — create / fetch_pending（FIFO）/ mark_running / mark_success / mark_failed（retry_count+1，达 max_retries 置 failed 否则回 pending）

## database/operations/docs.py（63 行）
**用途**：文档 CRUD，删除时级联清理（glossary DB 级联、bindings 显式清理）+ 未绑定文档查询。
**类**：`DocOps` — get_document / get_all_documents / delete_document / get_unassociated_docs（全量减已绑定差集）

---

# 四、ingest 层

## ingest/api_parser.py（855 行）
**用途**：接口文档纯代码解析器（无外部业务依赖），YApi 导出 MD/纯 Markdown/YApi JSON 三形态归一化为 `{name,url,method,description,header,body,return,annotations}`，输出 `(METHOD,URL)` 白名单供下游过滤 LLM 幻觉接口。
**函数**：`_parse_md_table`/`_parse_html_table`（六字段数组，YApi span padding-left 缩进树→children）/ `_parse_header_table` / `_extract_json_example` / `_apply_examples` / `_detect_api_level` / `_split_text_by_headers` / `_split_clean_api_sections` / `_parse_yapi_section` / `_parse_clean_md_section` / `extract_apis_from_yapi_md` / `_schema_to_field`/`_parse_json_schema_fields`（JSON Schema→六字段）/ `_yapi_json_api_to_def` / `extract_apis_from_yapi_json`（跨分类 method+url 去重）/ `_coerce_api_format`（幂等归一化）/ `_merge_api_defs` / `_extract_valid_api_paths`

## ingest/pipelines.py（801 行）
**用途**：五条流程入口编排（product/api/axure）。产品与 Axure 走「提取→切块→LLM 分析→SQLite→摘要→ChromaDB（失败补偿回滚）」；接口两阶段（extract 只提取确认 / commit 确认入库）。统一「SQLite 先写→ChromaDB 后写→失败补偿回滚」一致性原则。
**对外暴露**：process_product_doc / process_api_doc（已弃用兼容层）/ process_api_doc_extract（线程池 5 并发 LLM 提取不入库）/ commit_api_docs（批量入库 + 逐条 ChromaDB，失败回滚）/ process_axure_zip（页面块/术语入库 + 兜底内嵌图片直存 SQLite）
**嵌套函数**：`_group_chunks_into_batches`、`_extract_one`、`_extract_page_name`；辅助 `_page_has_extractable_content`、`_locate_embedded_image`、`_save_embedded_page_images`

## ingest/chunking.py（166 行）
**用途**：切块与检索文本构建——批量生成 simple_summary（LLM，5 chunks/批，失败写补偿任务）+ API/产品/Axure 检索文本构造。
**函数**：`_parse_chunk_summaries`（===CHUNK_SUMMARY=== 正则解析）、`_generate_batch_summaries`（补偿回退 CompensationOps.create）、`_build_api_search_text`（方法+URL+名称+描述+参数[≤20]+返回值[≤10]+标签）、`_build_doc_search_text`（analyzed_summary > simple_summary > content 前 500 字）

## ingest/storage.py（120 行）
**用途**：SQLite 持久化 + 补偿回滚。
**函数**：`_cascade_bind_to_module_docs`（绑模块时自动级联 doc↔doc 绑定）、`_delete_sqlite_doc` / `_delete_document_chunks`（ChromaDB 失败补偿动作）、`_save_to_sqlite` / `_save_single_chunk` / `_save_document_chunks`

## ingest/extractors.py（103 行）
**用途**：文件文本提取与 doc_id 生成（PDF/MD/TXT/JSON/DOCX）；DOCX 图片占位标记存临时目录。
**函数**：`_extract_text` / `_extract_docx`（段落+表格 [表格]块）/ `_docx_img_dir` / `_safe_doc_id`（消毒 + 180 字符超限 md5）

## ingest/__init__.py（45 行）
**用途**：re-export 五子模块全部对外符号。

**ingest 层上下游小结**：`extractors`（文本提取叶）+ `api_parser`（接口解析叶）→ `chunking`（切块/摘要）+ `storage`（持久化）→ `pipelines`（编排中枢，串联 ChromaDB + LLM）。

---

# 五、agent_components

## 5.1 LangGraph 核心组

### nodes.py（809 行）
**用途**：LangGraph 节点容器 `ChatTestAgentGraph`——Excel 计划 thinking 一步生成、校验/修复/落盘、资源冲突消解 + 指向拆分模块的薄转发。Phase B 检索在 RetrievalMixin、Phase C 生成在 GenerationMixin。
**对外暴露**：re-export `reload_llm`/`_get_llm`（来自 llm_client）；模块级 `METHOD_FEATURES`、`_quality_gate_decision`；类 `ChatTestAgentGraph`
**类**：
- `ChatTestAgentGraph(RetrievalMixin, GenerationMixin)`：
  - `__init__` — LLM 单例 + PromptFactory + Chroma 检索库 + 工作流日志累积器
  - `_generate_excel_plan_thinking(state, gen_warning="")` — thinking+json_object 一步生成 ExcelPlanV2，只生成不落盘
  - `_generate_excel_plan_node(state)` — 纯处理节点：校验/修复循环（ExcelPlanValidator 9 类错误 + URL + db 拦截、_quality_gate_decision、重试只接受失败 ID、PRE- 按 ID 合并）、去重、冲突消解、写双 Sheet Excel + api_defs.json + module_scope.json、文件层校验
  - `_resolve_resource_conflicts(plan, shared_pres=None)` — 纯代码冲突消解（RESOURCE_MUTATE_KEYWORDS 兜底 + PRE 隔离副本）
  - 薄转发：`_split_thinking_sections`/`_prepare_plan_prompt_vars`/`_serialize_for_log`/`_log_node_output`/`_cleanup_logs`/`_load_factory_methods`/`_invoke_think`/`_invoke_structured`
**函数/常量**：`_quality_gate_decision`（首轮 <50% regen，重试仍低 abort）、`METHOD_FEATURES`（方法特性配置表：method×thinking 兼容性）

### graph_builder.py（116 行）
**用途**：LangGraph 图构建（confirm_intent → retrieve_product_docs → extract_related_modules → retrieve_related_data → generate_plan_thinking → generate_excel_plan → END），支持条件中断（WAITING/NO_DATA）。
**函数**：`_make_initial_state`、`build_workflow`（返回 (graph, components)，含 thinking 安全包装闭包与两个路由闭包）

### state.py（34 行）
**用途**：`State(TypedDict)` — 工作流全局状态（基础/多跳检索/多轮对话/生成处理解耦字段）。

### graph_logging.py（182 行）
**用途**：工作流日志（2026-08-07 自 nodes.py 迁移，nodes 类内薄转发）。
**函数**：`split_thinking_sections`（三段分析拆分）、`serialize_for_log`、`log_node_output`（JSON+MD 写盘 + 清理）、`cleanup_logs`（成对保留 max_pairs 组）

### prompt_builder.py（39 行）
**用途**：prompt 变量单一数据源（2026-08-07 自 nodes.py 迁移）。
**函数**：`prepare_plan_prompt_vars(host, state)` — module_tree/analysis_section/shared_pre_section/cases_section/all_apis_info/db_schema/plan_source/user_context

### llm_client.py（214 行）
**用途**：LLM 客户端单例 + 通用调用封装（2026-08-07 自 nodes.py 迁移，nodes 顶部 re-export）。
**函数**：`reload_llm` / `_get_llm`（DCL 单例 DeepSeekChatOpenAI）/ `load_factory_methods`（数据工厂清单，实现归位 data_factory/registry.py）/ `invoke_think`（thinking 调用 + reasoning 采集落 thinking_trace.log）/ `_extract_reasoning_content` / `_log_reasoning_content` / `invoke_structured`（结构化输出 + method_features + pre_validate + 自动重试）

## 5.2 检索/嵌入组

### dual_chroma.py（238 行）
**用途**：统一向量检索引擎封装——**单一 Collection（doc_search）替代旧双集合，靠 metadata.doc_type 区分 api/product/axure**；正文以 SQLite 为唯一真相源、损坏可全量重建。
**对外暴露**：`DualChromaDB` 类、`get_chroma_db()` 单例
**类**：`DualChromaDB` — add_product_doc_chunks / search_product_docs / add_api_defs（page_content 优先 _search_text）/ search_api_defs / delete_by_doc_id（幂等）/ _chunks_from_chroma / get_doc_apis（优先 SQLite 列）/ _apis_from_chroma

### retrievers.py（625 行）
**用途**：Phase B 多跳检索 Mixin（nodes.py 的 ChatTestAgentGraph 继承）。
**类**：`RetrievalMixin` — _docs_to_text / _search_product_docs / _compensate_product_docs_from_sqlite / _search_api_defs / _compensate_api_defs_from_sqlite / _confirm_user_intent（节点1 意图识别，恢复路径跳过 LLM）/ _retrieve_product_docs（节点2 Hop1）/ _extract_related_modules（节点3 三路召回）/ _retrieve_related_data（节点4 Hop2a+2b）/ _analyze_test_points_raw（节点5 thinking）
**函数**：`_mod_exists_in_tree`、`_build_full_api_defs_text`（SQLite 查全量）、`_format_params`、`_fallback_api_text`、`_HTTP_METHODS`、`_parse_api_prefix`、`_dedup_api_defs`

### fallback_embeddings.py（92 行）
**用途**：Ollama Embedding 端点降级适配——优先新版 /api/embed，旧版 404 自动降级 /api/embeddings。
**类**：`FallbackOllamaEmbeddings(OllamaEmbeddings)` — embed_documents / _embed_via_old_api / aembed_documents / _aembed_via_old_api
**函数**：`_should_use_old_api` / `_mark_old_api`（按 base_url 缓存降级状态，仅首警告）

## 5.3 入站/标注组

### axure_parser.py（1416 行）
**用途**：Axure .zip 演示包解析 → 页面结构/UI 文本/必填/筛选/说明/弹窗/交互流，转四段结构产品文档块。查找策略「标准目录定位 → 递归降级搜索」，缓存页面路径。
**类**：`AxureParser` — parse（主入口：解压→sitemap→判断新旧格式→逐页提取→清理临时目录）/ to_product_doc_chunks（四段结构切分，超过 50 页截断）/ cleanup
  - Sitemap/页面发现：`_parse_sitemap` / `_flatten_pages` / `_discover_pages_from_html` / `_find_page_data_js` / `_load_page_widget_tops` / `_parse_rp9_sitemap`（document.js 压缩树解码）
  - 文件查找：`_find_data_file` / `_find_page_html_impl`
  - UI 文本：`_extract_ui_text_from_html` / `_clean_html_to_text`（不再截断 2000 字符，体积交给 chunk 切分器）
  - 必填字段：`_STAR_SPAN_RE` / `_extract_required_fields`（红色星号+后随 span，长度上限 40）
  - 筛选项：`_is_placeholder_option` / `_prev_field_label` / `_extract_filters`
  - 页面说明：`_extract_explanation` / `_extract_page_explanation`（导航容器截断剔除顶栏 + post-nav 散件簇按位剔除）/ `TOPBAR_CLASS_RE` / `_is_topbar_class` / `_is_breadcrumb_text` / `_strip_post_nav_cluster` / `_extract_embedded_images`
  - 容器操作：`_find_div_balanced_end` / `_strip_container` / `_find_direct_child_panel_states`
  - 导航/弹窗：`_html_to_text` / `_is_meaningless_state_label` / `_guess_dialog_title` / `_find_nav_container` / `_find_nav_container_id` / `_find_nav_container_start` / `_extract_block_title`（纯结构无 LLM）/ `_extract_dialogs_from_html`（每面板1块，状态合并）
  - RP9 解码：`_match_js_bracket` / `_split_js_elements` / `_decode_js_value` / `_extract_brace_content`
  - 交互流：`_extract_interactions_for_page`（策略 1 registerCaseInfo → 1.5 pageData.push → 2 键值对 → 3 HTML on[Event]，去重截断 20 条）

### api_annotations.py（178 行）
**用途**：API 异常标识注册表——入库 apply_all 自动检测写 annotations，校验 is_active 按标识精准放行，前端 get_types 渲染选项。
**对外暴露**：`normalize_api_url`（D1 消除两处漂移：去域名/query/尾斜杠）、`ApiAnnotationDef`、`ApiAnnotationRegistry`
**类**：
- `ApiAnnotationDef` — 元数据（key/label/description/category/detector 检测函数）
- `ApiAnnotationRegistry` — register / get_types / apply_all（命中且无人标注→写 auto；命中有人工→保留）/ is_active
**内置类型**：`is_export`（category=response）、`has_path_params`（category=request）；预留 `paginated_get_with_body`（注释未激活）

## 5.4 子包组（generators / llm / validation）

### generators/__init__.py（25 行）
**用途**：Phase C 组合层，re-export 各子 Mixin + _helpers 4 函数。
**类**：`GenerationMixin(ExcelMixin, TranslationMixin, PyExportMixin, YamlMixin)`

### generators/excel.py（230 行）
**用途**：Excel 测试计划读取 + Phase C Step 0 依赖映射生成。
**类**：`ExcelMixin` — `_generate_dependency_map`（LLM thinking 生成 + Pydantic 校验 + 补漏模式 + 重试）/ `_read_excel_rows`（Sheet1 9 列）/ `_read_shared_preconditions`（Sheet2 5 列）

### generators/translation.py（133 行）
**用途**：中文标识符→英文翻译（LLM + 缓存 + sanitize + 拼音降级）。
**类**：`TranslationMixin` — `_sanitize_en` / `_load_translation_cache` / `_save_translation_cache` / `_pinyin_fallback` / `_translate_to_en`

### generators/py_export.py（245 行）
**用途**：pytest .py 测试文件生成 + 断言关键词解析（C6-1）。
**类**：`PyExportMixin` — 嵌套 `AssertionParseError`、`_ASSERTION_PATTERN`/`_ASSERTION_INVALID_SPACE` 正则、`_takeover_export_assertions`（is_export 强制 contains status_code 200）、`_parse_assertion`（[eq]/[contains]/[ne]/[db]）、`_generate_py_file`

### generators/yaml_gen.py（639 行）
**用途**：Phase C YAML 测试数据生成核心 Mixin——单节点 thinking + json_object 一次调用，多轮修复循环，三阶段（setup→test→teardown）生成，URL 前缀代码接管，写盘前断言规范化。
**类**：`YamlMixin` — `_build_annotation_injector`（pre_validate 注入 _annotations + is_export 补占位断言）/ `_write_yaml_result`（路径参数替换 + 导出断言接管 + 去 _annotations 写盘）/ `_normalize_base_urls`（URL 前缀保真，补回 LLM 丢弃的业务前缀）/ `_generate_one_yaml_single`（当前唯一生成路径）/ `_generate_all_yamls`（三阶段 orchestrator：分组→C6-1 预校验→barrier 分流→逐阶段轮次→键状态传递→后校验）/ `_run_yaml_rounds`（第 1 轮全量并发，失败登记占位，修复轮带错误上下文重生成，超限写 _generation_errors.json）

### generators/_repair_helpers.py（228 行）
**用途**：Phase C 生成后处理辅助（setup 键状态传递、teardown 容错、三阶段合并）。静态校验函数已迁至 validation/yaml_validator.py。
**函数**：`_match_pre_label` / `_parse_setup_extract_keys`（D4 兜底 __MISSING_KEY__ 占位）/ `_d4_placeholder_key` / `_inject_setup_keys_note`（D3）/ `_merge_stage_results` / `_collect_stage_errors` / `_filter_teardown_missing_pres`（task #10）/ `_relax_teardown_validation`（task #9 剥离严格断言为 []）

### generators/_helpers.py（71 行）
**用途**：修复循环错误分类辅助（B1-B10 关键词表对齐校验器文案）。
**函数**：`_summarize_error_patterns` / `_extract_completion_snippet` / `_write_fail_detail` / `_format_post_issues_for_prompt`

### llm/base.py（48 行）
**用途**：LLM 适配器基类，统一空 content 兜底防御。
**类**：`BaseCompatibleChatOpenAI(ChatOpenAI)` — `_guard_empty_content` / `_create_chat_result`

### llm/deepseek.py（178 行）
**用途**：DeepSeek LLM 适配器——归一化 tool_calls 格式差异 + 回补 parsed/refusal/reasoning_content。
**类**：`DeepSeekChatOpenAI(BaseCompatibleChatOpenAI)` — `normalize_tool_calls`（三模式原地归一化）/ `_create_chat_result`（核心覆写：dict 路径归一化委托 / pydantic 路径 dump 归一化 + 回补）/ `_restore_parsed_and_refusal` / `_extract_reasoning_content` / `_restore_reasoning_content`

### validation/case_validator.py（350 行）
**用途**：用例生成检测——validate_excel_file（文件层，原 validator.py）+ ExcelPlanValidator（计划校验，原 plan_validator.py），纯 Python 无 LLM。
**对外暴露**：validate_excel_file / extract_url_paths / match_api_template / ValidationResult / ExcelPlanValidator / FileValidationResult
**类**：
- `ValidationResult` — 校验结果容器（failed_details/all_confirmed/block_reasons）
- `ExcelPlanValidator` — `ERR_TYPES`（9 类错误）/ check_urls（URL 有效性）/ check_case（必填/前置/对齐/断言格式/URL/db 拦截）/ classify / validate（整个 plan + 共享前置 URL）/ aggregate_block_reasons
**函数/常量**：VALID_ENABLED、EXPECTED_HEADERS_SHEET1/2、断言格式正则、`_URL_RE`

### validation/yaml_validator.py（257 行）
**用途**：YAML 数据生成检测——YamlPostValidator 生成后快速验证（delete body 包裹/断言动态 key/块键白名单/引号配对）+ 迁移自 _repair_helpers 的引用完整性/D4 缺失键扫描。
**类**：`YamlPostValidator` — validate_all（4 项检查：delete_body_wrapper P0 / assertion_dynamic_key P1 / assertion_op_key P0 / malformed_assertion P2）/ `_has_unmatched_quotes`
**函数**：`_find_missing_yaml_refs`（.py 引用 yaml 完整性）/ `_scan_missing_key_refs`（D4 缺失键 → P1 人工复核）
**常量**：`_PLACEHOLDER_RE`、`_VALID_OPS`、`_GET_EXTRACT_RE`

---

# 六、web 层

## web/app.py（428 行）
**用途**：FastAPI 装配层——lifespan 初始化重资源（SQLite/Ollama/ChromaDB/Agent 工作流/文件列表/临时清理/补偿 worker）、trace_id 中间件、首页与模块审核路由、注册全部子路由。
**对外暴露**：`app`（title=智能测试助手 v0.3）、`_cleanup_doc_to_doc_bindings`
**类**：`TraceMiddleware`（ASGI 注入 X-Trace-Id）
**函数**：`lifespan`（配置校验→init_db→Ollama 自动启动→ChromaDB 重试→build_workflow→孤儿诊断→清理/补偿）、`index` / `favicon` / `chrome_devtools_probe` / `audit_module`（POST /update-module）/ `analyze_module_scenarios`、`_cleanup_temp_files_loop`、`_scan_orphan_files`

## web/tasks.py（926 行）
**用途**：后台异步任务（上传入库、Phase C 确认生成 .py/.yaml、Phase B 工作流恢复、Phase A 三步分析、接口批量提交）；同步阻塞调用经 asyncio.to_thread 卸载。
**对外暴露**：末尾 re-export web.compensation 6 个 worker 符号
**类**：`_BoundedThreadPoolExecutor(ThreadPoolExecutor)` — 有界线程池（队列满 submit 阻塞）
**函数**：`_process_file_bg` / `_resolve_api_defs`（规则 M8 缺失显式失败：显式入参→模块作用域→回退全部→None）/ `_read_module_scope` / `_load_api_defs_scoped`（模块+关联+公共基础服务镜像 Phase B 口径）/ `_load_all_api_defs`（SQL 读接口详情，绕过 ChromaDB 缺 body/return 根因）/ `_confirm_plan_bg`（心跳防超时）/ `_resume_workflow_bg`（_phase_b_graph.invoke 线程池执行）/ `_analyze_module_scenarios_3step_bg`（Phase A 三步）/ `_analyze_module_scenarios_bg`（废弃委托）/ `_commit_apis_bg`

## web/compensation.py（214 行）
**用途**：补偿 Worker——独立轮询线程处理 simple_summary/chroma_rebuild/api_search_text 三类补偿任务（2026-08-07 自 web/tasks.py 810-998 迁移）。
**函数**：`_start_compensation_worker` / `_stop_compensation_worker` / `_compensation_loop`（秒级可打断轮询）/ `_process_pending_compensation` / `_compensate_simple_summary` / `_compensate_chroma_rebuild` / `_compensate_api_search_text`

## web/state.py（129 行）
**用途**：全局共享状态（无项目内依赖防循环导入）——LLM/工作流组件实例、文件导入状态、任务状态追踪、Phase B 会话。
**对外暴露**：`_phase_b_graph` / `_phase_b_components` / `_chroma_db` / `_vector_ready` / `_imported_files` / `_task_store` / `_workflow_sessions` / `WORKFLOW_SESSION_TTL` 及 _get_imported_files / _add_imported_file / _remove_imported_file / _create_task / _update_task / _cleanup_expired_sessions

## web/routes/modules.py（433 行）
**用途**：模块管理路由（树查询 + CRUD + 合并 + 术语表 + 场景分析 CRUD + 接口定义读取与 annotations 更新）。
**路由**：GET /api/modules、GET /{name}/docs、GET /{name}/related、POST /api/modules、PUT /{id}、DELETE /{id}、POST /merge、GET /{name}/glossary、POST/DELETE glossary 项、GET/PUT/DELETE /{name}/analysis（三步格式 + approve）、GET /{name}/api-defs（优先 SQLite 降级 ChromaDB）、PUT /{name}/api-defs/{index}/annotations（SQLite 为准 + ChromaDB 异步重建 + 补偿任务）、GET /{name}/annotation-types

## web/routes/files.py（378 行）
**用途**：文件管理路由（上传后台处理、删除全链路清理、列表 SQLite 为准、打开/下载、查看/编辑内容）。
**函数**：`_win_remove`（PermissionError 重试防 Defender 锁定）、`upload_file`（防路径遍历 + 同名覆盖清理）、`delete_file`（doc↔doc 绑定/SQLite/ChromaDB 延迟重试/物理文件/内存状态全清理）、`uploaded_files`、`open_file`（限 TESTCASE_BASE 前缀）、`download_file`（commonpath 防跨目录）、`get_file_content`（禁读 .env/.key/.pem）、`save_file_content`（10MB 上限）

## web/routes/docs.py（309 行）
**用途**：文档操作路由（未关联文档、chunks/apis/术语/页面图片/关联文档查看、模块迁移与解绑）。
**函数**：`get_unassociated_docs` / `_axure_pages_from_glossary`（glossary 聚合页面块）/ `disassociate_doc` / `change_doc_module` / `get_doc_chunks` / `get_doc_apis` / `get_doc_glossary` / `add_doc_glossary`（幂等）/ `delete_doc_glossary` / `rename_block_title` / `get_doc_page_images` / `get_page_image_file`（BLOB 优先 + 防路径遍历）/ `get_doc_related_docs`（批量防 N+1）

## web/routes/chat.py（241 行）
**用途**：测试生成路由（Phase C 确认、任务轮询、Phase B 多轮 start/confirm）。
**路由**：POST /confirm-plan、GET /task/{id}、POST /workflow/start、POST /workflow/confirm（序号/精确/重识别三策略）

## web/routes/api_extract.py（150 行）
**用途**：接口提取路由（LLM 提取确认入库 + 纯代码 YApi md/json 确定性解析）。
**路由**：POST /api/upload/extract-api（LLM）、POST /api/upload/commit-api（后台入库）、POST /api/upload/retry-api、POST /api/upload/extract-api-code（纯代码 + apply_all 自动标注）

## web/routes/bindings.py（69 行）
**用途**：绑定关系路由。POST/DELETE/GET /api/bindings（文档↔模块触发级联）

## web/services/doc_binding.py（45 行）
**用途**：文档-模块绑定服务。`rebind_doc_to_module`（清旧级联→清旧绑定→绑新→可选级联）、`_cleanup_doc_to_doc_bindings`

---

# 七、scripts

## scripts/migrate_chroma_to_sqlite.py（316 行）
**用途**：一次性存量迁移脚本（ChromaDB → SQLite + 重建 doc_search），支持 --dry-run/--skip-summary/--step {1,2,4}。
**函数**：`migrate_api_defs`（Step 1：api_defs → documents.api_*，SHA256 content_hash）/ `migrate_product_docs`（Step 2：product_docs → document_chunks）/ `rebuild_doc_search`（Step 4：清空后从 SQLite 重建，分批 100）/ `main`（CLI 入口 + 迁移前后对照）

---

# 八、关键交叉依赖与结构观察

## 8.1 依赖方向（无环）

```
config ← settings（薄包装）
prompts ← data_factory（无环，import 方向 prompts → data_factory）
agent_components/nodes ← retrievers ← dual_chroma ← fallback_embeddings
agent_components/nodes ← generators ← validation
ingest ← agent_components（pipelines 依赖 nodes/dual_chroma/api_annotations/axure_parser）
web ← agent_components + ingest_v2 + database
```

## 8.2 结构观察（供目录规划参考）

1. **agent_components 扁平命名空间混三类**（本次档案的直接动因）：检索（dual_chroma/retrievers/fallback_embeddings）+ langgraph 路径（nodes/graph_builder/state/graph_logging/prompt_builder）+ LLM（llm_client 在 `llm/` 子包**外**），另混入入站解析（axure_parser）与标注（api_annotations）。
2. **已存在子包**：`llm/`（base+deepseek）、`generators/`（Mixin 组合 + 修复辅助）、`validation/`（2026-09-01 刚归位）。`llm_client.py` 是明显的"归位缺口"——`llm/` 子包已存在，client 却在外层。
3. **facade/兼容层模式**：nodes.py re-export llm_client/graph_logging 符号；ingest_v2.py 是 ingest 包兼容层；prompts/__init__.py、database/operations/__init__.py、web/routes/__init__.py 均为包级转发。**目录调整可复用此模式降低爆炸半径。**
4. **被引用最重的模块**（迁移需重点照顾）：api_annotations（26 处）、nodes（21 处）、dual_chroma（18 处）、retrievers（7 处）、graph_builder（6 处）、state（5 处）、llm_client（5 处）。
5. **根级 8 文件较薄**：config/settings/observability 是基础设施；main/web_app/ingest_v2 是入口；apikey.py 独立工具（不提交）。根级基本干净，主要问题集中在 agent_components。
6. **web 层结构已规整**：app/state/tasks/compensation/routes/services 分层清晰，route 文件按资源分组，无明显混置。
7. **ingest 层结构清晰**：extractors/api_parser（输入叶）→ chunking/storage（持久化）→ pipelines（编排），`__init__.py` 转发，无混置。
8. **database 层结构清晰**：models + 入口 + operations 按实体分文件，无混置。
