# ParkingRuleTable 两层实跑归属（2026-09-11）

命令：`python -m pytest -c pytest.ini "testcase/园区基线/智慧停车/ParkingRuleTable"`（框架 venv）
证据来源：`report/temp/*-result.json` 的 allure 原始**请求参数 / 接口响应信息**附件（非推测）。

| 轮次 | 结果 | 直接原因 |
|:---|:---|:---|
| Round 1 | **29/29 setup ERROR** | `/mock/*` 响应无 `{retCode,msg}` 外壳，却用配置面 `validation_ok` 断言 → 已修（新增 `validation_mock_read`） |
| Round 2 | **6 passed / 23 failed** | 见下表五簇 |

---

## 一、正面结论：推导金额被服务端证实

`mock/bill/latest` 直接返回账单，**与推导值逐条一致**：

| 用例 | 我推导 | 服务端 `payableAmount` | 结论 |
|:---|:---|:---|:---|
| 表2-05 主场无券 | 20 | **20.0** | ✅ |
| 表2-10 主+子合并 | 25 | **25.0** | ✅ |
| 表2-11 主+子主场无费 | 15 | **15.0** | ✅ |

→ 金额口径与 timeline 构造**正确**。唯一错的是断言字段：
`paid` 是**实缴标志**，车没缴费服务端给 `false`；判定表「是否缴费」列若意指"应缴/已产生费用"，
应断 `payableAmount`，不能断 `paid: true`。

---

## 二、五簇归属

### A1. 我的生成器 bug：按临停计费的查询打错车场
`scope` 未指定时取 `eids[0]`（主/园区码），但动作是「入子」时车在**子场**。

报文铁证（表1-02）：入子用 `DEV-S1-1_emti96`，查询却打 `PO` 的 parkCode `zhyq4687…`
→ `{"inPark":false,"parkingType":null}`。

| 用例 | 症状 |
|:---|:---|
| 表1-02 | 入子 S1 → 查询打 PO |
| 表1-13 | 入子 S1 → 查询打 PO（兼 B） |

### A2. `bill/unpaid` 调用时机与参数错
参照 `testcase/VehicleAccess/VehicleAccesstest_004`：车**在场时**查、`currentTime` **晚于入场时间**
（10:05 入 → 05:05 查 → `fee: 30`；随后出场被拦 → `pay/simulate` → 出场放行）。

我生成的形态相反：**出场后**查 + `currentTime` = **入场时刻**（10:00，取自 timeline 首项）
→ 服务端返 `{"retCode":0,"msg":"fail","data":"请求失败"}`（表1-24 返吐槽串）。

| 用例 | 症状 |
|:---|:---|
| 表1-24、表2-05、表2-06、表2-10、表2-11 | `bill/unpaid` 拿不到 `fee` |

### B. 包月车记录不生效 —— ✅ **已定性：接口逻辑 Bug，我方数据正确**

最小对照实验（`tests/tools/_probe_monthly_car_recognized.py`，复用框架 `RequestsBase.replace_load`，
原样跑完 setup 后在**同一现场**探测，跑完执行 teardown 清场）：

| 探针 | 原始响应 | 结论 |
|:---|:---|:---|
| [A] `monthlyCar/query {carNumber}` | `[{"code":"zhyq168657384", "carNumber":"津TPDAU8", "parkCode":"zhyq350559482468608", …}]` | 记录**存在** |
| 对比 | `parkCode` 与 `preP2ParkCode` **逐字相同** | 挂的车场**正确** |
| [F] 入场后 `carTypeQuery` | `{"inPark":true, "parkingType":"2"}` | 车在场，却判**临停** |
| 对照 | 同 parkCode 下**白名单**记录 `carTypeQuery` 正确返回 `4`（表1-09 实测） | 同通道白名单认、包月不认 |

→ **`mock/carTypeQuery` 不识别 `mock/monthlyCar/add` 写入的记录**。写入侧经回读证实无误，
按三层判障 §3「输入正确 ⇒ 接口逻辑 Bug」归属接口侧。

| 用例 | 报文症状 | 归属 |
|:---|:---|:---|
| 表1-12/13/14/15/18/19/21/22 | 入主被拒 `openGate:false` | 接口 Bug 连带 |
| 表1-16/17/20 | 放行但 `parkingType:"2"`（期望 `1`） | 同上 |

