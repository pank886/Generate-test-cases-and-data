# YAML 合规审查报告

> 审查目录：`testcase/园区基线/智慧用电_27/SmartElectricity/`
> 审查时间：2026-08-12
> 审查范围：**121 个非空 YAML 文件**（111 个 test_data.yaml + 10 个 setup/teardown）+ `test_SmartElectricity.py`（引用 122 个路径，121 个存在，**1 个缺失**）
> 总 Block 数：**210**，测试用例数：**222**
> 方法分布：GET 22 / POST 188（无 PUT/DELETE）
> 断言运算符：eq 165 / ne 35 / contains 60（无 db）
> 框架基准：`base/apiutil.py`（含 2026-08-05 `parse_dollar_args` 引号兼容）+ `common/assertions.py` + `common/debugtilk.py`
> 检查方式：脚本自动化 + **框架运行时实测**（`RequestsBase().specification_yaml()`、`Assertions.assert_result()` 直接复现）

---

## 总体评价

❌ **不可用（4 类致命问题）**。YAML 静态结构质量延续 _26 的高水准（header、参数层级、URL、断言运算符、extract、`${}`、导出断言 12/12 全部通过，db 0 处），但出现 **1 个 Python 侧回归（fixture 调用模式错误，导致 91% 用例 class 级 ERROR）+ 3 个运行时级问题**（23 处 `eq: status_code` 新反模式、1 个文件缺失、3 处 `contains` 裸字符串崩溃）。

生成管线侧：`VALIDATION_INTERCEPT.md`（2026-08-12 18:32）记录 4 次拦截（2 次 URL 字面量占位符 `{code}`、2 次 GET 用 json）——磁盘上已全部化解（0 处残留），说明生成期校验本轮有效。

---

## 一、致命问题

### 🔴 1. fixture 调用模式回归：`specification_yaml(get_testcase_yaml(...))` 传入多 block 列表 → `TypeError`（5 个 fixture / 10 处调用）⭐ 本批最大问题

**框架实测**（已复现）：

```python
get_testcase_yaml(setup_meter_management.yaml)   # 返回 list，7 blocks
RequestsBase().specification_yaml(<该 list>)
# >>> TypeError: list indices must be integers or slices, not str
```

`get_testcase_yaml()` 返回 YAML **列表**，而本批所有 setup 文件均为多 block（setup_meter 7 / setup_billing_rule 11 / setup_common_area 21 / setup_prepaid 3 / teardown_prepaid 15 / teardown_common_area 20 …）。`specification_yaml()` 只接受单个 block dict（内部 `case_info['baseInfo']`），传列表必抛 `TypeError`。`specification_yaml` 的 docstring 亦明确注明 *"get_testcase_yaml()返回的是list，不能直接传入，fixture中需手动取 yaml_data[0] 传入"*。

**回归对比**：上一批 _26 的 fixture 用的是 `base.run_blocks(path)`（逐 block 执行 + 汇总断言，正确）。

**受影响 fixture**（setup + teardown 共 10 处）：

| fixture | 影响的测试类 | 测试方法数 |
|---|---|---|
| `setup_meter_management` | TestMeterManagement | 26 |
| `setup_billing_rule_management` | TestBillingRuleManagement | 21 |
| `setup_settlement_config_management` | TestSettlementConfigManagement | 7 |
| `setup_prepaid_management` | TestPrepaidManagement | 22 |
| `setup_common_area_management` | TestCommonAreaManagement | 26 |

**影响范围**：**102 / 112（91%）测试方法**在 class fixture 阶段直接 ERROR（TestElectricityStatistics 8 个 + TestPostpaidManagement 2 个的 fixture 为 `pass` 空操作，不受影响）。

**修复**：10 处 `base.specification_yaml(get_testcase_yaml(path))` → `base.run_blocks(path)`（一行级改动）。

---

### 🔴 2. `eq: {status_code: ...}` 断言模式错误（23 处 / 3 个文件）⭐ 本批新引入反模式

**表现**：

```yaml
validation:
- eq:
    status_code: 200     # ❌ eq 把 status_code 当 JSONPath，响应体无此字段
```

**框架实测**：响应体 `{retCode, msg, data}` 下，
```
assert_result([{'eq': {'status_code': 200}}], resp, 200)
>>> AssertionError: JSONPath "status_code" 未匹配到返回值中的任何值
```
`status_code` 特殊处理**只存在于 `contains_assert`**（`assertions.py:43-49` 与 HTTP 状态码比对）；`equals_assert` 一律按 JSONPath 解析，响应体 JSON 中没有 `status_code` 字段 → 必失败。正确写法为 `contains: {status_code: 200}`（实测通过）。

**新增反模式**：_21/_24/_25/_26 四批均为 **0 处** `eq: status_code`，本批首次引入。

