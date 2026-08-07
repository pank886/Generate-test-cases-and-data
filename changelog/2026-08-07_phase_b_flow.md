# Phase B 流程 — 输入/输出来源全图

| 项目 | 内容 |
|:---|:---|
| 日期 | 2026-08-07 |
| 阶段定位 | 工作流运行：用户需求 → 多跳检索 → 测试点分析 → Excel 测试计划（校验/修复/落盘） |
| 入口 | `web/routes/chat.py:/workflow/start` → `/workflow/confirm` → `web/tasks.py:_resume_workflow_bg` |
| 图定义 | `agent_components/graph_builder.py`（LangGraph StateGraph） |
| 核心文件 | `agent_components/nodes.py`、`retrievers.py`、`plan_validator.py`、`validator.py`、`prompts/definitions.py` |
| 数据落点 | 输出目录 `test_plan.xlsx` + `api_defs.json`（+ 运行日志 `logs/workflow_*.json|.md`） |

---

## 〇、总览图（LangGraph 节点拓扑）

```
  /workflow/start（用户输入 user_input）
       │
       ▼
┌────────────────────────────┐
│ 节点1 confirm_intent        │  输入: user_input + 模块树
│   LLM: PromptFactory.      │  输出: candidate_modules[]
│   confirm_user_intent      │        + confirmation_question
│   → IntentConfirmation     │        + workflow_status
└────────────┬───────────────┘
             │ 状态判断
      ┌──────▼──────┐
      │ WAITING     │→ END（返回前端，用户点选模块）
      │ CONFIRMED   │→ 继续
      └──────┬──────┘
             ▼
┌────────────────────────────┐
│ 节点2 retrieve_product_docs │  输入: confirmed_module
│   Hop1: ChromaDB doc_search │  输出: product_docs[]
└────────────┬───────────────┘
             │ 有数据?
      ┌──────▼──────┐
      │ NO_DATA     │→ END（提示先导入文档）
      │ 有数据       │→ 继续
      └──────┬──────┘
             ▼
┌────────────────────────────┐
│ 节点3 extract_related_modules│  输入: product_docs
│   LLM 关联模块提取           │  输出: related_modules[]
└────────────┬───────────────┘
             ▼
┌────────────────────────────┐
│ 节点4 retrieve_related_data │  输入: related_modules
│   Hop2a: 关联模块产品文档    │  输出: api_definitions[]
│   Hop2b: 关联模块+公共基础   │        + product_docs 补充
│          服务接口            │
└────────────┬───────────────┘
             ▼
┌────────────────────────────┐
│ 节点5 generate_plan_thinking│  ← 生产主路径（生成/处理解耦）
│   thinking + json_object   │  输入: module_analysis(DB) + api概要
│   → ExcelPlanV2            │       + module_tree + related_docs
└────────────┬───────────────┘       + user_ctx + db_schema
             ▼                     输出: excel_plan + plan_source
┌────────────────────────────┐           + api_full_for_snapshot
│ 节点6 generate_excel_plan  │  ← 纯处理节点（无生成）
│   ① 数据源检测              │  输入: state.excel_plan
│   ② 校验 ExcelPlanValidator│        + api_urls + module_tree
│   ③ 修复轮（失败行重填）     │  输出: test_plan.xlsx
│   ④ 引用完整性              │        + api_defs.json
│   ⑤ 消解器 _resolve_        │        + requires_review(失败)
│      resource_conflicts     │
│   ⑥ 落盘 + 文件层校验        │
└────────────┬───────────────┘
             ▼
           END → 前端展示 + 供 Phase C /confirm-plan 消费
```

---

## 一、节点输入/输出对照表

| 节点 | 输入 | 输入来源 | 输出 | 输出去向 |
|:--|:---|:---|:---|:---|
| 节点1 `confirm_intent` | `user_input` + 模块树 | 前端 `/workflow/start` + `ModuleOps.get_tree` | `candidate_modules[≤3]` / `confirmation_question` / `workflow_status` | 前端按钮渲染；`WAITING` 时挂起会话 |
| 节点2 `retrieve_product_docs` | `confirmed_module`（用户确认） | 前端 `/workflow/confirm` choice | `product_docs[]` | state 传递节点3 |
| 节点3 `extract_related_modules` | `product_docs` | 节点2 | `related_modules[]` | state 传递节点4 |
| 节点4 `retrieve_related_data` | `related_modules` | 节点3 + `config.COMMON_SERVICE_MODULE` | `api_definitions[]`（Hop2b）+ 关联产品文档 | state 传递节点5 |
| 节点5 `generate_plan_thinking` | `module_analysis`(scenario/ui/api) + api 概要 + `module_tree` + `related_docs` + `user_ctx` + `db_schema` | `AnalysisOps`（Phase A 三步分析落库）+ ChromaDB `api_definitions` + `ModuleOps.get_tree` + `state.original_input` + `config.DB_SCHEMA` | `excel_plan`(ExcelPlanV2) + `plan_source` + `api_full_for_snapshot`(含 annotations) | state 传递节点6；快照供落盘复用 |
| 节点6 `generate_excel_plan` | `excel_plan` / `api_urls` / `module_tree` | 节点5 + `state.api_definitions` | `test_plan.xlsx`（双 Sheet）+ `api_defs.json` + `output_dir` + `requires_review` | 输出目录；`requires_review` → 前端人工审查 |

