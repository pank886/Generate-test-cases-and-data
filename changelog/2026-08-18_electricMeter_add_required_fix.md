# electricMeter/add 接口必填信息修正方案

> 日期：2026-08-18
> 状态：**✅ 已按此方案实施完成**（用户决策：必填口径=非空值 22 个；只改 required 不动字段集）
> 背景：用户提供 `/electricMeter/add` 的真实成功请求体（标注「必填参数」）与返回值，要求把存储的该接口信息按实际必填修正。

## 0. 实际核查（当前存储）

| 项 | 结论 |
|----|------|
| 存储位置 | `data/app.db` → `documents.api_parameters`（运行时唯一事实源；`database/app.db` 空表、Chroma 仅 `doc_search` 不含 API 定义） |
| 数据来源 | 源 YApi 文档不在 uploads，无法重导 → 直接改库即全局生效（Phase B/C 运行时实时读库，`retrievers.py`/`dual_chroma.py`） |
| 当前 required=True（8 个） | `code` / `name` / `sceneCode` / `sceneName` / `level` / `whetherToCount` / `meterTypeCode` / `accessMethod` |
| 当前 required=False | 其余全部（含实际必填但未标、以及大量查询参数） |

## 1. 用户真实成功请求（返回 retCode=1）的字段

请求体 35 个 key。其中**非空值**字段 22 个：

**已在必填中（8）**：`code` / `name` / `sceneCode` / `sceneName` / `level` / `whetherToCount` / `meterTypeCode` / `accessMethod`

**实际必填但当前非必填（14，需改 True）**：
`meterDeviceType` / `meterDeviceManufacturerCode` / `meterDeviceManufacturerName` / `meterDeviceModelCode` / `meterDeviceModelName` / `useType` / `electricity` / `purpose` / `billingFactor` / `autoOff` / `mutualInductorOnOrOff` / `powerRate` / `payConfigCode` / `accessType`

**空值（13，倾向保持可选）**：`electricControlDeviceCode` / `electricControlDeviceName` / `roomCode` / `roomNumber` / `buildingCode` / `buildingName` / `memo` / `maxValue` / `parentDeviceCode` / `parentDeviceName` / `personCode` / `personName` / `initDetailList`（空数组）

## 2. 方案外发现（需用户决策）

1. **字段范围污染**：`/electricMeter/add` 的参数列表与 `/electricMeter/getPage` **完全相同**（约 67 个字段，含 `pageNum`/`pageSize`/`searchKey`/`startTime` 等查询参数）→ 疑似提取器把模块共享参数表错误作用到 add。
2. **缺字段**：真实请求的 `initDetailList` 在全库任何记录中都搜不到。

> 若只改 required 不动字段集：22 个必填标志落在污染列表上仍可生效（字段都在列表里）；
> 若一并收敛字段集：add 参数列表替换为真实请求的 35 个字段（补 `initDetailList`、剔除查询参数等），信息更准，但改动面更大。

## 3. 改动方案（按用户决策后执行）

- 直接更新 `data/app.db` 中 `/electricMeter/add` 记录的 `api_parameters`：
  - 决策 1 定 required 口径（非空值 22 个 / 全部 35 个）
  - 决策 2 定是否一并收敛字段集
- 不动其他接口、不动任何代码

## 4. 测试

- 更新后校验：记录 JSON 合法；required 集合符合决策结果；`initDetailList`（若收敛）存在
- 消费路径回归：`retrievers.py`/`dual_chroma.py` 读取该记录正常（构造 dict 无异常）

## 5. 风险 / 注意

- 若未来重新导入同源 YApi 文档，提取器会再次产生污染列表 → 长期根治需改提取器 scoping 逻辑，本次不涉及（除非用户要求一并处理）。
- 本次是纯数据修正，不改模型、不改提取代码、不影响其他 73 条接口记录。

## 6. 实施记录（2026-08-18 完成）

**用户决策**：必填口径 = 非空值 22 个；字段集不改。

**改动**：`data/app.db` → `documents` → `/electricMeter/add` 记录 `api_parameters`，14 个字段 `required: false → true`：

`meterDeviceType` / `meterDeviceManufacturerCode` / `meterDeviceManufacturerName` / `meterDeviceModelCode` / `meterDeviceModelName` / `useType` / `electricity` / `purpose` / `billingFactor` / `autoOff` / `mutualInductorOnOrOff` / `powerRate` / `payConfigCode` / `accessType`

（原 8 个必填 code/name/sceneCode/sceneName/level/whetherToCount/meterTypeCode/accessMethod 保持不动）

**验证结果**：
- required 集合 == 预期 22 个，无多无漏（diff extra/missing 均空）
- 字段总数保持 68（字段集未动）；六字段完整性（name/type/required/default/desc/value）全部满足
- 消费路径（SQLAlchemy `Document` + `retrievers.py`/`dual_chroma.py` 依赖的 key）读取无异常
- 不影响其他 73 条接口记录；未改任何代码

**未做**：字段集收敛（`initDetailList` 补入、查询参数剔除）——用户决策仅改 required；若未来需根治，需源 YApi 文档确认后改提取器 scoping。
