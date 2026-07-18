# 智能测试助手 (Intelligent Test Assistant)

基于 LangGraph + RAG + DeepSeek 的 AI 测试用例生成平台。上传产品文档与接口定义，AI 自动分析测试场景、设计测试用例、生成可执行测试脚本。

---

## 系统架构

```
Ingest（上传阶段）
─────────────────────────────────────────────────
产品文档 (Word/PDF/MD)
  ├── 文本提取 + 图片保存
  ├── LLM 提取模块归属 + 关联模块
  ├── LLM 提取业务术语表
  ├── 人工审核弹窗 → 确认/修改关联关系
  └── 存入 product_docs 集合

Axure HTML 原型包 (.zip)
  ├── 解压 → data/sitemap.js → 页面树
  ├── 解析 HTML → data-label + 可见文本
  ├── data/data.js → 交互流提取（触发→动作→目标）
  └── 存入 product_docs 集合

接口文档 (Word/PDF/MD)
  ├── LLM 提取接口定义（URL/method/params/returns）
  ├── 人工确认接口列表 + 指定所属模块
  └── 存入 api_defs 集合

Workflow（运行阶段）
─────────────────────────────────────────────────
用户输入测试需求
  │
  ├── Hop 1: 检索 product_docs → 找到主模块
  ├── 提取 related_modules → 关联模块列表
  ├── Hop 2a: 检索关联模块产品文档
  ├── Hop 2b: 检索关联模块 + 公共基础服务接口
  │
  ├── 测试点分析（两阶段：thinking 自由文本 → format json_mode）
  │     输出测试点列表 + 风险区域
  │
  ├── Excel 测试计划（两阶段：analyze_scenarios → generate_excel_plan）
  │     ├── 思考阶段（thinking, 自由文本分析场景）
  │     ├── 格式化阶段（function_calling + Pydantic 模型约束）
  │     ├── Pydantic 校验 ← 自动修复循环
  │     ├── 文件层校验 ← 通过 → 继续
  │     │   └── 失败 → 重试（最多 3 次）
  │     │       └── 仍失败 → 标记需人工审查
  │     └── 落盘三件套：test_plan.xlsx + translation_cache.json
  │         + api_defs.json（接口定义快照，Phase C 数据来源，缺失即阻断确认）
  │
  ├── .py 文件生成（纯代码组装：fixture + run_blocks 结构，翻译缓存优先）
  └── YAML 生成（两阶段：analyze_yaml_data thinking → format json_mode，单次输出）
        ├── 规整层（确定性修正：method 小写/url 去域名/header 补全/断言合并/空字段剔除）
        ├── 校验层（回炉类：占位符注册表/三选一/空列表/提取值类型，失败不写盘）
        └── 修复循环：失败登记占位 → 轮末错误模式汇总 → 思考自查 → 重生成
            └── 超轮次仍失败 → _generation_errors.json + 计 failed（无占位假文件）
```

---

## 功能特性

- **多格式文档上传** — PDF / Markdown / Word (.docx) / Axure HTML 演示包 (.zip)
- **智能模块关联** — LLM 自动提取文档的模块归属和跨模块依赖关系，人工审核确认
- **双集合向量库** — 产品文档与接口定义隔离存储，按模块 metadata 精确过滤
- **多跳检索（Multi-hop）** — 根据模块依赖关系自动追溯关联文档和接口定义
- **测试点分析** — 深度思考模式分析业务场景，输出测试点和风险区域
- **Excel 测试计划** — 生成含 Allure 标签、模块划分、步骤描述的标准化测试计划
- **自动校验修复** — Pydantic + 文件层双重校验 + 自动修复循环（最多 3 次）+ 人工审查兜底
- **YAML 质量治理** — 规整/重生成两分法：确定性格式修正静默执行；语义性错误（占位符幻觉/三选一冲突/空输出）登记后集中送思考节点自查重生成，终态失败输出 `_generation_errors.json`，杜绝"假成功"
- **数据真实性** — 接口定义快照 `api_defs.json` 随计划落盘，Phase C 数据缺失显式阻断，禁止假数据托底
- **数据工厂注册表** — `data_factory/methods.yaml` 单一事实源（目录+大类结构），prompt 渲染 / 占位符校验 / 单元测试三处同源
- **Python 测试脚本** — 生成 pytest + allure 测试类代码
- **YAML 测试数据** — 结构化的请求/响应测试数据
- **模块目录树** — 支持模块的增删改查、重命名级联更新向量库
- **业务术语表** — LLM 提取产品文档术语，减少字段名/状态值幻觉
- **Web + CLI 双模式** — FastAPI Web 界面 + 命令行交互式 REPL
- **结构化日志** — JSON 格式日志 + ContextVar trace_id 全链路追踪

