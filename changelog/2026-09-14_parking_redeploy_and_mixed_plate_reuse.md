# 2026-09-14 ParkingRuleTable：部署产物刷新 + 混合车场同槽位车牌复用

> 状态：**已执行**（2026-09-14，用户批准；执行结果见 §8）

## 0. 输入与范围

- 输入：09-14 09:29 跑批日志 `C:\Users\damai\PyCharmMiscProject\log\test.20260914092925.log`（+ `.log.1`）
- 结论：37 例 / 27 失败 / 59 处断言失败
- 归因后**我方数据生成问题**两条（根因 1、根因 2），本次只修这两条
- `bill/unpaid`（**账单-未缴费查询**）用户已确认为接口问题且已修复 → **不在本次范围**
- 未解业务项（不确定类）→ 本次不动，见 §6

## 1. 根因 1：部署产物与内核不一致（内核已修，产物未重导出）

### 证据（本次实测：部署目录 vs 当场重生成，逐字比对）

- 部署目录 `C:\Users\damai\PyCharmMiscProject\testcase\园区基线\智慧停车\ParkingRuleTable`
- 39 个文件中：**相同 26 / 不同 15 / 仅旧 2**（仅旧 2 个 = `__pycache__\*.pyc`）
- 15 个差异文件分三类：

| 文件 | 部署目录（09-11 17:13） | 当场重生成 |
|:---|:---|:---|
| `setup_data/setup_parking_rule_table.yaml` | `get_current_time(ydm)`×15、`get_offset_time(ydm,365)`×15、`PRE-004`×1、键 `preTempCarNumber`×1、`random_plates`×31 | 时间工厂表达式 **0**、`2029-01-01`×15、`2030-12-31`×15、`PRE-004`×13、`preTempCarNumber` **0**、`random_plates`×43 |
| 13 个 `test_t2_01…t2_13/test_data.yaml` | 引用**共用**键 `preTempCarNumber` | 引用**各例自有**键 |
| `test_ParkingRuleTable.py` | 119 行差异 **全部**是输出路径前缀（`./testcase/园区基线/…` vs `./../AppData/Local/Temp/…`），**语义零变化** | — |

### 映射到失败

- **H19 根因 A**：13 个表2 用例共用**一块**临停车牌 → mock 的在场/账单状态互相污染，后续每例 `bill/unpaid`（账单-未缴费查询）塌成 `fee:0`（无券用例同样中招）→ 9 例 `fee` 断言失败（表2-04/05/06/07/09/10/11/12/13）
- **H19 根因 B1**：有效期 `${get_current_time(ydm)}`→`${get_offset_time(ydm,365)}` = 2026-09-14→2027-09-14，**不覆盖**时间线基准 `base_date=2029-12-31` → 包月车/白名单记录被 mock 判为过期（`openGate=false` / `parkingType=null`）→ 15 处

### 修法

重跑导出。`rule_tables/yaml_kernel.py`（09-11 18:04）与 `rule_tables/parking_rule_map.yaml`（09-11 18:04）中这两处修复**已落地**，无需改动内核逻辑。

## 2. 根因 2：内核同槽位车牌被覆盖（**重生成也修不掉**）

### 位置与现象

- `rule_tables/yaml_kernel.py::_car_add_step`（配合 `_car_setup_steps` 传参）
- `body = copy.deepcopy(spec["add"]["body"])` 中的 `carNumber: "${random_plates(1)}"` 是**每条记录各抽一次**；
  而提取键 `k_car_number(case, car)` 只按「用例 + 槽位」命名 → 同一槽位出现两次时，第二次覆盖第一次的键
- 命中面：仅 `vehicle_recipes.混合_主场白名单_子场包月`（表1-23）。该 recipe 两条记录同取 `car: car_a`：

```
'表1-23: 新建白名单'   carNumber=${random_plates(1)}   input_extract={'preCarT1_23ANumber': …}
'表1-23: 新建包月车'   carNumber=${random_plates(1)}   input_extract={'preCarT1_23ANumber': …}   ← 覆盖
```

- 09-14 跑批实测两牌：**湘BHFL9H**（白名单 @ 主车场 PS1M）、**云UH73VG**（包月 @ 子车场 PS1S）；
  键最终值 = 云UH73VG → 该用例**全场次**都用云UH73VG → 主车场白名单记录查不到 →
  `carTypeQuery`（**车牌类型查询**）拿不到 `parkingType=4` → 断言失败

