# 存储架构翻转：SQLite 存正文，ChromaDB 退化为纯检索引擎

| 项目 | 内容 |
|:---|:---|
| 讨论日期 | 2026-07-29（补充讨论 2026-07-30、2026-07-31） |
| 变更类型 | 架构重构 — 数据存储层翻转 |
| 涉及文件 | `database/models.py`, `ingest_v2.py`, `agent_components/dual_chroma.py`, `web/routes/modules.py`, `web/routes/docs.py`, `prompts/response_model.py`, `agent_components/generators/__init__.py`, `agent_components/retrievers.py`, `web/tasks.py`, `prompts/extraction_prompts.py`, `settings.py` |

---

## 一、现状 vs 目标

### 1.1 现状（问题架构）

```
SQLite documents 表:
  id, file_name, file_type, doc_type, upload_time, status, chunk_count
  ← 只存元数据，零正文

ChromaDB product_docs 集合:
  page_content: 产品文档切块原文
  metadata: {doc_id, chunk_index}
  ← 产品/Axure 的正文全在这

ChromaDB api_defs 集合:
  page_content: JSON.stringify({name, url, method, description, parameters, returns, annotations})
  metadata: {doc_id, api_name, chunk_index, type: "api_def"}
  ← API 定义的正文全在这
```

**问题**：
- 三种文档类型的正文**全部**在 ChromaDB，SQLite 是空壳
- ChromaDB 挂了数据全丢，无法从 SQLite 恢复
- 编辑接口要走 `json.loads` + 改 JSON + 重新序列化 + 回写 ChromaDB，脆弱
- 产品/Axure 切块原文无法直接查询，前端"查看详情"还要调 ChromaDB
- 语义检索搜的是 JSON/原文，结构字符干扰向量质量

### 1.2 目标（工业标准架构 + 三阶段数据流）

三阶段数据生命周期：

```
阶段 1: 原文入库（上传即入库，不分析）
  ┌─────────────────────────────────────────────┐
  │ SQLite                                       │
  │  documents: 文档元信息 + API 结构化字段       │
  │  document_chunks: 切块原文（不做分析）        │
  │                                              │
  │ ChromaDB（单一 collection: doc_search）       │
  │  page_content: 原文简单摘要（或 API 检索文本）│
  │  metadata: {doc_id, doc_type, chunk_index}   │
  └─────────────────────────────────────────────┘

阶段 2: 绑定后三步分析（前端按钮触发）
  ┌─────────────────────────────────────────────┐
  │ 用户绑定文档到模块 → 点击"分析测试场景"       │
  │                                              │
  │ Step 1: 产品文档原文 → LLM thinking → 场景总结
  │ Step 2: 场景总结 + Axure 原文 → 逻辑关系总结  │
  │ Step 3: 前两步输出 + API 定义 → 接口映射总结  │
  │  → 存入 module_analysis 表（三个 Text 列）    │
  │                                              │
  │ ChromaDB                                     │
  │  → 用分析结果重建检索文本（覆盖阶段1的简单摘要）│
  └─────────────────────────────────────────────┘

阶段 3: 检索（Phase B 使用）
  ┌─────────────────────────────────────────────┐
  │ 优先路径：module_analysis 存在且至少一列非空  │
  │  → 用分析后的结构化数据（三步分析文本拼接）    │
  │  → Token 少、精准                            │
  │                                              │
  │ 降级路径：module_analysis 不存在或全为空      │
  │  → ChromaDB 语义检索拿 doc_id                │
  │  → SQLite 取原文（阶段1的原始数据）           │
  └─────────────────────────────────────────────┘
```

**核心原则**：
```
检索优先级:  分析结果 > 精细总结 > 简单摘要 > 原文
存储位置:    全部正文在 SQLite，ChromaDB 只存可重建的检索文本
```

---

## 二、SQLite schema 变更

### 2.1 `documents` 表 — API 类型加列

```python
# database/models.py — Document 新增字段（仅 api 类型填充）
api_name        = Column(String(200))
api_url         = Column(String(500))
api_method      = Column(String(10))
api_description = Column(String(500))
api_headers     = Column(Text)      # JSON: [{name, type, required, description, default}]
api_parameters  = Column(Text)      # JSON: [{name, type, required, description, default, children}]
api_returns     = Column(Text)      # JSON: [{name, type, required, description, default, children}]
api_annotations = Column(Text)      # JSON: {key: {active, source, ...meta}}

# 所有文档类型通用新增字段
content_hash    = Column(String(64))  # 文档内容 SHA256（多 chunk 拼接后哈希），用于感知文档变更
```

### 2.2 `document_chunks` 表 — product/axure 切块原文（新增）

三阶段数据：原文 → 简单摘要 → 分析后精细总结。

```python
class DocumentChunk(Base):
    """文档切块：原文 + 阶段1简单摘要 + 阶段2分析后精细总结。

    与 documents 表通过 doc_id 关联（非外键，允许独立删除）。
    阶段1入库时填充 content + simple_summary。
    阶段2分析后填充 analyzed_summary + analyzed_tags + analyzed_at。
    """
    __tablename__ = "document_chunks"

    id = Column(Integer, primary_key=True, autoincrement=True)
    doc_id = Column(String(200), nullable=False, index=True)
    chunk_index = Column(Integer, nullable=False)

    # ── 阶段1: 原文 + 简单摘要 ──
    content = Column(Text, nullable=False)           # 切块原文
    simple_summary = Column(Text, default="")        # 入库时 LLM 生成简单摘要（200字内）

    # ── 阶段2: 模块分析后的精细总结 ──
    analyzed_summary = Column(Text, default="")      # 分析后精细总结（含场景归属、接口关联）
    analyzed_tags = Column(Text, default="")         # JSON: ["标签1","标签2"]
    analyzed_at = Column(DateTime)                   # 分析时间

    # ── 元数据 ──
    chunk_type = Column(String(20), default="text")  # text | page | section
    page_name = Column(String(200), default="")      # Axure 页面名 / PDF 章节标题
    token_count = Column(Integer, default=0)
```

ChromaDB 检索文本来源（优先级从高到低）：
1. `analyzed_summary` 非空 → 用精细总结
2. `simple_summary` 非空 → 用简单摘要
3. 都没有 → 取 content 前 500 字

### 2.3 `compensation_tasks` 表 — LLM 摘要失败补偿（新增）

```python
class CompensationTask(Base):
    """补偿任务：LLM 摘要生成失败后的异步重试"""
    __tablename__ = "compensation_tasks"

    id = Column(Integer, primary_key=True, autoincrement=True)
    doc_id = Column(String(200), nullable=True, index=True)   # NULL = 全局重建任务
    chunk_index = Column(Integer, nullable=False, default=0)
    task_type = Column(String(50), default="summary")  # summary | chroma_rebuild
    status = Column(String(20), default="pending")      # pending | processing | done | failed
    retry_count = Column(Integer, default=0)
    max_retries = Column(Integer, default=2)
    error_message = Column(Text, default="")
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    next_retry_at = Column(DateTime)                    # 下次重试时间（10分钟后）

    # 应用层唯一约束（防止竞态重复入队）：
    #   INSERT 前在事务内 SELECT，WHERE doc_id=? AND task_type=? AND status='pending' AND created_at > now-30min
    #   不存在时才 INSERT，事务提交保证原子性
    __table_args__ = (
        Index('idx_compensation_pending', 'doc_id', 'task_type', 'status'),
    )
```

**去重与僵尸清理规则**：
- **去重**：同一 `(doc_id, task_type)` 在 `status IN (pending, processing)` 且 `created_at` 在 30 分钟内 → 视为已有任务，跳过
- **僵尸清理**：`status=pending` 或 `status=processing` 且 `created_at` 超过 30 分钟 → 视为 worker 崩溃遗留，标为 `failed`，允许新建补偿任务

### 2.4 `module_analysis` 表 — 三步分析输出（替代旧 `analysis_json`）

