# Phase C 独立重跑工作流：对话流新增「Phase C 项」+ 模块目录 user_context 注入

> **状态：方案已确认（2026-09-09）。** 触发源：智慧停车执行日志 77/111 失败，根因是
> Phase C **数据生成**阶段产出的 YAML 数据违反服务端真实约束（临停收费规则绑定无效、设备
> 厂家/型号编码自造、车牌格式非法）；url 前缀已在 2026-09-09 Thread D 修入存档。用户要求把该
> 分析**不归档**，改为提炼成模块目录约定文件作为 `{user_context}`，让 Phase C 可作为**独立工作流**
> 单独启动重跑。
> 原则：①先确认（AskUserQuestion 四项决策）②最小改动③连带汇总④方案含测试、改完运行⑤文档先行。

## 一、背景：为什么需要「Phase C 独立工作流」

现网 Phase C 只有一条入口：Phase B 生成 test_plan.xlsx 后，聊天结果面板点「确认并生成
PY+YAML」→ `/confirm-plan` → `_confirm_plan_bg`（dep_map + .py + yaml 全链）。想**单独重跑某
模块的 YAML 数据**（不重跑 Phase B / .py / dependency_map），没有入口；想给数据生成注入一段
「执行失败教训」作为 `{user_context}`，也没有稳定通道（user_ctx 参数现网前端没传）。

## 二、已确认决策（AskUserQuestion 2026-09-09）

1. **入口**：对话工作流新增「Phase C 项」→ 单独启动 Phase C 重跑（复用聊天候选按钮渲染）。
2. **分析文件绑定**：模块目录约定文件 `phase_c_user_context.md`，放在模块目录（`test_plan.xlsx`
   旁），该模块 Phase C 启动时自动读作 `user_ctx`。
3. **重跑范围**：仅 YAML 数据生成（跳过 .py 与 dependency_map；test_plan.xlsx / api_defs.json
   已存在）。
4. **执行**：实现后实跑一次智慧停车 Phase C 重跑（消费 LLM credit）。

## 三、现状事实（探索已确认）

- `_generate_all_yamls(excel_path, api_defs_json, user_ctx)`（yaml_gen.py:248）直接读 excel 行、
  三阶段 barrier（setup→test→teardown）、`output_base = dirname(excel_path)`、**不依赖**
  dependency_map.json；`user_ctx` 注入 prompt 的 `{user_context}` 槽（extraction_prompts.py:734）。
- `api_defs_json` 形参是 JSON **字符串**；模块目录快照 `api_defs.json` 恰为同形状 list
  （智慧停车 39 条 = 业务 28 + mock 11，Thread D 已补 url 前缀）→ 重跑直接读该文件文本，保证
  与新 YAML 同源、前缀不回归。
- 聊天工作流会话：`_workflow_sessions[session_id]={"state":dict, "created_at"}`；
  `/workflow/start` 返回 waiting+候选 → 前端按钮 → `/workflow/confirm`。Phase C 独立项复用同一
  会话槽与按钮渲染，`state["flow"]="phase_c"` 时走独立解析（不碰 LangGraph）。
- 智慧停车模块目录实测含 `test_plan.xlsx` `api_defs.json` `SmartParking/**/*.yaml`。

## 四、方案

### 1. 分析文件（模块目录约定，产物）
`C:/Users/damai/PyCharmMiscProject/testcase/园区基线/智慧停车/phase_c_user_context.md` = 从日志
失败根因提炼的**生成约束/规则**（不含示例）。详见该文件本体；不归档到 changelog。

### 2. `web/tasks.py`
- 纯函数 `_list_phase_c_module_dirs(testcase_base) -> list[str]`：扫 `*/test_plan.xlsx` 且同目录有
  `api_defs.json` + `phase_c_user_context.md` 的目录名，排除 `.bak`/`_backup`/`_regenerated`。
- 纯函数 `_read_phase_c_user_ctx(module_dir) -> str`（读 context 文件，缺失→""）。
- bg `_phase_c_rerun_bg(task_id, excel_path, module_dir)`：仅 YAML 重跑——`user_ctx` 读约定文件、
  `api_defs_json` 读 `api_defs.json` 文本、心跳 + `_generate_all_yamls`、按 `_confirm_plan_bg`
  尾部同构回填结果。**不调用** `_generate_py_file` / `_generate_dependency_map`。

