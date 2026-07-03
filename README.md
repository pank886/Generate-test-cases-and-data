# 智能测试助手 (Intelligent Test Assistant)

基于 LangGraph + RAG 的 AI 测试用例生成工具。上传 API 接口文档（PDF/Markdown），用自然语言描述测试需求，自动生成 **Excel 测试计划**、**Python 测试脚本** 和 **YAML 测试数据文件**。

---

## 功能特性

- **📄 多格式文档支持** — 上传 PDF 或 Markdown 格式的 API 接口文档
- **🧠 RAG 向量检索** — 使用 ChromaDB + Ollama Embedding 构建知识库，确保 LLM 生成内容基于文档
- **💬 自然语言交互** — 描述测试需求，AI 自动分析接口并设计测试场景
- **📊 Excel 测试计划** — 生成含 Allure 标签、模块划分、步骤描述的标准化测试计划
- **🐍 Python 测试脚本** — 生成 pytest + allure 测试类代码，开箱即用
- **📁 YAML 测试数据** — 为每个接口步骤生成结构化的请求/响应测试数据
- **🖥️ 双模式运行** — Web 界面（完整流程）和 CLI 命令行（快速体验）
- **☁️ 多 LLM 支持** — 支持本地 Ollama 模型或云端 DeepSeek API

---

## 工作流程

```
用户上传 API 文档 (PDF/MD)
        │
        ▼
  ┌─ 文档向量化 ──┐
  │ ChromaDB +    │
  │ bge-m3 嵌入   │
  └───────┬───────┘
          │
  ┌─ 用户输入测试需求 ──┐
  │ "测试入场出场流程"  │
  └───────┬───────┘
          │
  ┌─ LangGraph 三步骤 ──────────┐
  │                             │
  │ ① retrieve ── 检索相关文档块  │
  │ ② parse_api ─ LLM 提取接口   │
  │ ③ generate_excel_plan ─      │
  │   生成测试计划               │
  └──────────┬──────────────────┘
             │
  ┌─ 用户确认计划 ──┐
  │ 查看/编辑 Excel │
  └───────┬───────┘
          │
  ┌─ 自动生成 ────────────┐
  │ • test_*.py 测试脚本  │
  │ • *.yaml 测试数据文件  │
  └──────────────────────┘
```

---

## 快速开始

### 前置条件

