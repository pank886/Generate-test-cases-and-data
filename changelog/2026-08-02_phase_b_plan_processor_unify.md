# Phase B Excel 计划：生成/处理解耦 —— 统一处理节点兼容双数据源

| 项目 | 内容 |
|:---|:---|
| 讨论日期 | 2026-08-02 |
| 变更类型 | 流程重构 — thinking 一步生成 与 校验/修复/落盘 管线解耦 |
| 涉及文件 | `agent_components/state.py`, `agent_components/nodes.py`, `agent_components/graph_builder.py`, `agent_components/plan_validator.py`(新增), `prompts/extraction_prompts.py` |
| 触发背景 | 悬空前置引用落盘：thinking 节点绕过旧节点校验管线 |

---

## 一、现状与问题

### 1.1 当前两条 Excel 生成路径（存在重复 + 校验盲区）

```
旧节点 _generate_excel_plan_node（旧流程，当前只在 thinking 失败时降级执行）：
  生成(thinking+json) → 校验 → 质量门禁 → 修复轮 → 引用完整性 → 消解器 → 落盘

新节点 _generate_excel_plan_thinking（当前生产主路径）：
  生成(thinking+json) → _finalize_excel_plan → 直接落盘（无任何校验/修复/消解）
```

### 1.2 问题

| # | 问题 | 影响 |
|---|---|---|
| 1 | **thinking 节点绕过校验管线** | 悬空前置引用（PRE-068/099/136 等）直接落盘；expected 断言格式、步骤/预期对齐同样无人校验 |
| 2 | **生成逻辑重复** | 两个节点各写一份 thinking+json 调用；生成节点与修复节点各构造一套入参（`all_apis_info`/`_sections` 不一致） |
| 3 | **校验/修复逻辑只内嵌在旧节点** | 无法被 thinking 复用，一旦 thinking 成为主路径，整条管线形同虚设 |

---

## 二、方案：生成与处理解耦，职责单一化

### 2.1 核心原则（本次修订）

1. **生成只发生在生成节点**：`generate_plan_thinking`（主路径）+ `analyze_test_points_raw`（thinking 失败时的兜底生成）。**处理节点 `generate_excel_plan` 不包含任何生成代码**。
2. **处理节点只处理**：数据源检测 → 校验 → 修复轮 → 引用完整性 → 消解器 → 落盘。
3. **修复节点入参与生成节点一致**：生成与修复共用同一套数据准备（统一构造 prompt 变量 + 传递数据源标注），不各自为政。

```
┌─ 生成环节（唯一产生 plan 的地方） ─┐        ┌─ 处理环节（纯处理，无生成） ──────────────┐
│ 主路径: generate_plan_thinking        │       │ generate_excel_plan                    │
│   thinking+json → ExcelPlanV2         │──plan─►│ ① 数据源检测：state.excel_plan 有值？    │
│   标注 plan_source=thinking           │       │    ├─ 有 → 用外部 plan（来源=thinking）   │
│                                       │       │    └─ 无 → 直接失败/中断（不生成兜底）     │
│ 兜底: analyze_test_points_raw(旧两步) │       │ ② 校验/拦截（字段/前置引用/对齐/断言格式）│
│   thinking 失败/为空时经 graph 降级    │       │ ③ 修复轮（入参=共享数据+错误用例+拦截原因）│
└───────────────────────────────────────┘       │ ④ 引用完整性（剔除悬空前置）             │
                                                │ ⑤ 消解器（冲突前置克隆隔离）             │
                                                │ ⑥ 落盘（Excel + api_defs.json）          │
                                                └──────────────────────────────────────────┘
```

### 2.2 数据源标注（入参即标注来源）

