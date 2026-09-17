# VehicleAccess 参考实现分析（2026-09-11）

对象：`C:\Users\damai\PyCharmMiscProject\testcase\VehicleAccess`
规模：1 个驱动（2049 行）+ 35 个用例组 + 278 个 YAML。

---

## 一、结构（.py 驱动）

| 层 | 对应 | 说明 |
|:---|:---|:---|
| 文件 | `test_Vehicle_access.py` | 一个模块一个驱动 |
| class | **一条用例（场景）** | 35 个 `TestVehicleAccess_NNN` |
| method | **一个步骤** | `@pytest.mark.order(N)` 定序 |
| 目录 | `<组>/<步骤>.yaml` | 每步一个 YAML（**一个 YAML 也可含多步**，见 002） |

驱动模板：

```python
@allure.story('...')
@pytest.mark.danyuan
class TestVehicleAccess_NNN:
    @allure.title("NN_步骤名")
    @pytest.mark.order(N)
    @pytest.mark.parametrize('params', get_testcase_yaml('./testcase/VehicleAccess/VehicleAccesstest_NNN/x.yaml'))
    def test_Step(self, params):
        RequestsBase().specification_yaml(params)
```

> ⚠️ 入口是 `get_testcase_yaml` + `specification_yaml`，**与 SmartParking 的 `ReadYamlData` + `run_blocks` 不同**。

**系统前置**：`TestCleanAllData`，`@pytest.mark.order(-1)`，story 注明「所有用例前置」—— 正对应「每次执行前跑一次」。

---

## 二、接口调用顺序关系（核心）

### 2.1 标准骨架（以 016「主场到子场」为例）

```
[系统前置]  mock/delAllForMock        json:{}                     order(-1)
   ↓
[数据准备]  mock/monthlyCar/add       carNumber=${random_plates(1)}
            或 mock/whitelist/add      input_extract: carANumber=$.json.carNumber
   ↓
[入场]      mock/access/enter         deviceCode=mainCarInDeviceCode   断言 openGate:true
   ↓
[入子场]    mock/access/enter         deviceCode=pankparkin            断言 openGate:true
   ↓
[出子场]    mock/access/exit          deviceCode=pankparkout           断言 openGate:true
   ↓
[出主场]    mock/access/exit          deviceCode=mainCarOutDeviceCode  断言 openGate:true
   ↓
[计费]      mock/bill/unpaid          carNumber + currentTime          断言 fee:N
                                      extract: unpaidFee=$.fee
   ↓
[缴费]      mock/pay/simulate         fee=${get_extract_data(unpaidFee)}  断言 success:true
   ↓
[回读]      mock/monthlyCar/query     extract: carACode=$.[0].code
            或 mock/whitelist/query
   ↓
[删除]      mock/monthlyCar/delete    codes:["${get_extract_data(carACode)}"]
            或 mock/whitelist/delete
```

### 2.2 顺序规律（逐条实证）

1. **前置清理必须最先**，且 `json: {}` **无参** —— 全量清。
2. **入场必在出场前**，且出场时间 > 入场时间。
3. **出场断言 `openGate`**：`true`=放行 / `false`=拦下（`carOutFalse_005` = 临停未缴费被拦）。
4. **计费断言在 `bill/unpaid`**，需传 `currentTime`（落在行程内），`fee` 由**服务端算**（`eq: {fee: 10}`）。
5. **`pay/simulate` 的 `fee` 不自算** —— 是 `${get_extract_data(unpaidFee)}`（**先读后付**）。
6. **转类型/删除必须「先 query 回读 code、再 delete by codes」**（见 §三）。
7. **一人多车**：`monthlyCar/add` 带 `subCarNumbers:[车牌]` + `mainCount:2`；
   `input_extract` 分别存主/辅车牌（`carANumber=$.json.carNumber`、`carBNumber=$.json.subCarNumbers`）。
   辅车牌存的是**数组** → 引用须用 `get_extract_data_list(carBNumber, 0)`。
8. **子场顺序**：入主 → 入子 → 出子 → 出主（016 实测该序）。

### 2.3 `parkCode` / `deviceCode` 全部是**约定字面量**

| 用途 | 值 |
|:---|:---|
| 主场 parkCode | `test` |
| 另一车场 parkCode | `zhyq682545835296730` |
| 主场入场设备 | `mainCarInDeviceCode` |
| 主场出场设备 | `mainCarOutDeviceCode` |
| 子场入场设备 | `pankparkin` |
| 子场出场设备 | `pankparkout` |

**VehicleAccess 全程不调用 `parkingPark/saveOrUpdate`、无 `carInConfig`、无 `tempCarIn`** —— 完全依赖**预置 mock 环境**。

---

## 三、回读链（删除/转类型）

三处完全一致的形态：

| 步骤 | 端点 | 关键 |
|:---|:---|:---|
| add | `mock/monthlyCar/add` | `input_extract: carANumber=$.json.carNumber` |
| query | `mock/monthlyCar/query` | `extract: carACode=$.[0].code` ← **回读编码** |
| delete | `mock/monthlyCar/delete` | `codes:["${get_extract_data(carACode)}"]` |

