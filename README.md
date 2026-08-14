# 智能测试助手 (Intelligent Test Assistant)

基于 LangGraph + RAG + DeepSeek 的 AI 测试用例生成平台。上传产品文档、Axure 原型与接口定义，AI 自动分析业务场景、设计测试用例、生成可执行的 pytest 测试脚本与 YAML 测试数据。

> 📌 本项目为**架构防御体系**工作流（`.claude/skills/`）管理项目，扫描/修复/规则编译通过 `/全盘扫描`、`/修复`、`/规则更新` 触发，详见 `.claude/CLAUDE.md`。

---

## 系统架构

平台按 **Phase A（摄取）→ Phase B（工作流）→ Phase C（生成）** 三阶段串联：

```
Phase A — Ingest（文档摄取与向量化）
─────────────────────────────────────────────────────
产品文档 (Word/PDF/MD)
  ├── 文本提取 + 图片保存（.docx）
  ├── LLM 提取模块归属（DocModuleExtract）
  ├── LLM 提取业务术语表（GlossaryExtract）
  ├── 人工审核弹窗 → 确认/修改关联关系
  └── SQLite 落库 → ChromaDB 统一 doc_search 向量化

Axure HTML 原型包 (.zip)
  ├── 解压 → sitemap.js → 页面树
  ├── 解析 HTML → data-label + 可见文本
  ├── data/data.js → 交互流提取（触发→动作→目标）
  └── → 与产品文档同下游（SQLite + ChromaDB）

接口文档 (Word/PDF/MD / YApi 导出 MD)
  ├── LLM 提取（ApiDefExtract）或纯代码提取（extract_apis_from_yapi_md）
  ├── 白名单校验 → 过滤 LLM 幻觉接口
  ├── 前端确认接口列表 + 指定所属模块 → commit_api_docs
  └── SQLite 结构化落库（api_* 列）+ ChromaDB 自然语言检索文本

模块场景预分析（可选，前端按钮触发）
  ├── Step1 场景分析（产品文档 → 测试场景总结）
  ├── Step2 UI 交互分析（+ Axure → 逻辑关系总结）
  └── Step3 接口映射（+ API → 接口总结）→ 落 module_analysis 表

Phase B — Workflow（LangGraph 工作流）
─────────────────────────────────────────────────────
用户输入测试需求
  │
  ├── 节点1 confirm_intent        模块语义匹配（IntentConfirmation）
  │        ├── WAITING → 挂起，用户点选模块
  │        └── CONFIRMED → 继续
  ├── 节点2 retrieve_product_docs   Hop 1: 检索 doc_search → 主模块文档
  │        └── NO_DATA → 终止，提示先导入文档
  ├── 节点3 extract_related_modules  LLM 提取关联模块列表
  ├── 节点4 retrieve_related_data    Hop 2a: 关联模块文档
  │                                  Hop 2b: 关联模块 + 公共基础服务接口
  ├── 节点5 generate_plan_thinking   thinking 分析 → json_object 一步生成 ExcelPlanV2
  │        （只生成不落盘，plan_source 标注数据来源；接口只喂概要）
  └── 节点6 generate_excel_plan      纯处理节点（校验/修复/消解/落盘）
         ├── 数据源检测：有 plan → 消费；无 plan（thinking 失败）→ requires_review
         ├── 校验（ExcelPlanValidator：字段/前置引用/步骤对齐/断言格式/URL/db 拦截）
         ├── 修复轮（LLM 只重填失败行，拦截原因按类型聚合）
         ├── 引用完整性 + 资源冲突消解（冲突前置克隆隔离）
         └── 落盘：test_plan.xlsx（双 Sheet）+ api_defs.json 接口快照
              （Phase C 数据来源，缺失即阻断确认）

Phase C — Generation（确认计划 → 生成 .py + .yaml）
─────────────────────────────────────────────────────
/confirm-plan（excel_path + api_defs_json + user_ctx）
  │
  ├── 0. 接口定义解析 _resolve_api_defs（规则 M8：缺失 → 显式阻断）
  ├── Step0 dependency_map.json     thinking 生成依赖映射（当前未被下游消费）
  ├── C4  英文翻译                 缓存优先 → LLM → 拼音兜底
  ├── C   .py 文件生成             纯代码组装（fixture + run_blocks，不经 LLM）
  └── C   YAML 文件生成（并发）
        ├── ① thinking 分析（free_text）
        ├── ② json_mode 格式化（单次输出 TestData）
        ├── 规整层（确定性修正：method 小写/url 去域名/header 补全/断言合并）
        ├── 校验层（回炉类：占位符注册表/三选一/空列表/提取值类型，失败不写盘）
        ├── 修复循环：失败登记占位 → 轮末错误模式汇总 → 思考自查 → 重生成
        └── 终态失败 → _generation_errors.json + 计 failed（无占位假文件）
  └── 后校验（YamlPostValidator → _post_validation_issues.json）
```

