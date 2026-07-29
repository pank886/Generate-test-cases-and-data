# Phase B 入库预处理：测试骨架提取 + 推理时结构化调用

| 项目 | 内容 |
|:---|:---|
| 讨论日期 | 2026-07-26 |
| 变更类型 | 新增入库预处理节点 + `analyze_test_points_raw` 输入源切换 |
| 涉及文件 | `prompts/definitions.py`, `agent_components/retrievers.py`, `ingest_v2.py`（新增 prompt） |

---

## 一、问题背景

### 1.1 当前流程（推理时全量塞入）

```
用户输入 → confirm_intent → retrieve_product_docs → extract_related_modules
  → retrieve_related_data → analyze_test_points_raw（全量原始文档）
  → generate_excel_plan → END
```

`analyze_test_points_raw` 的 human prompt 注入三块数据：

| 变量 | 内容 | 规模 |
|------|------|------|
| `product_docs` | 产品文档全文（ChromaDB 检索） | 10~30 个 chunk，2~8 万 Token |
| `api_definitions` | 接口定义列表（ChromaDB 检索） | 30~80 个接口，1~3 万 Token |
| `related_docs` | 关联模块名（逗号分隔字符串） | 极短 |

单次调用 5~20 万 Token，且每次对话都重新分析同一批文档。

### 1.2 三大死穴

| 问题 | 后果 | 代码表现 |
|------|------|---------|
| **上下文爆炸** | Token 贵、易超限、LLM 丢上下文 | `retrievers.py:359-367` 三块全量文本注入 |
| **噪声干扰** | 文档中的 UI 描述、运营规则淹没接口契约，LLM 编造参数 | 文档写"用户点击按钮"但 API 没有对应字段 |
| **无结构复用** | 同一模块多次对话，每次重新读全文分析 | 对话间 0 缓存 |

---

## 二、方案：入库预处理 + 推理时结构化调用

### 2.1 两阶段架构

```
┌──────────────────────────────────────────────────────────────────┐
│ 阶段一（前端按钮触发）：场景分析 + 接口映射                         │
│                                                                  │
│ 用户点击"分析测试场景"按钮 ──→ 触发后台任务                        │
│   │                                                              │
│   ├── 该模块下全部产品文档 (product/axure)                         │
│   ├── 该模块下全部接口定义 (api) ← 绑定时已确认关联                 │
│   ├── 关联模块的接口/文档 (BindingOps) ← 跨模块依赖已解析          │
│   │                                                              │
│   ▼                                                              │
│   只做两件事（不生成用例，不分析测试内容）：                         │
│                                                                    │
│   ① 场景分析，提取测试点                                           │
│      例: 设备管理场景 → 添加/删除/修改/查询/导入/导出 6 个功能点   │
│                                                                  │
│   ② 接口分析，场景涉及哪些接口 + 数据流向                           │
│      例: A场景涉及 x, y, z 接口                                    │
│           x 接口产出 order_id → y 接口需要消费                     │
│           z 接口需要从上游获取 user_token                          │
│                                           │                      │
│                                           ▼                      │
│                              存入 SQLite module_analysis 表        │
│                              前端展示场景-接口映射图               │
└──────────────────────────────────────────────────────────────────┘
                               │
                               ▼
┌──────────────────────────────────────────────────────────────────┐
│ 阶段二（推理时）：Phase B 基于场景+接口映射生成用例                 │
│                                                                  │
│ 用户输入 ──→ 检索 module_analysis ──→ Phase B 生成用例             │
│              （场景+接口+数据流）      （不做重复分析）              │
│                                                                  │
│              Token: 每次 2000~5000（下降 10~30 倍）                │
└──────────────────────────────────────────────────────────────────┘
```

**阶段一只做两件事，不越界**：
- ✅ 场景分析：从文档中识别有哪些业务场景，每个场景有哪些功能点（测试点）
- ✅ 接口映射：每个功能点涉及哪些接口，接口间的数据依赖关系
- ❌ 不生成测试用例
- ❌ 不分析测试内容（参数值、断言、预期结果）

### 2.2 分析输出结构

阶段一只输出场景和接口映射，不输出任何与测试内容相关的东西（参数值、断言、预期结果）。

**关键设计**：每个 `test_point` 不只是名字，还带 `scope`（覆盖维度）。Phase B 拿到后不需要猜"这个功能点要测哪些类型"，直接按 scope 逐条展开。

