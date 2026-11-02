# 现有框架可复用清单

> 对照对象：`C:\Users\damai\Desktop\自动化测试框架\测试框架DFD图`（0/1/2 层图）与 `测试框架DD\自动化测试DFD数据字典.xlsx`
> 现有框架：`E:\Generate-test-cases-and-data`（基线 `297d387`）+ 外部执行引擎 `C:\Users\damai\PyCharmMiscProject`
> 复用级别：**A** 直接复用 ｜ **B** 改造复用 ｜ **C** 仅参考 ｜ **D** 需新建
> 目标构件共 28 个：**A 13 ／ B 14 ／ D 1**；D2 按本次决策拆为 3 张待确认表；外部引擎能力单列于第 6 节
> 本文件为前期规划稿：本轮只定方案，不改代码、不建表、不动外部执行引擎。

> 说明：DFD 中的 ID 前缀含义——`P` = 加工处理（Process），`D` = 数据存储（Data Store），`E` = 外部实体，`F` = 数据流。因此 P1-P5 是加工，D1-D3 是存储，二者不是同一层级的编号。

---

## 1. P1 多源文档摄取与预处理

| 构件 | 现有实现（仓库相对路径） | 级别 | 复用内容 / 改造点 |
|---|---|---|---|
| P1.1 文档接入、校验与分发 | `web/routes/files.py::upload_file`；`ingest/pipelines.py` 各类型 `process_*` | B | 上传、落盘、按文件类型分发已有；缺显式路由建议、内容嗅探、统一校验回执（见 15.1） |
| P1.2 接口文档解析与结构化 | `ingest/api_parser.py::extract_apis_from_yapi_md` / `extract_apis_from_yapi_json` / `_extract_valid_api_paths`；`ingest/pipelines.py::process_api_doc_extract`；`prompts/response_model.py::ApiDefinition` / `ApiDefExtract` | B | YApi MD/JSON 确定性解析、LLM 抽取、去重合并均已实现；缺“接口可信等级/来源/质量”字段（见 2.1、15.2） |
| P1.3 产品文档提取、切块与要素抽取 | `ingest/extractors.py`；`ingest/chunking.py`；`ingest/pipelines.py::process_product_doc`；`database/operations/glossary.py`；`DocumentChunk` | A | PDF/docx/xlsx/md 文本提取、切块、术语表抽取、入库可直接复用 |
| P1.4 Axure 原型解析与交互提取 | `agent_components/axure_parser.py`；`ingest/pipelines.py::process_axure_zip`；`PageImage`；`DocumentChunk.page_name` | A | 页面树与交互流提取可直接复用 |
| P1.5 统一落库与向量索引 | `ingest/storage.py::_save_to_sqlite` / `_save_document_chunks` / `_save_single_chunk`；`ingest/pipelines.py::commit_api_docs`；`infrastructure/vector_store/dual_chroma.py`；`CompensationTask` | A | SQLite + Chroma 双写、失败补偿已实现；需扩字段/扩表（见 D1） |

**P1 结论：** 摄取链路整体成熟，可直接作为 D1 的写入侧；主要改造是把“已解析”升级为“带可信等级的接口资产”。

---

## 2. P2 接口前置验证与过滤

| 构件 | 现有实现（仓库相对路径） | 级别 | 复用内容 / 改造点 |
|---|---|---|---|
| P2.1 静态白名单校验 | `ingest/api_parser.py::_extract_valid_api_paths`；`agent_components/validation/case_validator.py::check_urls`；D1 中的接口可信等级 | B | `_extract_valid_api_paths` 是**文档内白名单**，可作为幻觉/拼写过滤的第一道；目标的权威清单来自 D1 中已完成 P1→P4.1 验证的接口（`authoritative`）。需新增分层查询与回退策略 |
| P2.2 特征标注与分类 | `infrastructure/annotations/api_annotations.py::ApiAnnotationRegistry`（`normalize_api_url` / `apply_all` / `is_active` / `get_types`）；`web/routes/api_extract.py::extract_api_code` 入库自动打标 | B | 归一化、自动检测、人工覆盖、前端可选项机制可直接复用；需扩特征词表（依赖特征、鉴权、幂等、读写属性等） |
| P2.3 接口打标与归属确认 | `web/routes/api_extract.py`（`extract-api` / `commit-api` / `retry-api` / `extract-api-code`）；`web/routes/bindings.py`；`BindingOps`；`Binding` 表 | B | 提取 → 用户确认 → 模块绑定流程已有；确认结果当前直接落 `Binding`，没有独立“待确认单”（D2） |
| P2.4 连通性安全探活 | **P5 执行引擎**：`PyCharmMiscProject/run.py`、`base/apiutil.py::RequestsBase`、`common/sendrequests.py::SendRequests`；本仓库 `tests/execution/test_run_generated_case.py` 为调用示例 | A | DFD 1 层图-2 已明确 `P2.4 --探活请求与参数--> P5_Node`、`P5_Node --探活执行结果--> P2.4`；复用 P5 构造/执行探活请求即可，P2.4 只负责编排与结果回写。P5 需增加只读探活模式（超时、白名单、幂等、副作用防护） |

**P2 结论：** P2 不需要另建一套探活器，按图借 P5；真正的增量是**接口可信等级的流转**（P1 文档级 → P2 准权威 → P4.1 权威）和 **D2 待确认单**。

### 2.1 接口可信等级（按你的口径）

| 等级 | 产生阶段 | 含义 | 用途 |
|---|---|---|---|
| `documented` | P1.2 | 从接口文档解析出的接口 | 仅作候选；进入 P2 前需比对 |
| `probed`（准权威） | P2.4 | 探活通过，**仅证明可调用，未经过测试** | 可进入 P3/P4 生成；不能直接当权威清单 |
| `authoritative`（权威） | P1→P4.1 全链验证 | 正向链路 + 契约断言验证通过，准确可用 | P2.1 白名单、生成侧优先使用 |
| `unknown` | 任意 | 证据不足或文档有但未验证 | 不进入生成侧 |

