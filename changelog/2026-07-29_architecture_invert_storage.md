# 存储架构翻转：SQLite 存正文，ChromaDB 退化为纯检索引擎

| 项目 | 内容 |
|:---|:---|
| 讨论日期 | 2026-07-29 |
| 变更类型 | 架构重构 — 数据存储层翻转 |
| 涉及文件 | `database/models.py`, `ingest_v2.py`, `agent_components/dual_chroma.py`, `web/routes/modules.py`, `web/routes/docs.py`, `prompts/response_model.py`, `agent_components/generators/__init__.py`, `agent_components/retrievers.py` |

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
  │ ChromaDB                                     │
  │  page_content: 原文简单摘要（或 API 检索文本）│
  │  metadata: {doc_id}                          │
  └─────────────────────────────────────────────┘

阶段 2: 绑定后整体分析（前端按钮触发，见 2026-07-26.md）
  ┌─────────────────────────────────────────────┐
  │ 用户绑定文档到模块 → 点击"分析测试场景"       │
  │                                              │
  │ LLM 读取该模块所有已绑定文档的原文              │
  │  → 场景分析 + 接口映射                        │
  │  → 存入 module_analysis 表                   │
  └─────────────────────────────────────────────┘

阶段 3: 检索（Phase B 使用）
  ┌─────────────────────────────────────────────┐
  │ 优先路径：module_analysis 存在               │
  │  → 用分析后的结构化数据（场景+接口映射）       │
  │  → Token 少、精准                            │
  │                                              │
  │ 降级路径：module_analysis 不存在              │
  │  → ChromaDB 语义检索拿 doc_id               │
  │  → SQLite 取原文（阶段1的原始数据）           │
  └─────────────────────────────────────────────┘
```

**核心原则**：
```
检索优先级:  分析结果 > 简单摘要 > 原文
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
```

### 2.2 `document_chunks` 表 — product/axure 切块原文（新增）

三阶段数据：原文 → 简单摘要。`analyzed_summary` / `analyzed_tags` / `analyzed_at` 预留，当前不填充。

```python
class DocumentChunk(Base):
    """文档切块：原文 + 阶段1简单摘要。

    与 documents 表通过 doc_id 关联（非外键，允许独立删除）。
    阶段1入库时填充 content + simple_summary。
    analyzed_summary / analyzed_tags / analyzed_at 预留，当前不填充。
    """
    __tablename__ = "document_chunks"

    id = Column(Integer, primary_key=True, autoincrement=True)
    doc_id = Column(String(200), nullable=False, index=True)
    chunk_index = Column(Integer, nullable=False)

    # ── 阶段1: 原文 + 简单摘要 ──
    content = Column(Text, nullable=False)           # 切块原文
    simple_summary = Column(Text, default="")        # 入库时 LLM 生成简单摘要（200字内）

    # ── 预留字段（当前不填充）──
    analyzed_summary = Column(Text, default="")      # 分析后精细总结（预留）
    analyzed_tags = Column(Text, default="")         # JSON: ["标签1","标签2"]（预留）
    analyzed_at = Column(DateTime)                   # 分析时间（预留）

    # ── 元数据 ──
    chunk_type = Column(String(20), default="text")  # text | page | section
    page_name = Column(String(200), default="")      # Axure 页面名 / PDF 章节标题
    token_count = Column(Integer, default=0)
