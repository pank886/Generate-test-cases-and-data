# 系统流程结构图

> 生成时间: 2026-07-22 | 更新: 2026-07-22（Phase A 已移除）| 覆盖: 文件上传 → Phase B → Phase C 全链路

---

## 总览

```
┌─────────────────────────────────────────────────────────────────────┐
│                         用户入口（前端页面）                           │
│                                                                      │
│  POST /upload                  POST /workflow/start                  │
│  （上传文档入库）                 （启动工作流）                        │
│                                        │                             │
│                                        ▼                             │
│                                  POST /workflow/confirm              │
│                                  （确认模块→继续执行）                 │
│                                        │                             │
│                                        ▼                             │
│                                  POST /confirm-plan                  │
│                                  （确认计划→生成代码）                 │
└─────────────────────────────────────────────────────────────────────┘
         │                                    │
         ▼                                    ▼
┌──────────────────┐            ┌──────────────────────────┐
│  文件处理         │            │  Phase B → Phase C       │
│  （异步任务入库）  │            │  （LangGraph 工作流）     │
└──────────────────┘            └──────────────────────────┘
```

---

## 一、文件上传与入库

### 入口：`POST /upload` → `_process_file_bg`（异步后台任务）

```
┌─────────────────────────────────────────────────────────────────┐
│ 用户上传文件（文件上传入口）                                        │
│   输入:                                                            │
│     ├─ file（上传文件对象）— UploadFile 类型                        │
│     └─ background_tasks（后台任务管理器）                           │
└────────────┬────────────────────────────────────────────────────┘
             │
             ▼
┌─────────────────────────────────────────────────────────────────┐
│ 1. 文件接收与校验（文件安全检查）                                    │
│   输入:                                                            │
│     ├─ file.filename（原始文件名）                                  │
│     ├─ file.content_type（文件MIME类型）                            │
│     └─ file.size（文件大小，字节）                                   │
│   处理:                                                           │
│     ├─ 扩展名校验 (.pdf/.docx/.md/.rp/.zip)                        │
│     ├─ 大小限制校验 (settings.upload_max_size_mb，上传大小上限)      │
│     └─ 安全文件名 (os.path.basename 防路径穿越)                     │
│   输出:                                                            │
│     ├─ safe_filename（安全文件名）— 经 basename 清洗                 │
│     └─ file_path（文件绝对路径）— 基于 BASE_DIR                      │
└────────────┬────────────────────────────────────────────────────┘
             │
             ▼
┌─────────────────────────────────────────────────────────────────┐
│ 2. 文件类型判断与解析（按扩展名分流解析）                             │
│   输入:                                                            │
│     ├─ file_path（文件绝对路径）                                    │
│     └─ ext（文件扩展名）— .pdf/.docx/.md/.rp/.zip                  │
│   处理:                                                           │
│     ├─ .pdf   → PDFMiner / PyPDF 提取文本                         │
│     ├─ .docx  → python-docx 提取文本 + 图片                         │
│     ├─ .md    → 直接读取文本（API 文档）                             │
│     ├─ .rp    → Axure 解析器 (HTML→结构化数据)                      │
│     └─ .zip   → 解压后递归处理（Axure 原型包）                       │
│   输出:                                                            │
│     ├─ extracted_text（提取后的文本内容）                            │
│     └─ doc_type（文档类型）— product（产品文档）/api（接口文档）/    │
│        axure（原型文档）                                            │
└────────────┬────────────────────────────────────────────────────┘
             │
             ▼
┌─────────────────────────────────────────────────────────────────┐
│ 3. 文本分块（Chunking，语义切分）                                    │
│   输入:                                                            │
│     ├─ extracted_text（提取后的文本内容）                            │
│     └─ doc_type（文档类型）                                         │
│   处理:                                                           │
│     ├─ 按段落/章节切分为 chunks（文本块）                            │
│     ├─ chunk_size 控制 (settings.chunk_size，块大小上限)            │
│     ├─ chunk_overlap 重叠控制（块间重叠字符数）                      │
│     └─ 每个 chunk 附加元数据                                        │
│   输出:                                                            │
│     └─ chunks（文本块列表）— List[dict]，每块含:                     │
│         ├─ text（块文本内容）                                       │
│         ├─ doc_id（所属文档ID）                                     │
│         ├─ doc_type（文档类型）                                     │
│         └─ module_name（所属模块名）                                │
└────────────┬────────────────────────────────────────────────────┘
             │
             ▼
┌─────────────────────────────────────────────────────────────────┐
│ 4. 双存储写入（SQLite → ChromaDB 顺序写入）                          │
│   输入:                                                            │
│     ├─ chunks（文本块列表）— List[dict]                             │
│     ├─ doc_metadata（文档元数据）— 文件名、类型、大小                 │
│     └─ file_path（文件绝对路径）                                    │
│   处理:                                                           │
│     ├─ Step 1: SQLite 先写（关系库优先）                            │
│     │    ├─ 创建/更新 Document 记录（文档主表）                      │
│     │    └─ 关联到模块 (Binding 关联表)                              │
│     ├─ Step 2: ChromaDB 后写（向量库后写）                          │
│     │    ├─ 调用 Embedding 模型生成向量（文本→向量嵌入）              │
│     │    ├─ 写入 ChromaDB collection（向量集合）                     │
│     │    └─ 失败时回滚 SQLite (_delete_sqlite_doc，补偿回滚)         │
│     └─ Step 3: 内存状态更新                                         │
│          └─ _add_imported_file 更新文件列表缓存（前端展示用）         │
│   输出:                                                            │
│     ├─ doc_id（文档唯一标识）— UUID 字符串                           │
│     ├─ 写入状态（写入结果）— success / failed                        │
│     ├─ SQLite 记录（关系库持久化）                                   │
│     ├─ ChromaDB 向量（向量库持久化）                                 │
│     └─ .meta.json 元数据文件（磁盘元信息）                           │
└────────────┬────────────────────────────────────────────────────┘
             │
             ▼
┌─────────────────────────────────────────────────────────────────┐
│ 5. .md 文件额外处理: API 接口提取（接口文档特殊流程）                 │
│   触发条件: 仅 .md 文件（API 接口文档）                              │
│   输入:                                                            │
│     ├─ file_path（文件路径）— .md 文件                              │
│     └─ doc_id（文档ID）— 已写入 SQLite 的文档标识                    │
│   处理:                                                           │
│     ├─ LLM 调用: 从 Markdown 提取接口定义                           │
│     │   prompt: parse_api_prompt()（接口解析提示词）                │
│     │   method: json_mode（结构化输出模式）                          │
│     │   thinking: off（与结构化输出不兼容）                           │
│     │   输出模型: ApiDefExtract（接口提取结果模型）                   │
│     ├─ Pydantic 校验接口定义                                        │
│     └─ 写入 ChromaDB (collection: api_defs，接口定义向量集合)        │
│   输出:                                                            │
│     └─ api_definitions（接口定义列表）— List[dict]                   │
│         每个接口含: name（接口名）, url（路径）, method（HTTP方法）,    │
│         parameters（请求参数）, returns（返回字段）, module（所属模块） │
└─────────────────────────────────────────────────────────────────┘
```

---

## 二、Phase B：多跳检索与 Excel 测试计划

### 入口：`POST /workflow/start` → `_resume_workflow_bg`

