# 智慧用电_37 用例修复总结 + Prompt 优化项（待确认）

> 日期：2026-08-26
> 状态：**修复已完成（对照组 14 passed / 6 skipped）；O1-O9 已全部回滚**——按「数据源 vs 非数据源」分类后的最终决策与待维护数据清单见 `2026-08-26_prompt_optimization_plan_v1.md`。
> 决策：本文件记录本次 37 用例全部手工修复内容，并提炼可作为 prompt 优化项的清单（对照 `prompts/extraction_prompts.py` 现状，标出缺口）。

## 一、本次修复内容（对照组 = 14 passed / 6 skipped）

对照组路径：`C:/Users/damai/PycharmMiscProject/testcase/园区基线/智慧用电_37/`

### A. add 请求体字段对齐真实 payload
- 以用户提供的两个真实电表 payload 为准：分时电表（三相 / meterTypeCode=2）、单一费率电表（单相 / meterTypeCode=1）。
- 全部 add YAML 请求体字段与 payload 对齐（字段名、层级、取值）。

### B. setup 动态唯一名 + 引用连通
- PRE-001 / PRE-001_isolated / PRE-003 / PRE-004 的 `code`/`name` 固定值 → `${random_code(...)}`。
- 各 setup 增加 `input_extract` 提取 code：`ELEC_001` / `meterCode` / `ELEC_BIND` / `ELEC_PARENT`。
- 用例内通过 `${get_extract_data('ELEC_001')}` 引用前置创建的资源，解决「名称请勿重复」重跑撞名。

### C. 断言路径修复（框架断言语义对齐）
- 列表型返回断言 `$..code` → `$.data`（框架 `contains $.data` = 拼接列表子串包含，稳定；`$..字段` = 取第一个匹配，不稳定）。
- 删除 `ne` 断言（框架 `ne` 不解析 JSONPath，仅做简单 dict key 比较，JSONPath ne 必败）。
- 删除 add 块 `$.data: X` 断言（add 成功响应 `data=null`）。
- 正向 getList 断言统一为 `$.data: ${get_extract_data('ELEC_001')}`。

### D. delete 数组参数格式
- delete 参数由对象 `{code: x}` → 数组 `json: [- ${get_extract_data('ELEC_001')}]`（用户权威实测：delete 支持 JSON 数组 `["code1","code2"]` 批量删除）。

### E. B 类负向断言 msg → fail
- 后端拒绝时统一返回 `retCode=0, msg='fail'`（无具体文案）。负向断言 msg 改 `fail`，不再臆造「必须选择计费方案」「排序参数不合法」等文案。

### F. 负电量 / 精度用例用顶层 `electricity` 字段
- 负初始电量 / 精度超限用例传顶层 `electricity: '-1'` / `'1.235'`（接口定义中 electricity 为顶层字段），而非 initDetailList 子对象。

### G. 6 个失败用例处置（标记跳过 + 理由，见 test_SmartPower.py）

| 用例 | 判定 | 理由 |
|------|------|------|
| test_get_parent_meter_list_positive | 接口异常 | getParentList 接口文档标注异常，dev 返回空列表，失败属预期 |
| test_add_meter_billing_not_selected_negative | 后端 bug | payConfigCode 为用户必填项（原始 payload 传值），后端未校验缺失即成功 |
| test_add_meter_gateway_protocol_not_selected_negative | 后端 bug | accessType 为用户必填项，后端未校验缺失即成功 |
| test_delete_meter_bound_billing_negative | 前提不成立 | 后端允许删除已绑定计费方案的电表（用户实测成功） |
| test_get_meter_list_invalid_page_negative | 计划设计问题 | 计划描述「非法页码」传 pageNum=0/-1（范围越界），后端对分页范围值宽容；负向值由用例描述驱动，计划层改描述/移除，不补 prompt |
| test_get_meter_list_invalid_sort_negative | 计划设计问题 | 计划描述「非法排序值」传 sortKey=2（枚举越界），后端宽容；且断言臆造失败文案「排序参数不合法」 |

> 必填性判定准则：**查看用户最初提供的真实 payload——传值的字段 = 必填；未传 = 非必填**。必填字段后端不校验 = 后端 bug；非必填字段后端不校验 = 正常。