```

ChromaDB 检索文本来源（优先级从高到低）：
1. `simple_summary` 非空 → 用简单摘要
2. 都没有 → 取 content 前 500 字

### 2.3 迁移策略

- `init_db()` 中 `Base.metadata.create_all()` 自动加列+建表
- 新增数据：直接写入 SQLite 列/表

---

## 三、ChromaDB 检索文本构造（单一 Collection）

合并为一个 collection（如 `doc_search`），靠 `metadata.doc_type` 区分 `api` / `product` / `axure`。

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

- API：`ingest_v2.py` `commit_api_docs()` → 新函数 `_build_api_search_text(api)`
- 产品/Axure：`ingest_v2.py` `process_product_doc()` / `process_axure_zip()` → LLM 生成摘要后写入 `document_chunks.summary`，再构造检索文本

---

## 四、涉及文件及改动

### 4.1 `database/models.py`

| 改动 | 说明 |
|:---|------|
| `Document` 类加 8 列 | `api_name` ~ `api_annotations` |
| 新增 `DocumentChunk` 类 | 切块原文+摘要表 |

### 4.2 `ingest_v2.py`

| 函数 | 改动 |
|:---|------|
| `commit_api_docs()` | 写入 SQLite `api_*` 列；构造检索文本写入 ChromaDB |
| `process_product_doc()` | 切块原文写入 `document_chunks`；LLM 生成摘要；构造检索文本写入 ChromaDB |
| `process_axure_zip()` | 同上，`page_name` 填 Axure 页面名 |
| 新增 `_build_api_search_text(api)` | 构造 API 检索文本 |
| 新增 `_build_doc_search_text(chunk)` | 构造产品/Axure 检索文本 |
| `_safe_doc_id()` | 增加 `<` `>` `"` `&` 等 HTML 字符清理 |

### 4.3 `agent_components/dual_chroma.py`

| 方法 | 改动 |
|:---|------|
| `add_api_defs()` | page_content 改为检索文本 |
| `add_product_doc_chunks()` | page_content 改为摘要文本 |
| `get_doc_apis()` | 返回值改为从 SQLite 查正文 + ChromaDB 只补 doc_id |
| `get_doc_chunks()` | 返回值改为从 SQLite `document_chunks` 查原文 |

### 4.4 `web/routes/modules.py`

| 端点 | 改动 |
|:---|------|
| `GET /{name}/api-defs` | 直接从 SQLite `api_*` 列查，不走 ChromaDB |
| `PUT /{name}/api-defs/{index}/annotations` | 更新 SQLite `api_annotations` + 重建 ChromaDB 检索文本 |

### 4.5 `web/routes/docs.py`

| 端点 | 改动 |
|:---|------|
| `GET /{doc_id}/chunks` | 从 SQLite `document_chunks` 查原文，不走 ChromaDB |

### 4.6 `agent_components/retrievers.py`

| 方法 | 改动 |
|:---|------|
| 所有检索方法 | 先从 ChromaDB 语义检索拿 doc_id，再从 SQLite 查正文 |

### 4.7 `prompts/response_model.py`

| 改动 |
|:---|
| `ApiDefinition` model 无需改动（已经兼容新旧格式） |

### 4.8 `agent_components/generators/__init__.py`

| 方法 | 改动 |
|:---|------|
| `_generate_one_yaml()` | `api_defs_json` 来源从 ChromaDB 改为 SQLite |

---

## 五、实施步骤

### Phase 1: Schema 变更

1. `database/models.py` — `Document` 加 8 列 + 新增 `DocumentChunk` 表
2. `init_db()` 测试 — 验证自动迁移

### Phase 2: 阶段1 — 原文入库（API）

3. `ingest_v2.py` — `commit_api_docs()` 填充 `api_*` 列
4. `ingest_v2.py` — 新增 `_build_api_search_text()`
5. `ingest_v2.py` — ChromaDB 写入改为检索文本（阶段1版本）

### Phase 3: 阶段1 — 原文入库（产品/Axure）+ 补偿体系

6. `database/models.py` — `DocumentChunk` 表新增 `simple_summary`、`analyzed_summary`、`analyzed_tags`、`analyzed_at`、`page_name`、`token_count` 列
7. `database/models.py` — 新增 `CompensationTask` 表（讨论5）
8. `database/operations/compensation.py` — 新建，补偿任务 CRUD
9. `ingest_v2.py` — `process_product_doc()` / `process_axure_zip()`：
    - 切块原文写入 `document_chunks.content`
    - 批量调 LLM 生成 `simple_summary`（5 chunks/批，同步等待，`===CHUNK_SUMMARY===` 分隔词 + 正则解析）
    - LLM 摘要失败 → 降级入库（content 截断写入 ChromaDB，`source: content_fallback`）→ 创建 `compensation_tasks` 记录
