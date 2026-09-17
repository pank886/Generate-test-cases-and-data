# 2026-09-02 Phase B 失败提示误报修复（0 失败却回「部分用例校验失败」）

> **状态：已完成（2026-09-02）。** 遵循写作原则：①确定方案先确认 ②最小改动 ③只改相关部分、
> 发现连带项汇总提醒 ④方案设计测试用例改完测试 ⑤先更新文档再看实际情况，实况确认后与文档比对。

> **范围说明：** 本文件覆盖两个相连的修复——(A) 0 失败误报（主），(B) 全无效路径补
> `requires_review`（连带项，用户确认一并处理）。

> **用户决策（AskUserQuestion）：** 问题 2「已新增两段生成节点，原 prompt 先注释」——
> 用户确认 **「无遗留，先跳过」**，不动作。本 changelog 只记录问题 1。

## 一、用户报告

用户实测一条 23 条用例的运行，得到回复：

> Excel 测试计划已生成：共 23 条用例（部分用例校验失败，详见 logs/thinking_trace.log）

诉求：「用例失败的详情又不展示了」。

## 二、根因（误报，非展示回归）

该运行（智慧用电_41，trace e33c9883）实际是 **23 用例全部通过、0 失败**：

```
09:40:50 两段式生成完成: 2 前置, 23 用例
09:40:50 ⚠️ 校验: 23 用例通过, 0 失败 (第1次)
09:40:50 📄 Excel 已保存: …智慧用电_41\test_plan.xlsx (23条/20模块, 2共享前置)
```

误报机制链（`web/tasks.py` `_resume_workflow_bg`）：

1. `nodes.py` 正常路径**总是**返回 `failed_case_ids`/`failed_case_details`，0 失败时二者为 `[]`；
2. 旧代码 `if not result.get("failed_case_details"):` —— `not []` 为真，把空列表误当「缺失」；
3. 误入兜底分支 → 读**整份**累积的 `thinking_trace.log`；
4. 命中**历史** `generate_excel_plan_FAILED` 标记（09-02 14:46 一步生成那次真实失败的
   PRE-002/TC-002，以及 09-01 的残留）→ 误置 fail_warn；
5. `_build_phase_b_reply` 看到 `failed_case_details` 为空 → 返回 `base + fail_warn` →
   0 失败却显示「部分用例校验失败，详见 logs/thinking_trace.log」。

真实失败详情的渲染链路（reply 逐行展示编号+标题+原因）**无回归**——bug 只在 0 失败时把
空列表当缺失、并让历史日志污染了当前回复。

## 三、改动方案（已落地）

### 改动 1：纯判定函数 `_resolve_failed_case_info(result, trace_tail)`

放 `_build_phase_b_reply` 之前，三分语义：

| `failed_case_details` | 语义 | 行为 |
|:---|:---|:---|
| 非空列表 | 真实失败 | fail_warn=`""`，编号取 `failed_case_ids`（详情由 reply 逐行渲染） |
| 空列表 `[]` | **0 失败** | 不提示（本次误报修复点） |
| `None`（缺失） | 节点未透传（异常路径） | 兜底：段内含 `generate_excel_plan_FAILED` 才提示并提取 TC-/PRE- 编号 |

### 改动 2：兜底日志只读本次运行尾部

- `graph.invoke` 前记录 `thinking_trace.log` 文件偏移（`os.path.getsize`）；
- 仅当 `result.get("failed_case_details") is None` 才读日志，且 `seek(偏移)` 后只读本次
  追加段——历史 FAILED 不再污染当前回复。

### 改动 3（连带项，用户确认一并处理）：全无效路径补 `requires_review`

`nodes.py` `_generate_excel_plan_node` 的 all-invalid 守卫（repair 轮耗尽仍 0 条可落盘，
返回 `excel_plan=None`）此前漏 `requires_review` flag → `_build_phase_b_reply` 走
`base + fail_warn` 分支 → case_count=0 时产出「共 0 条用例（部分用例校验失败）」的歧义
回复。修复：该返回补 `"requires_review": True` → reply 走「需人工审查」分支
（`⚠️ 测试计划需人工审查：所有行均未通过校验`），与 `_generate_excel_plan_thinking`
失败路径（incoming_plan=None）同契约。

## 四、测试用例设计

**`tests/test_workflow_init.py::TestFailedCaseDisplay` 新增 5 条**（纯函数，不依赖 LLM/DB）：

| 测试 | 覆盖 |
|:---|:---|
| `test_zero_failure_empty_list_no_false_warning` | **回归点(A)**：`[]` + trace_tail 含历史 FAILED → `("", [])` |
| `test_zero_failure_reply_clean` | `[]` → reply 恰为「共 23 条用例」，无「部分用例校验失败」 |
| `test_missing_details_scoped_tail_warns` | `None` + 段内含 FAILED → 提示 + 提取 PRE-002/TC-002 |
| `test_missing_details_clean_tail_no_warning` | `None` + 段内干净 → 无提示 |
| `test_real_failures_use_node_ids` | 非空 → 编号取 `failed_case_ids`，不读日志 |

**`tests/test_quality_gate.py::TestQualityGateWiring` 新增 1 条**（回归点(B)，端到端）：

| 测试 | 覆盖 |
|:---|:---|
| `test_all_invalid_marks_requires_review` | 空 plan（0/0 不触发质量门禁）驱动全无效守卫 → 返回 `requires_review=True` + `error_info`；`_build_phase_b_reply` 走「需人工审查」、不含「共 0 条用例」 |

## 五、验证结果

- `TestFailedCaseDisplay`：8 passed（3 旧 + 5 新）
- `TestQualityGateWiring`：新增用例后 3 passed（2 旧 + 1 新）
- 连带回归 test_workflow_init + test_thinking_log + test_phase_b_two_stage +
  test_quality_gate + test_phase_b_prompt_chain + test_enum_prompt_rules +
  test_plan_validator：**89 passed**

## 七、文档更新

- `changelog/2026-09-02_phase_b_failed_case_false_positive.md`（本文件）
- `docs/fixes_summary.md` §15 归档追加（按 CLAUDE.md 约定，编号 [67]/[P2]）
