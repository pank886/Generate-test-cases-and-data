# 四个大文件拆分方案

> 日期：2026-08-07
> 背景：全库扫描发现 33 个文件超 500 行，其中 4 个源码文件存在职责混杂/超大方法/超大 `__init__`，需要拆分以降低改动回归面。
> 依据：`changelog/2026-08-07_large_file_split_plan.md` 的前置统计见 `changelog/2026-08-07_*`（拆分必要性分析已在会话中确认）。

## 0. 拆分总览

| 文件 | 行数 | 拆分目标 | 优先级 |
|------|------|---------|--------|
| `ingest_v2.py` | 1272 | `ingest/` 包（5 模块）+ 顶层兼容层 | P0 |
| `agent_components/nodes.py` | 1036 | `llm_client.py` + `graph_logging.py` + prompt 构建外移 | P0 |
| `web/tasks.py` | 998 | `web/compensation.py` 独立 + 兼容 re-export | P1 |
| `agent_components/generators/__init__.py` | 986 | 拆子模块函数 + `__init__` 收敛为组合/代理 | P1 |

**统一兼容策略**：所有被外部（tests、web/routes、其他 agent_components 模块）直接引用的符号，一律保留在原模块路径做 `re-export`，**迁移阶段外部 import 零改动**。待稳定后再评估是否收拢测试 import。

---

## 1. `ingest_v2.py`（1272 行）→ `ingest/` 包

### 1.1 现状职责分布（已实测行区间）

| 职责 | 函数 | 约行数 |
|------|------|--------|
| API 定义合并 | `_merge_api_defs` | 31–73 |
| 文件文本提取 | `_extract_text` / `_extract_docx` / `_docx_img_dir` | 74–142 |
| 文档 ID 与路径 | `_safe_doc_id` / `_extract_valid_api_paths` | 143–198 |
| SQLite 持久化 | `_cascade_bind_to_module_docs` / `_delete_sqlite_doc` / `_save_to_sqlite` / `_save_single_chunk` / `_save_document_chunks` / `_delete_document_chunks` | 199–313 |
| 摘要/分块生成 | `_parse_chunk_summaries` / `_generate_batch_summaries` | 314–411 |
| 流程入口 ① | `process_product_doc` | 412–584 |
| 纯解析算法 | `extract_apis_from_yapi_md` | 585–806 |
| 分块 | `_split_text_by_headers` | 807–831 |
| 流程入口 ②③ | `process_api_doc`（已弃用） / `process_api_doc_extract` | 832–951 |
| 搜索文本构建 | `_build_api_search_text` / `_build_doc_search_text` | 952–1012 |
| 流程入口 ④⑤ | `commit_api_docs` / `process_axure_zip` | 1013–1255 |

### 1.2 目标结构

```
ingest_v2.py                 # 兼容层：仅 re-export，保留模块 docstring
ingest/
  __init__.py                # 包聚合导出
  extractors.py              # _extract_text / _extract_docx / _docx_img_dir / _safe_doc_id
  storage.py                 # SQLite 全量读写（_save_to_sqlite 等 6 个）
  api_parser.py              # extract_apis_from_yapi_md / _extract_valid_api_paths / _merge_api_defs（纯算法，零外部依赖）
  chunking.py                # _split_text_by_headers / _parse_chunk_summaries / _generate_batch_summaries / _build_*_search_text
  pipelines.py               # 5 个流程入口 + 摘要编排调用链
```

### 1.3 依赖关系（已实测）

- `api_parser.py` 仅依赖 `re`，可最先迁移、独立单测。
- `extractors.py` 依赖 `docx`、`config`，无 LLM 依赖。
- `storage.py` 依赖 `config` / `dual_chroma`（chroma 清理部分）。
- `chunking.py` 依赖 `RecursiveCharacterTextSplitter`（langchain）。
- `pipelines.py` 依赖 `ChatTestAgentGraph`（来自 `nodes.py`）、`extraction_prompts`、上述全部子模块。

### 1.4 迁移步骤

1. 新建 `ingest/` 包，按表迁移纯函数（先 `api_parser` → `extractors` → `storage` → `chunking`）。
2. 迁移 `pipelines.py`，内部 import 改为 `from ingest.storage import ...`。
3. 重写 `ingest_v2.py` 为兼容层：

   ```python
   """Phase A: 智能文档处理入口（兼容层，实现已迁移至 ingest/ 包）"""
   from ingest.api_parser import _merge_api_defs, extract_apis_from_yapi_md, ...
   from ingest.extractors import _extract_text, _extract_docx, ...
   from ingest.storage import _save_to_sqlite, ...
   from ingest.chunking import _split_text_by_headers, ...
   from ingest.pipelines import process_product_doc, process_api_doc, ...
   ```

