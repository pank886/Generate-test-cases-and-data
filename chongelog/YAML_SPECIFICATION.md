# YAML 测试用例完整规范（AI 自动生成参考）

> **用途**：本文档是 YAML 测试用例的**唯一权威规范**。AI 模型在生成 YAML 文件时，必须严格遵循本文档的所有规则、语法和示例。
>
> **适用范围**：pytest + Allure + YAML 数据驱动测试框架（`base/apiutil.py` `RequestsBase.specification_yaml()` 消费）

---

## 目录

1. [文件整体结构](#1-文件整体结构)
2. [baseInfo 节点规范](#2-baseinfo-节点规范)
3. [testCase 节点规范](#3-testcase-节点规范)
4. [请求参数：json / params / data](#4-请求参数json--params--data)
5. [动态占位符：${} 函数全集](#5-动态占位符-函数全集)
6. [数据提取：extract / extract_list / input_extract](#6-数据提取extract--extract_list--input_extract)
7. [断言体系：validation](#7-断言体系validation)
8. [完整综合范例](#8-完整综合范例)

---

## 1. 文件整体结构

一个 YAML 文件有**三种合法的顶层结构**，根据消费方式选择：

### 1.1 单块结构（最常用，配合 parametrize 使用）

`get_testcase_yaml()` 返回 `list`，pytest 的 `@parametrize` 自动将 list 中每个元素注入 `params` 参数。

```yaml
# 文件只包含一个列表元素（一个 baseInfo + 一个 testCase）
- baseInfo:
    api_name: "接口名称"
    url: /path/to/api
    method: post
    header:
      Content-Type: application/json;charset=UTF-8
  testCase:
    - case_name: "用例名称"
      json:
        field1: value1
      validation:
        - eq: { retCode: 1 }
```

### 1.2 多块结构（配合 `run_blocks()` 使用）

一个 YAML 包含多个 `baseInfo + testCase` 列表项。`run_blocks()` 按顺序依次执行，一个失败不影响后续，最后汇总所有错误。

```yaml
# 第 1 个接口调用
- baseInfo:
    api_name: "入场"
    url: /mock/access/enter
    method: post
    header:
      Content-Type: application/json;charset=UTF-8
  testCase:
    - case_name: "临停车辆入场"
      json:
        carNumber: ${random_plates(1)}
        time: "2026-07-03 10:00:00"
      input_extract:
        carANumber: "$.json.carNumber"
      validation:
        - eq: { openGate: true }

# 第 2 个接口调用（同一文件，从上到下顺序执行）
- baseInfo:
    api_name: "出场"
    url: /mock/access/exit
    method: post
    header:
      Content-Type: application/json;charset=UTF-8
  testCase:
    - case_name: "缴费后出场"
      json:
        carNumber: ${get_extract_data(carANumber)}
        time: "2026-07-03 11:05:00"
      validation:
        - eq: { openGate: true }
```

### 1.3 单 baseInfo 多 testCase 结构

一个接口下包含多个用例，共享同一个 `baseInfo`。框架会遍历 `testCase` 列表，对每个元素发送一次请求。

```yaml
- baseInfo:
    api_name: "根据车牌号删除"
    url: /park-access-parking-web/inner/carNumber/deleteByCarNumber
    method: post
    header:
      Content-Type: application/json;charset=UTF-8
  testCase:
    - case_name: "删除车辆A"
      json:
        - carNumber: ${get_extract_data(carABlack)}
      validation:
        - eq: { retCode: 1 }

    - case_name: "删除车辆B"
      json:
        - carNumber: ${get_extract_data(carBWhite)}
      validation:
        - eq: { retCode: 1 }

    - case_name: "删除车辆C"
      json:
        - carNumber: ${get_extract_data(carCMonthly)}
      validation:
        - eq: { retCode: 1 }
```

---

## 2. baseInfo 节点规范

`baseInfo` 描述接口的元信息。`api_name` / `url` / `method` **必填**；`header` 条件必填（见下）；`cookies` 可选。

| 字段 | 类型 | 必填 | 说明 | 示例 |
|:---|:---|:---|:---|:---|
| `api_name` | string | ✅ | 接口中文名，用于日志和 Allure 报告 | `"包月车添加"` |
| `url` | string | ✅ | 接口路径，**不含域名**。域名由 `conf/config.ini` 的 `[api_envi] host` 提供 | `/park-access-parking-rule-new/mock/monthlyCar/add` |
| `method` | string | ✅ | HTTP 方法，**必须小写**。支持的值：`post` `get` `put` `delete` `patch` | `post` |
| `header` | dict | 条件 | 请求头，只写 Content-Type。**公共头（`yq-app-code`、`token` 等）由框架作为常量自动注入，YAML 中一律不写** | 见下方 |
| `cookies` | dict | ❌ | 请求 cookies，需要时填写 | `{ sessionId: "abc123" }` |

### header 的标准写法

```yaml
# json 请求体（post/put/patch）
header:
  Content-Type: application/json;charset=UTF-8

# 表单请求体（data 字段仅在此 Content-Type 下合法）
header:
  Content-Type: application/x-www-form-urlencoded
```

> **注意**：
> - 公共头（`yq-app-code`、鉴权 `token` 等）由框架层设置为常量自动注入，**生成的 YAML 中不出现**。
> - 仅 `params`（GET/DELETE 查询）时无需请求体头，`header` 整体可省略。
> - 文件上传接口不写 `Content-Type`（multipart 边界由 HTTP 客户端自动生成），`header` 整体可省略。

---

## 3. testCase 节点规范

`testCase` 是一个列表，包含一个或多个测试用例。每个元素的字段如下：

| 字段 | 类型 | 必填 | 说明 |
|:---|:---|:---|:---|
| `case_name` | string | ✅ | 用例名称，Allure 报告中展示 |
| `json` | dict/list | 三选一 | JSON 请求体 |
| `params` | dict | 三选一 | URL 查询参数 |
| `data` | dict | 三选一 | 表单编码请求体 |
| `validation` | list[dict] | 建议填写 | 断言列表，每项一个断言类型 |
| `extract` | dict | ❌ | 从接口响应中提取数据（JSONPath 或正则） |
| `extract_list` | dict | ❌ | 从接口响应中提取列表数据（JSONPath 或正则） |
| `input_extract` | dict | ❌ | 从请求参数中提取数据（不依赖响应） |

> **注意**：`input_extract` 也兼容大写 `Input_extract`。`json` / `params` / `data` 必须且只能出现一个。

### 框架内部处理流程

```
testCase 中字段的处理顺序：
  1. pop('case_name')      → Allure 报告展示
  2. pop('validation')     → 执行断言
  3. pop('extract')        → 响应提取
  4. pop('extract_list')   → 响应列表提取
  5. pop('input_extract')  → 请求参数提取
  6. 遍历剩余字段，凡是 json/params/data → replace_load() 解析 ${}
  7. 剩余字段全部作为 **kwargs 传给 requests.request()
```

---

## 4. 请求参数：json / params / data

三者对应 Python `requests` 库的同名参数，通过 `**kwargs` 原样透传。**必须且只能选一个**。

### 4.1 json — JSON 请求体（最常见）

序列化为 JSON 字符串，自动设置 `Content-Type: application/json`。

```yaml
# 示例1：普通对象
json:
  parkCode: "test"
  carNumber: ${random_plates(1)}
  startTime: "2026-05-27"
  endTime: "2029-05-27"

# 示例2：数组作为请求体（接口要求 JSON 数组时使用）
json:
  - carNumber: "粤B12345"
  - carNumber: "粤B67890"

# 示例3：空 JSON 对象
json: {}
```

### 4.2 params — URL 查询参数

拼接到 URL 上变成 `?key1=val1&key2=val2`。**通常配合 `method: get` 使用**。

```yaml
- baseInfo:
    api_name: "分页查询"
    url: /api/resource/search
    method: get
  testCase:
    - case_name: "第1页查询"
      params:
        pageNum: 1
        pageSize: 10
        keyword: "test"
      validation:
        - eq: { retCode: 1 }
```

实际发出：
```
GET /api/resource/search?pageNum=1&pageSize=10&keyword=test
```

### 4.3 data — 表单编码请求体

以 `application/x-www-form-urlencoded` 格式编码发送。

```yaml
- baseInfo:
    api_name: "表单登录"
    url: /api/login
    method: post
    header:
      Content-Type: application/x-www-form-urlencoded
  testCase:
    - case_name: "用户名密码登录"
      data:
        username: "admin"
        password: "123456"
      validation:
        - eq: { code: 200 }
```

---

## 5. 动态占位符：${} 函数全集

YAML 中所有字符串值（包括嵌套在 dict/list 中的字符串）都可以使用 `${函数名(参数)}` 占位符。运行时由 `base/apiutil.py` 的 `replace_load()` 方法解析，函数实现在 `common/debugtilk.py`。

### 5.1 使用位置

`${}` 可以在以下位置使用：
- `json:` / `params:` / `data:` 的值中（任意嵌套层级）
- `validation:` 的值中
- `header:` 的值中（如需动态 header）

### 5.2 函数列表

#### `random_plates(count)`

生成指定数量的不重复随机车牌（全局去重，已生成过的不会重复）。

| 参数 | 类型 | 说明 |
|:---|:---|:---|
| `count` | int | 生成个数；传 `"clear"` 清空已生成记录 |

```yaml
# 生成 1 个随机车牌
carNumber: ${random_plates(1)}

# 生成 4 个随机车牌，返回逗号拼接字符串
subCarNumbers: ${random_plates(4)}
```

#### `get_extract_data(key)`

从 `extract.yaml` 读取已存储的变量值。如果值是一个列表，返回列表的第一个元素。

| 参数 | 类型 | 说明 |
|:---|:---|:---|
| `key` | string | extract.yaml 中的 key |

```yaml
# 读取单个值
carNumber: ${get_extract_data(carInNumber)}

# 读取后在另一个接口中使用
fee: ${get_extract_data(unpaidFee)}
```

#### `get_extract_data_list(key, randoms)`

从 `extract.yaml` 读取列表值，支持随机和拼接。

| 参数 | 类型 | 必填 | 说明 |
|:---|:---|:---|:---|
| `key` | string | ✅ | extract.yaml 中的 key |
| `randoms` | int | ❌ | `None`(默认)=返回列表第一个元素；`0`=随机取一个；`-1`=逗号拼接为字符串 |

```yaml
# 默认取第一个
carNumber: ${get_extract_data_list(carInNumber)}

# 随机取一个
carNumber: ${get_extract_data_list(plates, 0)}

# 全部逗号拼接
carNumbers: ${get_extract_data_list(plates, -1)}
```

#### `get_extract_data(key, sec_node_name, randoms)`

读取嵌套的字典值。

| 参数 | 类型 | 必填 | 说明 |
|:---|:---|:---|:---|
| `key` | string | ✅ | extract.yaml 的一级 key |
| `sec_node_name` | string | ❌ | 二级 key，访问嵌套字典 |
| `randoms` | int | ❌ | 同 `get_extract_data_list` |

```yaml
# 读取 dict 中的嵌套字段
value: ${get_extract_data(configData, host)}
```

#### `get_current_time(fmt)`

获取当前时间字符串。

| 参数 | 值 | 输出示例 |
|:---|:---|:---|
| `"ydm"` | 年月日 | `"2026-07-18"` |
| `"hms"` | 年月日时分秒 | `"2026-07-18 14:33:00"` |

```yaml
startTime: ${get_current_time(ydm)}
time: ${get_current_time(hms)}
```

#### `get_offset_time(fmt, days, hours, minutes, seconds)`

获取**偏移后的时间**字符串。所有偏移参数可为负数（表示过去）。

| 参数 | 类型 | 必填 | 说明 |
|:---|:---|:---|:---|
| `fmt` | string | ✅ | `"ydm"` 仅日期，`"hms"` 日期+时间 |
| `days` | int | ❌ | 偏移天数，默认 `0`。负数=过去 |
| `hours` | int | ❌ | 偏移小时，默认 `0` |
| `minutes` | int | ❌ | 偏移分钟，默认 `0` |
| `seconds` | int | ❌ | 偏移秒数，默认 `0` |

```yaml
# ===== 常用场景 =====

# 明天日期（仅日期）
startDate: ${get_offset_time(ydm, 1)}

# 昨天此刻
startTime: ${get_offset_time(hms, -1)}

# 7 天后
endDate: ${get_offset_time(ydm, 7)}

# 2 小时后
startTime: ${get_offset_time(hms, 0, 2)}

# 30 分钟后
startTime: ${get_offset_time(hms, 0, 0, 30)}

# 30 分钟前
startTime: ${get_offset_time(hms, 0, 0, -30)}

# 明天上午 10 点 = 明天日期拼接固定时间
startTime: ${get_offset_time(ydm, 1)} 10:00:00

# 昨天下午 3 点
startTime: ${get_offset_time(ydm, -1)} 15:00:00

# 3 天后的下午 2 点半
endTime: ${get_offset_time(ydm, 3)} 14:30:00

# 过去 30 天（月卡起始）
startTime: ${get_offset_time(ydm, -30)}

# 未来 365 天（年卡截止）
endTime: ${get_offset_time(ydm, 365)}
```

> **技巧**：如果只需要日期偏移后拼接固定时分秒，用 `get_offset_time(ydm, N)` 取日期，后面直接拼 ` HH:MM:SS` 字符串。

#### `split_extract_data(key, index)`

从 `extract.yaml` 读取逗号拼接的字符串，按索引拆分取其中一个。

| 参数 | 类型 | 必填 | 说明 |
|:---|:---|:---|:---|
| `key` | string | ✅ | extract.yaml 中的 key |
| `index` | int | ❌ | 索引，0 开始，默认 0 |

```yaml
# extract.yaml 中 plates = "粤A11111,粤B22222,粤C33333,粤D44444"
# 取第 2 个
carB: ${split_extract_data(plates, 1)}    # → "粤B22222"
```

### 5.3 JSONPath 语法（用于 extract / input_extract / validation 表达式）

| 表达式 | 含义 | 示例 |
|:---|:---|:---|
| `$.field` | 根路径下的字段 | `$.retCode` |
| `$.data.field` | 嵌套字段 | `$.data.id` |
| `$.data.records[0]` | 数组第 1 个元素 | `$.data.records[0]` |
| `$.data.records[0].carNumber` | 数组第 1 个元素的字段 | `$.data.records[0].carNumber` |
| `$.data.records[*].id` | 数组所有元素的 id（用于 extract_list） | `$.data.records[*].id` |
| `$.json.field` | 请求参数 json 中的字段（用于 input_extract） | `$.json.carNumber` |
| `$.data.field` | 请求参数 data 中的字段（用于 input_extract） | `$.data.username` |

---

## 6. 数据提取：extract / extract_list / input_extract

### 6.1 extract — 从响应提取单个值

支持两种表达式：
- **以 `$` 开头** → JSONPath 解析（用于 JSON 响应）
- **其他** → 正则表达式，必须包含一个捕获组 `(.*?)`

```yaml
# JSONPath 提取：从响应 JSON 中取 $.data 的值，存为 testResourceId
extract:
  testResourceId: $.data

# JSONPath 提取：取 $.outPrice，存为 unpaidFee
extract:
  unpaidFee: "$.outPrice"

# JSONPath 提取：取数组第一个元素的 id
extract:
  queriedId: "$.data.records[0].id"

# 正则提取：从响应文本中匹配
extract:
  plateNumber: '"plateNumber":"(.*?)"'
```

### 6.2 extract_list — 从响应提取列表

同样支持 JSONPath 和正则两种表达式。`findall` 行为（正则）或全量匹配（JSONPath `[*]`）。

```yaml
# JSONPath 提取数组所有元素的 id
extract_list:
  allIds: "$.data.records[*].id"

# 正则全局匹配
extract_list:
  allPlates: '"carNumber":"(.*?)"'
```

### 6.3 input_extract — 从请求参数提取

**不依赖接口响应**，在请求发送后立即从 `actual_request_params`（实际发出的请求参数，已经过 `${}` 替换）中提取。

```yaml
# 从 json 参数中提取 carNumber，存入 myPlate
input_extract:
  myPlate: "$.json.carNumber"

# 从 data 参数中提取
input_extract:
  myUsername: "$.data.username"

# 简写形式：不写 $.json. 前缀，框架自动在 json/data/params 中查找
input_extract:
  carInNumber: carNumber
```

---

## 7. 断言体系：validation

`validation` 是一个**列表**，每项是一个**单键字典**，键为断言类型，值为断言参数。所有断言通过才认为用例成功；任一断言失败即抛 `AssertionError`。

### 7.1 eq — 相等断言

校验响应 JSON 中指定字段的值是否**等于**预期值。支持 JSONPath 表达式定位字段。

```yaml
# 单字段相等
validation:
  - eq:
      retCode: 1

# 多字段同时相等（同一个 eq 块内所有字段必须全部相等）
validation:
  - eq:
      paid: false
      payableAmount: 80

# JSONPath 定位嵌套字段
validation:
  - eq:
      $.msg: "success"
      $.data.total: 100
```

### 7.2 contains — 包含断言

校验预期字符串是否**出现在**响应 JSON 的指定字段中。

```yaml
# msg 字段中必须包含 "success"
validation:
  - contains:
      $.msg: "success"

# 组合使用
validation:
  - eq:
      retCode: 1
  - contains:
      $.msg: "success"
```

### 7.3 ne — 不等断言

校验响应 JSON 中指定字段的值是否**不等于**预期值。

```yaml
# retCode 不等于 0（验证不是失败）
validation:
  - ne:
      retCode: 0

# 验证 msg 不是空字符串
validation:
  - ne:
      $.msg: ""
```

### 7.4 db — 数据库断言

连接 MySQL 数据库执行 SQL 查询，验证能查到结果。

| 子字段 | 类型 | 必填 | 说明 |
|:---|:---|:---|:---|
| `sql` | string | ✅ | SQL 语句，占位符用 `%s` |
| `data` | list/tuple | ❌ | 填充 SQL 占位符的参数 |
| `one` | bool | ❌ | `true` 只取第一条结果，默认 `false` |

```yaml
# 验证数据库中存在某条记录
validation:
  - db:
      sql: "SELECT * FROM car_record WHERE car_number = %s AND status = %s"
      data: ["粤B12345", "active"]
      one: true
```

### 7.5 复合断言

```yaml
# 多个不同类型的断言组合
validation:
  - eq:
      retCode: 1
  - contains:
      $.msg: "success"
  - ne:
      $.data: null
```

---

## 8. 完整综合范例

以下是一个 YAML 文件集合，覆盖了本文档所述的**所有特性**，可直接作为 AI 生成 YAML 的参考模板。

### 8.1 单块结构 — 最常用模式（parametrize 消费）

```yaml
# ============================================================================
# 文件：step_demo_full.yaml
# 消费方式：test 方法中 @parametrize('params', get_testcase_yaml('...'))
# 覆盖特性：json 请求体、extract 提取、input_extract 提取、
#          ${} 占位符、eq + contains + ne 复合断言
# ============================================================================
- baseInfo:
    api_name: "创建并查询资源"
    url: /api/resource/create
    method: post
    header:
      Content-Type: application/json;charset=UTF-8
    # cookies:                                    # ← 可选，需要时取消注释
    #   sessionId: "abc123"

  testCase:
    - case_name: "动态车牌创建资源并验证"
      # ===== 请求参数 =====
      json:
        parkCode: "test"
        carNumber: ${random_plates(1)}            # ← 动态生成随机车牌
        startTime: ${get_current_time(ydm)}       # ← 当前日期
        endTime: "2029-12-31"
        owner: ${get_extract_data(testOwnerName)}  # ← 从 extract.yaml 读取

      # ===== 从请求参数提取（不依赖响应） =====
      input_extract:
        myPlate: "$.json.carNumber"              # ← JSONPath 提取 json 参数

      # ===== 从响应提取 =====
      extract:
        resourceId: "$.data.id"                  # ← JSONPath 提取
        resourceCode: "$.data.code"

      # ===== 断言 =====
      validation:
        - eq:                                    # ← 相等断言（多字段）
            retCode: 1
        - contains:                              # ← 包含断言
            $.msg: "success"
        - ne:                                    # ← 不等断言
            $.data: null
```

### 8.2 多块结构 — run_blocks 消费（跨步骤数据传递）

```yaml
# ============================================================================
# 文件：test_full_flow.yaml
# 消费方式：RequestsBase().run_blocks('./test_full_flow.yaml')
# 覆盖特性：多接口串联、跨步骤数据传递、extract + input_extract 协作
# ============================================================================

# ==================== 步骤1：创建资源 ====================
- baseInfo:
    api_name: "生成随机车牌并创建资源"
    url: /api/resource/create
    method: post
    header:
      Content-Type: application/json;charset=UTF-8
  testCase:
    - case_name: "创建测试资源"
      json:
        carNumber: ${random_plates(1)}
        startTime: ${get_current_time(ydm)}
        endTime: "2029-12-31"
      input_extract:
        createdPlate: "$.json.carNumber"
      extract:
        resourceId: "$.data.id"
      validation:
        - eq: { retCode: 1 }

# ==================== 步骤2：查询验证 ====================
- baseInfo:
    api_name: "查询资源"
    url: /api/resource/query
    method: get
  testCase:
    - case_name: "按步骤1创建的车牌查询"
      params:
        pageNum: 1
        pageSize: 10
        carNumber: ${get_extract_data(createdPlate)}    # ← 用步骤1 input_extract 的数据
      validation:
        - eq: { retCode: 1 }
        - contains: { $.msg: "success" }

# ==================== 步骤3：更新资源 ====================
- baseInfo:
    api_name: "更新资源"
    url: /api/resource/update
    method: post
    header:
      Content-Type: application/json;charset=UTF-8
  testCase:
    - case_name: "使用步骤1的资源ID更新"
      json:
        id: ${get_extract_data(resourceId)}             # ← 用步骤1 extract 的 ID
        endTime: "2030-12-31"
      validation:
        - eq: { retCode: 1 }

# ==================== 步骤4：数据库断言验证 ====================
- baseInfo:
    api_name: "数据库验证"
    url: /api/resource/query
    method: get
  testCase:
    - case_name: "数据库验证记录已更新"
      params:
        carNumber: ${get_extract_data(createdPlate)}
      validation:
        - eq: { retCode: 1 }
        - db:                                             # ← 数据库断言
            sql: "SELECT end_time FROM resource WHERE car_number = %s"
            data:
              - ${get_extract_data(createdPlate)}
            one: true
```

### 8.3 单 baseInfo 多 testCase — 批量操作

```yaml
# ============================================================================
# 文件：teardown_batch_delete.yaml
# 消费方式：fixture 中 RequestsBase().specification_yaml(yaml_data[0])
# 覆盖特性：多个 testCase 共享 baseInfo、数组请求体、列表提取
# ============================================================================
- baseInfo:
    api_name: "批量删除资源(后置清理)"
    url: /api/resource/delete
    method: post
    header:
      Content-Type: application/json;charset=UTF-8
  testCase:
    # ---------- 第 1 个用例 ----------
    - case_name: "删除资源A"
      json:
        - carNumber: ${get_extract_data(carABlack)}     # ← 数组请求体
      validation:
        - eq: { retCode: 1 }

    # ---------- 第 2 个用例 ----------
    - case_name: "删除资源B"
      json:
        - carNumber: ${get_extract_data(carBWhite)}
      validation:
        - eq: { retCode: 1 }

    # ---------- 第 3 个用例 ----------
    - case_name: "分页查询验证已全部删除"
      json:
        pageNum: 1
        pageSize: 100
        parkCode: "test"
      extract_list:                                    # ← 提取列表
        remainingIds: "$.data.records[*].id"
      validation:
        - eq:
            retCode: 1
        - eq:
            $.data.records: []                         # ← 验证数组为空
```

### 8.4 完整断言类型演示

```yaml
# ============================================================================
# 文件：assertion_all_types_demo.yaml
# 覆盖特性：eq(多字段+JSONPath)、contains、ne、db 全部断言类型
# ============================================================================
- baseInfo:
    api_name: "断言全集演示"
    url: /api/resource/query
    method: post
    header:
      Content-Type: application/json;charset=UTF-8
  testCase:
    - case_name: "全部断言类型演示"
      json:
        pageNum: 1
        pageSize: 10
        carNumber: ${get_extract_data(myPlate)}

      validation:
        # 1️⃣ eq：相等断言 — 单字段
        - eq:
            retCode: 1

        # 2️⃣ eq：相等断言 — 多字段并列
        - eq:
            $.data.total: 1
            $.data.records[0].carNumber: ${get_extract_data(myPlate)}

        # 3️⃣ contains：包含断言 — JSONPath 定位字段
        - contains:
            $.msg: "success"

        # 4️⃣ ne：不等断言
        - ne:
            $.data: null

        # 5️⃣ db：数据库断言
        - db:
            sql: "SELECT COUNT(*) as cnt FROM car_record WHERE car_number = %s"
            data:
              - ${get_extract_data(myPlate)}
            one: true
```

### 8.5 数据提取全模式演示

```yaml
# ============================================================================
# 文件：extract_all_modes_demo.yaml
# 覆盖特性：extract(JSONPath/正则)、extract_list(JSONPath/正则)、input_extract
# ============================================================================
- baseInfo:
    api_name: "数据提取全模式演示"
    url: /api/resource/batchCreate
    method: post
    header:
      Content-Type: application/json;charset=UTF-8
  testCase:
    - case_name: "演示所有提取模式"
      json:
        parkCode: "test"
        carNumbers: ${random_plates(4)}
        startTime: ${get_current_time(ydm)}
        endTime: "2029-12-31"

      # ===== input_extract：从请求参数提取 =====
      input_extract:
        # JSONPath 完整路径提取
        allPlatesFromReq: "$.json.carNumbers"
        # 简写：框架自动在 json/data/params 中查找
        park: parkCode

      # ===== extract：从响应提取单个值 =====
      extract:
        # JSONPath 提取
        batchId: "$.data.batchId"
        firstRecordId: "$.data.records[0].id"
        # 正则提取
        # recordCode: '"code":"(.*?)"'

      # ===== extract_list：从响应提取列表 =====
      extract_list:
        # JSONPath 提取数组所有元素
        allRecordIds: "$.data.records[*].id"
        # 正则全局提取
        # allCodes: '"code":"(.*?)"'

      validation:
        - eq: { retCode: 1 }
```

---

## 附录 A：支持字段速查表

### baseInfo 字段

| 字段 | 必填 | 说明 |
|:---|:---|:---|
| `api_name` | ✅ | 接口名称，字符串 |
| `url` | ✅ | 接口路径，不含域名 |
| `method` | ✅ | `post` / `get` / `put` / `delete` / `patch` |
| `header` | 条件 | 只写 Content-Type（json/表单体必填）；仅 params 或文件上传可省略；公共头由框架常量注入 |
| `cookies` | ❌ | 请求 cookies 字典 |

### testCase 字段

| 字段 | 必填 | 说明 |
|:---|:---|:---|
| `case_name` | ✅ | 用例名称，字符串 |
| `json` | 三选一 | JSON 请求体，dict 或 list |
| `params` | 三选一 | URL 查询参数，dict |
| `data` | 三选一 | 表单编码请求体，dict |
| `validation` | ❌ | 断言列表 |
| `extract` | ❌ | 从响应提取数据 |
| `extract_list` | ❌ | 从响应提取列表数据 |
| `input_extract` | ❌ | 从请求参数提取数据 |

### validation 断言类型

| 类型 | 语法 | 说明 |
|:---|:---|:---|
| `eq` | `- eq: { key: val, ... }` | 相等断言，支持多字段 |
| `contains` | `- contains: { $.key: "substr" }` | 包含断言 |
| `ne` | `- ne: { key: val }` | 不等断言 |
| `db` | `- db: { sql: "...", data: [...], one: bool }` | 数据库断言 |

### ${} 函数速查

| 函数 | 参数 | 说明 |
|:---|:---|:---|
| `random_plates(N)` | N=个数 或 "clear" | 生成随机车牌 |
| `get_extract_data(key)` | key | 读 extract.yaml 单值 |
| `get_extract_data_list(key, randoms)` | key, randoms(可选) | 读列表，0=随机取 -1=拼接 |
| `get_current_time(fmt)` | "ydm" 或 "hms" | 获取当前时间 |
| `get_offset_time(fmt, days, hours, minutes, seconds)` | fmt 必填(ydm/hms)，偏移量可选、可负数 | 获取偏移后的时间（明天/N天后/N分钟前等） |
| `split_extract_data(key, index)` | key, index(可选) | 拆分逗号字符串 |

---

## 附录 B：常见错误对照

| ❌ 错误写法 | ✅ 正确写法 | 原因 |
|:---|:---|:---|
| `method: POST` | `method: post` | method 必须小写 |
| `url: https://...` | `url: /path/to/api` | url 不含域名 |
| `- eq: retCode: 1` | `- eq: { retCode: 1 }` | eq 值必须是 dict 类型 |
| `validation: { eq: { retCode: 1 } }` | `validation: - eq: { retCode: 1 }` | validation 必须是列表 |
| `extract: { key: data.field }` | `extract: { key: $.data.field }` | JSONPath 必须以 `$` 开头 |
| `json:` / `params:` 同时写 | 只写一个 | 三选一，不能同时出现 |
| `case_name` 遗漏 | 每个 testCase 必须写 `case_name` | 必填字段 |
