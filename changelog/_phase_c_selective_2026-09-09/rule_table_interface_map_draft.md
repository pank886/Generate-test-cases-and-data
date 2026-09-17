# 判定表用例 → 接口映射（草案 v1）

承接 `rule_table_cases_draft.md`（37 条用例，已过目）。
目标：把每条用例的 **前置条件 / 操作步骤 / 预期结果** 落到具体接口与字段。

---

## 一、条件侧映射（前置条件 → setup 接口）

| 判定表列 | 接口 | 字段 | 取值映射 |
|:---|:---|:---|:---|
| 条件（子场\主场情况） | `parkingPark/saveOrUpdate` | `parkingPark.mainPark` / `parkingPark.parentCode` | 主场=`true`；子场=`false` + `parentCode`=主场编码 |
| 条件（车场临停准入开关） | `parkingPark/saveOrUpdate` | `carInConfig.tempCarIn` | 是=`1` / 否=`0` |
| 条件（车场是否支持一人多车白名单） | `parkingPark/saveOrUpdate` | `carInConfig.carWhiteOtherIsCasual` | 是=`1` / 否=`0` |
| 条件（其他车场白名单准入） | `parkingPark/saveOrUpdate` | `carInConfig.otherParkWhiteAccess` + `otherParkWhiteAccessParks` | 是=`1`+车场code；否=`0` |
| 条件（其他车场包月车准入） | `parkingPark/saveOrUpdate` | `carInConfig.otherParkMonthAccess` + `otherParkMonthAccessParks` | 是=`1`+车场code；否=`0` |
| 条件（是否特殊车牌放行） | `parkingPark/saveOrUpdate` | `carInConfig.specialCarEnable` + `specialAccessConfigList[].carNumber/enable` | 是=`1`+车牌；否=`0` |
| 条件（是否收费） | `parkingPark/saveOrUpdate` | `parkingPark.needPay`=1 / `parkingPark.needCount`=1 | 是=`1` / 否=`0` |
| └ 收费规则（绑定） | `parkingPaymentConfig/add` → `parkingPaymentConfig/getParkingPaymentConfigPage` **回读** `$.data.list[0].code` | → `parkingPark.bindPayCode` | **必须回读**（V1–V5 防的就是此处） |
| 条件（车辆类型）·白名单 | `carWhiteBlack/add` | `isWhite`=`1`, `carNumber` | |
| 条件（车辆类型）·包月车 | `mock/monthlyCar/add` | `carNumber`, `parkCode`, `startTime`, `endTime` | |
| 条件（车辆类型）·特殊车牌 | `specialAccessConfigList`（随 `parkingPark/saveOrUpdate`） | `carNumber`, `enable`=`1` | |
| 条件（车辆类型）·其他车场白名单/包月 | 上游车场建白名单/包月车 **+** 本车场 `carInConfig.otherPark*Access`=`1` | | 需**两个车场** |
| 条件（优惠券-金额/时长） | `parkingCouponIssue/add` | `type`=金额/时长, `denominationMoney` / `denominationHour`+`denominationMinite`, `parkCode` | ⚠️ 见待决 #1 |
| **设备（操作步骤必需）** | `parkingDevice/add` → `parkingDevice/getPage` **回读** `code` | → `deviceCode` | `mock/access/*` 的 `deviceCode` 必填 |

---

## 二、操作侧映射（出入场动作 → 触发接口）

| 出入场动作 | 接口 | 关键入参 |
|:---|:---|:---|
| 入主场 / 入子场 | `mock/access/enter` | `carNumber`, `time`, `deviceCode`=对应场设备 |
| 出主场 / 出子场 | `mock/access/exit` | 同上 |
| 提前缴费 | `mock/pay/simulate` | `carNumber`, `fee`, `payTime`, `deviceCode` |
| 出场缴费 | `mock/payWhenLeave/simulate` | 同上 |
| **中途转临停** | **删除该车的类型记录** → `carWhiteBlack/delete`（白名单）/ `mock/monthlyCar/delete`（包月车），删后自动降级为临停车 | ⚠️ **需回读编码**，见 §5.1 |
| 清理（共享 teardown） | `mock/delAllForMock` + `parkingPark/deleteByCodes` | ⚠️ body 为空，见 §5.2 |

---

## 三、断言侧映射（动作列 → 查询接口）