### 语义确认（双向印证，非拍定）

1. 数据层 recipe 声明两条记录**同槽位**（`car: car_a`）
2. 表1-23 的用例 YAML 全程**只用一个**车牌键做 入主 → 类型查 → 入子 → 出子 → 出主

⇒ 结论：**同一块牌**在主场是白名单、在子场是包月车。故应改**内核**，而非数据层。

### 修法（最小改动）

- `_car_setup_steps`：每用例维护一张「已生成槽位」表
- `_car_add_step`：槽位**首次**出现 → 按现状（`carNumber: ${random_plates(1)}`，写 `input_extract`）；
  槽位**再次**出现 → `carNumber` 改为复用首块牌 `${get_extract_data('<键>')}`，且**不再写** `input_extract`（避免覆盖）
- 影响面：仅表1-23 命中；其余用例每槽位仅一条记录 ⇒ 产物**逐字不变**（由测试反向锁定）

## 3. 变更清单

| # | 对象 | 动作 |
|:---|:---|:---|
| 1 | `rule_tables/yaml_kernel.py` | `_car_setup_steps` + `_car_add_step` 的上述最小改动 |
| 2 | 部署产物 | `phase_c_export.export(plan, output_base, keep_data=True)`，`output_base = C:\Users\damai\PyCharmMiscProject\testcase\园区基线\智慧停车`（= `meta.module_dir` 上一级） |
| 3 | 测试 | 新增 §4 的 **A/B/C 三组**（原则④）；D 组**暂不做**（待裁决） |
| 4 | 文档 | 本文件补「实际结果」节（原则⑤） |
| 5 | 验证 | 只跑仓内单测；**不跑接口实跑**（用户手动跑） |

### 变更 2 的写出物（已核实 `phase_c_export.py`）

- `智慧停车/ParkingRuleTable/`：39 个 YAML + `test_ParkingRuleTable.py`
- `智慧停车/test_plan_parking_rule_table.xlsx`（**独立文件名**，不覆盖你现有的 `test_plan.xlsx`）
- `智慧停车/translation_cache.json`（**合并写**，SmartParking 既有条目保留）
- **不删** `__pycache__`，不动该目录下其它文件

## 4. 测试用例设计（原则④）

### A. `tests/test_rule_table_yaml.py::TestMixedScopeSharesPlate`

- **A1 正向**：表1-23 两条 add —— 第一条 `carNumber == "${random_plates(1)}"`；第二条 `carNumber == "${get_extract_data('preCarT1_23ANumber')}"`
- **A2 反向**：`preCarT1_23ANumber` 在整份 setup 中**只被写一次**

### B. `TestPlateExtractKeyWrittenAtMostOnce`（通用不变式，同族 bug 兜底）

- 遍历所有 setup 步骤的 `input_extract`，任一键出现 ≥ 2 次即红

### C. `TestOtherCasesStillDrawIndependentPlates`（防我改过头）

- 表1-01…1-22 各例自己的 add 体仍是 `${random_plates(1)}`
- 不锁死 `random_plates` 总数（避免以后新增用例即误红）

### D. `tests/test_rule_table_artifact_consistency.py::TestDeployedArtifactsMatchSource`

> ⏸ **2026-09-14 用户质疑必要性（「测试有什么必要？」）→ 暂不做，待用户裁决。**
> 我复核后同意质疑成立，**收回原先「建议落地」**：
>
> - 它唯一能抓的是「源码改了、忘了重新导出」——**流程疏漏，不是代码缺陷**
> - 三条弱点：① 要读本仓之外的 `C:\Users\damai\PyCharmMiscProject\…`，跨项目耦合，为此得归一化路径前缀/日期戳/跳 `__pycache__`，测试本身变脆；② **只在有人跑本仓测试时才红**，不跑就等于零，防不住它本该防的那次；③ 真红了也改不了结局，唯一修法还是「去重新导出」，只是提早报信
> - 且原方案**自相矛盾**：把更强的杠杆（下面 D2「一条命令导出+部署」，从路径上消除「忘导出」）建议**不做**，却把更弱的 D1 建议**做**
>
> **二选一交给用户**：**要防线** → 做 D2；**不要防线**（守本次最小改动）→ D1/D2 都不做，把「改内核 → 必须重新导出」写进文档当纪律。

