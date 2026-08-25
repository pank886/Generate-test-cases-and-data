# Phase C validation 单键断言硬校验：拦截多键断言块

> 日期：2026-08-20
> 状态：**已实现，验证通过（2026-08-20）**
> 决策：Pydantic 校验层强制 `validation` 每块恰好一个断言运算符键（`{eq|contains|ne|db: {...}}`），多键块（`check/expected/operator`、`jsonpath/operator/value`）直接校验失败进修复轮，防止落盘后被执行框架整体拒绝。

## 背景

- 智慧用电_35（2026-08-20 新代码生成）执行：**31 条收集，30 ERROR + 1 FAILED，0 条真实执行**。
- 全部挂在 setup 阶段：执行框架 `common/assertions.py:207 assert_result` 只接受单键断言块 `{eq|contains|ne|db: {...}}`，而生成物混入两种三键格式——`['check','expected','operator']`（setup + 12 个 test_data.yaml）和 `['jsonpath','operator','value']`（TC-031）。
- 根因：`_35` 由 `65de719`（schema 驱动专用 prompt）生成，LLM 自创三键写法；而 Pydantic 校验 `validate_validation_element_is_dict` 只拦截「元素非 dict」和「单键操作数非 dict」，**从不拒绝多键块** → 三键 dict 通过校验落盘，执行时才报「每个断言块只能包含一个断言类型」。
- 对比 `_32`/`_33`：单键 `eq: {$.data.code: ..., $.retCode: 0}`，格式本身框架能跑——确认是 65de719 引入的**格式回归**。

## 改动（3 文件）

1. **`prompts/response_model.py`** `TestCase.validate_validation_element_is_dict`：
   - 新增 `if len(v) != 1: raise ValueError`（rule=`断言块多键`），错误信息指明框架只接受单键块、禁止三键写法。
   - 校验失败 → 该用例进 YAML 修复轮（repair_yaml_data_prompt），由 LLM 自查改写为单键格式。
   - `__placeholder_export` 占位（is_export 步骤）为单键 dict，不受影响；写盘前由 `_takeover_export_assertions`（py_export.py:31）替换为 `contains: {status_code: 200}`。
2. **`prompts/extraction_prompts.py`** 两个 YAML 生成 prompt 铁律各补一句单键块约束：
   - `format_yaml_data_prompt` 铁律 13：加「每条断言必须且只能是一个单键块（如 {eq: {$.msg: 成功}}），禁止 check/expected/operator、jsonpath/operator/value 等多键写法」。
   - `generate_yaml_data_single_prompt` 铁律 5：加「每条断言必须且只能是一个单键块，即一个运算符键对应一个断言对象，禁止 check/expected/operator、jsonpath/operator/value 等多键写法」（单节点 prompt 模板为 f-string，避免引入字面花括号以防 `{...}` 被当模板变量）。
3. **`tests/test_phase_bc_unit.py`** `TestYamlOutputHygiene` 新增 2 条：
   - `test_multi_key_validation_block_rejected`（check/expected/operator）
   - `test_multi_key_jsonpath_validation_block_rejected`（jsonpath/operator/value）

## 验证

```bash
python -m pytest "tests/test_phase_bc_unit.py::TestYamlOutputHygiene" -q        # 21 passed
python -m pytest tests/test_phase_bc_unit.py tests/test_phase_a_analysis.py \
    tests/test_yaml_db_export.py tests/test_phase_c_autofix.py -q              # 227 passed, 2 skipped, 1 xfailed
python -m pytest "tests/test_phase_bc_unit.py::TestFormatYamlPromptNoProjectRetcode" \
    "tests/test_phase_bc_unit.py::TestYamlSingleNodePromptRender" -q            # 6 passed
# 真实 _35 产物复核：setup + test_data 的三键 validation 均被模型拦截（GOOD）
# prompt 渲染复核：两段式渲染出 {eq: {$.msg: 成功}}；单节点渲染出单键块措辞（无花括号模板冲突）
```

## 覆盖范围

setup/teardown 与 per-test test_data.yaml 走同一 `_generate_one_yaml` → 同一 `TestData` 模型校验，新校验覆盖三类文件。prompt 铁律（第一层防御）+ Pydantic 硬校验（第二层防御）双层约束。

## 未做 / 后续

- teardown 仍生成 add 非 delete、URL 缺 `park-energy-electric-web/` 前缀、teardown 断言 `$.retCode: 200` 与 dev 实际成功值 1 不符——均属既有缺陷，另行处理。
- 待重生成 _35（或新计划）后重新执行验证修复轮产出单键格式。
