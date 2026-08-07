# Phase A 流程 — 输入/输出来源全图

| 项目 | 内容 |
|:---|:---|
| 日期 | 2026-08-07 |
| 阶段定位 | 文档摄取与向量化（Ingest）：产品文档 / Axure 原型 / 接口文档 → 结构化入库，供 Phase B 检索 |
| 入口 | `web/routes/files.py:upload_file`、`web/routes/api_extract.py` |
| 核心文件 | `ingest_v2.py`、`agent_components/axure_parser.py`、`agent_components/dual_chroma.py`、`database/operations.py` |
| 数据落点 | SQLite（`documents` / `document_chunks` / `api_*`）+ ChromaDB（统一 `doc_search` 集合） |

---

## 〇、总览图

```
                    ┌─────────────────────────────────────────────────────────┐
                    │                      用户 / 前端                          │
                    │   上传文件 + 人工审核（模块归属 / 接口列表 / 术语 / 绑定）   │
                    └───────────────┬─────────────────────────┬───────────────┘
                                    │                         │
                ┌───────────────────┘            ┌────────────┘
                ▼                                ▼
       ┌──────────────────┐          ┌───────────────────────┐
       │ 产品文档 MD/PDF/  │          │  接口文档 MD/PDF/DOCX  │
       │ Word(.docx)      │          │  或 YApi 导出 MD       │
       └────────┬─────────┘          └───────────┬───────────┘
                │                                │
       ┌────────▼─────────┐          ┌───────────▼───────────┐
       │ ① 文本提取        │          │ ① 文本提取 + 按 header │
       │    _extract_text  │          │    分批 _split_text_  │
       └────────┬─────────┘          │    by_headers         │
                │                    └───────────┬───────────┘
       ┌────────▼─────────┐          ┌───────────▼───────────┐
       │ ② 切块 Recursive  │          │ ② 接口提取             │
       │    TextSplitter   │          │    LLM: api_def_extract│
       └────────┬─────────┘          │    → ApiDefExtract     │
                │                    │    纯代码: extract_apis│
                │                    │    _from_yapi_md       │
                │                    └───────────┬───────────┘
                │                                │
                │                    ┌───────────▼───────────┐
                │                    │ ③ 白名单校验           │
                │                    │    _extract_valid_api_ │
                │                    │    paths 过滤幻觉接口   │
                │                    └───────────┬───────────┘
                │                                │
       ┌────────▼─────────┐          ┌───────────▼───────────┐
       │ ③ LLM 模块归属     │          │ ④ 前端人工确认         │
       │    DocModuleExtract│          │    接口列表+所属模块   │
       │ ④ LLM 业务术语表   │          └───────────┬───────────┘
       │    GlossaryExtract │                      │
       └────────┬─────────┘          ┌───────────▼───────────┐
                │                    │ ⑤ commit_api_docs      │
                │ 人工审核弹窗       │    合并去重+annotations │
                ▼                    └───────────┬───────────┘
       ┌──────────────────────────────────────────┐
       │              SQLite 持久化                │
       │  documents 元数据 / document_chunks 原文  │
       │  + api_* 结构化列 / glossary_terms        │
       └──────────────────┬───────────────────────┘
                          ▼
       ┌──────────────────────────────────────────┐
       │            ChromaDB 向量化（doc_search）  │
       │  product/axure: _build_doc_search_text   │
       │  api: _build_api_search_text(自然语言)    │
       │  失败 → 补偿回滚 SQLite                   │
       └──────────────────────────────────────────┘
```

Axure 原型包（`.zip`）独立分支：

```
┌──────────────────┐
│ Axure .zip       │
└────────┬─────────┘
         ▼
┌───────────────────────────────┐
│ AxureParser.parse()           │
│  ① sitemap.js → 页面树         │
│  ② 各页面 HTML → data-label +  │
│     可见文本（_clean_html）      │
│  ③ data/data.js → 交互流       │
│     （触发→动作→目标）           │
└────────┬──────────────────────┘
         ▼
┌───────────────────────────────┐
│ to_product_doc_chunks()        │
│  → list[{content, page_name}]  │
└────────┬──────────────────────┘
         ▼
┌───────────────────────────────┐
│ LLM 关联模块（≤50 页截断）       │
│  product_doc_extract_prompt   │
└────────┬──────────────────────┘
         ▼  → 与产品文档相同 → SQLite + ChromaDB
```

---

## 一、三类输入 → 输出对照表

| # | 输入 | 来源 | 处理节点 | 输出 | 去向 |
|:--|:---|:---|:---|:---|:---|
| A1 | 产品文档 `file_path`（.md/.pdf/.docx） | 前端上传 | `process_product_doc` | `doc_id` / `module_name` / `related_modules` / `chunks` | 返回前端 + 落 SQLite/ChromaDB |
| A2 | Axure 原型包 `.zip` | 前端上传 | `process_axure_zip` | 同上（doc_type=`axure`） | 同上 |
| A3 | 接口文档 `file_path` | 前端上传 | `process_api_doc_extract` | `module_name` / `apis`（待确认） | 仅返回前端，**不入库** |
| A4 | 用户确认的接口列表 + 模块名 | 前端 `/commit-api` | `commit_api_docs` | `apis` 结构化入库 | SQLite + ChromaDB，可选删原文件 |