**做法**（若最终采纳）：把 `meta.module_dir` 里正在跑的产物，与「按当前源码当场 `export` + `generate` 到 tmp」的结果**逐文件比对**，不一致即红。

- 归一化：输出路径前缀、`\d{4}-\d{2}-\d{2}` 日期戳；忽略 `__pycache__`
- `module_dir` 或 xlsx 不存在 → `skip`（换机器不误伤）
- **要抓的故障**：源码改了、部署产物没跟着重新导出 —— 即本次 09-11 18:04 改内核、部署目录仍停在 17:13，09-14 跑批按旧产物跑失败却无人报警
- **为什么原来没抓住**：`TestRecordValidity` / `TestTempCarPlateIsolation` 等现有测试都在 `artifacts` fixture 里**重生成到 tmp**，
  比对的是源码自己跟自己，**不看部署目录**，所以产物与源码不一致时它们照样全绿

## 5. 验证方式

- `pytest tests/test_rule_table_yaml.py tests/test_rule_table_export.py tests/test_rule_table_rows.py -q`
  → 期望：现有 92 项 + §4 A/B/C 新增全绿（D 组若采纳再加 `tests/test_rule_table_artifact_consistency.py`）
- 导出后重跑「部署目录 vs 当场重生成」比对 → 期望：**不同 0**（除 `__pycache__`）

## 6. 需要你决策（原则③：方案外发现，我不顺手改）

| # | 项 | 结论 |
|:---|:---|:---|
| D1 | 是否落地 §4-D「部署产物 vs 源码重生成」比对测试 | **用户质疑必要性 → 暂不做待裁决**；我复核后收回原「建议落地」，见 §4-D 附注 |
| D2 | 是否把「导出 + 部署」固化成脚本（如 `tests/tools/_deploy_parking_rule_table.py`） | 用户未选；与 D1 二选一（要防线选 D2 / 不要防线则都不做） |
| D3 | 15 处有效期用**字面量** `2029-01-01 / 2030-12-31`（09-11 已定的 H19-B1 修法）是否照旧 | **照旧**，本次只重导出 |
| D4 | 未解业务项：`carWhiteOtherIsCasual` 列→字段语义（实测疑似反向）、表1-11 其他车场白名单、表1-24 特殊车牌、表2 `paid` 口径 | **本次不动**，维持「不确定」归类 |
| D5 | 是否同步更新 `_phase_c_selective_2026-09-09/MANIFEST.md` 的 H19 节 | **用户已准**：「除 1 之外可以先更新」→ 已新增 **H20** 节 |

## 7. 不做的事（逐条列明）

- 不动 `bill/unpaid`（账单-未缴费查询）相关断言（用户已确认属接口问题且已修复）
- 不改 `carWhiteOtherIsCasual` 映射、表1-11、表1-24、表2 `paid` 口径
- 不跑接口实跑、不重跑框架
- 不碰 `git`（无提交、无暂存、无 stash）
- 不动 `_phase_c_*` 历史目录与其它无关文件

## 8. 实际结果（2026-09-14 执行）

### 决策回填

- **D1 测试**：不做（用户质疑必要性，我方复核后同意并收回原建议，见 §4-D 附注）
- **D2 一条命令导出+部署**：**做**（用户「做」）→ 已落地 `tests/tools/_redeploy_parking_rule_table.py`
- D3 照旧、D4 不动、D5 已更新（MANIFEST 新增 H20）

### 变更 1：`rule_tables/yaml_kernel.py`

- `_car_setup_steps`：每用例新增 `plates = {}`（已抽过的车牌槽位），传入 `_car_add_step`
- `_car_add_step`：新增 docstring 说明故障与实证；`ix` 改为按需写入 —— 槽位首次出现才
  `body["carNumber"] = ${random_plates(1)}` 并写 `input_extract`，再次出现改
  `body["carNumber"] = _g(num_key)`（复用首块牌）且**不写** `input_extract`
- `subs` 分支同规则（带 `len(sub_slots)` 越界保护，行为与改动前一致的部分不受影响）

### 变更 2：部署产物（经 D2 脚本执行）

```
.venv/Scripts/python.exe tests/tools/_redeploy_parking_rule_table.py
```

部署后核对（部署目录实测）：

