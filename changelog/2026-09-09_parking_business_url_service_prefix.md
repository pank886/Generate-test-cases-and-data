# 2026-09-09 智慧停车业务接口 url 补服务前缀：/park-access-parking-web（数据维护）

> **状态：已施工完成并自查通过（2026-09-09）。** 用户提供真实服务根 `/park-access-parking-web`，
> 指示「同步都加一下：已生成测试文件 + 存档数据」。纯数据维护，不动代码/prompt。
> 依据：16:17 执行日志 110 次调用里 105 次业务接口全被 nginx 405（url 缺服务前缀），
> 仅带前缀的 mock（/park-access-parking-rule-new）与用电（/park-energy-electric-web）可跑通。
> 原则：①先确认②最小改动③连带汇总④含验证、改完自查⑤文档先行。

## 一、现象（执行日志 test.20260909161740.log）

框架把生成的 YAML 跑到 `https://dev.damaiiot.com:40443`：

| 结果 | 次数 | 对象 |
|:---|:---|:---|
| ✅ | 5 | mock：`/park-access-parking-rule-new/mock/delAllForMock`、`/monthlyCar/add` |
| ❌ 405 Not Allowed | 105 | 业务接口：`parkingPaymentConfig/add`(7) `parkingPark/saveOrUpdate`(43) `parkingDevice/add`(35) `carWhiteBlack/add`(13) `parkingCouponIssue/add`(6) `parkingPark/getDetail/{..}`(1) |

业务响应全是 nginx 405 HTML → JSONPath `$.msg`/`$.retCode` 断言对 `{}` 失败 → 真实 parkCode/
ruleCode 未捕获 → setup 起逐级级联失败，日志只跑到 setup（PRE-017）即清场。

## 二、根因：url 缺网关服务前缀（不是生成代码逻辑）

框架把 spec 里的 url **逐字**接到网关后，不自动补服务上下文。对比：

| 模块 | 数据里存的 url | 执行结果 |
|:---|:---|:---|
| 智慧用电 add/delete | `/park-energy-electric-web/electricMeter/add`（数据已带前缀） | ✅ |
| 智慧停车 mock | `/park-access-parking-rule-new/mock/...`（数据已带前缀） | ✅ |
| 智慧停车业务 | `/parkingDevice/add`、`/parkingPaymentConfig/add`（**裸 controller 路径**） | ❌ 405 |

SQLite `documents`（doc_type=api）：用电的 `update/getPage` 等同样裸存（本轮只跑到带前缀的
add/delete 故未暴露，是**同类潜在缺陷**，本次不扩）；智慧停车业务接口 **28 条全部裸存**，
mock 11 条带前缀。生成器只是把 `api_url` 抄进 YAML，根因在接口 url 数据。
（这 28 条全部来自智慧停车接口源文档 `0907-测试接口api(1).md`，id 前缀 `api_0907-…`；
bindings 中并无该模块的 api→module 直连行，选择依据是源文档归属而非绑定表。）

## 三、修复方案（已确认）

业务接口 url 统一补前缀 `/park-access-parking-web`，两处同步：
1. **存档数据**：SQLite `documents.api_url` —— 28 条（doc_type='api'、url 为裸停车业务控制器
   `/parkingDevice|parkingPark|parkingPaymentConfig|carWhiteBlack|parkingCouponIssue/`，
   源文档 `0907-测试接口api(1).md`）。
2. **已生成测试文件**：`C:/Users/damai/PyCharmMiscProject/testcase/园区基线/智慧停车/SmartParking/**`
   下 64 个 yaml 的 `url:` 值（66 个 yaml 中 64 个含裸业务 url）；`.py` 不含 url 不需改。
   `test_plan.xlsx` 步骤文本不参与框架执行，不改。
3. 连带：模块目录 `api_defs.json` 快照同步（与存档一致，防误导）；mock/已带前缀/其它模块不动。

### 不改 / 不扩
- 用电 `electricMeter/update、getPage` 等裸 url —— 同类潜在缺陷，另立方案再修（本次不扩）。
- `_generation_errors.json` 等失败现场文件 —— 保留供查，重新生成会重建。
- 数据里「硬编码字面量跨步不一致」待前缀修好、真响应可捕获后再评估（见 §五 现象 2）。

## 四、验证设计（数据改动自查）

1. 前置：圈定 28 条全部归属「智慧停车」，mock 11 条不受影响（已勘察）。
2. SQLite：备份 `data/backup_parking_business_api_url_20260909.json`（id+url）→ 更新 →
   复查裸业务 url 归零、无双重前缀、mock 前缀不变（幂等）。
3. 测试文件：yaml `url:` 补前缀后复查 SmartParking 内裸业务 url 归零；再跑同一条目不重复加
   （幂等）。
4. 重跑执行：复用模块目录 pytest（框架侧），业务接口应从 405 → 正常 JSON（retCode/msg），
   由用户执行确认。

## 五、实施记录（2026-09-09）

| 位置 | 改动 |
|:---|:---|
| `data/app.db` documents.api_url | ✅ 28 条业务接口 url 前补 `/park-access-parking-web` |
| `data/backup_parking_business_api_url_20260909.json` | ✅ 改动前 28 条 (id, file_name, api_method, api_url) 备份，7,169 bytes |
| 智慧停车/SmartParking/**/*.yaml | ✅ 64 个文件、126 处 `url:` 值补前缀 |
| 智慧停车/api_defs.json | ✅ url 快照 28 条同步补前缀 |

**自查结果**（SQLite 侧 `python` 复查 + 文件侧扫描）：
1. SQLite：更新后裸业务 url（doc_type=api，无前缀）**0**；带 `/park-access-parking-web` 总数 **28**；
   幂等——二轮重跑命中 **0**；mock `/park-access-parking-rule-new` 11 行 **不变**。
2. 文件侧：SmartParking yaml 裸业务 `url:` 剩余 **0**、带前缀 **126 行 / 64 文件**、双重前缀 **0**、
   幂等二轮应改 **0**（实际 0）；mock `/park-access-parking-rule-new` url 行保持原样。
3. api_defs.json：裸剩余 **0**、带前缀 **28**。
4. 未触碰：`.py`（无 url）、test_plan.xlsx 步骤文本（框架不执行）、模块根其它 json（无 url 值）、
   用电裸 url（同类潜在缺陷另案）。