---

## 二、节点6 处理链明细（生成/处理解耦后的纯处理节点）

```
state.excel_plan
      │
      ▼
① 数据源检测：state.excel_plan 有值？
      ├─ 有 → 消费（plan_source=thinking）
      └─ 无 → requires_review=true（不降级自生成，2026-08-02 方案3）
      ▼
② 校验：ExcelPlanValidator.validate(plan, test_analysis, api_urls, db_schema)
      ├─ 9 类错误聚合（字段/前置引用/步骤预期对齐/断言格式/URL 有效性/db 拦截）
      ├─ 质量门禁：首轮通过率 <50% → 全量重新生成（≤2 次），仍不达标 → 终止
      ▼
③ 修复轮（≤ config.EXCEL_REPAIR_ATTEMPTS=3）
      ├─ 入参统一：_prepare_plan_prompt_vars 共享数据 + failed_test_cases + block_reasons
      ├─ repair_excel_plan_prompt（json_mode）只重填失败行
      ├─ 代码侧裁剪：拒绝非失败行 / 已通过行 / 重复 ID
      └─ 单用例 check_case + PRE URL 修复判定
      ▼
④ 引用完整性：剔除悬空前置引用（孤儿 PRE → 弃行）
      ▼
⑤ 消解器 _resolve_resource_conflicts：同一 PRE 被 ≥2 个正向写用例引用 → 克隆隔离
      ▼
⑥ 落盘：
      ├─ test_plan.xlsx —— Sheet1「测试计划」9 列（epic/feature/story/title/fixture/编号/前置/步骤/预期）
      │                    Sheet2「共享前置」5 列（编号/名称/步骤/预期/关联用例）
      ├─ api_defs.json —— 完整接口快照（含 parameters/returns + annotations），Phase C 数据来源
      └─ 文件层校验 validate_excel_file（openpyxl 读回）
```

### 节点6 输入/输出明细

| 项 | 输入 | 来源 | 输出 | 去向 |
|:--|:---|:---|:---|:---|
| 接口概要 | `[{name, method, url, description}]` | `state.api_definitions` 瘦身（避免撑爆 thinking+json context） | — | 注入节点5/修复 prompt |
| 接口快照 | `[{name, url, method, description, parameters, returns}]` + annotations | `state.api_definitions` 全量 + `ApiAnnotationRegistry.apply_all` | `api_defs.json` | 输出目录，Phase C M8 消费 |
| 模块树 | 树结构 | `ModuleOps.get_tree` | `module_tree_json` | 注入 prompt + 输出目录定位 |
| 输出目录 | `confirmed_module` 路径 | 模块树路径拼 `config.TESTCASE_BASE`；同名非空目录自动加 `_2` 后缀 | `output_dir` | `.py/.yaml` 生成基址 |
| Excel 行 | `valid_cases` + `all_shared_pres` | 校验/修复轮收敛 | 9 列 + 5 列双 Sheet | `test_plan.xlsx` |
| 资源消解 | 完整 plan | 节点5 + 修复轮合并 | 克隆后的 PRE 隔离 | Excel Sheet2 |

---

## 三、与相邻阶段的数据交接

| 交接 | 载体 | 说明 |
|:--|:---|:---|
| Phase A → Phase B | ChromaDB `doc_search` + SQLite | 检索召回产品文档/接口定义 |
| Phase A → Phase B | SQLite `analysis` 表（`AnalysisOps`） | 三步预分析（场景/UI 交互/接口映射）作为权威分析源 |
| Phase B → Phase C | `test_plan.xlsx` + 同目录 `api_defs.json` | 产物快照传递，**禁止内存态跨阶段交接**（规则 M8） |

---

## 四、关键设计约束

1. **生成/处理解耦**：`generate_plan_thinking` 只生成 plan 入 state；`generate_excel_plan` 只做校验/修复/落盘。thinking 失败 → `requires_review`，不降级自生成。
2. **接口概要瘦身**：LLM 只喂 name/method/url/description，全量参数留在 `api_defs.json` 快照，避免超大输入导致 thinking+json 空响应。
3. **修复轮只重填失败行**：按 `failed_ids` 裁剪，拒绝 LLM 幻觉新用例；拦截原因按错误类型聚合（`aggregate_block_reasons`，同类一条+计数+受影响用例）。
4. **数据真实性**：`api_defs.json` 快照随 Excel 落盘；Phase C 确认时 `_resolve_api_defs` 若快照缺失/为空 → 显式阻断（M8），禁止空定义盲写 YAML。