4. 跑全量测试验证（尤其 `tests/test_regression_extraction.py`、`tests/test_ingest_main_flow.py`、`tests/test_doc_binding.py`、`tests/test_delete_file.py`，它们 `from ingest_v2 import _private`）。

### 1.5 风险点

- **私有函数被测试直接 import**（`_save_to_sqlite`、`_safe_doc_id`、`_extract_text` 等）：兼容层必须逐符号 re-export，迁移期间不得删改符号名。
- `process_api_doc` 已弃用但仍被 `process_api_doc_extract` 链调用，保留其行为（告警日志）。
- 顶层 `ingest_v2.py` 与包同名 `ingest` 无冲突（不同标识符）。

### 1.6 执行记录（2026-08-07，已完成）

**测试 patch 目标迁移**：拆分后 8 个测试失败，根因是测试通过 `mock.patch("ingest_v2.X")` 打桩，而拆分后函数体在 `ingest.pipelines` 命名空间解析符号，patch 不再传播。

已修改（仅测试文件，生产代码零 import 变更）：
- `tests/test_ingest_main_flow.py`：`@patch("ingest_v2.ChatTestAgentGraph")` → `ingest.pipelines.ChatTestAgentGraph`；`@patch("ingest_v2.get_chroma_db")` → `ingest.pipelines.get_chroma_db`；`patch("ingest_v2._save_to_sqlite")` → `ingest.pipelines._save_to_sqlite`。
- `tests/test_commit_api.py`：`import ingest_v2 as _ing` → `import ingest.pipelines as _ing`（`_ing.get_chroma_db = lambda...` 赋值随之命中 commit 路径）。

> 说明：`mock.patch` 目标是**实现位置**而非对外 API；`from ingest_v2 import X` 语句（生产代码与测试均未改动）依然可用，兼容层 re-export 完整。`web/routes/bindings.py` 等从 `ingest_v2` 模块级 import `_cascade_bind_to_module_docs` 的调用方不受影响。

---

## 2. `agent_components/nodes.py`（1036 行）→ 三类逻辑外移

### 2.1 现状职责分布

| 职责 | 成员 | 约行数 |
|------|------|--------|
| LLM 单例管理 | 模块级 `reload_llm` / `_get_llm` | 56–76 |
| LLM 调用封装 | `_invoke_think` / `_invoke_structured` / `_load_factory_methods` | 921–1036 |
| 图日志 | `_serialize_for_log` / `_log_node_output` / `_cleanup_logs` / `_split_thinking_sections` | 737–921 |
| 提示词构建 | `_prepare_plan_prompt_vars` / `_generate_excel_plan_thinking`（101–202 中的 prompt 部分） | ~100 |
| 主节点逻辑 | `_generate_excel_plan_node`（含校验、修复循环、Excel 写入、资源冲突） | **202–667（465 行）** |
| 资源冲突解决 | `_resolve_resource_conflicts` | 667–737 |

### 2.2 关键事实（已实测）

- `reload_llm` / `_get_llm` 被 `web/tasks.py`（L201/502/880）、`web/routes/chat.py`（L87）、`tests/test_regression_import_smoke.py` 直接 import。
- `_generate_excel_plan_node` 内部调用 `self.prompt_factory`、`self._invoke_structured`、`self._resolve_resource_conflicts`、`self._serialize_for_log`、`self._cleanup_logs` —— 说明 LLM/日志逻辑已可解耦为独立依赖。
- `ChatTestAgentGraph` 是 `RetrievalMixin` + `GenerationMixin` 的组合类，拆分后**保持类名与继承关系不变**。

### 2.3 目标结构

```
agent_components/
  llm_client.py            # reload_llm / _get_llm（原样迁移）+ 通用调用封装（供 nodes/补偿 worker 复用）
  graph_logging.py         # _serialize_for_log / _log_node_output / _cleanup_logs / _split_thinking_sections（纯静态工具）
  prompt_builder.py        # _prepare_plan_prompt_vars / excel_plan thinking 提示词构建
  nodes.py                 # 保留 ChatTestAgentGraph 类（主节点逻辑仍在此，但 prompt/LLM/日志已外移）
```

### 2.4 迁移步骤

