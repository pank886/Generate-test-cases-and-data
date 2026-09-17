# 第一部分修复：A1 车场归属 + 多场白名单回读；A2/E/F 前提被推翻（2026-09-11）

> 决策载体。上游：`_phase_c_selective_2026-09-09/parking_rule_table_round2_attribution.md`、
> `changelog/2026-09-11_parkcode_readback_by_name.md`。MANIFEST 见 **H17**。
> 用户指令：**「先改第一部分」** + **「直接改yaml文件和代码，先不走生成」**。

---

## 一、这一轮做了什么

「第一部分」= 我先前列出的 5 项生成器侧问题（A1 / A2 / E / F / 多场白名单回读）。
**A1 / 多场白名单回读干净落地；A2 的顺序部分 + F 按用户决策落地；A2 的金额口径与 E 的前提被实跑推翻，
经用户决策后一并挂起等接口方 —— 见 §三。**

| # | 项 | 状态 |
|:---|:---|:---|
| A1 | 「按临停计费」查询打错车场 | ✅ 已改 + 已验 |
| task#26 | 多场白名单编码回读失效 | ✅ 已改 + 已验 |
| A2 | `bill/unpaid` 时机 + `currentTime` | 🟡 **顺序已改**（查询点前移到出场前）；**金额口径不动**，端点恒失败 → **挂起等接口方确认** |
| E | 表2-01 补一次入+出 | ⏸ **挂起等接口方确认**（判定表原文 `\`，且 P2 实测仍收费） |
| F | 接入缴费接口 | ✅ 已接入（`pay/simulate` / `payWhenLeave/simulate`） |

**用户决策（2026-09-11）**：① 金额断言先不改、挂起等接口方；② 表2-01 挂起；③ `paid` 严格按判定表，
流程改为「先查询缴费金额，再看能不能出场」；④ 缴费动作**接入**。

---

## 二、已落地的两项

### 2.1 A1：类型查询必须打**本次入场所在的那片场**

`rule_tables/yaml_kernel.py`

```python
# 之前：eid = eids[0] if p["scope"] != "sub" else eids[1]
#       scope 为 None（=「本次入场所在的那片场」）时兜底到主/园区码
# 之后：
scope = p["scope"] or ("sub" if entry_token == "入子" else "main")
eid = eids[0] if scope != "sub" else eids[1]
```

`build_case` 把入场 token 一起传下去：`_parking_type_call(case, p, token)`。

**为什么是 Bug 不是接口问题**：报文铁证（第 2 轮）——表1-02 用子场设备 `DEV-S1-1_emti96`
入场，查询却打 `PO` 的 `parkCode` → 服务端返 `{"inPark":false,"parkingType":null}`。
按三层判障 §3「输入错误 ⇒ 我方修」。

**验证**：表1-02 / 表1-13 的 `carTypeQuery.parkCode`
`prePOParkCode` → **`preS1ParkCode`**（子场）。

### 2.2 多场白名单编码回读（task#26）

**根因**：`carWhiteBlack/getPage` 的 `parkCode` 是**等值匹配**，而多场记录的 `parkCode`
存的是逗号复合串（`PS1M码,PS1S码`）⇒ 传复合串、传单码、**甚至传它自己那个串**都命中不了；
`''` 与不传逐字等价。首轮实证：

```text
带复合串 parkCode          total=0
带单码 PS1M               total=0
不传 parkCode             total=1  ← code=zhyqB20659590（正是该记录自己的 code）
不传 parkCode（空串）       total=1
```

**改法**（数据层 + 内核分流，两个文件）：

* `rule_tables/parking_rule_map.yaml` — `vehicle_kinds.白名单` 新增 `readback_multi`：
  ```yaml
  readback_multi:
    omit_body: [parkCode]
    extract: {<C>WhiteCode: "$.data.list[?(@.carNumber=='${get_extract_data('<C>CarNumber')}')].code"}
  ```
* `rule_tables/yaml_kernel.py` — `_car_readback_step` 按 `len(park_eids) > 1` 分流；
  `extract` 里的 `<C>CarNumber` 展开成**裸键名**（与请求体里展开成 `${...}` 不同源，故单独替换）。

**为什么不只删 `parkCode`**：`searchKey` 是**模糊**匹配（`carNumber` 字段被服务端完全忽略），
只靠它可能捞到别的记录 → 删掉错误的等值筛选的同时，用 `[?()]` filter 把精确性补回来。
（框架侧 `jsonpath_ng.ext` 支持 `[?()]`，`extract` 的 `${}` 注入，两处加固见上一份 changelog。）

**边界（原则②最小改动）**：**单场路径逐字未动**（表1-04 仍 `parkCode` + `list[0].code`）；
**`包月车` 不加 `readback_multi`** —— `mock/monthlyCar/query` 的 `parkCode` 实测正常
（对码 1 条 / 错码 0 条），多场也照样命中（表1-14 上一轮已成功）。

### 2.3 A2（顺序部分）+ F：先查金额 → 缴费 → 出场

**决策 ③④ 的落地形态**（`rule_tables/yaml_kernel.py` + `rule_tables/parking_rule_map.yaml`）：

```text
入主/入子 …  →  [获取待支付费用]  ← 插在**第一次出场之前**，currentTime = 该次出场时间
              →  [缴费模拟]        ← 按「是否提前缴费」/「是否缴费」二选一
              →  出主/出子
              →  [是否缴费校验]     ← 判定表原文，payer 不变