```python
class ModuleAnalysis(Base):
    __tablename__ = "module_analysis"

    id = Column(Integer, primary_key=True, autoincrement=True)
    module_id = Column(String(36), ForeignKey("modules.id", ondelete="CASCADE"),
                       nullable=False, unique=True, index=True)
    module_name = Column(String(200), nullable=False)

    # ── 三步分析输出（自由文本）──
    scenario_analysis = Column(Text, default="")   # Step 1: 产品文档 → 场景总结
    ui_flow_analysis = Column(Text, default="")    # Step 2: 场景+Axure → 逻辑关系
    api_analysis = Column(Text, default="")        # Step 3: 前两步+API定义 → 接口映射

    status = Column(String(20), default="draft")
    extracted_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    modified_at = Column(DateTime, ...)
    modified_by = Column(String(100), default="")
    version = Column(Integer, default=1)

    # ── 版本控制 ──
    bindings_hash = Column(String(64))  # 模块当前绑定文档集合的聚合 hash（doc_id 列表 + 各文档 content_hash 拼接后 SHA256）
                                         # 用于判断是否需要重新分析：hash 不变 → 跳过分析
```

**对比旧结构**：`analysis_json`（单一 JSON 列）拆为三个 Text 列，每步独立写入，Phase B 消费时按存在性拼接。

**分析触发判断**：
```python
# 计算当前绑定文档集合的 hash
current_hash = sha256("|".join(sorted(doc_ids)) + "|" + "|".join(sorted(content_hashes)))
if module_analysis.bindings_hash == current_hash:
    return  # 跳过，内容未变
else:
    执行三步分析 → 更新 bindings_hash
```

### 2.5 迁移策略

- `init_db()` 中 `Base.metadata.create_all()` 自动加列+建表
- 存量数据：一次性脚本从 ChromaDB 回灌到 SQLite
- 新增数据：直接写入 SQLite 列/表

---

## 三、ChromaDB 检索文本构造（单一 Collection）

合并 `product_docs` 和 `api_defs` 为一个 collection `doc_search`，靠 `metadata.doc_type` 区分 `api` / `product` / `axure`。

```python
# ChromaDB metadata 结构
{
    "doc_id": "xxx",
    "doc_type": "api" | "product" | "axure",
    "chunk_index": 0,           # 仅 product/axure
    "page_name": "",            # 仅 axure
    "source": "simple_summary" | "analyzed_summary" | "content_fallback",
}
```

**`source` 字段说明**：
- `simple_summary`：正常态，LLM 摘要已生成
- `analyzed_summary`：分析后更新，精细总结
- `content_fallback`：降级态，LLM 摘要失败，用 content 前 500 字作为临时检索文本，等待补偿任务覆盖

### 3.1 API 文档检索文本

不再存 JSON，改为构造自然语言检索文本：

```
"{method} {url} {api_name}。{description}。
  参数: {param1_name}{param1_type}{'必填' if required else ''}{param1_desc};
       {param2_name}{param2_type}{'必填' if required else ''}{param2_desc}; ...
  返回值: {ret1_name}{ret1_type}; {ret2_name}{ret2_type}; ...
  标签: {'导出接口' if is_export else ''} {'RESTful路径参数' if has_path_params else ''}"
```

### 3.2 产品/Axure 文档检索文本

不再存原文，改为存 LLM 摘要 + 结构化描述：

```
"[{page_name}] {summary}"
```

其中 `summary` 由入库时 LLM 生成，概括该切块的核心内容。检索文本精简为 200-500 字，去除了原文中的格式噪音、HTML 标签、冗余描述。

### 3.3 构造位置

- API：`ingest_v2.py` `commit_api_docs()` → `_build_api_search_text(api)`
- 产品/Axure：`ingest_v2.py` `process_product_doc()` / `process_axure_zip()` → LLM 生成摘要后写入 `document_chunks.simple_summary`，再构造检索文本

---

## 四、涉及文件及改动

### 4.1 `database/models.py`

| 改动 | 说明 |
|:---|------|
| `Document` 类加 9 列 | `api_name` ~ `api_annotations`（8 列）+ `content_hash`（1 列） |
| 新增 `DocumentChunk` 类 | 切块原文+摘要表 |
| 新增 `CompensationTask` 类 | LLM 摘要失败补偿任务表 |
| `ModuleAnalysis` 表改造 | `analysis_json` 拆为 `scenario_analysis` / `ui_flow_analysis` / `api_analysis` 三个 Text 列 |

### 4.2 `ingest_v2.py`

| 函数 | 改动 |
|:---|------|
| `commit_api_docs()` | 写入 SQLite `api_*` 列；构造检索文本写入 ChromaDB |
| `process_product_doc()` | 切块原文写入 `document_chunks`；批量调 LLM 生成 `simple_summary`；构造检索文本写入 ChromaDB；LLM 失败 → 降级入库 + 创建补偿任务 |
| `process_axure_zip()` | 同上，`page_name` 填 Axure 页面名 |
| 新增 `_build_api_search_text(api)` | 构造 API 检索文本 |
| 新增 `_build_doc_search_text(chunk)` | 构造产品/Axure 检索文本 |
| 新增 `_batch_generate_summaries(chunks)` | 批量调 LLM 生成摘要（5 chunks/批，同步等待） |
| `_safe_doc_id()` | 增加 `<` `>` `"` `&` 等 HTML 字符清理 |

### 4.3 `agent_components/dual_chroma.py`

| 方法 | 改动 |
|:---|------|
| `add_api_defs()` | page_content 改为检索文本；写入单一 collection `doc_search` |
| `add_product_doc_chunks()` | page_content 改为摘要文本；写入单一 collection `doc_search` |
| `get_doc_apis()` | 返回值改为从 SQLite 查正文 + ChromaDB 只补 doc_id |
| `get_doc_chunks()` | 返回值改为从 SQLite `document_chunks` 查原文 |
| 新增 `_ensure_healthy()` → bool | ChromaDB 健康检查：`collection.count()` 成功→True（单条补偿）；失败→False（即时降级 + 全局异步重建） |

### 4.4 `web/routes/modules.py`

| 端点 | 改动 |
|:---|------|
| `GET /{name}/api-defs` | 直接从 SQLite `api_*` 列查，不走 ChromaDB |
| `PUT /{name}/api-defs/{index}/annotations` | 更新 SQLite `api_annotations` → 异步重建 ChromaDB 检索文本 |
| `POST /{name}/analyze-scenarios` | 改为调用三步分析管线（替代旧 `_analyze_module_scenarios_bg`） |

### 4.5 `web/routes/docs.py`

| 端点 | 改动 |
|:---|------|
| `GET /{doc_id}/chunks` | 从 SQLite `document_chunks` 查原文，不走 ChromaDB |

### 4.6 `agent_components/retrievers.py`

| 方法 | 改动 |
|:---|------|
| 所有检索方法 | 先从 ChromaDB 语义检索拿 doc_id，再从 SQLite 查正文 |
| `_analyze_test_points_raw()` | 优先用 `module_analysis` 三步分析文本拼接；降级走 ChromaDB + SQLite |

### 4.7 `prompts/response_model.py`

| 改动 |
|:---|
| `ApiDefinition` model 无需改动（已经兼容新旧格式） |

### 4.8 `agent_components/generators/__init__.py`

| 方法 | 改动 |
|:---|------|
| `_generate_one_yaml()` | `api_defs_json` 来源从 ChromaDB 改为 SQLite |

### 4.9 `prompts/extraction_prompts.py`

| 改动 |
|:---|
| 新增 `batch_chunk_summary_prompt()` — 批量 chunk 摘要（增强版，带 file_name + page_name + chunk 序号） |
| 新增 `analyze_product_scenarios_prompt()` — 三步分析 Step 1 |
| 新增 `analyze_axure_ui_flow_prompt()` — 三步分析 Step 2 |
| 新增 `analyze_api_mapping_prompt()` — 三步分析 Step 3 |

### 4.10 `web/tasks.py`

| 改动 |
|:---|
| `_MAX_WORKERS` 10→9（预留 1 个给补偿 worker） |
| 新增 `_compensation_worker()` 独立轮询线程 |
| 新增 `_analyze_module_scenarios_3step_bg(task_id, module_name)` — 替代旧 `_analyze_module_scenarios_bg` |

### 4.11 `database/operations/compensation.py`（新建）

| 内容 |
|:---|
| `CompensationOps` — 补偿任务 CRUD（create / poll_pending / mark_done / mark_failed） |

### 4.12 `settings.py`

| 新增配置项 |
|:---|
| `llm_global_concurrency=5` — LLM 全局并发上限 |
| `batch_summary_chunk_size=5` — 每批摘要 chunk 数 |
| `compensation_poll_interval=1800` — 补偿轮询间隔（秒），启动时立即执行一次，之后每 30 分钟轮询 |
| `compensation_max_retries=2` — 补偿最大重试次数 |
| `compensation_zombie_timeout_minutes=30` — 补偿任务僵尸超时（分钟），pending/processing 超时视为 worker 崩溃 |
| `COLLECTION_DOC_SEARCH="doc_search"` — 合并后的单一 ChromaDB collection 名称 |