**升级口径（已定）：** `documented → probed → authoritative` 必须**正向链路通过 + 契约断言通过 + 人工确认**三者全满足，缺一不可。

现有代码没有该字段（全仓库仅 2026-09-17 架构草稿提到 `evidence_status`），需要新增并在 P2.1 / P2.4 / P4.1 三处读写。

---

## 3. P3 意图匹配与多跳检索

| 构件 | 现有实现（仓库相对路径） | 级别 | 复用内容 / 改造点 |
|---|---|---|---|
| P3.1 意图语义匹配与确认 | `agent_components/retrievers.py::_confirm_user_intent`；`agent_components/graph/graph_builder.py` `confirm_intent` 节点；`web/routes/chat.py::workflow_confirm`；`ModuleOps.get_all` / `get_tree` | A | LLM 候选模块 + `candidate_modules` + `confirmation_question` + 用户点击确认的闭环已实现 |
| P3.2 主模块知识召回 | `retrievers.py::_retrieve_product_docs` / `_search_product_docs`；`DualChromaDB.search_product_docs`；`ModuleAnalysis` | A | 先按模块绑定过滤 `doc_id`，再向量召回；无结果时全库回退 |
| P3.3 关联依赖提取与确认 | `retrievers.py::_extract_related_modules`；`database/operations/bindings.py::discover_related_modules`；`Binding` | B | 三路召回已抽成共享函数；缺“用户确认/修改/剔除关联模块”交互，确认后的模块级依赖也无独立落库 |
| P3.4 多跳关联数据与接口检索 | `retrievers.py::_retrieve_related_data`（Hop 2a 文档 + Hop 2b 接口）；`_search_api_defs`；`DualChromaDB.get_doc_apis` | A | 固定两跳（主模块 → 关联模块）检索可直接复用；跳数可配置化 |
| P3.5 上下文融合与截断 | `retrievers.py::_docs_to_text`；`_search_api_defs` 按 `doc_ids` 精确过滤、默认不截断；各节点内上下文拼装；`infrastructure/config` | B | 拼接逻辑已有；缺显式 token 预算、来源优先级、截断/摘要策略 |

**P3 结论：** 与现有 Phase B 工作流高度同构，可直接复用节点与状态机；补齐 P3.3 的确认回写和 P3.5 的预算控制即可。

---

## 4. P4 AI 用例计划生成与审查

| 构件 | 现有实现（仓库相对路径） | 级别 | 复用内容 / 改造点 |
|---|---|---|---|
| P4.1 正向主链路用例生成与核验 | `graph_builder.py` `generate_plan_thinking` → `generate_excel_plan`；`prompts/response_model.py::ExcelPlanV2` / `TestCaseRow` / `SharedPrecondition`；`validation/case_validator.py` | B | 生成、校验、失败重试、`requires_review` 已闭环；依赖拓扑与用例计划尚未拆成独立确认步骤；验证通过后回写 `authoritative`，须满足“正向链路 + 契约断言 + 人工确认”三者全满足 |
| P4.1.1 AI 推演接口依赖拓扑 | `agent_components/generators/excel.py::_generate_dependency_map`；`response_model.py::DependencyMap` / `InternalDependency` / `CrossModuleDep` / `StoryDependencyMap` | B | 依赖关系数据模型与生成逻辑已有；需前移到生成前，并支持人工确认/修改链路与参数映射 |
| P4.1.2 正向用例生成（结合链路与示例） | `prompts/definitions.py` 正向链路生成提示词；`generators/excel.py::_read_excel_rows`；`TestCaseRow` | B | 正向用例已并入 Excel 计划生成；需拆成“链路确认 → 用例生成”两段 |
| P4.1.3 契约断言提取 | `agent_components/generators/py_export.py::_ASSERTION_PATTERN`（`eq` / `contains` / `ne` / `db`）；`response_model.py::TestData` / `StepData`；外部引擎 `common/assertions.py::Assertions` | B | 断言语法与解析器可复用；缺从 API Schema/状态码自动提取契约断言 |
| P4.2 扩展用例（全路径/异常/边界）与核验 | `ExcelPlanV2` / `TestCaseRow`；`case_validator`；`data_factory` | B | 表结构可承载扩展用例；缺独立的“用例设计树”对象与覆盖矩阵 |
| P4.3 测试数据生成与核验 | 本仓库 `data_factory/registry.py::load_methods` / `get_validation_rules` / `render_for_prompt`；`data_factory/methods.yaml`；外部引擎 `data_factory/*`、`common/debugtilk.py::DebugTalk` | A | 方法注册表 + 校验规则 + 提示词渲染；外部引擎已有车牌/编码/时间生成器，可直接复用 |
| P4.4 测试资产固化落库 | 本仓库 `generators/yaml_gen.py`；`generators/py_export.py::_generate_py_file`；`generators/translation.py`；`validation/yaml_validator.py`；外部引擎 YAML 执行内核 | A | 生成本仓库负责，落到外部引擎后由其 `base/apiutil.py` 执行；资产布局已对齐（见第 6 节） |

**P4 结论：** 生成侧是本仓库最强的一段，P4.3/P4.4 可直接搬；P4.1/P4.2 需要把“一步生成”拆成 DFD 要求的“分步确认 + D2 落库”。

---

## 5. P5 自动化测试调度与归因