10. `prompts/extraction_prompts.py` — 新增 `batch_chunk_summary_prompt()`（讨论6）
11. `web/tasks.py` — `_MAX_WORKERS` 10→9；新增 `_compensation_worker()` 独立轮询线程（讨论5）
12. `settings.py` — 新增 `llm_global_concurrency=5`、`batch_summary_chunk_size=5`、`compensation_poll_interval`、`compensation_max_retries`

### Phase 4: 阶段2 — 三步分析管线（替代原有 `_analyze_module_scenarios_bg`）

10. `web/tasks.py` — 新增 `_analyze_module_scenarios_3step_bg(task_id, module_name)`：
    - Step 1: 从 `document_chunks` 读所有 product 类型 chunk 原文 → LLM thinking → 写入 `scenario_analysis`
    - Step 2: Step1 输出 + 所有 axure 类型 chunk 原文 → LLM thinking → 写入 `ui_flow_analysis`（无 Axure 跳过）
    - Step 3: Step1+Step2 输出 + 所有 API 定义（SQLite `documents.api_*` 列）→ LLM thinking → 写入 `api_analysis`（无 API 跳过）
    - 每步独立心跳 + 进度上报
11. `prompts/extraction_prompts.py` — 新增三个 prompt：
    - `analyze_product_scenarios_prompt()` — Step 1
    - `analyze_axure_ui_flow_prompt()` — Step 2
    - `analyze_api_mapping_prompt()` — Step 3
12. `web/app.py` — 端点 `POST /api/module/{name}/analyze-scenarios` 改为调用三步管线
13. `agent_components/retrievers.py` — Phase B 消费改为拼接三个 Text 列（兼容旧 `analysis_json`）

### Phase 5: 阶段3 — 检索优先级 + 读取路径

12. `agent_components/retrievers.py` — 优先用 `module_analysis`，降级走 ChromaDB + SQLite
13. `web/routes/modules.py` — `get_module_api_defs` 改为读 SQLite
14. `web/routes/docs.py` — `/chunks` 端点改为读 SQLite `document_chunks`
15. `agent_components/generators/__init__.py` — `api_defs_json` 来源改为 SQLite

### Phase 6: ChromaDB 退化

16. `agent_components/dual_chroma.py` — 写入方法只存检索文本+指针
17. `agent_components/dual_chroma.py` — 读取方法只返回 doc_id/chunk_index

### Phase 7: 验证

18. 回归测试
19. 端到端验证：上传 → 绑定 → 分析 → 检索 完整链路

---

## 六、风险评估

| 风险 | 缓解 |
|:---|------|
| ChromaDB 检索质量下降 | 检索文本比原文/JSON 更自然，质量应提升 |
| LLM 摘要成本 | 仅产品/Axure 文档入库时生成一次，Phase B 复用时不再调 LLM |
| SQLite 大文本性能 | SQLite TEXT 类型支持 GB 级别，切块单条 < 10KB |
| 上下游兼容性 | 一级一级切，每级独立验证 |

---

## 七、ChromaDB Collection 结构变更（2026-07-30 讨论确认）

### 7.1 合并为单一 Collection

`product_docs` 和 `api_defs` 合并为一个 collection（如 `doc_search`），靠 metadata 区分类型：

```python
# ChromaDB metadata 结构
{
    "doc_id": "xxx",
    "doc_type": "api" | "product" | "axure",
    "chunk_index": 0,           # 仅 product/axure
    "page_name": "",            # 仅 axure
}
```

**理由**：跨类型的语义检索更自然，管理和重建更简单。

### 7.2 ChromaDB 损坏补偿机制

```
┌─────────────────────────────────────────────────────────────┐
│ 触发条件：检索 ChromaDB 返回空 / 连接失败 / collection 不存在  │
│                                                             │
│ 即时补偿（保证当前请求不卡死）：                               │
│   1. 从 SQLite document_chunks 提取 simple_summary 或原文      │
│   2. simple_summary 优先，其次 content 原文                     │
│   3. 用提取文本直接返回结果，请求继续                          │
│                                                             │
│ 后台补偿（异步任务）：                                        │
│   1. 创建 ChromaDB 重建任务入队                               │
│   2. 从 SQLite 全量重建 collection                           │
│   3. 检索文本优先级：simple_summary > content 前 500 字       │
└─────────────────────────────────────────────────────────────┘
```

