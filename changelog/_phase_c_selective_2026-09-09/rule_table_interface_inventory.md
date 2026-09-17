# 智慧停车接口清单（39 个，按生成角色分类）

来源：`C:\Users\damai\PyCharmMiscProject\testcase\园区基线\智慧停车\api_defs.json`
用途：判定表驱动生成的**可用接口面**。`mock/*` = 旧版约定面（本次测验对象之一）。

---

## A. 车场配置（4）

| 接口 | 作用 | 关键字段 |
|:---|:---|:---|
| `parkingPark/saveOrUpdate` | **建/改 车场及全部关联配置**（一次可带 `carInConfig` + `paymentConfig` + `specialAccessConfigList`） | `parkingPark.{parkCode, parkName, mainPark, parentCode, needCount, bindPayCode, needPay}`<br>`carInConfig.{tempCarIn, carWhiteOtherIsCasual, nobodyPark, specialCarEnable, enableMisMatch, innerOpenWhenNotMatch, otherParkWhiteAccess, otherParkWhiteAccessParks, otherParkMonthAccess, otherParkMonthAccessParks}`<br>`paymentConfig.{name, unitPrice, freeTime, realFree, maxParkingFee, minToLeaveAfterAdv, configType, mainPark, parkCode}`<br>`specialAccessConfigList[].{carNumber, enable, parkCode, parkName}` |
| `parkingPark/getDetail/{parkCode}` | **车场全量回读/断言**（基础 + `carInConfig` + `paymentConfig` + `specialAccessConfigList`） | 返回 `data.parkingPark.*` / `data.carInConfig.*` / `data.paymentConfig.*` / `data.specialAccessConfigList[]` |
| `parkingPark/getAllWithDevices` | 查所有车场及其进出场设备 | 入 `parkName, parkCode, mainPark`；返 `data[i].parkingPark.{parkCode,…}` + `carInDeviceList`/`carOutDeviceList` |
| `parkingPark/deleteByCodes` | 删车场（**裸数组**） | `codes` |

## B. 收费规则（6）

| 接口 | 作用 | 关键字段 |
|:---|:---|:---|
| `parkingPaymentConfig/add` | **建临时停车收费规则** | `name, mainPark, unitPrice, freeTime, realFree, maxParkingFee, minToLeaveAfterAdv, configType(=3), parkingLotType` |
| `parkingPaymentConfig/update` | 改规则（带 `code` 定位） | 同上 + `code` |
| `parkingPaymentConfig/delete` | 删（**裸数组**） | `codes` |
| `parkingPaymentConfig/getParkingPaymentConfigPage` | **分页查 → 回读 `code`** | 入 `pageNum, pageSize, configType, mainPark, parkCode, parkName`；返 `data.list[].code` |
| `parkingPaymentConfig/getParkingPaymentConfigList` | 列表查 | 同上（无分页） |
| `parkingPaymentConfig/getByCode/{code}` | 单条查 | `code` |

## C. 设备（7）

| 接口 | 作用 | 关键字段 |
|:---|:---|:---|
| `parkingDevice/add` | **建设备** | `code, name, parkingDeviceManufacturerCode/Name, parkingDeviceModelCode/Name, placeCode/Name, passType, deviceType, parkCode, parkName, …` |
| `parkingDevice/update` | 改设备（带 `code`） | 同上 |
| `parkingDevice/deleteByCodes` | 删（逗号串壳） | `codes` |
| `parkingDevice/getPage` | **分页查 → 回读 `data.list[].code`** | 入 `pageNum, pageSize, searchKey, passType, deviceStatus, parkCode, mainPark` |
| `parkingDevice/getList` | 设备列表 | `searchKey, passType, mainPark` |
| `parkingDevice/getMainDevices` | 主相机设备列表 | `parkCode, mainPark, mainDevice` |
| `parkingDevice/getByCode/{code}` | 单条查 | `code` |

## D. 黑白名单（5）

| 接口 | 作用 | 关键字段 |
|:---|:---|:---|
| `carWhiteBlack/add` | **加白/黑名单**（**返回 `data:null`**） | `carNumber, isWhite(1白/0黑), multi, subCarNumbers[], parkCode（逗号分隔/ALL）, parkName, enterStartDate, enterEndDate, status, lotCode, …` |
| `carWhiteBlack/update` | 改（带 `code`） | 同上 + `code` |
| `carWhiteBlack/delete` | 删（**裸数组**） | `codes` |
| `carWhiteBlack/getPage` | **分页查 → 回读 `data.list[].code`** | 入 `pageNum, pageSize, searchKey, isWhite, parkCode, status` |
| `carWhiteBlack/getByCode/{code}` | 单条查 | 返 `data.{code, carNumber, isWhite, multi, parkCode, subCarNumbers, …}` |

## E. 优惠券（6）— 暂留（8 条券用例本轮不生成）

`parkingCouponIssue/add|update|deleteByCodes|getList|getPage|getByCode/{code}`
字段：`name, type(金额/时长), denominationMoney, denominationHour, denominationMinite, num, price, startTime, endTime, saleForMobile, parkCode, parkName`。
⚠️ 无「券→行程」核销接口（见映射草案 §五-1）。

## F. Mock 测试面（11）

