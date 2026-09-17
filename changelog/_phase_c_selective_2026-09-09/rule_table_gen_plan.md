# 判定表驱动生成 —— 落地方案（v1，待确认）

来源：`D:\0-导出文件\停车类型和费用计算-规则表.xlsx`（29 条：表1 24 + 表2 01/05/06/10/11）
格式依据：**Phase C**（`py_export.py::_generate_py_file` + `yaml_gen.py::_generate_all_yamls`）
实例参照：`C:\Users\damai\PyCharmMiscProject\testcase\园区基线\智慧停车\SmartParking\`（已生成的 82 用例）

---

## 一、从真实 setup 提炼的事实基线（**照抄，不臆造**）

> 来源：`SmartParking/setup_data/setup_temp_parking_charge_calculation.yaml`、
> `setup_monthly_car_process_rule.yaml`、`setup_entry_vehicle_process_rule.yaml`（均为 Phase C 已产出且可运行）

### 1.1 两个 URL 前缀（**必须逐字**）

| 面 | 前缀 |
|:---|:---|
| 配置面（车场/设备/收费规则/黑白名单） | `/park-access-parking-web/` |
| Mock 面 | `/park-access-parking-rule-new/mock/` |

### 1.2 字段类型（易错）

| 字段 | 类型 | 值 |
|:---|:---|:---|
| `carInConfig.*` 各开关 | **bool** | `true` / `false`（**不是 1/0**） |
| `parkingPark.needPay` / `needCount` | **int** | `1` / `0` |
| `carWhiteBlack.isWhite` | int | `1`白 / `0`黑 |
| `carWhiteBlack.status` | str | `'3'` |
| 收费规则 `configType`/`unitPrice`/`freeTime`/`maxParkingFee`/`parkingLotType` | str | `'3'` / `'10'` / `'45'` / `'240'` / `'1'` |
| 设备 `passType` | str | 入口 `'1'` / 出口 `'2'` |
| 设备 `deviceType`/`deviceStatus`/`needToShow` | str | `'1'` |

### 1.3 三条**回读链**（照抄，一条都不能省）

**① 收费规则 → `bindPayCode`（V1–V5 防的就是这里）**
```
parkingPaymentConfig/add          json: {name: ${random_code('RULE-...')}, ...}
parkingPaymentConfig/getParkingPaymentConfigPage   extract: <k>PayCode = $.data.list[0].code
parkingPark/saveOrUpdate          parkingPark.bindPayCode: ${get_extract_data('<k>PayCode')}
```

**② 车场 → `parkCode`**
```
parkingPark/saveOrUpdate          input_extract: <k>ParkName = $.json.parkingPark.parkName
parkingPark/getAllWithDevices     json: {mainPark, parkName: ${...ParkName}}
                                  extract: <k>ParkCode = $.data[0].parkingPark.parkCode
```

**③ 白名单 → 删除用 `code`**
```
carWhiteBlack/add                 input_extract: <k>CarNumber = $.json.carNumber
carWhiteBlack/getPage             json: {isWhite:1, pageNum:1, pageSize:10,
                                         searchKey: ${...CarNumber}, parkCode: ${...ParkCode}}
                                  extract: <k>WhiteCode = $.data.list[0].code
carWhiteBlack/delete              json: [${get_extract_data('<k>WhiteCode')}]
```

**④ 包月车 → 删除用 `code`（**2026-09-11 实测确定**）**
```
mock/monthlyCar/add               input_extract: <k>CarNumber = $.json.carNumber
                                  validation: contains: {success: true}
mock/monthlyCar/query             json: {carNumber: ${...CarNumber}, parkCode: ${...ParkCode}}
                                  extract: <k>MonthCode = $.[0].code      ← 裸数组！（§一.7）