```
┌─────────────────────────────────────────────────────────────────┐
│ 启动工作流（Phase B 入口）                                         │
│   输入:                                                            │
│     └─ user_input（用户输入文本）— 如"智慧用电-电表管理"             │
│   初始化处理:                                                       │
│     ├─ reload_llm()（重建 LLM 客户端连接池，防僵死连接）             │
│     ├─ _make_initial_state(user_input)（构建初始 State）           │
│     ├─ 启动心跳协程（每 10s 更新进度，避免前端超时）                 │
│     └─ LangGraph astream 逐节点执行                                │
│   初始 State（状态初始值）:                                         │
│     ├─ user_input（用户输入文本）                                   │
│     ├─ original_input（原始用户输入）                                │
│     ├─ context（上下文文本）— 初始为 ""                             │

│     ├─ response_obj（响应对象）— 初始为 None                        │

│     ├─ product_docs（产品文档检索结果）— 初始为 None                 │
│     ├─ related_modules（关联模块列表）— 初始为 None                  │
│     ├─ api_definitions（接口定义检索结果）— 初始为 None              │
│     ├─ test_point_analysis（测试点分析报告）— 初始为 None            │
│     ├─ excel_plan（Excel 计划对象）— 初始为 None                    │
│     ├─ excel_path（Excel 文件路径）— 初始为 None                    │
│     ├─ output_dir（输出目录路径）— 初始为 None                      │
│     ├─ candidate_modules（候选模块列表）— 初始为 None               │
│     ├─ confirmation_question（确认提示文本）— 初始为 None            │
│     ├─ workflow_status（工作流状态）— 初始为 "PENDING"              │
│     └─ confirmed_module（用户确认的模块名）— 初始为 None             │
└────────────┬────────────────────────────────────────────────────┘
             │
             ▼
┌─────────────────────────────────────────────────────────────────┐
│ 节点 B1: confirm_intent（确认用户意图与模块推荐）                    │
│   方法: _confirm_user_intent                                        │
│   位置: agent_components/retrievers.py:85                            │
│   输入:                                                            │
│     ├─ state["user_input"]（用户输入文本）                          │
│     ├─ state["confirmed_module"]（恢复路径标记，可为 None）         │
│     ├─ state["workflow_status"]（恢复路径状态，可为 None）          │
│     └─ 模块树 JSON (module_tree.get_tree，完整模块层级结构)          │
│   处理:                                                           │
│     ├─ 恢复路径检测（短路返回）:                                     │
│     │   若 confirmed_module 存在 且 workflow_status=="CONFIRMED"   │
│     │   → 直接放行，不覆盖上游状态                                  │
│     ├─ LLM 调用 (_invoke_structured，统一结构化调用):               │
│     │   prompt: confirm_user_intent_prompt()（意图确认提示词）      │
│     │   method: json_mode（结构化输出模式）                          │
│     │   thinking: off                                               │
│     │   输出模型: IntentConfirmation（意图确认模型）                 │
│     │     ├─ matched_modules（匹配的模块列表）— List[str]           │
│     │     └─ confidence（匹配置信度）— "high"/"medium"/"low"        │
│     ├─ 字段漂移兼容: matches → matched_modules 自动迁移             │
│     ├─ 解析失败降级: except → workflow_status="WAITING"              │
│     └─ 候选模块与实际模块树交叉过滤                                  │
│   输出:                                                            │
│     ├─ state["candidate_modules"]（LLM 匹配的候选模块列表）          │
│     ├─ state["confirmation_question"]（用户确认提示文本）            │
│     ├─ state["workflow_status"]（工作流状态）— "WAITING"/"CONFIRMED" │
│     ├─ state["confirmed_module"]（用户选择的模块名）                 │
│     └─ state["response_obj"]（前端展示响应对象）— ProperResponse      │
│   路由:                                                           │
│     ├─ workflow_status=="WAITING" → END（挂起等用户确认）            │
│     └─ workflow_status=="CONFIRMED" → 继续到 B2                     │
└────────────┬────────────────────────────────────────────────────┘
             │ (CONFIRMED)
             ▼
┌─────────────────────────────────────────────────────────────────┐
│ 节点 B2: retrieve_product_docs（检索产品文档，Hop 1）                │
│   方法: _retrieve_product_docs                                      │
│   位置: agent_components/retrievers.py:154                          │
│   输入:                                                            │
│     ├─ state["user_input"]（用户输入文本）                          │
│     ├─ state["confirmed_module"]（用户确认的模块名）                 │
│     └─ ChromaDB (collection: product_docs，产品文档向量集合)        │
│   处理:                                                           │
│     ├─ SQLite 精确过滤:                                             │
│     │   └─ BindingOps.get_bound_docs(session, confirmed_module)    │
│     │      → 获取该模块绑定的产品/axure 文档 doc_id 列表             │
│     ├─ ChromaDB 语义搜索:                                           │
│     │   └─ dual_chroma.search_product_docs(query, k=RETRIEVAL_K,   │
│     │      doc_ids=bound_doc_ids)（按模块过滤的向量检索）            │
│     ├─ 无结果回退: doc_ids 过滤无结果时全库检索                      │
│     └─ 无数据检测 → workflow_status="NO_DATA"                       │
│   输出:                                                            │
│     ├─ state["product_docs"]（产品文档检索结果）— List[dict]         │
│     │   每项含: content（文档内容）, source（来源文件）,               │
│     │   type（类型: "product_doc"）                                  │
│     └─ state["workflow_status"]（工作流状态）— "NO_DATA" 或不变       │
│   路由:                                                           │
│     ├─ workflow_status=="NO_DATA" → END（提示用户先导入文档）        │
│     └─ 有数据 → 继续到 B3                                           │
└────────────┬────────────────────────────────────────────────────┘
             │ (有数据)
             ▼
┌─────────────────────────────────────────────────────────────────┐
│ 节点 B3: extract_related_modules（提取关联模块，三路召回）            │
│   方法: _extract_related_modules                                    │
│   位置: agent_components/retrievers.py:207                           │
│   输入:                                                            │
│     ├─ state["confirmed_module"]（用户确认的模块名）                 │
│     └─ SQLite Binding 表（模块-文档关联表）                          │
│   处理（三路召回，纯 SQLite 查询，无 LLM）:                           │
│     ├─ 路径 1: module↔module 直接绑定                               │
│     │   └─ BindingOps.get_partners("module", confirmed_module)     │
│     │      → 获取与当前模块直接关联的其他模块名                       │
│     ├─ 路径 2: 产品/axure 文档 → 其他模块                            │
│     │   └─ BindingOps.get_bound_docs(confirmed_module)             │
│     │      → 按 doc_type 拆分 product_ids / axure_ids / api_ids     │
│     │      → BindingOps.get_partners_batch("product"/"axure", ids) │
│     │      → 找到共享同一文档的其他模块                              │
│     └─ 路径 3: API 文档 → 其他模块                                   │
│         └─ BindingOps.get_partners_batch("api", api_ids)           │
│   输出:                                                            │
│     └─ state["related_modules"]（关联模块名列表）— List[str]         │
│        去重 + 排序后的模块名称集合                                    │
└────────────┬────────────────────────────────────────────────────┘
             │
             ▼
┌─────────────────────────────────────────────────────────────────┐
│ 节点 B4: retrieve_related_data（检索关联数据，Hop 2a+2b）            │
│   方法: _retrieve_related_data                                      │
│   位置: agent_components/retrievers.py:288                           │
│   输入:                                                            │
│     ├─ state["related_modules"]（关联模块列表，来自 B3）              │
│     ├─ state["product_docs"]（已有产品文档，来自 B2）                 │
│     ├─ state["user_input"]（用户输入文本）                           │
│     ├─ state["confirmed_module"]（用户确认的主模块名）                │
│     └─ ChromaDB (product_docs + api_defs 双集合)                    │
│   处理:                                                           │
│     ├─ Hop 2a: 关联模块的产品文档检索                                │
│     │   └─ 遍历 related_modules:                                    │
│     │       获取绑定文档 → 过滤 doc_type → 语义检索 → 去重追加       │
│     ├─ Hop 2b: 接口定义检索                                         │
│     │   └─ 搜索范围: [confirmed_module] + related_modules           │
│     │              + [COMMON_SERVICE_MODULE]（公共基础服务模块）     │
│     │   遍历每个模块 → 获取 api 类型绑定文档 → 语义检索               │
│     └─ 接口去重: 按 "method url" 键去重，后者覆盖前者（保留最新版）   │
│   输出:                                                            │
│     ├─ state["product_docs"]（合并后的全量产品文档）— List[dict]     │
│     │   = 原有 product_docs + 关联模块的产品文档                     │
│     └─ state["api_definitions"]（去重后的接口定义列表）— List[dict]  │
│         每个 dict 含: name（接口名）, url（路径）, method（方法）,     │
│         parameters（请求参数）, returns（返回字段）, module（模块）    │
└────────────┬────────────────────────────────────────────────────┘
             │
             ▼
┌─────────────────────────────────────────────────────────────────┐
│ 节点 B5: analyze_test_points_raw（分析测试点，两阶段）               │
│   方法: _analyze_test_points_raw                                    │
│   位置: agent_components/retrievers.py:352                           │
│   输入:                                                            │
│     ├─ state["product_docs"]（产品文档检索结果）— 拼接为文本          │
│     ├─ state["related_modules"]（关联模块列表）— 拼接为文本           │
│     ├─ state["api_definitions"]（接口定义检索结果）— 格式化文本       │
│     ├─ state["original_input"]（用户原始输入，上下文参考）            │
│     └─ module_tree_json（模块树 JSON，用于理解模块结构）             │
│   处理（两阶段，thinking 与 json_mode 隔离）:                         │
│     ├─ 阶段 1: thinking 自由分析（深度思考，无结构约束）               │
│     │   prompt: analyze_test_points_prompt()（测试点分析提示词）      │
│     │   method: free_text（自由文本）                                 │
│     │   thinking: on（启用深度思考）                                  │
│     │   输出: 自由文本测试点分析报告                                   │
│     └─ 阶段 2: json_mode 格式化（结构化输出）                         │
│         prompt: format_test_points_prompt()（测试点格式化提示词）     │
│         method: json_mode（结构化输出）                               │
│         thinking: off（互斥）                                         │
│         输出模型: TestPointAnalysis（测试点分析模型）                  │
│   输出:                                                            │
│     └─ state["test_point_analysis"]（测试点分析报告）— 自由文本       │
│         内容: 每个模块的测试场景、正反向用例、边界条件、数据依赖       │
│   日志: thinking_trace.log（LLM 原始 thinking 输出完整记录）          │
└────────────┬────────────────────────────────────────────────────┘
             │
             ▼
┌─────────────────────────────────────────────────────────────────┐
│ 节点 B6: generate_excel_plan（生成 Excel 测试计划 V2）              │
│   方法: _generate_excel_plan_node                                    │
│   位置: agent_components/nodes.py:171                                │
│   输入:                                                            │
│     ├─ state["api_definitions"]（接口定义检索结果，来自 B4）         │
│     ├─ state["test_point_analysis"]（测试点分析报告，来自 B5）        │
│     ├─ state["original_input"]（用户原始输入，上下文参考）            │
│     ├─ state["confirmed_module"]（用户确认的模块名，用于目录路径）     │
│     └─ module_tree_json（模块树 JSON，用于输出目录计算）              │
│   处理:                                                           │
│     ├─ 6.1 输出目录计算                                             │
│     │   输入: 模块树路径 + config.TESTCASE_BASE（输出根目录）       │
│     │   处理: 按模块在树中的路径构建目录；已存在且非空追加 _2, _3...  │
│     │   输出: output_dir（本次生成输出目录路径）                      │
│     │                                                             │
│     ├─ 6.2 LLM 初始生成 (_invoke_structured，统一调用)              │
│     │   输入: api_definitions（接口定义）, module_tree（模块树）,   │
│     │         test_analysis（测试分析报告）, user_context（用户上下文）│
│     │   prompt: generate_excel_plan_prompt()（Excel生成提示词）     │
│     │   method: json_mode（结构化输出）                               │
│     │   thinking: off                                                │
│     │   输出模型: ExcelPlanV2（Excel 计划 V2 模型）                   │
│     │   输出含:                                                       │
│     │     ├─ plan.test_cases（测试用例列表）— List[TestCaseRow]      │
│     │     │   每个含: id（用例编号）, story（子模块/Story）,           │
│     │     │   title（用例标题）, preconditions（前置条件ID列表）,      │
│     │     │   steps（执行步骤）, expected（预期结果）,                 │
│     │     │   mutates_data（是否变更数据）, is_negative_test（是否反向）│
│     │     └─ plan.shared_preconditions（共享前置条件列表）            │
│     │         每个含: id（前置编号）, name（前置名称）,                 │
│     │         steps（详细步骤）, expected（预期结果）                   │
│     │                                                             │
│     ├─ 6.3 首轮校验（纯代码校验，无 LLM）                             │
│     │   输入: plan.test_cases（LLM 输出的用例列表）                   │
│     │   校验项:                                                      │
│     │     ├─ id（用例编号）去重（seen_ids 集合）                       │
│     │     ├─ id / story（子模块）/ title（标题）/ steps（步骤）       │
│     │     │   / expected（预期结果）五字段非空                         │
│     │     ├─ preconditions（前置条件）引用完整性（ID 必须存在）        │
│     │     ├─ steps（步骤）与 expected（预期）换行数一致（步骤数=预期数）│
│     │     └─ 资源冲突消解 (_resolve_resource_conflicts)              │
│     │   输出:                                                       │
│     │     ├─ all_confirmed（通过校验的用例列表）— List[TestCaseRow]  │
│     │     └─ failed_details（失败用例列表）— 每项含: 索引, 用例字典,  │
│     │        错误信息列表                                             │
│     │                                                             │
│     ├─ 6.4 修复重试循环 (EXCEL_REPAIR_ATTEMPTS 次)                  │
│     │   输入: failed_details（失败用例+错误信息）                      │
│     │   触发条件: failed_details 非空                                 │
│     │   prompt: repair_excel_plan_prompt()（Excel修复提示词）         │
│     │     └─ 仅传入 {failed_test_cases}（失败用例+错误），              │
│     │        不传全量分析（避免 LLM 重复输出已通过用例）               │
│     │   method: json_mode（结构化输出）                               │
│     │   输出模型: ExcelPlanV2（仅含失败行修复版）                       │
│     │   修复轮校验（比首轮更严格，纯代码裁剪）:                          │
│     │     ├─ 拒绝不在 failed_ids 中的行（代码侧裁剪，非 prompt 控制）  │
│     │     ├─ 拒绝 _already_confirmed 中的重复 ID                     │
│     │     ├─ 通过 → all_confirmed.append（追加到已确认列表）          │
│     │     └─ 仍失败 → 保留在 failed_details（下一轮继续）              │
│     │   终止条件: failed_details 为空 或 超过重试上限                  │
│     │                                                             │
│     ├─ 6.5 最终组装 valid_cases（合并全量有效用例）                    │
│     │   valid_cases = all_confirmed（累计的已确认用例）                │
│     │     + failed_details 中无孤立前置引用（orphan）的行（降级接受）   │
│     │   n_confirmed = len(valid_cases)（最终用例数）                  │
│     │                                                             │
│     ├─ 6.6 写入 Excel 文件（双 Sheet）                                │
│     │   输入: valid_cases（全量有效用例）+ shared_preconditions（前置）│
│     │   Sheet1 "测试计划"（9 列）:                                    │
│     │     ├─ @allure.epic（史诗）— 项目名                            │
│     │     ├─ @allure.feature（功能模块）— feature 名                 │
│     │     ├─ @allure.story（用户故事）— story 名                     │
│     │     ├─ @allure.title（用例标题）— 用例如"电表新增-正向"         │
│     │     ├─ fixture等级（夹具等级）— 如 "danyuan"                   │
│     │     ├─ 用例编号（用例ID）— 如 "TC-001"                          │
│     │     ├─ 前置步骤（前置条件）— PRE ID 列表或 "无"                  │
│     │     ├─ 执行步骤（测试步骤）— 换行分隔的多步骤描述                 │
│     │     └─ 预期结果（断言描述）— 换行分隔，每行含 [eq/contains/ne/db]│
│     │   Sheet2 "共享前置"（5 列）:                                    │
│     │     ├─ 前置编号（前置ID）— 如 "PRE-001"                         │
│     │     ├─ 前置名称（前置名）— 如 "测试企业A"                        │
│     │     ├─ 详细步骤（前置步骤）— 换行分隔                            │
│     │     ├─ 预期结果（前置断言）— 换行分隔                             │
│     │     └─ 关联用例（引用该前置的用例ID列表）                        │
│     │   输出文件: test_plan.xlsx（Excel 测试计划文件）                 │
│     │                                                             │
│     ├─ 6.7 API 定义快照落盘（规则 M8: 产物传递，不依赖内存态）         │
│     │   输入: all_apis_dict（接口定义字典）                            │
│     │   输出文件: api_defs.json（接口定义快照，与 Excel 同目录）       │
│     │                                                             │
│     ├─ 6.8 Excel 文件层校验 (validate_excel_file，纯代码)            │
│     │   输入: excel_path（Excel 文件路径）                             │
│     │   校验项:                                                       │
│     │     ├─ 文件可正常打开                                           │
│     │     ├─ Sheet1 "测试计划" 存在且表头完整                         │
│     │     ├─ Sheet2 "共享前置" 存在且表头完整（如无前置则为空）        │
│     │     ├─ 必填列非空（epic/feature/story/title/步骤/预期）         │
│     │     └─ 前置编号/名称/步骤/预期非空（如有前置）                   │
│     │   输出: (is_valid: bool, errors: List[str])                    │
│     │                                                             │
│     └─ 6.9 工作流日志写入                                            │
│         输入: valid_cases 全量（非最后一轮 LLM 补丁）                  │
│         输出: logs/workflow/{timestamp}.json（全量数据 JSON）         │
│               logs/workflow/{timestamp}.md（可读摘要 Markdown）        │
│   输出:                                                            │
│     ├─ state["excel_plan"]（Excel 计划对象）— ExcelPlanV2            │
│     ├─ state["excel_path"]（Excel 文件绝对路径）                      │
│     ├─ state["output_dir"]（输出目录路径）                             │
│     ├─ state["response_obj"]（前端展示响应对象）— ProperResponse       │
│     │   └─ final_response: "Excel 测试计划已生成：共 N 条用例"         │
│     ├─ state["requires_review"]（重试耗尽时标记需人工审查）— bool     │
│     └─ state["error_info"]（错误信息列表）— 审查相关错误详情           │
└─────────────────────────────────────────────────────────────────┘
```