### C. 一人多车白名单 —— ✅ **已定性：我方数据正确，行为与判定表相反**

| 探针 | 原始响应 | 结论 |
|:---|:---|:---|
| [G] `carWhiteBlack/getPage`（P3） | `{"total":1, "list":[{"code":"zhyqB54665556", …}]}` | 记录**在**，`multi:true`+`subCarNumbers` 写入成功 |
| [I] 该车入场 P3 | `{"openGate": false}` | 有白名单记录**仍被拒** |
| 对照 | 同一套写法在 P2 放行（表1-09 car_a = `true`） | 差异只在车场开关 |

| 用例 | 车场开关 | 判定表要求 | 实测 |
|:---|:---|:---|:---|
| 表1-09 | P2 `carWhiteOtherIsCasual=false` | 后车转临停(2) | 后车 **4 白名单** |
| 表1-10 | P1 同上 | 后车拒入 | 后车 **放行** |
| 表1-08 | P3 `=true` | 两车都放行(4) | **两车全拒** |
| 表1-19 | P3 同上 | 两车都放行 | **两车全拒** |

→ 开关与结果的对应关系**与判定表相反**，且数据侧已排除。
**未排除项**：判定表列「是否支持一人多车白名单」→ `carInConfig.carWhiteOtherIsCasual`
这个**列到字段的映射本身未经验证** —— 也可能语义不同（字段名字面读更像"白名单的**其他车牌**是否按临停处理"）。
此项须与接口方核对后再定 Bug 归属。

### D. 次级现象（证据不足，待查）
| 用例 | 症状 | 待查点 |
|:---|:---|:---|
| 表1-11 | P4 他场白名单准入（指 PO）→ 入主被拒 | `otherParkWhiteAccessParks` 传 zhyq 码 vs **园区编号** |
| 表1-23 | 同一车牌同时挂 PS1M 白名单 + PS1S 包月 → 入主被拒 | 双记录是否互斥 |

### E. 表2-01 压根没有账单
不收费车场用例**无任何进出场动作** → `bill/latest` 返 `{}`（空响应）。
至少要有一次进+出才会产生账单记录。

### F. 缴费接口未接入（用户指出）
判定表动作列里**没有映射缴费动作**，故 `bill/latest.paid` 恒为 `false`。
api_defs 里两条可用且名称正好对应表2两种场景：

| 接口 | 名称 | 对应 |
|:---|:---|:---|
| `mock/payWhenLeave/simulate` | 出场缴费模拟 | 表2-05 主场·无券（出场缴费） |
| `mock/pay/simulate` | 提前出场缴费 | 表2-06 主场·无券·**提前缴费** |

参照 VehicleAccess 004：`bill/unpaid` 读费 → `pay/simulate {fee: ${回读值}}` → 出场放行。

---

## 三、逐例归属表

| 用例 | 结果 | 归属 |
|:---|:---|:---|
| 表1-01 / 03 / 04 / 05 / 06 / 07 | ✅ PASS | — |
| 表1-02 | FAIL | A1 |
| 表1-08 | FAIL | C |
| 表1-09 | FAIL | C |
| 表1-10 | FAIL | C |
| 表1-11 | FAIL | D（他场准入配置） |
| 表1-12/13/14/15/16/17/18/19/20/21/22 | FAIL | B（13 另兼 A1） |
| 表1-23 | FAIL | D（双记录） |
| 表1-24 | FAIL | A2 |
| 表2-01 | FAIL | E |
| 表2-05/06/10/11 | FAIL | A2 + `paid` 语义 |

---

## 四、结论与待决策

最小对照实验已执行（用户批准路线），B/C 两簇**数据侧嫌疑全部排除**：

| 簇 | 定性 | 依据 |
|:---|:---|:---|
| **B** | **接口逻辑 Bug** | 记录存在、parkCode 逐字正确；同 parkCode 下白名单能识别，包月不能 |
| **C** | **行为与判定表相反；映射待核** | 记录存在（`code` 已回读）、仍被拒；开关取值相反结果也相反。唯一未排除项：**列→字段映射未经验证** |
| **A1/A2/E/F** | **我方生成器待修** | A1 查询挂错场；A2 `bill/unpaid` 时机+`currentTime`；E 表2-01 缺进出场；F 未接缴费动作 |
| **D** | 待查 | 证据不足 |

