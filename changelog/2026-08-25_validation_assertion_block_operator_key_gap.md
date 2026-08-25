# 断言块键未约束为合法运算符：`{$.retCode: {eq: 1}}` 静默放行

> 日期：2026-08-25
> 状态：**已记录，待后续修复（本文件不执行改动，仅登记问题与方案）**
> 关联：[2026-08-20 Phase C validation 单键断言硬校验](2026-08-20_phase_c_validation_single_key.md) —— 该次只约束「块键数量 == 1」，未约束「块键必须是 eq/contains/ne/db 之一」。

## 问题

生成产物中出现**块键不是合法运算符**的断言，仍通过 `TestData.model_validate` 与 `YamlPostValidator` 双层校验，静默落盘：

- `- $.retCode:\n    eq: 1`（单键块，但块键是 JSONPath `$.retCode`，运算符 `eq` 被塞进值里）
- `- status_code: 200`（裸字段名当块键，未用 contains 包裹）

这类结构执行框架 `assert_result` 只认 `{eq|contains|ne|db: {...}}` 单键运算符块，无法识别，属于**应拦截而未拦截**的格式缺陷。

## 证据（2026-08-25 A/B 对比，智慧用电_36 全量一轮）

- NEW 版 prompt 产出 **19 条畸形断言**，分布在 6 个文件：
  - `SmartPower/setup_data/teardown_meter_management.yaml`（6 条）
  - `test_add_meter_initial_reading_zero_positive_007` / `_max_positive_008`（各 2 条）
  - `test_add_meter_platform_integration_positive_010`（6 条，含 2 条裸 `status_code`）
  - `test_add_meter_reading_not_number_negative_024`（1 条）
  - `test_add_meter_name_too_long_negative_025`（2 条）
- OLD 版（现有 prompt）71 条断言全部为合法运算符键，0 条畸形。
- 两版均无 `_post_validation_issues.json`（后校验零报告），NEW 的修复轮 3 个文件为其他错误，与本次无关。
- 产物保留：`logs/prompt_ab/NEW`、`logs/prompt_ab/OLD`。

## 根因（为什么双层校验都没拦住）

`prompts/response_model.py` `validate_validation_element_is_dict`（约 L415）：

```python
if len(v) != 1:            # 只拦「块键数量 != 1」（2026-08-20 加的）
    raise ValueError(...)
op, operand = next(iter(v.items()))
if op in ("eq", "contains", "ne") and not isinstance(operand, dict):
    raise ValueError(...)   # 只拦「合法运算符的操作数非 dict」
```

- `{$.retCode: {eq: 1}}` 块键数 == 1 → 过第一道；
- `op = "$.retCode"` 不在 `("eq","contains","ne")` 里 → `op in (...)` 为假，第二道**整体短路跳过**（`db` 也不在元组里）→ 校验通过。

`agent_components/post_validator.py` 的断言相关检查只覆盖「key 用动态占位符」（P1）与「引号未配对」，**没有检查断言块键是否为合法运算符**。

## 待修方案（后序执行）

1. **`prompts/response_model.py`** `validate_validation_element_is_dict`：在取出 `op` 后新增
   ```python
   if op not in ("eq", "contains", "ne", "db"):
       rule = "断言块键非法"
       raise ValueError(f"validation[{i}] 断言块键 '{op}' 不是合法运算符 "
                        "(eq/contains/ne/db)；块键必须是运算符，JSONPath 写在操作数 dict 的键位。")
   ```
   校验失败 → 进修复轮（与多键块同待遇），由 LLM 自查改写。
2. **`agent_components/post_validator.py`**：补一条结构检查，把「块键非 eq/contains/ne/db」标为 P0/P1，作为校验层的兜底告警。
3. **`tests/test_phase_bc_unit.py`**：`TestYamlOutputHygiene` 增补用例——`{$.retCode: {eq: 1}}`、`{status_code: 200}` 单键块必须被模型拦截。

## 说明

- 本缺陷与 prompt 版本无关：是**校验层缺一道「块键白名单」检查**，任何 prompt 输出该结构都会放行。
- 修复优先级：中——畸形块落盘后执行阶段才暴露，且修复轮内 LLM 仍可能重犯（取决于 prompt 措辞），代码层拦截最可靠。