- `state` 新增 `plan_source: Optional[str]`，标注当前 `excel_plan` 由哪个生成节点产出
- `generate_plan_thinking` 返回：`plan_source="thinking"`
- `analyze_test_points_raw`（兜底生成）返回：`plan_source="analyze"`
- 处理节点入参时读取 `state["plan_source"]` + `state["excel_plan"]` 判定数据来源
- **修复节点同样接收/返回 `plan_source`**，保证修复产物与原始生成同源、可追踪

---

## 三、改动点明细

### 3.1 `state.py` — 增加数据源标注

```python
# --- Phase B 数据源标注 ---
plan_source: Optional[str]   # excel_plan 来源: "thinking" / "analyze"
api_full_for_snapshot: Optional[list]  # 生成节点已构造的快照数据（供落盘复用）
module_tree_json: Optional[str]        # 生成节点已取的模块树（避免重复查询）
```

### 3.2 `nodes.py::_generate_excel_plan_thinking` — 只生成，不落盘、不校验

当前（line 248）：`return self._finalize_excel_plan(state, plan, api_full_for_snapshot, module_tree)`

改为：
```python
return {
    "excel_plan": plan,
    "plan_source": "thinking",
    "api_full_for_snapshot": api_full_for_snapshot,
    "module_tree_json": module_tree,
}
```
落盘、校验、修复职责全部移交处理节点。

### 3.3 `nodes.py::_generate_excel_plan_node` — 纯处理节点（无生成代码）

- **移除内部自生成分支**（`thinking+json` 全量生成逻辑从本节点剥离）
- 数据入口：
  ```python
  def _generate_excel_plan_node(self, state: State):
      plan = state.get("excel_plan")
      if plan is None:
          raise RuntimeError("处理节点收到空 excel_plan：生成环节缺失")  # 由 graph 兜底降级
      api_full_for_snapshot = state.get("api_full_for_snapshot") or <自构造>
      module_tree = state.get("module_tree_json") or <自查询>
      # 直接进入既有 校验 → 质量门禁 → 修复轮 → 引用完整性 → 消解器 → 落盘
  ```
- 校验/修复/消解/落盘逻辑**一行不改**，仅数据入口从"自生成"改为"消费上游 plan"
- 其中**校验/拦截逻辑从本节点与旧节点剥离，独立到 `agent_components/plan_validator.py`**（`ExcelPlanValidator`），消除首轮/重试两份重复副本，处理节点与修复节点统一调用（见 §3.6）

### 3.4 修复节点升级 — 入参 = 共享数据（与生成一致）+ 错误用例 + 拦截原因

**现状**：生成节点与修复节点各自构造 prompt 变量（生成用 `_vars`，修复用 `failed_test_cases/all_apis_info/_sections`），存在入参漂移；且修复时只给错误用例文本，未把"为什么被拦"单独显式给全。

**改为**：修复节点入参分**三类**，缺一不可：

**① 共享数据**（与生成节点同一来源，`_prepare_plan_prompt_vars`）：
```python
def _prepare_plan_prompt_vars(self, state) -> dict:
    """生成/修复共用的 prompt 变量构造（单一数据源）：
       module_tree / analysis_section / shared_pre_section /
       cases_section / all_apis_info(概要) / plan_source / user_context
    """
    return {
        "module_tree": ...,
        "analysis_section": ...,
        "shared_pre_section": ...,
        "cases_section": ...,
        "all_apis_info": <概要 JSON，与生成节点一致>,
        "plan_source": state.get("plan_source"),
        "user_context": state["original_input"],
    }
```

**② 错误用例**（`failed_test_cases`）：修复目标行明细 —— 每行含 TC ID / 子模块 / 标题 / 步骤 / 预期原文，逐条拼接，供 LLM 定位"要修哪几行"。

**③ 拦截原因**（`block_reasons`）：校验/拦截环节返回的失败原因清单，例如：
- `引用前置 PRE-068 不存在`
- `步骤(3条)与预期(2条)数量不一致`
- `预期第2条缺少断言关键词`
- `预期第1条含双层括号`
- `title 为空`

