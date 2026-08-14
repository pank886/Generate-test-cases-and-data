# YAML 合规审查报告

> 审查目录：`testcase/园区基线/智慧用电_5/SmartElectricity/`
> 审查时间：2026-07-23
> 审查范围：66 个 YAML 文件
> 框架基准：`base/apiutil.py` + `common/assertions.py` + `common/sendrequests.py` + `common/debugtilk.py`
> 对比参考：智慧用电_3 审查结果

---

## 总体评价

智慧用电_5 相比智慧用电_3 有**显著改进**：
- 全部 `baseInfo` 都有 `header` 字段（无 KeyError 风险）
- 无 `params` 错放在 `baseInfo` 层级的问题
- 无 POST 用 `params` 替代 `json` 的问题
- 无 `neq` 拼写错误
- 无 `extract` 路径缺 `$` 前缀的问题
- **`/electricMeter/delete` 的测试用例文件已修正为 `json: [...]`**（正确格式）

但仍存在以下问题：

---

## 一、致命问题

### 1. `/electricMeter/delete` body 包裹错误（仅 teardown，4 处）

测试用例文件已正确使用 `json: [...]` 顶层数组，但 **setup/teardown 中仍有 4 处使用了错误的 `json: {body: [...]}`** 包裹。

**影响**：删除电表时 HTTP body 变成 `{"body": ["..."]}` 而非预期的 `["..."]`。

| 文件 | 行号 | 当前写法 | 应改为 |
|---|---|---|---|
| `setup_data/teardown_electricity_statistics.yaml` | 66‑69 | `json: {body: [TEST_METER_001, TEST_METER_002]}` | `json: [TEST_METER_001, TEST_METER_002]` |
| `setup_data/teardown_electricity_statistics.yaml` | 82‑85 | `json: {body: [TEST_METER_003, TEST_METER_004]}` | `json: [TEST_METER_003, TEST_METER_004]` |
| `setup_data/teardown_meter_reading.yaml` | 8‑10 | `json: {body: [AUTO_PRE001_TOD_001]}` | `json: [AUTO_PRE001_TOD_001]` |
| `setup_data/teardown_meter_reading.yaml` | 23‑25 | `json: {body: [AUTO_PRE002_SINGLE_001]}` | `json: [AUTO_PRE002_SINGLE_001]` |

**对比**：以下测试用例文件是 **正确** 的写法（供参考）：
```yaml
# test_meter_delete_unused_positive_001/test_data.yaml ✅
json:
- PRE-002

# test_meter_delete_with_data_exception_001/test_data.yaml ✅
json:
- PRE-001
```

---

### 2. URL 中含有未解析的 `{code}` 路径占位符（5 处）

**根因**：`apiutil.py:82` 直接拼接 URL，不对 URL 做任何变量替换。`/payConfig/delete/{code}` 中的 `{code}` 是字面量文本，不会被替换为实际编码值。请求会发往 `/payConfig/delete/{code}` 字面地址，大概率 404。

与此对比，同目录下正确的写法是直接在 URL 中拼接具体值（如 `/payConfig/delete/PRE-004`）。

| 文件 | 行号 | 当前 URL | 应改为 |
|---|---|---|---|
| `setup_data/setup_meter_reading.yaml` | 33 | `/electricMeter/getEle/{code}` | `/meterDevice/detail`（用 params 传 code） |
| `setup_data/setup_meter_reading.yaml` | 74 | `/electricMeter/getEle/{code}` | `/meterDevice/detail`（用 params 传 code） |
| `test_billing_add_fixed_plan_positive_001/test_data.yaml` | 28 | `/payConfig/detail/{code}` | `/payConfig/detail/${...}` 或固定路径 |
| `test_billing_add_tou_plan_positive_001/test_data.yaml` | 42 | `/payConfig/detail/{code}` | `/payConfig/detail/${...}` 或固定路径 |
| `test_billing_delete_unbound_plan_positive_001/test_data.yaml` | 6 | `/payConfig/delete/{code}` | `/payConfig/delete/PRE-004`（去掉 json body） |

**特别注意** `test_billing_delete_unbound_plan_positive_001`：
```yaml
# ❌ 两个错误：URL 含 {code} 字面量 + 多余 json body
- baseInfo:
    method: post
    url: /payConfig/delete/{code}
  testCase:
    - json:
        code: PRE-004   # 无意义，框架不会处理
```
正确写法参考 `test_billing_delete_bound_plan_exception_001`：
```yaml
# ✅ 正确
- baseInfo:
    method: post
    url: /payConfig/delete/PRE-004
  testCase:
    - json: {}
```

