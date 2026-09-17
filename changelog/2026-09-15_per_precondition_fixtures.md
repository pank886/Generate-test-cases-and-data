# 前置按「全局 / 每组 / 每例」拆成 48 个 fixture（2026-09-15）

## 一、要解决的问题

Allure 报告里每条用例的 Set up 都是**同一坨 122 步**：37 条用例共用 `setup_parking_rule_table`
一个 class 级 fixture，里面既有清场、收费规则，也有**全部 14 个车场**的建场/回读/设备，
还有**其它用例的**车辆记录和临停车牌。用户原话：

> 所有的前置都放在一块了，不能一些测试用例，共有的前置，展示时只展示自己的前置么？

### 为什么装饰器解决不了

`allure_pytest/listener.py::_update_fixtures_children(item)` 走
`_test_fixtures(item)` → `item._request.fixturenames`（传递闭包），即

**fixture 容器的 children = 所有（直接或间接）引用它的用例**，无 CLI 开关可关。

所以「展示粒度」= 「引用粒度」= 「执行粒度」——三者是同一个东西。要让每条用例只显示
自己的前置，只能真的**按用例引用不同的 fixture**；而要引用不同 fixture，就必须把它们
拆成不同的 fixture。方案 ②（三级拆分）由此而来，用户 2026-09-15 拍板「按2改吧」。

## 二、拆法（并集恒等于原来的 122 块）

| 级 | 前置编号 | 内容 | 块数 | fixture 数 |
|:--|:--|:--|--:|--:|
| 全局 | `PRE-RT-001` | 清场 + 收费规则（主场/子场 add + 分页回读） | 5 | 1 |
| 每组 | `PRE-RT-G-<组>` | 该组车场的建场 + 回读 parkCode + 入口/出口设备 | 56 | 11 |
| 每例 | `PRE-RT-C-<用例>` | 该例的车辆记录（add + 回读）；临停车例的随机车牌 | 61 | 36 |
| | | | **122** | **48** |

* 11 组 = PO、P1–P6、S1、PS1、PS2、PS3（PS\* 组的 M+S 两个实体在**同一个** fixture 内，
  因为子场建场要用主场的 parkCode）；
* 36 例 = 22 例各 2 块 + 表1-23 四块（两个根车辆）+ 13 例临停车牌各 1 块；
  **表1-24 没有自有块**（只有特殊车牌，随车场 payload 的 `specialAccessConfigList` 写入，
  由它所属的 P5 组 fixture 承担），故不生成 fixture。

### 组间依赖（写进 Sheet2「依赖前置」列）

| 依赖 | 来源 | 为什么 |
|:--|:--|:--|
| 全部 11 组 + 36 例 → `PRE-RT-001` | 清场必须先、收费规则要先有 payCode | 每条用例都要 |
| `PRE-RT-G-P4` → `PRE-RT-G-PO` | 表1-11/1-22 的 `otherParkWhiteAccessParks` 要 PO 的 parkCode | 原代码靠「PO 先于 P4」的实体顺序保证 |
| `PRE-RT-G-S1` → `PRE-RT-G-P1` | S1 是 P1 的子场（`parent=P1`） | 子场建场要主场 parkCode |

## 三、文件与改动面

| 文件 | 改动 |
|:--|:--|
| `rule_tables/yaml_kernel.py` | 新增 `_setup_blocks` / `build_setup_parts` / `group_deps` / `has_own_blocks` / 前置编号命名；`build_setup` 改为**按原顺序**重组底层块（旧顺序、旧内容逐字保持）；`generate()` 按三级写 `setup_data/setup_parking_rule_table__<编号>.yaml`（48 个） |
| `rule_tables/rule_rows.py` | Sheet1 每行 `fixture_level=逐前置`、`preconditions` 逐条列出该例真实需要的编号；Sheet2 由 1 行变 48 行，新增「依赖前置」列 |
| `agent_components/generators/py_export.py`（**共用**） | 新增「逐前置」分支：每个编号一个 `scope="class"` fixture（依赖走 fixture 参数）、`usefixtures` 落到**方法**上、fixture 中文显示名走 `__allure_display_name__`（设在**原始函数**上，pytest 包装后读不到）；**非该模式的分支逐字保持原样** |
| `agent_components/generators/excel.py` | `_read_excel_rows` / `_read_shared_preconditions` 读第 6 列（`row[5]`）—— 读不到就是空串，不影响旧 plan |
| `rule_tables/phase_c_export.py` | Sheet2 表头加「依赖前置」（第 6 列） |
| `tests/test_rule_table_export.py` | 受影响的那几条锁按新形状更新 |
| `tests/test_rule_table_fixture_split.py`（新增） | 见下 |

