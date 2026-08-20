# 单节点 YAML 生成：schema 驱动的专用 prompt + 19号 prompt 可修缺陷修复

> 日期：2026-08-20
> 状态：**已实现，验证通过（2026-08-20）**
> 决策：单节点不再复用两段式 `format_yaml_data_prompt`，改为专用 `generate_yaml_data_single_prompt()`：**schema 驱动（无手写示例）+ 固定 schema 入 system 段 + 19号 prompt 可修缺陷子集**

## 背景

- 当前单节点（`_generate_one_yaml_single`）复用 `format_yaml_data_prompt`（两段式第二阶段模板）：**手写 JSON 示例 + 18 条铁律**，未注入任何 schema。模型靠示例猜结构，且示例/铁律里硬编码 `eq: {$.retCode: 0}`（见 `changelog/2026-08-19_execute_wiselectric_32_diagnosis.md` §3-1：dev 后端成功=retCode 1、失败=0）。
- 机制可行性已证：`excel.py _generate_dependency_map` 即同款模式——`json.dumps(Model.model_json_schema(), ensure_ascii=False, indent=2)` 注入 `{json_schema}` → `_invoke_think` → `json.loads` → Pydantic 校验。`TestData.model_json_schema()` 实测 4400 字符、`json` 别名正确渲染、$defs 含 StepData/TestCase。

## 决策

1. **单节点输出格式不增加手写示例**，所有格式来源 = api 数据 + `TestData.model_json_schema()`（YAML 自有原始模型，与 Excel/依赖图 schema 无关），Pydantic 校验沿用 `TestData.model_validate`。
2. **prompt 含 4 个来源（唯一内容来源）**：B 用例内容（`{test_case_logic}`）、A 数据分析（`{data_analysis}`）、接口详情文档（`{api_definitions}`）、yaml 格式 schema（`{json_schema}`）。
3. **schema 格式固定 → 放 system 段**：json_schema 对所有调用恒定，作固定内容进 system 消息（提升 schema 遵循命中率；system 消息逐次字节一致也利于 prompt 缓存）。human 只放逐用例/逐接口的可变内容，不重复注入 schema。
4. **不要具体事例**——prompt 不含任何可照抄的业务示例/示例数据/示例断言 payload（现有 `format_yaml_data_prompt` 的 JSON 示例块就是 LLM 照抄源）；铁律一律抽象规则措辞，禁止 `eq: {$.retCode: 1}`、`contains: {status_code: X}` 这类可整段复制的字面量。
5. **19号缺陷子集**：数据唯一化（防跨套件/套件内重名碰撞，§3-7）、delete 参数按接口定义（§3-3）、成功/失败断言的期望值**取自接口返回定义**（不硬编码项目特例，§3-1）写进新 prompt；teardown 逆操作**先不加**（用户延后）。

## 方案摘要（4 文件）

### 1. `prompts/extraction_prompts.py` — 新增 `generate_yaml_data_single_prompt()`

放 `format_yaml_data_prompt`（240 行）之后。system 段：角色文本 + `{data_factory_methods}` + `{json_schema}` + 12 条铁律（抽象措辞、无示例字面量）。human 段三节：B 用例内容 / A 数据分析 / 接口详情文档 + `{user_context}` / `{db_schema}`，结尾"请严格按照 system 段的 yaml 格式 schema 输出 TestData JSON…"。**schema 只在 system，human 无 `{json_schema}` 占位**。

### 2. `agent_components/generators/yaml_gen.py` — `_generate_one_yaml_single` 接线

局部 import `generate_yaml_data_single_prompt` 替换 `format_yaml_data_prompt`（250 行）；`json_schema_text = json.dumps(TestData.model_json_schema(), ensure_ascii=False, indent=2)`；`format_messages(..., json_schema=json_schema_text, ...)` 增加注入；`prompt_label = "generate_yaml_data_single_prompt"`（274 行）。其余（thinking+json_object 绑定、reasoning_label 监测、json.loads→注入注解→set_db_schema_empty→model_validate→写盘）不动。

### 3. `tests/test_phase_bc_unit.py` — 更新 `TestYamlSingleNodeGenerate` + 新增 `TestYamlSingleNodePromptRender`

渲染断言：system 消息含 `"$defs"`/`"StepData"`（schema 已注入）+ 铁律；human 消息含 B 用例内容 / A 数据分析 / 接口详情文档、不含 `{json_schema}` 占位；整体无 `$.retCode` / `retCode: 0` 可照抄字面量；含成功/失败断言取值来源（接口返回定义）、数据唯一化、delete 按定义；**不硬编码任何项目特例取值**（无 `retCode: 1/0`、无 `成功=1`）。

## 不做