---

## 三、Phase C：Python + YAML 生成

### 入口：`POST /confirm-plan` → `_confirm_plan_bg`

```
┌─────────────────────────────────────────────────────────────────┐
│ 确认计划并生成（Phase C 入口）                                      │
│   输入:                                                            │
│     ├─ excel_path（Excel 测试计划文件路径）                         │
│     ├─ task_id（前端任务 ID，用于进度轮询）                          │
│     └─ api_defs_json（接口定义 JSON 字符串，可选）                   │
│   初始化:                                                           │
│     ├─ reload_llm()（重建 LLM 客户端，新连接池）                     │
│     ├─ M8 门控: _resolve_api_defs（解析接口定义来源）                │
│     │   优先级: ① 显式传入 api_defs_json ② 扫描 api_defs.json      │
│     │   两者都缺失 → task=failed（严格阻断，禁止空定义续跑）          │
│     └─ 按序执行: .py 文件生成 → YAML 数据文件生成                     │
└────────────┬────────────────────────────────────────────────────┘
             │
             ▼
┌─────────────────────────────────────────────────────────────────┐
│ Phase C-1: _generate_py_file（生成 Python 测试文件）                │
│   方法: ChatTestAgentGraph._generate_py_file                        │
│   位置: agent_components/generators.py:308                           │
│   输入:                                                            │
│     ├─ excel_path（test_plan.xlsx 文件路径）                         │
│     └─ project_name（项目名称，可选）                                │
│   处理:                                                           │
│     ├─ 1. 读取 Excel (_read_excel_rows，读取工作簿行)               │
│     │   输入: excel_path（Excel 文件路径）                            │
│     │   处理:                                                       │
│     │     ├─ openpyxl.load_workbook → 读取 Sheet1 "测试计划"        │
│     │     ├─ 展开共享前置 (Sheet2 "共享前置" → preconditions 映射)   │
│     │     ├─ try/finally 确保 wb.close()（工作簿关闭）               │
│     │     └─ 合并为 expanded_rows（展开后的行列表，含前置信息）       │
│     │   输出: raw_rows（原始行列表）— List[dict]                      │
│     │     每行含: epic（史诗）, feature（功能模块）, story（用户故事）,│
│     │     title（用例标题）, fixture_level（夹具等级）,                │
│     │     case_id（用例编号）, preconditions（前置条件）,              │
│     │     steps（执行步骤）, expected（预期结果）                     │
│     │                                                             │
│     ├─ 2. 中→英翻译 (_translate_to_en，中文标识符转英文)             │
│     │   输入: 去重后的 feature（功能模块）/story（用户故事）/         │
│     │         title（用例标题）中文集合                                │
│     │   处理:                                                       │
│     │     ├─ 检查翻译缓存 (_translation_cache.json，同目录缓存)      │
│     │     ├─ 未缓存条目 → LLM 翻译 (json_mode, TranslationResult)   │
│     │     ├─ 翻译清洗 (_sanitize_en，移除特殊字符)                    │
│     │     ├─ 拼音回退: LLM 失败→ pypinyin 首字母                     │
│     │     └─ 哈希回退: pypinyin 缺失→ MD5("M" + hex[:7])            │
│     │   输出: 翻译映射表 — {feature_en（英文功能名）: ...,             │
│     │         story_en（英文故事名）: ..., title_en（英文标题）: ...}  │
│     │   输出文件: _translation_cache.json（翻译缓存，持久化）         │
│     │                                                             │
│     ├─ 3. 按 feature（功能模块）分组 → 生成 .py 文件                  │
│     │   输入: expanded_rows（展开行列表）+ 翻译映射表                  │
│     │   处理:                                                       │
│     │     ├─ 同 feature → 一个 .py 文件 (test_{feature_en}.py)      │
│     │     ├─ 同 story → 一个 class (class Test{story_en})          │
│     │     ├─ 每条用例 → @pytest.mark.parametrize 参数组              │
│     │     ├─ fixture（夹具）生成: setup/teardown + 依赖注入           │
│     │     └─ 注入公共 import（公共导入）+ allure 装饰器               │
│     │   输出文件: test_{feature_en}.py（Python 测试文件）            │
│     │                                                             │
│     └─ 4. 统计汇总                                                  │
│   输出:                                                            │
│     ├─ py_path（.py 文件路径）— 首个生成文件                         │
│     ├─ py_file_name（.py 文件名）— 逗号分隔列表                      │
│     ├─ modules（模块数）— class 数量                                │
│     └─ cases（用例数）— test 方法总数                                │
└────────────┬────────────────────────────────────────────────────┘
             │ (进度: 50% → 55%)
             ▼
┌─────────────────────────────────────────────────────────────────┐
│ Phase C-2: _generate_all_yamls（生成 YAML 测试数据文件）             │
│   方法: ChatTestAgentGraph._generate_all_yamls                      │
│   位置: agent_components/generators.py:548                           │
│   输入:                                                            │
│     ├─ excel_path（test_plan.xlsx 文件路径）                         │
│     ├─ api_defs_json（接口定义 JSON 字符串）                         │
│     └─ user_ctx（用户输入上下文）                                    │
│   处理:                                                           │
│     ├─ 1. 读取 Excel + 读取共享前置                                 │
│     │   函数: _read_excel_rows（读取 Excel 行）+                    │
│     │         _read_shared_preconditions（读取共享前置）              │
│     │   输出: raw_rows（原始行列表）, shared_pres（共享前置列表）     │
│     │                                                             │
│     ├─ 2. 断言格式前置校验（C6-1，仅 warn 不阻断）                    │
│     │   输入: raw_rows[].expected（每行预期结果文本）                 │
│     │   校验:                                                       │
│     │     ├─ 双层括号 ([[ 或 ]]) → 格式非法                         │
│     │     ├─ 关键词含空格 ([ eq] / [eq ]) → 格式非法                │
│     │     └─ 无断言关键词 [eq/contains/ne/db] → 无断言              │
│     │   输出: assertion_errors（断言格式错误列表）— 仅 warn 不阻断    │
│     │                                                             │
│     ├─ 3. 按 feature（功能模块）→ story（用户故事）分组               │
│     │   输入: raw_rows（原始行列表）                                  │
│     │   输出: feature_story_map（功能-故事-用例三级字典）              │
│     │                                                             │
│     ├─ 4. 构建 yaml_tasks（YAML 生成任务列表）                       │
│     │   输入: feature_story_map + 翻译映射表                          │
│     │   处理:                                                       │
│     │     ├─ 目录结构: {feature_en}（英文功能名）/                   │
│     │     │            setup_data/（前置数据目录）                    │
│     │     │               ├─ setup_{story_en}.yaml（前置用例）       │
│     │     │               └─ teardown_{story_en}.yaml（清理用例）    │
│     │     │            test_{func_en}/（用例目录）                    │
│     │     │               └─ test_data.yaml（测试数据文件）           │
│     │     └─ 每个 task = (row_dict, output_yaml_path)               │
│     │   输出: yaml_tasks（YAML 任务列表）— List[tuple]               │
│     │                                                             │
│     ├─ 5. _run_yaml_rounds（多轮生成+修复，见第五章详细展开）         │
│     │   输入: yaml_tasks, api_defs_json, user_ctx, output_base      │
│     │   输出: total（总数）, success（成功数）, failed（失败数）,      │
│     │         repaired（修复数）, rounds（轮次数）,                    │
│     │         errors_file（错误文件路径）                             │
│     │                                                             │
│     └─ 6. 工作流日志记录                                             │
│   输出:                                                            │
│     ├─ yaml_total（YAML 文件总数）                                   │
│     ├─ yaml_success（成功生成数）                                    │
│     ├─ yaml_repaired（修复成功数）                                   │
│     ├─ yaml_failed（终态失败数）                                     │
│     ├─ yaml_rounds（执行轮次数）                                     │
│     ├─ errors_file（_generation_errors.json 文件路径）               │
│     ├─ N 个 .yaml 数据文件（每个用例一个）                            │
│     ├─ _generation_errors.json（生成失败详情清单）                    │
│     └─ logs/VALIDATION_INTERCEPT.md（Schema 校验拦截统计报告）       │
└─────────────────────────────────────────────────────────────────┘
```