```json
{
  "module_name": "设备管理-电表管理",
  "analyzed_at": "2026-07-26T10:00:00",
  "scenarios": [
    {
      "name": "电表管理",
      "description": "电表的增删改查、导入导出、分页查询",
      "test_points": [
        {
          "name": "新增电表（单一费率）",
          "scope": ["正向", "边界-编号最大长度", "反向-编号重复", "反向-必填字段缺失", "反向-SQL注入"]
        },
        {
          "name": "新增电表（分时电表）",
          "scope": ["正向", "反向-未填尖峰平谷读数", "边界-初始读数最大最小值"]
        },
        {
          "name": "修改电表信息",
          "scope": ["正向", "反向-修改已绑定计费方案的电表类型", "反向-修改不存在的电表"]
        },
        {
          "name": "删除电表",
          "scope": ["正向", "反向-删除已绑定计费方案的电表", "反向-重复删除"]
        },
        {
          "name": "分页查询电表列表",
          "scope": ["正向", "边界-pageSize=1", "边界-pageSize=10000"]
        },
        {
          "name": "查询电表详情",
          "scope": ["正向", "反向-查询不存在的电表"]
        },
        {
          "name": "导出电表列表",
          "scope": ["正向"]
        },
        {
          "name": "导入电表数据",
          "scope": ["正向", "反向-格式错误的文件", "反向-重复数据导入"]
        }
      ],
      "apis": [
        {
          "path": "/electricMeter/add",
          "method": "POST",
          "name": "新增电表",
          "produces": ["meter_code"],
          "consumes": []
        },
        {
          "path": "/electricMeter/update",
          "method": "POST",
          "name": "修改电表",
          "produces": [],
          "consumes": ["meter_code"]
        },
        {
          "path": "/electricMeter/delete",
          "method": "POST",
          "name": "删除电表",
          "produces": [],
          "consumes": ["meter_code"]
        },
        {
          "path": "/electricMeter/getEle/{code}",
          "method": "GET",
          "name": "查询电表详情",
          "produces": [],
          "consumes": ["meter_code"]
        }
      ],
      "data_flow": [
        {
          "from": {"api": "POST /electricMeter/add", "field": "meter_code"},
          "to": {"api": "POST /electricMeter/update", "field": "meter_code", "via": "json body"},
          "relationship": "新增产出的 code → 修改时作为请求参数传入"
        }
      ]
    }
  ],
  "cross_module_refs": [
    {
      "related_module": "计费规则",
      "relation_type": "数据约束",
      "impact": "电表被计费方案绑定后不可删除，修改类型前需校验绑定状态",
      "affected_test_points": [
        {"point": "删除电表", "scope_impact": "正向仅在无绑定时成立；新增反向场景：删除已绑定计费方案的电表"},
        {"point": "修改电表信息", "scope_impact": "新增反向场景：修改已绑定计费方案的电表类型"}
      ]
    }
  ]
}
```

**字段说明**：

| 字段 | 层级 | 说明 |
|------|------|------|
| `test_points[].name` | 场景 → 测试点 | 测试点名称 |
| **`test_points[].scope`** | 场景 → 测试点 → 覆盖维度 | **Phase B 直接据此展开用例**。每个元素是一个覆盖类型标签 |
| `apis[].produces` | 接口 → 产出 | 该接口产出的变量名（供下游消费） |
| `apis[].consumes` | 接口 → 消费 | 该接口需要从上游获取的变量名 |
| `data_flow` | 场景 → 数据流 | from/to 三段式描述 API 间的数据传递 |
| `cross_module_refs[].affected_test_points` | 跨模块 → 影响的测试点 | 关联模块对哪些测试点产生新的覆盖要求 |

**scope 标签规范**（LLM 提取时使用，Phase B 消费时展开）：

| 类别 | 标签示例 | Phase B 展开动作 |
|------|---------|-----------------|
| 正向 | `正向` | 生成 1 条全字段合法值的正常用例 |
| 边界 | `边界-编号最大长度`、`边界-pageSize=1` | 生成边界值用例，取标签中指定的具体边界 |
| 反向-业务 | `反向-编号重复`、`反向-已绑定不可删` | 生成业务规则冲突用例，预期 [ne] 或 [contains] 错误提示 |
| 反向-字段 | `反向-必填字段缺失`、`反向-格式错误` | 生成字段校验用例 |
| 安全 | `反向-SQL注入`、`反向-XSS` | 生成安全攻击向量用例 |