**存储模型（2026-07 存储反转）**：**SQLite（`data/app.db`）为持久事实源**，保存文档元数据、切块原文、接口定义（api_* 结构化列）、绑定关系、术语表、模块场景分析、补偿任务；**ChromaDB 统一 `doc_search` 集合仅作检索索引**。原双集合（product_docs / api_defs）已废弃。

---

## 功能特性

- **多格式文档上传** — PDF / Markdown / Word (.docx) / Axure HTML 演示包 (.zip)；MD 接口文档支持「代码提取 / LLM 提取」双模式
- **智能模块关联** — LLM 自动提取文档的模块归属和跨模块依赖关系，人工审核确认
- **SQLite 单一事实源 + 统一向量索引** — 存储反转：接口定义/切块原文/绑定/术语全部结构化落 SQLite，ChromaDB `doc_search` 统一检索，Chroma 失败时补偿回滚 SQLite，杜绝「索引有、数据无」
- **模块场景三步预分析** — Phase A 可选触发 LLM 预分析（场景 / UI 交互 / 接口映射），Phase B 直接消费权威分析源，跳过重复场景识别
- **多跳检索（Multi-hop）** — 根据模块依赖关系自动追溯关联文档和接口定义；检索结果可经 SQLite 兜底补偿（向量库异常时降级查询）
- **生成/处理解耦** — `generate_plan_thinking`（thinking + json_object）只生成 plan 入 state；`generate_excel_plan` 纯处理节点（校验/修复/消解/落盘）；thinking 失败 → `requires_review` 人工审查，**不降级自生成**
- **Excel 测试计划** — 双 Sheet（测试计划 9 列 + 共享前置 5 列），含 Allure 标签、模块划分、断言标签（eq/contains/ne/db）
- **自动校验修复** — Pydantic + 文件层双重校验；ExcelPlanValidator 按 9 类错误类型聚合拦截原因；修复轮最多 3 次 + 资源冲突消解（克隆隔离）+ 人工审查兜底
- **YAML 质量治理** — 规整/重生成两分法：确定性格式修正静默执行；语义性错误（占位符幻觉/三选一冲突/空输出）登记后集中送思考节点自查重生成；终态失败输出 `_generation_errors.json`，杜绝「假成功」
- **数据真实性** — 接口定义快照 `api_defs.json` 随计划落盘，Phase C 确认时缺失/为空显式阻断（规则 M8），禁止空定义盲写 YAML
- **接口标注注册表** — `ApiAnnotationRegistry` 自动检测 `is_export`（导出接口）/ `has_path_params`（路径参数）等标注，随 `api_defs.json` 存库，Phase C 消费
- **数据工厂注册表** — `data_factory/methods.yaml` 单一事实源（目录+大类结构），prompt 渲染 / 占位符校验 / 单元测试三处同源
- **Python 测试脚本** — 生成 pytest + allure 测试类代码（fixture 引用 setup/teardown YAML）
- **YAML 测试数据** — 结构化的 `test_data.yaml` / `setup_*.yaml` / `teardown_*.yaml` 三件套
- **模块目录树** — 模块的增删改查、重命名级联、合并、删除（非叶子禁止）
- **业务术语表** — LLM 提取产品文档术语，减少字段名/状态值幻觉；随文档生命周期
- **后台任务 + 补偿 worker** — 有界线程池统一管理异步任务；补偿 worker 轮询 `compensation_tasks` 表，重试 LLM 摘要 / Chroma 重建等失败任务
- **Web 单模式** — FastAPI Web 界面（CLI 交互模式已废弃）
- **结构化日志** — JSON 格式日志 + ContextVar trace_id 全链路追踪 + `thinking_trace.log` 思考全文