**补偿数据源优先级**：`simple_summary` > `content` 原文

### 7.3 API Annotations 编辑一致性策略

```
以 SQLite 为准：
  编辑请求 → 更新 SQLite api_annotations 列 → 返回成功
                                         ↘ 异步：重建 ChromaDB 检索文本
                                            ├─ 成功 → 完成
                                            └─ 失败 → 创建重试任务（10 分钟后重试一次）
                                                     ├─ 成功 → 完成
                                                     └─ 失败 → 报异常升级，走损坏补偿逻辑
```

---

## 八、三步分析管线（2026-07-31 讨论确认）

替代原有的 `_analyze_module_scenarios_bg()` 一次性全量分析。三种文档类型按依赖关系**串行级联分析**，每步 thinking 模式、1 次 LLM 调用、输出自由文本。

### 8.1 管线流程

```
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
  无 API → 跳过，字段留空
```

### 8.2 `module_analysis` 表结构变更

```python
class ModuleAnalysis(Base):
    __tablename__ = "module_analysis"

    id = Column(Integer, primary_key=True, autoincrement=True)
    module_id = Column(String(36), ForeignKey("modules.id", ondelete="CASCADE"),
                       nullable=False, unique=True, index=True)
    module_name = Column(String(200), nullable=False)

    # ── 三步分析输出（自由文本）──
    scenario_analysis = Column(Text, default="")   # Step 1
    ui_flow_analysis = Column(Text, default="")    # Step 2
    api_analysis = Column(Text, default="")        # Step 3

    status = Column(String(20), default="draft")
    extracted_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    modified_at = Column(DateTime, ...)
    modified_by = Column(String(100), default="")
    version = Column(Integer, default=1)
```

**对比旧结构**：`analysis_json`（单一 JSON 列）拆为三个 Text 列，每步独立写入，Phase B 消费时按存在性拼接。

### 8.3 Phase B 消费方式

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

### 8.4 前置依赖

| 步骤 | 前置条件 | 状态 |
|------|---------|------|
| Step 1 | `document_chunks` 表存在 + `simple_summary` 已生成 | 依赖 Phase 3（讨论5/6） |
| Step 2 | Step 1 完成 + Axure 文档已入库 | 依赖 Step 1 |
| Step 3 | Step 1/2 完成 + `documents.api_*` 列已迁移 | **依赖 Phase 2（API 迁移）先完成** |

---

## 九、决策记录