---

## 四、YAML 生成多轮修复详细流程

### `_run_yaml_rounds` 内部展开

```
┌─────────────────────────────────────────────────────────────────┐
│ _run_yaml_rounds 入口（YAML 生成修复循环）                          │
│   方法: ChatTestAgentGraph._run_yaml_rounds                         │
│   位置: agent_components/generators.py:681                           │
│   输入:                                                            │
│     ├─ yaml_tasks（任务列表）— [(row_dict, output_path), ...]        │
│     ├─ api_defs_json（接口定义 JSON 字符串）                         │
│     ├─ user_ctx（用户输入上下文）                                    │
│     ├─ output_base（输出根目录路径）                                  │
│     ├─ gen_func（可注入的单文件生成函数，测试用）— 默认 _generate_one_yaml│
│     └─ repair_rounds（修复轮数上限）— 默认 config.YAML_REPAIR_ROUNDS│
│   初始化:                                                           │
│     ├─ ValidationInterceptor.reset()（重置 Schema 拦截计数器）       │
│     ├─ total = len(yaml_tasks)（任务总数）                           │
│     └─ pending = [(row, path, None) for row, path in yaml_tasks]   │
│         （待处理列表，第三项 repair_ctx 首轮为 None）                  │
│   循环 (round_no = 1..max_repair+1):                               │
│     ├─ round_no==1: 全量生成轮（所有任务）                            │
│     └─ round_no>=2: 修复轮（仅处理上一轮失败项，带 repair_ctx）        │
└────────────┬────────────────────────────────────────────────────┘
             │
             ▼
┌─────────────────────────────────────────────────────────────────┐
│ 每轮: 线程池并发执行 (_BoundedThreadPoolExecutor，有界线程池)         │
│   输入:                                                            │
│     └─ pending（待处理任务列表）— List[(row, path, repair_ctx)]      │
│   并发控制:                                                         │
│     ├─ max_workers（最大工作线程数）— config.YAML_CONCURRENCY        │
│     └─ max_queue（最大队列长度）— 有界阻塞，队列满时 submit 阻塞调用方│
│   每个任务调用:                                                       │
│     └─ _generate_one_yaml(row, api_defs_json, user_ctx, path,      │
│                           repair_ctx)（单文件生成）                    │
└────────────┬────────────────────────────────────────────────────┘
             │
             ▼
┌─────────────────────────────────────────────────────────────────┐
│ _generate_one_yaml（单文件 YAML 生成 — 两阶段 LLM 调用）              │
│   方法: ChatTestAgentGraph._generate_one_yaml                       │
│   位置: agent_components/generators.py:462                           │
│   输入:                                                            │
│     ├─ row（Excel 行数据）— dict                                    │
│     │    ├─ case_id（用例编号）— 如 "TC-001"                         │
│     │    ├─ feature（功能模块）— 如 "智慧用电"                        │
│     │    ├─ story（用户故事）— 如 "电表管理"                          │
│     │    ├─ title（用例标题）— 如 "电表新增-单一费率-正向"            │
│     │    ├─ steps（执行步骤）— 换行分隔的多步骤文本                    │
│     │    ├─ expected（预期结果）— 换行分隔的断言文本                   │
│     │    ├─ preconditions（前置条件列表）— 如 ["PRE-001"]             │
│     │    ├─ mutates_data（是否变更数据）— bool                       │
│     │    └─ is_negative_test（是否反向用例）— bool                    │
│     ├─ api_defs_json（接口定义 JSON）— 所有接口的完整定义             │
│     ├─ user_ctx（用户输入上下文）                                    │
│     ├─ output_path（输出 YAML 文件路径）                              │
│     └─ repair_ctx（修复上下文，None=首轮）— dict                     │
│          ├─ prior_output（上一轮失败输出）— 原始 JSON 字符串          │
│          ├─ error_detail（校验错误明细）— 逐条错误描述                │
│          ├─ error_pattern_summary（全批次错误模式统计）— 跨文件高频错误│
│          └─ round_no（当前轮次）— 第几轮修复                          │
│   处理:                                                           │
│     ├─ 加载数据工厂方法清单                                           │
│     │   函数: _load_factory_methods() → factory_methods_text       │
│     │   来源: data_factory/registry.py → render_for_prompt()       │
│     │   内容: 所有可用工厂函数的名称、参数和语法说明                   │
│     │                                                             │
│     ├─ === 阶段 1: thinking 分析（深度思考，自由文本） ===          │
│     │   首轮: analyze_yaml_data_prompt()（YAML 分析提示词）         │
│     │   修复轮: repair_yaml_data_prompt()（YAML 修复提示词）         │
│     │   输入变量:                                                   │
│     │     ├─ api_definitions（接口定义 JSON）                       │
│     │     ├─ test_case_logic（用例逻辑）— steps（步骤）+expected（预期）│
│     │     ├─ user_context（用户上下文）                              │
│     │     ├─ data_factory_methods（数据工厂方法清单）                │
│     │     └─ 修复轮额外: error_pattern_summary（错误模式统计）,      │
│     │         prior_output（上一轮失败输出）, error_detail（错误详情）│
│     │   method: free_text（自由文本模式）                             │
│     │   thinking: on（启用深度思考）                                  │
│     │   输出: data_analysis（数据分析报告，自由文本）— 含:            │
│     │     ├─ 接口匹配（每个步骤对应哪个接口）                         │
│     │     ├─ 请求参数（每个接口需要哪些参数，值从哪来）                │
│     │     ├─ 数据传递（哪些步骤的返回值需要 extract）                 │
│     │     ├─ 断言设计（每个步骤应该断言什么字段）                      │
│     │     └─ 工厂方法（哪些参数值需要用工厂方法随机生成）              │
│     │   日志: thinking_trace.log（LLM 原始 thinking 输出）            │
│     │                                                             │
│     ├─ === 阶段 2: json_mode 结构化输出（无思考，纯格式化） ===      │
│     │   prompt: format_yaml_data_prompt()（YAML 格式化提示词）      │
│     │   输入变量:                                                   │
│     │     ├─ data_analysis（数据分析报告，来自阶段 1）               │
│     │     ├─ api_definitions（接口定义 JSON）                       │
│     │     ├─ test_case_logic（用例逻辑文本）                         │
│     │     ├─ user_context（用户上下文）                              │
│     │     └─ data_factory_methods（数据工厂方法清单）                │
│     │   method: json_mode（结构化输出模式）                           │
│     │   thinking: off（互斥，必须禁用）                               │
│     │   max_retries=0（无内联重试，由修复轮统一处理）                 │
│     │   输出模型: TestData（测试数据模型）                             │
│     │   输出含: file_name（输出 YAML 文件名）                         │
│     │          data（步骤列表）— List[StepData]                       │
│     │           每个 StepData 含:                                    │
│     │             ├─ baseInfo（基本信息）                             │
│     │             │    ├─ api_name（接口名称）                        │
│     │             │    ├─ url（接口路径）                             │
│     │             │    ├─ method（HTTP 方法）— get/post/put/delete    │
│     │             │    └─ header（请求头）— Content-Type 等           │
│     │             └─ testCase（测试用例列表）— List[TestCase]         │
│     │                 每个 TestCase 含:                                │
│     │                   ├─ case_name（用例名，中文简要描述）          │
│     │                   ├─ json|params|data（请求体/查询参数/表单）   │
│     │                   ├─ validation（断言规则列表）                 │
│     │                   ├─ extract（从响应提取的字段，JSONPath）      │
│     │                   └─ input_extract（从请求提取的字段）          │
│     │                                                             │
│     ├─ === Pydantic 多层校验（按顺序执行） ===                      │
│     │   TestCase 层校验:                                            │
│     │     ├─ migrate_data_to_json（字段漂移兼容）                     │
│     │     │   输入: LLM 输出的 data 字段                             │
│     │     │   处理: data → request_body 自动迁移                    │
│     │     │   输出: 统计漂移频率（>5% 则 ERROR 级别日志）             │
│     │     ├─ strip_empty_optional_dicts（空字段剔除）                 │
│     │     │   处理: 空 {} 的 extract/input_extract/params → None     │
│     │     ├─ merge_same_type_validations（同类型断言合并）            │
│     │     │   处理: [{eq:{a:1}}, {eq:{b:2}}] → [{eq:{a:1, b:2}}]   │
│     │     ├─ validate_body_exclusivity（json/params/data 三选一）     │
│     │     │   规则 B9: 禁止两种请求参数类型同时出现                   │
│     │     ├─ validate_no_neq_operator（neq 非法运算符检查）            │
│     │     │   规则: 断言运算符仅支持 [eq, contains, ne, db]，neq→ne   │
│     │     ├─ validate_extract_jsonpath（extract JSONPath 前缀检查）   │
│     │     │   规则: extract 的 JSONPath 必须以 $. 开头                │
│     │     └─ validate_validation_not_empty（validation 非空检查）     │
│     │         规则: 每步至少一条断言，禁止 validation: []               │
│     │   StepData 层校验:                                            │
│     │     ├─ normalize_base_info（baseInfo 规范化，静默修正）          │
│     │     │   ├─ method → 强制小写                                   │
│     │     │   ├─ url → 去域名（仅保留 path）                          │
│     │     │   ├─ header → 有 json 体时自动注入 Content-Type           │
│     │     │   └─ data → 表单 Content-Type 时迁移为 form_data         │
│     │     ├─ validate_url_no_placeholder（url 禁动态占位符检查）      │
│     │     │   规则: url 字段禁止使用 ${}，框架不对 url 做 replace_load│
│     │     ├─ validate_header_exists（header 必须存在检查）             │
│     │     │   规则: 每个 baseInfo 必须有 header 键，GET 写 {}，       │
│     │     │         POST 写 Content-Type                              │
│     │     ├─ validate_no_params_in_baseinfo（params 归属检查）         │
│     │     │   规则: params/json/data 只能放在 testCase 内，           │
│     │     │         baseInfo 下只含 api_name/url/method/header       │
│     │     └─ validate_method_body_match（方法-参数类型匹配检查）       │
│     │         规则: GET/DELETE 用 params，POST/PUT/PATCH 用 json      │
│     │   TestData 层校验:                                            │
│     │     └─ validate_placeholders（动态占位符白名单校验）            │
│     │        规则 B1-B4:                                            │
│     │          ├─ B1 双花括号拦截: {{}} 框架不解析                    │
│     │          ├─ B2 占位符内运算拦截: ${func()+1day} 不支持          │
│     │          ├─ B3 非注册表函数拦截: 函数必须在 methods.yaml 中      │
│     │          └─ B4 实参个数/枚举越界: 参数必须匹配注册表定义        │
│     │                                                             │
│     ├─ 校验通过 → 序列化为 YAML → 写入文件                            │
│     │   序列化: model_dump(exclude_none=True, by_alias=True)        │
│     │   写入: yaml.dump → output_path（通过 .tmp + os.replace）      │
│     │   返回: output_path（写入的 YAML 文件路径）                     │
│     │                                                             │
│     └─ 校验失败 → 抛出 ValueError                                     │
│         异常信息包含三要素: ① 错在哪 ② 为什么错 ③ 正确做法              │
│         ValidationInterceptor.record(rule_name, error_msg)         │
│         （记录拦截规则名和错误信息到拦截统计）                          │
└────────────┬────────────────────────────────────────────────────┘
             │
             ▼
┌─────────────────────────────────────────────────────────────────┐
│ 每轮结束后处理（统计 + 错误聚合 + 修复上下文构建）                    │
│   成功任务:                                                         │
│     ├─ success++（成功计数）                                        │
│     └─ 修复轮: repaired++（修复成功计数）                            │
│   失败任务: 登记到 failures 列表                                     │
│     ├─ placeholder_id（占位ID）— GEN-FAIL-R{round}-{seq}           │
│     ├─ case_id（用例编号）— 关联的用例标识                           │
│     ├─ yaml_path（YAML 相对路径）                                   │
│     ├─ rounds_attempted（已尝试轮次）                                │
│     ├─ error（完整错误信息）— 含教学信息                             │
│     └─ raw_output_snippet（LLM 原始输出片段）— 用于分析              │
│   修复轮额外构建:                                                    │
│     ├─ _summarize_error_patterns（聚合全批次错误模式）               │
│     │   按类别计数: B1 双花括号, B2 占位符运算, B3 非注册表函数,       │
│     │              B4 实参越界, B5/B10 提取类型错误,                  │
│     │              B6/B7 空列表, B8 结构解析失败, B9 参数冲突         │
│     └─ repair_ctx（修复上下文）传入下一轮                              │
│         └─ pending = 失败项列表 (每项带 repair_ctx)                   │
│   循环终止:                                                         │
│     ├─ pending 为空（全部成功，正常退出）                             │
│     └─ round_no > max_repair+1（超过修复轮上限，强制结束）            │
└────────────┬────────────────────────────────────────────────────┘
             │
             ▼
┌─────────────────────────────────────────────────────────────────┐
│ 终态处理（最终结果写入）                                            │
│   超过修复轮上限仍失败:                                              │
│     ├─ 写入 _generation_errors.json（所有终态失败项详细清单）         │
│     │   内容: 每个失败项的 placeholder_id, case_id, yaml_path,       │
│     │         rounds_attempted, error, raw_output_snippet            │
│     ├─ 不写占位文件（规则 M8: 禁止假数据托底）                        │
│     └─ 日志: tlog.info("FINAL_FAILED: N 个")                       │
│   拦截报告:                                                         │
│     └─ ValidationInterceptor.write_report("logs")                  │
│         输出文件: logs/VALIDATION_INTERCEPT.md                      │
│         内容:                                                        │
│           ├─ 总拦截次数                                              │
│           ├─ 各规则拦截次数 + 占比                                   │
│           ├─ 各规则错误信息样本（最多 3 条）                          │
│           └─ 提示词优化建议（优先处理命中最多的规则）                  │
│   最终统计返回:                                                      │
│     └─ {total（总数）, success（成功数）, failed（失败数）,           │
│         repaired（修复数）, rounds（轮次数）,                         │
│         errors_file（错误文件路径）}                                 │
└─────────────────────────────────────────────────────────────────┘
```