| 动作列 | 接口 | 字段 | 判定 |
|:---|:---|:---|:---|
| 允许入场-主场 | `mock/access/enter` 响应 | `openGate` | 是=`true` / 否=`false`（⚠️ 见待决 #4） |
| 允许出场-主场 | `mock/access/exit` 响应 | `openGate` | 同上 |
| 允许入场-子场 | `mock/access/enter`（子场设备） | `openGate` | 同上 |
| 允许出场-子场 | `mock/access/exit`（子场设备） | `openGate` | 同上 |
| 按临停计费 | `mock/carTypeQuery` | `parkingType` | 临停=`2`（包月=`1` 白名单=`4` 无牌=`3` 黑名单=`5`） |
| 是否缴费 | `mock/bill/latest` | `paid` | 是=`true` / 否=`false` |
| 缴费金额 | `mock/bill/unpaid` → `fee`；`mock/bill/latest` → `payableAmount` | | ⚠️ 见待决 #1 |

---

## 四、逐条用例落点

### 表1（24 条）

| 用例 | setup 关键 | 触发序列 | 断言 |
|:---|:---|:---|:---|
| 表1-01 白名单·主场 | 主场 + `carWhiteBlack/add` + 设备 | `access/enter`→`access/exit` | `openGate`=true/true |
| 表1-02 白名单·子场 | 子场 + 白名单 + 设备 | `enter`→`exit`（子场设备） | true/true |
| 表1-03 白名单·主+子 | 主+子场 + 白名单 + 两设备 | `enter主`→`enter子`→`exit子`→`exit主` | true×4 |
| 表1-04 白名单仅主·`tempCarIn`=0 | 主+子场 + `tempCarIn`=0 + `otherParkWhiteAccess`=0 + 白名单(仅主场) | `enter主`→`enter子`→`exit主` | 主入 true，**子入 false** |
| 表1-05 白名单仅主·`tempCarIn`=1 | 同上但 `tempCarIn`=1 | `enter主`→`enter子`→`exit子`→`exit主` | true×4 + `carTypeQuery` 子场=`2` |
| 表1-06 白名单·主场·`tempCarIn`=1·转临停 | 主场 + `tempCarIn`=1 + 白名单 | `enter`→**转临停**→`exit` | true/true |
| 表1-07 白名单·主场·`tempCarIn`=0·转临停 | 同上 `tempCarIn`=0 | 同上 | true/true |
| 表1-08 一人多车·`carWhiteOtherIsCasual`=1 | 主场 + 该开关=1 + A/B 白名单 + 设备 | A`enter`→B`enter`→A`exit`→B`exit` | A/B 全 true |
| 表1-09 一人多车·`tempCarIn`=1·开关=0 | 主场 + `tempCarIn`=1 + 开关=0 | 同上 | 入出 true×4 + `carTypeQuery` **A=4 B=2** |
| 表1-10 一人多车·`tempCarIn`=0·开关=0 | 主场 + `tempCarIn`=0 + 开关=0 | A`enter`→B`enter` | **A true / B false** |
| 表1-11 白名单其他车场 | 主场 + `otherParkWhiteAccess`=1 + 上游车场白名单 | `enter`→`exit` | true/true |
| 表1-12 包月车·主场 | 主场 + `mock/monthlyCar/add` + 设备 | `enter`→`exit` | true/true |
| 表1-13 包月车·子场 | 子场 + 包月车 | 子场 `enter`→`exit` | true/true |
| 表1-14 包月车·主+子 | 主+子场 + 包月车 | 主入→子入→子出→主出 | true×4 |
| 表1-15 包月车仅主·`tempCarIn`=0 | 主+子场 + `tempCarIn`=0 + `otherParkMonthAccess`=0 | 主入→子入→主出 | 子入 **false** |
| 表1-16 包月车仅主·`tempCarIn`=1 | 同上 `tempCarIn`=1 | 主入→子入→子出→主出 | true×4 + 子场 `parkingType`=2 |
| 表1-17 包月车·主场·`tempCarIn`=1·转临停 | 主场 + `tempCarIn`=1 + 包月车 | `enter`→转临停→`exit` | true/true |
| 表1-18 包月车·主场·`tempCarIn`=0·转临停 | 同上 `tempCarIn`=0 | 同上 | true/true |
| 表1-19 包月一人多车·开关=1 | 主场 + 开关=1 + A/B 包月车 | A入→B入→A出→B出 | 全 true |
| 表1-20 包月一人多车·`tempCarIn`=1·开关=0 | 主场 + `tempCarIn`=1 + 开关=0 | 同上 | 入出 true×4 + **A 非临停 B 临停** |
| 表1-21 包月一人多车·`tempCarIn`=0·开关=0 | 主场 + `tempCarIn`=0 + 开关=0 | A入→B入 | **A true / B false** |
| 表1-22 包月车其他车场 | 主场 + `otherParkWhiteAccess`=1 + 上游包月车 | `enter`→`exit` | true/true |
| 表1-23 主场白名单+子场包月 | 主+子场 + 主场白名单 + 子场包月 + 两个 `otherPark*Access`=0 | 主入→子入→子出→主出 | true×4 |
| 表1-24 特殊车牌 | 主场 + `specialCarEnable`=1 + `specialAccessConfigList` | `enter`→`exit` | true/true |