### 待决策
1. **A1/A2/E/F 的修法**（我的生成器）—— 建议一并改，改完再跑一轮验证。
2. **C 的映射核对**：判定表列「是否支持一人多车白名单」是否真对应
   `carInConfig.carWhiteOtherIsCasual`？若不对应，属我方映射错，不是接口 Bug。
3. **「是否缴费」口径 + 是否引入缴费动作**：接 `payWhenLeave/simulate`（表2-05）与
   `pay/simulate`（表2-06 提前缴费）后 `paid` 才有意义。

---

## 五、第 3 轮（14:53 那次）「跑完看不到车辆记录/进出记录」的根因

**用户报告**：「这轮跑下来还是没有车辆记录和进出记录」。

### 5.1 先结论：记录**存在**，是回读机制把车场编码取错了

| 项 | 实测（`tests/tools/_check_parking_env_state.py`，只读） |
|:---|:---|
| 车场 | 本轮 **14 个**（`createTime=2026-09-11 14:53:46~52`），PO/P1–P6/S1/PS1M–S/PS2M–S/PS3M–S |
| **白名单（车辆记录）** | **10 条，`createTime=14:54:08~14:54:27`**，挂在 PK-PO/P1/P2/P3/P6/PS1M/PS2M 上 |
| 进出请求 | 报告附件统计：`access/enter` **71 次**、`access/exit` **63 次** —— 请求确实发出去了 |
| 收费规则 | 205 条（历史累积，`RULE-M_*`/`RULE-C_*` 与我方同前缀，无法按名区分） |

> 即：**数据没有被清、也确实建出来了**。查看入口见 5.4。

### 5.2 根因（我方生成器 Bug）：`getAllWithDevices` 的 `parkName` 过滤服务端不生效

所有 `preXXXParkCode` 都是靠"建场 → `getAllWithDevices{parkName}` 回读 → `$.data[0]`"
拿到的。实测该过滤**完全无效**：

| 请求体 | 返回条数 |
|:---|:---|
| `{"parkName":"PK-PS3M_hfx8hc"}` | **107** |
| `{"parkName":"不存在的名字ZZZ"}` | **107** |
| `{"parkCode":"zhyq457861985152284"}` | **107** |
| `{"mainPark":true}` | 77 ✅ **只有这个生效** |

⇒ `$.data[0]` 退化成"在全量列表里撞排序运气"。日志逐字铁证（`log/test.20260911145342.log`）：

```
line 457 请求参数: {'json': {'parkName':'PK-PS3M_hfx8hc'}}
line 458 接口实际返回信息 … data[0]=id2705 PK-PS2S_mvd05o（PS3M 是 id2706，排在第 2 位）
line 465 apiutil.py:303 JSON 提取 [prePS3MParkCode]: zhyq846724013278630   ← 这是 PS2S 的码
```

**接口文档与实现不符**（`api_defs.json` 的 `getAllWithDevices.data` 说明）：

> 「按 parkName 查询时**精确命中的车场位于 data[0]**」
> `parkCode`：「**新建后回读车场编码取此字段**」

→ 按三层判障 §2「契约验证」，这是**接口缺陷**（过滤未实现）。
同时**我方生成器必须加固** —— 不能把正确性押在一个未实现的行为上。

### 5.3 波及面：4 个子场拓扑全坏（这就是"看不到子场数据"的直接原因）

| 车场 | 应有设备 | 实际 | 根因 |
|:---|:---|:---|:---|
| PK-S1_mz6dca | DEV-S1-1/2 | **0 个** | `preS1ParkCode` 取成 **P6** → 设备建在 P6 下 |
| PK-PS1S_ana9ia | DEV-PS1S-1/2 | **0 个** | `prePS1SParkCode` 取成 **PS1M** |
| PK-PS3M_hfx8hc | DEV-PS3M-1/2 | **0 个** | `prePS3MParkCode` 取成 **PS2S** |
| PK-PS3S_p4lvo1 | — | 设备对，但 `parentCode` 挂到 **PS2S** | 同源 |

连带：表1-03 的白名单 `parkCode` 变成 `PS1M,PS1M`（应为 `PS1M,PS1S`），
即日志 `line 368` 回读 PS1S 时 `data[0]` 是 PS1M(id2702)。
→ **用 S1/PS1S/PS3M 的设备"入场"，实际进的是 P6/PS1M/PS2S** —— 这一片「子场」用例结果全部不可信（含 A1 簇）。