---

## 五、资源冲突消解（Phase B 纯代码节点）

```
┌─────────────────────────────────────────────────────────────────┐
│ _resolve_resource_conflicts（共享前置资源冲突消解）                  │
│   方法: ChatTestAgentGraph._resolve_resource_conflicts             │
│   位置: agent_components/nodes.py:545                               │
│   输入:                                                            │
│     └─ plan（Excel 计划对象）— ExcelPlanV2                           │
│         含: shared_preconditions（共享前置列表）,                    │
│              test_cases（测试用例列表）                               │
│   处理:                                                           │
│     ├─ 1. 关键字回退检测                                            │
│     │   条件: steps（步骤文本）含 RESOURCE_MUTATE_KEYWORDS 中关键词  │
│     │   动作: 强制标记 mutates_data=True（标记为数据变更操作）        │
│     ├─ 2. 构建 PRE→写操作用例 映射                                   │
│     │   条件: mutates_data=True 且 is_negative_test=False           │
│     │   输出: pre_refs = {PRE_ID: [用例列表]}                       │
│     ├─ 3. 冲突检测                                                   │
│     │   条件: 同一个 PRE 被 ≥2 个写操作用例引用                      │
│     │   风险: 两个用例共享同一资源，后执行的会破坏先执行的环境        │
│     └─ 4. 克隆隔离                                                   │
│         策略: 第一个用例保留原始 PRE 引用，其后用例获得克隆           │
│         克隆名: PRE_{原ID}_isolated_{TC_ID}                         │
│         操作: 创建新的 SharedPrecondition → 追加到 shared_preconditions│
│               更新用例的 preconditions 引用                          │
│   输出:                                                            │
│     └─ plan（原地修改，新增克隆前置到 shared_preconditions）          │
│   日志: isolation_count > 0 时输出消解器完成信息                      │
└─────────────────────────────────────────────────────────────────┘
```