### 3. `web/routes/chat.py`
- 新增 `POST /phase-c/start`：列候选（有 context 文件的模块目录）→ 建 `flow="phase_c"` session →
  返回 waiting+候选；空 → no_match 提示。**不要求 vector/files**。
- `/workflow/confirm` 开头：`state["flow"]=="phase_c"` → 独立解析（数字/精确匹配 → candidate_paths
  → `_phase_c_rerun_bg`），未匹配返回 404（不触发 LLM 重识别）；session 由 bg finally 清理。
  非 phase_c 走原逻辑不动。

### 4. 前端
`templates/index.html` 聊天输入行加「🔄 单独重跑 Phase C 数据」按钮 → `static/app.js` 新增
`startPhaseC()`（POST `/phase-c/start`，waiting 时复用候选按钮渲染 + `confirmWorkflowModule`）。

## 五、测试设计（`tests/test_phase_c_standalone_rerun.py`）

1. 纯函数：tmp 目录命中规则 / 缺 context 不列 / `.bak` 排除 / 读文件正确、缺失返回 ""。
2. `_phase_c_rerun_bg` 装配：mock `_generate_all_yamls` + `_update_task` → 断言 api_defs_json==原文、
   user_ctx==context 内容、excel_path 正确、completed 字段齐全；断言未调用 .py / dep_map。
3. 路由：静态断言 chat.py 含 flow 分支 + `/phase-c/start` 路由存在。
4. 相邻回归：test_workflow_init / test_phase_b_repair_round_thinking / test_thinking_log /
   test_quality_gate / test_plan_validator。（test_phase_bc_unit.py 9 failed 为既有工作区问题无关。）

## 六、实施记录（2026-09-09）

| 位置 | 改动 |
|:---|:---|
| `web/tasks.py` | 新增 `_list_phase_c_module_dirs`（门槛：test_plan.xlsx+api_defs.json+phase_c_user_context.md，排除 .bak/_backup/_regenerated）、`_read_phase_c_user_ctx`（读约定文件，缺失→""）、`_phase_c_rerun_bg(task_id, module_dir, session_id)`（仅 `_generate_all_yamls`；api_defs 读模块目录 api_defs.json 原文；user_ctx 读 context 文件；心跳照抄 `_confirm_plan_bg`；完成后清理 session） |
| `web/routes/chat.py` | 新增 `POST /phase-c/start`（列候选建 flow=phase_c session，不要求 vector/files）；`/workflow/confirm` 开头 `flow=="phase_c"` 独立分支（数字/精确匹配 → `_phase_c_rerun_bg`，未匹配 404，不触发 LLM 重识别） |
| `templates/index.html` | 聊天输入行加「🔄 单独重跑 Phase C 数据」按钮 |
| `static/app.js` | 新增 `startPhaseC()`（POST /phase-c/start → 复用候选按钮渲染 → confirmWorkflowModule） |
| 模块目录 | 创建 `智慧停车/phase_c_user_context.md`（日志失败根因提炼的生成约束规则 R1-R5，无示例；不归档） |
| 测试 | `tests/test_phase_c_standalone_rerun.py` 12 passed + 相邻回归 5 文件 69 passed |

**自查结果**：
1. 纯函数：只列齐备模块目录；`.bak`/`_backup`/`_regenerated`/缺任一文件均排除；目录不存在返回 []。
2. `_phase_c_rerun_bg`：api_defs_json == api_defs.json 原文、user_ctx == context 内容、excel_path 正确、
   completed 字段齐全；守卫 .py / dependency_map 未被调用（调即 AssertionError）。
3. 路由：`/phase-c/start` 已注册；`/workflow/confirm` 含 flow=phase_c 分支；Phase C 入口不要求向量库。
4. 未触碰：原「Phase B → 确认并生成 PY+YAML」全链（`/confirm-plan`→`_confirm_plan_bg`）不动。
   服务无热重载：需重启服务后按生产路径验证新路由。

## 七、波及面 / 未覆盖

- 原 Phase B → 「确认并生成 PY+YAML」全链不动；新增仅独立入口。
- 重跑覆盖模块目录既有 YAML 并重建 `_generation_errors.json`（语义同 `_confirm_plan_bg`）。
- 未扩：其余模块暂不建 context 文件 → 不在候选列表（列表以存在 context 文件为门槛）。
- 服务无热重载：改完需重启服务后验证新路由。