1. `llm_client.py`：迁移 `reload_llm` / `_get_llm`（保持模块级单例语义，`threading` 锁逻辑原样）。
2. `graph_logging.py`：迁移 4 个静态日志方法为模块级函数，`nodes.py` 内改为 `from .graph_logging import ...` 或类内薄封装。
3. `prompt_builder.py`：迁移 `_prepare_plan_prompt_vars`；`_generate_excel_plan_thinking` 的 prompt 拼装段外移，保留节点逻辑骨架。
4. `nodes.py` 顶部追加 re-export，保证既有引用不断：

   ```python
   from agent_components.llm_client import reload_llm, _get_llm
   ```

5. 跑 `tests/test_phase_bc_unit.py`、`tests/test_regression_import_smoke.py`、`tests/test_new_node_evaluation.py` 及 `web/routes/chat.py` 路径。

### 2.5 风险点

- **`reload_llm` 热重载语义**：是全局单例重置，迁移到新模块后必须保证 `nodes`、`web.tasks`、`web.routes` 引用的是同一实例状态，建议 `llm_client` 持有 `_llm_singleton`，`nodes.py` 仅转发。
- `_generate_excel_plan_node`（465 行）**阶段一不强行再拆**，拆 LLM/日志/提示词后它已降至纯业务逻辑；若后续仍超 300 行，再按「校验→修复循环→Excel 落盘」拆内部辅助方法。
- `METHOD_FEATURES` 字典被 prompt 构建使用，随 `prompt_builder.py` 或保留在 `nodes.py`，取后者（避免循环引用）。

### 2.6 执行记录（2026-08-07，已完成）

新建 3 个模块（实现整体迁移，逻辑零改动）：
- `agent_components/llm_client.py`：`_llm_instance` / `_llm_lock` / `reload_llm` / `_get_llm` + 通用调用封装 `invoke_think` / `invoke_structured` / `load_factory_methods`（`invoke_structured` 以参数接收 `llm` 与 `method_features`，与 `nodes.py` 解耦）。
- `agent_components/graph_logging.py`：`split_thinking_sections` / `serialize_for_log`（自递归）/ `cleanup_logs` / `log_node_output`（改收 `host` 承载 `_run_data`/`_run_timestamp`）。
- `agent_components/prompt_builder.py`：`prepare_plan_prompt_vars`（改收 `host`）。

`nodes.py`（1036 → 759 行）：
- 删除 51–75、736–918、920–1036 原实现，类内薄转发 8 个方法（`_split_thinking_sections` / `_prepare_plan_prompt_vars` / `_serialize_for_log` / `_log_node_output` / `_cleanup_logs` / `_load_factory_methods` / `_invoke_think` / `_invoke_structured`），**签名参数名与默认值不变**（测试 mock `ChatTestAgentGraph._invoke_structured` 目标仍有效）。
- 顶部 re-export `reload_llm` / `_get_llm`（`from agent_components.llm_client import ...`），既有 `from agent_components.nodes import reload_llm, _get_llm` 用法不变。
- `METHOD_FEATURES` 保留在 `nodes.py`，`_invoke_structured` 薄转发时作为参数传入。
- 清理 import：移除 `threading` / `re` / `openai` / `pydantic.BaseModel`+`ValidationError` / `langchain OutputParserException` / `DeepSeekChatOpenAI` / `Callable` / `Type` / `yaml` / 顶部 `datetime`（保留 `Optional` 与函数内 `datetime`）。

验证：热重载语义（`reload_llm is llm_client.reload_llm`，清空 `llm_client` 单例）、`_serialize_for_log` 递归、薄转发签名兼容均通过；`tests/test_regression_import_smoke.py` + `tests/test_phase_bc_unit.py` + `tests/test_new_node_evaluation.py` 118 passed。

> 行数说明：`nodes.py` 759 行，主节点 `_generate_excel_plan_node`（~465 行）仍为核心业务逻辑，无外部可拆依赖，按 §5 验收标准 #3 记录例外。

---

## 3. `web/tasks.py`（998 行）→ 补偿 worker 独立

### 3.1 现状职责分布

| 职责 | 成员 | 约行数 |
|------|------|--------|
| 线程池 | `_BoundedThreadPoolExecutor` / `_MAX_WORKERS` / `_executor` | 20–42 |
| 文件处理任务 | `_process_file_bg` | 43–161 |
| 工具 | `_resolve_api_defs` | 162–185 |
| 计划确认任务 | `_confirm_plan_bg` | 186–370 |
| 恢复任务 | `_resume_workflow_bg` | 371–492 |
| 场景分析任务 | `_analyze_module_scenarios_3step_bg`（260 行） / `_analyze_module_scenarios_bg` | 493–759 |
| API 提交任务 | `_commit_apis_bg` | 760–809 |
| **补偿 worker（独立状态机）** | `_start_compensation_worker` / `_stop_compensation_worker` / `_compensation_loop` / `_process_pending_compensation` / `_compensate_simple_summary` / `_compensate_chroma_rebuild` / `_compensate_api_search_text` | **810–998（188 行）** |

