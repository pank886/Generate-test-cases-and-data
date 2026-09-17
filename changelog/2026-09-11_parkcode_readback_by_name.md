# 车场编码回读按名筛选（框架加固 + 生成器改写）2026-09-11

> 决策载体。上一轮结论见 `_phase_c_selective_2026-09-09/parking_rule_table_round2_attribution.md` §五。
> 用户批准（「做吧」）= ①方案甲（框架两处 + 生成器回读表达式）②conftest 的 extract.yaml 快照。

---

## 一、要解决的问题（一句话）

**记录建出来了，但后续数据挂错了车场** —— 所有 `preXXXParkCode` 靠
`getAllWithDevices{parkName}` → `$.data[0]` 回读，而该 `parkName` 过滤**服务端不生效**
（传任意名都返回全量 107 条），`data[0]` 于是退化成"撞排序运气"，实测把 PS3M 的码取成了 PS2S。

两个能力缺口导致「按名精确取码」这条路一开始就走不通：

| 缺口 | 现象 | 位置 |
|:---|:---|:---|
| jsonpath 不支持过滤器 | `[?(@.f=='v')]` 抛 `JsonPathLexerError`，被 `try/except` **吞掉** → 静默「提取不到数据」 | `base/apiutil.py` 三处 `jsonpath_ng.parse` |
| `extract` 不解析变量 | `extract` 在 `replace_load` **之前**被 `pop` → 表达式里不能带 `${}` | `base/apiutil.py::specification_yaml` |

---

## 二、改动清单（最小改动，逐项）

### 2.1 框架 `base/apiutil.py`

| # | 改动 | 说明 |
|:---|:---|:---|
| 1 | 新增模块级 `jsonpath_parse(expr)` | 优先 `jsonpath_ng.ext.parse`（基础语法**超集**，多支持 `[?()]` 过滤器），`ext` 不可用时退回 `jsonpath_ng.parse` —— 行为与改动前逐字一致 |
| 2 | 三个调用点改用它 | `extract_input_data` / `extract_data` / `extract_list_data` |
| 3 | `extract` / `extract_list` 补 `replace_load` | 与请求参数、`validation` 同等对待，使 `${}` 能注进 jsonpath |

**为什么是"超集退回"而不是直接删掉 `parse`**：`ext` 是 `parse` 的严格超集，理论上不会
拒掉 `parse` 认的表达式；但保留回退分支可保证在任何一个只装了基础包的机器上行为不变。

**兼容性实测（不改行为）**：全框架 `testcase/**/*.yaml` 里 **723 条** jsonpath 表达式，
`jsonpath_ng.ext.parse` **723/723 通过、0 失败**。

**`replace_load` 这项的波及面 = 零**：静态扫描全框架，`extract`/`extract_list` 里含 `${}` 的
表达式**只有本轮新生成的这 14 条**（其余存量一条都没有）⇒ 这条改动对任何既有套件行为无影响。

### 2.2 生成器 `rule_tables/yaml_kernel.py`

```python
# 之前：extract={k_park_code(eid): "$.data[0].parkingPark.parkCode"}
# 之后：
extract={k_park_code(eid):
         "$.data[?(@.parkingPark.parkName=='%s')].parkingPark.parkCode"
         % _g(k_park_name(eid))}
```

- 车场名继续走 `random_code('PK-<EID>', 6)` —— **没有固定化**（用户指正：每次取不同名天然避免覆盖）。
- 过滤依据 = 上一步 `input_extract` 从**请求体**回读的 `pre<EID>ParkName`，即本步实际提交的名字。
- 服务端**强制车场名唯一**（重名报「此车场名称已存在」）⇒ 过滤结果无歧义。

### 2.3 框架 `conftest.py`

`clear_extract_data` 在清空前把 `extract.yaml` 复制为 `extract.last_run.yaml`（空文件不留快照）。
跑完仍能把页面上看到的数据对回「哪条用例建的 车场名 ↔ 车场编码 ↔ 设备编码 ↔ 车牌」。
路径取自 `conf.setting.FILE_PATH['extract']`，不受 cwd 影响。

