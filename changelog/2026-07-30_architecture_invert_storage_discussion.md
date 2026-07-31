# 存储架构翻转 — 补充讨论（2026-07-30）✅ 已归档

> **归档日期**：2026-07-31
> **状态**：全部疑问已解决，决策已回写到主文档 `2026-07-29_architecture_invert_storage.md`
>
> | 疑问 | 最终决议 | 主文档位置 |
> |------|---------|-----------|
> | #5 LLM摘要失败方案 | 方案B降级入库 | 决策 #18-21, Phase 3 |
> | #6 批量摘要Prompt | 增强版, 5 chunks/批, 分隔词解析, 全局并发5 | 决策 #22-27 |
> | #8 分析Prompt策略 | 原文全量(方案A), 自由文本输出, 三步串行 | 决策 #28-34 |
> | #10 损坏补偿数据源 | SQL提取数据损坏→报异常; 分析后文件=LLM输出 | 决策 #35 |
> | #11 损坏检测触发 | 三种都触发 + 去重(pending任务已存在则跳过) | 决策 #36, Phase 6.1 |
> | #12 模块关联关系 | 已有双向表, 逻辑已存在, 无需改动 | — |
> | #13 metadata补充字段 | `source` 已加; `analyzed_at` 在 document_chunks; `is_dirty` 由 source 覆盖 | 第三章, 决策 #19 |

---

## 讨论 #5：LLM 摘要失败时的入库与保存策略

### 背景

你的回复：「失败参考回复 #3，（保存方案再讨论下）」

与 #3（annotations 编辑）的关键差异：**#3 是用户主动操作，可同步等待；#5 是上传流程的异步步骤。**

### 场景

```
上传文档 → 切块 → 调 LLM 生成 simple_summary → 失败
```

此时切块原文已可写入 `document_chunks.content`（不需要 LLM），只有 `simple_summary` 和 ChromaDB 检索文本无法生成。

### 方案对比

| 方案 | 描述 | 优劣 |
|------|------|------|
| **A. 部分入库** | 原文入库成功即返回，`simple_summary` 留空，ChromaDB 暂不写入该 chunk，异步补偿任务补齐 | 上传不阻塞；但补偿完成前该 chunk **无法被语义检索命中** |
| **B. 降级入库（推荐）** | 原文入库 + ChromaDB 写入 `content` 前 500 字作为临时检索文本，异步补偿任务生成摘要后覆盖 | 立即可检索；ChromaDB 暂存截断原文属于**可接受的临时态**，补偿任务完成后覆盖 |
| **C. 同步阻塞** | 上传接口同步等待 LLM 摘要 + 重试，全部成功才返回 | 用户体验差，不推荐 |

### 推荐方案 B 的详细流程

```
上传文档 → 切块
  ├─ 成功：原文写入 document_chunks.content
  │       ├─ LLM 生成摘要成功 → simple_summary 写入 SQLite
  │       │                     → 摘要文本写入 ChromaDB
  │       └─ LLM 生成摘要失败 → simple_summary 留空
  │                            → 降级：content 前 500 字写入 ChromaDB ← 临时态
  │                            → 创建补偿任务入队
  │
  └─ 补偿任务（后台异步）：
       1. 重试 LLM 生成摘要
       2. 成功 → 更新 SQLite simple_summary + 覆盖 ChromaDB 检索文本
       3. 失败 → 10 分钟后重试一次
       4. 再次失败 → 报异常升级，走损坏补偿逻辑（从原文件重新提取）
```

### 待确认

1. **是否同意方案 B（降级入库）？**
2. **降级时 ChromaDB 写入原文截断，是否标记 "dirty" 状态以便追踪哪些 chunk 等待补偿？**
3. **补偿任务的持久化方式**：内存队列？数据库任务表？Celery/Redis？

---

## 讨论 #6：批量摘要的 Prompt 设计与并发控制

### Prompt 结构

#### 基础版