| 构件 | 现有实现 | 级别 | 复用内容 / 改造点 |
|---|---|---|---|
| P5.1 CI/CD 触发与用例筛选 | 外部引擎 `run.py -p/-m`；`pytest.ini` markers；本仓库 `run_results.select_rerun_targets`；`web/tasks.py::_selective_phase_c_rerun_bg` | A | 路径/marker 筛选、选择性重跑已实现；缺 CI/定时触发与统一调度入口 |
| P5.2 调度执行引擎（PyCharmMiscProject） | 外部引擎 `run.py`、`base/apiutil.py`、`common/sendrequests.py`、`conftest.py`；本仓库 `tests/execution/test_run_generated_case.py` 为集成示例 | A | 执行引擎已具备：YAML 驱动、请求重试、token 注入、前后置、Allure/JUnit 输出；本仓库接入侧只需薄包装 |
| P5.3 解析执行结果与失败归因 | 本仓库 `run_results.py::parse_junit` / `build_module_map` / `parse_run_log_blocks` / `class_setup_abs` / `ingest_run_results` / `read_last_run_records` / `build_failure_digest`；外部引擎 `report/junit.xml`、`report/temp/*.json`、`report/allure-report/` | A | JUnit + 执行日志解析、setup 失败归并、失败摘要、`.last_run.json` 旁车已成熟；Allure 插件已配置，同时解析原始 JSON 与 HTML，对比实际效果后再定主口径 |
| P5.4 测试结果看板与告警 | 无 | D | 需新建；可复用 `web`（FastAPI）、任务状态、`build_failure_digest`、Allure 报告 |

**P5 结论：** 外部引擎已经把“执行 + Allure + JUnit”做完整，P5.1/P5.2/P5.3 均可直接复用；看板告警是唯一纯新建项。

---

## 6. 外部执行引擎（PyCharmMiscProject）能力清单

> 按你的要求，外部引擎能力计入复用清单。以下均已在 `C:\Users\damai\PyCharmMiscProject` 核对。

| 能力 | 落点 | 对应目标 | 级别 | 复用要点 / 风险 |
|---|---|---|---|---|
| 执行入口与筛选 | `run.py`（`-p` 路径、`-m` marker）；`pytest.ini`（markers、`testpaths`、`--order-scope=class`） | P5.1 / P5.2 | A | 支持路径 + marker 双筛选；缺 CI/定时触发；`run.py` 结束会起 HTTP 服务并开浏览器，CI 需 headless 开关 |
| Allure / JUnit 报告 | `conftest.py::pytest_configure` / `pytest_sessionfinish`；`conf/setting.py::FILE_PATH` | P5.3 / D3 | A | 自动注入 `--alluredir=report/temp`、`--junitxml=report/junit.xml`；session 结束自动 `allure generate`；含 `environment.xml` |
| YAML 执行内核 | `base/apiutil.py::RequestsBase`（`run_blocks` / `specification_yaml` / `replace_load` / `extract_*`） | P4.4 / P5.2 | A | `baseInfo + testCase` 结构、`${}` 动态替换、token/yq-app-code 注入、断言调度、JSONPath/正则/输入参数提取 |
| HTTP 封装 | `common/sendrequests.py::SendRequests` | P5.2 | A | requests session + 5xx 重试（3 次、退避 2）+ 超时 30s + cookie 持久化 + Allure 附件 |
| 断言引擎 | `common/assertions.py::Assertions` | P4.1.3 / P4.2 / P5.3 | A | `contains` / `eq` / `ne` / `db` 四种模式；支持 JSONPath 过滤器；数据库断言 |
| 运行时变量 | `common/readyaml.py::ReadYamlData` / `get_testcase_yaml`；`extract.yaml` | P5.2 / D3 | A | 跨步骤变量持久化；跑完留存 `extract.last_run.yaml` 现场快照 |
| 动态函数 | `common/debugtilk.py::DebugTalk` | P4.3 | A | `get_extract_data` / `get_extract_data_list` / `random_plates` / `random_code` / `get_current_time` / `get_offset_time` / `split_extract_data` |
| 数据工厂 | `data_factory/plate_generator.py`、`code_generator.py`、`time_utils.py` | P4.3 | A | 车牌去重持久化（上限 2000）、前缀编码生成、时间偏移 |
| MySQL | `common/connection.py::CommectMysql` | P4.3 / P5.3 | A | 查询/更新/批量更新；供 `db` 断言使用 |
| 环境配置 | `conf/setting.py`、`conf/operationConfig.py`、`conf/config.ini` | P5.2 | B | 路径、环境 host、MySQL、Allure 路径集中管理；**host/账号密码硬编码在 config.ini，需外置** |
| 登录与环境头 | `conftest.py::login_and_get_token` / `get_yq_app_code` | P5.2 | B | session 级自动登录并写 token；**账号密码硬编码在 conftest.py，需外置并支持多环境** |
| 前置夹具模式 | 生成的 `test_*.py` 中 `@_fixture_with_display_name` + 逐前置 fixture 链 | P4.4 | A | class 级前置链、用例前置依赖、中文 Allure 显示名；已与本仓库 `py_export` 对齐 |
| 资产布局 | `testcase/<模块>/<feature>/{test_*.py, setup_data/*.yaml, api_defs.json, dependency_map.json, module_scope.json, phase_c_user_context.md, translation_cache.json}` | P4.4 / D3 | A | 本仓库生成产物直接落此布局，执行侧零改造 |
| 用例文档 | `_case_docs_*/gen_case_doc.py`、CSV/README | D3 / P5.4 | B | 可复用为资产说明/报告素材，非核心链路 |
| 环境隔离 | README 声称 `BUILD_NUMBER` / `CI_JOB_ID` | P5.2 | C | 仅 README 提及，代码中未核实；若要做 CI 并行需补 |

**外部引擎主要风险：**