### 5.4 「看不到」的查看入口

数据挂在**测试车场**上（`PK-P1_h8xft6` 这类随机名），不是默认园区。要在
车场列表里选 `PK-PO_*/PK-P1_*…` 这些再看白名单/记录。
另：`api_defs` 39 个接口里**没有任何「进出记录列表」查询面**，mock 层只有
`mock/carTypeQuery`（问单车在不在场）—— 若你说的「进出记录」是业务页面，那一层未必由 mock 写入。

### 5.5 修复方案（约束已实测确认，待用户选）

| 约束 | 实测结论 |
|:---|:---|
| 客户端指定 `parkCode` | ❌ `saveOrUpdate` 带 parkCode 报「请求失败」，码由服务端生成 |
| 车场名 | ✅ 服务端**强制唯一**（重名报「此车场名称已存在」） |
| `extract` 里代变量 | ❌ `apiutil.py:156` 先 `pop('extract')` 再 `replace_load`，jsonpath 不能带 `${}` |
| jsonpath 过滤器 | 基础 `jsonpath_ng.parse` **不支持** `[?()]`；但 `jsonpath_ng.ext`（1.7.0，已装）**支持**，实测按名精确取码成功 |
| 顺带发现 | `parkingPark/deleteByCodes` 要**裸数组** `["code"]`；我方 teardown 用的是 `"a,b"` 字符串，需核对 |

**方案甲（推荐，精确且与排序无关）**：车场名确定化（`PK-P1` 固定名，靠"重名即报错"天然隔离）
＋ 框架 `extract_data`/`extract_input_data`/`extract_list_data` 改用 `jsonpath_ng.ext.parse`
（`ext` 是 `parse` 的超集，向后兼容，且当前这类表达式是**抛异常被吞**、静默不写 key）
→ `extract: {preP1ParkCode: "$.data[?(@.parkingPark.parkName=='PK-P1')].parkingPark.parkCode"}`

**方案乙（不动框架，但不稳）**：车场名确定化 ＋ `mainPark` 过滤 ＋ `$.data[0]`。
依赖返回排序，**同秒创建会翻车**（本轮已实测翻车），不推荐。

> 方案甲要动 `base/apiutil.py`（SmartParking 82 条共用）→ 按原则③**先报备再改**。

### 5.6 ✅ 已修复并验证（2026-09-11，用户批准「做吧」）

车场名**未固定化**（用户指正：每轮取不同名天然避免覆盖），只补"按名筛选"两个能力洞。
改动与证据：`changelog/2026-09-11_parkcode_readback_by_name.md`、MANIFEST **H16**。

| 项 | 结果 |
|:---|:---|
| 生成器回读表达式 | `$.data[0]…` → `$.data[?(@.parkingPark.parkName=='${get_extract_data('preXParkName')}')]…`（14 处） |
| **车场拓扑** | ✅ **14/14 全对**（第 3 轮的 4 个坏子场全部恢复） |
| 连带 | ✅ 表1-03 `parkCode` = `PS1M,PS1S`（原 `PS1M,PS1M`） |
| 车辆记录 | ✅ 10 条白名单逐条挂对车场 |
| 门槛 | 6 passed / 23 failed，与第 2 轮同分**无回归**（失败全属未批准的 A1/A2/B/C/E/F） |

> ⚠️ 本轮另发现**我方生成器 bug**（❗定性已更正，登记于 changelog §5.4 / MANIFEST H16）：
> **多场白名单记录的编码回读失效** —— `carWhiteBlack/getPage` 的 `parkCode` 是**等值匹配且工作正常**
> （单场记录传自己的单码 → 命中）；坏在多场记录的 `parkCode` 存的是**复合串**，
> 任何等值查询都命中不了（**含它自己那个串**），`''` ≡ 不传。
> ⇒ 受影响 = both 作用域白名单（表1-03）→ 编码回读拿占位串 → teardown 删不掉、记录残留。
> **未改**，修法候选 = 回读体不传 `parkCode`、只留 `searchKey`。
> （~~初判"带 parkCode 即返 0 = 接口缺陷"~~ 已撤回；对照 `mock/monthlyCar/query` 的 `parkCode` 正常，不要一起删。）