- Python 3.10+
- [Ollama](https://ollama.ai/)（本地嵌入模型，必需）
- 安装嵌入模型：
  ```bash
  ollama pull bge-m3
  ```
- LLM：本地 Ollama 模型 或 DeepSeek API

### 安装

```bash
# 1. 克隆项目
cd Generate-test-cases-and-data

# 2. 创建虚拟环境
python -m venv .venv
# Windows
.venv\Scripts\activate
# Linux/Mac
source .venv/bin/activate

# 3. 安装依赖
pip install -r requirements.txt
```

### 配置

在项目根目录创建 `.env` 文件：

```env
# ========== 本地 LLM (Ollama) ==========
LANGCHAIN_URL=http://localhost:11434/v1
LLM_MODEL=qwen2.5:7b          # 或 deepseek-r1:7b / qwen2.5-coder:7b 等
LLM_API_KEY=anything
LLM_TEMPERATURE=0.7

# ========== Embedding 模型（Ollama 必需） ==========
EMBEDDING_MODEL=bge-m3        # 已拉取的模型名
EMBEDDING_URL=http://localhost:11434

# ========== 云端 LLM（可选，配置后将覆盖本地 LLM） ==========
DEEP_URL=https://api.deepseek.com
DEEP_API_KEY=sk-your-key-here
DEEP_MODEL=deepseek-chat

# ========== 输出路径（可选） ==========
PYCHARM_MISC=C:\Users\用户名\PycharmMiscProject
TESTCASE_BASE=C:\Users\用户名\PycharmMiscProject\testcase
```

### 启动

```bash
python web_app.py
```

访问 `http://localhost:8000`，输入 `q` 停止服务。

---

## 使用指南

### 1. 上传 API 文档

- 支持 **PDF** 和 **Markdown** 格式
- 点击「上传并构建向量库」，系统自动解析文档并存入向量数据库
- 已上传的文件会显示在「已导入文件」列表中，可随时删除

### 2. 输入测试需求

在聊天框输入测试需求，例如：

```
测试车辆入场后查询在场记录的功能
测试包月车从添加到删除的完整流程
测试白名单/黑名单的增删查功能
```

### 3. 查看测试计划

AI 会分析文档中的接口，生成 Excel 测试计划。每条用例包含：

| 字段 | 说明 |
|------|------|
| 项目名称 | 自动从需求中提取 |
| Allure Epic/Feature/Story | 分层标签 |
| 模块名称 | 测试类名，如 `TestVehicleAccess_001` |
| 用例名称 | 测试方法名，如 `test_VehicleAccess_001` |
| 前置条件 | 场景级前置条件 |
| 执行步骤 | 各步骤描述，分号分隔 |
| 测试数据YAML | 对应的 YAML 数据文件名 |
| 是否启用 | Y/N |

### 4. 确认生成

- 点击「打开编辑」可在 Excel 中微调
- 点击「确认并继续」生成 `.py` 测试脚本和 `.yaml` 数据文件

### 5. 输出目录结构

```
{TESTCASE_BASE}/{ProjectName}/
├── test_plan.xlsx              # Excel 测试计划
├── test_{ProjectName}.py       # pytest 测试脚本
├── {ModuleSubdir1}/            # 模块1 的 YAML 数据
│   ├── apiName_001.yaml
│   └── apiName_002.yaml
└── {ModuleSubdir2}/            # 模块2 的 YAML 数据
    └── apiName_001.yaml
```

---

## CLI 模式

```bash
# 直接运行对话
python main.py

# 单独向量化文档
python ingest_file.py uploads/md/post.md
python ingest_file.py uploads/pdf/api-doc.pdf
```

---

## 项目结构

```
Generate-test-cases-and-data/
├── .env                         # 环境变量配置
├── config.py                    # 统一配置读取
├── web_app.py                   # Web 服务入口 (FastAPI)
├── main.py                      # CLI 入口
├── ingest_file.py               # 文件摄取入口（注册式分发）
├── requirements.txt             # Python 依赖
│
├── agent_components/            # 核心逻辑
│   ├── chromadb_file.py         # ChromaDB 客户端、文档切分、向量检索
│   ├── state.py                 # LangGraph 状态定义
│   ├── graph_builder.py         # LangGraph 图构建
│   └── nodes.py                 # 所有节点方法（检索/提取/生成）
│
├── prompts/                     # 提示词工程
│   ├── definitions.py           # PromptFactory（所有提示词模板）
│   └── response_model.py        # Pydantic 数据模型
│
├── templates/
│   └── index.html               # 前端页面 (Jinja2)
│
├── data_factory/                # 数据工厂方法注册表
│   └── methods.yaml             #   ${} 模板方法配置（新增方法改此文件）
│
├── uploads/                     # 上传文件存储（自动创建）
│   ├── pdf/
│   └── md/
│
├── vector_store/
│   └── chroma_db/               # 向量数据库（自动创建）
```

---

## 技术栈

| 组件 | 技术 |
|------|------|
| Web 框架 | FastAPI + Uvicorn |
| 前端 | Jinja2 + 原生 JavaScript |
| 工作流引擎 | LangGraph |
| 向量数据库 | ChromaDB |
| 嵌入模型 | bge-m3 (Ollama) |
| LLM | Ollama 本地模型 / DeepSeek API |
| 文档解析 | PyPDF、自定义 Markdown 切分 |
| 数据模型 | Pydantic |
| Excel 处理 | openpyxl |
| YAML 生成 | PyYAML |

---

## 常见问题

### 向量化失败: "input length exceeds context length"

嵌入模型有 token 上限（如旧版 m3e-base 最大 512 tokens）。推荐使用 `bge-m3`（最大 8192 tokens）。

### "Collection expecting embedding with dimension of 768, got 1024"

更换了嵌入模型后，旧向量库维度不匹配。删除 `./vector_store/chroma_db` 目录重新向量化，或在 `.env` 中指定新的 `CHROMA_COLLECTION` 名称。

### 提取的接口数量少于文档实际接口

默认检索数量 `k=50` 已覆盖绝大多数场景。如果文档接口数量超过 50，可在 `nodes.py` 的 `_retrieve_node` 方法中增大 `k` 值。

### 生成的用例数量少

当前设计按**业务场景**组合接口（一条用例覆盖多个接口步骤）。如需更细粒度的单接口测试，可调整提示词策略。

---

## License

MIT