1. `conftest.py`、`conf/config.ini` 含明文账号密码与固定 dev host，需迁移到环境变量/多环境配置。
2. `run.py` 总是启动报告服务并打开浏览器，CI 场景需 `--no-serve` 或环境变量开关。
3. 登录、清车牌等 session 级 fixture 是全局的，多模块并行/多环境时需参数化。
4. Allure 原始结果与 HTML 已生成，但本仓库尚未解析，P5.3 归因应优先读取 `report/temp/*.json`。
5. 目前只有执行与报告，没有 CI 触发、看板、告警、资产索引。

---

## 7. 数据存储 D1 / D2 / D3

| 存储 | 现有实现 | 级别 | 复用内容 / 改造点 |
|---|---|---|---|
| D1 智能测试知识库 | `database/models.py`：`Module` / `Document` / `Binding` / `GlossaryTerm` / `PageImage` / `ModuleAnalysis` / `DocumentChunk` / `CompensationTask`；`database/operations/*`；`infrastructure/vector_store/dual_chroma.py` | A | 知识库底座可直接复用；需补：接口可信等级、接口一等表（或扩 `Document.api_*`）、模块级依赖边、资产索引 |
| D2 待确认计划 | LangGraph `State.requires_review` / `candidate_modules` / `confirmation_question`；`/workflow/confirm`；`/confirm-plan`；`web/state.py` 内存任务表 | B（流程复用）+ 新建 3 表 | 确认交互流程可复用；按本次决策新建场景/用例/测试数据三张表，一并解决无持久化、无驳回理由、无版本历史、多用户/重启即丢的问题 |
| D3 测试资产库 | 本仓库生成目录 + `.last_run.json` 旁车；外部引擎 `testcase/` 资产布局、`report/` 报告 | B | 文件产物与布局已有；缺资产索引、版本、检索与“已固化”状态 |

### 7.1 D2 出处（原文）

D2 是**数据存储**，不是加工处理；`P` 开头才是加工。原文在数据字典的 `数据存储` sheet 第 4 行：

> **D2｜待确认计划**：存储待用户确认/驳回的产物清单，包括接口打标清单、用例计划、测试数据等。
> 输入/输出：待确认产物、提取待确认清单（正向用例/扩展用例/测试数据）、确认/驳回写回结果、确认后的结果。
> 关联：P2.3（写入）、P4.1（写入）、P4.2（写入）、P4.3（写入）、User（读取/写回）。
> 图：0 层图、1 层图-2、1 层图-4、2 层图-4.1。

对应图与数据流：

- 0 层图：`ApprovalStore[(待确认计划)]`；P4 --待确认产物--> ApprovalStore --待确认产物--> User --确认/驳回--> ApprovalStore --确认后的结果--> P5。
- 1 层图-2：P2.3 --待确认清单写入--> D2；D2 --提取待确认清单--> User；User --确认/修改/剔除写回--> D2；D2 --确认后的结果--> P2.4。
- 1 层图-4：P4.1/P4.2/P4.3 分别写 D2，User 确认/驳回写回，P4.4 从 D2 取“已确认用例与数据”。
- 数据流编号：F16-F18、F35-F38、F60-F71、F79。

**结论：** D2 是贯穿 P2.3 / P4.1 / P4.2 / P4.3 的持久化审批中枢；现有代码只有内存态确认流程，需新增持久化表。

### 7.2 D2 落地设计（已定）

按本次决策，D2 拆为**场景、用例、测试数据**三张表，共用一套审批状态与版本字段：

| 表 | 存储内容 | 关键字段 |
|---|---|---|
| `pending_scenario` | P4.1.1 依赖拓扑、P4.1.2 正向链路与参数映射 | `scenario_key`、`dependency_dag` JSON、`param_mapping` JSON、`source_api_ids` JSON |
| `pending_case` | P4.1.3 正向用例、P4.2 扩展用例 | `scenario_id`、`case_type`（positive / extension / exception / boundary）、`title`、`steps` JSON、`assertions` JSON |
| `pending_test_data` | P4.3 测试数据 | `case_id`、`data_set` JSON、`method_refs` JSON、`constraints` JSON |

**共用字段：** `id`、`module_name`、`record_type`、`stage`（P2.3 / P4.1 / P4.2 / P4.3）、`status`、`version`、`parent_id`、`payload` JSON、`reviewer`、`review_comment`、`created_at`、`updated_at`。

**状态机：** `draft → pending → approved / rejected / modified`；`modified` 保留旧版本，重新提交时递增 `version`；只有 `approved` 的记录可被 P4.4 固化、被 P5 读取。`rejected` 必须带 `review_comment`。

**层级与追溯：** `pending_scenario` → `pending_case.scenario_id` → `pending_test_data.case_id`，通过 `parent_id` 与 `version` 保留修改历史；对应 DFD 数据流 F60-F71。

**P2.3 接口打标清单：** DFD 原文也把接口打标清单列入 D2，但你本次只指定三类。规划中暂不单建第四张表，作为 `pending_case` 中 `stage='P2.3'`、`record_type='api_tag'` 的记录（`payload` 存接口清单）；若后续量级或字段差异变大，再拆 `pending_api_tag`。

---

## 8. 横切基础设施

