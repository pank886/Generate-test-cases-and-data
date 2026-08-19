# Phase B 修复 prompt 注入完整 Schema 方案

> 日期：2026-08-18
> 状态：**✅ 已按此方案实施完成**
> 背景：智慧用电 Phase B 工作流执行失败，`LLM 结构化输出校验失败`：
> `shared_preconditions.0.name Field required`。
> 本次只修根因（修复 prompt 缺 schema 引导），不动其他。

## 0. 根因（已实测验证）

修复节点与生成节点用了**不同调用机制**，导致修复 LLM 看不到 schema：

| | 生成节点 | 修复节点 |
|---|---|---|
| 调用 | `_invoke_think` + 手动 `response_format=json_object` | `_invoke_structured(..., method="json_mode")` |
| schema 去向 | **手动注入 prompt 文本**（`{json_schema}` 占位符） | langchain `json_mode` 只 bind `response_format=json_object`，**schema 不进请求体** |
| 模型能否看到 schema | ✅ | ❌ |

`PydanticOutputParser`（json_mode 自动挂的输出侧解析器）只做**事后校验**，不参与引导生成。

实测渲染修复 prompt（system 1228 字符）：
- 含 `JSON Schema` 关键字：`False`
- 含 `SharedPrecondition/ExcelPlanV2`：`False`
- 含 PRE 对象示例 `"name"`：`False`

修复 LLM 唯一能抄的结构示例是硬编码的 `"shared_preconditions": []` + 一条**用例**示例 →
一旦需要输出 PRE，唯一可抄对象是用例结构 → 输出 `{id, story, title, preconditions, steps, expected, mutates_data, is_negative_test}`，漏掉必填 `name`。
同形错误 3 次重试复现（7-22 亦有同类 `name` 缺失记录）→ 确定性结构混淆，非偶发。

## 1. 改动（最小，2 处）

### 1.1 `prompts/extraction_prompts.py` → `repair_excel_plan_prompt()`

1. **注入完整 schema**（对齐生成节点写法）：
   - 在 prompt 开头加 `{json_schema}` 占位符 + 「### JSON Schema（必须严格遵循此结构）」说明
   - 调用方传入 `ExcelPlanV2.model_json_schema()` 序列化文本
2. **`"shared_preconditions": [],` 换成具体 PRE 对象示例**（含 `name`）：
   ```json
   "shared_preconditions": [
     {"id": "PRE-001", "name": "已创建测试电表",
      "steps": "1.调用 POST /electricMeter/add，填写电表名称\"测试电表B\"。\n2.确认返回创建成功。",
      "expected": "1.[eq]返回200，创建成功。"}
   ],
   ```
3. **「字段硬约束」补一条**：
   - `shared_preconditions` 元素 = `{id, name, steps, expected}`，`name` 必填；
     禁止输出 `story/title/preconditions/mutates_data/is_negative_test/cloned_from` 等用例字段

### 1.2 `agent_components/nodes.py` 修复调用点（≈371 行）

`_invoke_structured(repair_prompt, ExcelPlanV2, ...)` 传参补：
```python
json_schema=json.dumps(ExcelPlanV2.model_json_schema(), ensure_ascii=False, indent=2),
```

> `json` / `ExcelPlanV2` 已在该文件导入，无新增 import。

## 2. 测试用例（新增 `tests/test_repair_prompt_schema.py`）

| 用例 | 断言 |
|------|------|
| 渲染注入完整 schema | 修复 prompt 渲染后 system 含 `JSON Schema` 段 + PRE 的 `"name"` 字段 |
| PRE 示例非空 | 渲染后含 `PRE-001` 对象示例，不再出现 `"shared_preconditions": [],` |
| 字段硬约束已补充 | 渲染后含 `name 必填` 与「禁止输出 story/title」约束文本 |
| 畸形 PRE 仍被拒绝（行为保护） | `ExcelPlanV2.model_validate` 对本次失败的真实畸形 PRE（TestCaseRow 形状）仍抛 `name` 缺失错误 —— schema 是引导、不是容错 |

**回归**：`tests/test_phase_bc_unit.py`、`test_plan_validator.py`、`test_regression_import_smoke.py` 及相关修复轮套件全绿。

## 3. 明确不改

- **不拆** PRE 单独修复 prompt（主生成已证明两类共存可行；拆分加第二次 LLM 调用 + 合并逻辑，成本大于收益）
- **不把**动态字段挪 human 消息（缓存收益 ≈3%，与本次 bug 无关）
- **不改** `SharedPrecondition` 模型（`name` 保持必填，质量问题显性暴露）
- **不改**生成节点 / 其他节点 / 存储 / 前端

## 4. 实施顺序

1. 改 `repair_excel_plan_prompt()` + 修复调用点
2. 检查是否有既有测试渲染修复 prompt（不传 `json_schema` 会 KeyError）→ 同步更新
3. 新增 `tests/test_repair_prompt_schema.py`
4. 全量回归测试

## 5. 若实施中发现方案外涉及项

按约定汇总提醒用户决策，不擅自扩大改动。

## 6. 实施记录（2026-08-18 完成）

**改动文件**：

| 文件 | 改动 |
|------|------|
| `prompts/extraction_prompts.py` | `repair_excel_plan_prompt()` 注入 `{json_schema}`（`ExcelPlanV2.model_json_schema()`）段；`"shared_preconditions": []` → 具体 PRE 对象示例（含 name/steps/expected）；字段硬约束补 PRE 一条（name 必填，禁止用例字段/cloned_from） |
| `agent_components/nodes.py` | 修复调用点 `_invoke_structured` 补传 `json_schema=json.dumps(ExcelPlanV2.model_json_schema(), ...)` |
| `tests/test_repair_prompt_schema.py` | 新增 8 用例（schema 注入 / PRE 示例 / 字段约束 / 畸形 PRE 行为保护） |

**验证结果**：
- 冒烟：修复 prompt 渲染后含 `### JSON Schema` 段 + `SharedPrecondition` + PRE 示例 name；旧 `"shared_preconditions": [],` 已移除；无残留 `{{`/`}}` 转义
- 新增用例：`test_repair_prompt_schema.py` 8/8 通过
- 相关回归：`test_phase_bc_unit.py`/`test_plan_validator.py`/`test_quality_gate.py`/`test_regression_import_smoke.py`/`test_new_node_evaluation.py` 151 全过
- 全量：**615 passed, 7 skipped, 1 xfailed**（195s，exit 0，无回归）
- 实时验证：`tests/verify_repair_prompt_schema.py`（新增诊断脚本，不跑完整工作流，直接重放修复轮真实 LLM 调用）
  - 输入：真实 trace 的 14 条 PRE + 故意植入错误 URL 的 PRE-002（触发修复规则3）
  - 结果：**14 条 PRE 全部含 name**，错误 URL 一并修正，`ExcelPlanV2` 解析成功
  - 修复前同场景：输出 1 条畸形 PRE（TestCaseRow 形状、漏 name）→ 3 次重试失败 → 工作流终止

**明确不做**：不拆 PRE 单独 prompt、不挪动态字段、不改 `SharedPrecondition` 模型（name 保持必填）、不改生成节点。
**新增（方案外，诊断用）**：`tests/verify_repair_prompt_schema.py`——独立重放修复轮，供后续复现/回归该问题；不参与系统运行。
