# Phase C 流程总体检查 — 问题清单

| 项目 | 内容 |
|:---|:---|
| 日期 | 2026-08-07 |
| 检查范围 | Phase C（确认计划 → dependency_map → .py 生成 → YAML 生成 + 修复循环 + 后校验）全链路 |
| 检查文件 | `agent_components/generators/__init__.py`、`agent_components/generators/_helpers.py`、`agent_components/post_validator.py`、`agent_components/nodes.py`（_invoke_think/_invoke_structured）、`prompts/response_model.py`、`prompts/extraction_prompts.py`、`web/tasks.py`（_confirm_plan_bg）、`settings.py`、`config.py` |
| 状态 | 📋 检查清单（待定稿实施） |

> 背景：`2026-08-05_execution_failure_26_optimization.md` 已从三阶段梳理出优化点并标记优先级；本次是**对 Phase C 当前代码做独立复核**，聚焦「代码实现与文档/配置/设计意图是否一致」，不重复既有 2.1~3.6 的已知项（已落地情况见第五节复核表）。

---

## 一、P0 — 功能性缺陷

### 1.1 🔴 后校验修复轮在默认配置下永不触发（死逻辑 / 越界比较）

**定位**：`agent_components/generators/__init__.py:814`

```python
if _fixable and result["rounds"] < config.YAML_REPAIR_ROUNDS:
```

`result["rounds"]`（`_run_yaml_rounds` 返回的 `rounds_run`）只有两种取值：
- 全量轮全过 → `1`
- 触发过修复轮 → `2`

而 `config.YAML_REPAIR_ROUNDS` 默认 `1`（`settings.py:217`）。**`rounds < 1` 恒为 False** → 后校验发现的 P0/P1 问题只写入 `_post_validation_issues.json`，**永远不进修复轮**。注释写「修复轮未耗尽时」，但按此比较式，耗尽判定是 `rounds >= budget`，实际应为 `rounds <= budget`。

**影响**：`YamlPostValidator`（delete_body_wrapper / assertion_dynamic_key）发现的问题仅落文件告警，不驱动重生成 → README「YAML 质量治理 · 批量自查修复循环」对后校验部分名不副实。全默认配置下该段代码是死代码。

**建议**：改为 `result["rounds"] <= config.YAML_REPAIR_ROUNDS`，并加一条覆盖该编排的测试（现有测试只单测 `_run_yaml_rounds` 和 validator 单项，无 `_generate_all_yamls` 后校验轮次集成测试）。

### 1.2 🔴 后校验修复轮覆盖主轮失败登记与计数（记账错误）

**定位**：`agent_components/generators/__init__.py:829-830`

```python
result["success"] = result["success"] - len(_affected_tasks) + _post_result["success"]
result["failed"] = _post_result["failed"]
```

- 主轮（`_run_yaml_rounds` 第一次）若已有失败 → 已写 `_generation_errors.json` 且 `result["failed"]=N`。
- 后校验轮再跑 `_run_yaml_rounds` → 其返回的 `failed` 只统计**受影响文件**（都是主轮成功、仅带后校验问题的文件）→ `result["failed"]` 被覆盖，**主轮 N 个失败从最终计数中消失**。
- 若后校验轮有失败，第二次 `_run_yaml_rounds` 会用本轮 registry **覆盖写 `_generation_errors.json`** → 主轮失败登记丢失。
- 终态消息「YAML: 成功/总数，仍失败 X」因此可能报 `0 失败`，但 `_generation_errors.json` 里还有主轮失败 → 前后矛盾。

**建议**：主轮失败与后校验失败合并计数（`failed = 主轮 failed + 后校验轮 failed`），`_generation_errors.json` 合并两批 registry 后一次写入；为 `_generate_all_yamls` 补集成测试断言「主轮失败 + 后校验失败同时存在时的成功/失败/错误清单」。

### 1.3 🔴 `.py` 生成与 YAML 生成脱节 — 失败文件仍被 .py 引用

**定位**：`web/tasks.py:295-326`（先 `_generate_py_file` 后 `_generate_all_yamls`）、`agent_components/generators/__init__.py:441-480`