### 开发中查出的一个真缺陷：只被依赖、没有用例直接引用的编号

`py_export._per_pre_fixtures` 起初按 **Sheet1 用到的编号**收集 fixture，于是 PO 组
（**没有任何用例直接引用它**，只有 P4 的「依赖前置」指向它）没有落成 fixture，
而 P4 的 fixture 参数里写着 `setup_parking_rule_table__pre_rt_g_po` —— pytest
**跑到引用 P4 组的用例时**就报 `fixture not found`（整例 ERROR，不是断言失败；
实测 `--collect-only` 收得下、真正 setup 时才炸 —— 引用 P4 组的是表1-11 / 表1-22）。
已改为先做**依赖闭包**再生成；回归锁见
`tests/test_rule_table_fixture_split.py::TestDependencies::test_closure_includes_ids_only_depended_upon`。
（这个缺陷是靠「fixture 数 == 48」这条锁发现的 —— 当时数出来是 47。）

### 为什么 `other_codes` 要改传法

原来 `build_setup` 用**累积字典** `other_codes`（边建边塞 `eid → parkCode`）传给
`reg.get_park_spec`，而 `get_park_spec` 只按 `*AccessParks` 里的分组 id 取值
（`codes.get(g,"")`）——全库只有 P4→PO 一处，且 `eid == 分组 id == "PO"`。
按组拆开后字典的累积范围变了，故改成**按该实体自己的 `other_parks` 精确取值**，
唯一可达的一处逐字不变（有测试锁 + 与改动前的落盘产物逐字比对）。

## 四、测试设计

1. **块划分锁**：`build_setup_parts` 三级并集逐块 == 原 `build_setup` 的 122 块；
   硬锁 5 / 56 / 61 与 11 组 / 36 例；块内容两两不重复（无一块同时属于两级）。
2. **旧顺序还原锁**：`build_setup(plan)` 的落盘文本与**改动前**的
   `setup_parking_rule_table.yaml` 逐字相同（golden 见 §六；这一步是**开发期手工比对**，
   进不了 CI —— 仓库里落的是可复现的那半：块内容逐块相等 + 顺序契约，
   见 `test_rule_table_fixture_split.py::TestLegacyOrder`）。
3. **组内自足锁**：每组 fixture 的块里只出现本组自己的 `ParkCode/ParkName/设备`
   提取键；每例 fixture 的块只涉及该用例自己的车辆。
4. **依赖锁**：Sheet2「依赖前置」与 fixture 参数一一对应；P4→PO、S1→P1 在列；
   每条用例的 `usefixtures` 集合 == 全局 + 自己组 + 自己例。
5. **反向锁（共用代码）**：拿 `园区基线/智慧停车/test_plan.xlsx` 重新生成
   SmartParking 的 `.py`，与改动前逐字比对（80 用例 / 67 class / 67968 字节）——
   同样是开发期手工比对，仓库里落的是
   `tests/test_py_export_fixture.py::TestLegacyModeUnchanged`
   （非逐前置模式：类级 fixture 形状、无显示名助手、无 `pre_rt` 痕迹；
   外加「逐前置却拿不到 Sheet2 必须报错」的守门项）。
6. **端到端（用户执行）**：拆前一轮基线 → 拆后一轮，逐用例比对请求序列、通过集合。

## 五、已知代价（必须讲清）