---

## 五、实施步骤

### 依赖关系总览

```
Phase 1: Schema 变更（基础设施，无依赖）
  │
  ├── Phase 2: 阶段1 — API 原文入库
  │     │
  │     └── Phase 6 Step 3 前置依赖（三步分析 Step 3 从 SQLite 读 API 定义）
  │
  ├── Phase 3: 阶段1 — 产品/Axure 原文入库 + 补偿体系
  │     │
  │     └── Phase 4 前置依赖（三步分析 Step 1 需要 document_chunks.content）
  │
  ├── Phase 4: 阶段2 — 三步分析管线
  │     │
  │     └── Phase 5 前置依赖（检索优先级依赖 module_analysis）
  │
  ├── Phase 5: 阶段3 — 检索优先级 + 读取路径切换
  │
  ├── Phase 6: ChromaDB 退化 + 单 Collection 合并 + 损坏补偿
  │
  └── Phase 7: 存量迁移 + 验证
```

---

### 跨 Phase 同步改造清单

> 以下 11 个调用点受 ChromaDB 返回值断裂 + `ModuleAnalysis` 表结构变更影响。按执行批次分 3 组切换，**每组内部必须同步上线**，组间设立检查点验证通过后再推进。

#### 第 2 批切换点（对应 §八 Step 7）

> 检查点：ChromaDB 写入已切换到 `doc_search`，旧 collection 仍并行保留用于读取。

| # | 调用点 | 当前行为 | 改造目标 | 对应 Step |
|:---|:---|:---|:---|:---|
| 11 | `dual_chroma.py` — 构造函数 | 两个 Chroma 实例 + 两个持久化目录 | 单一 `doc_search` collection，写入双写（新旧并行） | Step 7 |

**验证**：上传任意文档 → 新旧 collection 均有数据；旧读取路径不受影响。

---

#### 第 3 批切换点 A：三步分析管线（对应 §八 Step 8）

> 检查点：三步分析管线可独立运行，产出写入 `module_analysis` 三个 Text 列。旧 `analysis_json` 列保留不动，旧分析函数不删。

| # | 调用点 | 当前行为 | 改造目标 | 对应 Step |
|:---|:---|:---|:---|:---|
| 4 | `tasks.py` — 三步分析读 chunk | 旧逻辑读 ChromaDB 原文 | 三步管线从 SQLite 读 `document_chunks.content` | Step 8 |
| 5 | `tasks.py` — 三步分析读 API | 旧逻辑读 ChromaDB JSON | 三步管线从 SQLite `api_*` 列读 | Step 8 |
| 6 | `tasks.py` — 分析结果写入 | `AnalysisOps.upsert(session, mid, name, json_text)` | 改为三列分别写入（同时保留旧 `analysis_json` 兼容写入） | Step 8 |

**验证**：触发三步分析 → `module_analysis` 三个 Text 列有内容 + 旧 `analysis_json` 列同步更新（双写兼容期）。

---

#### 第 3 批切换点 B：检索 + 读取路径（对应 §八 Step 9-10）

> 检查点：所有消费者切换到从 SQLite 读正文，ChromaDB 退化为纯索引。此组调用点最多，**必须先通过检查点 A 再执行**。

| # | 调用点 | 当前行为 | 改造目标 | 对应 Step |
|:---|:---|:---|:---|:---|
| 1 | `modules.py` — 读 API defs | `chroma.get_doc_apis()` → `json.loads(content)` | 从 SQLite `api_*` 列直接读 | Step 9 |
| 2 | `modules.py` — 收集全模块 API | 同上 | 同上 | Step 9 |
| 3 | `docs.py` — `/chunks` 端点 | `db.get_doc_chunks()` 返回 `content` | 从 SQLite `document_chunks` 读 | Step 9 |
| 7 | `modules.py` — 编辑分析 | 读/写 `analysis_json` 单字段 | 改为读/写三个 Text 列 | Step 9 |
| 8 | `retrievers.py` — Phase B 消费分析 | `record.analysis_json` 单字段 | 拼接三个 Text 列（兼容旧 `analysis_json`） | Step 9 |
| 9 | `modules.py` — 读取分析 | `record.analysis_json` 单字段 | 拼接三个 Text 列返回（兼容旧 `analysis_json`） | Step 9 |
| 10 | `retrievers.py` — 检索数据流 | ChromaDB 搜索返回原文存 `state["product_docs"]` | ChromaDB 返回 doc_id → SQLite 取原文；检索前加 `_ensure_healthy()` | Step 9 |

**验证**：有分析走分析、无分析走检索降级 → 两条路径均正常返回数据；旧 `analysis_json` 列仍可回退读取。

---

#### 第 4 批：死代码清理（对应 §八 Step 13）

> 前置：Step 12 全部测试通过，系统已稳定运行一段时间，确认无消费者回退到旧路径。

| # | 调用点 | 删除内容 | 对应 Step |
|:---|:---|:---|:---|
| D1 | `prompts/extraction_prompts.py` | `analyze_module_scenarios_prompt()` + `format_module_scenarios_prompt()` | Step 13 |
| D2 | `web/tasks.py` | `_analyze_module_scenarios_bg()` | Step 13 |
| D3 | `database/models.py` | `ModuleAnalysis.analysis_json` 列（SQLite 不支持 DROP COLUMN，保留列不填充，ORM 移除字段映射） | Step 13 |
| D4 | `agent_components/dual_chroma.py` | 旧 `product_docs` / `api_defs` collection 常量和写入路径 | Step 13 |
| D5 | `settings.py` | 旧 collection 名称配置 key | Step 13 |

---

### Phase 1: Schema 变更

| 步骤 | 文件 | 内容 |
|:---|:---|:---|
| 1.1 | `database/models.py` | `Document` 加 9 列（`api_name` ~ `api_annotations` 共 8 列 + `content_hash` 1 列） |
| 1.2 | `database/models.py` | 新增 `DocumentChunk` 表（含 `simple_summary`、`analyzed_summary`、`analyzed_tags`、`analyzed_at`、`page_name`、`token_count`） |
| 1.3 | `database/models.py` | 新增 `CompensationTask` 表 |
| 1.4 | `database/models.py` | `ModuleAnalysis` 表改造：`analysis_json` 拆为 `scenario_analysis` / `ui_flow_analysis` / `api_analysis` 三个 Text 列 |
| 1.5 | `database/operations/compensation.py` | 新建，补偿任务 CRUD（`create` / `poll_pending` / `mark_done` / `mark_failed`） |
| 1.6 | `settings.py` | 新增 `llm_global_concurrency`、`batch_summary_chunk_size`、`compensation_poll_interval`、`compensation_max_retries`、`compensation_zombie_timeout_minutes`、`COLLECTION_DOC_SEARCH` |
| 1.7 | — | `init_db()` 测试 — 验证自动迁移 |

---

### Phase 2: 阶段1 — API 原文入库

| 步骤 | 文件 | 内容 |
|:---|:---|:---|
| 2.1 | `ingest_v2.py` | `commit_api_docs()` 填充 `api_*` 列（`api_name` ~ `api_annotations`） |
| 2.2 | `ingest_v2.py` | 新增 `_build_api_search_text(api)` — 构造 API 自然语言检索文本 |
| 2.3 | `ingest_v2.py` | ChromaDB 写入改为检索文本（阶段1版本），`metadata.source = "simple_summary"` |

---

### Phase 3: 阶段1 — 产品/Axure 原文入库 + 补偿体系