`.py` 无条件引用 `setup_data/setup_<class_slug>.yaml`、`setup_data/teardown_<class_slug>.yaml`、`<func_en>/test_data.yaml` 路径。若对应 YAML 终态失败（不写盘，符合「无占位假文件」原则），`.py` 仍指向不存在文件 → pytest 收集/运行时直接报错，且前端消息「.py 成功 + YAML 部分失败」会掩盖「这批用例实际不可运行」。

**建议**：YAML 生成后回写 .py——对 `test_data.yaml` 失败的功能方法，从 .py 中剔除对应 `test_` 方法（或打 `@pytest.mark.skip`）；setup/teardown YAML 失败时对应 class 的 fixture 降级为 `pass` 并告警。

### 1.4 🔴 dependency_map.json 生成后完全不消费（2026-08-05 分析 2.1 未落地）

**定位**：`web/tasks.py:265-289`（生成+加载）、`agent_components/generators/__init__.py:669-788`

`_generate_all_yamls` / `_generate_one_yaml` / `_generate_py_file` 签名均不含 dep_map。`story_pre_api_sequence`、`case_api_sequences`、`decision_map`、`internal_dependency`、`teardown_api_sequence` 全部落日志即弃——依赖分析结果不驱动 setup 顺序、不驱动 extract 必需性、不驱动 teardown 序列。这是智慧用电_26「充值失败:租户不存在」等高发问题的已知根因，**本次复核确认仍为分析产物落空**。

**建议**：按 2.1 落地——setup 顺序取 `story_pre_api_sequence`；`teardown_api_sequence` 直接喂 teardown 生成；`internal_dependency.used_by` 决定 extract 是否必需。

---

## 二、P1 — 稳健性 / 一致性

### 2.1 🟡 YAML 全局熔断已移除，配置残留且注释误导

**定位**：`settings.py:222-226`、`config.py:80`、`agent_components/generators/__init__.py:845+`、`tests/test_phase_bc_unit.py:1015-1017`

`yaml_failure_circuit_breaker`（注释「首轮失败率超阈值 → 终止并报错，防止 prompt/骨架缺陷导致批量失败、token 失控」）与 `config.YAML_FAILURE_CIRCUIT_BREAKER` 仍存在，但 2026-08-03 已从 `_run_yaml_rounds` 移除（测试备注明确「仅残留」）。**批量失败时无熔断，token 消耗不可控**。要么重新实现熔断，要么删除配置项与误导性注释。

### 2.2 🟡 dependency_map 重试用了 YAML_REPAIR_ROUNDS 而非专用配置

**定位**：`agent_components/generators/__init__.py:74` vs `settings.py:173-176`

`_generate_dependency_map` 用 `range(1 + config.YAML_REPAIR_ROUNDS)` 控制重试；专用配置 `DEPENDENCY_REPAIR_ATTEMPTS`（默认 2，changelog 2026-07-21 明确要求使用）是死配置。两者语义独立，改 YAML 修复轮会连带改变 dep_map 重试次数。应改用 `config.DEPENDENCY_REPAIR_ATTEMPTS`。

### 2.3 🟡 teardown 由 200 字符截断的创建步骤逆向推导，且弃用 dep_map.teardown_api_sequence

**定位**：`agent_components/generators/__init__.py:756-759`

```python
teardown_lines.append(f"根据 {pid} 的创建步骤逆向操作：{pre['steps'][:200]}")
```

创建步骤截断到前 200 字符喂给 LLM 逆向推导清理操作——信息严重不足只能猜；而 dep_map 里已有 LLM 精心分析的 `teardown_api_sequence`（正是为此设计）却被弃用。与既有 3.4（teardown 8192 token 触顶）同源，属「未设计」而非「实现不完善」。

### 2.4 🟡 url 路径参数注入与校验器文案三方矛盾

**定位**：
- `prompts/response_model.py:605-640` `validate_url_no_placeholder`：**禁止** url 含 `${`，警告「框架不对 URL 调用 replace_load()，${} 会被原样拼接导致 404」
- `agent_components/generators/__init__.py:633-643`：对 has_path_params 接口在校验后**注入** `${get_extract_data(param)}`
- `agent_components/api_annotations.py:131-141`：has_path_params 描述「运行时替换为 ${get_extract_data(xxx)}」