mock/monthlyCar/delete            json: {codes: [${get_extract_data('<k>MonthCode')}]}
```
> ⚠️ `mock/monthlyCar/query` 返回**裸数组**（非 `{retCode,msg,data}`），api_defs 记错；
> ⚠️ `delete` 传**车牌**会「假成功」（返 `success:true` 但不删），**必须传真实 code**。

### 1.4 设备**不需要 getPage 回读**

`parkingDevice/add` 的 `code` 自带 `${random_code('DEV-...')}`，直接
`input_extract: <k>DeviceCode = $.json.code` 即可 → 有两条真实 setup 都用这个写法。
**（更正我上一轮说的「设备要 getPage 回读」）**

### 1.5 唯一化（R4 两个新升类别的根因）

现网 setup 里存在 `parkName: PK-HOME`（**字面量**）与 `bindPayCode: RULE-HOME`（**字面量**）
→ 正是 Round 4「此车场名称已存在」2→10 的根因。
**生成器强制**：所有 `parkName` / `name` / `code` 一律 `${random_code(...)}` 或 `${random_plates(1)}`，
`bindPayCode` **一律回读**，禁止字面量。

### 1.6 断言形态（两种都在用）

```yaml
validation:
- contains: {status_code: 200}
- eq: {$.retCode: 1}            # 或 contains: {retCode: 1, msg: success}
```

---

## 二、产物文件树

### 2.1 输出侧（生成到框架里）

```
C:\Users\damai\PyCharmMiscProject\testcase\园区基线\智慧停车\
└── ParkingRuleTable\                             ← 新 feature 目录（不动现有 SmartParking/）
    ├── __init__.py
    ├── test_ParkingRuleTable.py                  ← 1 story = 1 class = 29 methods
    ├── setup_data\
    │   ├── setup_parking_rule_table.yaml         ← 共享前置：10 组 / 13 实体车场 + 设备 + 收费规则
    │   └── teardown_parking_rule_table.yaml      ← 统一删除
    └── test_<case_en>\test_data.yaml             ← 29 个（1 用例 = 1 目录 + 1 YAML）
```

### 2.2 仓库侧（本仓库新增）

```
E:\Generate-test-cases-and-data\
└── rule_tables\
    ├── parking_rule_map.yaml      ← 映射数据文件（唯一事实源）
    ├── registry.py                ← 加载器（照 data_factory/registry.py 模式）
    └── rule_rows.py               ← 判定表 xlsx → Phase C 行结构（适配器）
```

---

## 三、映射数据文件 schema（`rule_tables/parking_rule_map.yaml` 骨架）

> 照 `data_factory/methods.yaml` 的 `categories/name/description` 风格。

```yaml
meta:
  name: 智慧停车判定表映射
  version: "1.0"
  source_xlsx: D:\0-导出文件\停车类型和费用计算-规则表.xlsx
  module_dir: C:\Users\damai\PyCharmMiscProject\testcase\园区基线\智慧停车\ParkingRuleTable

# ① 条件列 → 车场配置字段
park_flags:
  - column: 车场临停准入开关
    field: carInConfig.tempCarIn
    type: bool
    values: {是: true, 否: false}
  - column: 车场是否支持一人多车白名单
    field: carInConfig.carWhiteOtherIsCasual
    type: bool
    values: {是: true, 否: false}
  - column: 其他车场白名单准入
    field: carInConfig.otherParkWhiteAccess
    type: bool
    values: {是: true, 否: false}
  - column: 其他车场包月车准入
    field: carInConfig.otherParkMonthAccess
    type: bool
    values: {是: true, 否: false}
  - column: 是否特殊车牌放行
    field: carInConfig.specialCarEnable
    type: bool
    values: {是: true, 否: false}
  - column: 是否收费
    field: parkingPark.needPay
    type: int
    values: {是: 1, 否: 0}

# ② 车辆类型 → 数据构造动作（setup 片段）
vehicle_types:
  白名单:
    add:   {api: carWhiteBlack/add, body: {...}}
    readback: {api: carWhiteBlack/getPage, path: $.data.list[0].code}
    delete: {api: carWhiteBlack/delete, body: [<code>]}
  包月车:
    add:   {api: mock/monthlyCar/add, body: {...}}
    readback: {api: mock/monthlyCar/query, path: $.code}     # ⚠️ 顶层 or 数组，运行期核实
    delete: {api: mock/monthlyCar/delete, body: [<code>]}
  临停车: {add: none}          # 无记录即临停
  特殊车牌:
    add: {api: parkingPark/saveOrUpdate, path: specialAccessConfigList}

