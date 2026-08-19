# Phase C YAML 生成喂空 body 接口定义 → 字段瞎编 修复方案

> 日期：2026-08-18
> 状态：**✅ 已按此方案实施完成**（用户决策：Phase C 直接用 SQL 读全部接口详情，不从 ChromaDB / 快照）
> 背景：YAML 生成的 `/electricMeter/add` 用例字段写成 `meterName`/`meterNo`/`meterType`（真实字段 `name`/`code`/`meterDeviceType`），断言写 `$.message`（真实返回 `msg`）。

## 0. 根因（已实测验证）

**Phase C 拿到的接口定义 body 是空的，LLM 无字段名可抄 → 只能按业务语义瞎猜。**

| 环节 | 实际行为 |
|------|---------|
| 前端 `/confirm-plan` | `static/app.js:1192` 只传 `excel_path`，`api_defs_json` 恒为 `""` |
| `web/tasks._resolve_api_defs`（旧） | 显式入参为空 → 回退读 excel 同级 `api_defs.json` 快照 |
| 快照来源 | Phase B 落盘：`api_full_for_snapshot` ← `state["api_definitions"]` ← `retrievers._search_api_defs`（ChromaDB 检索） |
| ChromaDB 检索返回 | **只有 name/method/url + `raw` 自然语言检索文本**（`retrievers.py:284-289`），body/return 结构上就丢失了；仅 SQLite 即时补偿路径（`retrievers.py:330-332`）才带 body |
| 实测快照 | `园区基线/智慧用电_31/api_defs.json`：4 个接口 **body/return 全空**；`智慧用电_30` 的 72 接口快照同样全空 |
| YAML 生成 | LLM 分析明确写「接口定义中body为空，说明没有明确的字段定义…只能合理猜测」（thinking_trace 23290 行） |

**结论**：不是模型瞎写，是 Phase C 的输入（ChromaDB 检索快照）结构性缺 body/return。

## 1. 改动（最小，1 处 + 测试）

**`web/tasks.py` → `_resolve_api_defs`**：不再读快照，直接用 SQL 从 `documents` 表读**全部**接口详情。

- 新增 `_load_all_api_defs()`：`session.query(Document).filter_by(doc_type="api")`，组装 name/url/method/description/header/body/return/annotations
- annotations 取 DB 列（已含 `is_export`/`has_path_params` 持久化），再跑一次 `ApiAnnotationRegistry.apply_all`（幂等、不覆盖人工编辑）
- 优先级：显式入参（非空且非 `[]`，信任原样）> SQL 全部接口详情 > `None`（M8 阻断）
- 显式入参路径、Phase B 快照落盘、前端传参、ChromaDB 检索层**均不改**
- `_generate_dependency_map` / `_generate_all_yamls` 共用此 api_defs_json，全部拿到详情

## 2. 测试用例

| 用例 | 断言 |
|------|------|
| 显式入参优先 | 返回原样，不查 SQL |
| 空入参读 SQL 全部详情 | 解析后 ≥60 接口；`/electricMeter/add` body ≥60 字段，含 `name`/`code`/`meterDeviceType`/`meterTypeCode`；return ≥1 |
| 入参 `"[]"` 视为缺失 | 继续读 SQL，含 `/electricMeter/add` |
| annotations 从 SQL 带出 | `/getEle/{code}` 有 `has_path_params`；`/importTemplate` 有 `is_export` |
| SQL 无接口定义 | monkeypatch `_load_all_api_defs` → `None` 阻断（M8） |

## 3. 明确不改

- **不改** ChromaDB 存储 / `_search_api_defs`（Phase A/B 语义检索仍用它）
- **不改** Phase B prompt / 生成逻辑 / 快照落盘（保留为参考工件）
- **不改** 前端 `/confirm-plan`、`api_defs.json` 快照写入

## 4. 实施记录（2026-08-18 完成）

**改动文件**：

| 文件 | 改动 |
|------|------|
| `web/tasks.py` | `_resolve_api_defs` 改为：显式入参 > SQL 全部接口详情 > None；新增 `_load_all_api_defs()`（documents 表 + `ApiAnnotationRegistry.apply_all`） |
| `tests/test_phase_bc_unit.py` | `TestResolveApiDefs` 重写（5 用例：入参优先 / SQL 全量详情 / `[]` 回退 / annotations / 空库阻断） |

**验证结果**：
- `TestResolveApiDefs`：5/5 通过
- 回归：`test_phase_bc_unit.py` + `test_phase_c_api.py` + `test_phase_a_analysis.py` = **160 passed, 2 skipped, 1 xfailed**
- 实时验证：`_resolve_api_defs("", "")` 返回 72 个接口；`/electricMeter/add` body 68 字段含 `name`/`code`/`meterDeviceType`/`meterTypeCode`、22 必填、return `retCode/msg/data/queue`；渲染 `format_yaml_data_prompt` 后真实字段名已进 prompt

**操作注意（已与用户确认接受）**：全量 72 接口详情使每次 YAML 调用 prompt 约 275k 字符（典型单 story 场景只有几个接口，无压力；整模块场景输入较大）。后续可按 excel 实际引用接口收敛范围（dependency_map 为数据源），本次不做。