---

## 六、LLM 调用机制

```
┌─────────────────────────────────────────────────────────────────┐
│ _invoke_structured（统一 LLM 结构化调用入口）                        │
│   方法: ChatTestAgentGraph._invoke_structured                       │
│   位置: agent_components/nodes.py:763                                │
│   输入:                                                            │
│     ├─ prompt（提示模板）— ChatPromptTemplate                        │
│     ├─ model_class（输出模型类）— Pydantic BaseModel                 │
│     ├─ max_retries（最大重试次数）— 默认 config.MAX_RETRIES          │
│     ├─ method（调用方式）— function_calling/json_mode/json_schema    │
│     │                       /free_text                                │
│     ├─ thinking（是否启用深度思考）— bool                            │
│     └─ **kwargs（提示模板变量注入）— 如 api_definitions=...,         │
│             test_case_logic=..., user_context=...                    │
│   处理:                                                           │
│     ├─ 1. 方法兼容性检查 (METHOD_FEATURES 声明式配置表)              │
│     │    function_calling/json_mode/json_schema:                    │
│     │      supports_thinking=False（thinking 与结构化输出互斥）     │
│     │    free_text: supports_thinking=True                          │
│     │    未知 method: 自动降级 + logger.warning 告警                │
│     ├─ 2. 构建 LLM 链                                               │
│     │    chain = prompt | llm.with_structured_output(              │
│     │        model_class, method=method)                           │
│     ├─ 3. 调用 + 重试循环 (最多 1+max_retries 次)                   │
│     │    chain.invoke(kwargs) → 结构化输出                          │
│     │    dict 结果 → model_class(**dict) 转换                       │
│     │    捕获异常:                                                   │
│     │      ├─ ValidationError（Pydantic 校验失败）→ 重试            │
│     │      ├─ OutputParserException（输出解析失败）→ 重试            │
│     │      └─ BadRequestError（API 请求失败）→ 重试                 │
│     │    重试时: logger.warning 记录失败次数                          │
│     └─ 4. 全部重试耗尽 → RuntimeError                                │
│         错误信息含: "Failed to parse {model_class} from completion" │
│                     + 原始输出 + 校验错误                             │
│   输出:                                                            │
│     └─ model_class 实例（Pydantic 校验通过的完整对象）               │
└─────────────────────────────────────────────────────────────────┘
```

