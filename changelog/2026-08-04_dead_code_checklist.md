# 全库死代码与未使用数据检查清单（2026-08-04）

## 基本信息

| 项目 | 内容 |
|:---|:---|
| 检查日期 | 2026-08-04 |
| 检查范围 | agent_components / database / web / prompts / data_factory / ingest_v2.py 等全部源码（排除 .venv / tests / logs / scripts） |
| 检查方法 | AST 全量函数定义与引用分析 → grep 逐条核实（LangGraph 节点、FastAPI 路由、pydantic validator、SQLAlchemy event 均已排除）→ pyflakes 补查未用变量/import |

---

## 一、完全未被引用的方法（真死代码，可直接删除）

| 方法 | 位置 | 说明 |
|:---|:---|:---|
| `ChatTestAgentGraph._finalize_excel_plan` | `agent_components/nodes.py:101` | 全库无任何调用，连注释都自认是「死代码」 |
| `ChatTestAgentGraph._validate_excel_plan` | `agent_components/nodes.py:720` | 用例校验逻辑已被 `ExcelPlanValidator.validate` 取代 |
| `validate_py_file` | `agent_components/validator.py:84` | 校验器模块只使用了 `validate_excel_file` |
| `DEEP_API_KEY` | `config.py:47` | 一个返回字符串的函数，无任何调用 |
| `drop_db` | `database/__init__.py:151` | 删除整个数据库的工具函数，无入口挂载 |
| `delete_bindings_for_module` | `database/operations/bindings.py:153` | 删除某模块所有绑定，未被调用（模块删除走的是 `delete_bindings_for_doc`） |
| `delete_bindings_between_docs` | `database/operations/bindings.py:163` | 删除文档之间绑定，未被调用 |
| `delete_completed` | `database/operations/compensation.py:80` | 清理已完成/失败补偿任务，补偿 worker 未启用它 |
| `detect_all` | `agent_components/api_annotations.py:74` | 单次跑全部异常检测器，已被 `apply_all`（检测+写入）取代 |
| `_find_file_recursive` | `agent_components/axure_parser.py:235` | 递归按文件名找文件，内部辅助，无调用 |
| `_path_components_match` | `agent_components/axure_parser.py:256` | 路径组件比对，内部辅助，无调用 |
| `ValidationInterceptor.get_summary` | `prompts/response_model.py:53` | classmethod，无调用 |
| `Binding.make` | `database/models.py:145` | 静态工厂方法，无调用 |
| `TestPointList` | `prompts/response_model.py:920` | Pydantic 模型，全库未使用 |
| `analyze_data_deps_prompt` | `prompts/extraction_prompts.py:37` | 旧版「数据依赖分析」prompt，无调用 |
| `generate_data_plan_prompt` | `prompts/extraction_prompts.py:60` | 旧版「场景级数据规划」prompt，无调用 |
| `PromptFactory.format_test_points` | `prompts/definitions.py:266` | 旧版「格式化测试点为 JSON」prompt，无调用 |
| `PromptFactory.generate_dependency_map` | `prompts/definitions.py:318` | 空壳：内部直接调 `generate_dependency_map_prompt`，壳本身无消费者 |
| `PromptFactory.analyze_module_scenarios` | `prompts/definitions.py:326` | 空壳，无消费者 |
| `PromptFactory.format_module_scenarios` | `prompts/definitions.py:334` | 空壳，无消费者 |
| `analyze_module_scenarios_prompt` | `prompts/extraction_prompts.py:507` | 仅被死壳 `PromptFactory.analyze_module_scenarios` 引用，整条链无人调用 |
| `format_module_scenarios_prompt` | `prompts/extraction_prompts.py:543` | 仅被死壳 `PromptFactory.format_module_scenarios` 引用，整条链无人调用 |

---

## 二、仅测试引用（生产链路无消费者，删除前需同步处理测试）

| 方法 | 位置 | 说明 |
|:---|:---|:---|
| `ChatTestAgentGraph._find_pre` | `agent_components/nodes.py:751` | 仅 `tests/test_phase_bc_unit.py` 调用 |
| `ApiAnnotationRegistry.get_type` | `agent_components/api_annotations.py:62` | 仅测试调用 |
| `ApiAnnotationRegistry.has_any` | `agent_components/api_annotations.py:134` | 仅测试调用 |
| `DualChromaDB.search_context` | `agent_components/dual_chroma.py:194` | 仅测试 mock |
| `DualChromaDB.get_doc_chunks` | `agent_components/dual_chroma.py:147` | 仅测试 mock |
| `reset_cache` | `data_factory/registry.py:27` | 仅测试用于清缓存 |
| `DocOps.add_document` | `database/operations/docs.py:15` | 生产实际用裸 `Document(...)` + `session.merge()` 写入（`ingest_v2.py:237`） |
| `DocOps.update_document` | `database/operations/docs.py:56` | 仅测试 |
| `ModuleOps.get_by_id` | `database/operations/modules.py:29` | 仅测试 |
| `DataPlan` | `prompts/response_model.py:897` | 仅测试 import-smoke |
| `DecisionStep` | `prompts/response_model.py:984` | 仅测试 import-smoke |

> **注意**：`ApiAnnotationRegistry.is_active`、`observability.JSONFormatter.format`（logging 框架回调）、全部 `__repr__`、`_set_pragma`（SQLAlchemy event）、pydantic 的 `@model_validator`、FastAPI 路由函数均已确认是**有效使用**，不在清单内。

---

## 三、产出了数据但未使用的代码路径

| 位置 | 产出物 | 现状 |
|:---|:---|:---|
| `agent_components/nodes.py:268` | `prompt = self.prompt_factory.generate_excel_plan_node()` 构建的 prompt | 新流程改为 thinking→处理节点后，该 prompt 构建完即被丢弃，属旧「自生成」路径残留 |
| `agent_components/nodes.py:296-303` | `prompt_vars` 大字典（模块树/分析段落/接口概要等） | 构建后从未消费，新流程改用 `_prepare_plan_prompt_vars` |
| `agent_components/generators/__init__.py:369` | `pre_by_id = {p["id"]: p for p in shared_pres}` | 构建后从未使用 |
| `web/routes/modules.py:249` | `ModuleAnalysis.status = "approved"` | 代码注释自认「仅前端追踪，Phase B 不检查」，写入后无任何消费者 |
| `agent_components/nodes.py:927-929` | 日志 `node_order` 列表里的 `"format_test_points"` | 旧节点残留键，永远不会有数据填充 |
| 第一章列出的 15 个 prompt/函数 | 各自产出的 prompt 模板 / 校验结果 / 数据库操作 | 产出了但从没被消费 |

---

## 四、其他（未使用 import 与潜在 bug）

- **未使用的 import**：`nodes.py`、`web/app.py`、`agent_components/generators/__init__.py`、`ingest_v2.py` 等存在一批历史遗留的未使用 import（如 `openai`、`yaml`、`ValidationError`、`Workbook` 等），非功能性死代码，可顺手清理。
- **`web/tasks.py:309 / 395`**：`nonlocal _heartbeat_stop` 声明了但函数内从未赋值，心跳停止标志实际不生效——潜在 bug，建议修复或删除。

---

## 附：分析产出物

- 数据流图：见同目录 `2026-08-04_method_dataflow_diagram.md`
