# Phase C 定向重跑 + 权威失败数据「双落点」动态记录（`.last_run.json`）

> **状态：方案已确认（2026-09-09）。** Thread F（`2026-09-09_phase_c_standalone_rerun_user_context.md`）
> 已交付 Phase C 独立**全量**重跑。本文档 = 其演进：**定向重跑**（只跑实际失败 + 导致失败的上游）+
> 给权威失败数据一个**动态落点**（一份原位原始日志、一份逐测试文件覆盖式 `.last_run.json`）。
> 原则：①先确认（AskUserQuestion 两次）②最小改动③连带汇总④方案含测试、改完运行⑤文档先行。

## 一、背景：为什么需要「定向重跑 + 动态落点」

智慧停车执行日志 77/111 失败根因（R1-R5）已提炼成 `phase_c_user_context.md`（只含规则无示例）。
Thread F 提供**全量** Phase C 重跑入口。用户进一步要求：①只重跑「实际是失败的用例 + 导致它们失败的
用例」，避免每轮全量重生成；②`{user_context}` 携带失败事实；③权威失败数据要有一个**动态落点**——
全量原始日志(原位)太长、junit 单文件覆盖不留逐文件视图，故每个被执行 YAML 旁要有一份**只保留最新一轮**
的伴随记录，让定向重跑（及人工检视 func 目录）立刻看到该文件最新权威失败事实。

**本轮基线状态**：Thread F 的全量重跑被「暂停，换 key」打断 → 模块磁盘不一致（setup 47/…、teardown 0、
部分覆盖一半）。先跑完基线得一致状态再做定向。

## 二、已确认决策（AskUserQuestion 2026-09-09 两次）

1. **本轮基线 = 先跑完全量基线，再做定向**：先重放被打断的全量 174-YAML 重跑（R1-R5 静态上下文）得一致
   状态；定向工具化供后续迭代。
2. **失败登记 = junit.xml 权威 + 执行日志补原因**：junit 选失败/破损 pytest 函数；原始执行日志补服务端
   拒绝原因（`接口实际返回信息{...}` 的 data）。
3. **动态落点刷新归属 = 自动化测试框架改造，本次由 LLM 手动处理**：长期由执行框架（PyCharmMiscProject
   run 层）执行后原生写逐文件记录（本次不改框架）；本轮仓库侧提供可测试吸收辅助 + 一次性驱动
   （tests/tools 惯例、不入系统运行），LLM 每次框架执行后手动跑一次。
4. **动态落点形态 = 逐 YAML 伴随同名前缀覆盖式**：`test_data.yaml → test_data.last_run.json`；
   `setup_<slug>.yaml → setup_<slug>.last_run.json`；只保留最新一轮；通过的文件也写 `passed` 清旧失败；
   **原位原始日志不动、不标逻辑**。
5. **失败注入 = 静态规则 + 文件级失败摘要（逐选中文件）**：`{user_context}` = 静态
   `phase_c_user_context.md` + 该文件自己的失败摘要（`ctx_overrides` 逐文件注入）。
6. **重跑范围 = test + 其类 setup 夹具**（细化）：某 func 判定失败时——其 `test_data.yaml` **仅当** body 级
   失败（junit `<failure>`）才重生成；其类 `setup_<slug>.yaml` **仅当** setup 破损（junit `<error>`
   "failed on setup"）才重生成。body 未执行（broken-by-setup）的 func **不**重生成其 test_data。
   teardown 本轮不涉及（0 个）。

> Thread F 四项决策（独立入口/上下文文件绑定/仅 YAML 不碰 .py+dep_map/实跑一次）仍有效，见上引文档 §二。

## 三、现状事实（真实文件已核验）

- 执行在**框架侧**（run.py + pytest.ini `--junitxml`）；web 服务只生成不执行。仓库执行包装
  `tests/execution/test_run_generated_case.py::run_generated_case()`。
- junit 分类**必须按 message 前缀**：88 节点去 `[param]` = 49 setup-error + 30 body-failure + 1 passed，
  其中 8 个 `<error>` 实为 **`failed on teardown`**（盘上无 teardown → `TypeError`，且同时有 `<failure>`）
  → `<failure>` 优先 → failed；`failed on setup` → broken；纯 `failed on teardown` → passed+flag。
- **runner `.py` 是权威 func/setup 映射**（py_export.py 生成模板）：usefixtures + fixture pre-yield
  `run_blocks(...setup_<slug>.yaml)`；无前置 fixture = pass-only（不伪造 broken）。
- **case_name 漂移真实存在**（盘上现 YAML ≠ 被执行 YAML）→ 摄取必须发生在「对当前盘上 YAML 执行后」，
  匹配失败优雅降级 junit_msg。
- `_translate_to_en` 命中 translation_cache 时不调 LLM；`_parse_setup_extract_keys` glob 磁盘 setup 重建
  键表 → 只选 test 时 D3 注解不丢。
- yaml_gen.py:248 `_generate_all_yamls(excel_path, api_defs_json, user_ctx)`；任务 Append 期
  :329-330/:349-359/:370-375；:377 all_tasks；三阶段 barrier；`user_ctx` 逐文件逐轮注入 prompt `{user_context}`。

## 四、方案