### 2.3 数据库存储格式

分析结果存入 SQLite，允许用户手动修改后再交给 Phase B。

**表结构**：

```sql
CREATE TABLE module_analysis (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    module_name   TEXT NOT NULL UNIQUE,          -- 模块名（如 "智慧用电"）
    analysis_json TEXT NOT NULL,                 -- 完整 JSON（§2.2 结构）
    status        TEXT DEFAULT 'draft',          -- draft | reviewed | approved
    extracted_at  TEXT,                          -- LLM 提取时间
    modified_at   TEXT,                          -- 最后手动修改时间
    modified_by   TEXT,                          -- 修改人（前端用户标识）
    version       INTEGER DEFAULT 1              -- 乐观锁版本号
);
```

**状态流转**：

```
draft ──→ reviewed ──→ approved
  │                      │
  └── 用户编辑后保存 ────┘
  │
  └── 用户点击"重新分析" → LLM 重新生成，覆盖旧 JSON，status 重置为 draft
```

**前端编辑接口**：

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/module/{name}/analyze-scenarios` | POST | 触发 LLM 分析（status=draft） |
| `/api/module/{name}/analysis` | GET | 读取当前 analysis JSON |
| `/api/module/{name}/analysis` | PUT | 手动修改保存（status=reviewed，version++） |
| `/api/module/{name}/analysis/approve` | POST | 标记为 approved（Phase B 只消费 approved 的 analysis） |

**Phase B 消费时**：只读取 `status = 'approved'` 且 `version` 最新的记录。如果用户改了但没 approve，Phase B 走降级路径。

**为什么需要手动修改**：
- LLM 可能漏掉某个功能点的 scope（如忘了"删除电表"的边界场景）
- 用户比 LLM 更了解业务——用户知道"这个模块的导入功能不需要测性能"
- scope 标签可以增删改：用户加一条 `"边界-尖峰时段临界值"`，Phase B 就会多生成一条用例

### 2.3 Phase B 输入变化

Phase B 的 `analyze_test_points_raw` 保持现有逻辑（仍需做测试场景分析），但新增 `module_analysis` 作为优先输入。当 analysis 存在时，LLM 跳过场景识别，直接基于已知的场景+接口映射生成用例。

```python
human_parts = ["### 用户需求\n{user_context}"]

if module_analysis:
    # 优先路径：已有场景+接口映射
    human_parts.append(
        "### 模块场景与接口分析（权威数据源）\n"
        "{module_analysis}\n\n"
        "上述分析已列出所有场景、功能点、涉及接口及数据流向。"
        "请基于此分析生成测试用例，不要重复分析场景。"
    )
else:
    # 降级路径：全量原文（当前逻辑）
    human_parts.append("### 产品文档\n{product_docs}")
    human_parts.append("### 关联模块\n{related_docs}")