| # | 决策点 | 结论 | 理由 |
|:---|--------|------|------|
| 1 | 正文放哪 | SQLite（`documents` 列 + `document_chunks` 表） | 事务安全、SQL 可查、可备份 |
| 2 | ChromaDB 存什么 | API: 检索文本；产品/Axure: LLM 摘要 | 纯搜索引擎，可重建 |
| 3 | 检索文本格式 | 结构化自然语言（非 JSON） | 比 JSON/原文 更利于语义匹配 |
| 4 | 产品/Axure 摘要 | 入库时 LLM 生成一次 | 不增加 Phase B 成本 |
| 5 | 前端接口编辑 | 直接改 SQLite 列 | 不走 ChromaDB，事务安全 |
| 6 | 文档 chunk 原文 | 单独建表 `document_chunks` | 1:N 关系，独立管理，不污染 `documents` 表 |
| 7 | 入库时分析 | **不做**，只存原文+简单摘要 | 快速入库，不阻塞上传流程 |
| 8 | 分析时机 | 文档绑定到模块后，用户手动触发 | 用户可控，批量分析更准 |
| 9 | 检索优先级 | 分析结果 > 简单摘要 > 原文 | 有分析用分析，没分析也能用 |
| 10 | 分析后 ChromaDB | 重建检索文本，覆盖阶段1简单摘要 | 语义检索始终用最佳文本 |
| 11 | ChromaDB 损坏 | 即时补偿（SQLite 取文本）+ 后台异步重建 | 当前请求不卡死，静默修复 |
| 12 | Annotations 编辑一致性 | SQLite 为准，ChromaDB 异步重建，失败重试 | 避免双写事务问题 |
| 13 | 检索优先级 | SQLite 关联关系 > ChromaDB 语义检索 | 有结构的用结构，没结构的用语义 |
| 14 | 检索结果验证 | ChromaDB 返回 doc_id 后必须从 SQLite 拿正文 | 正文以 SQLite 为唯一真相源 |
| 15 | Collection 结构 | 合并 product_docs + api_defs 为单一 collection | 跨类型语义检索，管理简化 |
| 16 | 文档 chunk 原文 | 单独建表 `document_chunks`，doc_id 不做外键 | 1:N 关系，合并为单 collection 后无孤儿问题 |
| 17 | 模块分析触发 | 单模块已绑定文档；hash 版本控制防重复 | 零成本重复点击；内容变更自动感知 |
| 18 | 模块解绑 | 解绑后清空 module_analysis，重新绑定后重新分析 | 不覆盖旧分析，直接重建 |
| 19 | LLM 摘要失败策略 | **方案 B — 降级入库**（讨论5） | 原文写入 SQLite + ChromaDB 写 content 截断作为临时检索文本；异步补偿补齐 |
| 20 | 降级标记 | `document_chunks` 不设 `is_dirty` 字段；通过 ChromaDB metadata `source` 字段区分（讨论5/13） | `source`: `simple_summary` / `content_fallback`；`content_fallback` = 待补偿 |
| 21 | 补偿任务持久化 | SQLite `compensation_tasks` 表（讨论5） | 重启不丢；独立 worker 线程轮询处理 |
| 22 | 补偿 worker 线程 | 全局线程池 `max_workers` 10→9，预留 1 个给独立补偿轮询线程（讨论5） | 补偿与正常任务隔离，互不阻塞 |
| 23 | 批量摘要 Prompt | **增强版**（讨论6） | 带 `file_name` + `page_name` + chunk 序号上下文 |
| 24 | 批量摘要每批 chunk 数 | **5 个**（讨论6） | 平衡 token 消耗与 LLM 调用次数 |
| 25 | 批量摘要输出格式 | LLM 输出 `===CHUNK_SUMMARY===` 分隔词，正则匹配解析（讨论6） | 不用 json_mode，格式容错性更好；解析失败时逐条正则回退 |
| 26 | LLM 全局并发上限 | **5 个**（讨论6） | 多个 API key 时可适当增加；Phase 2 分析、Phase B 生成共享此池 |
| 27 | 批量摘要等待方式 | **同步等待** + 前端进度展示（讨论6） | 失败部分写 `compensation_tasks` 异步补偿 |
| 28 | 摘要 Prompt 存放位置 | `prompts/extraction_prompts.py`（讨论6） | 新增 `batch_chunk_summary_prompt()` |
| 29 | 三步分析链路 | **产品文档→场景总结→Axure逻辑关系→接口总结**（2026-07-31讨论） | 分步分析，上一步输出作为下一步上下文 |
| 30 | 三步分析输出存储 | **分开存储**：`module_analysis` 表拆为三个 Text 列（讨论8） | `scenario_analysis` / `ui_flow_analysis` / `api_analysis`，每步独立写入 |
| 31 | 三步分析输出格式 | **自由文本**，thinking 模式，每步 1 次 LLM 调用，不限制输出格式（讨论8） | thinking 质量高；自由文本给 Phase B 更多上下文 |
| 32 | 三步分析输入 | **原文全量**，不做任何筛选（讨论8） | 不依赖尚未实现的 simple_summary |
| 33 | 缺失文档类型 | 直接跳过对应步骤，不在 prompt 中体现（讨论8） | 小概率事件，不增加 prompt 复杂度 |
| 34 | 三步分析替代现有分析 | 替代 `_analyze_module_scenarios_bg()` 的一次性分析（讨论8） | 新的三步后台任务覆盖旧逻辑 |
| 35 | Step 3 前置依赖 | 依赖 API 数据先完成 SQLite 迁移（`documents.api_*` 列） | Step 3 从 SQLite 读 API 定义，不调 ChromaDB |