| 能力 | 落点 | 级别 | 说明 |
|---|---|---|---|
| LLM 抽象与客户端 | `infrastructure/llm/{base,client,deepseek}.py` | A | 统一接口 + DeepSeek 适配；换模型只改 client |
| 向量库 | `infrastructure/vector_store/dual_chroma.py` | A | `doc_search` / `api_defs` 双 collection，增删查已封装 |
| 配置与观测 | `infrastructure/settings.py`；`infrastructure/observability.py` | A | 配置、日志、格式化输出可直接复用 |
| 数据库层 | `database/`（`models.py` + `operations/*`） | A | ORM 与会话管理可直接复用 |
| 数据工厂 | `data_factory/registry.py`；`data_factory/methods.yaml` | A | 方法注册表渲染进 prompt + 校验规则，P4.3 直接复用 |
| 校验器 | `agent_components/validation/case_validator.py`；`yaml_validator.py` | A | 计划级 + YAML 文件级校验，含失败分类与阻断原因聚合 |
| 执行结果解析 | `agent_components/run_results.py` | A | P5.3 直接复用；建议补 Allure JSON + HTML 双口径解析 |
| 标注注册表 | `infrastructure/annotations/api_annotations.py` | A | P2.2 直接复用，扩词表即可 |
| Web 服务 | `web/{app,state,tasks,compensation}.py`；`web/routes/*` | B | FastAPI 路由可复用；会话与任务为内存态，需持久化 |
| 工作流编排 | `agent_components/graph/{graph_builder,state,nodes}.py` | B | LangGraph 骨架可保留为 P3/P4 子图；P2/P5 目前不在图内 |
| 提示词与响应模型 | `prompts/response_model.py`；`prompts/definitions.py`；`prompts/extraction_prompts.py` | A/B | Pydantic 模型齐全，需按新阶段扩字段/拆提示词 |
| YAML 规范 | `changelog/YAML_SPECIFICATION.md`；外部 `skill_YAML_CHECK.md` | A | 生成侧与执行侧共享的 YAML 契约文档 |

---

## 9. 不作为生产复用（仅参考或排除）

| 资产 | 处理 | 原因 |
|---|---|---|
| `有作用/PhaseA/_probe_*.py` | C 仅参考 | 一次性 requests 探测脚本，硬编码环境与业务链；P2.4 已改为借 P5，不再以此为实现主体 |
| `有作用/PhaseB/rule_tables/` | C 仅参考 | 已归档的确定性 YAML 内核；`README.md` 列了值得移植的方法（部分已被 `py_export._pre_id_slug` 等覆盖），不要直接 import |
| `待整改/` | 排除 | 历史整改脚本，非生产组件 |
| `logs/`、生成产物、`__pycache__` | 排除 | 运行产物，无复用价值 |
| `tests/execution/test_run_generated_case.py` | 模式复用 | 真实执行测试受 `RUN_GENERATED_CASE=1` 控制，适合做 P5.2 集成测试模板，不作为调度器 |
| 外部引擎 `_case_docs_*/` | C 参考 | 用例文档生成器，非执行主链 |

---

## 10. 缺口清单（按优先级）

### P0（不做则新架构不成立）

1. **接口可信等级 `api_trust_level`**：`documented`（P1）→ `probed`（P2，仅证明可调用）→ `authoritative`（P1→P4.1 验证通过）→ `unknown`；需在 `ApiDefinition` / `Document.api_*` 落字段，并在 P2.1 / P2.4 / P4.1 三处读写。
2. **P2.1 白名单分层查询**：优先使用 D1 中 `authoritative` 接口；无权威记录时回退到 `documented` 并标记待验证；`_extract_valid_api_paths` 保留为文档内幻觉过滤。
3. **D2 持久化待确认计划（三表）**：新建 `pending_scenario` / `pending_case` / `pending_test_data` 三张表 + 状态机（draft / pending / approved / rejected / modified），承接 P4.1、P4.2、P4.3；P2.3 接口打标清单暂以 `pending_case.stage='P2.3'`、`record_type='api_tag'` 记录，不单建表。
4. **P5 探活模式**：在外部引擎增加只读探活入口（超时、白名单、幂等、副作用防护），P2.4 负责编排与结果回写，不另建探活器。
5. **外部引擎配置外置**：`conftest.py` 的登录账号与 `conf/config.ini` 的 host/MySQL 密码迁到环境变量或多环境配置，避免明文与固定 dev 环境。**已纳入改造范围；改造轮优先最小化改动，本轮只做规划。**

### P1（影响生成质量与可运营性）

6. **P4.2 用例设计树**：现有 `ExcelPlanV2` 是平表，缺“全路径/异常/边界”树与覆盖矩阵。
7. **P3.3 关联模块确认回写**：把用户确认后的模块级依赖沉淀为 D1 的独立关系，而不是仅文档 `Binding`。
8. **P3.5 上下文预算**：补 token 预算、来源优先级、截断/摘要策略。
9. **P4.1.1 依赖拓扑确认步骤**：把 `_generate_dependency_map` 前移，并接入 D2。
10. **P5.1 统一运行入口/CI 触发**：现有外部 `run.py` 可手动运行，缺 CI/定时触发与统一调度接口。

### P2（体验与闭环）

11. **P5.4 看板与告警**：新建；可复用 `web` + `build_failure_digest` + Allure 报告。
12. **D3 资产索引/版本**：为 pytest/YAML 三件套建立索引、版本与“已固化”状态。
13. **Allure 双口径解析**：同时解析 `report/temp/*.json` 与 `report/allure-report/` HTML，先做实际效果对比，再决定主口径；原始 JSON 优先用于 step/attachment 级归因。
14. **`run.py` headless 模式**：CI 下禁用自动打开浏览器/报告服务。

---

## 11. 建议复用顺序

| 波次 | 内容 | 理由 |
|---|---|---|
| 第 1 波 | D1 + P1 + 外部引擎配置外置（规划） | 先把“文档进得来、检索得到、环境可用”跑通；P1 与 D1 均为 A 级 |
| 第 2 波 | P3 + P4.3 + P4.4 + 外部 YAML 执行内核 | 生成侧与执行侧契约已对齐，改造量最小 |
| 第 3 波 | P5.2 薄包装 + P5.3 归因（JUnit + Allure JSON/HTML 对比） | 外部引擎已具备执行与报告，先形成闭环 |
| 第 4 波 | P2 + D2 三表 + P4.1/P4.2 分步确认 + P5.1/P5.4 | 治理与运营能力，新建比例最高 |

