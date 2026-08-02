# Phase B 生成/处理解耦 — 待删除/待修改清单（等确认后执行）

| 项目 | 内容 |
|:---|:---|
| 日期 | 2026-08-02 |
| 类型 | 增量实施记录 — 先新增，后按**方案 3** 执行删除/修改 |
| 关联方案 | `2026-08-02_phase_b_plan_processor_unify.md`、`2026-08-02_old_generation_fallback.md` |
| 状态 | ✅ 方案 3 已执行（thinking 不落盘 / 处理节点纯消费 / graph 直线）；C、D 待办 |

---

## 一、本轮已完成（纯新增，不影响现有行为）

| 文件 | 新增内容 |
|:---|:---|
| `agent_components/plan_validator.py` | ExcelPlanValidator（validate + aggregate_block_reasons，7 类错误聚合） |
| `agent_components/state.py` | `plan_source` / `api_full_for_snapshot` / `module_tree_json` 三个字段 |
| `agent_components/nodes.py` | `_prepare_plan_prompt_vars(state)` 共享数据准备方法（未接线） |
| `prompts/extraction_prompts.py` | `repair_excel_plan_prompt` 新增 `{block_reasons}` 占位符，段落命名「拦截方法提示」；「修复指南」改为「修正要求」（不含修复具体原因） |

### 方案 3 已执行（2026-08-02 确认）

| # | 改动 | 结果 |
|:--|:---|:---|
| A1 | thinking 只生成不落盘 | ✅ `_generate_excel_plan_thinking` 返回 `{excel_plan, plan_source, api_full_for_snapshot, module_tree_json}` |
| B1 | 处理节点纯处理 | ✅ `_generate_excel_plan_node` 无 external plan → `requires_review`；不再自生成 |
| B2 | 落盘保留 | ✅ 处理节点内 `_finalize_excel_plan` 逻辑保留（Excel + api_defs.json） |
| B3 | 消解器复用 | ✅ 处理节点落盘前照常调用 `_resolve_resource_conflicts` |
| E1-E3 | graph 直线串联 | ✅ `retrieve_related_data → generate_plan_thinking → generate_excel_plan → END`；去掉 analyze 降级（节点保留不连边） |

### 实施中暴露的问题（2026-08-03 已修复，完整链路跑通）

| # | 问题 | 根因 | 修复 |
|:--|:---|:---|:---|
| F1 | thinking expected 合并行 → 校验通过率 0 | thinking 把多步断言合并 1 行，旧校验要求 steps/expected 逐行 1:1 | `generate_excel_plan_thinking` prompt 加「步骤/预期严格对齐」硬约束 + human 自检 |
| F2 | 断言匹配误杀行内标签 | 旧校验 `_ASSERT_OK` 用 `^\d+\.` 前缀，框架 `_ASSERTION_PATTERN` 是 `re.search(行内[tag])` | `_ASSERT_OK` 改为行内标签匹配（nodes.py + plan_validator.py） |
| F3 | `AttributeError: _run_timestamp` | 初始化误放在 `_finalize_excel_plan` 的 `return` 后（死代码从未执行），`_log_node_output` 直接访问 | 移入 `ChatTestAgentGraph.__init__`，删除死代码 |

**验证结果**：thinking 135 用例 → 处理节点校验 135/135 通过 → 消解器（13 PRE 隔离）→ 落盘 test_plan.xlsx（135条/10模块/84前置）→ requires_review=None ✅

---

## 二、待删除/待修改清单（C、D 待办）

### A. 落盘职责移交（thinking 只生成）— ✅ 已执行

| # | 位置 | 状态 |
|:--|:---|:---|
| A1 | `nodes.py::_generate_excel_plan_thinking` 末尾 | ✅ 已改为返回 plan + plan_source，不落盘 |

### B. 旧节点改造为纯处理节点 — ✅ 已执行（方案 3）

| # | 位置 | 状态 |
|:--|:---|:---|
| B1 | `nodes.py::_generate_excel_plan_node` | ✅ 纯处理：消费 `state["excel_plan"]`；空 plan → `requires_review`。自生成兜底移入 `2026-08-02_old_generation_fallback.md`（未来） |
| B2 | 同 B1 落盘段 | ✅ 保留 |
| B3 | `nodes.py` 消解器 | ✅ 处理节点照常调用 |

### C. 校验副本收敛（两处重复 → 调 validator）— ✅ 已执行（2026-08-03）

| # | 位置 | 状态 |
|:--|:---|:---|
| C1 | `nodes.py` 首轮校验 | ✅ 替换为 `ExcelPlanValidator.validate(plan, test_analysis)` |
| C2 | `nodes.py` 重试校验 | ✅ 替换为 `ExcelPlanValidator.check_case(tc, pre_ids_all)` |
| C3 | `nodes.py` 顶部 `_ASSERT_*` 正则 | ✅ 已删除（校验收敛到 `plan_validator.py`） |

### D. 修复节点入参统一 — ✅ 已执行（2026-08-03）

| # | 位置 | 状态 |
|:--|:---|:---|
| D1 | `nodes.py` 修复轮 | ✅ 改调 `_prepare_plan_prompt_vars(state)`（与生成一致）+ 传 `failed_test_cases` + `block_reasons`（`aggregate_block_reasons(failed_details)` 聚合） |
| D2 | `repair_excel_plan_prompt` 调用处 | ✅ 传入 `block_reasons` 变量（`{block_reasons}` 占位符已就位） |

### E. 图结构调整

| # | 位置 | 当前行为 | 目标行为 | 类型 |
|:--|:---|:---|:---|:---|
| E1 | `graph_builder.py` | `generate_plan_thinking` 条件分支（成功→END，失败→analyze→旧节点） | 成功→`generate_excel_plan`(处理)；失败→`analyze_test_points_raw`(兜底生成)→处理节点 | 修改 |
| E2 | `graph_builder.py` | thinking 失败时降级旧节点自生成 | 降级路径改为 analyze 兜底生成 → 处理节点 | 修改 |
| E3 | `graph_builder.py::_route_after_thinking` | `done→END` | `done→"generate_excel_plan"` | 修改 |

---

## 三、删除/修改的验证要求（执行时）

1. `nodes.py` 修改后跑 `test_phase_bc_unit.py`、`test_phase_b_dedup.py`、`test_phase_bc_api.py` 全绿
2. thinking 节点改造后跑 `tests/test_new_node_evaluation.py` 5 轮，悬空前置应为 0
3. 处理节点收到空 plan → 明确抛错（不静默生成）
4. validator 单测：7 类错误类型聚合正确（同类一条含计数、不同类各自一条、双向可追溯）

---

## 四、结论

本轮增量落地了全部**新增**（validator / state 字段 / prompt 占位符 / 共享数据准备），现有流程零改动、零风险。待删除/修改项（A-E）已逐条登记，用户确认后按上表执行并跑验证。