| 步骤 | 文件 | 内容 |
|:---|:---|:---|
| 3.1 | `prompts/extraction_prompts.py` | 新增 `batch_chunk_summary_prompt()` — 增强版（带 `file_name` + `page_name` + chunk 序号上下文） |
| 3.2 | `ingest_v2.py` | `process_product_doc()` / `process_axure_zip()`：切块原文写入 `document_chunks.content` |
| 3.3 | `ingest_v2.py` | 批量调 LLM 生成 `simple_summary`：每批 5 chunks，同步等待，`===CHUNK_SUMMARY===` 分隔词 + 正则解析 |
| 3.4 | `ingest_v2.py` | LLM 摘要失败 → **降级入库**（方案 B）：原文写入 SQLite + ChromaDB 写 content 前 500 字（`source: content_fallback`）→ 创建 `compensation_tasks` 记录 |
| 3.5 | `web/tasks.py` | `_MAX_WORKERS` 10→9；新增 `_compensation_worker()` 独立轮询线程（启动时立即执行一次，之后每 30 分钟轮询） |
| 3.6 | `ingest_v2.py` | 新增 `_build_doc_search_text(chunk)` — 构造产品/Axure 检索文本 |
| 3.7 | `ingest_v2.py` 所依赖的 AxureParser | `to_product_doc_chunks()` 返回结构从 `list[str]` 升级为 `list[dict]`（含 `content` + `page_name`），填充 `document_chunks.page_name` |

**补偿任务流程**（全部任务先落盘后处理）：
```
创建任务：
  任何需要异步补偿的场景 → 先 INSERT compensation_tasks（status=pending）→ 落盘成功即返回
  去重：事务内 SELECT WHERE (doc_id, task_type, status='pending' OR status='processing')
        AND created_at > now - compensation_zombie_timeout_minutes → 存在则跳过

compensation_worker 轮询 compensation_tasks（status=pending, next_retry_at <= now）
  ├─ 更新 status=processing（防止其他 worker 重复取）
  ├─ 重试 LLM 生成摘要
  ├─ 成功 → 更新 SQLite simple_summary + 覆盖 ChromaDB 检索文本（source → simple_summary）
  │         → status=done
  ├─ 失败 → retry_count++，next_retry_at = now + 10min，status 回 pending
  └─ retry_count >= max_retries → status=failed，报异常升级，走损坏补偿逻辑

僵尸任务清理（worker 同时扫描两种超时）：
  ├─ status=pending AND created_at < now - 30min → worker 崩溃未取走 → 标为 failed
  └─ status=processing AND created_at < now - 30min → worker 处理中崩溃 → 标为 failed，新建补偿任务
```

**LLM 全局并发控制**：
- `llm_global_concurrency=5`：Phase 3 摘要、Phase 4 分析、Phase B 生成共享此池
- 每批 5 chunks → 1 次 LLM 调用，200 chunks = 40 批，并发 5 → 8 轮

---

### Phase 4: 阶段2 — 三步分析管线

替代原有 `_analyze_module_scenarios_bg()` 一次性全量分析。三种文档类型按依赖关系**串行级联分析**，每步 thinking 模式、1 次 LLM 调用、输出自由文本。

#### 4.1 管线流程


前置检查：
  无产品文档 → 提示"请先绑定产品文档"，终止分析
  无 API 文档 → 提示"请先绑定 API 文档"，终止分析

Step 1: 产品文档 → 测试场景总结
  输入: 该模块所有 doc_type='product' 的 document_chunks.content（原文全量）
  LLM:  thinking 模式
  输出: 自由文本 — 业务场景、功能点、scope 覆盖维度
  存储: module_analysis.scenario_analysis

Step 2: 场景总结 + Axure → 逻辑关系总结
  输入: Step1 输出文本 + 该模块所有 doc_type='axure' 的 document_chunks.content
  LLM:  thinking 模式
  输出: 自由文本 — 页面流转、交互逻辑、数据表单结构
  存储: module_analysis.ui_flow_analysis
  无 Axure → 跳过，字段留空

Step 3: 场景总结 + 逻辑关系 + API → 接口总结
  输入: Step1 输出 + Step2 输出 + 该模块所有 doc_type='api' 的定义（从 SQLite documents.api_* 列读取）
  LLM:  thinking 模式
  输出: 自由文本 — 接口→场景映射、数据流向、跨模块约束
  存储: module_analysis.api_analysis

Step 4: 分析结果回写 ChromaDB 检索文本
  输入: 三步分析输出的 analyzed_summary（写入 document_chunks 后）
  动作: 调用 _build_doc_search_text(chunk)（复用入库时的检索文本构造方法）
        → 覆盖 ChromaDB 中对应 chunk 的 page_content（source → analyzed_summary）
        → 存储字段（analyzed_summary）和待总结字段（simple_summary）作为参数传入
  失败: 创建 compensation_task（task_type=chroma_rebuild, doc_id=具体ID）→ 走统一补偿


#### 4.2 关键设计决策

| 决策 | 结论 | 理由 |
|:---|:---|:---|
| 输出格式 | 自由文本（非 JSON） | thinking 质量高；自由文本给 Phase B 更多上下文 |
| 输入策略 | 原文全量，不做筛选 | 不依赖尚未实现的 simple_summary |
| 缺失文档类型 | 无产品文档/API → 直接终止并提示；无 Axure → 跳过对应步骤 | 产品文档和 API 为必须，Axure 为可选 |
| 每步 LLM 调用 | 1 次 thinking 模式 | 串行级联，上一步输出作为下一步上下文 |
| 替代对象 | 替代 `_analyze_module_scenarios_bg()` | 新的三步后台任务覆盖旧逻辑 |

#### 4.3 实施步骤

| 步骤 | 文件 | 内容 |
|:---|:---|:---|
| 4.1 | `prompts/extraction_prompts.py` | 新增 `analyze_product_scenarios_prompt()` — Step 1 |
| 4.2 | `prompts/extraction_prompts.py` | 新增 `analyze_axure_ui_flow_prompt()` — Step 2 |
| 4.3 | `prompts/extraction_prompts.py` | 新增 `analyze_api_mapping_prompt()` — Step 3 |
| 4.4 | `web/tasks.py` | 新增 `_analyze_module_scenarios_3step_bg(task_id, module_name)`：三步串行，每步独立心跳 + 进度上报 |
| 4.5 | `web/app.py` | 端点 `POST /api/module/{name}/analyze-scenarios` 改为调用三步管线 |
| 4.6 | `agent_components/retrievers.py` | Phase B 消费改为拼接三个 Text 列（兼容旧 `analysis_json`） |
| 4.7 | `ingest_v2.py` | 复用 `_build_doc_search_text(chunk)`，分析完成后覆盖 ChromaDB 检索文本（`source → analyzed_summary`）；`analyzed_summary` 和 `simple_summary` 作为参数传入，方法内部按优先级取检索文本 |

#### 4.4 Phase B 消费方式

```python
# agent_components/retrievers.py — 拼接三步分析文本注入 prompt
parts = []
if record.scenario_analysis:
    parts.append("### 测试场景分析\n" + record.scenario_analysis)
if record.ui_flow_analysis:
    parts.append("### 页面交互逻辑\n" + record.ui_flow_analysis)
if record.api_analysis:
    parts.append("### 接口映射分析\n" + record.api_analysis)
analysis_text = "\n\n".join(parts)
```

#### 4.5 前置依赖

| 步骤 | 前置条件 | 状态 |
|------|---------|------|
| Step 1 | `document_chunks` 表存在 + `content` 已填充 | 依赖 Phase 3 |
| Step 2 | Step 1 完成 + Axure 文档已入库 | 依赖 Step 1 |
| Step 3 | Step 1/2 完成 + `documents.api_*` 列已迁移 | **依赖 Phase 2 先完成** |
| Step 4 | Step 1/2/3 完成 + `analyzed_summary` 已写入 `document_chunks` | 依赖 Step 1/2/3；复用 `ingest_v2._build_doc_search_text()` |

---

### Phase 5: 阶段3 — 检索优先级 + 读取路径切换

| 步骤 | 文件 | 内容 |
|:---|:---|:---|
| 5.1 | `agent_components/retrievers.py` | 检索优先级：`module_analysis` 存在且**至少一个分析列非空** → 拼接非空分析文本注入；否则 → ChromaDB 语义检索拿 doc_id → SQLite 取原文 |
| 5.2 | `web/routes/modules.py` | `get_module_api_defs` 改为读 SQLite `api_*` 列 |
| 5.3 | `web/routes/docs.py` | `/chunks` 端点改为读 SQLite `document_chunks` |
| 5.4 | `agent_components/generators/__init__.py` | `api_defs_json` 来源改为 SQLite |

---

### Phase 6: ChromaDB 退化 + 单 Collection 合并 + 损坏补偿

