# Phase B/C 接口范围收敛：模块绑定 + 关联模块绑定（B 拿快照 / C 拿详情）

> 日期：2026-08-20
> 状态：**已实现，验证通过（2026-08-20）**
> 决策：Phase B 与 Phase C 只消费「测试模块绑定 + 模块关联模块绑定」的接口定义；B 用快照（接口概要），C 用 SQL 详情；关联模块的发现后续在此增加语义检索（本次为绑定图三路召回）。

## 背景

- 用户发现：Phase C YAML 生成拿到全部 72 个接口详情，而测试模块「智慧用电」绑定仅 4 个 API（`/electricMeter/add` `/electricMeter/delete` `/electricMeter/getList` `/electricMeter/getParentList`）。
- 根因（2026-08-18 变更）：`_resolve_api_defs` 为修复 ChromaDB 快照缺 body/return 导致的字段瞎编，改为直接读 SQL `documents` 表**全部**接口详情，未按模块收敛。
- 用户决策（2026-08-20）：
  1. **B、C 两个流程只拿到「测试模块绑定 + 模块关联模块绑定」的接口**；关联模块发现后续增加语义检索。
  2. **B 拿快照**（概要 name/method/url/description），**C 拿详情**（body/return/header 全字段）。

## 现状核实（DB + 代码）

- **Phase B 已满足，不改**：`_retrieve_related_data` 用 `search_modules = {模块} ∪ 关联模块 ∪ 公共基础服务(存在时)`，ChromaDB `search_api_defs` 的 doc_ids 过滤生效（`dual_chroma.py:127-138`），返回接口概要（快照形式）。
- **Phase C 是唯一缺口**：`_resolve_api_defs`（`web/tasks.py:161`）显式入参为空 → `_load_all_api_defs()` 读全部 72 个接口详情。
- DB 事实：智慧用电绑定 4 个 API；绑定图推导关联模块为空（module↔module 无、product/axure/api→module 均指向自身）；「公共基础服务」模块不存在 → Phase B 的 COMMON_SERVICE_MODULE 分支不触发。智慧用电正确 scope = 4 个 API。
- 图顺序：`extract_related_modules` → `retrieve_related_data` → `generate_plan_thinking` → `generate_excel_plan` → END；写节点（`nodes.py:632-641`）在关联模块提取之后，`state["confirmed_module"]`/`state["related_modules"]` 可用。

## 方案摘要（6 文件）

1. **`database/operations/bindings.py`**：新增 `discover_related_modules(session, module_name) -> list[str]`——抽取 `retrievers._extract_related_modules` 三路召回（module↔module + product/axure/api→module，跳过自身，去重排序）。**后续「语义检索」增强的挂钩点**。
2. **`agent_components/retrievers.py`**：`_extract_related_modules` 委托 `discover_related_modules`（保留日志与返回结构）。
3. **`web/tasks.py`**：
   - `_load_all_api_defs(doc_ids=None)` 加可选过滤（None = 全部，向后兼容）。
   - 新增 `_load_api_defs_scoped(module_name)`：scope = `{模块} ∪ discover_related_modules ∪ 公共基础服务(存在时)`；`get_bound_docs` 收集 api doc_ids → 详情加载。
   - 新增 `_read_module_scope(excel_path)`：读计划目录 `module_scope.json` 的 module。
   - `_resolve_api_defs(excel_path, api_defs_json="", module_name="")`：显式入参 > 作用域详情 > 空作用域回退全部 > None(M8)。
   - `_confirm_plan_bg(..., module_name="")`：透传。
4. **`web/routes/chat.py`**：`/confirm-plan` 增加 `module_name` Form 字段；修复位置参数 bug（`add_task` 第 3 个位置参数把 user_ctx 误传进 `api_defs_json` 槽位，前端恒空串无实害）。
5. **`agent_components/nodes.py`**：api_defs.json 写入块旁，向同一目录写 `module_scope.json`（`{"module", "related_modules"}`）——B→C 作用域契约（M8 产物传递）。
6. **`tests/test_phase_bc_unit.py`**：`TestResolveApiDefs` 新增作用域 / module_scope.json 读取 / 空作用域回退 / 关联模块纳入；新增 `discover_related_modules` 单测。

## 不做

- 不改 Phase B 检索 / prompt / 快照（已满足「B 拿快照」）。
- 不改 ChromaDB 存储 / 语义检索；关联模块语义检索后续在 `discover_related_modules` 挂钩点扩展。
- 不做 excel 引用接口收敛（dependency_map 粒度，2026-08-18 预告的另一方向）。

## 验证

```bash
python -m pytest "tests/test_phase_bc_unit.py::TestResolveApiDefs" "tests/test_phase_bc_unit.py::TestDiscoverRelatedModules" -q
python -m pytest tests/test_phase_bc_unit.py tests/test_phase_c_api.py tests/test_phase_a_analysis.py -q
python -c "from web.tasks import _resolve_api_defs; import json; d=json.loads(_resolve_api_defs('', '', module_name='智慧用电')); print(len(d), [a['url'] for a in d])"  # 期望 4，非 72
```

## 风险

- 作用域收敛后 YAML prompt 接口更少 → 依赖其他模块公共接口的用例可能缺定义 → 空作用域回退全部兜底；dependency_map 仍按 excel 引用过滤。
- 旧计划目录无 `module_scope.json` → 回退全部（与现状一致，不回归）；重新跑 Phase B 后生效。
- 位置参数修复改变 /confirm-plan 传参语义（现传空串无实害）；`test_phase_c_api.py` 走 HTTP 端点不受影响。

## 实施记录（2026-08-20）

**改动文件**（6 处）：

| 文件 | 改动 |
|------|------|
| `database/operations/bindings.py` | 新增 `discover_related_modules(session, module_name)`（三路召回共享函数，语义检索挂钩点） |
| `agent_components/retrievers.py` | `_extract_related_modules` 委托共享函数（保留日志与返回结构） |
| `web/tasks.py` | `_load_all_api_defs(doc_ids=None)` 加过滤；新增 `_load_api_defs_scoped` / `_read_module_scope`；`_resolve_api_defs(..., module_name="")` 作用域优先级；`_confirm_plan_bg` 透传 |
| `web/routes/chat.py` | `/confirm-plan` 加 `module_name` Form 字段；修复位置参数错位（`user_ctx` 误传进 `api_defs_json` 槽位 → 改关键字传参） |
| `agent_components/nodes.py` | api_defs.json 旁落盘 `module_scope.json`（module + related_modules，B→C 作用域契约） |
| `tests/test_phase_bc_unit.py` | `TestResolveApiDefs` +4（作用域 / module_scope.json / 空作用域回退 / 关联模块纳入）；新增 `TestDiscoverRelatedModules` ×3 |

**验证结果**：

- 定向（TestResolveApiDefs + TestDiscoverRelatedModules）：**12 passed**。
- 回归（test_phase_bc_unit + test_phase_c_api + test_phase_a_analysis）：**177 passed, 2 skipped, 1 xfailed**。
- 附加（test_yaml_db_export / test_yaml_ref_check / test_thinking_log / test_llm_adapter）：**38 passed**。
- 实时：`_resolve_api_defs("", "", module_name="智慧用电")` → **4 个**接口（`/electricMeter/add|delete|getList|getParentList`），`/electricMeter/add` body 68 字段详情完整；空入参 → 72（向后兼容）；不存在的模块 → 回退 72；`module_scope.json` 读取生效（4 个）。
- 2 条既有 PytestCollectionWarning（pydantic 模型 TestCase/TestCaseRow 命名）与本次无关。
