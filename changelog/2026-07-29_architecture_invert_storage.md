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
  │  → 每块生成精细总结 + 关联描述                 │
  │                                              │
  │ ChromaDB                                     │
  │  → 用分析结果重建检索文本（覆盖阶段1的简单摘要）│
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

### 2.3 迁移策略

- `init_db()` 中 `Base.metadata.create_all()` 自动加列+建表
- 存量数据：一次性脚本从 ChromaDB 回灌到 SQLite
- 新增数据：直接写入 SQLite 列/表

---

## 三、ChromaDB 检索文本构造

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

### Phase 3: 阶段1 — 原文入库（产品/Axure）

6. `ingest_v2.py` — `process_product_doc()` 切块原文写入 `document_chunks.content`
7. `ingest_v2.py` — `process_axure_zip()` 同上
8. `ingest_v2.py` — 入库后生成 `simple_summary`（200字内），写入 SQLite + ChromaDB
9. `prompts/extraction_prompts.py` — 新增 chunk 简单摘要 prompt

### Phase 4: 阶段2 — 绑定后整体分析

10. `web/tasks.py` — `_analyze_module_scenarios_bg()` 改为：
    - 从 SQLite `document_chunks` 读所有绑定文档的原文
    - LLM 整体分析 → 更新 `module_analysis` 表
    - 为每个 chunk 生成 `analyzed_summary` + `analyzed_tags` → 写入 `document_chunks`
    - 用分析结果重建 ChromaDB 检索文本（覆盖阶段1的简单摘要）
11. `web/routes/modules.py` — `update_api_annotations` 触发对应 chunk 的 ChromaDB 重建

### Phase 5: 阶段3 — 检索优先级 + 读取路径

12. `agent_components/retrievers.py` — 优先用 `module_analysis`，降级走 ChromaDB + SQLite
13. `web/routes/modules.py` — `get_module_api_defs` 改为读 SQLite
14. `web/routes/docs.py` — `/chunks` 端点改为读 SQLite `document_chunks`
15. `agent_components/generators/__init__.py` — `api_defs_json` 来源改为 SQLite

### Phase 6: ChromaDB 退化

16. `agent_components/dual_chroma.py` — 写入方法只存检索文本+指针
17. `agent_components/dual_chroma.py` — 读取方法只返回 doc_id/chunk_index

### Phase 7: 存量迁移 + 验证

18. 一次性脚本：从 ChromaDB 回灌存量数据到 SQLite
19. 单元测试 + 回归测试
20. 端到端验证：上传 → 绑定 → 分析 → 检索 完整链路

---

## 六、风险评估

| 风险 | 缓解 |
|:---|------|
| 存量数据丢失 | 先跑迁移脚本，验证后再切代码 |
| ChromaDB 检索质量下降 | 检索文本比原文/JSON 更自然，质量应提升 |
| LLM 摘要成本 | 仅产品/Axure 文档入库时生成一次，Phase B 复用时不再调 LLM |
| SQLite 大文本性能 | SQLite TEXT 类型支持 GB 级别，切块单条 < 10KB |
| 上下游兼容性 | 一级一级切，每级独立验证 |

---

## 七、决策记录

| # | 决策点 | 结论 | 理由 |
|:---|--------|------|------|
| 1 | 正文放哪 | SQLite（`documents` 列 + `document_chunks` 表） | 事务安全、SQL 可查、可备份 |
| 2 | ChromaDB 存什么 | API: 检索文本；产品/Axure: LLM 摘要 | 纯搜索引擎，可重建 |
| 3 | 检索文本格式 | 结构化自然语言（非 JSON） | 比 JSON/原文 更利于语义匹配 |
| 4 | 产品/Axure 摘要 | 入库时 LLM 生成一次 | 不增加 Phase B 成本 |
| 5 | 存量迁移 | 一次性脚本 | 避免双写复杂度 |
| 6 | 前端接口编辑 | 直接改 SQLite 列 | 不走 ChromaDB，事务安全 |
| 7 | 文档 chunk 原文 | 单独建表 `document_chunks` | 1:N 关系，独立管理，不污染 `documents` 表 |
| 8 | 入库时分析 | **不做**，只存原文+简单摘要 | 快速入库，不阻塞上传流程 |
| 9 | 分析时机 | 文档绑定到模块后，用户手动触发 | 用户可控，批量分析更准 |
| 10 | 检索优先级 | 分析结果 > 精细总结 > 简单摘要 > 原文 | 有分析用分析，没分析也能用 |
| 11 | 分析后 ChromaDB | 重建检索文本，覆盖阶段1简单摘要 | 语义检索始终用最佳文本 |
