# 智慧用电_26 执行失败问题清单（生成框架修复依据）

> 执行时间：2026-08-05（共 3 轮）
> 执行范围：`testcase/园区基线/智慧用电_26/SmartPower/`（113 个测试方法）
> 执行方式：`pytest testcase/园区基线/智慧用电_26/SmartPower/`（真实接口调用，登录自动注入 token）
> 执行日志：`smartpower26_run.log`（同目录）

**结论：URL 前缀问题已解决（业务接口可达），但真实 API 暴露了 3 类新问题——最关键的是「retCode 成功约定错误」和「电表新增字段缺失」，是生成数据与真实 API 的契约不匹配。**

---

## 执行进展

| 轮次 | 结果 | 根因 |
|---|---|---|
| 1 | 1 failed + 112 errors（2.11s） | 生成器 fixture 模板 bug（`specification_yaml` 收 list） |
| 2 | 1 failed + 112 errors（9.37s） | 业务接口 405（nginx 未代理）→ 级联 |
| 3（加前缀后） | 6 failed + 2 passed + 106 errors（15.97s） | 真实 API 契约问题（retCode 约定 + 缺字段） |
| 4（retCode 修复后） | **1 failed + 112 errors（13.74s）** | retCode 已修复（6→1），剩余 error 全为**真实 API 拒绝**（见「当前阻塞」） |

> **问题 2 已在本地修复**：240 处成功断言 `retCode 0/200→1`（76 文件）；反向失败断言保留 0；SETUP 弱断言 `ne:200` 保留。修复后正向断言约定正确。

## 当前阻塞（第 4 轮后，均为真实 API 拒绝，非断言问题）

| 根因 | 频次 | 说明 |
|---|---|---|
| 🔴 **404 路径** `/ElectricRentMoney/getApartmentRentMoneyPage` | 106 | api_defs 路径错误，真实 API 无此路径 |
| 🔴 **404 路径** `/ElectricMonthBill/getMasterPage` | 10 | 同上 |
| 🔴 `缺少固定电费配置` | 28 | payConfig/insert payload 缺固定电费配置 |
| 🔴 `充值失败:租户不存在` | 24 | 充值依赖的租户/账户未创建（setup 依赖未满足） |
| 🟡 `电表场景/级别不能为空` | ~24 | add meter 缺必填字段（问题 3，未修） |
| 🟡 `初次配置后无法修改!` | 18 | 结算配置重复保存（setup 幂等问题） |
| 🟡 `计费方案不存在` | 10 | 引用未创建的方案 |
| 🟡 `请求失败` | 18 | 通用 |

**结论**：retCode 约定修复后，套件不再有"断言约定"级失败；剩余全部是**测试数据与真实 API 的契约问题**（路径 404、payload 缺字段/缺值、setup 依赖未满足），是生成框架下一层要修的重点。

---

## 问题 1（✅ 已解决）：业务接口 405 → 增加 `/park-energy-electric-web` 前缀

**根因**：`config.ini` 的 host `dev.damaiiot.com:40443` 是 nginx 前端，只代理 `/park-base-auth`（登录），业务接口 POST 全 405、GET 返回前端 SPA 页面。

**解决**：实测确认业务 API 挂在 context path `/park-energy-electric-web` 下，已给全部 128 个 YAML 的 **279 处 url 加前缀**（`/electricMeter/add` → `/park-energy-electric-web/electricMeter/add`）。验证：
```
POST /park-energy-electric-web/electricMeter/add          → 400（JSON 校验："电表场景不能为空..."）
POST /park-energy-electric-web/electricMeter/getParentList → 200 {"retCode":1,"msg":"success"}
GET  /park-energy-electric-web/*/export                   → 200 + Excel 文件
```

**生成框架启示**：①api_defs 应含 base url / 服务前缀；②若生成器自动拼前缀，**必须按行处理且保留行尾（\r\n）**——本次批处理曾因 CRLF 行尾把 `testCase:` 并进 url 行导致 YAML 损坏，已通过「在 url 路径后重新断行」恢复。

---

## 问题 2（🔴 新发现）：retCode 成功约定错误（生成断言 0，真实 API 用 1）—— 仅查询类接口纯断言问题

### 现象
`$.retCode 期望 0，实际 1` 大量出现（~350 处）。

### 已实测确认（2026-08-05）
```
POST /electricMeter/getParentList → {"retCode":1,"msg":"success","data":[]}   ← 成功 = 1
POST /electricMeter/getPage       → {"retCode":1,"msg":"success","data":{"total":1,...}}  ← 成功 = 1
POST 登录                        → {"retCode":1,"msg":"success"}                           ← 成功 = 1
POST /electricMeter/add 缺字段   → {"retCode":0,"msg":"fail","data":"..."}                  ← 失败 = 0
```
**真实 API 成功 = `retCode:1`，失败 = `retCode:0`**。生成器把成功码写成了 0。