- 不动 `format_yaml_data_prompt` / `analyze_yaml_data_prompt` / `repair_yaml_data_prompt`（两段式行为不变）。
- 不做 teardown 逆操作（用户延后）；不修数据类缺陷（字段集污染 / 主数据真实化 → DB 层）。

## 验证

```bash
python -m pytest "tests/test_phase_bc_unit.py::TestYamlSingleNodePromptRender" "tests/test_phase_bc_unit.py::TestYamlSingleNodeGenerate" -q
python -m pytest tests/test_phase_bc_unit.py tests/test_yaml_db_export.py tests/test_yaml_ref_check.py tests/test_thinking_log.py tests/test_llm_adapter.py -q
```

### 实际结果（2026-08-20）

- 定向新测试（TestYamlSingleNodePromptRender ×3 + TestYamlSingleNodeGenerate ×2）：**5 passed**；修正后 + `test_no_project_specific_retcode` → **6 passed**。
- 防回归套件（test_phase_bc_unit / test_yaml_db_export / test_yaml_ref_check / test_thinking_log / test_llm_adapter）：**141 passed**（137 既有 + 4 新增；3 条既有 pydantic 收集告警与本次无关）。
- 两段式同思路修正后（+ `TestFormatYamlPromptNoProjectRetcode` ×2）：7 套件合计 **192 passed**（test_phase_bc_unit 106）。
- 附加安全网（test_phase_a_analysis + test_phase_c_api）：**64 passed, 2 skipped, 1 xfailed**（此前 2 条 TestConfirmPlanValidation `httpx.ConnectError` 本轮随本地 web 服务恢复通过）。

### 用户反馈修正（2026-08-20，rule 9 重写）

- 用户指出：原铁律 9「本项目 dev 后端业务信封 retCode 语义为 业务成功=1、业务失败=0」把**项目特例**写进**公用 prompt**（generate_yaml_data_single_prompt 跨项目共用，各项目成功/失败取值不同）。
- 修正：铁律 9 改为「**成功/失败断言的期望值取自接口返回定义**」——断言字段与期望值必须来自「接口详情文档」返回定义中真实给出的字段/取值/语义；正向断言成功返回取值、反向断言失败返回取值；返回定义未给出明确取值时退化为 contains 字段存在性或 status_code 断言，禁止臆造固定取值。
- 实测接口返回定义（`/electricMeter/add` return 列表）`retCode` 的 `desc`/`value` 为空——成功语义缺失即 §3-1 数据缺口；prompt 层已正确委托接口数据，不替代数据修复（数据补语义后断言自然完整）。
- 同步新增测试 `TestYamlSingleNodePromptRender::test_no_project_specific_retcode`：system 无 `retCode: 1` / `retCode: 0` / `成功=1`。

### 用户反馈修正 2（2026-08-20，两段式 prompt 同思路处理）

- 用户指令「同思路处理」：两段式 `format_yaml_data_prompt` 与 `response_model.py` 错误消息同样硬编码 `eq: {$.retCode: 0}`（项目特例 0=成功），按同一思路移除。
- `format_yaml_data_prompt`：
  - 示例 1/2 移除 `eq: {$.retCode: 0}` 断言（保留 contains 结构教学）；
  - 铁律 12 移除 `（如 {{eq: {{retCode: 0}}}}）` 特例示例；
  - 新增**铁律 19**：成功/失败断言的期望值取自接口返回定义（字段/取值/语义必须来自「接口定义」返回定义；正向断言成功返回取值、反向断言失败返回取值；无明确取值时退化为 contains 字段存在性或 status_code 断言；禁止臆造固定取值）。
- `response_model.py:517-518`（`validate_validation_not_empty` 错误消息，喂回修复轮）：`{eq: {retCode: 0}}` → `{eq: {$.retCode: <接口返回定义中的成功取值>}}`；导出/下载兜底 `{eq: {retCode: 0}}` → `{contains: {status_code: 200}}`（与铁律 15 一致）。
- 新增测试 `TestFormatYamlPromptNoProjectRetcode` ×2：两段式 prompt 无 `retCode: 0` / `$.retCode` 字面量、含铁律 19。
- 保留不动：`response_model.py:986` 键存在性检查（`keys & {"status_code","status","retCode","code"}`，非值硬编码）。

## 风险

- 新 prompt 无手写示例 → 结构漂移概率上升？由 `TestData.model_validate` + 修复轮兜底（与 dep_map schema 驱动同机制）。
- 断言取值委托接口返回定义：当前 api_returns 缺成功语义（数据缺口 §3-1），模型在返回定义无明确取值时退化为结构/状态断言，成功断言可能不完整——数据补语义后完整；prompt 层不再承载任何项目特定取值。
- `prompt_label` 变更只影响 thinking_trace 标签（无测试断言旧标签）。
