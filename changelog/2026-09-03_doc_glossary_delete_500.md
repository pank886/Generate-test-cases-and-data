# 2026-09-03 文档详情删除术语 500 修复（GET 术语漏 id）

> **触发**：前端报 `DELETE /api/docs/{doc_id}/glossary/NaN → 500`。
> **修复范围（用户决策：最小）**：GET 术语返回值补 `"id": t.id`。仅此一处后端改动。

## 一、根因

- 前端 `static/app.js` 删除按钮从 GET `/glossary` 渲染，`data-term-id` 直接取
  `t.id`（`app.js:829` axure 页块、产品 doc 术语同源渲染）。
- 但后端 `web/routes/docs.py get_doc_glossary` 返回值**漏了 `id` 字段**：
  `{"term", "definition", "notes", "kind"}`。
- `t.id` = `undefined` → `parseInt("undefined")` = `NaN` → 发 `DELETE .../glossary/NaN`
  → 后端 `delete_doc_glossary` 里 `int("NaN")` 抛 `ValueError` → 500。
- 即：**前端早已按含 `id` 的响应编写，后端漏出该字段**，属前后端契约缺口。

## 二、改动（最小集）

| 文件 | 变更 |
|------|------|
| `web/routes/docs.py` `get_doc_glossary` | terms 返回项补 `"id": t.id`（字段序：id 在前） |

无其他代码改动。删除路由本身无问题：`GlossaryOps.delete_term(session, int(term_id))`
在 `get_session_ctx` 块内，块退出自动 `commit()`（`database/__init__.py:56`），
`success=True` 即已持久化删除。

## 三、测试

新增 `tests/test_doc_glossary_delete.py`（2 例）：

1. `test_get_glossary_returns_id` — GET 术语必须含 `id`（前端删除依赖）。
2. `test_delete_glossary_by_id_succeeds` — 用 GET 返回的真实 id DELETE → success、
   术语消失、二次 GET 为空（覆盖原 500 场景全链路）。

**测试基建坑（记录）**：`test_ingest_main_flow.py` 同规约的 autouse
`_reset_singletons`（每测试重置 `database._ENGINE/_SESSION_LOCAL`）在新文件里
**必须同步**，否则 `_ENGINE` 缓存首个临时 SQLite 路径，多测试共享同一 DB、同 doc_id
互相污染 → 删除验证误报「术语仍在」（实为上一测试遗留行）。

## 四、验证结果

- `tests/test_doc_glossary_delete.py` → **2 passed**。
- 聚焦回归 `test_doc_glossary_delete + test_regression_import_smoke +
  test_regression_operations` → **43 passed**。
- 全量 `pytest tests/` → 720 passed；9 failed 集中在 `test_phase_bc_unit.py`
  （ResolveApiDefs / YAML 提示词消息 schema），与本改动无关——`test_phase_bc_unit.py`
  不 import 本次改动文件，属既有 Phase B/C 工作区在途问题（工作区已有未提交改动），
  不在此次范围。

## 五、波及面提示（原则③，供后续决策）

- `delete_doc_glossary` 仅按 `term_id` 删、忽略 `doc_id` 归属校验——当前前端保证
  doc_id 与术语匹配，非缺陷；如需服务端归属强校验可另行立项。
- 模块视图术语删除走独立路由（`/api/modules/.../glossary/{term}`，`app.js:1094`），
  不经本 GET，未受影响。