# ③ 动作列 → 触发 + 断言
actions:
  允许入场-主场: {trigger: {api: mock/access/enter, device: <主场入口>}, assert: {openGate: true}}
  允许出场-主场: {trigger: {api: mock/access/exit,  device: <主场出口>}, assert: {openGate: true}}
  允许入场-子场: {trigger: {api: mock/access/enter, device: <子场入口>}, assert: {openGate: true}}
  允许出场-子场: {trigger: {api: mock/access/exit,  device: <子场出口>}, assert: {openGate: true}}
  按临停计费:    {query: {api: mock/carTypeQuery, body: {carNumber, parkCode, startTime}},
                 assert: {parkingType: '2'}}
  是否缴费:      {query: {api: mock/bill/latest, body: {carNumber}}, assert: {paid: ...}}
  缴费金额:      {query: {api: mock/bill/unpaid, body: {carNumber, currentTime}}, assert: {fee: N}}

# ④ 车场分组（§六 落地）
park_groups:
  - {id: P1, struct: 主场, flags: {tempCarIn: false, carWhiteOtherIsCasual: false, needPay: 0}}
  # … P2–P6 / S1 / PS1–PS3
```

---

## 四、生成算法

```
判定表.xlsx ──(rule_rows.py)──▶ Phase C 行结构
    {feature, story, title, steps, expected, preconditions, case_id}
                          │
        ┌─────────────────┴──────────────────┐
        ▼                                    ▼
  ① py_export.py::_generate_py_file     ② yaml 内核（新）
     （原样复用：骨架/命名/路径/断言接管）    （确定性构造，不走 LLM）
```

**② yaml 内核**（新写，替代 `yaml_gen._generate_one_yaml_single` 的 LLM 调用）：
1. 读 `parking_rule_map.yaml` 的 `park_groups` → 生成 `setup_<slug>.yaml`（10 组车场 + 设备 + 收费规则）
2. 逐用例：条件列 → 选组；动作列 → 有序生成 `enter/exit/bill` 步骤 + 断言
3. 统一 `teardown_<slug>.yaml`
4. 复用后校验：`_find_missing_yaml_refs` + V1–V5 + `_takeover_export_assertions`

---

## 五、复用 vs 新建

| 组件 | 处置 |
|:---|:---|
| `py_export.py::_generate_py_file` | ✅ **原样复用** |
| `_takeover_export_assertions` | ✅ 原样复用 |
| `yaml_gen.py` 的目录布局 / 三阶段顺序 | ✅ 复用**布局**，替换生成内核 |
| `_find_missing_yaml_refs` / V1–V5 / P0 回炉闸门 | ✅ 原样复用 |
| `data_factory/registry.py` | 📋 **照抄模式** → `rule_tables/registry.py` |
| YAML 步骤构造 | 🆕 新写（确定性） |
| 判定表 → 行 适配器 | 🆕 新写 |

---

## 六、测试用例设计（原则④）

| 层 | 用例 | 断言 |
|:---|:---|:---|
| **映射文件** | 每个条件列取值齐全、每个动作列有 trigger/assert | 无缺键、类型符 |
| **适配器** | 37 条 → 29 条（8 券条被过滤）；行结构字段完整 | count=29、字段齐 |
| **YAML 内核** | 逐用例快照：步骤数 / api 顺序 / 断言值 | 与 §四逐条落点表一致 |
| **回读链** | `bindPayCode` 无字面量（V1–V5 扫）；白名单 delete 前必有 getPage | 0 处 P0 |
| **唯一化** | 全量扫 `parkName`/`name`/`code` 无字面量 | 0 处 |
| **端到端** | `python run.py -p testcase/园区基线/智慧停车/ParkingRuleTable` | 见「跑两轮」协议 |

---

## 七、已定（2026-09-11 用户决策）

| # | 事项 | 决策 |
|:---|:---|:---|
| 1 | feature 目录名 | **新建 `ParkingRuleTable/`**（不动现有 82 用例） |
| 2 | story 划分 | **统一前置** → 29 条 = **1 story / 1 class / 1 `setup_<slug>.yaml`** |
| 3 | `mock/monthlyCar/query` 形状 | ✅ **已实测** → 裸数组，`$.[0].code`；delete 传车牌假成功 → 见 §一.3④ |
| 4 | 特殊车牌 | 车牌用 **`我A12345`** |

## 八、接口事实缺口（原则③登记，待你定）

实测发现 **`api_defs.json` 的 `mock/monthlyCar/query` 记录有误**：

| 项 | api_defs 记的 | 实测 |
|:---|:---|:---|
| 响应外壳 | `{retCode, msg, data}` | **裸数组** |
| `code` 位置 | 顶层（`$.code`） | **`$.[0].code`** |

**另**：`mock/monthlyCar/delete` 传车牌返回 `success:true` 但**不删**（假成功）—— 属**接口缺陷**，不是我们的数据问题。

→ 处置建议（二选一，请定）：
- **A**：按 `tests/tools/_apply_parking_api_defs_facts.py` 同款脚本**修正 api_defs.json + DB**（把 query 的响应形状改成裸数组 / `$.[0].code`）
- **B**：只在本方案文档登记，**不改** api_defs（生成器硬编码 `$.[0].code`）

**已决（2026-09-11）：选 A**，已由 `tests/tools/_apply_monthlycar_query_shape_fact.py`
落盘（9 处 desc 修正 + `--sync-db`），幂等复跑 0 变更。见 MANIFEST **H7**。

---

## 九、落盘接线（2026-09-11 实现，`rule_tables/phase_c_export.py`）

§四 的两条产线是**分开调用**的，接线层只负责把 `RuleTablePlan` 变成
`_generate_py_file` 认得的输入，**不改生成器一行代码**：

```
plan ──phase_c_export.export()──▶ ①写 Excel ②写翻译缓存 ③调 _generate_py_file
     └─phase_c_export.export_yaml()──▶ yaml_kernel.generate()