---

## 节点与模型策略

| 节点 | method | thinking | Pydantic 模型 |
|------|--------|----------|------|
| 产品文档解析 | json_mode | ❌ | DocModuleExtract |
| 接口提取 | json_mode | ❌ | ApiDefExtract / ApiDefinitionList |
| 分析场景（thinking） | free_text | ✅ | 无（自由文本） |
| 测试点分析（thinking） | free_text | ✅ | 无（自由文本） |
| 格式化测试点 | json_mode | ❌ | TestPointList |
| 格式化 Excel 计划 | function_calling | ❌ | ExcelPlan |
| 英文翻译（缓存未命中时） | json_mode | ❌ | TranslationResult |
| YAML 数据分析 / 修复轮自查 | free_text | ✅ | 无（自由文本，全文落 thinking_trace.log） |
| YAML 格式化（单次，无 inline 重试） | json_mode | ❌ | TestData（占位符/三选一/空列表校验内置） |
| .py 生成 | 纯代码组装 | — | —（不经 LLM） |

> DeepSeek V4 的 thinking 控制通过声明式 `METHOD_FEATURES` 配置表管理：
> - `METHOD_FEATURES = {"function_calling": {"supports_thinking": False}, ...}`
> - `function_calling` / `json_mode` / `json_schema` 均不支持 thinking
> - `free_text` 支持 thinking（分析节点）
> - 未知 method 自动禁用 thinking 并记日志警告

---

## 快速开始

### 前置条件