| 文件 | 处数 | 说明 |
|---|---|---|
| `setup_data/setup_meter_management.yaml` | 7 | 每步**唯一断言**就是 `eq: status_code`；断言在 extract 之前执行，断言失败 → `payConfigCode/meterCode_003/...` 不提取 → 后续 `${get_extract_data(...)}` 全部级联失败 |
| `setup_data/teardown_prepaid_management.yaml` | 15 | 清理步骤全部断言失败 → 数据残留 |
| `test_meter_notify_report_unknown_negative/test_data.yaml` | 1 | `eq: {status_code: 404}` 断言 HTTP 404，负向用例核心断言失效（同文件 `contains: {$: ...}` 写法正确） |

> 注：当前被致命 #1 掩盖（fixture 先崩，到不了断言）。修复 #1 后 #2 立即暴露。修复：23 处 `eq: {status_code: X}` → `contains: {status_code: X}`。

---

### 🔴 3. YAML 文件缺失：`test_billing_rule_delete_bound_negative/test_data.yaml`

**引用位置**：`test_SmartElectricity.py:262-264`（`TestBillingRuleManagement` 类内 `test_billing_rule_delete_bound_negative`）。

目录 `test_billing_rule_delete_bound_negative/` 为**空目录**（0 文件）。

**影响**：运行时抛 `FileNotFoundError`，该用例无法执行。

---

### 🔴 4. `contains` 断言值为裸字符串（3 文件）→ 运行时 `AttributeError` 崩溃

**表现**：

```yaml
validation:
- contains: 收费名称不能为空   # ❌ 裸字符串，应为 dict
```

**框架实测**：`contains_assert` 内部 `for assert_key, assert_value in value.items()` 对字符串抛 `AttributeError: 'str' object has no attribute 'items'`——非 `AssertionError`，`run_blocks` 不捕获，**整个测试方法崩溃**（非干净的断言失败）。

**正确写法**（本批其他文件的标准形式）：
```yaml
- contains:
    message: 电表不存在        # 或 $.message: ...
```

| 文件 | 行号 | 期望文本 |
|---|---|---|
| `test_billing_rule_add_required_missing_negative/test_data.yaml` | 11 | 收费名称不能为空 |
| `test_meter_detail_not_found_negative/test_data.yaml` | 10 | 电表不存在 |
| `test_prepaid_balance_push_invalid_amount_negative/test_data.yaml` | 13 | 金额必须大于0 |

---

## 二、全部通过的检查项

| 检查项 | 状态 |
|---|---|
| 顶层是 YAML 列表 | ✅ 121/121 |
| 每个元素有 `baseInfo` 和 `testCase` | ✅ 210/210 blocks |
| `baseInfo` 含 `api_name`/`url`/`method`/`header` | ✅ 210/210 |
| `header` 键存在（GET 为 `{}`） | ✅ 210/210 |
| 方法仅 GET/POST，无非法值 | ✅ 22 GET + 188 POST |
| GET 用 `params`、POST/PUT 用 `json` | ✅ 0 处反例（无真实 `data:` 请求参数） |
| URL 不含 `{xxx}` 占位符 / `${}` | ✅ 0 处（生成期 2 次拦截已化解） |
| 无 `{body: [...]}` 包裹（陷阱 1） | ✅ 0 处 |
| `params`/`json`/`data` 不在 `baseInfo` 层级（陷阱 7） | ✅ 0 处 |
| 断言运算符仅 `eq`/`ne`/`contains`（陷阱 5） | ✅ 0 处非法（无 `neq`，无 `db`） |
| `eq`/`ne` key 不用 `${}` 动态值（陷阱 10） | ✅ 0 处 |
| `extract` 路径以 `$` 开头（陷阱 6） | ✅ 全部 |
| `${}` 函数均为 DebugTalk 已知函数 / 参数个数合法 | ✅ `get_extract_data`×37 / `get_offset_time`×1 / `get_current_time`×1，0 处未知 |
| `${}` 引号兼容（框架 `parse_dollar_args` 修复后） | ✅ 全部带单引号写法，实测兼容 |
| `db` 断言（无表结构应禁止） | ✅ 0 处 |
| 导出/下载/模板接口断言（陷阱 8） | ✅ **12/12 正确**（`contains: {status_code: 200}`） |
| 空文件 / 孤儿 YAML | ✅ 0 个（112 个测试目录全部被 Python 引用） |

**导出/下载/模板接口（12 个，全部正确）**：`test_electricity_stat_by_apartment_export`、`test_electricity_stat_by_enterprise_export_excel`、`test_electricity_stat_by_meter_export`、`test_meter_download_template`、`test_meter_export`、`test_meter_history_readings_export`、`test_prepaid_export_balance_list`、`test_prepaid_recharge_record_export`、`test_prepaid_settlement_record_export`、`test_common_area_download_apartment_template`、`test_common_area_download_enterprise_template`、`test_common_area_export_usage_template`。

---

## 三、统计汇总

| 类别 | 问题数 | 严重程度 |
|---|---|---|
| fixture 调用模式回归（`specification_yaml(列表)` → TypeError） | 5 fixture / 10 处 | 🔴 致命（91% 用例 ERROR） |
| `eq: {status_code: ...}` 新反模式 | 23 处 / 3 文件 | 🔴 致命（断言必失败 + extract 级联） |
| 文件缺失 | 1 个 | 🔴 致命 |
| `contains` 裸字符串 | 3 处 / 3 文件 | 🔴 致命（AttributeError 崩溃） |
| 静态结构问题 | **0** | — |
| 导出接口断言错误 | **0** | — |
| 带引号 `${}`（框架已修复） | 39 处（正常） | — |