human_parts.append("### 接口定义\n{api_definitions}")
human_parts.append("请分析以上信息并生成测试用例。")
```

| 变量 | 改前 | 改后（有analysis） |
|------|------|------|
| `product_docs` | 全量原文 2~8 万 Token | **跳过**，analysis 已包含场景摘要 |
| `module_analysis` | — | **新增**：场景+接口映射 JSON，500~2000 Token |
| `api_definitions` | 全量列表 | 保留（接口参数详情不在 analysis 中） |

Phase B LLM 不再需要"识别有哪些场景、每个场景涉及哪些接口"——这些已经在前端按钮触发的分析中完成了。

### 2.4 Token 节省估算

| 场景 | 改前 Token/次 | 改后 Token/次 | 节省 |
|------|:---:|:---:|:---:|
| 单模块（8 个子模块，30 API） | ~120,000 | ~5,000 | 96% |
| 单模块（3 个子模块，10 API） | ~50,000 | ~3,000 | 94% |
| 月调用 50 次 | ~4,250,000 | ~200,000 | 95% |

---

## 三、实施步骤

### Phase 1：场景分析（前端按钮触发）

1. `prompts/extraction_prompts.py`：新增 `analyze_module_scenarios_prompt()` — 只做两件事：① 场景+功能点提取 ② 接口映射+数据流向
2. `web/tasks.py`：新增后台任务 `_analyze_module_scenarios_bg(task_id, module_name)` — 前端按钮触发
   - 读取该模块下全部产品文档 chunks（ChromaDB）
   - 读取该模块下全部 API 定义（ChromaDB / SQLite）
   - 读取 BindingOps 跨模块关系
   - 调 LLM 生成 `module_analysis` JSON（**不生成用例，不分析测试内容**）
3. `web/app.py`：新增 API 端点 `POST /api/module/{module_name}/analyze-scenarios` — 前端按钮调用
4. `database/models.py`：新增 `ModuleAnalysis` 表 — 存储场景+接口映射
5. `database/operations.py`：新增 `AnalysisOps` — CRUD

### Phase 2：前端展示

6. 前端：按钮点击后轮询 task 状态，完成后展示场景-接口映射图
   - 场景卡片：场景名 + 功能点列表
   - 接口标签：每个功能点关联的 API
   - 数据流向箭头：produces → consumes

### Phase 3：Phase B 输入增强

7. `agent_components/retrievers.py`：`_analyze_test_points_raw()` 调用前，先查 `ModuleAnalysis` 表
   - 存在 → 注入 `module_analysis` JSON，跳过场景识别步骤
   - 不存在 → 降级为当前全量原文逻辑
8. `prompts/definitions.py`：`analyze_test_points_raw()` human prompt 新增 `{module_analysis}` 可选变量

### Phase 4：验证

9. 同一模块对比：有 analysis vs 无 analysis，Token 用量 + 用例数量 + 场景覆盖
10. 降级路径：旧模块无 analysis → 自动回退原文模式

---

## 四、Phase A 插入点与 Phase B 衔接

### 4.1 当前流程全景

```
┌── Phase A（入库）─────────────────────────────────────────────────┐
│                                                                    │
│  PDF/DOCX ──→ _extract_text() ──→ chunk ──→ process_product_doc() │
│                                              │                    │
│    ① LLM 提取模块信息 (product_doc_extract_prompt)                 │
│       → module_name, related_modules, business_summary, tags      │
│    ② LLM 提取术语表 (glossary_extract_prompt)                      │
│    ③ 写 SQLite (_save_to_sqlite)                                  │
│    ④ 写 ChromaDB (db.add_product_doc_chunks)                      │
│    ⑤ 返回任务结果给前端 (_update_task)                              │
│                                                                    │
│  MD(API) ──→ process_api_doc_extract() ──→ 提取接口 JSON          │
│                                              │                    │
│    ① LLM 提取接口定义 (api_def_extract_prompt)                     │
│    ② 返回 apis[] 给前端，等待用户确认                              │
│    ③ 用户确认 → commit_api_docs() → 写 ChromaDB + SQLite          │
│                                                                    │
│  用户确认绑定 (_cascade_bind_to_module_docs)                       │
│    → BindingOps 链接 module ↔ document                            │
└────────────────────────────────────────────────────────────────────┘
                               │
                               ▼
┌── Phase B（检索 → 分析）──────────────────────────────────────────┐
│                                                                    │
│  confirm_intent → retrieve_product_docs → extract_related_modules  │
│    │                 │                       │                    │
│    │                 │ ChromaDB.similarity   │ BindingOps         │
│    │                 │ _search (doc_ids)     │ get_partners()     │
│    │                 ▼                       ▼                    │
│    │            product_docs[]          related_modules[]         │
│    │                 │                       │                    │
│    │                 └───────────┬───────────┘                    │
│    │                             ▼                                │
│    │              retrieve_related_data                           │
│    │                 │ 追加关联模块的 product_docs + api_defs      │
│    │                 ▼                                            │
│    └──────→ analyze_test_points_raw                               │
│                 │ 输入: product_docs(全量原文) + api_definitions   │
│                 ▼ 输出: test_point_analysis (自由文本)             │
│            generate_excel_plan                                    │
└────────────────────────────────────────────────────────────────────┘
```

### 4.2 按钮触发流程

模块绑定完成后，前端展示"分析测试场景"按钮。用户点击后触发后台任务。

```
前端                                  后端
────                                  ────
[分析测试场景] ──POST──→  /api/module/{name}/analyze-scenarios
                            │
                            ▼
                        创建 task_id
                            │
                            ▼
                        _analyze_module_scenarios_bg(task_id, module_name)
                            │
                            ├── 读取该模块全部产品文档 (ChromaDB)
                            ├── 读取该模块全部 API 定义 (ChromaDB/SQLite)
                            ├── 读取跨模块关系 (BindingOps)
                            ├── 调 LLM 生成 module_analysis JSON
                            └── 写入 SQLite ModuleAnalysis 表