| 接口 | 角色 | 入参 | 返回 |
|:---|:---|:---|:---|
| `mock/access/enter` | **触发·入场** | `carNumber, time, deviceCode` | **`openGate`** |
| `mock/access/exit` | **触发·出场** | 同上 | **`openGate`** |
| `mock/bill/latest` | **断言·账单** | `carNumber` | `paid, payableAmount, paidAmount, parkingType, entryTime, exitTime` |
| `mock/bill/unpaid` | **断言·待支付** | `carNumber, currentTime` | `fee` |
| `mock/carTypeQuery` | **断言·在场类型** | `carNumber, parkCode, startTime` | `inPark, parkingType`（1包月/2临停/3无牌/4白名单/5黑名单） |
| `mock/delAllForMock` | **系统前置**（无参） | — | `success` |
| `mock/monthlyCar/add` | 建包月车 | `parkCode, carNumber, startTime, endTime, subCarNumbers[], mainCount` | `success` |
| `mock/monthlyCar/query` | **回读包月车 code** | `carNumber, parkCode` | `code, id, carNumber, …, subCarNumbers` ⚠️ |
| `mock/monthlyCar/delete` | 删包月车 | `codes`（唯一编号列表） | `success` |
| `mock/payWhenLeave/simulate` | 触发·出场缴费 | `carNumber, fee, payTime, deviceCode` | `success` |
| `mock/pay/simulate` | 触发·提前缴费 | 同上 | `success` |

---

## 角色汇总

| 角色 | 接口 |
|:---|:---|
| **系统前置** | `mock/delAllForMock`（`order(-1)`，一次） |
| **建数据（setup）** | `parkingPark/saveOrUpdate`、`parkingPaymentConfig/add`、`parkingDevice/add`、`carWhiteBlack/add`、`mock/monthlyCar/add` |
| **回读（引用链）** | `parkingPaymentConfig/…Page`→`code`、`carWhiteBlack/getPage`→`code`、`mock/monthlyCar/query`→`code`（**设备不回读**：`add` 的 `code` 自生成，用 `input_extract: <k>DeviceCode: $.json.code`，见 §一.4） |
| **触发** | `mock/access/enter|exit`、`mock/pay/simulate`、`mock/payWhenLeave/simulate` |
| **断言** | `mock/access/*.openGate`、`mock/bill/latest`、`mock/bill/unpaid.fee`、`mock/carTypeQuery`、`parkingPark/getDetail` |
| **清理** | `parkingPark/deleteByCodes`、`parkingDevice/deleteByCodes`、`parkingPaymentConfig/delete`、`carWhiteBlack/delete`、`mock/monthlyCar/delete`（券接口本轮不用） |

---

## 生成时必须守的三条引用铁律

1. **`bindPayCode` 必须来自 `parkingPaymentConfig/…Page` 回读的 `code`** —— 不得用规则名字面量、不得空串（R3/R4 头号根因，V1–V5 已强制）。
2. **删除类必须先回读 code**：`carWhiteBlack/add` 返回 `data:null`、`mock/monthlyCar/add` 只返回 `success` → 各自 `getPage` / `query` 回读后再 `delete`。
3. **`extract_list` 产出的列表引用必须带索引**（`get_extract_data('k', 1)`），否则被 `str()` 成 `"['…']"`（断点②）。

## ✅ 运行期实测（2026-09-11，探针 `tests/tools/_probe_monthlycar_query_shape.py`）

对 dev `https://dev.damaiiot.com:40443` 实测 `mock/monthlyCar/*`：

### 实测 1 —— `mock/monthlyCar/query` 返回**裸数组**（api_defs 记错）

```json
[{"code":"zhyq188420780","id":"2590","carNumber":"京A88888",
  "startTime":"2026-01-01","endTime":"2027-12-31",
  "parkCode":"zhyq132705726111715","subCarNumbers":[]}]
```

- **JSONPath = `$.[0].code`**（旧版 VehicleAccess 写法才对）
- `api_defs.json` 记成「顶层 `code`」（→ `$.code`）且带 `retCode/msg/data` 外壳 → **与实测不符**

### 实测 2 ⚠️ **接口缺陷：`mock/monthlyCar/delete` 传车牌 → 假成功**

| 传入 `codes` | 响应 | 实际效果 |
|:---|:---|:---|
| `["京A88888"]`（车牌） | `200 {"success": true}` | ❌ **记录仍在**（未删） |
| `["zhyq188420780"]`（真实 code） | `200 {"success": true}` | ✅ 真删（再 query 得 `[]`） |

→ **必须回读真实 `code` 再删**；传车牌会**静默返回成功但不生效**（假成功，比报错更危险）。

### 实测 3 —— `add` 允许重复

同车牌 + 同车场重复 `add` → 产生多条记录（实测 id 2590/2591/2592…）。
→ `$.[0].code` 只取第一条；生成器须保证**建一次**（共享前置里一次性建）。

### 其它实测

- `mock/monthlyCar/add` / `delete` 响应 = `{"success": true}`（**无 `retCode`/`msg` 外壳**）→ `validation` 用 `contains: {success: true}`
- `mock/monthlyCar/query` 无 `success` 字段 → 其 `validation` 只能断 `$.[0].carNumber` 或留空
- `startTime` 回读被截断为**日期**（存入 `2026-01-01 00:00:00` → 返回 `2026-01-01`）
- `parkingPark/getAllWithDevices` 实测返回 `carInDeviceList`/`carOutDeviceList`，且现网残留旧批次车场（`bindPayCode` 为 `zhyqC…` 回读值，**非字面量**——正确形态）