---

## 12. 已确认决策与阶段边界

1. **D2 建表（已定）**：建 `pending_scenario` / `pending_case` / `pending_test_data` 三张表，分别存场景、用例、测试数据；复用现有 `/workflow/confirm`、`/confirm-plan` 交互，存储层替换为持久化表。
2. **`authoritative` 升级口径（已定）**：必须同时满足**正向链路通过 + 契约断言通过 + 人工确认**，三者全满足才从 `probed` 升级为 `authoritative`，缺一不可。
3. **外部引擎改造边界（已定）**：`PyCharmMiscProject` 纳入改造范围，但改造轮优先最小化改动；本轮只做前期规划，不改外部引擎代码、不动 `conftest.py` / `config.ini`。
4. **Allure 归因口径（已定）**：同时读取 `report/temp/*.json` 与 `report/allure-report/` HTML，做实际效果对比后再决定主口径；本轮不实现解析器。
5. **本阶段交付边界**：本文件只作为规划与复用清单；代码、表结构、外部引擎改动均留到后续改造轮，需另行确认后执行。

---

## 13. 新增 DFD 图（2026-11-02）

按“A 级直接复用补 2 层图、B 级改造按目标态补 2 层图、复杂加工补 3 层图、单图加工数 < 9”的原则，在
`C:\Users\damai\Desktop\自动化测试框架\测试框架DFD图\` 新增以下图：

**2 层图（14 张）**

| 图 | 分解对象 | 加工数 |
|---|---|---:|
| `自动化测试2层图-1.1.文档接入校验与分发.txt` | P1.1 | 5 |
| `自动化测试2层图-1.2.接口文档解析与结构化.txt` | P1.2 | 8 |
| `自动化测试2层图-1.3.产品文档提取与切块.txt` | P1.3 | 4 |
| `自动化测试2层图-1.4.Axure原型解析.txt` | P1.4 | 4 |
| `自动化测试2层图-1.5.统一落库与向量索引.txt` | P1.5 | 4 |
| `自动化测试2层图-2.4.连通性安全探活.txt` | P2.4 | 5 |
| `自动化测试2层图-3.1.意图语义匹配与确认.txt` | P3.1 | 4 |
| `自动化测试2层图-3.2.主模块知识召回.txt` | P3.2 | 4 |
| `自动化测试2层图-3.4.多跳关联检索.txt` | P3.4 | 4 |
| `自动化测试2层图-4.3.测试数据生成与核验.txt` | P4.3 | 5 |
| `自动化测试2层图-4.4.测试资产固化落库.txt` | P4.4 | 6 |
| `自动化测试2层图-5.1.CICD触发与用例筛选.txt` | P5.1 | 4 |
| `自动化测试2层图-5.2.调度执行引擎.txt` | P5.2 | 4 |
| `自动化测试2层图-5.3.解析结果与失败归因.txt` | P5.3 | 4 |

**3 层图（4 张）**

| 图 | 分解对象 | 加工数 |
|---|---|---:|
| `自动化测试3层图-4.4.2.YAML配置生成.txt` | P4.4.2 | 5 |
| `自动化测试3层图-4.4.3.pytest脚本生成.txt` | P4.4.3 | 5 |
| `自动化测试3层图-5.3.3.失败归因分析.txt` | P5.3.3 | 5 |
| `自动化测试3层图-1.2.6.补全策略与回填校验.txt` | P1.2.6 | 6 |

索引与层级关系见同目录 `自动化测试DFD图-新增图索引.md`。

**说明：** 新增的 `x.y.z` 级加工编号目前只存在于图中，数据字典的 `加工处理 / 数据流` 两张表尚未补录，改造轮需同步；P1.1 / P1.2 / P1.2.6 已按目标态补图，其余 B / D 级加工本轮未展开。

---

## 14. P1.1 / P1.2 现状补充（2026-11-02）

### P1.1 文档接入、校验与分发（B 级，改造复用）

- **现状入口**：只有 `/api/files/upload-file` 一个通用上传入口，按扩展名映射：
  `.pdf/.docx/.xlsx → product`，`.md/.json/.yml/.yaml → md`，`.zip → axure`。
- **实际分发**在 `web/tasks.py::_process_file_bg`：
  - `.zip` → `process_axure_zip`
  - `.md/.json` → 不立即解析，返回 `needs_extract_choice`，前端再选 `/extract-api`（LLM）或 `/extract-api-code`（确定性解析）
  - 其余 → `process_product_doc`
- **结论**：没有独立的“文档分类 / 内容校验”加工，属于“入口 + 扩展名”的隐式分发。因此 P1.1 是 B 级改造，不是纯新建。
- **改造点**：显式 `doc_type`/内容校验、统一接入回执、`.json` 歧义处理（YApi JSON vs 普通 JSON）、分类结果落库。

### P1.2 接口文档解析与结构化（B 级，改造复用）

现有实现确实分成两条路径：

1. **LLM 分析**：`/api/upload/extract-api` → `ingest.pipelines.process_api_doc_extract`
   - 用 `ApiDefExtract` + `json_mode` 做结构化抽取；
   - 按标题分批（`_split_text_by_headers`），线程池 5 并发；
   - 抽取后依次做 `normalize_api_url` → `_extract_valid_api_paths` 文档白名单过滤（剔除 LLM 幻觉）→ `_merge_api_defs` 合并去重。
2. **YApi 固定格式拆解**：`/api/upload/extract-api-code`
   - `format=md` → `extract_apis_from_yapi_md`（兼容 YApi MD 与纯 Markdown）；
   - `format=json` → `extract_apis_from_yapi_json`（YApi `api.json` 的分类/list 结构）；
   - 纯代码解析，不走 LLM；随后 `ApiAnnotationRegistry.apply_all` 自动标注（`is_export` / `has_path_params` / `category` 等）。

两条路径都返回 `{module_name, apis}` 给前端确认；`/commit-api` → `commit_api_docs` 写入 SQLite + Chroma（含补偿回滚）。

- **注意**：`extract-api-code` 里的 “code” 指“纯代码解析、不走 LLM”，**不是扫描源码**。
- **改造点**：增加 `api_trust_level=documented`、持久化 `extract_method`（llm / yapi_md / yapi_json）、统一两条路径的入口选择与结果结构；P1.2 只做基于接口定义的轻量补全，产品文档 RAG 深度补全下放到 P2.3 按需触发。
- **可复用结论**：解析器、白名单过滤、合并去重、标注注册表都可直接复用；需要补的是“状态/来源字段”和“统一入口”，所以定为 B 级而不是 A 级。本轮已补 P1.1 / P1.2 的 2 层图与 P1.2.6 的 3 层图（见第 13 节）。

---

## 15. P1.1 / P1.2 目标态调整口径（2026-11-02）

### 15.1 P1.1：从“扩展名隐式分发”改为“显式路由 + 校验回执”

| 调整项 | 口径 |
|---|---|
| 显式声明 | 前端上传时携带可选 `declared_type`；`.json` / `.md` 必须让用户选“YApi 导出（代码解析）”或“普通接口文档（LLM 解析）”，不再由后端猜 |
| 内容嗅探 | ZIP/PDF 等二进制用文件签名；JSON/MD **没有魔数**，改用结构特征：JSON 顶层 `index/list/project` 等键、MD 的 `**Path：** + **Method：**`、YApi HTML 表格、`## 接口名称` 等 |
| 嗅探定位 | 嗅探结果只作为 `suggested_route` 和告警依据，最终以用户确认为准；低置信不自动分发 |
| 统一回执 | 返回 `{doc_id, status, suggested_route, detected_type, validation_errors, needs_choice}`；状态至少覆盖 `pending_parse` / `needs_choice` / `rejected` |
| 分发 | 1.1.5 按确认后的 `route_hint` 分发到 P1.2 / P1.3 / P1.4；接入元数据随文档传递，P1.5 统一落库 |