1. **新纯模块 `agent_components/run_results.py`**（可导入 web+工具；不入 nodes 图）：parse_junit /
   build_module_map(AST) / parse_run_log_blocks / read_yaml_case_names+extract_file_failures /
   ingest_run_results(原子写 sidecar, run_ts 优先级：显式 > junit timestamp > log 文件名) /
   read_last_run_records(只留 max run_ts cohort) / select_rerun_targets / build_failure_digest。
2. **`yaml_gen.py`**：`_generate_all_yamls(..., allow_paths=None, ctx_overrides=None)` — Append 期过滤三列表
   （命中才 makedirs+append，row 打 `_ctx_override`）；`_run_yaml_rounds` submit 前取
   `ctx = row.get("_ctx_override") or user_ctx`。None = 现行为（向后兼容）。
3. **`web/tasks.py`** `_selective_phase_c_rerun_bg`：read_last_run_records（空+junit→ingest；空→failed）；
   targets 空→completed；ctx_overrides = static_ctx+digest；allow_paths = test+setup；result+excluded 回填。
4. **`web/routes/chat.py`** `POST /phase-c/selective-rerun`（模块路径 + 可选 junit/log/run_ts；默认定位
   config.PYCHARM_MISC 下最新 junit+log）→ task_id running。不动 `/workflow/confirm` 的 phase_c 全量分支。
5. **手动摄取驱动** `tests/tools/_ingest_phase_c_run_sidecars.py`（本回合 LLM 手动跑；框架原生后替换）。
6. 前端不新增按钮（本轮走驱动/端点，后续需要再补 UI）。

## 五、测试设计

- `tests/test_run_results.py`：parse_junit（param 去重 / failure→failed / failed-on-setup→broken /
  failure+teardown→failed+flag / 纯 teardown→passed+flag / 88 节点样本计数）；parse_run_log_blocks；
  extract_file_failures 只归因本文件、无匹配不抛；build_module_map（usefixtures+pre-yield+pass-only / fallback）；
  ingest 写覆盖+passed 清场+broken-by-setup 不写+run_ts 优先级+原子；read 只留 max cohort；select 目标/排除；
  digest 事实无示例。
- `tests/test_selective_phase_c_rerun.py`：yaml_gen allow 端到端（fake agent 计数子集 / None 全量不变 /
  只选 setup 仅 Stage1 / ctx_overrides 逐文件注入）；bg 装配（allow_paths+ctx_overrides 派生、excluded 回填、
  session 清理、无落点+junit→ingest、无落点无 junit→failed）；路由静态断言。
- 相邻回归：test_phase_c_standalone_rerun / test_three_stage_generation / test_phase_c_autofix /
  test_regression_generators_helpers / test_phase_c_api / test_workflow_init。
  （test_phase_bc_unit.py 9 failed 为既有工作区问题，无关。）

## 六、实施记录（2026-09-09）

| 位置 | 改动 |
|:---|:---|
| `changelog/` | 本文档（决策归档） |
| `agent_components/run_results.py` | 纯函数库：parse_junit / parse_run_log_blocks / build_module_map(AST) / 归因 / ingest 原子写 / read cohort / select 目标 / digest；单测 `tests/test_run_results.py` 20 passed |
| `yaml_gen.py` | `_generate_all_yamls(..., allow_paths, ctx_overrides)`；Append 期过滤 + row `_ctx_override`；`_run_yaml_rounds` submit 取 override 优先（向后兼容 None=全量） |
| `web/tasks.py` / `chat.py` | `_selective_phase_c_rerun_bg`（落点→选点→digest→allow 定向）+ `POST /phase-c/selective-rerun`；单测 `tests/test_selective_phase_c_rerun.py` 12 passed |
| `tests/tools/_ingest_phase_c_run_sidecars.py` | 手动摄取驱动（LLM 每次框架执行后跑；框架原生写后替换）；py_compile 通过、--help 可用 |
| 测试 | 相邻回归（详见 §五 清单；test_phase_bc_unit.py 9 failed 为既有工作区问题，无关） |

## 七、波及面 / 未覆盖

- 不改执行框架（本次动态落点 LLM 手动；框架原生写 = 未来独立改造）。
- 不扩 teardown；不改 Thread F 全量入口；原「Phase B → 确认并生成 PY+YAML」全链不动。
- 重跑覆盖既有 YAML + 重建 `_generation_errors.json`（语义同既有全量路径）。
- 服务无热重载：改完需重启服务验证新路由 `/phase-c/selective-rerun`。
- 失败事实不进 changelog（在模块目录 sidecar / 原始日志）。

## 八、#31 实跑判障协议（2026-09-09 用户追加，决策 7）

- 框架执行由 **LLM 用包装器**（tests/execution `run_generated_case`）自动跑；迭代上限 **跑两轮**，
  两轮后出最终测试报告。
- 每例失败三层排查（第一轮记录 URL/Headers/Body/响应码/响应体）：连通性（非 200/超时/拒连→环境问题，
  终止该例）；契约（结构不符→接口缺陷，**不改数据重试**，只记证据）；业务逻辑（成功但结果不符→仅 1 次
  数据自查：中文标点/空格/类型错，有误则修正重跑备注「修正了测试数据」，无误→接口逻辑 Bug 停止）。
- 最终报告逐例带 **Bug 归属**（测试数据问题 / 接口代码缺陷 / 环境问题）。判障倾向：先疑测试数据自身，
  排除后再疑接口。此协议已存记忆（parking-run-triage-two-round-protocol）。