> **表1 共性**：每条需 **1 个主场 +（涉子场者）1 个子场 + 1~2 个设备 + 车辆类型数据记录**。
> 「其他车场」两条需 **2 个车场**（上游场 + 本场）。

### 表2（13 条，临停车）

| 用例 | setup 关键 | 触发 | 断言 |
|:---|:---|:---|:---|
| 表2-01 不收费 | 主场 `needPay`=0 | `enter`→`exit` | `bill/latest.paid`=false |
| 表2-02 金额券 | 主场 `needPay`=1 + 收费规则(单价5) + 金额券5元 | `enter`→`exit`→`payWhenLeave` | `fee`=**15** |
| 表2-03 时长券 | 同上 + 时长券1小时 | 同上 | `fee`=**15** |
| 表2-04 双券 | 同上 + 金额券5 + 时长券1h | 同上 | `fee`=**10** |
| 表2-05 无券 | 主场 + 收费规则(单价5) | `enter`→`exit`→`payWhenLeave` | `fee`=**20** |
| 表2-06 无券·提前缴 | 同上 | `enter`→`pay/simulate`→`exit` | 提前缴 **20**；出场 `paid`=true |
| 表2-07 金额券·提前缴 | + 金额券5 | 同上 | 提前缴 **15** |
| 表2-08 时长券·提前缴 | + 时长券1h | 同上 | 提前缴 **15** |
| 表2-09 双券·提前缴 | + 两券 | 同上 | 提前缴 **10** |
| 表2-10 主+子合并 | 主+子场均 `needPay`=1 + 各自收费规则 | 主入→子入→子出→主出 | `fee`=**25** |
| 表2-11 主场未产生费用 | 主+子场（主场不计费） | 同上 | `fee`=**15** |
| 表2-12 主+子·金额券 | + 金额券5 | 同上 | `fee`=**20** |
| 表2-13 主+子·时长券 | + 时长券1h（优先抵主场） | 同上 | `fee`=**20** |

---

## 五、缺口处置（2026-09-11 用户决策）

| # | 事项 | 决策 | 落地 |
|:---|:---|:---|:---|
| **1** | 优惠券无「券→行程」核销端点 | **先不管，用例暂留** | 表2-02/03/04/07/08/09/12/13 共 **8 条**标记「暂留」，本轮不生成。**剩余 29 条**照常 |
| **2** | 「中途转临停」无端点 | **删除该车的白名单/包月车记录即降级为临停车** | 表1-06/07 → `carWhiteBlack/delete`；表1-17/18 → `mock/monthlyCar/delete`。**需回读编码**，见 §5.1 |
| **3** | 共用数据 | **所有涉及共用的数据作为前置，统一生成一次、统一删除** | 见 §六 共享前置设计。附带解掉 R4 的车场名冲突（不再逐用例建场） |
| 4 | `openGate=false` 语义 | 暂以 `openGate` 作代理断言 | 现 `return` 仅此一个字段，无替代 |
| 5 | 每场需独立设备 | 归入共享前置（§六） | `parkingDevice/add` + `getPage` 回读 |
| 6 | 每用例独立车场 | **由决策 3 消解** | 车场改为共享前置，不再逐用例创建 |

### 5.1 转临停的回读链（新增缺口，必须实现）

| 端点 | 问题 | 处置 |
|:---|:---|:---|
| `carWhiteBlack/add` | 返回 `data: null`，**不返回新建编码**；而 `carWhiteBlack/delete` 需 `codes`（裸数组） | 新增后 `carWhiteBlack/getPage` **回读** `$.data.list[…].code` → 再删 |
| `mock/monthlyCar/add` | 只返回 `success`；而 `mock/monthlyCar/delete` 需 `codes`（唯一编号列表） | 新增后 `mock/monthlyCar/query` **回读**编码 → 再删 |

> 与 `bindPayCode` **同类坑**（add 不回码、delete 要码）。V1–V5 校验器的泛化（V6）应覆盖此形态。