| 步骤 | 文件 | 内容 |
|:---|:---|:---|
| 6.1 | `agent_components/dual_chroma.py` | `product_docs` + `api_defs` 合并为单一 collection `doc_search`；写入方法只存检索文本 + metadata 指针 |
| 6.2 | `agent_components/dual_chroma.py` | 读取方法只返回 doc_id/chunk_index，调用方从 SQLite 取正文 |
| 6.3 | `agent_components/dual_chroma.py` | 新增 `_ensure_healthy()` → bool：`collection.count()` 成功 → True（整体健康，单条 page_content 空走单文档补偿）；失败 → False（全局损坏，触发全局重建 + 即时降级） |
| 6.4 | `web/routes/modules.py` | API Annotations 编辑：更新 SQLite `api_annotations` 列 → 返回成功 → 异步重建 ChromaDB 检索文本；失败 → 创建 `compensation_task` 走统一补偿流程 |

#### 6.1 ChromaDB 损坏检测与补偿机制

**检测入口**：`_ensure_healthy() → bool`，每次检索前调用 `collection.count()` 判断整体健康度。
- 返回 `True`：整体健康，调用方正常走 ChromaDB 检索路径
- 返回 `False`：全局损坏，调用方走即时补偿（SQLite 取数据），后台触发全局重建

```
_ensure_healthy() 流程：

  collection.count() 调用
    │
    ├─ 失败（连接失败 / collection 不存在）
    │     → 整体损坏，触发全局重建（task_type=chroma_rebuild, doc_id=NULL）
    │
    └─ 成功
          → 整体健康，继续检索
          → 检索结果中 page_content 为空的单条 → 触发单文档补偿（task_type=chroma_rebuild, doc_id=具体ID）
```

**即时补偿**（保证当前请求不卡死）：
  1. 整体损坏：从 SQLite `document_chunks` 提取 analyzed_summary 或 content 原文返回
  2. 单条损坏：同上，只补当前 doc_id 的数据
  3. 优先级：analyzed_summary > simple_summary > content 原文

**后台补偿**（异步任务，带去重 + 竞态防护 + 僵尸清理）：
  1. 去重查询（事务内）：SELECT WHERE doc_id=? AND task_type=? AND status IN ('pending','processing') AND created_at > now-30min
  2. 存在 → 跳过（已有活跃补偿任务）
  3. 不存在 → 原子 INSERT 新任务（事务提交保证无竞态）
  4. Worker 从 SQLite 重建 ChromaDB 检索文本
  5. 检索文本优先级：analyzed_summary > simple_summary > content 前 500 字
  6. 僵尸清理：Worker 扫描 status IN ('pending','processing') AND created_at < now-30min → 标为 failed，释放阻塞；processing 超时额外新建补偿任务


#### 6.2 API Annotations 编辑一致性策略

走统一补偿体系（`compensation_tasks` 表），与损坏补偿共用去重、重试、僵尸清理机制。


以 SQLite 为准：
  编辑请求 → 更新 SQLite api_annotations 列 → 返回成功
                                         ↘ 异步：重建 ChromaDB 检索文本
                                            ├─ 成功 → 完成
                                            └─ 失败 → 创建 compensation_task（task_type=chroma_rebuild, doc_id=具体ID）
                                                     → 走统一补偿流程（去重 + 最多 2 次重试 + 僵尸清理）
                                                     → retry_count >= max_retries → status=failed，报异常升级

> 与损坏补偿的区别：Annotations 编辑失败是**已知确定的失败**，不需要等 30 分钟超时或探针探测，直接创建补偿任务入队即可。

---

### Phase 7: 存量迁移 + 验证

| 步骤 | 内容 |
|:---|:---|
| 7.1 | 一次性脚本：从 ChromaDB 回灌存量数据到 SQLite（`api_defs` → `documents.api_*` 列；`product_docs` page_content → `document_chunks.content`） |
| 7.2 | 一次性脚本：为存量 chunks 批量生成 `simple_summary`（走 Phase 3 的批量摘要逻辑，失败走补偿） |
| 7.3 | 单元测试：`DocumentChunk` CRUD、`CompensationTask` 生命周期、`ModuleAnalysis` 三步读写、补偿 worker 轮询逻辑 |
| 7.4 | 回归测试：智慧用电各批次无回归 |
| 7.5 | 端到端验证：上传 → 入库（含降级路径）→ 绑定 → 三步分析 → 检索 完整链路 |

---

## 六、风险评估

| 风险 | 缓解 |
|:---|------|
| 存量数据丢失 | 先跑迁移脚本，验证后再切代码 |
| ChromaDB 检索质量下降 | 检索文本比原文/JSON 更自然，质量应提升；降级路径保证可用性 |
| LLM 摘要成本 | 仅产品/Axure 文档入库时生成一次，Phase B 复用时不再调 LLM；每批 5 chunks 批量生成降低调用次数 |
| LLM 摘要失败 | 降级入库（content 截断写入 ChromaDB）+ 补偿任务异步重试 + 独立 worker 线程 |
| SQLite 大文本性能 | SQLite TEXT 类型支持 GB 级别，切块单条 < 10KB |
| 三步分析 Token 消耗 | thinking 模式单步 ~50K-200K token；原文全量注入可能超上下文窗口 → 后续优化：超阈值时按 simple_summary 预筛选 |
| 三步分析 LLM 不可用 | 三步分析失败 → `module_analysis` 不更新 → Phase B 自动降级到原文检索路径 |
| ChromaDB 损坏 | 即时补偿（SQLite 取文本）+ 后台异步重建；SQLite 为唯一真相源 |
| 补偿任务堆积 | 独立 worker 线程 + 最大重试次数限制 + 失败告警升级 |
| 上下游兼容性 | 一级一级切，每级独立验证；旧 `analysis_json` 兼容读取 |

---

## 七、决策记录