轮询 GET /api/task/{task_id} ←── 返回进度 + 完成后结果
                            │
                            ▼
                      展示场景-接口映射图
```

分析结果返回给前端：

```python
{
    "success": True,
    "module_name": "智慧用电",
    "analysis": {
        "scenario_count": 6,
        "test_point_count": 35,
        "api_count": 23,
        "cross_module_count": 3,
        "summary": "6个场景 · 35个功能点 · 23个接口 · 3个跨模块约束"
    }
}
```

### 4.3 前端可视化

分析完成后前端展示场景-接口映射图：

```
智慧用电 — 场景分析完成（6场景 · 35功能点 · 23接口）

┌─ 电表管理 ─────────────────────────────────────────┐
│  功能点: 新增 | 删除 | 修改 | 查询 | 导入 | 导出        │
│  接口:                                              │
│    POST /electricMeter/add ──→ produces: meter_code │
│    GET /electricMeter/getEle/{code} ←── consumes: meter_code │
│    POST /electricMeter/delete ← 跨模块约束: 计费规则    │
│  数据流: add.meter_code → getEle.code (URL param)    │
└────────────────────────────────────────────────────┘
│
├── 跨模块: 计费规则（已绑定电表不可删除）
│
┌─ 计费规则管理 ─────────────────────────────────────┐
│  ...
```

### 4.4 关联模块的功能增强

分析时传入 BindingOps 跨模块关系 → LLM 产出 `cross_module_refs`。关联模块从"需要保护的独立功能"变成"分析的数据源之一"。

### 4.5 Phase B 调用变化

`_analyze_test_points_raw()` 调用前先查 `ModuleAnalysis` 表：

```python
def _analyze_test_points_raw(self, state: State):
    confirmed_module = state.get("confirmed_module", "")
    analysis_json = self._load_module_analysis(confirmed_module)

    # ...现有的 docs_text / apis_text 构建...

    if analysis_json:
        prompt_vars["module_analysis"] = analysis_json
        # Phase B LLM 看到已有场景+接口映射，跳过重复分析

    result = bound_llm.invoke(prompt.format_messages(**prompt_vars))
```

---

## 五、决策记录

| # | 决策点 | 结论 | 理由 |
|---|--------|------|------|
| 1 | 分析结果存放 | SQLite `module_analysis` 表 | 按 module_name 精确查询，不需要向量检索 |
| 2 | 分析触发时机 | **前端按钮手动触发**（绑定完成后用户点击"分析测试场景"） | 绑定完成 ≠ 用户准备好了，手动触发给用户控制权 |
| 3 | 分析范围 | **只做场景+接口映射**，不生成用例，不分析测试内容（参数值/断言/预期结果） | 职责边界清晰：阶段一管"测什么"，Phase B 管"怎么测" |
| 4 | 降级策略 | analysis 不存在时 Phase B 走当前全量原文逻辑 | 旧模块未分析时不阻断流程 |
| 5 | Phase B 变化 | 保留 `analyze_test_points_raw`，新增 `{module_analysis}` 可选入参 | 有 analysis 时跳过场景识别，没有时走原逻辑 |
| 6 | 关联模块 | analysis 利用 BindingOps 跨模块关系，产出 `cross_module_refs` | 关联模块从保护对象变成分析数据源 |
| 7 | 前端可视化 | 分析完成后展示场景-接口映射图（场景卡片+API标签+数据流向箭头） | 用户进入 Phase B 之前就能看到完整的场景和接口关系 |
| 8 | 表名 | `ModuleAnalysis`（不是 `ModuleMetadata`） | 准确反映内容：场景+接口分析，不是元数据 |
| 9 | `test_point.scope` | 每个测试点带 `scope` 数组（正向/边界-具体值/反向-具体场景/安全） | Phase B 不猜覆盖类型，直接按 scope 逐条展开，杜绝漏测 |
| 10 | 数据库存储 | SQLite `module_analysis` 表，JSON 全文存储 + status 状态机（draft→reviewed→approved） | 用户可手动编辑，Phase B 只消费 approved 版本 |
| 11 | 手动修改 | 提供 GET/PUT API，前端编辑 scope / test_points / data_flow | LLM 可能漏 scope，用户比 LLM 更了解业务 |
