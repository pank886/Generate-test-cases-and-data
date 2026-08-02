# 旧节点自生成兜底方案（Future）—— Phase B 生成/处理解耦

| 项目 | 内容 |
|:---|:---|
| 日期 | 2026-08-02 |
| 变更类型 | 未来实现计划（当前**未启用**，仅登记） |
| 状态 | 📋 计划中，待后续实现 |
| 关联文档 | `2026-08-02_phase_b_plan_processor_unify.md`（方案）、`2026-08-02_deferred_deletions.md`（删除清单） |

---

## 一、背景

已按**方案 3** 实施生成/处理解耦：

```
generate_plan_thinking（只生成，不落盘，plan_source=thinking）
        │
        ▼
generate_excel_plan（纯处理：数据源检测 → 校验 → 修复轮 → 引用完整性 → 消解器 → 落盘）
        │
        ▼
       END
```

- `generate_excel_plan` 为**纯处理节点**，只消费上游 `state.excel_plan`
- 收到空 plan（thinking 失败）→ 直接 `requires_review`，**不降级自生成**
- 旧链路 `analyze_test_points_raw → generate_excel_plan` 当前**不连边**（节点保留定义）

## 二、为什么需要兜底（Future 的动机）

当前 `generate_excel_plan` 对"thinking 失败"只能 `requires_review`（人工介入）。但存在两类场景希望自动兜底：

| 场景 | 现状 | 期望（未来） |
|:---|:---|:---|
| thinking 偶发空响应 / JSON 解析失败 | requires_review，人工重试 | 自动重试或降级到旧链路生成 |
| thinking 生成质量不达标（通过率 < 50%） | requires_review | 自动重新生成（质量门禁重试） |

## 三、可选兜底方案

### 方案 A：`generate_excel_plan` 恢复自生成（改动小，复用旧逻辑）

```python
# 无 external plan 时（原 else 分支，2026-08-02 已移除）：
plan = self._generate_plan_by_thinking(state, prompt_vars)   # 基于 test_point_analysis 生成
```

- **优点**：改动小，恢复被移除的 thinking+json 自生成分支即可
- **缺点**：`generate_excel_plan` 再次包含生成代码（与"纯处理"目标相悖）

### 方案 B：`analyze_test_points_raw` 升级为旧链路生成节点（彻底解耦）

```python
# analyze_test_points_raw 升级为输出 ExcelPlanV2（plan_source="analyze"）
return {"test_point_analysis": analysis, "excel_plan": plan, "plan_source": "analyze"}
# generate_excel_plan 仍纯处理，只是多了一个上游数据源
```

- **优点**：双链路（thinking / analyze）都产 plan，`generate_excel_plan` 100% 纯处理
- **缺点**：改动大 —— analyze 的 prompt 与返回结构要改；需评估 Phase C 对 `test_point_analysis` 字段的依赖（YAML 生成等）

### 方案 C：thinking 失败自动重试（不动处理节点）

```python
# generate_plan_thinking 内部重试 N 次，仍失败再 requires_review
```

- **优点**：处理节点完全不动
- **缺点**：thinking 系统性问题（如输入超长）重试无意义；无法覆盖"质量不达标需换思路生成"的场景

## 四、建议路线

**分两步（推荐）**：
1. **近期（方案 C）**：`generate_plan_thinking` 加有限重试（如 2 次），降低偶发空响应概率；`generate_excel_plan` 保持纯处理
2. **远期（方案 B）**：`analyze_test_points_raw` 升级产出 plan，恢复旧链路 `analyze → generate_excel_plan`，实现完整双链路；同时处理节点对"旧链路 plan"一视同仁（靠 `plan_source` 标注区分）

## 五、实现清单（未来执行时）

- [ ] `generate_plan_thinking`：失败重试逻辑（最多 2 次）+ 重试提示词
- [ ] `analyze_test_points_raw`：返回结构扩展 `excel_plan` + `plan_source="analyze"`
- [ ] `graph_builder`：`generate_plan_thinking` 条件路由 —— 成功→处理节点，重试耗尽→旧链路 analyze
- [ ] `generate_excel_plan`：确认对 `plan_source in ("thinking", "analyze")` 统一处理
- [ ] 回归：双链路各跑 5 轮，覆盖 ≥ 99%、悬空前置 0、无幻觉

## 六、当前行为的边界说明

| 场景 | 当前行为（2026-08-02 方案 3） |
|:---|:---|
| thinking 成功 | 处理节点消费 → 校验 → 修复 → 落盘 |
| thinking 失败（空响应） | `state.excel_plan` 为空 → 处理节点 `requires_review` |
| thinking 生成质量 < 50% | 处理节点 `requires_review`（不自动重试） |
| 旧链路 analyze | 节点保留定义，**不连边**（未启用） |