---

## 三、测试（原则④）

| 套件 | venv | 项数 | 结果 |
|:---|:---|:---|:---|
| `tests/test_framework_extract_by_filter.py`（新增） | **框架 venv** | 8 | ✅ passed |
| `tests/test_rule_table_export.py`（新增 `TestParkCodeReadbackByName` 3 项） | 仓库 venv | 17（原 14） | ✅ passed |
| 全框架存量表达式静态兼容扫描 | 框架 venv | 723 条 | ✅ 723/723 |

两个文件构成**同一条缝的两半**，各自在能跑的 venv 里：
生成侧锁「表达式形状 + YAML 往返不变形」，运行时侧锁「`replace_load` 注变量 + `ext` 解析取到**正确那一条**的码」。
其中一条是**反证**：旧 `jsonpath_ng.parse` 对同一表达式必抛异常 —— 这就是"静默取不到"的来源。

**跑法**（仓库 venv 缺 `jsonpath_ng`/`allure`，框架侧必须在框架 venv）：

```bash
C:/Users/damai/PyCharmMiscProject/.venv/Scripts/python.exe -m pytest tests/test_framework_extract_by_filter.py -v
.venv/Scripts/python.exe -m pytest tests/test_rule_table_export.py -v
```

---

## 四、顺带核实（原登记为「待核对」）

`_phase_c_selective_2026-09-09/parking_rule_table_round2_attribution.md` §5.5 末行登记
「我方 teardown 用的是 `"a,b"` 字符串，需核对」。核实结论：**生成器已经是对的**，
三个删除形状各按其道，无需改动。

| 端点 | 生成的形状 | 实测要求 |
|:---|:---|:---|
| `parkingPark/deleteByCodes` | 裸数组 `[${code}]` | ✅ 裸数组 |
| `carWhiteBlack/delete` | 裸数组 `[${code}]` | ✅ 裸数组 |
| `parkingDevice/deleteByCodes` | `{"codes": "a,b"}` | ✅ 逗号串 |

---

## 五、验证（重生成 + 框架实跑第 4 轮）

产物重生成：`ParkingRuleTable/` 33 个文件（1 `.py` + 1 `__init__.py` + 31 YAML）；
setup 里 `parkName==` 回读 **14 处**、`data[0]` **0 处**。

命令：`pytest -c pytest.ini "testcase/园区基线/智慧停车/ParkingRuleTable"`
证据：`_phase_c_selective_2026-09-09/prt_round4_{junit.xml,pytest.log}`。

### 5.1 结果：6 passed / 23 failed（与第 2 轮同分，**无回归**）

比分没动是**预期内**的 —— 那 23 条失败从来不是"挂错车场"造成的，而是 A1/A2/B/C/E/F
（本就未批准修）。真正要看的是**根因是否消失**：

### 5.2 决定性证据：车场拓扑 14/14 全对（第 3 轮 4 个子场全坏）

只读复核（`getAllWithDevices` 按名取记录，逐场比对"自己的码 + 自己的 2 台设备"）：

| 车场 | 码归属 | 设备 |
|:---|:---|:---|
| PO / P1–P6 | ✅ 14/14 自己的码 | ✅ 各自 1 进 1 出 |
| S1 / PS1M / PS1S / PS2M / PS2S / PS3M / PS3S | ✅（**第 3 轮：S1→P6、PS1S→PS1M、PS3M→PS2S、PS3S 挂错父场**） | ✅ `DEV-S1-*` 等落在自己场下（第 3 轮为 0 个） |

**连带修复**：表1-03（主+子双场白名单）`parkCode` = `zhyq763831964452589,zhyq968360205756087`
= **PS1M,PS1S**（第 3 轮为 `PS1M,PS1M`）。

### 5.3 车辆记录（用户原始诉求「看不到车辆记录」）

`carWhiteBlack/getPage` 实查：本轮 **10 条**白名单记录，`parkName`/`parkCode` **逐条对应**，
例如 `青Y09730 → PK-PS1M_go0g6j,PK-PS1S_n046pc → zhyq763831964452589,zhyq968360205756087`。
→ **记录在、且挂在正确的车场下**。