### 重要澄清（针对"环境里没有新增电表"的疑问）
**这不是唯一/主因**。已用**补全必填字段的完整 payload** 实测：`/electricMeter/add` 返回 `{"retCode":1,"msg":"success"}` 且 **getPage 查到电表真实入库**（total:1）→ 说明 `retCode:1` 确实是成功、添加接口本身可用。
- **查询类接口**（getParentList/getHistoryPage/getPage）在测试里**确实成功了**（retCode:1），只是断言写反（该断 1）→ **纯断言问题**。
- **添加类接口**（add meter）在测试里**是真失败**（retCode:0 + 校验信息）→ 见问题 3，这才是"环境里没新电表"的真正原因。

### 生成框架修复
正向用例成功码统一改 `eq: {$.retCode: 1}`（影响查询类断言；添加类还需修字段，见问题 3）。

---

## 问题 3（🔴 已实测确认为"环境没新电表"的真正原因）：电表新增接口缺必填字段

### 现象
```
[添加电表] 返回值 … '电表场景不能为空;接入方式不能为空;电表名称不能为空;电表编号不能为空'
[添加电表] … '电表场景不能为空;电表场景不能为空'   （setup 里 add meter 全部被拒）
```

### 已实测确认（2026-08-05）
用**补全必填字段的完整 payload** 直接调 `/electricMeter/add`：
```
请求含 code/name/accessMethod/accessType/meterTypeCode/sceneCode/sceneName/level/leasingEntity/whetherToCount/useType/deviceStatus/personCode 等
→ {"retCode":1,"msg":"success","data":null}
→ getPage 按 code 查询 → {"total":1,"list":[{...code":"DIAG_FULL_001"...}]}   ← 电表真实入库
```
**结论**：接口本身可用，成功码 retCode=1；测试里没建出电表，是因为**生成的 add payload 缺少必填字段**（sceneCode 电表场景、accessMethod 接入方式、name 名称、code 编号、level 级别）。

### 根因
生成器未按 api_defs 的 `required` 字段填全必填项。api_defs 参数列表把全部字段标 required（质量问题），但生成器并未真正填充，导致缺字段被真实后端校验拒绝。

### 生成框架修复
- 生成 add/insert 类请求时，按 api_defs 的 `required` 字段**逐一填充非空值**（不省略、不填空字符串）。
- api_defs 参数提炼需修正：区分"新增必填"与"查询可选"。

---

## 问题 4（🟡 新发现）：个别接口路径 404

### 现象
```
JSONPath "$.retCode" 未匹配 … response: {'status': 404, 'path': '/ElectricMonthBill/getMasterPage'}
```
### 根因
`/ElectricMonthBill/getMasterPage` 在真实 API 上 404 —— api_defs 中的路径与真实接口不符（可能漏了服务前缀以外的 context，或路径本身错）。

### 生成框架修复
校验 api_defs 每个路径在真实环境可达（可加一次接口探测/契约测试）。

---

## 问题 5（🟡 已修复·生成框架需防）：批处理加前缀导致 CRLF 行尾 YAML 损坏

### 现象
批量给 279 处 url 加前缀后，全部 128 个 YAML 解析失败（`mapping values are not allowed here`）。

### 根因
文件为 **CRLF（\r\n）** 行尾。文本模式下 `readlines()` 将 `\r\n` 转成 `\n`，正则 `(/.*)$` 吞掉行尾后替换时**丢失 `\n`**，导致下一行内容（`testCase:`）被拼进 url 行。

### 修复（本次已做）
在 url 路径后重新断行（`^(\s*url: /park-energy-electric-web/\S+)(\s+\S.*)$` 加 `re.MULTILINE` → 替换为 `\1\n\2`），128 文件全部恢复可解析。

### 生成框架启示
若生成器/批处理脚本要改 YAML 文本：**用逐行处理 + 保留行尾，或统一换行符（LF），禁止在文本模式下用 `$` 匹配整行并丢弃行尾**。

---

## 问题 6（🟡 未变）：未授权用例断言 401 必然失败

`test_global_unauthorized_export_negative` 断言 `contains: {status_code: 401}`，但框架自动注入 token → 实际 200。生成策略应禁止"无 token 访问"类用例。

---

## 问题 7（🟡 未变）：teardown 生成超长失败

`teardown_power_consumption_statistics.yaml` 因 LLM 输出 8192 token 触顶缺失（GEN-FAIL-R2-007）。需分块生成 + 失败告警。

---

## 执行统计（第 3 轮：前缀修复后）

| 类别 | 数量 | 根因 |
|---|---|---|
| 6 failed | 5×结算配置 + 1×全局401 | retCode 约定 / 401 前提 |
| 2 passed | `test_settlement_config_add_enterprise_postpaid_positive` 等 | 正向 payload 合法 |
| 106 errors | ~350 处 retCode 0→1 + ~19 处电表字段缺失 + 级联 KeyError | 问题 2 / 3 为主 |

---

## 修复优先级建议

1. **问题 2（retCode 约定）**：正向断言 `0`→`1`（影响 ~350 处，修复收益最大）。
2. **问题 3（电表必填字段）**：补全 add 接口必填字段。
3. **问题 4（404 路径）**：校验 api_defs 路径真实可达。
4. **问题 5（CRLF）**：生成器/批处理脚本按行处理、保留行尾。
5. **问题 6/7**：401 前提禁止 + teardown 分块生成。