### 15.2 P1.2：把“可信等级”和“来源/补全方式”拆开

原提案里的 `trust_level = yapi_only / merged_llm / authoritative / inferred` 混用了两个维度，建议拆成：

| 字段 | 取值 | 含义 |
|---|---|---|
| `extract_method` | `llm` / `yapi_md` / `yapi_json` | 首次提取走哪条解析器 |
| `schema_origin` | `yapi_only` / `llm_only` / `developer_local` / `merged_llm` / `merged_developer` | Schema 的来源/补全方式；替代原提案的 `yapi_only`、`merged_llm` |
| `api_trust_level` | `documented` / `probed` / `authoritative` / `unknown` | 沿用 2.1 的验证等级；P1.2 只能产出 `documented` |
| `completion_method` | `none` / `developer_export` / `system_fallback` / `deep_rag` | 开发回填为主；系统兜底仅无开发资源时启用；`deep_rag` 仅在 P2.3 按需触发 |
| `is_low_quality` | boolean | 质量门禁未通过；进入 P2.3 置顶/标黄 |
| `needs_review` | boolean | 存在低置信、冲突或 `unknown` 字段，需人工 Diff 审查 |
| `field_evidence` | JSON 数组 | 每个补全字段的 `{field, source, reasoning, confidence, is_inferred}` |

> 不要把 `yapi_only` / `merged_llm` 写进 `api_trust_level`；它们描述的是“Schema 怎么来的”，不是“接口验证到什么程度”。

### 15.3 补全策略与准确性约束（A 版：开发回填为主；系统兜底可选）

1. **开发回填为主，系统兜底为辅**：A 版主路径是把待补全包交给开发本地 vibe coding 补全；只有无开发资源时才启用系统兜底。系统兜底只根据接口定义（URL/Method/已有字段/描述）+ 同模块接口 + 分类/术语做轻量推断，**默认不接产品文档 RAG**；产品文档切片属于较重分析，仅在低质量接口由 P2.3 人工触发深度补全时按需召回。
2. **字段级补全**：不让 LLM 重写整份 Schema，只输出字段级候选 + `reasoning`；`reasoning` 为空或找不到来源的字段丢弃。
3. **证据链**：每个字段必须带 `source`（接口名/分类/术语/文档块 ID）与 `confidence`；无证据的字段标 `unknown`，不进入最终 Schema。
4. **强类型/HTTP 语义校验**：用 `ApiDefinition` / Pydantic 校验 JSON Schema；GET 参数应在 query，POST 参数应在 body；与 YApi 原定义冲突时保留原值并标 `needs_review`。
5. **自省复核**：对补全结果做一次反向检查（字段是否符合业务常识、类型是否冲突），失败项降级为 `unknown` 或 `needs_review`。
6. **人工绝对卡点**：P1.2 只负责“打标”，不阻塞摄取，所有接口统一进 P1.5；P2.3 做初步确认，展示“YApi 原定义 vs 轻量/深度补全”Diff，补全字段高亮并显示 `reasoning`；P4.1 再做最终确认和 `authoritative` 升级。

### 15.4 质量门禁（P1.2.8）

至少检查：

