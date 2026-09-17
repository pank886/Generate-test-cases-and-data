# 全部时刻基准日 2029-12-31 → 2026-09-14（2026-09-15）

## 一、要求与现状

用户 2026-09-15 要求：**把所有时间改成 2026-09-15 之前的日期**。

改动前，产物的时间全部由一个基准日派生，且**都是字面量**（注册表里没有
`${get_offset_time(...)}` 之类的相对时间，全库只有一处注释提到该助手）：

| 位置 | 现值 | 条数 |
|:--|:--|--:|
| `time_format.base_date`（`parking_rule_map.yaml:737`） | `2029-12-31` | 1 |
| `timeline`（`:755-836`）37 例逐动作时刻 | `2029-12-31 HH:MM:SS`（6 种时分秒） | 106 |
| 白名单（一人多车）有效期 `enterStartDate/enterEndDate` | `2029-01-01` ~ `2030-12-31` | 2 |
| 包月车有效期 `startTime/endTime` | `2029-01-01` ~ `2030-12-31` | 2 |

## 二、改法与取值

| 项 | 改成 | 理由 |
|:--|:--|:--|
| `base_date` | `2026-09-14` | 用户选定。**与 2029-12-31 同为周一** —— 万一 mock 有工作日/周末费率差异，星期完全对齐 |
| `timeline` 106 个时刻 | 日期段换 `2026-09-14`，**HH:MM:SS 一字不动** | 内核 `yaml_kernel._times_for:751` 强制「日期 == base_date」；时刻按位置对应，判的是**差值** |
| 两个有效期窗口 | `2026-01-01` ~ `2026-12-31` | 原写法是「基准年 ±1」，照搬；窗口必须**含**基准日（`TestRecordValidity` 锁 `start <= base <= end`） |

**时长与金额不受影响**：4h / 主2h+子3h / 主0h+子3h 三种剖面平移后差值不变，
表2 的金额期望 20 / 25 / 15 与 `fee_expectation` 一字未动。

**没动**：判定表 xlsx、断言、动作数、用例内容、前置拆分、teardown，
以及文档/changelog 里的**写作日期**（如 2026-09-11 的探针记录 —— 那是历史，
不是产物的时间）。

## 三、测试设计

1. **新增直接锁**（`tests/test_rule_table_yaml.py::TestTimeline::
   test_every_request_time_predates_the_users_cutoff`）——
   产物里每个 `${...}` 时刻（形如 `time: 'YYYY-MM-DD HH:MM:SS'`）都必须**早于
   2026-09-15**，且全部落在 `base_date` 当天。
   为什么必须新加：既有三条锁（`TestRecordValidity` 窗口含基准日、
   `test_rule_table_export.py` 时刻日期 == base_date、`TestActionTimes` 时刻数 ==
   动作数）锁的都是「**跟随** base_date」—— 把 base_date 换成将来日期它们照样绿。
   只有这条直接锁住用户要的那个性质。
2. **更新写死字面量的两条锁**：`TestTimeline::test_duration_literals_differ_by_design`、
   `::test_main_sub_case_uses_two_distinct_durations`（它们刻意逐字比对时刻，
   日期段更新为新基准日，时分秒不变 —— 这本身就是「差值不变」的证据）。
3. **不改**：`tests/tools/_probe_*.py` 里的一次性探针（其中
   `_probe_vehicle_record_validity.py` / `_probe_monthly_car_recognized.py`
   等本就已登记为陈旧探针，见 E12）；`tests/test_phase_bc_unit.py:944` 的
   `endTime` 是括号转义用例的无关取值。

## 四、实证

### 4.1 测试

受影响子集（`test_rule_table_yaml` / `_export` / `_rows` / `_fixture_split` /
`test_py_export_fixture` / `test_run_results`）：**155 passed**（比改动前 154 多 1 条，
即 §三 新增的那条锁）。

全套 `pytest tests/`：**1045 passed / 28 failed / 20 skipped / 1 xfailed in 248s**
（改动前 1044 passed / 28 failed —— passed 多 1 即新增那条，failed 集合与改动前**逐条相同**，
全部落在 `test_workflow_api`（15，无存活的本地服务，ConnectError 10061）、
`test_phase_bc_unit`（9，prompt 措辞 + api-def 解析需 SQL）、`test_phase_c_api`（2，同上）、
`test_framework_extract_by_filter`（2，已知 E8），**无一在被改动的文件里**。
跑 `tests/` 而非仓库根，是因为根目录 collection 会被 E12 ⑤ 那份 dry-run 产物打断。

### 4.2 产物差分：**只有日期段**

先把改动前的部署目录整份快照（`%TEMP%\prt_before_dateshift`），改完重新导出部署后
逐文件原始比对（**不做**任何日期归一）：

| 项 | 数 |
|:--|--:|
| 旧 / 新 文件数 | 89 / 89（无增无删） |
| 逐字相同 | 24 |
| **只差日期** | **65** |
| **日期之外还有差异** | **0** |

样例（`test_t1_01_white_list_main_in_out/test_data.yaml`）—— 只有日期段变，时分秒不动：

```
-      time: '2029-12-31 10:00:00'          +      time: '2026-09-14 10:00:00'
-      startTime: '2029-12-31 10:00:00'     +      startTime: '2026-09-14 10:00:00'
-      time: '2029-12-31 14:00:00'          +      time: '2026-09-14 14:00:00'
```

### 4.3 部署后状态

- 部署目录 `setup_data/` 共 **50** 个 YAML = 48 个拆分前置 + 1 个 teardown +
  **1 个旧的单文件 `setup_parking_rule_table.yaml` 残留** —— 部署工具不清残留
  （E12 ①，本轮仍未处理，已无任何引用）。
- 本次部署末尾打印的「相同 89 | 不同 0 | 仅部署侧有 0 | 仅重生成侧有 0」
  **不作为证据**：那是 E12 ② 记的同一性比对（`feature_dir` 就是 `module_dir`），
  恒真。上面 4.2 的原始差分才是有判别力的那份。

## 五、方案外发现（登记，未动）

**E14 —— `tests/tools/_redeploy_parking_rule_table.py --check` 对「日期整体平移」是盲的。**
该工具的 `_normalize()`（`:39-49`）把 `\d{4}-\d{2}-\d{2}` 一律抹成 `<DATE>`
（本意是抹掉 `keep_data` 注释里的生成日期戳），于是日期改动在比对里被当成噪声。
实测：拿同一份 `test_data.yaml` 的改前/改后文本喂 `_normalize`，
原始文本不同、归一后**相同**（被抹掉的是 `2029-12-31` 与 `2026-09-14`）。
影响：本次这种「只动日期」的改动，`--check` 会报「一致」——
它是「忘了重新导出」的守卫，不是「产物是否变了」的守卫。
本轮没动它（改 `_normalize` 会牵动既有比对口径），登记待决。

（`fixture_level` 死列、E11/E12/E13 等既有登记见
`changelog/2026-09-15_per_precondition_fixtures.md` §七 与
`_phase_c_selective_2026-09-09/MANIFEST.md` H24，本轮未动。）