### A1 产品文档子流程 I/O

| 步骤 | 输入 | 来源 | 输出 | 去向 |
|:--|:---|:---|:---|:---|
| 文本提取 | 文件内容 | `_extract_text`（PyPDF / python-docx / markdown） | `full_text`；.docx 附带图片目录 | 内存（临时目录 finally 清理） |
| 切块 | `full_text` | `RecursiveCharacterTextSplitter`（`CHUNK_SIZE=1000` / `CHUNK_OVERLAP=200`） | `chunks[]` | 后续 LLM 提取与 ChromaDB **共用同一批块** |
| 分批 | `chunks` | `_group_chunks_into_batches`（≤ `MAX_INGEST_CHARS_PER_BATCH=30000`） | `text_batches[]` | LLM 提取 |
| LLM 模块归属 | `text_batches` + `product_doc_extract_prompt` | `_invoke_structured(json_mode)` | `DocModuleExtract{module_name, related_modules, business_summary, tags}` | 多批合并（module 取首个，related/tags 并集） |
| LLM 术语表 | `text_batches` + `glossary_extract_prompt` | `_invoke_structured(json_mode)` | `GlossaryExtract.terms[]` | 多批按 term 去重合并；失败跳过（非致命） |
| SQLite 写入 | `doc_id` / `chunks` / 元数据 | `_save_to_sqlite` + `_save_document_chunks` | `documents` 行 + `document_chunks` 行 | SQLite |
| 批量摘要 | `chunks` + `batch_chunk_summary_prompt` | `_generate_batch_summaries`（5 块/批） | `simple_summary` | 写回 `document_chunks` |
| ChromaDB 写入 | `document_chunks`（摘要优先） | `_build_doc_search_text` → `db.add_product_doc_chunks` | 向量块 | ChromaDB `doc_search` 集合；**失败 → 补偿回滚 SQLite** |

### A3/A4 接口文档子流程 I/O

| 步骤 | 输入 | 来源 | 输出 | 去向 |
|:--|:---|:---|:---|:---|
| 文本提取 | 接口文档 | `_extract_text` | `full_text` | 内存 |
| 分批 | `full_text` | `_split_text_by_headers`（≤30000 字符/批） | `batches[]` | 并发提取 |
| LLM 提取 | `batches` + `api_def_extract_prompt` | `_invoke_structured` → `ApiDefExtract`（5 线程并发） | `apis[]`（含 headers/parameters/returns + required） | 内存合并 |
| 纯代码提取 | YApi 导出 MD | `extract_apis_from_yapi_md`（正则 + BeautifulSoup 解析 HTML 表格） | `apis[]` | 复用同一下游 |
| 白名单校验 | `full_text` 扫描 `**Path：**`/`**Method：**` | `_extract_valid_api_paths` | 过滤 LLM 幻觉接口 | 过滤后 `apis` |
| 合并去重 | `apis` | `_merge_api_defs`（`method+url` 键） | 去重后 `apis` | 返回前端 |
| 前端确认 | 接口列表 + 模块名 | 用户勾选/指定 | 确认集 | `commit_api_docs` |
| 结构化入库 | 确认集 | `commit_api_docs`（同一事务） | `documents` + `api_*` 结构化列 | SQLite |
| 检索文本构建 | `api` 定义 | `_build_api_search_text`（自然语言：method/url/name/参数/返回值/annotations 标签） | 向量文本 | ChromaDB `doc_search` |
| 异常标注 | `api` dict | `ApiAnnotationRegistry.apply_all`（`is_export` / `has_path_params` 自动检测） | `annotations` | 随 api_defs 存库，Phase C 消费 |
| 补偿 | ChromaDB 任一条失败 | — | 回滚全部 SQLite 记录 | 删除 |

---

## 二、关键设计约束

1. **SQLite 为持久事实源，ChromaDB 为检索索引**：文本块先落 SQLite，再从 SQLite 读回（带摘要）构造检索文本写向量库；ChromaDB 失败时补偿删除 SQLite 记录，避免"索引有、数据无"。
2. **人工审核是 Phase A 的必经关卡**：产品模块归属（弹窗确认/修改关联）、接口列表（确认 + 指定模块）、术语表——LLM 只做初稿。
3. **检索文本非原文**：产品块用 `analyzed_summary > simple_summary > content 前 500 字`；接口用自然语言摘要（参数上限 20、返回值上限 10），控制向量库体积与检索噪声。
4. **接口合并键为 `method+url`**：语义相同但 url 截断不同的接口可能被合并（已知缺陷，见 `2026-08-05_execution_failure_26_optimization.md` 1.1）。