### 3.2 关键事实（已实测）

- `web/app.py` 直接 import：`_start_compensation_worker`（L314）、`_executor` / `_stop_compensation_worker`（L321）、`_analyze_module_scenarios_bg`（L399）。
- `web/routes/*` 直接 import：`_process_file_bg`、`_confirm_plan_bg`、`_resume_workflow_bg`、`_commit_apis_bg`。
- `tests/test_phase_bc_unit.py` import `_resolve_api_defs`；`generators/__init__.py` import `_BoundedThreadPoolExecutor`。
- 补偿 worker 依赖：`_get_llm`（nodes）、`get_chroma_db`、`config.COMPENSATION_POLL_INTERVAL`、`batch_chunk_summary_prompt`、DB session（DocumentChunk 等）。

### 3.3 目标结构

```
web/
  tasks.py            # 保留线程池 + 全部 _bg 任务编排，末尾 re-export 补偿函数
  compensation.py     # 新增：_start/_stop_compensation_worker / _compensation_loop / _process_pending_compensation / _compensate_*
```

### 3.4 迁移步骤

1. 新建 `web/compensation.py`，整体搬运 810–998 行（含 worker 全局变量 `_compensation_thread` / 停止标志）。
2. 调整内部 import：`from agent_components.nodes import _get_llm` 改为直接 `from agent_components.llm_client import _get_llm`（若阶段按 §2 已拆 llm_client；否则保持 nodes）。
3. `web/tasks.py` 末尾追加：

   ```python
   from web.compensation import (
       _start_compensation_worker, _stop_compensation_worker,
   )
   ```

   （`_executor` 仍在 tasks.py 原位，app.py 的 `from web.tasks import _executor` 不变。）
4. 验证：`web/app.py` 生命周期（startup 启动 / shutdown 停止）行为不变，跑 `tests/test_phase_bc_unit.py`。

### 3.5 风险点

- 补偿 worker 是**常驻线程**，迁移时 `_start_compensation_worker` / `_stop_compensation_worker` 必须保持幂等（重复调用不重复起线程）。
- 三个 `_compensate_*` 函数直接操作 DB session 与 chroma，迁移时**不改任何逻辑**，仅移动位置。
- `app.py` 若从 `web.tasks` 和 `web.compensation` 两处 import 同一符号，确保 re-export 指向同一对象（模块单例）。

### 3.6 执行记录（2026-08-07，已完成）

- 新建 `web/compensation.py`：整体搬运 802–998 行（`_compensation_stop` / `_compensation_thread` 全局变量 + 7 个函数），逻辑零改动；顶部补 `import config as _config`（原依赖 tasks.py 模块级绑定）。
- `web/tasks.py`：截断至 799 行，删除未使用的 `import time`，末尾追加 `from web.compensation import ...` re-export。
- 验证：符号身份断言（`web.tasks._start_compensation_worker is web.compensation._start_compensation_worker`）通过；worker 起停幂等性通过；`tests/test_phase_bc_unit.py` + `tests/test_regression_import_smoke.py` 118 passed。

---

## 4. `agent_components/generators/__init__.py`（986 行）→ 子模块 + 组合 Mixin

### 4.1 现状职责分布（GenerationMixin 内，已实测）

| 职责 | 方法 | 约行数 |
|------|------|--------|
| Excel 依赖图/读行 | `_generate_dependency_map` / `_read_excel_rows` / `_read_shared_preconditions` | 29–202 |
| 翻译 | `_sanitize_en` / `_load_translation_cache` / `_save_translation_cache` / `_pinyin_fallback` / `_translate_to_en` | 202–326 |
| pytest 导出 | `_takeover_export_assertions` / `_parse_assertion` / `_generate_py_file` | 326–520 |
| YAML 生成 | `_generate_one_yaml` / `_generate_all_yamls` / `_run_yaml_rounds` | 520–986 |

> 注：`generators/` 已是包目录，且已自拆过 `_helpers.py`（4 个辅助函数），`__init__` 当前从 `_helpers` 导入并 re-export。本次延续该模式。

### 4.2 关键事实（已实测）