- Python 3.10+
- [Ollama](https://ollama.ai/)（本地嵌入模型，必需）
- 安装嵌入模型：`ollama pull bge-m3`
- 启动前运行 `.\infra\start_ollama.bat` 自动检测并启动 Ollama
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

在项目根目录创建 `.env` 文件：

```env
# ========== Embedding 模型（Ollama 必需） ==========
EMBEDDING_MODEL=bge-m3
EMBEDDING_URL=http://localhost:11434

# ========== DeepSeek API（推荐） ==========
DEEP_URL=https://api.deepseek.com
DEEP_API_KEY=sk-your-key-here
DEEP_MODEL=deepseek-v4-pro

# ========== 本地 LLM（DeepSeek 未配时自动降级） ==========
# LLM_MODEL=qwen2.5:14b
# LANGCHAIN_URL=http://localhost:11434/v1

# ========== 深度思考控制 ==========
ENABLE_THINKING=true

# ========== 可选调优 ==========
# UPLOAD_MAX_SIZE_MB=100
# TASK_MAX_WORKERS=10
# TASK_MAX_QUEUE=30
# WORKFLOW_SESSION_TTL=1800
# TESTCASE_BASE=./testcase_out
# LOG_LEVEL=INFO
```

### 启动

```bash
python web_app.py
```

访问 `http://localhost:8000`。

---

## 使用指南

### 1. 上传文档

支持四种类型：
- 📄 **PDF** — API 文档、产品说明
- 📝 **Markdown** — 接口文档
- 📃 **Word (.docx)** — 产品需求文档（含图片自动提取）
- 🎨 **Axure (.zip)** — HTML 原型演示包（自动解析页面树 + 交互流）

上传后自动弹出模块审核弹窗，可修改模块名称和关联模块。

### 2. 管理模块

在「模块管理」面板创建/重命名/删除模块目录。
重命名会自动级联更新向量库中所有 chunks 的 metadata。

### 3. 输入测试需求

```
分析合同管理功能，生成功能测试用例
测试车辆入场后查询在场记录的功能
```

### 4. 查看结果

- 测试点列表 + 风险区域
- Excel 测试计划（校验失败时自动修复或标记审查）
- 确认后生成 `.py` 和 `.yaml` 文件

---

## 项目结构

```
Generate-test-cases-and-data/
├── config.py                       # 配置兼容层（代理 settings.py）
├── settings.py                     # Pydantic Settings 配置中心
├── web_app.py                      # Web 服务入口（Uvicorn）
├── main.py                         # CLI 交互式入口
├── ingest_v2.py                    # Phase A 智能摄取入口
├── observability.py                # 结构化 JSON 日志
├── requirements.txt
│
├── web/                            # FastAPI Web 应用包
│   ├── app.py                      # 应用工厂 + 生命周期管理
│   ├── tasks.py                    # 后台异步任务线程池
│   ├── routes/
│   │   ├── api_extract.py          # 接口提取 API
│   │   ├── bindings.py             # 文档关联绑定
│   │   ├── chat.py                 # 对话 / 工作流
│   │   ├── docs.py                 # 文档管理
│   │   ├── files.py                # 文件上传 / 删除
│   │   └── modules.py              # 模块树管理
│   └── services/
│       └── doc_binding.py          # 文档绑定业务逻辑
│
├── agent_components/               # AI 代理核心组件
│   ├── llm/
│   │   ├── base.py                 # BaseCompatibleChatOpenAI
│   │   └── deepseek.py             # DeepSeekChatOpenAI 适配器
│   ├── nodes.py                    # LangGraph 节点方法
│   ├── graph_builder.py            # 工作流图构建
│   ├── state.py                    # 状态定义
│   ├── dual_chroma.py              # DualChromaDB 双集合封装
│   ├── module_tree.py              # 模块目录树管理
│   ├── validator.py                # 只读校验节点
│   ├── axure_parser.py             # Axure 原型解析器
│   ├── generators.py               # PY/YAML 生成节点
│   └── retrievers.py               # 多跳检索节点
│
├── database/                       # SQLAlchemy ORM 层
│   ├── models.py                   # 数据模型定义
│   ├── operations.py               # CRUD 操作封装
│   └── init_db.py                  # 数据库初始化脚本
│
├── prompts/
│   ├── definitions.py              # PromptFactory
│   ├── extraction_prompts.py       # 提取/修复 prompt
│   └── response_model.py           # Pydantic 响应模型
│
├── data_factory/                   # 测试数据工厂
│   ├── registry.py                 # 方法注册表加载层（prompt 渲染 + 校验规则）
│   └── methods.yaml                # 数据工厂方法注册表（分类结构，单一事实源）
│
├── static/
│   ├── app.js                      # 前端主逻辑
│   └── style.css                   # 前端样式
│
├── templates/
│   └── index.html                  # Jinja2 前端页面
│
├── tests/                          # Pytest 测试套件
│   ├── conftest.py                 # 共享 fixtures
│   ├── test_ingest_main_flow.py    # 主摄取流程集成测试
│   ├── test_workflow_api.py        # Phase B 工作流 API 测试
│   ├── test_workflow_init.py       # 工作流初始化测试
│   ├── test_phase_bc_unit.py       # Phase B/C 单元测试（消解/校验/注册表/修复循环）
│   ├── test_phase_c_api.py         # Phase C /confirm-plan API 集成测试（产物质量校验）
│   ├── test_commit_api.py          # 提交 API 测试
│   ├── test_delete_file.py         # 文件删除测试
│   ├── test_doc_binding.py         # 文档绑定测试
│   ├── test_key_flows.py           # 关键流程集成测试
│   ├── test_phase_a_flow.py        # Phase A 完整流程测试
│   └── test_llm_adapter.py         # LLM 适配器单元测试
│
├── uploads/                        # 上传文件存储（gitignored）
├── data/                           # 运行时数据（gitignored）
│   └── modules.json                # 模块树持久化
├── testcase/                       # 生成产物输出（PYCHARM_MISC/PYTEST_DATA_DIR 解析，gitignored）
└── vector_store/                   # ChromaDB 向量库（gitignored）
```

---

## 技术栈

| 组件 | 技术 |
|------|------|
| Web 框架 | FastAPI + Uvicorn |
| 前端 | Jinja2 + 原生 JavaScript + CSS |
| 工作流引擎 | LangGraph |
| 向量数据库 | ChromaDB（双集合隔离：product_docs / api_defs） |
| ORM | SQLAlchemy（SQLite） |
| 嵌入模型 | bge-m3 (Ollama) |
| LLM | DeepSeek V4 Pro（兼容 OpenAI 协议） |
| 文档解析 | PyPDF / python-docx / BeautifulSoup / json5 |
| 数据模型 | Pydantic v2（含 model_validator 防御性校验） |
| Excel 处理 | openpyxl |
| YAML 生成 | PyYAML |
| 配置 | pydantic-settings (.env) |
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

**Phase B — Excel 计划**

```
生成节点 → Excel 文件
    │
    ▼
Pydantic 层校验    ←     自动修复循环
    │                       │
    ├── 通过 ─────────────→ 写入 Excel
    │                       │
    ▼                       ▼
文件层校验 ────────────→ 通过 → 返回
(openpyxl 读回检查)       │
    │                    失败 → 打包错误上下文
    │                           │
    ▼                           ▼
  重试（最多 3 次）←─── LLM 修复生成
    │
    └── 仍失败 → 标记 requires_review → 前端展示错误
```

**Phase C — YAML 生成（规整/重生成两分法 + 批量自查修复循环）**

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
    │
    ├── 通过 → 原子写盘
    └── 失败 → 登记占位 GEN-FAIL-R{轮}-{序}（不写盘）
              │
              ▼ 轮末
        全批次错误模式汇总 → repair prompt 思考自查 → 修复轮重生成
              │  （≤ YAML_REPAIR_ROUNDS，默认 1 轮）
              ▼
        终态仍失败 → 计 failed + _generation_errors.json
                     + thinking_trace.log 标记 generate_yaml_FAILED（无占位假文件）
```

---

## 最新变更

**2026-07-18**

- **P0 — 接口定义传递断链** — `api_defs.json` 快照随 Excel 落盘，Phase C 数据缺失显式阻断；删除假数据托底文件，确立"数据缺失必须显式失败"原则
- **P0 — YAML 质量治理** — 规整/重生成两分法 + 批量自查修复循环 + 占位符注册表校验 + `_generation_errors.json` 终态错误清单
- **数据工厂注册表 v2** — `methods.yaml` 重构为目录+大类结构（6 方法含 `get_offset_time`），`data_factory/registry.py` 加载层，prompt/校验器/测试三处同源
- **Phase C 日志补全** — 思考全文、轮次汇总、失败标记全量写入 `thinking_trace.log`，与 Phase B 同规格

**历史**

- **P0 — Phase C 工作流恢复断裂** — `_confirm_user_intent` 覆盖 CONFIRMED 状态已修复
- **P0 — 路径遍历漏洞** — 所有文件上传入口加 basename 清洗 + UUID 前缀
- **P0 — 向量库数据孤岛** — 废弃 ReadersChroma，统一使用 DualChromaDB
- **P0 — DeepSeek thinking 兼容性** — METHOD_FEATURES 声明式配置表 + 自动降级
- **P0 — API Key 脱敏** — 日志/序列化节点自动过滤 sk- 前缀的敏感字段
- **P1 — 两阶段节点拆分** — analyze_scenarios (thinking) → generate_excel_plan (format)
- **P1 — 线程池** — ThreadPoolExecutor 统一管理后台异步任务
- **P2 — 测试数据 Pydantic 化** — StepData/TestCase 模型，model_validator 字段漂移防御
- **P2 — Session 统一管理** — `get_session_ctx()` 上下文管理器，22 处调用点迁移
- **P3 — 全量代码清理** — 删除废弃方法/类、死代码、未使用导入
- **Web 模块化** — FastAPI 路由拆分到 `web/routes/`，服务逻辑抽取到 `web/services/`
- **数据库 ORM** — SQLAlchemy 模型 + 操作层封装 `database/`