```
请为以下文本块分别生成一句话摘要（50 字以内），以 JSON 字符串数组格式返回：
["摘要1", "摘要2", ...]

[块1] {content}
[块2] {content}
...
```

#### 增强版（带上下文）

```
以下是文档《{file_name}》的连续文本块，第 {i}-{j} 块 / 共 {total} 块。
每块位于页面「{page_name}」。

[块1] {content}
[块2] {content}
...

请为每个文本块生成一句摘要（50字以内），概括核心内容。
严格返回 JSON 字符串数组：["摘要1", "摘要2", ...]
```

### 待讨论

1. **是否需要增强版（带 page_name + chunk 序号上下文）？** 还是基础版足够？
2. **每批 chunks 数量**：5 个还是 10 个？取决于单 chunk 平均长度。
3. **JSON 解析容错**：LLM 返回格式异常时怎么办？
   - 逐条正则匹配回退？
   - 整批重试（同一批再调一次）？
   - 部分成功部分标记失败走补偿？

### 并发控制

```
LLM 并发限制：5 个并发请求
每批 5-10 个 chunk → 调用一次 LLM

示例：200 个 chunk，每批 10 个 = 20 批
      并发 5 → 4 轮，每轮 ~3 秒 = 约 12 秒
```

### 待确认

1. **5 个并发是指全局 LLM 并发池的上限？** Phase 2 分析、Phase B 生成用例是否共享这个池？
2. **同步等待 vs 全部异步**：
   - 同步等待（上传接口等摘要完成）：上传耗时 10-20 秒，可接受？
   - 全部异步（上传即返回，后台生成摘要）：上传瞬间完成，但摘要完成前检索质量下降
   - **当前设计倾向**：你说的"同步等，异步补偿"——同步等成功，失败走异步补偿

### 摘要 Prompt 模板存放

如果采用增强版，Prompt 模板放在哪里？
- `prompts/extraction_prompts.py`（文档 Phase 3 Step 9 已提到）
- 需要新增 `BATCH_CHUNK_SUMMARY_PROMPT` 模板

---

## 讨论 #8：模块分析时数据提供 Prompt 的方式

### 核心问题

Phase 2「绑定后整体分析」：用户点击"分析测试场景" → LLM 读取该模块所有绑定文档 → 场景分析 + 接口映射。

**如何将文档数据组织成 Prompt 喂给 LLM？**

### 已确认的前提

- 对**单个模块**的已绑定文档做分析
- Hash 版本控制：`content_hash` 不变 + `module_analysis` 存在 → 直接返回，零成本
- 解绑后清空 `module_analysis`，重新绑定后重新分析
- 已有分析过的作为参考，不覆盖

### 待讨论：Prompt 组装策略

```
┌─────────────────────────────────────────────────────────────┐
│ 方案 A：全量原文灌入                                         │
│                                                             │
│ 把所有绑定文档的全部 chunk 原文直接拼入 Prompt。               │
│                                                             │
│ 优点：信息完整，LLM 不会漏掉任何细节。                        │
│ 缺点：3 个文档 × 200 chunks × 500-2000 字 ≈ 30万-120万字    │
│       Token 消耗大，可能超出上下文窗口。                      │
├─────────────────────────────────────────────────────────────┤
│ 方案 B：simple_summary 预筛选                                 │
│                                                             │
│ 先用 simple_summary 做一层筛选：                              │
│   - 全部 simple_summary 拼入（200 words × N chunks）         │
│   - 让 LLM 先识别哪些 chunk 与测试场景相关                    │
│   - 再把相关 chunk 的原文二次送入                             │
│                                                             │
│ 优点：Token 省。缺点：两轮调用，摘要不准会漏信息。             │
├─────────────────────────────────────────────────────────────┤
│ 方案 C：分层注入（推荐）                                      │
│                                                             │
│ 第一层：模块名 + 模块描述 + 文档概览                          │
│ 第二层：每个文档的 chunk 摘要列表（simple_summary）           │
│ 第三层：chunk 原文（按文档分组，带 page_name）                │
│                                                             │
│ 如果原文总量 > 阈值（如 50K tokens），自动降级：               │
│   - 保留全部摘要 + 只送前 N 个 chunk 的原文                   │
│   - Prompt 注明 "原文过长，以下为部分内容"                    │
│                                                             │
│ 优点：兼顾完整性和 Token 控制。                               │
└─────────────────────────────────────────────────────────────┘
```

