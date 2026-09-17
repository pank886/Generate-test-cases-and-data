# 判定表链路：事实记录（2026-09-16）

> **本文件只记录事实，不含方案、不含建议、不含改动。**
> 用户 2026-09-16 原话：「你先记录事实，修改方案现在还没规划好，不定方案」。
>
> 来源：本日只读排查（判定表 xlsx / 生成侧源码 / 部署产物 / 用例产物 / 跑批日志）
> ＋ 两份只读盘点（三阶段工作流现状、现有校验器清单）。
>
> 标注：【今日实测】＝本文件写作当天当场复验；【此前排查】＝本日之前的同一轮排查所得
> （探针/跑批日志），本文件未重新复验，**如实标注不混同**。

---

## 一、判定表（外部权威输入）

【今日实测】openpyxl 读 `D:\0-导出文件\停车类型和费用计算-规则表.xlsx` sheet1。
列序：`用例 | 车辆类型 | 子场主场 | 临停准入开关 | 支持一人多车 | 其他场白名单 | 其他场包月 | 特殊车牌 | 动作 | 允许入场-主场 | 允许出场-主场 | 允许入场-子场 | 允许出场-子场 | 按临停计费`。
`\` = 原表「不适用」。表1-N ↔ sheet 行 N+2。

| 行 | 用例 | 车辆类型 | 临停准入开关 | 支持一人多车 | 动作 | 允许入场-主场 | 允许出场-主场 | 按临停计费 |
|--:|:--|:--|:--|:--|:--|:--|:--|:--|
| 10 | 表1-08 | 白名单一人多车 | `\` | **是** | A入主-B入主-A出主-B出主 | A、B是 | A、B是 | A、B否 |
| 11 | 表1-09 | 白名单一人多车 | **是** | **否** | A入主-B入主-A出主-B出主 | A、B是 | A、B是 | A否、**B是** |
| 12 | 表1-10 | 白名单一人多车 | **否** | **否** | A入主-B入主 | A是、**B否** | `\` | `\` |
| 21 | 表1-19 | 包月车一人多车 | `\` | **是** | A入主-B入主-A出主-B出主 | A、B是 | A、B是 | A、B否 |
| 22 | 表1-20 | 包月车一人多车 | **是** | **否** | A入主-B入主-A出主-B出主 | A、B是 | A、B是 | A否、**B是** |
| 23 | 表1-21 | 包月车一人多车 | **否** | **否** | A入主-B入主 | A是、**B否** | `\` | `\` |

行 13 = 表1-11（`入主-出主`，其他场白名单准入=**是**，按临停计费=否）；
行 24 = 表1-22（`入主-出主`，白名单准入列=**是**，车辆类型=包月车其他车场，按临停计费=否）。

---

## 二、产物事实（用例即事实）

### 2.1 逐例断言 ——【今日实测】读**部署目录**产物

| 用例 | 步数（由断言个数推定） | `openGate` 序列 | `parkingType` 序列 | 判定表要求 |
|:--|--:|:--|:--|:--|
| 表1-08 | 6 | true×4 | **4、2** | A、B 否（非临停）→ B 应为 4 ✗ |
| 表1-09 | 6 | true×4 | **4、4** | A 否、B 是 → B 应为 2 ✗ |
| 表1-10 | 2 | **true、false** | — | A 是、B 否 ✓ |
| 表1-19 | 6 | true×4 | 1、1 | A、B 否 → 1、1 ✓ |
| 表1-20 | 6 | true×4 | 1、**2** | A 否、B 是 → 1、2 ✓ |
| 表1-21 | 2 | **true、false** | — | A 是、B 否 ✓ |
| 表1-11 | 3 | true、true | 4 | 非临停 ✓ |
| 表1-22 | 3 | true、true | 1 | 非临停 ✓ |

### 2.2 前置夹具里的车辆记录构造 ——【今日实测】读**部署目录** `setup_data/`

| 夹具 | 接口 | 关键字段 |
|:--|:--|:--|
| `pre_rt_c_t1_08` | `carWhiteBlack/add` | `isWhite:1, multi:true, subCarNumbers:[…], carOwnerName:张三, enterStartDate:'2026-01-01'`，场 = **P3** |
| `pre_rt_c_t1_09` | `carWhiteBlack/add` | 同上（multi:true + subCarNumbers + owner + 有效期），场 = **P2** |
| `pre_rt_c_t1_10` | `carWhiteBlack/add` | 同上，场 = **P1** |
| `pre_rt_c_t1_19` | `mock/monthlyCar/add` | `mainCount:2, subCarNumbers:[…]`，场 = **P3** |
| `pre_rt_c_t1_20` | `mock/monthlyCar/add` | `mainCount:1, subCarNumbers:[…]`，场 = **P2** ← 今日 14:44:30 被单独改过 |
| `pre_rt_c_t1_21` | `mock/monthlyCar/add` | `mainCount:2, subCarNumbers:[…]`，场 = **P1** |
| `pre_rt_c_t1_11` | `carWhiteBlack/add` | `multi:false`，无 owner，建在上游场 **PO** |
| `pre_rt_c_t1_22` | `mock/monthlyCar/add` | `mainCount:1`，建在上游场 **PO** |
| `pre_rt_c_t1_12` | `mock/monthlyCar/add` | `mainCount:1`，无 `subCarNumbers`（普通单车主车） |

→ 四例「不支持一人多车」（表1-09/1-10/1-20/1-21）的**副车写法并不一致**：
白名单侧三例（08/09/10）一律 `multi:true + subCarNumbers + owner`；
包月侧 `mainCount` 在 表1-20（1）与 表1-21（2）上是两个值。

### 2.3 部署目录文件清单

`setup_data/` 共 **50 个**文件：`setup_parking_rule_table.yaml`（陈旧遗留）+ `pre_rt_001` + `pre_rt_c_t1_01…t1_23、t2_01…t2_13`（36）+ `pre_rt_g_p1…p6、po、ps1…ps3、s1`（11）+ `teardown_parking_rule_table.yaml`。【今日实测】

---

## 三、部署产物 vs 源码 ——【今日实测】`_redeploy_parking_rule_table.py --check`

```
相同 78 | 不同 10 | 仅部署侧有 1 | 仅重生成侧有 0
```

10 个「不同」的文件，与今日被单独改过的文件 **mtime 逐一对应**：

| mtime | 文件 |
|:--|:--|
| 10:45:35 | `test_t1_06_…switch_to_temp/test_data.yaml` |
| 10:48:55 | `test_t1_07_…switch_to_temp/test_data.yaml` |
| 10:54:03 | `test_t1_09_…unsupported_b_as_temp/test_data.yaml` |
| 11:00:52 | `setup_data/setup_parking_rule_table__pre_rt_g_p3.yaml` |
| 11:08:57 | `test_t1_17_…switch_to_temp/test_data.yaml` |
| 11:09:20 | `test_t1_18_…switch_to_temp/test_data.yaml` |
| 11:16:10 | `test_t2_01_free_park_no_payment/test_data.yaml` |
| 11:45:52 | `test_t1_16_…sub_as_temp/test_data.yaml` |
| 11:53:01 | `test_t1_08_white_list_multi_supported_two_cars/test_data.yaml` |
| 14:44:30 | `setup_data/setup_parking_rule_table__pre_rt_c_t1_20.yaml` |

- 本次被问的四例（表1-10 / 1-11 / 1-21 / 1-22）**不在这 10 个里**，与当场重生成逐字一致。
- 「仅部署侧有 1」= `setup_data/setup_parking_rule_table.yaml`（前置拆成 48 个 fixture 后已无引用，部署时不清残留）。
- 源码 vs 部署的分歧点【此前排查】：表1-09 断言 B（源码 `2` / 部署 `4`）、表1-08 断言 B（源码 `4` / 部署 `2`）、`c_t1_20` 的 `mainCount`（源码 `2` / 部署 `1`）。今日以「--check 的 10 个不同文件」佐证这几处确在分歧名单内。

---

## 四、生成侧源码事实

【今日实测】读 `rule_tables/parking_rule_map.yaml`、`rule_tables/yaml_kernel.py`：

| 项 | 事实 |
|:--|:--|
| 列→字段映射 | `park_flags`：临停准入开关→`carInConfig.tempCarIn`；**支持一人多车→`carInConfig.carWhiteOtherAsCasual`**（`values: {是: true, 否: false}`）；其他场白名单→`otherParkWhiteAccess`；其他场包月→`otherParkMonthAccess`；特殊车牌→`specialCarEnable`；是否收费→`parkingPark.needPay` |
| 车场分组 | P1 全 false（覆盖 表1-01/07/10/12/18/21）；P2 `tempCarIn: true`（1-06/09/17/20、表2-01）；P3 `carWhiteOtherAsCasual: true`（1-08/19）；P4 `otherParkWhiteAccess: true` + `otherParkWhiteAccessParks: [PO]`（1-11/22）；另有 P5/P6/S1/PS1–PS3/PO |
| 车辆记录配方 | `vehicle_recipes.一人多车` = `all_of: [一人多车]`、`kind_from: [白名单, 包月车]`、`records: [{scope: main, car: car_a}, {scope: main, car: car_b, sub_of: car_a}]` |
| 内核行为 | `_car_setup_steps_for_case` 对副车槽位**无条件**套 `spec["add"]["multi"]` 覆盖（`yaml_kernel.py:595`）⇒ 包月副车一律 `mainCount=2`；副车写 `input_extract_multi`，`label` 追加「（一人多车）」 |
| 注释口径 | `parking_rule_map.yaml:426-430`：「…车场侧是否放行副车牌由 `carInConfig.carWhiteOtherAsCasual` 决定，**不是**靠「有没有记录」——故此处不再按 flag 门控产不产 `car_b`」 |

【今日实测】两份前置草案（判定表支线开工前的产物，位于 `changelog/_phase_c_selective_2026-09-09/`）：

- `rule_table_cases_draft.md:61`：表1-10 的**前置条件写明**「车场是否支持一人多车白名单=**否**」，预期结果「A 是、B 否」；
  同文件 `:56` 表1-09 写「后车转临停」。→ **设计阶段有完整业务口径**。
- `rule_table_interface_map_draft.md:14`：「条件（车场是否支持一人多车白名单）」→ `carInConfig.carWhiteOtherAsCasual`，取值映射「是=`1` / 否=`0`」。
  该文件标题为**「草案 v1」**，§七 列 4 条「待你确认」，§五 全是缺口处置。
- `rule_table_interface_inventory.md:87-91`：三条引用铁律；`:93-127` 运行期实测（`mock/monthlyCar/query` 裸数组、`delete` 传车牌假成功、`add` 允许重复）。

---

## 五、接口侧实测结论 ——【此前排查】

| 项 | 实测结论 |
|:--|:--|
| `carWhiteOtherIsCasual` 方向 | `true` ⇒ 白名单副车按**临停**（实测得 `parkingType 2`）；`false` ⇒ 副车**仍是白名单**（得 `4`） |
| `tempCarIn` 对副车类型 | 无影响（单变量对照过） |
| 包月副车身份 | 由 `mock/monthlyCar/add` 的 `mainCount` 决定：`2` = 副车也算包月（得 `1`）；`1` = 副车落临停（得 `2` 并放行） |
| 表1-11（P4 只开 `otherParkWhiteAccess`） | 实测**被拒**；真实产品参考 `SmartParking/setup_other_parking_whitelist_permit.yaml` 是两个跨场开关**同开** |
| 表1-22 | 判定表的「是」标在白名单列，而车辆类型是**包月车**（接口侧包月跨场看 `otherParkMonthAccess`，P4 为 false）→ 归属待定 |
| 车场开关语义 | MANIFEST H19 根因 B2「车场开关语义未实现」：4 个「不支持」例 mock 未按判定表走（子车该转临停却保持原类型、子车该拒却放行、P3 连主车都拒）；属**接口侧**，已登记 |
| `carInConfig` 字段全集 | 11 个键，**没有**「一人多车」对应字段（旧登记 E5）；`carWhiteOtherAsCasual` 映射当初是**穷举挑的、未验证**（旧登记 E1） |

---

## 六、现有工作流事实（两份只读盘点结果）

### 6.1 三阶段主链

| 阶段 | 输入 | 输出 |
|:--|:--|:--|
| Phase A（文档摄取与向量化） | 产品文档 .md/.pdf/.docx/.xlsx、Axure zip、接口文档/YApi | SQLite `documents`/`document_chunks`/`api_*`/`glossary` + ChromaDB `doc_search` |
| Phase B（生成 Excel 测试计划） | `user_input` + 确认模块 + 多跳检索 | `test_plan.xlsx`（Sheet1 9 列 + Sheet2 5 列）、`api_defs.json`、`module_scope.json` |
| Phase C（确认计划 → 生成 .py + .yaml） | `test_plan.xlsx` + `api_defs.json` + `user_ctx` | `dependency_map.json`、`.py`、`setup_data/*.yaml`、`<func_en>/test_data.yaml`；失败写 `_generation_errors.json` / `_post_validation_issues.json` |

- 交接规则 M8：接口定义**靠产物传递**（`api_defs.json`），禁止内存态跨阶段交接。
- Sheet2「依赖前置」列是 **2026-09-15 新增的第 6 列**（`excel.py:229`），当前只用于 fixture 依赖排序。

### 6.2 `rule_tables/` 判定表支线（独立于 LLM 主链）

```
判定表 xlsx（外部权威）
  → parking_rule_map.yaml / registry.py（映射事实，单一事实源）
  → rule_rows.py（xlsx → RuleTablePlan 适配器）
  → yaml_kernel.py（确定性 YAML 内核，零 LLM）+ phase_c_export.py（接线，原样复用 py_export）
  → 框架 module_dir 产物
末端：tests/tools/_redeploy_parking_rule_table.py（导出+部署+自检；--check 只比对不写）
```

### 6.3 回炉 / 重跑机制（现状）

| 环节 | 机制 |
|:--|:--|
| Phase B | 修复轮 `EXCEL_REPAIR_ATTEMPTS=3`（只重填失败行，代码侧按 `failed_ids` 裁剪）；质量门 `<50%` 触发全量重生成，重试后仍 `<50%` abort |
| Phase C YAML | `_run_yaml_rounds` 第 1 轮全量 + N 轮修复（默认 1）；后校验 P0/P1 有独立预算 `_post_repair_budget` 注入**追加修复轮**；P2 仅告警 |
| Phase C 运行 | 定向重跑（只重跑实际失败用例 + 上游 setup）；失败数据双落点（原位日志 + 每 YAML 旁 `.last_run.json` sidecar，只留最新一轮）；「两轮 + 三层排查」判障协议 |
| 收尾 | `_redeploy --check` 比对「部署目录 vs 按当前源码重生成」——属运维自检环，**不在生产三阶段内** |

### 6.4 是否存在「接口探测」阶段

**没有正式的接口探测阶段或产物。** 接口事实当前散在四处：
① `changelog/_phase_c_selective_2026-09-09/rule_table_interface_inventory.md`（接口清单 + 角色 + 引用铁律 + 运行期实测）；
② `api_defs.json` 的 desc/示例值（由 `tests/tools/_apply_*_facts.py` 写回，同时同步 DB）；
③ SQLite `api_*` 存档 + `annotations` 注册表；
④ `MANIFEST.md` H9 的「已确认并已落映射（有实证）/ 待定（未实证，不猜 —— 需实跑或探针定）」两栏。
一次性探针脚本 `tests/tools/_probe_*.py` 是既有的取证手段（aux，不入系统运行）。

### 6.5 现有校验器清单（分层）

| 层 | 校验器 / 检查 |
|:--|:--|
| plan / 用例设计层 | `ExcelPlanValidator`（必填为空、悬空前置、步骤与预期行数、预期空行、`[[ ]]`、断言关键词空格/缺失、URL 拼写、db 断言禁止）；`_quality_gate_decision`；Pydantic `TestCaseRow`/`ExcelPlanV2` |
| YAML 层 | Pydantic `TestCase`/`StepData`/`TestData` + `ValidationInterceptor`（约 20 项，含占位符 B1–B4、断言键动态、export 断言、模糊 jsonpath）；`YamlPostValidator`（delete body 包裹 P0 / 动态断言 key P1 / 断言键白名单 P0 / 引号配对 P2）；`_scan_missing_key_refs`（D4）；`_scan_extract_value_forms`（V1–V5，只扫 `setup_*.yaml`） |
| 导出产物层 | `validate_excel_file`（表头/非空，仅 warning）；`_find_missing_yaml_refs`（`.py` 引用的 yaml 是否存在，不回炉） |
| 部署产物层 | `_redeploy_parking_rule_table.py::compare` |
| 判定表支线静态锁 | `tests/test_rule_table_yaml.py` 约 66 条（引用闭包 / 唯一化 / mock 面不断 retCode / parkingType 与车辆类型 / 有效期覆盖时间线 / 临停车牌逐例隔离 / 共享键单写 / 提取键无双写者 …）；`tests/test_rule_table_rows.py`（37 条总数、分组 covers、token 登记、标题唯一、翻译缓存覆盖） |
| 生成器侧接管（就地改写，非报错） | `_takeover_export_assertions`、路径参数替换、`_normalize_base_urls`、`_resolve_resource_conflicts`、`_sanitize_en`、`_parse_setup_extract_keys`、`_filter_teardown_missing_pres`、`_relax_teardown_validation` |

### 6.6 三条「检查盲区」事实 ——【今日实测】

1. `tests/test_rule_table_yaml.py:329 test_parking_type_matches_vehicle_kind` **只锁三例**（表1-01→4、表1-12→1、表1-24→2）——**不含**一人多车各例。
2. `tests/test_rule_table_yaml.py:323 test_ab_denied_second_car` **只锁结论**「A true / B false」，不涉及「B 为什么该被拒 / 前置给 B 什么身份」——故它在**源码版就是绿的**（§二.1 表1-10 断言的成因无任何检查承接）。
3. `rule_tables/yaml_kernel.py:864` docstring 写「由单测 `_assert_coverage` 兜底报警」——全仓 grep **只有这一处**，该测试**不存在**。
4. 「判定表某条件列在用例里有无落点」与「断言与其自身前置是否自相矛盾」两类检查，在 LLM 主链**完全没有**；在 `rule_tables` 支线只有上述不完整的等价锁（见 1、2）。

---

## 七、未决（挂起，本文件未动任何一项）

| # | 项 | 状态 |
|:--|:--|:--|
| 1 | 表1-09 / 表1-20 是否纳入改动范围 | 上一轮问「为什么改09等无问题用例」，未决；本日已查明 表1-09 部署版断言 `4`（源码 `2`）、`c_t1_20` `mainCount` 部署 `1`（源码 `2`） |
| 2 | 部署目录中今日被单独改过的 10 个文件如何处置（重导出会覆盖手改） | 未决 |
| 3 | 表1-08 部署版断言 B=`2` 与判定表 R10「A、B 否」不符 | 本日新见，未决 |
| 4 | 表1-11 / 表1-22 归属（接口侧 vs 我方） | 需对照实验（用户执行）才能定 |
| 5 | 旧登记项 E1–E14（含 E11 `run_results` 映射、E12 `_redeploy` 三处、E5 `carInConfig` 无一人多车字段、E3 副车牌出场 400、E4 未覆盖口径） | 见 `changelog/_phase_c_selective_2026-09-09/MANIFEST.md` 与各 changelog，均未动 |
| 6 | 工作流修改方案（用户本轮目标：从本链条提炼固定工作流并优化 Phase A/B/C） | **用户 2026-09-16 明确「还没规划好，不定方案」** |

---

**本文件不含任何方案、建议或改动。** 方案待用户规划后另立。