**拦截原因聚合规则（防止多条重复刷屏）**：
- 每条失败用例可命中**多种类型**的问题（如同时"悬空前置"+"断言缺关键词"）
- **同一类型问题**：只返回**一条**代表性原因 + 受影响用例列表 + 计数，不逐条重复
  ```
  被拦截：引用前置不存在 —— 影响 3 条用例 (TC-070, TC-104, TC-135)，示例：引用了未定义的前置编号
  被拦截：步骤/预期数量不一致 —— 影响 2 条用例 (TC-012, TC-077)，示例：步骤条数与预期条数不相等
  ```
- **不同类型问题**：各返回一条（按类型分类，非按用例分类），类型清单固定 7 类：
  `pre_missing 悬空前置` / `field_empty 字段为空` / `steps_expected_mismatch 数量不一致` / `expected_empty_line 预期空行` / `expected_double_bracket 双层括号` / `expected_bad_space 断言空格` / `expected_missing_assert 缺断言关键词`
- 聚合由 `ExcelPlanValidator.aggregate_block_reasons(failed_details)` 完成，返回聚合后的 `block_reasons` 列表
- 验证点：**每条失败用例的每个拦截原因都有对应的类型记录，且 `failed_test_cases` 与 `block_reasons` 通过用例 ID / 类型双向可追溯**

修复 prompt 同时接收三类入参，LLM 才能既知道"修哪行"，又知道"为什么被拦"：
```python
repair_prompt = repair_excel_plan_prompt()
plan = self._invoke_structured(
    repair_prompt, ExcelPlanV2, method="json_mode",
    **shared_vars,                      # ① 与生成一致（含 plan_source）
    failed_test_cases=failed_tc_text,   # ② 错误用例
    block_reasons=block_reasons_text,   # ③ 拦截原因（新入参，repair prompt 同步加占位符）
)
```

- 生成节点、修复节点都调用 `_prepare_plan_prompt_vars`，保证 API 信息（概要）、模块树、分析段落**同一份来源**
- `repair_excel_plan_prompt`（extraction_prompts.py）新增 `{block_reasons}` 占位符，段落命名为**「拦截方法提示」**（提示被拦截的用例与原因，供 LLM 修正时消除对应问题）；提示词中**不出现**「修复具体原因 / 修复指南」类具体修复方法指引，避免过度引导 LLM 机械照改
- 修复产物继续带 `plan_source`，落盘时可溯源

### 3.5 `graph_builder.py` — 生成节点决定数据源，处理节点纯消费

```python
builder.add_edge("retrieve_related_data", "generate_plan_thinking")
builder.add_conditional_edges(
    "generate_plan_thinking",
    _route_after_thinking,                       # 成功/失败分支（thinking 自身判定）
    {"done": "generate_excel_plan",              # 成功 → 处理节点
     "fallback": "analyze_test_points_raw"},     # 失败 → 兜底生成
)
builder.add_edge("analyze_test_points_raw", "generate_excel_plan")
builder.add_edge("generate_excel_plan", END)
```

- thinking 失败 → 走 `analyze_test_points_raw`（兜底生成，独立节点，非处理节点内嵌）→ 处理节点
- 处理节点永远只消费上游已生成的 plan，**自身不含生成代码**

### 3.6 新增 `agent_components/plan_validator.py` — 校验/拦截逻辑独立管理

**动机**：当前校验逻辑在 `nodes.py` 有**两份几乎相同的副本**（首轮 line 341-377、重试 line 465-489，7 类错误重复实现），散落且难以演进。

**改为**：校验/拦截收敛为独立模块，单一职责 + 单测覆盖。