- 外部引用全部走 `from agent_components.generators import GenerationMixin`（`nodes.py`、`tests/test_yaml_db_export.py`、`tests/test_phase_bc_unit.py` 等）；`GenerationMixin` 被 `ChatTestAgentGraph` 继承，**类名必须保留**。
- `tests/test_regression_generators_helpers.py` 从包顶层 import `_summarize_error_patterns` 等 helpers → `__init__` 的 re-export 角色需保留。
- `_run_yaml_rounds` 内部使用 `web.tasks._BoundedThreadPoolExecutor` 做并发，迁移时该 import 需照搬。

### 4.3 目标结构

```
agent_components/generators/
  __init__.py        # 收敛为：组合类定义 + 全部 re-export（helpers + 各子模块符号）
  _helpers.py        # 已存在，不动
  excel.py           # _read_excel_rows / _read_shared_preconditions / _generate_dependency_map
  translation.py     # _sanitize_en / _pinyin_fallback / 缓存读写 / _translate_to_en
  py_export.py       # _takeover_export_assertions / _parse_assertion / _generate_py_file
  yaml_gen.py        # _generate_one_yaml / _generate_all_yamls / _run_yaml_rounds
```

### 4.4 迁移步骤

1. 将无状态方法迁移为各子模块**模块级函数**（读 Excel / 翻译 / pytest 生成逻辑本身不依赖 self 状态，仅用到 `config`、`openpyxl`、`TestData/TranslationResult`）。
2. `__init__.py` 中的 `GenerationMixin` 收敛为**薄代理**：方法体改为调用子模块函数；或改为多继承组合 `class GenerationMixin(ExcelMixin, TranslationMixin, PyExportMixin, YamlMixin)`（各子模块定义 Mixin，方法名不变）。

   > 推荐方案：**多继承组合**。子模块各自定义同名的 Mixin 类，`__init__.py` 中 `class GenerationMixin(ExcelMixin, TranslationMixin, PyExportMixin, YamlMixin)`，`nodes.py` 的 `from agent_components.generators import GenerationMixin` 与 `class ChatTestAgentGraph(RetrievalMixin, GenerationMixin)` **完全不变**。
3. `__init__.py` 保留现有 helpers re-export，并补子模块 re-export（如 `_summarize_error_patterns` 等测试引用）。
4. 跑 `tests/test_yaml_db_export.py`、`tests/test_phase_bc_unit.py`、`tests/test_regression_generators_helpers.py`。

### 4.5 风险点

- **MRO / 方法名冲突**：四类方法名互不重叠（已核对），多继承组合安全；但需确认 `_generate_all_yamls` 调 `_generate_one_yaml`、`_run_yaml_rounds` 调 `_generate_all_yamls` 的链在多继承下仍走 Mixin 内同名方法（推荐子模块 Mixin 方法体直接调用同模块函数，避免跨 Mixin 隐式耦合）。
- `_translate_to_en`（~84 行）使用缓存文件 + pinyin 回退，逻辑自洽，整体搬移即可。
- 若某方法确实依赖 `self` 其他方法（如 `_generate_dependency_map` 调 `self._read_excel_rows`），同模块内 Mixin 可直接 `self._read_excel_rows(...)` 调用，无需改签名。

---

## 5. 统一验收标准

1. **行为等价**：全量 `pytest` 通过，重点回归清单：
   - ingest：`test_regression_extraction.py`、`test_ingest_main_flow.py`、`test_doc_binding.py`、`test_delete_file.py`
   - nodes：`test_phase_bc_unit.py`、`test_regression_import_smoke.py`、`test_new_node_evaluation.py`
   - tasks：`test_phase_bc_unit.py`（`_resolve_api_defs`）
   - generators：`test_yaml_db_export.py`、`test_regression_generators_helpers.py`
2. **零外部 import 变更**：迁移阶段除新增子模块外，不允许改任何既有 `from X import Y` 语句（兼容层 re-export 兜底）。
3. **行数达标**：拆分后各源码文件 < 500 行（`nodes.py` 若主节点逻辑仍超 500 则保留，因已无外部可拆依赖，记录例外）。
4. **无死代码**：`grep` 确认无未 re-export 的孤儿函数；迁移后删除原文件中的旧定义，不保留两份。
5. **热重载语义保留**：`reload_llm` 全项目指向同一单例；补偿 worker 起停幂等。

## 6. 建议执行顺序

1. P0-1 `generators`（纯搬移，风险最低，验证 Mixin 组合模式）
2. P0-2 `ingest_v2`（职责边界最清晰，收益最大）
3. P1 `web/tasks` → `compensation.py`
4. P0-3 `nodes`（涉及全局单例与 465 行主节点，放最后，依赖前三步的模式成熟后执行）