### 5.2 `mock/delAllForMock` body 为空（接口事实缺口）

「删除某园区全量进出数据」必需 `parkCode`，但 `api_defs.json` 中 `body: []`。
→ 需按 `_apply_*_facts.py` 同款脚本补入 `api_defs.json` + DB（**不动 prompt**）。

---

## 六、共享前置数据设计（决策 3 落地）

**原则**：车场 / 设备 / 收费规则 **全局生成一次**，被多条用例引用；用例级只创建**车辆类型记录 + 行程**；**统一删除**。

**关键机制**：白名单/包月车的作用域由 `carWhiteBlack/add.parkCode` / `mock/monthlyCar/add.parkCode`（**逗号分隔多车场，`ALL`=全部**）表达 —— 即「仅主场」= 只填主场编码，「子场、主场」= 填两个编码。**故作用域差异不需要额外车场。**

### 6.1 车场分组（按 `carInConfig` 配置变体，最小集）

| 组 | 结构 | tempCarIn | 一人多车 | 其他场白/包 | 特殊车牌 | needPay | 覆盖用例 |
|:---|:---|:---|:---|:---|:---|:---|:---|
| **P1** | 主场 | 0 | 0 | 0/0 | 0 | 0 | 表1-01、07、10、12、18、21、**表2-01** |
| **P2** | 主场 | 1 | 0 | 0/0 | 0 | 0 | 表1-06、09、17、20 |
| **P3** | 主场 | 0 | **1** | 0/0 | 0 | 0 | 表1-08、19 |
| **P4** | 主场 | 0 | 0 | **1**/0 | 0 | 0 | 表1-11、22 |
| **P5** | 主场 | 0 | 0 | 0/0 | **1** | 0 | 表1-24 |
| **P6** | 主场 | 1 | 0 | 0/0 | 0 | **1** | 表2-05、06 |
| **S1** | 子场（挂 P1） | — | — | — | — | 0 | 表1-02、13 |
| **PS1** | 主+子 | 0 | 0 | 0/0 | 0 | 0 | 表1-04、15、23 |
| **PS2** | 主+子 | 1 | 0 | 0/0 | 0 | 子场**1** | 表1-03、05、14、16 |
| **PS3** | 主+子 | 1 | 0 | 0/0 | 0 | **1** | 表2-10、11 |

> **13 个车场实体**（P1–P6 六个 + S1 一个 + PS1/PS2/PS3 各含主+子 = 六个）。
> 每个实体 1 台设备（`deviceCode` 回读）；P6/PS2 子场/PS3 各 1 条收费规则（回读 `bindPayCode`）。

### 6.2 用例级数据（每用例独立，唯一车牌）

| 数据 | 端点 | 说明 |
|:---|:---|:---|
| 白名单记录 | `carWhiteBlack/add` | `carNumber`=随机唯一车牌，`parkCode`=作用域 |
| 包月车记录 | `mock/monthlyCar/add` | 同上 |
| 一人多车 | `carWhiteBlack/add`（`multi`=true + `subCarNumbers`）/ `mock/monthlyCar/add`（`mainCount`+`subCarNumbers`） | A/B 两车 |
| 行程 | `mock/access/enter`/`exit` | 触发 + 断言 |
| 转临停 | `…/delete`（回读编码后） | 表1-06/07/17/18 |

### 6.3 统一删除（teardown）

1. `mock/delAllForMock`（清各园区进出数据 —— 需先补 `parkCode`，§5.2）
2. `parkingPaymentConfig/delete`（收费规则，裸数组）
3. `parkingDevice/deleteByCodes`
4. `carWhiteBlack/delete` / `mock/monthlyCar/delete`（残留类型记录）
5. `parkingPark/deleteByCodes`（车场，裸数组）

> ⚠️ **副作用**：`mock/delAllForMock` 是**园区级全量删除** —— 共享前置下若在用例中途调用，会抹掉其他用例的在途行程。故它**只能出现在统一 teardown**，不得进单用例 teardown。

---

## 七、本草案待你确认

1. **§6.1 的车场分组**（10 组 / 13 实体）是否成立？有没有可合并或我漏掉的配置维度？
2. **§5.2** `delAllForMock` 补 `parkCode` 事实 —— 是否照 `_apply_*_facts.py` 脚本办？
3. **§5.1** 转临停回读链（`getPage`/`query` 回读编码）确认为必做？
4. 暂留 8 条券用例后，本轮 **29 条**是否即为生成范围？