```python
# agent_components/plan_validator.py
class ValidationResult:
    failed_details: list      # [(idx, case_dict, errs)]  逐条失败明细
    all_confirmed: list       # 通过的用例
    block_reasons: list[str]  # 聚合后的拦截原因（按类型去重）

class ExcelPlanValidator:
    # 7 类固定错误类型
    ERR_TYPES = ("pre_missing", "field_empty", "steps_expected_mismatch",
                 "expected_empty_line", "expected_double_bracket",
                 "expected_bad_space", "expected_missing_assert")

    def validate(self, plan, test_analysis, pre_ids=None) -> ValidationResult:
        """字段/前置引用/步骤对齐/断言格式 校验，返回失败明细 + 通过用例"""

    def aggregate_block_reasons(self, failed_details) -> list[str]:
        """同一类型问题 → 一条代表原因+计数+受影响用例；不同类型各自一条"""
```

- `nodes.py` 首轮/重试校验替换为 `ExcelPlanValidator.validate(...)` 调用，删除两份重复副本
- 处理节点、修复节点统一引用 `validate()` / `aggregate_block_reasons()`
- 既有 `ValidationInterceptor`（response_model.py，统计用途）保持不动，validator 负责**修复 prompt 入参**的拦截原因

---

## 四、边界情况

| 场景 | 行为 |
|---|---|
| thinking 生成成功 | 处理节点消费 thinking plan → 校验 → 修复 → 消解 → 落盘 |
| thinking 生成失败（空 content / JSON 解析失败） | graph 降级 `analyze_test_points_raw` 兜底生成 → 处理节点 |
| 处理节点收到空 plan（异常） | 抛错，由 graph/异常机制兜底，不静默生成 |
| thinking plan 校验发现悬空前置 | 校验拦截 → 修复轮让 LLM 补全/修正 |
| 修复轮无法解决（含悬空前置） | 引用完整性逻辑剔除悬空行 → 落盘可用计划 |
| 修复轮耗尽仍失败 | 现有 `requires_review` 标记，交人工审查 |

---

## 五、验证计划

| 层 | 用例 |
|---|---|
| 单测 | `test_phase_bc_unit.py`、`test_phase_b_dedup.py` 适配新 state 字段 |
| 新增单测 | ① 处理节点收到 `plan_source="thinking"` + 含悬空前置的 plan → 悬空前置被拦截/剔除；② 修复节点入参 = 共享数据 + `failed_test_cases` + `block_reasons` 三类齐全（`_prepare_plan_prompt_vars` 一致性）；③ 处理节点收到空 plan → 抛错不生成；④ **拦截原因聚合**：多条用例命中同一类型错误 → `aggregate_block_reasons` 只返回一条（含计数+受影响用例），不同类型各自一条；⑤ 每条失败用例的每个拦截原因都有对应类型记录，`failed_test_cases` ↔ `block_reasons` 双向可追溯 |
| 集成 | 构造 thinking plan 传入处理节点，确认落盘 Excel 无悬空前置引用 |
| 回归 | 跑 `generate_excel_plan_thinking` 5 轮，悬空前置引用应为 0；接口覆盖 ≥ 99% |

---

## 六、风险与权衡

| 风险 | 缓解 |
|---|---|
| 处理节点重构引入回归 | 管线内部逻辑不改，仅数据入口从自生成改为消费上游；单测覆盖双路径 |
| thinking 结果被修复轮重写 | 修复轮只修失败行，已通过用例保留（`all_confirmed` 机制） |
| 移除自生成后 thinking 失败成本上升 | 由 graph 降级 `analyze_test_points_raw` 承接，生成职责仍唯一 |
| 修复节点入参改动影响旧路径 | `_prepare_plan_prompt_vars` 与原变量字段一致，仅统一来源 |

---

## 七、结论

**生成/处理职责彻底分离**：
- 生成只在生成节点（thinking 主路径 / analyze 兜底），处理节点**不含任何生成代码**
- 修复节点入参与生成节点统一（共享 `_prepare_plan_prompt_vars` + 数据源标注），消除入参漂移
- thinking 生成的数据 100% 经过统一管线的校验/修复/消解，悬空前置等异常不再落盘