三方口径不一致：若运行时 apiutil 确实对 url 做 replace_load，则校验器报错文案对路径参数是误导；若不做，则注入产物 404（正如校验器警告）。需对照外部运行时框架（`base/apiutil.py` 的 `run_blocks`/`specification_yaml`）确认 url 是否走 replace_load，统一口径。

### 2.5 🟡 `header: null` 可绕过 validate_header_exists

**定位**：`prompts/response_model.py:579-592`（normalize_base_info 只在 `"header" not in base` 时注入）、`642-661`（validate_header_exists 只查 key 存在）

LLM 输出 `header: null`：key 存在 → 不注入也不拦截 → `model_dump(exclude_none=True)` 把 header 从 YAML 剔除 → 运行时按 validator 自己注释「直接读取 baseInfo['header'] 缺键 KeyError」。校验应查**值非空**（`not isinstance(self.baseInfo.get("header"), dict)`），而非仅 key 存在。

---

## 三、P2 — 已知项复核（2026-08-05 清单落地情况）

### 3.1 🟠 retCode:0 prompt 示例仍未修复（3.1 未落地，必复发）

**定位**：`prompts/extraction_prompts.py:252,268,287,328,330`、`prompts/response_model.py:463-464`

`format_yaml_data_prompt` 与 `generate_dependency_map_prompt` 的示例/铁律仍是 `eq: {$.retCode: 0}`；`validate_validation_not_empty` 的报错文案也仍是 `retCode: 0`。上一批已改产物为 1，但**新批次仍会生成 0**。已识别（2026-08-05 P0）尚未实施。

---

## 四、P3 — 低风险 / 潜在

| 项 | 定位 | 说明 |
|:---|:---|:---|
| 4.1 `_read_excel_rows` 依赖 `ws.active` | `__init__.py:152` | 假设 Sheet1 是活动表，用户重排 sheet 顺序会静默读错表 |
| 4.2 `_lookup_api` 前缀匹配过宽 | `__init__.py:592-601` | `url.startswith(api_url)` 对短 url（如 `/api`）会误匹配任意子路径 |
| 4.3 response_model 模块级全局跨线程共享 | `response_model.py:113-120`（_DB_SCHEMA_EMPTY）、`18-20`（_drift_*）、`28-101`（ValidationInterceptor） | YAML 并发生成时多线程写同一全局；当前 DB_SCHEMA 恒空故无害，属潜在竞态 |

---

## 五、验证方法

| 项 | 验证 |
|:---|:---|
| 1.1/1.2 | 单测 `_generate_all_yamls`：制造后校验 P0/P1 问题 + 主轮失败，断言修复轮被触发、失败计数与 `_generation_errors.json` 一致 |
| 1.3 | 单测：某 YAML 终态失败时，对应 .py 中方法被剔除/标记 skip |
| 2.2 | 改 YAML_REPAIR_ROUNDS 后 dep_map 重试次数不变 |
| 2.4 | 对照运行时 `run_blocks` 确认 url 是否 replace_load |
| 3.1 | 重跑新批次，扫描产物 retCode 断言 100% = 1 |

---

## 六、结论

- **P0 共 4 项**：后校验修复轮死逻辑（1.1）、失败计数被覆盖（1.2）、.py 与 YAML 脱节（1.3）、dep_map 分析产物落空（1.4）。前三项是**纯代码层确定性缺陷**，优先修复且均可单测覆盖；1.4 为跨文件改造（tasks + generators + prompt），按 2026-08-05 清单 P1 持续推进。
- **P1 共 5 项**：熔断缺失（2.1）、配置错用（2.2）、teardown 未设计（2.3）、url 口径矛盾（2.4）、header 校验漏洞（2.5）。
- **复核结论**：2026-08-05 清单中 1.1/3.2/3.6 未见落地痕迹；3.1 确认未修复且必复发；2.1（dep_map 接入）确认未实现。已落地部分（如 has_path_params、is_export 兜底、占位符注册表校验）工作正常。