```

| 项 | 落点 |
|:---|:---|
| 插点 | `build_case` 动作循环里 `token.startswith("出") and not fee_inserted` → `_fee_steps(case, token, times, eid)` |
| `currentTime` | = **本次出场的时间**（早于入场算不出时长） |
| 金额断言 | **口径不变**：`mock/bill/unpaid` + `eq: {fee: <判定表期望>}`（决策①：不改，挂起） |
| 缴费回读 | `extract: {unpaidFee: "$.fee"}` → 缴费动作的 `fee` 取 `${get_extract_data('unpaidFee')}`（逐字对齐 VehicleAccesstest_002/carBPrepayExit_002.yaml） |
| 缴费选口 | 条件列「是否提前缴费」=是 → `mock/pay/simulate`；否则动作列「是否缴费」=是 → `mock/payWhenLeave/simulate`；都不是 → 不缴费 |
| 端点/名称/字段 | 落在**数据层** `parking_rule_map.yaml::pay_actions`（占位符 `<C>CarNumber`/`<D>DeviceCode`/`<F>UnpaidFee`/`<T>Time` 由内核展开） |
| 适配器 | `rule_rows.py` 新增 `CaseSpec.prepay`（读表2 条件列「是否提前缴费」，走既有 `t2_header` 取名模式） |

**逐例形态**（表1-24 / 表2-05 / 表2-06 / 表2-10 / 表2-11 全部落盘复核）：

| 用例 | 序列 |
|:---|:---|
| 表1-24 | 入主 → 类型校(P5) → **查金额(14:00, fee=0)** → 出主 |
| 表2-05 | 入主 → **查金额(14:00, fee=20)** → **出场缴费** → 出主 → 是否缴费=true |
| 表2-06 | 入主 → **查金额(14:00, fee=20)** → **提前缴费** → 出主 → 是否缴费=**false**（判定表原文，冲突见 §3.5） |
| 表2-10 | 入主 → 入子 → **查金额(15:00, fee=25)** → **出场缴费** → 出子 → 出主 → true |
| 表2-11 | 入主 → 入子 → **查金额(13:00, fee=15)** → **出场缴费** → 出子 → 出主 → true |

⚠️ 两点如实登记：
1. 表2-10/2-11 的缴费点在**第一次出场（出子）之前**，用的是**子场出场设备** —— 这是「先查金额再看能不能出场」的字面落点；
   若约定应为「离开整个园区时（出主）缴费」，改 `build_case` 的插点即可（金额两者相同，因为 `出子`/`出主` 偏移一致）。
2. 缴费动作的 `fee` 依赖 `bill/unpaid` 的回读；该端点当前恒失败 ⇒ 缴费会拿到占位串 ⇒
   表2 的这几条**预期仍红**，属决策①挂起的同一根因，不做局部兜底（避免掩盖接口问题）。

---

## 三、A2 金额口径 / E 的前提被实证推翻（决策后：挂起等接口方）

探针（一次性 aux，自建自清）：
`tests/tools/_probe_pay_semantics.py`、`tests/tools/_probe_bill_latest_points.py`。

### 3.1 A2 的前提不成立 —— `bill/unpaid` 在这个环境是**不可用**，不是时机错

| 调用时点 | 返回 |
|:---|:---|
| 入场时刻 `2029-12-31 10:00:00` | `{"retCode":0,"msg":"fail","data":"请求失败"}` |
| 出场时刻（车仍在场） | 同上 |
| 出场时刻 + `parkCode` | 同上 |
| 出场尝试（被拦）之后 | 同上 |

⇒ 「移到出场前 + 改 `currentTime`」**修不好任何东西**。金额断言必须改走
**`bill/latest.payableAmount`** —— 第 2 轮已证实它返的值（20 / 25 / 15）与推导逐条一致。

### 3.2 `bill/latest` 的可靠读取点 = **出场之后**

| 时点 | 返回 |
|:---|:---|
| **出场前** | `""`（空串）—— 连查两次、间隔 1s **都是空** |
| **出场后** | 完整账单 `{paid, payableAmount, paidAmount, entryTime, exitTime, parkingType, code}` |

### 3.3 缴费接口可用，但顺序/口径需定

| 序列 | 结果 |
|:---|:---|
| 入 → 出 | `openGate:false`；`latest` → `paid:false, payableAmount:20.0` |
| 入 → 出(被拦) → `latest` → `payWhenLeave(fee=20)` → 出 | `openGate:true`；`latest` → `paid:true, payableAmount:0.0` |
| 入 → `pay/simulate(fee=20)` → 出 | `openGate:true`；`latest` → `paid:true, payableAmount:0.0` |

⚠️ 两条注意：① **缴费后 `payableAmount` 归零** ⇒ 「金额」与「paid 」两个断言必须落在**不同时点**；
② `pay/simulate` 会把账单的 `entryTime` 改写成出场时刻（mock 怪癖，勿据此断言时长）。

### 3.4 表2-01（E）与「不收费车场」的硬冲突

* 判定表该行「条件（出入场动作）」**逐字就是 `\`**（xlsx 第 34 行）——原方案说要补 `入主-出主`，
  这是**我方发明**，不是表上写的，必须用户点头。
* 更麻烦：**实测 P2（映射里 `needPay`=0）入→出后仍算 `payableAmount: 20.0`**，
  与 P6（`needPay`=1）一模一样 ⇒ 「不收费车场」在 mock 侧**没有生效**。
  表2-01 的期望（`不产生费用`）按现状**必然失败**，且这更像接口/环境侧问题而非我方数据问题。

### 3.5 表2-06 的口径冲突

判定表：条件「是否提前缴费」= **是**，动作「是否缴费」= **否**。
实测：提前缴费执行后 `latest.paid` = **true**（且出场放行）。
⇒ 若把「动作（是否缴费）」直接当 `paid` 断言，表2-06 会断 `false` 而实际是 `true`。
候选口径：`paid = (是否提前缴费=是) or (动作（是否缴费）=是)` —— 表2-01/05/06/10/11 全部自洽。

---

## 四、测试（原则④）

| 套件 | venv | 项数 | 结果 |
|:---|:---|:---|:---|
| `tests/test_rule_table_export.py` | 仓库 venv | **31**（原 17） | ✅ passed |
| `tests/test_framework_extract_by_filter.py` | **框架 venv** | 8 | ✅ passed |
| 全仓 `tests/`（除 tools / 框架侧文件） | 仓库 venv | 1004 | 995 passed / 9 failed（**全部既有**，见 §五） |

新增锁定（3 类，共 14 项）：
* `TestParkingTypeQueryTargetsEntryPark`（2 项）——通用不变量「类型查询的 parkCode 必须等于
  其前最近一次**入场设备**所在车场」＋ 反证「表1-02/1-13 不得再出现主/园区码」。
* `TestMultiParkWhitelistReadback`（5 项）——多场回读体无 `parkCode`、extract 带 `carNumber` filter、
  **单场路径逐字不变**、`包月车` 保留 `parkCode`。
* `TestFeeQueryAndPayActions`（7 项）——查金额步骤必须**早于**其对应出场设备、`currentTime` = 该出场时间、
  金额断言口径逐字不变（`bill/unpaid` + `eq: {fee}`）、缴费选口（提前缴费→`pay/simulate` /
  动作缴费→`payWhenLeave/simulate` / 都不缴费→无缴费步骤）、缴费 `fee` 取 `unpaidFee` 回读、
  端点与字段只来自数据层 `pay_actions`。

---

## 五、范围外发现（原则③登记，未处理）

全仓跑出 **9 个既有失败**，均与本次改动无关（`grep rule_tables tests/test_phase_bc_unit.py` 无命中）：

| 用例 | 症状 |
|:---|:---|
| `TestResolveApiDefs::*`（8 项） | 接口 URL 前缀漂移：期望 `/electricMeter/add`，实际 `/park-energy-electric-web/electricMeter/add` |
| `TestYamlSingleNode*`（2 项，1 项与上重叠） | 提示词规则缺失（`数据唯一化`），H10 已登记 |

---

## 六、不做的事（挂起 / 等接口方，不擅自动手）

| 项 | 现状 |
|:---|:---|
| 「`bill/unpaid` 恒返 `请求失败`」的定性与修法 | ⏸ 决策①：先不改口径，**挂起等接口方确认**（三层判障 §2 接口缺陷） |
| 表2-01（不收费车场） | ⏸ 决策②：**挂起等接口方确认**（判定表动作列为 `\`；且 P2 实测仍收费 20.0） |
| 表2-06 的 `paid` 断言 | 决策③：**严格按判定表** → 仍断 `false`；与实测 `true` 的冲突登记在案，不擅自改口径 |
| 表2-10/2-11 缴费点是否应挪到「出主」 | 已按字面落在「第一次出场前」；若约定不同，改插点即可（见 §2.3 ⚠️1） |
| C 的列→字段映射（`carWhiteOtherIsCasual`） | 需与接口方核对（第 2 轮已登记） |
| 8 条券用例（无核销接口） | 仍 deferred |

**产物已落盘**：`ParkingRuleTable/` 下 **31 个 YAML 已重生成**（`yaml_kernel.generate`，**纯确定性、不经 LLM**），
等效于「直接改 YAML」。逐项复核：表1-02 的 `parkCode` → `preS1ParkCode`；表1-03 回读体无 `parkCode`、
extract 带 `carNumber` filter；表1-04/1-14 逐字未变；表2 五条的「查金额 → 缴费 → 出场」顺序与上面 §2.3 表一致。
**`.py` 与 Excel/翻译缓存未重写**（未跑 `export()`），下一轮实跑前按需补齐。