---

## 七、验证器节点（纯代码，无 LLM）

```
┌─────────────────────────────────────────────────────────────────┐
│ validate_excel_file（Excel 文件层校验）                              │
│   位置: agent_components/validator.py:31                             │
│   输入:                                                            │
│     └─ excel_path（Excel 文件路径）— test_plan.xlsx                  │
│   校验:                                                           │
│     ├─ 文件可正常打开（openpyxl.load_workbook）                     │
│     ├─ Sheet1 "测试计划" 存在                                       │
│     ├─ Sheet1 表头: @allure.epic（史诗）, @allure.feature（功能）,  │
│     │   @allure.story（故事）, @allure.title（标题）,                │
│     │   fixture等级（夹具等级）, 用例编号, 执行步骤（步骤）,         │
│     │   预期结果（预期）                                             │
│     ├─ Sheet2 "共享前置" 存在（可为空）                               │
│     ├─ Sheet2 表头: 前置编号, 前置名称, 详细步骤, 预期结果,          │
│     │   关联用例                                                     │
│     ├─ Sheet1 必填列非空: epic（史诗）, feature（功能）,             │
│     │   story（故事）, title（标题）, fixture等级,                    │
│     │   执行步骤（步骤）                                             │
│     └─ Sheet2 如有行，必填列非空: 前置编号, 前置名称,                │
│         详细步骤, 预期结果                                            │
│   输出:                                                            │
│     ├─ is_valid（是否通过）— bool                                   │
│     └─ errors（错误信息列表）— List[str]，每项含行号和具体错误       │
│                                                                   │
│ _validate_excel_plan（Excel 计划业务逻辑校验）                        │
│   位置: agent_components/nodes.py:499                                │
│   输入:                                                            │
│     └─ plan（Excel 计划）— ExcelPlan or ExcelPlanV2                 │
│   校验:                                                           │
│     ├─ rows/test_cases 非空（至少有一条用例）                        │
│     ├─ case_name 以 "test_" 开头（Python 函数名规范）               │
│     └─ enabled 字段为 Y 或 N（是否启用标记）                        │
│   输出:                                                            │
│     └─ errors（错误信息列表）— List[str]                             │
└─────────────────────────────────────────────────────────────────┘
```

---

## 八、前端交互流程

```
┌─────────────────────────────────────────────────────────────────┐
│ 前端页面（index.html + app.js）                                     │
│   操作流程:                                                        │
│     1. 上传文档 → POST /upload → pollTask（轮询任务进度）            │
│     2. 输入描述 → POST /workflow/start → pollTask                   │
│     3. 确认模块 → 用户回复 → LangGraph resume（恢复执行）            │
│     4. 确认计划 → POST /confirm-plan → pollTask                     │
│   pollTask（轮询函数）:                                              │
│     ├─ 输入: task_id（任务ID）                                       │
│     ├─ 间隔: 2 秒（每次轮询等待）                                     │
│     ├─ 上限: 900 次（30 分钟超时）                                    │
│     ├─ 状态 running → 显示 progress（进度百分比）+ message（进度文本）│
│     ├─ 状态 completed → 显示 result（结果数据）                       │
│     └─ 状态 failed → 显示 error（错误信息）                           │
│   心跳机制（后端，避免前端超时）:                                      │
│     ├─ Phase B: _resume_workflow_bg 主协程心跳                        │
│     │   每 10s 调用 _update_task 更新进度 + 阶段描述文字               │
│     │   阶段轮转: "正在检索产品文档..." → "正在提取关联模块..." → ...  │
│     └─ Phase C: _confirm_plan_bg YAML 生成心跳                        │
│         每 10s 调用 _update_task 更新 "正在生成 YAML...（Xs）"        │
└─────────────────────────────────────────────────────────────────┘
```

---

## 九、完整 State 字段生命周期

```
State 字段流转（按节点填充次序）:

user_input（用户输入）───────────── 贯穿全流程 ─────────────►
original_input（原始输入）───────── 贯穿全流程 ─────────────►
context（上下文）─── B2:retrieve_docs（产品文档检索后填充）─►
product_docs（产品文档）── B2:retrieve_docs ──► B3:extract_modules ──► B4:retrieve_data ──► B5:test_points
related_modules（关联模块）───────── B3:extract_modules ──► B4:retrieve_data
api_definitions（接口定义）───────── B4:retrieve_data ──► B5:test_points ──► B6:excel_plan
test_point_analysis（测试点分析）── B5:test_points ──► B6:excel_plan
candidate_modules（候选模块）────── B1:confirm_intent ──► 前端展示
confirmation_question（确认提示）── B1:confirm_intent ──► 前端展示
workflow_status（工作流状态）────── B1:confirm_intent ──► 路由决策 ──► 后续节点
confirmed_module（确认模块）─────── B1:confirm_intent ──► B2~B6 全流程过滤
excel_plan（Excel计划）─────────── B6:excel_plan ──► Phase C
excel_path（Excel路径）─────────── B6:excel_plan ──► Phase C
output_dir（输出目录）──────────── B6:excel_plan ──► Phase C
response_obj（响应对象）────────── B1/B6 ──► 前端
requires_review（需人工审查）───── B6:excel_plan ──► graph_builder fallback
error_info（错误信息）──────────── B6:excel_plan ──► 前端/日志
```

---

## 十、错误处理路径汇总

