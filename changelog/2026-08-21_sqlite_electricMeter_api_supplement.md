# sqlite 补全：智慧用电绑定 4 个 API 的 URL 前缀 + 返回值语义

> 日期：2026-08-21
> 状态：**已写入并验证（2026-08-21）**
> 决策：用户授权将已确认部分直接补进 `data/app.db` → `documents`，仅限智慧用电绑定的 4 个 API（`/electricMeter/add|delete|getList|getParentList`）。依据 `2026-08-19_execute_wiselectric_32_diagnosis.md` §6/§7「已确认」事实。

## 写入内容（4 条记录）

| 记录 | api_url | api_returns 变更 |
|------|---------|------------------|
| `/electricMeter/add` | + `park-energy-electric-web/` 前缀 | retCode：desc「业务信封：成功=1，失败=0（非 HTTP 状态码）」+ value=`1`；msg：desc「成功时返回 success」+ value=`success` |
| `/electricMeter/delete` | 同上 | 同上 |
| `/electricMeter/getList` | 同上 | 同上 |
| `/electricMeter/getParentList` | 同上 | 同上 |

- 修改字段：`api_url`（补前缀）、`api_returns`（retCode/msg 的 desc + value）。
- 其余字段（api_parameters 等）不动；`data`/`queue` 返回字段未确认，不标注。

## 验证

```bash
# sqlite 直接查询：4 条 url 带前缀、retCode value=1
# _resolve_api_defs('', '', module_name='智慧用电') → 4 个，url 全带前缀，retCode value=1
```

Phase C 生成层将据此：
- url 直接产出完整路径（不再缺 `park-energy-electric-web/` 前缀）；
- 正向断言从接口返回定义取到 retCode=1（不再臆造 200/0）。

## 备份

`data/backup_electricMeter_api_20260821.json`（4 条受影响行更新前完整快照）。

## 未做 / 后续

- **ChromaDB（Phase B 快照）未同步**：Phase B 仍从 ChromaDB 读旧裸 url；需重建向量库或重灌该模块文档后 Phase B 才拿到前缀。
- 主数据 `value` 列（payConfig `zhyqP25529429`/scene `zhyqE29999415`/华立/DDS28）未写入——本次范围仅「接口返回值 + url 前缀」。
- 其余 9 条 electricMeter API（update/getPage/import/export 等）未动，仍缺前缀。
- delete 契约（body `{code}` → 400）、teardown add-vs-delete、add 字段集污染/缺 `initDetailList` 仍在待处理清单。
- 根治方向不变：Phase A 提取器存真实字段集/返回语义/主数据示例（2026-08-19 §7）。