- URL 合法且以 `/` 开头，Method 在 `GET/POST/PUT/DELETE/PATCH` 内；
- 入参、出参不能同时完全为空（文件上传/无参接口按白名单例外）；
- Response 至少能识别 HTTP 状态码或统一响应包裹结构；
- Schema 能被 `ApiDefinition` 解析，字段类型不与 YApi 原定义冲突；
- 不满足项写 `is_low_quality=true`，冲突/低置信项写 `needs_review=true`，不直接丢弃接口。

### 15.5 对 F28 / `api_defs` 的字段建议

`F28（接口定义-待验证）` 与 D1 的 `api_defs` 表建议增加：

- `extract_method`、`schema_origin`、`completion_method`
- `api_trust_level`、`is_low_quality`、`needs_review`
- `field_evidence`（JSON/TEXT，存字段级证据链）
- `source_doc_id` / `source_chunk_ids`（补全依据可追溯）

原有 `api_trust_level` 的升级口径不变：`documented → probed → authoritative` 仍需“正向链路通过 + 契约断言通过 + 人工确认”三者全满足。

### 15.6 接口状态与确认顺序（回应图 1.2 的疑问）

P1.2 **不直接把接口分流到 P2 或 P3**，也不在这里做“最终可用性确认”。固定顺序是：

```text
P1.2 documented（含开发回填/系统兜底）
  → P1.5 落库
  → P2.3 初步确认（人审 + 可选深度补全）
  → P2.4 探活 probed
  → P3 检索/意图匹配使用
  → P4.1 最终确认 authoritative
```

- 高/低置信度都走同一条主链路，只改 `is_low_quality` / `needs_review` 标记；
- P3 是意图匹配与检索，不是接口质量分流目的地；
- P2.3 是初步确认，P4.1 才是最终“可用性/契约”确认；
- 低质量接口不应被直接丢到 P3，也不应跳过 P2.3。

---

## 16. 采用方案 A：输出“待补全包”，开发本地 vibe coding 补全（2026-11-02）

**结论：采用 A 版——开发回填为主路径，系统内 LLM 补全降为无开发资源时的可选兜底。** 不直接丢一份自由格式 Markdown 给开发，而是输出“受控待补全包 + 校验器”，回填后仍走 P2.3 初确认、P2.4 探活、P4.1 终确认。

### 16.1 推荐流转

```text
P1.2 解析 + 完整性检测
  ├─ 完整接口 ──────────────────────────────→ P1.5
  ├─ 不完整接口 + 有开发资源（主路径）
  │    → 1.2.6 生成待补全包（YAML + MD + JSON Schema + validator）
  │    → 开发本地 vibe coding 工具补全
  │    → 回填导入 + Schema 校验 + Diff/冲突检测 + 来源标注（包/记录进 D2）
  │    → P1.5（documented，completion_status=developer_completed）
  └─ 不完整接口 + 无开发资源（可选兜底）
       → 1.2.6 系统轻量补全（evidence/confidence/needs_review）
       → P1.5（documented，completion_status=system_fallback）
  ↓
P2.3 初确认（人审 Diff）→ P2.4 probed → P3 检索/意图匹配 → P4.1 authoritative
```

### 16.2 待补全包内容

每个包按模块或文档生成，至少包含：

- `api_completion.yaml`：机器可读；每条接口含 `api_id`、`base_revision`、`method`、`url`、`name`、`known`、`missing`、`questions`、`schema_version`
- `api_completion.md`：人读清单；用 `TODO` 标出缺失的入参/出参/描述/示例，并附 YApi 原始片段
- `api_completion.schema.json`：回填文件的 JSON Schema，禁止自由格式
- `validate_api_completion.py`：本地校验器，开发提交前先跑
- `source_hint`：模块名、接口 URL、可能的 Controller/DTO 路径，方便本地 vibe coding 工具结合仓库代码

### 16.3 回填与合并规则

- **不直接写 D1 权威数据**：回填包/回填记录先进入 D2 待补全/待确认区；接口定义由 P1.5 以 `documented` 落 D1，P2.3 人审后才进入 P2.4/P3，不直接写 `authoritative`。
- **严格 Schema 校验**：字段名、类型、必填、HTTP 语义（GET query / POST body）不合法直接拒绝。
- **Diff 审查**：保留 YApi 原值；开发新增/修改字段逐项高亮，冲突项标 `needs_review`。
- **来源标注**：开发回填写 `schema_origin=developer_local`、`completion_method=developer_export`；系统兜底写 `schema_origin=llm_light`、`completion_method=system_fallback`；均记录 `completed_by`、`completed_at`、`base_revision`，且不自动升级 `api_trust_level`。
- **版本与并发**：用 `base_revision` / `content_hash` 防覆盖；同一接口多人回填时做三方合并或按接口加锁。
- **质量门禁仍执行**：回填后仍跑 URL/Method/空 Schema/类型冲突检查；不通过则退回开发或标 `needs_review`，不得进入 P2.4/P3。

### 16.4 安全与边界

- 接口文档可能含敏感路径/字段；本地 vibe coding 工具若走外部 LLM，需先做敏感信息分级、脱敏或改用本地模型。
- 开发回填只是“补全来源”，不等于接口已验证；`documented` 仍需 P2.4 探活、P4.1 终确认。
- A 版保留系统轻量推断作为**可选兜底**：没有开发资源时，P1.2 可生成“建议值 + needs_review”，但不替代开发回填主路径。

### 16.5 对现有图的影响（已按 A 版重画）

- `1.2.6` 已改为“缺口标注与补全策略”：开发回填主路径，系统 LLM 兜底可选。
- 3 层图已改为 `自动化测试3层图-1.2.6.补全策略与回填校验.txt`，覆盖待补全包生成、D2 待补全区、开发回填、Schema 校验、系统兜底与结果标注。
- `P2.3` 增加“回填导入 + Diff 审查”职责，但仍只做初步确认，不做最终权威升级。