```
┌─────────────────────────────────────────────────────────────────┐
│ 系统各层级错误处理                                                 │
│                                                                   │
│ 文件上传层 (_process_file_bg):                                    │
│   ├─ FileNotFoundError → task=failed, 错误: "上传文件不存在"       │
│   ├─ .md 提取异常 → task=failed, 删除源文件                         │
│   └─ 通用 Exception → task=failed                                 │
│                                                                   │
│ LLM 调用层 (_invoke_structured):                                   │
│   ├─ ValidationError → 重试 (最多 max_retries)                     │
│   ├─ OutputParserException → 重试                                   │
│   ├─ BadRequestError → 重试                                         │
│   └─ 全部重试耗尽 → RuntimeError → 外层捕获登记                     │
│                                                                   │
│ 意图确认层 (_confirm_user_intent):                                  │
│   └─ LLM 解析失败 → 降级为 WAITING（返回通用提示，不崩溃）          │
│                                                                   │
│ Excel 生成层 (_generate_excel_plan_node):                            │
│   ├─ 首轮校验失败 → 进入修复重试 (EXCEL_REPAIR_ATTEMPTS 次)         │
│   ├─ 修复轮 LLM 输出不在 failed_ids → 丢弃 + 日志告警（代码裁剪）   │
│   ├─ 修复轮 重复 ID → 丢弃 + 日志告警                                │
│   ├─ 修复轮耗尽 → valid_cases 为空则返回 error_info                  │
│   └─ 文件层校验失败 → 仅 warn + 日志, 不阻断                         │
│                                                                   │
│ YAML 生成层 (_run_yaml_rounds):                                    │
│   ├─ 单文件校验失败 → 登记到 failures, 进入修复轮                    │
│   ├─ 修复轮耗尽仍失败 → 写入 _generation_errors.json                │
│   └─ 全部轮次失败 → 不写占位文件 (M8 规则: 禁止假数据)               │
│                                                                   │
│ 工作流层 (_resume_workflow_bg):                                     │
│   ├─ B2 无数据 (NO_DATA) → task=failed                              │
│   ├─ 异常 → task=failed, finally 块清理 session                     │
│   └─ graph.invoke 超时 → task=failed                                │
│                                                                   │
│ 确认计划层 (_confirm_plan_bg):                                      │
│   ├─ api_defs.json 缺失 (M8 规则) → task=failed（严格阻断）         │
│   └─ 异常 → task=failed                                            │
│                                                                   │
│ 翻译回退层 (_translate_to_en):                                       │
│   ├─ LLM 翻译失败 → pypinyin 拼音首字母回退                          │
│   └─ pypinyin 缺失 → MD5 哈希回退 ("M" + hex 前 7 位)              │
└─────────────────────────────────────────────────────────────────┘
```

---

## 十一、配置常量 → 作用域映射

```
┌─────────────────────────────────────────────────────────────────┐
│ 关键配置项（来源: settings.py / config.py）                         │
│                                                                   │
│ 检索相关:                                                        │
│   RETRIEVAL_K（检索返回数量上限）— 每次查询返回的最大文档数         │
│   CHROMA_RETRY_DELAY（ChromaDB 重试延迟）— 不可用时延迟重试秒数    │
│                                                                   │
│ 生成相关:                                                        │
│   MAX_RETRIES（LLM 最大重试次数）— _invoke_structured 默认重试     │
│   EXCEL_REPAIR_ATTEMPTS（Excel 修复轮上限）— 计划生成最大修复次数   │
│   YAML_REPAIR_ROUNDS（YAML 修复轮上限）— YAML 生成最大修复次数      │
│   YAML_CONCURRENCY（YAML 生成并发数）— 并行线程数                   │
│                                                                   │
│ 线程池相关:                                                        │
│   TASK_MAX_WORKERS（线程池最大线程）— 全局线程池容量                 │
│   TASK_MAX_QUEUE（线程池最大队列）— 队列满时阻塞（背压）             │
│                                                                   │
│ 会话相关:                                                        │
│   WORKFLOW_SESSION_TTL（工作流会话过期）— Phase B 会话超时秒数      │
│   TASK_TTL_SECONDS（任务状态过期）— 任务存储超时秒数                 │
│                                                                   │
│ 业务相关:                                                        │
│   RESOURCE_MUTATE_KEYWORDS（写操作关键词）— 用于回退 mutates_data   │
│   COMMON_SERVICE_MODULE（公共服务模块）— Phase B 接口搜索的公共模块  │
│   TESTCASE_BASE（用例输出根目录）— 如 C:\...\testcase\园区基线      │
│   UPLOAD_MAX_SIZE_MB（上传大小上限）— 文件上传硬限制                 │
└─────────────────────────────────────────────────────────────────┘
```

---

## 十二、API 端点 → 任务 → 产出映射

```
┌─────────────────────────────────────────────────────────────────┐
│ POST /upload（上传文档）                                            │
│   输入: file（上传文件对象，UploadFile）                             │
│   后台: _process_file_bg                                           │
│     ├─ 文件接收校验 → 文件类型解析 → 文本分块                        │
│     ├─ SQLite 先写 → ChromaDB 后写（失败时回滚 SQLite）             │
│     └─ .md 文件额外: LLM 提取接口定义 → ChromaDB api_defs 集合      │
│   输出: {"success": True, "task_id": "..."}                        │
│   产出: SQLite 记录 + ChromaDB 向量 + .meta.json                    │
│                                                                   │
│ POST /workflow/start（启动工作流）                                   │
│   输入: user_input（用户输入文本，Form）                              │
│   处理: Phase B LangGraph → 运行到 confirm_intent                    │
│   输出-挂起: {"session_id": "...", "status": "waiting",            │
│              "question": "...", "candidates": [...]}               │
│   输出-继续: {"status": "no_match"/"completed"}                     │
│                                                                   │
│ POST /workflow/confirm（确认模块选择）                                │
│   输入: session_id（会话ID）, choice（用户选择，Form）                │
│   后台: _resume_workflow_bg → 继续 LangGraph B2→B3→B4→B5→B6→END    │
│   输出: {"task_id": "...", "status": "running"}                     │
│   产出: test_plan.xlsx + api_defs.json + workflow 日志               │
│                                                                   │
│ POST /confirm-plan（确认计划→生成代码）                               │
│   输入: excel_path（Excel路径）, api_defs_json（接口定义JSON）,      │
│         user_ctx（用户上下文，均为 Form）                             │
│   后台: _confirm_plan_bg                                            │
│     ├─ M8 门控: api_defs 缺失 → 阻断                                 │
│     ├─ _generate_py_file: 读取 Excel → 翻译 → 生成 .py 文件         │
│     └─ _generate_all_yamls: 多轮生成 + 修复 YAML 文件               │
│   输出: {"task_id": "...", "status": "running"}                     │
│   产出: .py 文件 + .yaml 文件 + _generation_errors.json              │
│         + VALIDATION_INTERCEPT.md + thinking_trace.log              │
│                                                                   │
│ GET /task/{task_id}（轮询任务状态）                                  │
│   输入: task_id（任务ID，URL Path）                                  │
│   输出: {"task": {status（状态）, progress（进度%）,                 │
│                   message（进度文本）, result（结果数据）,            │
│                   error（错误信息）}}                                │
└─────────────────────────────────────────────────────────────────┘
```

---

## 十三、文件产出物总览

| 阶段 | 产出文件 | 格式 | 用途 |
|------|---------|------|------|
| 文件上传 | SQLite 记录（文档主表+关联表） | DB | 关系库持久化 |
| 文件上传 | ChromaDB 向量（product_docs/api_defs） | Vector | 语义检索 |
| 文件上传 | .meta.json（元数据文件） | JSON | 磁盘元信息 |
| Phase B | test_plan.xlsx（测试计划） | Excel | 双 Sheet 测试计划 |
| Phase B | api_defs.json（接口定义快照） | JSON | Phase C 数据源 |
| Phase B | workflow/{timestamp}.json + .md（日志） | JSON+MD | 工作流可观测 |
| Phase C | test_{feature}.py（测试代码） | Python | pytest 执行文件 |
| Phase C | setup_{story}.yaml（前置用例） | YAML | 共享前置数据 |
| Phase C | teardown_{story}.yaml（清理用例） | YAML | 清理/回滚数据 |
| Phase C | test_data.yaml（测试数据） | YAML | 每条用例的数据 |
| Phase C | _translation_cache.json（翻译缓存） | JSON | 中英翻译复用 |
| Phase C | _generation_errors.json（生成错误） | JSON | 失败详情清单 |
| Phase C | VALIDATION_INTERCEPT.md（拦截报告） | Markdown | Schema 拦截统计 |