---

### 3. 导出类接口的断言无法执行——response 是二进制文件

**根因**：`apiutil.py:145` 对所有接口（含导出）统一调用 `res.json()`，但导出接口返回的是 Excel 二进制流 (`application/vnd.openxmlformats-officedocument.spreadsheetml.sheet`)，`res.json()` 会抛出 `JSONDecodeError`，测试直接崩溃，断言永远不会执行。

| 文件 | 问题描述 |
|---|---|
| `test_meter_export_list_positive_001/test_data.yaml` | 断言检查 `$.statusCode: 200` 和 `$.content-type: excel`，但 response 是二进制 |
| `test_postpaid_export_bill_positive_001/test_data.yaml` | 断言检查 `$.status_code: 200` 和 `$.headers.Content-Type: excel`，同上 |
| `test_stats_enterprise_export_positive_001/test_data.yaml` | 断言检查 `statusCode: 200`（键名还不一致），同上 |

**说明**：这是框架层面的限制——`apiutil.py` 未对导出接口做特殊处理。目前框架中所有导出类用例都会在这里失败。如果实际不需要校验导出内容，建议此类用例的 `validation` 置空（`[]`）并添加注释说明，或者修改框架代码对导出接口跳过 `res.json()`。

---

## 二、中等问题

### 4. `${}` 表达式参数个数不匹配（1 处）

| 文件 | 行号 | 表达式 | 问题 |
|---|---|---|---|
| `setup_data/teardown_enterprise_postpaid_management.yaml` | 10 | `${get_extract_data_list(meterCodes, -1)}` | 已确认 `get_extract_data_list` 方法只接受 `(self, node_name, randoms=None)` 两个参数，此处传的 `meterCodes`（无引号）会被当作变量名而非字符串。若 `meterCodes` 不是 DebugTalk 中定义的属性，这里会因 Python 变量未定义而抛 `NameError`。应改为 `${get_extract_data_list('meterCodes', -1)}` 或 `${get_extract_data('meterCodes')}` |

### 5. `test_meter_import_list_positive_001` — `json` 字段值为 YAML 数组（导入场景，合理但需确认）

```yaml
json:
- accessMethod: '1'
  code: ${random_plates(1)}
  ...
- accessMethod: '1'
  code: ${random_plates(1)}
  ...
```

`json` 是一个顶层 YAML 数组（两个 dict 元素）。这会转换为 `requests.post(json=[{...}, {...}])`，即批量导入的 JSON 数组 body。如果 API 的 `/electricMeter/import` 接受数组格式，这是**正确的**。

---

## 三、统计汇总

| 类别 | 问题数 | 严重程度 |
|---|---|---|
| `/electricMeter/delete` body 包裹 `{body: ...}` | 4 | 🔴 致命 |
| URL 含字面量 `{code}` 占位符 | 5 | 🔴 致命 |
| 导出接口断言（res.json() 对二进制） | 3 | 🔴 致命（框架限制） |
| `${}` 参数格式错误 | 1 | 🟡 中等 |

---

## 四、与智慧用电_3 的对比

| 检查项 | 智慧用电_3 | 智慧用电_5 |
|---|---|---|
| 缺少 `header` 字段 | 7 blocks ❌ | **0** ✅ |
| `neq` 运算符拼写 | 1 ❌ | **0** ✅ |
| `params` 错在 baseInfo | 1 ❌ | **0** ✅ |
| POST 用 params 不用 json | 1 ❌ | **0** ✅ |
| extract 缺 `$` 前缀 | 3 ❌ | **0** ✅ |
| URL 含未解析 `${}` | 2 ❌ | **0** ✅ |
| URL 含字面量 `{code}` | 0 | 5 ❌ (新问题) |
| delete body `{body: ...}` | 6 ❌ | 4 ❌ (仅 teardown) |
| 导出接口断言 | 未涉及 | 3 ⚠️ |

**结论**：智慧用电_5 的 LLM 生成质量明显优于智慧用电_3，结构性问题（header 缺失、参数层级错误、拼写错误）已全部修复。剩余问题集中在：URL 路径占位符、部分 teardown 的 delete body 格式、导出接口的断言模式。其中前两类可批量修复。