### 待讨论：旧分析作为参考的注入方式

> 你的回复："如果已有分析过，提供作为分析参考，不覆盖"

**场景**：用户解绑旧文档 → 绑定新文档 → 点击分析 → 此时旧 `module_analysis` 存在。

旧分析如何注入到 Prompt 中？

```
## 历史分析结果（仅供参考，文档已更新，请以当前文档为准）

### 历史场景列表
{旧 module_analysis.scenarios}

### 历史接口映射
{旧 module_analysis.api_mappings}

---

## 当前绑定文档内容（以此为准）

文档1《xxx》：
  [Chunk 1 - 页面: xxx] {content}
  ...

文档2《yyy》：
  ...
```

**待确认**：
1. 旧分析是**内联**到 Prompt（如上面），还是作为**独立的 system message**？
2. "不覆盖"是指 LLM **不更新** `module_analysis` 表（只读旧结果），还是指**生成新结果**但旧结果作为参考保留？
   - 根据你的描述（解绑后清空重新分析），应该是后者：解绑→清空→重新绑定→全量重新分析→生成新的 `module_analysis`

### 待讨论：分析后 chunk 级别 `analyzed_summary` 的更新

模块分析完成后，LLM 输出包含每个 chunk 的 `analyzed_summary`。如果同一文档后来被绑定到另一个模块：

```
文档 D 绑定到模块 A → 分析 → analyzed_summary_A 写入 document_chunks
文档 D 绑定到模块 B → 分析 → analyzed_summary_?
```

- **覆盖**：B 的分析结果覆盖 A 的 `analyzed_summary`？
- **追加/融合**：把 A 的 `analyzed_summary` 作为参考给 LLM，产出一个融合版？
- **独立存储**：`analyzed_summary` 不存 `document_chunks`，改存 `module_analysis` 内部？

**推荐**：分析后 `analyzed_summary` 属于 chunk 级别，应被最新分析覆盖（因为分析是针对"当前绑定集合"的，最新分析代表最新的理解）。旧模块的 `module_analysis` 在解绑时已被清空，不会有冲突。

### 待讨论：Versioning（content_hash）的存储位置

> 你提到在 `documents` 表中增加 `content_hash` 字段。

1. Hash 计算的是什么？
   - 单个文档所有 chunk 的 `content` 拼接后的 MD5/SHA256？
   - 还是文档原始文件（上传文件）的 hash？
2. Hash 存储在哪张表？
   - `documents.content_hash`：粒度是单个文档
   - `module_analysis.content_hash`：粒度是"该模块当前绑定文档集合"（多文档聚合 hash）
   - **推荐**：两者都要——`documents.content_hash`（文档级，感知文档更新）+ `module_analysis.bindings_hash`（模块绑定集合级，感知绑定变更）

---

## 新疑问 #10：损坏补偿中"原文件"的定位

你的回复：「从原文件或分析后文件提取原文，分析后文件优先」

### 问题

1. **"原文件"是指什么？**
   - 用户上传的原始文件（`uploads/xxx.docx`、`uploads/xxx.zip`）？
   - 还是 SQLite `document_chunks.content`？
2. **原文件是否永久保留在磁盘上？**
   - 如果有过期清理策略（磁盘空间），补偿任务可能找不到原文件
3. **"分析后文件"是指什么？**
   - `document_chunks.analyzed_summary`？
   - 还是 `module_analysis` 表的 JSON？

### 建议明确数据源优先级

