# Phase B 生成/处理解耦 — 稳定性验证与下一步计划

| 项目 | 内容 |
|:---|:---|
| 日期 | 2026-08-03 |
| 变更类型 | 状态记录 + 问题清单 + 下一步计划 |
| 关联文档 | `2026-08-02_phase_b_plan_processor_unify.md`（方案）、`2026-08-02_deferred_deletions.md`（清单）、`2026-08-02_old_generation_fallback.md`（兜底计划） |

---

## 一、当前状态

### 1.1 已完成（方案 3 全部落地）

| 项 | 内容 | 验证 |
|:---|:---|:---|
| 生成/处理解耦 | `generate_plan_thinking` 只生成不落盘；`generate_excel_plan` 纯处理 | ✅ |
| 校验收敛 | C1/C2/C3：首轮/重试校验 → `ExcelPlanValidator`，删除 `_ASSERT_*` 副本 | ✅ |
| 修复轮入参统一 | D1/D2：`_prepare_plan_prompt_vars` + `failed_test_cases` + `block_reasons` | ✅ |
| graph 直线串联 | `retrieve_related_data → generate_plan_thinking → generate_excel_plan → END` | ✅ |
| 完整链路 | thinking 135 用例 → 校验 135/135 → 消解器 → 落盘 xlsx | ✅ |

**核心目标达成**：thinking 生成的悬空前置被处理节点校验拦截（第 1 轮 8 个悬空 → 拦截 18 条用例），修复轮兜底，最终落盘无悬空。

### 1.2 5 轮稳定性结果（2026-08-03）

| 轮 | 用例 | 前置 | 覆盖 | 幻觉URL | 负向% | 悬空前置 |
|:--|:--|:--|:--|:--|:--|:--|
| 1 | 112 | 12 | 72/72 (100%) | 0 | 26.8% | **8 个** |
| 2 | 113 | 13 | 72/72 (100%) | 0 | 25.7% | 0 |
| 3 | 162 | 14 | 63/72 (87.5%) | 1 | **0%** | 0 |
| 4 | 104 | 9 | 53/72 (73.6%) | 1 | 35.6% | 0 |
| 5 | FAIL | — | — | — | — | 空响应 |

**汇总**：4/5 成功，覆盖 avg 90.3%（round 4 为 99.7%），悬空前置第 1 轮 8 个。

---

## 二、剩余问题清单

### P1 接口覆盖波动（P1 优先级最高）

- **现象**：第 4 轮覆盖 53/72（73.6%），19 个接口未覆盖；第 3 轮 87.5%
- **未覆盖模式**：`export` 导出 / `importTemplate` 模板 / `autoOffSupport` 等**无参数 GET 接口**
- **根因推测**：LLM 生成用例数偏少时优先覆盖核心接口，跳过"无参数、看似无业务可测"的边缘接口；thinking prompt 虽要求"每个接口至少 1 条"，但未强制执行
- **影响**：核心链路覆盖不完整，产出的测试计划缺边缘接口用例
- **对比**：round 4（未加步骤对齐约束）覆盖 avg 99.7%，当前 90.3% —— 步骤对齐约束可能间接影响广度，需增样本确认

### P2 thinking 偶发空响应

- **现象**：第 5 轮 `json.loads` 报 "Expecting value: line 1 column 1 (char 0)"（content 为空）
- **根因**：deepseek-v4-flash 的 thinking+json_object 偶发返回空 content（历史偶发，非本次引入）
- **当前处理**：方案 3 下 thinking 失败 → `state.excel_plan` 为空 → 处理节点 `requires_review`（人工审查，符合设计）
- **影响**：偶发需人工介入，无自动兜底生成

### P3 幻觉 URL（拼写错误）

- **现象**：第 3 轮 `/electrictMeter/getPage`（多打一个 t）、第 4 轮 `/payConfig/delete/`（trailing slash）
- **根因**：LLM 拼写漂移 / 路径参数写法不完整
- **影响**：拼写错误导致真实接口未被覆盖（覆盖率虚降）；处理节点当前不校验 URL 是否命中真实接口
- **占比**：5 轮 avg 0.5 个，不严重但会造成覆盖误判

### P4 悬空前置引用（thinking 原始输出，已兜底）

- **现象**：第 1 轮 8 个悬空 PRE（PRE-046/047/049/053/056/072/082/091）
- **说明**：thinking 原始输出仍有，但**处理节点已拦截**（18 条用例失败 → 修复轮）→ 最终落盘无悬空
- **影响**：无（兜底生效）；thinking 原始仍波动说明前置引用规范 prompt 未 100% 生效

---

## 三、下一步建议

### 建议 1（P1 覆盖波动）：强化 thinking prompt 的接口覆盖约束

```
在 generate_excel_plan_thinking prompt「用例设计规范」中强化：
「导出/导入/模板/开关类无参数接口（export/importTemplate/template/autoOff 等）
 也必须至少 1 条用例直接调用（如验证空文件导出、模板下载、开关查询），
 禁止仅因无参数而跳过。」
```

- **目标**：把 export/template 类边缘接口纳入必覆盖
- **验证**：改后跑 5 轮，覆盖 avg 应回到 ≥97%

### 建议 2（P2 空响应）：thinking 失败有限重试（old_generation_fallback 方案 C）

```
generate_plan_thinking 内部对空响应重试 1-2 次（复用概要输入，约 +4 分钟/次），
仍失败再 requires_review。
```

- **目标**：降低偶发空响应导致的 requires_review
- **代价**：thinking 失败时多等待 4-8 分钟
- **关联**：见 `2026-08-02_old_generation_fallback.md` 方案 C

### 建议 3（P3 幻觉 URL）：处理节点加 URL 有效性校验

```
generate_excel_plan 校验阶段新增：步骤中 URL 若无法匹配 api_definitions 中任一
真实接口路径 → 提示「疑似 URL 拼写错误」，修复轮一并修正。
（复用 ExcelPlanValidator，新增一类错误类型，如 invalid_url）
```

- **目标**：拦截拼写错误，避免覆盖虚降
- **注意**：需排除 Query 参数名（如 `/endTime`）与字段引用（`/peakElectricity`）误报

### 建议 4：增跑轮次确认随机性

- 当前仅 5 轮，覆盖波动（100% vs 73.6%）无法区分是"步骤对齐约束影响"还是"LLM 随机性"
- **做法**：保留当前 prompt 再跑 10 轮，统计覆盖分布；若稳定 ≥97% 则约束无碍，否则回退步骤对齐约束并另想办法

---

## 四、验证方法

| 项 | 方法 |
|:---|:---|
| 覆盖 | 跑 `test_new_node_evaluation.py` 5/10 轮，覆盖 avg ≥97% |
| 悬空前置 | 落盘 Excel 用 `ExcelPlanValidator.validate()` 复核，悬空 = 0 |
| 空响应 | thinking 失败率（应 <10%），requires_review 是否可接受 |
| 幻觉 URL | `test_new_node_evaluation` 幻觉 URL 计数（目标 avg ≤0.2） |
| 完整链路 | `thinking → generate_excel_plan` 端到端，`requires_review=None` |

---

## 五、结论

**架构目标（悬空前置兜底、格式规范化）已达成并验证**。剩余为 **LLM 生成质量的随机波动**（覆盖/空响应/拼写），不属于架构缺陷，但影响产出完整度。建议按 P1（覆盖）→ P2（空响应）→ P3（URL 校验）顺序优化，并先增跑轮次确认波动是否为随机性。