| # | 决策点 | 结论 | 理由 |
|:---|--------|------|------|
| 1 | 正文放哪 | SQLite（`documents` 列 + `document_chunks` 表） | 事务安全、SQL 可查、可备份 |
| 2 | ChromaDB 存什么 | API: 检索文本；产品/Axure: LLM 摘要 | 纯搜索引擎，可重建 |
| 3 | 检索文本格式 | 结构化自然语言（非 JSON） | 比 JSON/原文更利于语义匹配 |
| 4 | 产品/Axure 摘要 | 入库时 LLM 生成一次 | 不增加 Phase B 成本 |
| 5 | 存量迁移 | 一次性脚本 | 避免双写复杂度 |
| 6 | 前端接口编辑 | 直接改 SQLite 列 | 不走 ChromaDB，事务安全 |
| 7 | 文档 chunk 原文 | 单独建表 `document_chunks`（doc_id 不做外键） | 1:N 关系，独立管理，不污染 `documents` 表 |
| 8 | 入库时分析 | **不做**，只存原文+简单摘要 | 快速入库，不阻塞上传流程 |
| 9 | 分析时机 | 文档绑定到模块后，用户手动触发 | 用户可控，批量分析更准 |
| 10 | 检索优先级 | 分析结果 > 精细总结 > 简单摘要 > 原文 | 有分析用分析，没分析也能用 |
| 11 | 分析后 ChromaDB | 重建检索文本，覆盖阶段1简单摘要 | 语义检索始终用最佳文本 |
| 12 | ChromaDB 损坏 | 即时补偿（SQLite 取文本）+ 后台异步重建 | 当前请求不卡死，静默修复 |
| 13 | Annotations 编辑一致性 | SQLite 为准，ChromaDB 异步重建，失败走统一 `compensation_tasks` 流程 | 避免双写事务问题；复用去重+重试+僵尸清理机制 |
| 14 | 检索结果验证 | ChromaDB 返回 doc_id 后必须从 SQLite 拿正文 | 正文以 SQLite 为唯一真相源 |
| 15 | Collection 结构 | 合并 product_docs + api_defs 为单一 `doc_search` | 跨类型语义检索，管理简化 |
| 16 | 模块分析触发 | 单模块已绑定文档；hash 版本控制防重复 | 零成本重复点击；内容变更自动感知 |
| 17 | 模块解绑 | 解绑后清空 module_analysis，重新绑定后重新分析 | 不覆盖旧分析，直接重建 |
| 18 | LLM 摘要失败策略 | **方案 B — 降级入库** | 原文写入 SQLite + ChromaDB 写 content 截断作为临时检索文本；异步补偿补齐 |
| 19 | 降级标记 | ChromaDB metadata `source` 字段区分 | `simple_summary` / `analyzed_summary` / `content_fallback`；`content_fallback` = 待补偿 |
| 20 | 补偿任务持久化 | SQLite `compensation_tasks` 表 | 重启不丢；独立 worker 线程轮询处理 |
| 21 | 补偿 worker 线程 | 全局线程池 `max_workers` 10→9，预留 1 个给独立补偿轮询线程 | 补偿与正常任务隔离，互不阻塞 |
| 22 | 批量摘要 Prompt | **增强版** | 带 `file_name` + `page_name` + chunk 序号上下文 |
| 23 | 批量摘要每批 chunk 数 | **5 个** | 平衡 token 消耗与 LLM 调用次数 |
| 24 | 批量摘要输出格式 | LLM 输出 `===CHUNK_SUMMARY===` 分隔词，正则匹配解析 | 不用 json_mode，格式容错性更好；解析失败时逐条正则回退 |
| 25 | LLM 全局并发上限 | **5 个** | 多个 API key 时可适当增加；Phase 3 摘要、Phase 4 分析、Phase B 生成共享此池 |
| 26 | 批量摘要等待方式 | **同步等待** + 前端进度展示 | 失败部分写 `compensation_tasks` 异步补偿 |
| 27 | 摘要 Prompt 存放位置 | `prompts/extraction_prompts.py` | 新增 `batch_chunk_summary_prompt()` |
| 28 | 三步分析链路 | **产品文档→场景总结→Axure逻辑关系→接口总结** | 分步分析，上一步输出作为下一步上下文 |
| 29 | 三步分析输出存储 | **分开存储**：`module_analysis` 表拆为三个 Text 列 | `scenario_analysis` / `ui_flow_analysis` / `api_analysis`，每步独立写入 |
| 30 | 三步分析输出格式 | **自由文本**，thinking 模式，每步 1 次 LLM 调用 | thinking 质量高；自由文本给 Phase B 更多上下文 |
| 31 | 三步分析输入 | **原文全量**，不做任何筛选 | 不依赖尚未实现的 simple_summary |
| 32 | 缺失文档类型 | 无产品文档/API → 终止并提示；无 Axure → 跳过对应步骤 | 产品文档和 API 为分析必须项 |
| 33 | 三步分析替代现有分析 | 替代 `_analyze_module_scenarios_bg()` 的一次性分析 | 新的三步后台任务覆盖旧逻辑 |
| 34 | Step 3 前置依赖 | 依赖 API 数据先完成 SQLite 迁移（`documents.api_*` 列） | Step 3 从 SQLite 读 API 定义，不调 ChromaDB |
| 35 | 损坏补偿数据源优先级 | analyzed_summary > simple_summary > 磁盘原文件 > document_chunks.content | 分析后文件质量最高，优先使用 |
| 36 | 补偿检测触发场景 | `collection.count()` 二分：失败→全局重建；成功→单条 page_content 空走单文档补偿。去重覆盖 pending+processing 两种状态 | 封装在 `_ensure_healthy() → bool` 中统一处理 |
| 37 | 版本控制 hash | `documents.content_hash`（文档级 SHA256）+ `module_analysis.bindings_hash`（模块绑定集合聚合 hash） | 感知文档变更 + 绑定变更，hash 不变则跳过分析 |
| 38 | 分析后 ChromaDB 重建 | 三步分析完成后复用 `ingest_v2._build_doc_search_text()` 覆盖 ChromaDB 检索文本 | `analyzed_summary` 作为参数传入，失败走统一补偿 |
| 39 | 补偿任务统一入口 | 全部异步补偿先 INSERT `compensation_tasks` 落盘，worker 再取走处理 | 统一去重、重试、僵尸清理；Annotations 编辑失败也走此流程 |
| 40 | 僵尸超时覆盖范围 | pending + processing 两种状态，超时（默认 30min，可配置）→ 标 failed；processing 超时额外新建补偿任务 | worker 取走任务后崩溃不会永久阻塞 |
| 41 | `_ensure_healthy()` 契约 | 返回 bool：True=整体健康（单条问题走单文档补偿），False=全局损坏（走即时降级+全局重建） | 调用方根据返回值决定走 ChromaDB 还是 SQLite 降级路径 |

---

## 八、实施计划

> 按执行顺序排列，标注每步的输入/产出/验证方式/回滚策略。

### 执行路线图


第1批（基础设施，可并行）          第2批（入库改造）              第3批（分析+检索）              第4批（收尾）
──────────────────────────      ──────────────────            ──────────────────            ──────────────
Step 1: Schema 变更              Step 4: API 入库改造           Step 8: 三步分析管线          Step 12: 测试验证
Step 2: CompensationTask 表      Step 5: 产品/Axure 入库改造    Step 9: 检索优先级切换        Step 13: 清理死代码
Step 3: 补偿 worker + Config     Step 6: 批量摘要 Prompt        Step 10: ChromaDB 退化
                                 Step 7: ChromaDB 写入改造      Step 11: 存量迁移


---

### Step 1: Schema 变更（database/models.py）

| 项 | 内容 |
|:---|:---|
| **依赖** | 无 |
| **产出** | `Document` 加 9 列、`DocumentChunk` 表、`CompensationTask` 表、`ModuleAnalysis` 表改造 |
| **文件** | `database/models.py` |

**具体操作**：
1. `Document` 类新增 `api_name` ~ `api_annotations`（8 列）+ `content_hash`（1 列）= 9 列
2. 新增 `DocumentChunk` 类（`content`, `simple_summary`, `analyzed_summary`, `analyzed_tags`, `analyzed_at`, `chunk_type`, `page_name`, `token_count`）
3. 新增 `CompensationTask` 类（`doc_id` nullable, `chunk_index`, `task_type`, `status`, `retry_count`, `max_retries`, `error_message`, `created_at`, `next_retry_at` + `idx_compensation_pending` 索引）
4. `ModuleAnalysis` 表：`analysis_json` 拆为 `scenario_analysis` / `ui_flow_analysis` / `api_analysis` 三个 Text 列；新增 `bindings_hash` 列

**验证**：`init_db()` 调用后 `Base.metadata.create_all()` 无报错；新列/表在 SQLite 中可见。

**回滚**：删除新增列/表（SQLite 不支持 DROP COLUMN，需重建表或保留列不填充）。

---

### Step 2: 补偿任务 CRUD（database/operations/compensation.py）

| 项 | 内容 |
|:---|:---|
| **依赖** | Step 1（`CompensationTask` 表存在） |
| **产出** | `CompensationOps` 类 |
| **文件** | `database/operations/compensation.py`（新建） |

**具体操作**：
1. `CompensationOps.create(session, doc_id, chunk_index, task_type)` — 带去重逻辑：
   - 事务内 SELECT `WHERE doc_id=? AND task_type=? AND status IN ('pending','processing') AND created_at > now-30min`
   - 存在 → 跳过，返回 None；不存在 → INSERT，返回 task
2. `CompensationOps.poll_pending(session, task_type, limit)` — 取 `status='pending' AND next_retry_at <= now` 的任务
3. `CompensationOps.mark_processing(session, task_id)` — 更新 `status='processing'`
4. `CompensationOps.mark_done(session, task_id)` — 更新 `status='done'`
5. `CompensationOps.mark_failed(session, task_id, error)` — 更新 `status='failed'`
6. `CompensationOps.cleanup_zombies(session, timeout_minutes)` — 僵尸清理（pending + processing 超时 → failed；processing 超时额外新建补偿任务）

**验证**：单元测试覆盖去重（并发 INSERT 同一 doc_id 只成功一条）、僵尸清理（修改 created_at 模拟超时）。

---

### Step 3: 补偿 Worker + 配置（web/tasks.py + settings.py）

| 项 | 内容 |
|:---|:---|
| **依赖** | Step 1（config 项）+ Step 2（CompensationOps） |
| **产出** | `_compensation_worker()` 独立线程 |
| **文件** | `web/tasks.py`, `settings.py` |

**具体操作**：
1. `settings.py` 新增配置项：
   - `llm_global_concurrency=5`
   - `batch_summary_chunk_size=5`
   - `compensation_poll_interval=1800`（30 分钟，启动时立即执行一次）
   - `compensation_max_retries=2`
   - `compensation_zombie_timeout_minutes=30`
   - `COLLECTION_DOC_SEARCH="doc_search"` — 合并后的单一 ChromaDB collection 名称