---

## 节点与模型策略

| 节点 | method | thinking | Pydantic 模型 |
|------|--------|----------|------|
| 模块意图匹配 `confirm_intent` | json_mode | ❌ | IntentConfirmation |
| 产品文档解析 | json_mode | ❌ | DocModuleExtract |
| 业务术语提取 | json_mode | ❌ | GlossaryExtract |
| 接口提取 | json_mode | ❌ | ApiDefExtract |
| 模块场景预分析（Phase A 三步） | free_text | ✅ | 无（自由文本落 module_analysis 表） |
| Excel 一步生成 `generate_plan_thinking` | thinking → json_object | ✅ | ExcelPlanV2 |
| Excel 处理 `generate_excel_plan` | 纯代码 | — | —（校验/修复/消解/落盘，不经 LLM） |
| 英文翻译（缓存未命中时） | json_mode | ❌ | TranslationResult |
| 依赖映射 `dependency_map` | free_text | ✅ | DependencyMap |
| YAML 数据分析 / 修复轮自查 | free_text | ✅ | 无（自由文本，全文落 thinking_trace.log） |
| YAML 格式化（单次，无 inline 重试） | json_mode | ❌ | TestData（占位符/三选一/空列表校验内置） |
| .py 生成 | 纯代码组装 | — | —（不经 LLM） |

> DeepSeek V4 的 thinking 控制通过声明式 `METHOD_FEATURES` 配置表管理（`agent_components/nodes.py`）：
> - `function_calling` / `json_mode` / `json_schema` 均不支持 thinking
> - `free_text` 支持 thinking（分析节点）
> - 未知 method 自动禁用 thinking 并记日志警告

---

## 快速开始

### 前置条件