| 检查项 | 部署前 | 部署后 | 期望 |
|:---|:---|:---|:---|
| `get_current_time(ydm)` | 15 | **0** | 0 |
| `get_offset_time(ydm, 365)` | 15 | **0** | 0 |
| `2029-01-01` / `2030-12-31` | 0 / 0 | **15 / 15** | 15 / 15 |
| `PRE-004` | 1 | **13** | 13（= 临停用例数） |
| `preTempCarNumber` | 1 | **0** | 0 |
| `input_extract` 键总数 / 重复键 | 73 / `{preCarT1_23ANumber: 2}` | **85 / 无** | 无重复 |

表1-23 两条 add 的实测取值：

```yaml
- case_name: '表1-23: 新建白名单'
  input_extract: {preCarT1_23ANumber: $.json.carNumber}
  json: {carNumber: ${random_plates(1)}, ...}          # 抽牌 + 写键

- case_name: '表1-23: 新建包月车'
  json: {carNumber: ${get_extract_data('preCarT1_23ANumber')}, ...}   # 复用首块牌，无 input_extract
```

**自检（`--check`，部署目录 vs 按当前源码重生成）**：`相同 41 | 不同 0 | 仅部署侧有 0 | 仅重生成侧有 0`

### 变更 3：测试

新增 4 个（`tests/test_rule_table_yaml.py` §9）：

| 测试 | 断言 |
|:---|:---|
| `TestSharedSlotPlateReuse::test_repeated_slot_reuses_the_first_plate` | 同槽位二次出现必须是 `${get_extract_data('<键>')}` 且不重写 `input_extract`；首条必须抽牌并写键 |
| `TestSharedSlotPlateReuse::test_shared_key_is_written_once` | 被复用的车牌键只有一个写入者 |
| `TestPlateDispensedOncePerSlot::test_draws_equal_distinct_slots_within_each_case` | 每用例抽牌次数 == 不同槽位数；`carNumber` 只能是「随机牌」或「引用」两种形态 |
| `TestSetupExtractKeysAreWrittenOnce::test_no_extract_key_has_two_writers` | setup 里任一提取键（`input_extract`/`extract`/`extract_list`）不得有两个写入者 |

后两者都带**反向锁定**（有用例真的被检查到），不做静默通过。

**「测试能抓住 Bug」的反向验证**（把 `_car_add_step` 换回修复前实现，一次性脚本，不入库）：

```
✅ test_repeated_slot_reuses_the_first_plate        红了: 表1-23: 包月车 又抽了一块新牌 …，应复用 preCarT1_23ANumber
✅ test_shared_key_is_written_once                  红了: 这些车牌键有多个写入者: {'preCarT1_23ANumber': 2}
✅ test_draws_equal_distinct_slots_within_each_case 红了: 表1-23 抽了 2 块牌，但有 1 个槽位
✅ test_no_extract_key_has_two_writers              红了: 这些提取键有两个以上写入者: {'preCarT1_23ANumber': 2}
修复前实现下：4/4 个新测试能抓住该 Bug
```

### 变更 5：测试实跑结果

| 命令 | 结果 |
|:---|:---|
| `pytest tests/test_rule_table_yaml.py tests/test_rule_table_export.py tests/test_rule_table_rows.py -q` | **96 passed**（原 92 + 新增 4） |
| `pytest tests/ -q --ignore=tests/tools` | **26 failed, 1008 passed, 22 skipped, 1 xfailed** |

**26 红与本轮无关**（如实登记，未掩盖，也未修）：

- 三个失败文件 `test_workflow_api.py` / `test_phase_c_api.py` / `test_phase_bc_unit.py` **均不 import
  `rule_tables` / `yaml_kernel`**，本轮改动（`yaml_kernel.py` 两函数 + 新测试）无可达路径
- 原因两类：① **502 Bad Gateway**（web 服务未起，约 22 个）；② 更早改动留下的过期预期 ——
  prompt 措辞（`assert '数据唯一化' in <prompt>`，2 个）、业务 URL 前缀
  （`'/electricMeter/add' in {'/park-energy-electric-web/electricMeter/add', …}`，6 个，
  对应 `changelog/2026-09-09_parking_business_url_service_prefix.md`）

### 未做（本轮范围外）

- 未跑接口实跑（用户手动跑）
- 未动 D1 的比对测试、未改 `carWhiteOtherIsCasual` 映射 / 表1-11 / 表1-24 / 表2 `paid` 口径
- 未碰 git（无提交、无暂存）