`extract.last_run.yaml` 快照已落盘（本轮 conftest 改动生效），
14 个车场名 ↔ 编码 ↔ 设备码 ↔ 车牌可对照排查。

### 5.4 本轮新发现：多场白名单记录的编码回读失效（❗定性已更正）

> ⚠️ **更正（用户追问"parkCode 不能带空串么"后实测）**：初判为「接口缺陷（带参即返 0）」**是错的**。
> 该过滤**工作正常**，是**我方生成器按 `add` 的逗号串约定去反查 `getPage`**，属三层判障 §3「输入错误 ⇒ 修复」。

**`carWhiteBlack/getPage` 的 `parkCode` 是等值匹配（exact）**：

| 记录 | 传自己的单码 | 传 `自己,别的` | 传别的单码 | 传 `''` | 不传 |
|:---|:---|:---|:---|:---|:---|
| 京CWUC36（单场，`parkCode=P1`） | ✅ 1 | ❌ 0 | ❌ 0 | ✅ 1 | ✅ 1 |
| 青Y09730（**多场**，`parkCode=PS1M,PS1S`） | ❌ 0（PS1M / PS1S 任一都不行） | ❌ 0（**连记录原样的逗号串也不行**） | — | ✅ 1 | ✅ 1 |

⇒ 结论两条：
1. **单场用例的回读本来就是对的**（传自己的单码 → 命中），不在病灶内。
2. **多场记录的 `parkCode` 存的是复合串，任何等值查询都命中不了**（含它自己的串）——
   这类记录**只能靠"不传 `parkCode` + `searchKey`"捞**。
   受影响的用例 = both（主+子）作用域的白名单，如**表1-03**。

**空串语义**：`parkCode: ''` 与**完全不传**逐字等价（两者 total 一致），即"不筛"。

**连带影响**：`_car_readback_step` 对多场用 `_refs()` 拼逗号串 → `preCarT1_XXAWhiteCode` 拿
「未提取到数据」占位串 → **teardown 按错码删不掉**，记录残留。

**最小修法候选（待批准）**：白名单回读体**不传 `parkCode`**，只留 `searchKey: <完整车牌>`。
`searchKey` 是 carNumber 的**模糊**匹配（前缀/子串都命中），是本接口唯一可用的筛选
（`carNumber` 字段本身被完全忽略：传啥都返回全量）。
若要连"模糊命中别的记录"也挡住，`extract` 可改用 `$.data.list[?(@.carNumber=='<车牌>')].code`
（本轮框架刚支持过滤器）；车牌由 `random_plates` 生成，本轮 10 条互不为子串。

**对照：`mock/monthlyCar/query` 的 `parkCode` 是正常的**（传对的码 → 1 条，传错的码 → 0 条）
⇒ **那里的 `parkCode` 保留，别一起删**。

---

## 六、不做的事（原则③登记，等决策）

| 项 | 簇 | 现状 |
|:---|:---|:---|
| 入场/出场按临停计费的查询打错车场（子场 scope 取 `eids[0]`） | A1 | 未改，待决策 |
| `bill/unpaid` 移到出场**前**且 `currentTime` = 查询时间 | A2 | 未改，待决策 |
| 表2-01 需一次入场+出场行程（否则 `bill/latest` 返 `{}`） | E | 未改，待决策 |
| 接入 `payWhenLeave/simulate`（表2-05）与 `pay/simulate`（表2-06） | F | 未改，待决策 |
| 判定表列「是否支持一人多车白名单」→ `carWhiteOtherIsCasual` 的**映射本身**未验证 | C | 需与接口方核对 |
| 判定表「是否缴费」断 `paid`（实缴）还是 `payableAmount`（应缴） | — | 需口径决策 |
| 白名单（多场）编码回读：不传 `parkCode`、只留 `searchKey`（见 §5.4） | — | 未改，待决策 |

> A1/A2/E/F 已定性为我方生成器待修，**本轮未动** —— 本轮只为验证"回读按名取码"这一处修复。