2. `web/tasks.py`：
   - `_MAX_WORKERS` 10→9（预留 1 个线程给补偿 worker）
   - 新增 `_compensation_worker()` 线程函数：
     - 启动时立即执行一次，之后每 `compensation_poll_interval` 秒轮询
     - 每次循环：① `cleanup_zombies(session, compensation_zombie_timeout_minutes)` → ② `poll_pending()` → ③ 逐个处理（取 processing、调 LLM、更新结果）

**验证**：启动应用后日志显示 `[CompensationWorker] started`；手动插入一条 pending 任务，等待 worker 取走处理。

---

### Step 4: API 入库改造（ingest_v2.py）

| 项 | 内容 |
|:---|:---|
| **依赖** | Step 1（`Document.api_*` 列存在） |
| **产出** | API 正文写入 SQLite + 检索文本写入 ChromaDB |
| **文件** | `ingest_v2.py` |

**具体操作**：
1. `commit_api_docs()` 改造：
   - 写入 SQLite `documents` 表的 `api_name` ~ `api_annotations` 列（从解析后的 API dict 提取）
   - 计算 `content_hash = sha256(json.dumps(api_dict, sort_keys=True))`，写入 `documents.content_hash` 列
2. 新增 `_build_api_search_text(api)` 函数：
   - 构造自然语言检索文本：`"{method} {url} {api_name}。{description}。参数: ... 返回值: ..."`
3. ChromaDB 写入改造：
   - `page_content` 改为 `_build_api_search_text(api)` 的输出
   - `metadata.source = "simple_summary"`

**验证**：上传一个 API 文档 → 检查 SQLite `documents` 表中 `api_*` 列有数据；ChromaDB 中 `page_content` 为自然语言文本而非 JSON。

**回滚**：恢复从 ChromaDB `page_content` 读取 API 定义的旧逻辑（临时兼容）。

---

### Step 5: 产品/Axure 入库改造（ingest_v2.py）

| 项 | 内容 |
|:---|:---|
| **依赖** | Step 1（`DocumentChunk` 表存在）+ Step 3（config 可用）+ AxureParser 改造（`to_product_doc_chunks()` 返回 `list[dict]` 含 `content`+`page_name`） |
| **产出** | 产品/Axure 原文+摘要写入 SQLite + 检索文本写入 ChromaDB + 降级补偿 |
| **文件** | `ingest_v2.py`, AxureParser |

**具体操作**：
1. **前置改造 — AxureParser**：`to_product_doc_chunks()` 返回结构从 `list[str]` 升级为 `list[dict]`（每项含 `content` + `page_name`），填充 `document_chunks.page_name`
2. `process_product_doc()` / `process_axure_zip()` 改造：
   - 切块原文写入 `document_chunks`（`content`, `chunk_index`, `page_name`, `chunk_type`, `token_count`）
   - 计算 `content_hash = sha256("".join(chunk.content for chunk in chunks))`，写入 `documents.content_hash` 列
3. `_safe_doc_id()` 增加 `<` `>` `"` `&` 等 HTML 字符清理
4. 新增 `_batch_generate_summaries(chunks)` 函数：
   - 每批 `batch_summary_chunk_size`（默认 5）个 chunk
   - 调用 `batch_chunk_summary_prompt()`（Step 6 产出）
   - 全局并发 `llm_global_concurrency`（默认 5）限制
   - LLM 返回后正则解析 `===CHUNK_SUMMARY===` 分隔词
5. 新增 `_build_doc_search_text(chunk)` 函数：
   - 按优先级取检索文本：`analyzed_summary` > `simple_summary` > `content[:500]`
   - 格式：`"[{page_name}] {summary}"`
6. LLM 摘要失败 → 降级入库：
   - `simple_summary` 留空
   - ChromaDB 写入 `content[:500]`（`source: "content_fallback"`）
   - 创建 `CompensationTask`（`task_type="summary"`, `doc_id=具体ID`, `chunk_index`）

**验证**：上传产品文档 → SQLite `document_chunks` 有数据 → ChromaDB `page_content` 为摘要文本；模拟 LLM 失败 → `compensation_tasks` 表新增记录 → worker 稍后重试。

**回滚**：恢复 ChromaDB 写入原文的旧逻辑（临时兼容）。

---

### Step 6: 批量摘要 Prompt（prompts/extraction_prompts.py）

| 项 | 内容 |
|:---|:---|
| **依赖** | 无 |
| **产出** | `batch_chunk_summary_prompt()` 函数 |
| **文件** | `prompts/extraction_prompts.py` |

**具体操作**：
1. 新增 `batch_chunk_summary_prompt(file_name, chunks, page_names, start_index, total)` 函数：
   - 增强版模板：带 `file_name` + `page_name` + chunk 序号（`第 {i}-{j} 块 / 共 {total} 块`）
   - 输出格式指令：每个 chunk 的摘要用 `===CHUNK_SUMMARY===` 分隔，后跟摘要文本
   - 不使用 json_mode（格式容错性更好）

**验证**：单元测试 — 传入 5 个测试 chunk → 调用 LLM → 正则解析返回 5 条摘要。

---

### Step 7: ChromaDB 写入改造（agent_components/dual_chroma.py）

| 项 | 内容 |
|:---|:---|
| **依赖** | Step 4 + Step 5（检索文本构造方法已就绪） |
| **产出** | `add_api_defs()` / `add_product_doc_chunks()` 改为写入单一 collection `doc_search` |
| **文件** | `agent_components/dual_chroma.py` |

**具体操作**：
1. `add_api_defs()`：`page_content` 改为检索文本；`metadata = {doc_id, doc_type: "api", chunk_index: 0, source: "simple_summary"}`
2. `add_product_doc_chunks()`：`page_content` 改为摘要文本；`metadata = {doc_id, doc_type: "product"|"axure", chunk_index, page_name, source}`
3. 两个方法写入同一个 collection `doc_search`
4. 新增 `_ensure_healthy()` → bool：
   - `collection.count()` 成功 → 返回 True（整体健康）
   - 失败 → 返回 False（全局损坏，调用方走即时降级）

**验证**：上传 API + 产品文档 → ChromaDB `doc_search` collection 中同时有 `doc_type=api` 和 `doc_type=product` 的记录；metadata 字段完整。

**回滚**：保留旧 `product_docs` + `api_defs` 两个 collection，新 collection 并行写入一段时间后切换。

---

### Step 8: 三步分析管线（web/tasks.py + prompts/extraction_prompts.py）

| 项 | 内容 |
|:---|:---|
| **依赖** | Step 4（API 列已填充）+ Step 5（document_chunks.content 已填充）+ Step 6（prompt 已就绪） |
| **产出** | `_analyze_module_scenarios_3step_bg()` 替代旧 `_analyze_module_scenarios_bg()` |
| **文件** | `web/tasks.py`, `prompts/extraction_prompts.py`, `web/app.py`, `ingest_v2.py` |

**具体操作**：
1. `prompts/extraction_prompts.py` 新增三个 prompt：
   - `analyze_product_scenarios_prompt(chunks_text)` — Step 1
   - `analyze_axure_ui_flow_prompt(step1_output, axure_chunks_text)` — Step 2
   - `analyze_api_mapping_prompt(step1_output, step2_output, api_defs_text)` — Step 3
2. `web/tasks.py` 新增 `_analyze_module_scenarios_3step_bg(task_id, module_name)`：
   - 前置检查：无产品文档 → 终止并提示"请先绑定产品文档"；无 API → 终止并提示"请先绑定 API 文档"
   - 计算 `bindings_hash`：`sha256("|".join(sorted(doc_ids)) + "|" + "|".join(sorted(content_hashes)))`
   - 若 `module_analysis.bindings_hash == current_hash` → 跳过分析，直接返回
   - Step 1（thinking 模式）：产品文档原文全量 → `scenario_analysis`
   - Step 2（thinking 模式，无 Axure 跳过）：Step1 输出 + Axure 原文 → `ui_flow_analysis`
   - Step 3（thinking 模式）：Step1+Step2 输出 + SQLite `documents.api_*` 列 → `api_analysis`
   - 每步独立心跳 + 进度上报（`pending → step1 → step2 → step3 → step4 → completed`）
   - Step 4：分析结果回写 ChromaDB 检索文本：
     - 调用 `ingest_v2._build_doc_search_text(chunk)` 覆盖 ChromaDB（`analyzed_summary` 和 `simple_summary` 作为参数传入）
     - 失败 → 创建 `compensation_task`（`task_type="chroma_rebuild"`, `doc_id=具体ID`）
   - 全部完成 → 更新 `bindings_hash`