## 二、Prompt 优化项（待用户确认后实施）

对照 `generate_yaml_data_single_prompt`（现 12 条铁律）与 `api_def_extract_prompt`，本次修复暴露的生成规则缺口：

### O1. 跨步骤/用例引用前置资源标识（缺）
- **现状**：铁律 8 只说 extract/input_extract 用不到就省略，无「前置创建资源必须被下游引用」的强制规则。
- **问题**：用例对 setup 创建的资源用写死值或自行重造，无法对齐真实创建结果。
- **优化**：新增铁律——前置/上游步骤创建的资源标识（code/name 等），下游引用必须 `input_extract` 提取 + `${get_extract_data(key)}`，禁止写死。

### O2. 列表型返回断言语义（缺）
- **现状**：铁律 7 仅「JSONPath 以 $. 开头」，未声明框架对列表返回的断言语义。
- **问题**：`$..code: X` 取第一个匹配，重跑后顺序变化即不稳定失败。
- **优化**：新增铁律——列表型返回（data 为数组）断言用 `contains $.data: <目标>`（框架拼接子串包含），避免 `$..字段`。

### O3. ne 断言能力边界（缺）
- **现状**：铁律 5 列运算符 eq/contains/ne/db，未声明 ne 的能力边界。
- **问题**：JSONPath ne 断言必败（框架 ne 仅简单 dict key 比较）。
- **优化**：新增铁律——`ne` 仅用于简单字段比较，禁用 JSONPath；JSONPath 断言用 eq/contains。

### O4. array 类型参数的 YAML 写法（缺）
- **现状**：铁律 11「delete 参数按接口定义」，未给 array 类型写法。
- **问题**：delete 的 code 为 array，LLM 输出对象 `{code: x}` 而非数组。
- **优化**：铁律 11 强化——参数类型为 array 时 `testCase.json` 用 YAML 列表（`json:\n  - <值>`）。

### O5. 反向断言失败取值（强化 9）
- **现状**：铁律 9 已禁臆造，但未给出「后端统一失败形态」提示。
- **问题**：返回定义无失败文案时 LLM 臆造「必须选择计费方案」等。
- **优化**：铁律 9 强化——返回定义无具体失败取值时，仅断言 `retCode != 成功值` 或 `msg` 用后端统一 `fail`，禁止臆造失败文案。

### O6. 接口异常状态识别（缺，跨 Phase A/Phase C）
- **现状**：api_def 提取无接口状态字段；生成层不认识「标注异常」接口。
- **问题**：getParentList 标注异常仍生成正向用例。
- **优化**：`api_def_extract_prompt` 提取接口状态（annotations 增加 status：normal/abnormal/deprecated）；生成层识别 abnormal 接口不生成正向用例或标记 skip。

### O7. 写接口成功响应 data=null（缺）
- **现状**：铁律 9 未提示写接口成功响应常无 data。
- **问题**：add 成功 `data=null` 仍断言 `$.data`。
- **优化**：铁律 9 强化——写接口（POST/PUT/DELETE）成功断言 retCode/msg，成功响应 data 为 null 时不断言 data。

### O8. setup/前置创建步骤唯一键（强化 10）
- **现状**：铁律 10「数据唯一化」措辞未显式覆盖 setup/前置步骤。
- **问题**：37 的 setup 固定 code/name → 重跑撞名。
- **优化**：铁律 10 强化——唯一键动态化覆盖 setup/前置创建步骤。

### O9. 字段层级准确性（Phase A 提取）
- **现状**：`api_def_extract_prompt` 要求嵌套子字段保持层级，但无强制「顶层字段不落入子对象」校验。
- **问题**：electricity 应为顶层字段，曾在 initDetailList 子对象下（probe 用 initDetailList 误判后端接受）。
- **优化**：`api_def_extract_prompt` 强化——字段 position（顶层 body vs 子对象）严格按文档层级提取，禁止平铺/错位。

## 三、下一步（待用户确认后执行）

1. 确认上述 O1-O9 取舍（合并/新增/驳回）。
2. 实施选定优化项（改 `extraction_prompts.py` + `response_model.py`）。
3. 以修改后用例为对照组，重新生成 37 用例数据 + 跑测试框架对比。