```
损坏补偿数据源优先级（从高到低）：
  1. document_chunks.analyzed_summary  ← "分析后文件"
  2. document_chunks.simple_summary
  3. 磁盘上的原始上传文件（如 uploads/xxx.docx）  ← "原文件"
  4. document_chunks.content  ← SQLite 中的切块原文
```

---

## 新疑问 #11：ChromaDB 损坏检测机制

### 问题

"使用该文件时，如果 db 部分为空"——检测发生在哪一层？

| 检测点 | 触发条件 | 适用场景 |
|--------|---------|---------|
| `retrievers.py` 检索返回空结果 | ChromaDB 查询正常但无匹配 | collection 为空 / 数据未入库 |
| ChromaDB 连接失败 | 抛异常（网络/文件损坏） | ChromaDB 服务挂了 / 文件损坏 |
| ChromaDB 查询成功但 `page_content` 为空 | 返回了 doc_id 但内容丢失 | 数据不完整 |

### 待确认

1. **三种场景是否都触发补偿？** 还是只触发后两种（真正的"损坏"）？
2. **检测逻辑放在哪个模块？** 建议封装在 `agent_components/dual_chroma.py` 中作为 `_ensure_healthy()` 方法。

---

## 新疑问 #12：模块关联关系的存储

你的回复：「a 模块关联 b、c 模块时，对 b、c 模块内和 a 模块相关内容检索」

### 问题

1. **模块之间的关联关系存在哪张表？**
   - 目前代码里有这个模型吗？
   - 还是本次需要新建（如 `module_relations` 表）？
2. **关联关系的类型**：
   - 单向还是双向？（a 关联 b，b 是否自动关联 a？）
   - 关联类型枚举（如 `depends_on` / `related_to` / `extends`）？
3. **检索时的行为**：
   - 检索模块 a → ChromaDB 过滤 `module_name IN (a, b, c)`？
   - 还是先查关联模块列表，再逐个检索？

---

## 新疑问 #13：单 Collection 的 metadata 补充字段

已确认 metadata 基础结构：

```python
{
    "doc_id": "xxx",
    "doc_type": "api" | "product" | "axure",
    "chunk_index": 0,           # 仅 product/axure
    "page_name": "",            # 仅 axure
}
```

### 待确认：是否需要以下额外字段

| 字段 | 用途 | 建议 |
|------|------|------|
| `analyzed_at` | 标记检索文本的新旧程度 | 可选，调试用 |
| `source` | 检索文本来源：`simple_summary` / `analyzed_summary` / `content_fallback` | **建议加**，便于追踪补偿状态 |
| `module_names` | 该 chunk 所属文档被绑定的模块列表 | 复杂，建议通过 SQLite 关联查询而非冗余存 ChromaDB |
| `is_dirty` | 标记降级入库，等待补偿任务覆盖 | 与 #5 讨论相关：如果选了方案 B |

---

## 汇总：待用户决策清单

| 编号 | 问题 | 紧迫度 |
|------|------|--------|
| #5 | LLM 摘要失败方案：A / B / C？降级标记方式？补偿任务持久化方式？ | 🔴 高 |
| #6 | 摘要 Prompt：基础版 vs 增强版？每批 chunk 数？JSON 解析容错策略？并发池是否共享？同步 vs 全部异步？ | 🔴 高 |
| #8 | 分析 Prompt：A/B/C 哪个方案？旧分析注入方式（内联 vs system message）？`analyzed_summary` 覆盖策略？`content_hash` 粒度（文档级 vs 绑定集合级）？ | 🔴 高 |
| #10 | 损坏补偿数据源优先级确认 | 🟡 中 |
| #11 | ChromaDB 损坏检测的三种场景是否都触发补偿？ | 🟡 中 |
| #12 | 模块关联关系的表结构和检索行为 | 🟡 中 |
| #13 | ChromaDB metadata 是否加 `source`、`is_dirty` 等字段 | 🟢 低 |