- Python 3.10+
- [Ollama](https://ollama.ai/)（本地嵌入模型，必需）
- 安装嵌入模型：`ollama pull bge-m3`
- 启动前可运行 `.\infra\start_ollama.bat` 自动检测并启动 Ollama（服务启动时也会自动尝试拉起）
- **GPU 显存 < 4GB 建议用 CPU 模式**（见下方 [GPU 显存不足？](#gpu-显存不足)）
- LLM：DeepSeek API（推荐）或本地模型

### 安装

```bash
cd Generate-test-cases-and-data
python -m venv .venv
# Windows
.venv\Scripts\activate
# Linux/Mac
source .venv/bin/activate
pip install -r requirements.txt
```

### 配置

配置分为两层（`settings.py` 顶部有详细说明）：

**① `.env` — 模型地址与 API Key**（敏感信息 / 环境差异）

```env
# ========== Embedding 模型（Ollama 必需） ==========
EMBEDDING_MODEL=bge-m3
EMBEDDING_URL=http://localhost:11434

# ========== DeepSeek API（推荐，三项齐全即启用） ==========
DEEP_URL=https://api.deepseek.com
DEEP_API_KEY=sk-your-key-here
DEEP_MODEL=deepseek-v4-pro

# ========== 本地 LLM（DeepSeek 任一缺失时自动降级） ==========
# LLM_MODEL=qwen2.5:14b
# LLM_API_KEY=sk-no-auth
# LLM_BASE_URL=http://localhost:11434/v1

# ========== 深度思考控制 ==========
ENABLE_THINKING=true
```

**② `settings.py` — 其余可调参数**（.env 不生效）

启动前必须配置**输出路径**（缺失则启动报错）：

```python
pycharm_misc = "C:\\path\\to\\your\\pycharm\\project"   # 目标项目根路径
testcase_base = "testcase"                               # 输出子目录名
```

其他常用参数（直接改 `settings.py` 中各 Field 的 default）：
- `chunk_size` / `chunk_overlap` / `retrieval_k` — 切分与检索
- `excel_repair_attempts` / `yaml_repair_rounds` / `yaml_failure_circuit_breaker` — 修复与熔断
- `yaml_concurrency` / `task_max_workers` / `task_max_queue` — 并发
- `web_host` / `web_port` / `upload_max_size_mb` — Web 服务

### 启动

```bash
python web_app.py
```

访问 `http://localhost:8000`。终端输入 `q` 回车可停止服务。

> CLI 交互模式（`main.py`）已废弃，统一使用 Web 界面。

---

## 使用指南

### 1. 上传文档

支持四种类型：
- 📄 **PDF** — API 文档、产品说明
- 📝 **Markdown** — 接口文档（上传后弹窗选择「代码提取 / LLM 提取」）
- 📃 **Word (.docx)** — 产品需求文档（含图片自动提取）
- 🎨 **Axure (.zip)** — HTML 原型演示包（自动解析页面树 + 交互流）

上传后自动弹出模块审核弹窗，可修改模块名称和关联模块。

### 2. 管理模块

在「模块管理」面板创建/重命名/删除/合并模块目录。
重命名会自动级联更新所有绑定关系；非叶子节点禁止删除。

### 3. 模块场景预分析（可选）

在模块面板触发「场景分析」，Phase A 对模块进行三步预分析（场景 / UI 交互 / 接口映射），
后续 Phase B 生成测试计划时直接消费该权威分析，质量更高、token 更省。

### 4. 输入测试需求

```
分析合同管理功能，生成功能测试用例
测试车辆入场后查询在场记录的功能
```

### 5. 确认模块 → 查看结果

- 系统匹配候选模块，确认后执行多跳检索
- 测试点分析（thinking）→ 生成/处理解耦产出 Excel 测试计划
- 校验失败时自动修复或标记人工审查（`requires_review`）

### 6. 确认计划 → 生成产物

点击「确认计划」（`/confirm-plan`）：
- 校验接口定义快照（`api_defs.json`，缺失时显式阻断）
- 生成 `dependency_map.json` + 英文翻译缓存
- 生成 `.py` 测试脚本（纯代码组装）
- 并发生成 YAML 测试数据（`test_data.yaml` / `setup_*.yaml` / `teardown_*.yaml`）
- 终态失败输出 `_generation_errors.json`，前端给出指引

---

## 项目结构

```
Generate-test-cases-and-data/
├── web_app.py                    # Web 入口（Uvicorn，输入 q 停止）
├── main.py                       # CLI 入口（已废弃，提示使用 Web）
├── config.py                     # 配置兼容层（薄包装 settings.py）
├── settings.py                   # Pydantic Settings 配置中心（调参 + 输出路径）
├── ingest_v2.py                  # Phase A 摄取兼容入口（re-export ingest/ 包 + CLI）
├── observability.py              # 结构化 JSON 日志 + trace_id
├── requirements.txt
│
├── web/                          # FastAPI Web 应用
│   ├── app.py                    # 应用工厂 + lifespan 生命周期 + 中间件
│   ├── state.py                  # 全局共享状态（任务/会话/向量库引用）
│   ├── tasks.py                  # 后台异步任务（有界线程池 + Phase B/C 编排 + 补偿 worker）
│   ├── compensation.py           # 补偿 worker 实现
│   ├── routes/                   # 路由分组
│   │   ├── files.py              # 文件上传 / 删除
│   │   ├── modules.py            # 模块树 / 术语 / 场景分析 / 接口标注
│   │   ├── docs.py               # 文档关联 / 切块 / 术语
│   │   ├── bindings.py           # 文档绑定
│   │   ├── chat.py               # /workflow/start·confirm、/confirm-plan、任务轮询
│   │   └── api_extract.py        # 接口提取 / 提交 / 重试（MD）
│   └── services/
│       └── doc_binding.py        # 文档绑定业务逻辑
│
├── agent_components/             # AI 核心组件
│   ├── nodes.py                  # LangGraph 节点方法（ChatTestAgentGraph）+ METHOD_FEATURES
│   ├── graph_builder.py          # 工作流图构建（LangGraph StateGraph）
│   ├── state.py                  # LangGraph 状态定义
│   ├── retrievers.py             # 多跳检索节点（RetrievalMixin）
│   ├── generators/               # Phase C 生成器（GenerationMixin 组合层）
│   │   ├── __init__.py           #   组合层 + re-export
│   │   ├── excel.py              #   Excel 读取 + dependency_map（ExcelMixin）
│   │   ├── translation.py        #   英文翻译 + 幂等性（TranslationMixin）
│   │   ├── py_export.py          #   pytest 脚本生成（PyExportMixin）
│   │   ├── yaml_gen.py           #   YAML 生成 + 修复循环（YamlMixin）
│   │   └── _helpers.py           #   修复循环辅助函数
│   ├── llm/
│   │   ├── base.py               # BaseCompatibleChatOpenAI
│   │   └── deepseek.py           # DeepSeekChatOpenAI 适配器
│   ├── llm_client.py             # LLM 客户端单例
│   ├── dual_chroma.py            # ChromaDB 封装（统一 doc_search 集合）
│   ├── fallback_embeddings.py    # Embedding 兜底
│   ├── api_annotations.py        # 接口标注注册表（ApiAnnotationRegistry）
│   ├── plan_validator.py         # Excel 计划校验器（ExcelPlanValidator，9 类错误聚合）
│   ├── validator.py              # Excel 文件层校验（openpyxl 读回）
│   ├── post_validator.py         # YAML 后校验（YamlPostValidator）
│   ├── axure_parser.py           # Axure 原型解析器
│   ├── prompt_builder.py         # Prompt 构建辅助
│   └── graph_logging.py          # 工作流日志累积器
│
├── ingest/                       # Phase A 文档摄取（2026-08 大文件拆分）
│   ├── extractors.py             # 文本提取（PyPDF / docx / markdown + 图片）
│   ├── chunking.py               # 切块 / 分批 / 批量摘要
│   ├── api_parser.py             # 接口提取（LLM + YApi MD 纯代码 + 白名单校验）
│   ├── pipelines.py              # 摄取流水线（product / api / axure + commit）
│   └── storage.py                # SQLite 落库 + Chroma 向量化
│
├── prompts/
│   ├── definitions.py            # PromptFactory（Prompt 文本）
│   ├── extraction_prompts.py     # 提取 / 修复 prompt
│   └── response_model.py         # Pydantic 响应模型（ExcelPlanV2 / TestData 等）
│
├── data_factory/                 # 测试数据工厂
│   ├── registry.py               # 方法注册表加载层（prompt 渲染 + 校验规则）
│   └── methods.yaml              # 数据工厂方法注册表（分类结构，单一事实源）
│
├── database/                     # SQLAlchemy 数据层
│   ├── __init__.py               # 引擎 / 会话管理 + 自动迁移 + 种子导入
│   ├── models.py                 # 数据模型（Module / Document / Binding / Glossary /
│   │                             #   ModuleAnalysis / DocumentChunk / CompensationTask）
│   ├── init_db.py                # 建表脚本
│   └── operations/               # CRUD 操作封装（按实体拆分）
│       ├── docs.py               #   DocOps
│       ├── modules.py            #   ModuleOps（树 / 路径 / 合并）
│       ├── bindings.py           #   BindingOps（去重绑定）
│       ├── glossary.py           #   GlossaryOps
│       ├── analysis.py           #   AnalysisOps（模块场景分析）
│       └── compensation.py       #   CompensationOps（补偿任务队列）
│
├── infra/
│   ├── start_ollama.bat          # Ollama 启动检查（CPU 模式）
│   └── Modelfile                 # bge-m3-cpu CPU 模式模型文件
│
├── scripts/
│   └── migrate_chroma_to_sqlite.py   # ChromaDB → SQLite 迁移脚本
│
├── static/
│   ├── app.js                    # 前端主逻辑
│   └── style.css                 # 前端样式
│
├── templates/
│   └── index.html                # Jinja2 前端页面
│
├── tests/                        # Pytest 测试套件
│   ├── test_ingest_main_flow.py      # Phase A 主流程集成
│   ├── test_phase_a_analysis.py      # Phase A 场景预分析
│   ├── test_workflow_api.py          # Phase B 工作流 API
│   ├── test_workflow_init.py         # 工作流初始化
│   ├── test_phase_bc_unit.py         # Phase B/C 单元（消解/校验/注册表/修复循环）
│   ├── test_phase_b_dedup.py         # Phase B 用例去重
│   ├── test_phase_c_api.py           # Phase C /confirm-plan API（产物质量校验）
│   ├── test_phase_c_autofix.py       # Phase C 自动修复
│   ├── test_plan_validator.py        # ExcelPlanValidator 单测
│   ├── test_new_node_evaluation.py   # 新节点评估
│   ├── test_commit_api.py / test_delete_file.py / test_doc_binding.py
│   ├── test_files_bindings_api.py / test_key_flows.py / test_llm_adapter.py
│   ├── test_ollama_raw.py / test_orphan_detection.py / test_yaml_db_export.py
│   └── test_regression_*.py          # 回归（axure/extraction/generators/import_smoke/
│                                     #   operations/post_validator）
│
├── data/                         # 运行时数据（gitignored）
│   ├── app.db                    # SQLite 数据库（事实源）
│   └── source_apis.txt
├── uploads/                      # 上传文件存储（pdf/docx/product/axure/md，gitignored）
├── vector_store/chroma_db/       # ChromaDB 向量库（统一 doc_search，gitignored）
├── logs/                         # 运行日志 + thinking_trace.log + 诊断脚本
├── docs/                         # 架构审查 / 规则库（skill 体系产物）
├── changelog/                    # 变更记录（含 Phase A/B/C 流程文档）
└── .claude/skills/               # 架构防御体系（rule-compiler / risk-detective / code-executor）
```

---

## 技术栈

| 组件 | 技术 |
|------|------|
| Web 框架 | FastAPI + Uvicorn |
| 前端 | Jinja2 + 原生 JavaScript + CSS |
| 工作流引擎 | LangGraph（StateGraph 条件中断） |
| 向量数据库 | ChromaDB（统一 `doc_search` 集合，双集合已废弃） |
| ORM | SQLAlchemy 2.0（SQLite，WAL 模式，**持久事实源**） |
| 嵌入模型 | bge-m3 (Ollama) |
| LLM | DeepSeek V4 Pro（兼容 OpenAI 协议）/ 本地模型自动降级 |
| 文档解析 | PyPDF / python-docx / BeautifulSoup / json5 |
| 数据模型 | Pydantic v2（含 model_validator 防御性校验） |
| Excel 处理 | openpyxl |
| YAML 生成 | PyYAML |
| 中文转拼音 | pypinyin（翻译缓存未命中时的文件名兜底） |
| 配置 | pydantic-settings（.env 密钥 + settings.py 调参） |
| 日志 | 结构化 JSON（ContextVar trace_id 追踪）+ thinking_trace.log 思考全文 |

---

## GPU 显存不足？

bge-m3 模型（567M 参数，F16 精度）需要约 1.1GB 显存。若 GPU 显存 < 4GB，建议强制使用 CPU 运行，避免模型卡死或超时。

```powershell
# 1. 创建 CPU-only 版本（项目根目录已包含 Modelfile）
ollama create bge-m3-cpu -f ./infra/Modelfile

# 2. 修改 .env 中的 EMBEDDING_MODEL
EMBEDDING_MODEL=bge-m3-cpu
```

CPU 模式下，bge-m3 处理短文本耗时 5-15 秒，完全满足 RAG 入库和检索的性能需求。

> `./infra/Modelfile` 已在项目中管理，内容为 `FROM bge-m3:latest` + `PARAMETER num_gpu 0`，可放心使用。

---

## 校验与修复机制

### Phase B — Excel 计划（生成/处理解耦 + 9 类错误聚合）

```
generate_plan_thinking（thinking + json_object）只生成 plan
    │  （失败 → 无 plan）
    ▼
generate_excel_plan 纯处理节点
    │
    ├── ① 数据源检测：无 plan → requires_review（不降级自生成）
    ├── ② ExcelPlanValidator 校验
    │     字段 / 前置引用 / 步骤-预期对齐 / 断言格式 / URL 有效性 / db 拦截
    │     质量门禁：首轮通过率 <50% → 全量重生成（≤2 次）
    ├── ③ 修复轮（≤3 次）：只重填失败行，拦截原因按类型聚合
    ├── ④ 引用完整性：剔除悬空前置引用
    ├── ⑤ 资源冲突消解：同一 PRE 被多个写用例引用 → 克隆隔离
    └── ⑥ 落盘：test_plan.xlsx + api_defs.json + 文件层校验（openpyxl 读回）
```

### Phase C — YAML 生成（规整/重生成两分法 + 批量自查修复循环）

```
单文件生成（thinking 分析 → json_mode 单次输出，无 inline 重试）
    │
    ▼
规整层（确定性，静默）: method 小写 / url 去域名 / header 按 CT 补全
                        / 同类断言合并 / 空 {} 字段剔除
    │
    ▼
校验层（回炉类，Pydantic）: {{}} 占位符幻觉 / 非注册表函数 / 实参不合规
                            / json·params·data 三选一 / 空列表 / 提取值非 str
                            / db 断言无 schema / is_export 用 eq 断言
    │
    ├── 通过 → 原子写盘（tmp + os.replace；路径参数/导出断言写盘前注入）
    └── 失败 → 登记占位 GEN-FAIL-R{轮}-{序}（不写盘）
              │
              ▼ 轮末
        全批次错误模式汇总 → repair prompt 思考自查 → 修复轮重生成
              │  （≤ YAML_REPAIR_ROUNDS，默认 1 轮）
              ▼
        终态仍失败 → 计 failed + _generation_errors.json（无占位假文件）

后校验（YamlPostValidator）：delete_body_wrapper / 断言动态 key / 引号配对
  → _post_validation_issues.json
拦截统计：ValidationInterceptor → logs/VALIDATION_INTERCEPT.md
```

---

## 数据存储与一致性

| 存储 | 位置 | 角色 |
|------|------|------|
| SQLite | `data/app.db` | **事实源**：documents / document_chunks / api_* / bindings / glossary / module_analysis / compensation_tasks / modules |
| ChromaDB | `vector_store/chroma_db/doc_search` | 检索索引（统一集合，双集合已废弃） |
| 输出产物 | `pycharm_misc/testcase_base/<模块路径>/` | test_plan.xlsx + api_defs.json + .py + YAML + dependency_map.json |

一致性原则：
1. **先 SQLite 后 Chroma**：文本块先落 SQLite，再从 SQLite 读回（带摘要）构造检索文本写向量库；Chroma 失败时补偿删除 SQLite 记录，避免「索引有、数据无」。
2. **补偿 worker**：LLM 摘要失败 / Chroma 重建等异步任务写入 `compensation_tasks` 表，独立线程轮询重试（默认 3 次）。
3. **文件删除延迟重试**：ChromaDB 删除失败后延迟（默认 300s）异步重试。
4. **数据真实性门禁（规则 M8）**：接口定义快照 `api_defs.json` 缺失/为空时，Phase C 显式阻断，禁止空定义盲写 YAML。
5. **历史迁移**：从旧 Chroma 双集合迁移可运行 `python scripts/migrate_chroma_to_sqlite.py`。

---

## 架构防御体系

项目按 `.claude/CLAUDE.md` 接入三套技能闭环（`/全盘扫描` → 审查 → 自动修复 → 规则归档）：
- **Skill A `rule-compiler`** — 从 `docs/fixes_summary.md` 编译规则库（RULES_INDEX / RULES_DETAIL）
- **Skill B `risk-detective`** — 全盘扫描生成 `docs/architecture_review.md`
- **Skill C `code-executor`** — 基于审查报告自动修复并归档

相关文档：`docs/fixes_summary.md`（原始架构文档 + 归档记录）、`docs/architecture_review.md`（审查报告）、`docs/RULES_*.md`（规则库）。

---

## 最新变更

**2026-08-07**

- **大文件拆分** — 四个超 500 行文件模块化重构：`ingest_v2.py` → `ingest/` 包、`generators.py` → `agent_components/generators/` 包、`database/operations.py` → `operations/` 包、Web 全局状态 → `web/state.py`；对外符号全部 re-export，既有导入零改动
- **Phase C 优化** — YAML 生成准确率持续调优；新增 `2026-08-07_phase_{a,b,c}_flow.md` 三阶段输入/输出全图
- **Phase C 接口取源方案**（`2026-08-07_phase_c_api_sourcing_plan.md`）— 接口 URL 规范化、SQLite 查询层、M8 门禁升级为整批完整性校验

**2026-08-05**

- **执行失败 26 优化** — 接口合并键 `method+url` 截断缺陷定位与治理方案

**2026-08-02/03**

- **P0 — 接口提取修复** — `extract_apis_from_yapi_md` 解析 YApi MD 的 4 个缺陷（desc HTML 残留 `/p>`、Query 参数不解析、Body-MD file 丢失、响应信封误塞参数）
- **P0 — Excel 生成/处理解耦** — `generate_plan_thinking` 只生成不落盘 → `generate_excel_plan` 纯处理节点；thinking 失败 → requires_review，不降级自生成
- **P0 — 校验收敛** — 新增 `agent_components/plan_validator.py`（ExcelPlanValidator），首轮/重试校验副本收敛，拦截原因按错误类型聚合
- **P1 — thinking 节点入参瘦身** — 喂接口概要（name/method/url/description）而非全量参数
- **P1 — 修复节点入参统一** — 共享数据 + `failed_test_cases` + `block_reasons`（拦截方法提示）

**2026-07-29/30**

- **P0 — 存储反转** — SQLite 成为持久事实源，ChromaDB 统一 `doc_search` 集合替代双集合；接口定义结构化落 SQLite（api_* 列）；提供 ChromaDB → SQLite 迁移脚本

**2026-07-24/26**

- **Phase A 入库预处理** — 批量摘要（simple_summary）、接口自然语言检索文本、Chroma 失败补偿回滚
- **YAML 后校验** — YamlPostValidator（delete_body_wrapper / 断言动态 key / 引号配对）

**2026-07-18**

- **P0 — 接口定义传递断链** — `api_defs.json` 快照随 Excel 落盘，Phase C 数据缺失显式阻断；确立「数据缺失必须显式失败」原则
- **P0 — YAML 质量治理** — 规整/重生成两分法 + 批量自查修复循环 + 占位符注册表校验 + `_generation_errors.json` 终态错误清单
- **数据工厂注册表 v2** — `methods.yaml` 重构为目录+大类结构，prompt/校验器/测试三处同源

**历史**

- **P0 — Phase C 工作流恢复断裂** — `_confirm_user_intent` 覆盖 CONFIRMED 状态已修复
- **P0 — 路径遍历漏洞** — 所有文件上传入口加 basename 清洗 + UUID 前缀
- **P0 — 向量库数据孤岛** — 废弃 ReadersChroma，统一使用 DualChromaDB
- **P0 — DeepSeek thinking 兼容性** — METHOD_FEATURES 声明式配置表 + 自动降级
- **P0 — API Key 脱敏** — 日志/序列化节点自动过滤 sk- 前缀的敏感字段
- **P1 — 两阶段节点拆分** — analyze_scenarios (thinking) → generate_excel_plan (format)
- **P1 — 线程池** — 有界 ThreadPoolExecutor 统一管理后台异步任务
- **P2 — 测试数据 Pydantic 化** — StepData/TestCase 模型，model_validator 字段漂移防御
- **P2 — Session 统一管理** — `get_session_ctx()` 上下文管理器
- **P3 — 全量代码清理** — 删除废弃方法/类、死代码、未使用导入
- **Web 模块化** — FastAPI 路由拆分到 `web/routes/`，服务逻辑抽取到 `web/services/`
- **数据库 ORM** — SQLAlchemy 模型 + 操作层封装 `database/`
