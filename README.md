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
  ├── 测试点分析（thinking + json_mode）
  │     输出测试点列表 + 风险区域
  │
  ├── Excel 测试计划（thinking + json_mode）
  │     ├── Pydantic 校验 ← 自动修复循环 ← 校验节点
  │     └── 文件层校验 ← 通过 → 继续
  │         └── 失败 → 重试（最多 3 次）
  │             └── 仍失败 → 标记需人工审查
  │
  ├── 场景数据规划（thinking + json_mode）
  │     分析数据依赖、提取规则、断言策略
  │
  ├── YAML 填充（json_mode, 无 thinking）
  └── .py 文件生成（json_mode, 无 thinking）
```

---

## 功能特性

- **多格式文档上传** — PDF / Markdown / Word (.docx) / Axure HTML 演示包 (.zip)
- **智能模块关联** — LLM 自动提取文档的模块归属和跨模块依赖关系，人工审核确认
- **双集合向量库** — 产品文档与接口定义隔离存储，按模块 metadata 精确过滤
- **多跳检索（Multi-hop）** — 根据模块依赖关系自动追溯关联文档和接口定义
- **测试点分析** — 深度思考模式分析业务场景，输出测试点和风险区域
- **Excel 测试计划** — 生成含 Allure 标签、模块划分、步骤描述的标准化测试计划
- **自动校验修复** — 文件层校验 + 自动修复循环 + 人工审查兜底
- **Python 测试脚本** — 生成 pytest + allure 测试类代码
- **YAML 测试数据** — 结构化的请求/响应测试数据
- **模块目录树** — 支持模块的增删改查、重命名级联更新向量库
- **业务术语表** — LLM 提取产品文档术语，减少字段名/状态值幻觉

---

## 节点与模型策略

| 节点 | method | thinking | 用途 |
|------|--------|----------|------|
| 产品文档解析 | json_mode | — | 输入解析 |
| 测试点分析 | json_mode | ✅ | 跨模块推理 |
| 场景数据规划 | json_mode | ✅ | 数据依赖链推理 |
| Excel 计划 | json_mode | ✅ | 测试场景设计 |
| YAML 填充 | json_mode | — | 机械填入 |
| .py 生成 | json_mode | — | 代码生成 |

> DeepSeek V4 的 thinking 模式通过 `extra_body` 显式控制：
> - 需要思考 → `extra_body={"thinking": {"type": "enabled"}}`
> - 需要 function_calling → `extra_body={"thinking": {"type": "disabled"}}`

---

## 快速开始

### 前置条件

- Python 3.10+
- [Ollama](https://ollama.ai/)（本地嵌入模型，必需）
- 安装嵌入模型：`ollama pull bge-m3`
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

# ========== DeepSeek API ==========
DEEP_URL=https://api.deepseek.com
DEEP_API_KEY=sk-your-key-here
DEEP_MODEL=deepseek-v4-pro

# ========== 深度思考控制 ==========
ENABLE_THINKING=true

# ========== 输出路径 ==========
TESTCASE_BASE=./testcase_out
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
├── config.py                       # 配置管理
├── web_app.py                      # Web 服务 (FastAPI)
├── ingest_file.py                  # 旧版文件摄取
├── ingest_v2.py                    # Phase A 智能摄取入口
├── requirements.txt
│
├── agent_components/
│   ├── llm/
│   │   ├── base.py                 # BaseCompatibleChatOpenAI
│   │   └── deepseek.py             # DeepSeekChatOpenAI 适配器
│   ├── nodes.py                    # LangGraph 节点方法
│   ├── graph_builder.py            # 工作流图构建
│   ├── state.py                    # 状态定义
│   ├── dual_chroma.py             # DualChromaDB 双集合封装
│   ├── module_tree.py             # 模块目录树管理
│   ├── validator.py               # 只读校验节点
│   ├── axure_parser.py            # Axure 原型解析器
│   ├── chromadb_file.py           # 旧版 ChromaDB 客户端
│   └── ...
│
├── prompts/
│   ├── definitions.py             # PromptFactory
│   ├── extraction_prompts.py      # 提取/修复 prompt
│   └── response_model.py          # Pydantic 模型
│
├── templates/
│   └── index.html                 # 前端页面
│
├── tests/
│   └── test_llm_adapter.py        # 适配器单元测试
│
├── uploads/                        # 上传文件存储
│   ├── pdf/
│   ├── md/
│   ├── docx/
│   └── axure/
│
└── data/
    └── modules.json                # 模块树数据
```

---

## 技术栈

| 组件 | 技术 |
|------|------|
| Web 框架 | FastAPI + Uvicorn |
| 前端 | Jinja2 + 原生 JavaScript |
| 工作流引擎 | LangGraph |
| 向量数据库 | ChromaDB（双集合隔离） |
| 嵌入模型 | bge-m3 (Ollama) |
| LLM | DeepSeek V4 Pro |
| 文档解析 | PyPDF / python-docx / BeautifulSoup |
| 数据模型 | Pydantic |
| Excel 处理 | openpyxl |
| YAML 生成 | PyYAML |

---

## 校验与修复机制

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

---

## 最新变更

- **Phase C** — 多跳检索 + 测试点分析 + Excel 计划生成（thinking 模式）
- **Phase A** — 双集合存储、LLM 提取模块关联、Axure 解析、Word 支持、业务术语表
- **Phase B** — 前端审核弹窗、模块目录树、降级警告、时序记忆
- **适配器** — DeepSeekChatOpenAI 子类，tool_calls 归一化，显式 thinking 控制
- **校验节点** — 纯 Python 文件层校验 + 自动修复循环 + 人工审查兜底