* **时序变了**：车辆记录/临停车牌从「开跑前一次性全建」变成「跑到它才建」；
  车场组从「全部提前建」变成「首个用到它的用例触发」。**块内顺序、组内相对顺序、
  每块只跑一次**都不变，但请求在时间轴上的分布变了 —— 这正是第 6 条要真跑一轮的原因。
* **全局那块仍会出现在每条用例下**：它确实被每条用例需要（清场 + 收费规则），
  拆不掉。用户看到的从「122 步一坨」变成「5 步全局 + 4 步本组 + 1~2 步本例」。

## 六、留档（仓库外，一次性探针目录）

`…\8584cbb5-…\golden\`：`setup_before.yaml` / `teardown_before.yaml`（改动前产物）、
`prt_before.py`（改动前 37 用例 .py）、`smartparking_before.py`（改动前 80 用例 .py）。

## 七、未做 / 登记

以下几条**方案外发现**，按「方案外发现登记待用户决策、不顺手改」处理，未动手。

* **E11 —— `agent_components/run_results.py` 的「class → setup 文件」映射在拆分后失效**
  （本轮只读代码确认，未改）：
  `_fixture_of_class` 只认**类级** `@pytest.mark.usefixtures("setup_…")` 装饰器
  （`run_results.py:189`），逐前置模式下前置挂在**方法**上、类上没有 —— 于是
  `has_setup=False`、`class_setup_abs()` 返回 `None`（它退化去探测
  `setup_data/setup_parking_rule_table.yaml`，该文件已不再生成）。
  影响两处：① `ingest_run_results` 写 setup sidecar 时，前置失败不再归到 setup 文件；
  ② `select_rerun_targets` 的定向重跑不会再带上前置（前置仍由顶层 setup 正常执行，
  只是「失败归因 / 重跑范围」这两件事失去粒度）。
  两条路：改 `run_results` 收集**方法级** usefixtures（并与 fixture 依赖闭包对齐），
  或本轮不动。**等用户决策。**
* **E12 —— `tests/tools/_redeploy_parking_rule_table.py` 三处**（本轮实测，未改，登记见 MANIFEST）：
  ① **部署时不清残留** —— 拆分后旧的 `setup_data/setup_parking_rule_table.yaml`
  仍留在部署目录里（`--check` 实测「相同 88 | 不同 0 | **仅部署侧有 1**」，非致命；
  该文件已无任何引用）；
  ② **部署后的自检是同一性比对** —— 正常模式走
  `compare(module_dir, out["feature_dir"])`，而 `feature_dir = dirname(module_dir)/ParkingRuleTable`
  **就是** `module_dir` 本身，于是「相同 89 | 不同 0」是恒真的；真正有效的是 `--check`
  （它在临时目录里重生成后比对）。「刚部署完就打印一致」这一条**证明不了任何东西**；
  ③ dry-run 的打印把文件数写死成「39 个 YAML」（现在是 49 个前置/收尾 + 37 用例）。
  另两处陈旧产物：`tests/tools/_probe_monthly_car_recognized.py`（2026-09-11 的一次性探针，
  所属那轮实验早已结案）仍指向旧的单文件 `setup_parking_rule_table.yaml`，再跑会 FileNotFound；
  `_phase_c_selective_2026-09-09/_dryrun/ParkingRuleTable/test_ParkingRuleTable.py`
  （2026-09-11 的 dry-run 产物）会让**仓库根**的 `pytest` 整体 collection 中断
  （它 `import allure`，本仓 venv 没装）—— 全量要跑 `pytest tests/`。一并登记。
* **`fixture等级` 这一列在两个产品里原本都是死列**：SmartParking 的 `test_plan.xlsx`
  每行本来就带 1–5 个前置编号（`PRE-xxx`），但没人读那列的等级值。本轮只让
  「逐前置」这一个取值有了行为，其余取值（class/danyuan/空）走原分支 —— 共用代码
  的反向锁见 `tests/test_py_export_fixture.py::TestLegacyModeUnchanged`。
* 未因本轮改动调整任何时刻、断言、用例内容；`teardown_parking_rule_table.yaml` 未变。