白名单同构：`mock/whitelist/add` → `/query`（`extract $.[0].code`）→ `/delete`（`codes:[...]`）。

---

## 三补、⚠️ 定位更正（2026-09-11 用户澄清）

**VehicleAccess 是旧版本**，其中**所有 `mock/*` 端点都是旧的**。本模块**只参考「相互关系」（调用顺序）**，端点与约定值**一律不照抄**。

| 更正项 | 说明 |
|:---|:---|
| 测试范围 | **智慧停车模块绑定的全部 39 个接口**（新旧都在内），**含车场配置**（`parkingPark/saveOrUpdate` + `carInConfig.*`）与**收费规则配置**（`parkingPaymentConfig/*`） |
| 白名单 | 用**新的（不带 mock）** → `carWhiteBlack/add|getPage|delete`，**不用** `mock/whitelist/*` |
| 车场 | **表1、表2 不区分车场，可混用** —— 按**配置变体**分组即可，不按表分 |
| 设备 | 不再沿用旧版的约定字面量；`parkingDevice/*` 在范围内 → 需建 + **回读** `deviceCode`（**待确认**） |

**仍然有效**（纯顺序关系，与新旧无关）：
1. 前置清理最先（`order(-1)`）、系统级跑一次
2. 入场必在出场前；出场时间 > 入场时间
3. **`openGate` 是放行断言**（`true`放行 / `false`拦下）
4. 计费查询需传 `currentTime`（落在行程内）
5. **先读后付**：先查应缴，再拿读回的金额缴费
6. **转类型/删除必须「先 query 回读 code、再 delete by codes」**
7. 一人多车：主车牌 + 辅车牌（`subCarNumbers`），辅车牌存的是数组 → 引用用 `get_extract_data_list(x, 0)`
8. 子场顺序：入主 → 入子 → 出子 → 出主

---

## 四、与前面草案的差异（**三处推翻**）

> ⚠️ **本节结论已部分失效** —— 见上方「三补」。第 1、2 条（不建设备/不建车场）因**测试范围含车场配置与收费规则配置**而**不再成立**；
> 第 3 条（`delAllForMock` 无参）**仅适用于旧版**，新版待核。保留原文以便对照。

| 我的草案 | 实际 | 结论 |
|:---|:---|:---|
| 需 `parkingDevice/add` 建设备、回读 `deviceCode` | deviceCode 是约定字面量 | ❌ **作废**，不需要建设备 |
| §6 建 10 组 / 13 个车场实体 | 用预置 `parkCode: test` | ❌ **作废**，不建车场 |
| `delAllForMock` 缺 `parkCode`，需补接口事实 | 无参 `{}`，全量清，作为**系统前置** | ❌ **我的判断错了**，无需补事实 |
| `openGate` 仅作代理断言、保真度存疑 | 参考实现直接用 `eq: {openGate: true/false}` | ✅ 官方断言 |
| 缴费金额需自算/关系式断言 | `bill/unpaid.fee` 服务端算，直接 `eq` | ✅ 可数值断言 |
| 白名单用 `carWhiteBlack/add` | 参考用 `mock/whitelist/add|query|delete` | ⚠️ **两套并存**，见 §五-2 |
| 转临停需回读链 | **证实**（query → extract code → delete） | ✅ 我的判断对 |

---

## 五、必须你定的 3 件事

**1. 表1 的「条件」列怎么落地？**
VehicleAccess **从不配置车场**（无 `tempCarIn` / `carWhiteOtherIsCasual` / `otherParkWhiteAccess` / `specialCarEnable`）。
而表1 的 24 条用例**大量依赖这些开关**。
→ 是 **mock 环境预置**（那就无法按用例切换开关，多条用例会撞同一配置），还是**需要 `parkingPark/saveOrUpdate` 配置**？

**2. 白名单走哪套接口？**
- `mock/whitelist/add|query|delete`：参考在用，但 **SmartParking 的 `api_defs.json` 缺这 3 个端点**
- `carWhiteBlack/add|getPage|delete`：在 `api_defs.json` 里，但语义重（审批/车位/车场绑定）
→ 选哪套？

**3. §6 共享车场设计是否作废？**
若用预置 `parkCode: test`，我那套设计整体不需要；但表1 开关条件就没地方落（同问题 1）。

---

## 六、参考实现自身的缺陷（供参考，不照抄）

1. **驱动引用了不存在的 YAML**：`VehicleAccesstest_007/queryWhitelistADeleted_007.yaml`（`order(9)`）**磁盘缺失**
   —— 与 SmartParking 那「12 个 .py 引用 YAML 缺失」同类问题。
2. **多个组被注释停用**：story 009 / 020 / 022 等（`# @allure.story(...)`），导致后续 class 编号与组号错位。
3. **注释自承依赖未具备**：如 group 001 标「需车辆 code，删除入场记录接口」、group 002 标「需一人多车白名单接口」。