---

## 四、批次质量对比

```
智慧用电_3  ████████████░░░░░░  致命 16 / 57（28%）   结构性问题为主
智慧用电_5  ████████████████░░  致命 12 / 66（18%）   结构性 + URL 占位符
智慧用电_13 ██████████████████  致命  1 / 63（2%）    仅剩 1 处 {body: [...]}
智慧用电_21 ██████████████████  致命  1 / 59（1.7%）  YAML 结构完美，仅 Python 路径错误
智慧用电_24 ██████████████░░░░  致命  4 / 56（7.1%）  3 文件缺失 + 1 陷阱回归
智慧用电_25 ██████████████░░░░  致命  3 类 / 126 + 18 缺失（运行时级问题）
智慧用电_26 ██████████████████  致命  1 / 128（0.8%）仅 1 个 teardown 生成失败，YAML 数据全部可用
智慧用电_27 ████████████░░░░░░  致命 4 类：fixture TypeError（91% 用例）+ 23 处 eq-status_code + 1 缺失 + 3 处 contains 崩溃
```

| 检查项 | _3 | _5 | _13 | _21 | _24 | _25 | _26 | _27 |
|---|---|---|---|---|---|---|---|---|
| 缺 `header` | 7 ❌ | 0 ✅ | 0 ✅ | 0 ✅ | 0 ✅ | 0 ✅ | 0 ✅ | 0 ✅ |
| `neq` 拼写 | 1 ❌ | 0 ✅ | 0 ✅ | 0 ✅ | 0 ✅ | 0 ✅ | 0 ✅ | 0 ✅ |
| params 错在 baseInfo | 1 ❌ | 0 ✅ | 0 ✅ | 0 ✅ | 0 ✅ | 0 ✅ | 0 ✅ | 0 ✅ |
| URL `{code}` 占位符 | 0 | 5 ❌ | 0 ✅ | 0 ✅ | 0 ✅ | 0 ✅ | 0 ✅（生成期拦截） | 0 ✅（生成期 2 次拦截已化解） |
| delete `{body: ...}` 包裹 | 6 ❌ | 4 ❌ | 1 ❌ | 0 ✅ | 1 ❌ | 0 ✅ | 0 ✅ | 0 ✅ |
| 导出断言模式 | — | 3 ⚠️ | 0 ✅ | 3 ⚠️ | 2 ⚠️ | 12 ❌ | 0 ✅（13/13） | 0 ✅（12/12） |
| 带引号 `${}` | — | — | — | — | — | 36 ❌（框架修复前） | 0（兼容） | 0（兼容） |
| `db` 裸 SQL / db 断言 | — | — | — | — | — | 9 ❌ | 0 ✅ | 0 ✅ |
| **`eq: {status_code: ...}`** | — | — | — | — | — | 0 | 0 | **23 ❌ 新反模式** |
| **fixture 调用模式** | — | — | — | — | — | — | `run_blocks` ✅ | **`specification_yaml(列表)` ❌ 回归** |
| 文件缺失 | 0 | 0 | 0 | 0 | 3 | 18 | 1 | **1** |

**结论**：智慧用电_27 的 YAML 数据层延续 _26 的最高水准——静态结构 0 问题、导出断言 12/12 正确、db 0 处、`${}` 引号全部兼容、生成期校验拦截生效。但 **Python 测试文件出现两处回归**，将数据层的高质量完全淹没：

1. **fixture 调用模式回归（最大问题）**：`specification_yaml(get_testcase_yaml(...))` 传入多 block 列表 → TypeError，5 个真实 fixture 全崩，102/112（91%）用例 class 级 ERROR。这是纯 Python 侧回归（_26 用 `run_blocks` 正确，_27 改错），与 YAML 数据质量无关。
2. **`eq: {status_code: ...}` 新反模式（23 处）**：`status_code` 特殊处理仅在 `contains`；`eq` 按 JSONPath 在响应体找不到该字段 → 必失败，且 setup 步骤断言失败会阻断 extract 级联。本批首次引入，修复成本低（`eq`→`contains`）。
3. **1 个 YAML 缺失**：`test_billing_rule_delete_bound_negative`（空目录，生成遗漏）。
4. **3 处 `contains` 裸字符串**：AttributeError 崩溃（非干净断言失败）。

**修复优先级**：
① 10 处 fixture `specification_yaml(get_testcase_yaml(path))` → `run_blocks(path)`（解 91% 阻塞）
② 23 处 `eq: {status_code: X}` → `contains: {status_code: X}`（否则 setup 断言失败级联）
③ 补生成 `test_billing_rule_delete_bound_negative/test_data.yaml`
④ 3 处 `contains: <裸字符串>` → `contains: {message: ...}` / `contains: {$.message: ...}`