```

### 9.1 为什么 Excel 必须落在 `智慧停车/`

`_generate_py_file` 里 `output_base = os.path.dirname(excel_path)`，随后
`os.makedirs(output_base/feature_en)`。**唯一能让产物落进 `ParkingRuleTable/`
的位置，就是 `智慧停车/`**。该目录已有 SmartParking 的既有产物，故：

| 文件 | 处置 |
|:---|:---|
| `test_plan.xlsx`（SmartParking 的） | **不动**。本批用独立名 `test_plan_parking_rule_table.xlsx` |
| `translation_cache.json` | **合并写**，不覆盖 |

### 9.2 翻译缓存为什么必须合并

缓存是「中文 → 英文」字典，按 `<excel目录>/translation_cache.json` **单文件共享**。
直接覆写会抹掉 SmartParking 的 67 story / 81 title，**下次那批重跑就会掉进 LLM/拼音
降级、目录名漂移**。实测合并后：`title_en 81 → 118`，SmartParking 条目逐条仍在
（`tests/test_rule_table_export.py::TestTranslationCache` 锁定）。

补一层保险：`_translate_to_en` **只在有未命中项时**才回写缓存；本批命名全命中 →
该文件在生成期根本不被生成器触碰。

### 9.3 零 LLM

命名全走缓存 ⇒ `all_uncached` 为空 ⇒ 不调 `_invoke_structured`。接线层用
`object.__new__(ChatTestAgentGraph)` 绕过 `__init__`（不需要 LLM 配置/API key），
并补 `_run_timestamp` / `_run_data` 两个运行态字段（`_log_node_output` 要读）。
测试里把 `_invoke_structured` / `_invoke_think` 打桩成抛异常 —— 一旦触到即失败。

### 9.4 落地事实（2026-09-11 实跑）

| 项 | 值 |
|:---|:---|
| Excel | `智慧停车\test_plan_parking_rule_table.xlsx`（29 行 + 1 共享前置） |
| `.py` | `智慧停车\ParkingRuleTable\test_ParkingRuleTable.py`（1 class / 29 methods / order 1–29） |
| fixture | `setup_parking_rule_table` → `run_blocks(.../setup_data/setup_parking_rule_table.yaml)` |
| YAML | 31 个（1 setup + 1 teardown + 29 `test_data.yaml`），与 `.py` 同目录 |
| `run_blocks` 路径 | `./testcase/园区基线/智慧停车/ParkingRuleTable/...`（`PYCHARM_MISC` 相对） |