3. `web/app.py`：`POST /api/module/{name}/analyze-scenarios` 改为调用三步管线

**验证**：绑定产品文档+API→点击分析→`module_analysis` 表三个 Text 列有内容→ChromaDB 检索文本更新（`source: "analyzed_summary"`）。

**回滚**：临时切换回旧 `_analyze_module_scenarios_bg()` 端点。

---

### Step 9: 检索优先级 + 读取路径切换

| 项 | 内容 |
|:---|:---|
| **依赖** | Step 8（`module_analysis` 可产出） |
| **产出** | Phase B 优先用模块分析，降级走 ChromaDB+SQLite |
| **文件** | `agent_components/retrievers.py`, `web/routes/modules.py`, `web/routes/docs.py`, `agent_components/generators/__init__.py` |

**具体操作**：
1. `agent_components/retrievers.py` — `_analyze_test_points_raw()`：
   - 查 `module_analysis` 表 → 至少一个分析列非空 → 拼接三段分析文本注入 prompt（见 §4.4 消费方式）
   - 不存在或全为空 → 调用 `_ensure_healthy()` 判断 ChromaDB 状态：
     - 返回 True → ChromaDB 语义检索拿 doc_id → SQLite `document_chunks` / `documents.api_*` 取原文
     - 返回 False → 跳过 ChromaDB，直接从 SQLite 取原文（即时降级）
2. `web/routes/modules.py` — `get_module_api_defs`：从 SQLite `documents.api_*` 列读取
3. `web/routes/docs.py` — `/chunks` 端点：从 SQLite `document_chunks` 读取
4. `agent_components/generators/__init__.py` — `_generate_one_yaml()`：`api_defs_json` 来源改为 SQLite

**验证**：有 module_analysis 的模块 → Phase B 使用分析文本（Token 显著减少）；无 module_analysis 的模块 → 自动降级到原文检索（行为不变）。

---

### Step 10: ChromaDB 退化 + 损坏补偿

| 项 | 内容 |
|:---|:---|
| **依赖** | Step 7（单 collection）+ Step 9（读取路径已切换） |
| **产出** | ChromaDB 纯检索引擎 + 健康检查 + 统一补偿 |
| **文件** | `agent_components/dual_chroma.py`, `web/routes/modules.py` |

**具体操作**：
1. `agent_components/dual_chroma.py`：
   - 写入方法只存检索文本 + metadata 指针（Step 7 已完成）
   - 读取方法（`get_doc_apis()`, `get_doc_chunks()`）只返回 doc_id/chunk_index，调用方从 SQLite 取正文
   - `_ensure_healthy()` 集成到每次检索调用前：
     - 返回 False → 触发即时补偿（SQLite 取数据返回给调用方）+ 后台全局 ChromaDB 重建
     - 返回 True 但 page_content 为空 → 触发单文档补偿
2. `web/routes/modules.py` — API Annotations 编辑：
   - 更新 SQLite `api_annotations` → 返回成功
   - 异步重建 ChromaDB 检索文本 → 失败 → 创建 `compensation_task`（`task_type="chroma_rebuild"`, `doc_id=具体ID`）

**验证**：删除 ChromaDB collection → 检索接口自动降级到 SQLite → 后台重建 collection → `compensation_tasks` 记录完整生命周期。

---

### Step 11: 存量迁移

| 项 | 内容 |
|:---|:---|
| **依赖** | Step 1-10（所有新表/新逻辑就绪） |
| **产出** | 存量数据从 ChromaDB 完整迁移到 SQLite |
| **文件** | 一次性脚本（`scripts/migrate_chroma_to_sqlite.py`） |

**具体操作**：
1. **API 文档迁移**：遍历 ChromaDB `api_defs` collection → `json.loads(page_content)` → 写入 `documents.api_*` 列
2. **产品/Axure 迁移**：遍历 ChromaDB `product_docs` collection → 写入 `document_chunks.content`（page_content 作为原文）
3. **批量生成摘要**：对迁移后的存量 chunks 调用 `_batch_generate_summaries()`（走 Step 5 的批量摘要逻辑，失败走补偿）
4. **ChromaDB 重建**：用迁移后的 SQLite 数据重建 `doc_search` collection

**验证**：迁移前后数据量一致（`documents` 行数 = 原 `api_defs` 条数；`document_chunks` 行数 = 原 `product_docs` 条数）。

**回滚**：保留旧 ChromaDB collection 不删除，确认迁移成功后手动删除。

---

### Step 12: 测试验证

| 项 | 内容 |
|:---|:---|
| **依赖** | Step 1-11 |
| **产出** | 测试报告 |

**测试清单**：

| 编号 | 测试项 | 类型 | 验收标准 |
|:---|:---|:---|:---|
| T1 | `Document` 新列读写 | 单元 | `api_*` 列 + `content_hash` 正常存取 |
| T2 | `DocumentChunk` CRUD | 单元 | 原文+摘要+标签 正常存取 |
| T3 | `CompensationTask` 去重 | 单元 | 并发 INSERT 同一 doc_id 只成功一条 |
| T4 | `CompensationTask` 僵尸清理 | 单元 | 修改 created_at 模拟超时 → 标 failed |
| T5 | API 入库端到端 | 集成 | 上传 API 文档 → SQLite + ChromaDB 均有数据 |
| T6 | 产品文档入库端到端（含降级） | 集成 | 正常 → 摘要生成；LLM 失败 → 降级入库 + 补偿任务 |
| T7 | 批量摘要解析 | 单元 | 正则解析 `===CHUNK_SUMMARY===` 正确拆分 |
| T8 | 三步分析管线 | 集成 | 绑定的模块 → 分析 → 三个 Text 列有内容 |
| T9 | 三步分析 hash 跳过 | 单元 | 相同 bindings_hash → 跳过；变更后 → 重新分析 |
| T10 | Step 4 ChromaDB 回写 | 集成 | 分析后 ChromaDB `source` 变为 `analyzed_summary` |
| T11 | 检索优先级（有分析） | 集成 | Phase B 使用分析文本，Token 减少 |
| T12 | 检索降级（无分析） | 集成 | 自动降级到 ChromaDB+SQLite 原文路径 |
| T13 | ChromaDB 损坏补偿（全局） | 集成 | 删除 collection → 即时降级 + 后台全局重建 |
| T14 | ChromaDB 损坏补偿（单条） | 集成 | page_content 为空 → 单文档补偿 |
| T15 | Annotations 编辑一致性 | 集成 | 编辑 → SQLite 更新 + ChromaDB 异步重建 |
| T16 | 回归：智慧用电各批次 | 回归 | Phase B/C 无新增失败 |
| T17 | 存量迁移 | 集成 | 迁移前后数据量一致 |
| T18 | 端到端：上传→入库→绑定→分析→检索 | E2E | 完整链路无报错 |

---

### Step 13: 清理死代码

| 项 | 内容 |
|:---|:---|
| **依赖** | Step 12 全部测试通过 |
| **产出** | 删除旧分析相关的函数和 prompt，清理文件 |

**具体操作**：

| # | 文件 | 删除项 | 原因 |
|:---|:---|:---|:---|
| 13.1 | `prompts/extraction_prompts.py` | `analyze_module_scenarios_prompt()` | 被三步分析 prompt（4.1-4.3）替代 |
| 13.2 | `prompts/extraction_prompts.py` | `format_module_scenarios_prompt()` | 同上 |
| 13.3 | `web/tasks.py` | `_analyze_module_scenarios_bg()` | 被 `_analyze_module_scenarios_3step_bg()` 替代 |
| 13.4 | `database/models.py` | `ModuleAnalysis.analysis_json` 列 — SQLite 不支持 DROP COLUMN，**保留列不填充**，ORM 移除字段映射 | 已被三个 Text 列替代，确认无消费者后移除 ORM 映射 |
| 13.5 | `agent_components/dual_chroma.py` | `COLLECTION_PRODUCT_DOCS` / `COLLECTION_API_DEFS` 相关常量 | 已合并为 `COLLECTION_DOC_SEARCH` |
| 13.6 | `settings.py` | `COLLECTION_PRODUCT_DOCS` / `COLLECTION_API_DEFS` 配置 key | 同上 |

**验证**：全局搜索 `analysis_json`、`analyze_module_scenarios_prompt`、`format_module_scenarios_prompt`、`_analyze_module_scenarios_bg` 无引用。
