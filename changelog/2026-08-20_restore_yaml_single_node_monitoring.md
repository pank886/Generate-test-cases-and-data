# 恢复单节点 YAML 生成 + 思考内容监测

> 日期：2026-08-20
> 状态：**已实现，验证通过（2026-08-20）**
> 决策：误删恢复（单节点）+ 增强（思考内容监测）

## 背景

- `89c6e4b`（2026-08-12 "phase C优化为单节点thinking+json_mode"）实现了单节点 YAML 生成：thinking + json_object **一次 LLM 调用**生成 TestData，灰度 **single 87/92 > two_stage 83/92**，默认开启。
- `f26ae05`（2026-08-19 "优化了phaseA接口提取结构"）在 Phase A 重构中**误删**了整段单节点代码（yaml_gen.py -316 行：`_generate_one_yaml_single`、`YAML_ANALYSIS_GUIDE`、`gen_func` 接线全部移除），yaml_gen.py 退回两段式单轨。
- 残留**死配置**：`settings.yaml_single_node`（默认 True，settings.py:222）+ `config.YAML_SINGLE_NODE`（config.py:85），**零消费点**——生成永远走 1+1 两段式。trace 实证：`logs/thinking_trace.log` 08-19 生成时段 92 条 `analyze_yaml_data`（两段式阶段1标签），`generate_yaml_data_single` 0 条。

## 决策

1. **恢复单节点功能**（保证单节点可用，`YAML_SINGLE_NODE` 重新生效）。
2. **增加思考内容监测**：单节点 thinking 走 `reasoning_content`（不进 content），历史上"分析过程不可读"（changelog 2026-08-07_phase_c_api_sourcing_plan.md 明确对比过）；本次确保其落入 `logs/thinking_trace.log` 并按节点打标可 grep。

## 架构适配（不能原样恢复）

f26ae05 迁移了架构：旧单节点耦合 `dep_map`/`_api_sequence`/`ApiOps.get_by_url`/注解键 `api_annotations`，均已移除。当前 `_generate_one_yaml` 消费 `api_defs_json`（JSON 快照串）、注解键 `annotations`、无 `_api_sequence`。

→ **不恢复 D4 锚定旧版**；单节点基于当前 `api_defs_json` 架构重写，两段式/单节点**共用注入 + 写盘助手**防漂移。

机制可行性已证：Phase B `_generate_excel_plan_thinking`（nodes.py:90-201）当前就在用 `bind(temperature=0.4, response_format={"type":"json_object"}, extra_body={"thinking":{"type":"enabled"}})` 一步调用；DeepSeek 适配器已把 `reasoning_content` 回补到 `additional_kwargs`（`_log_reasoning_content` + TestReasoningContent 测试锁定）。

## 方案摘要（5 文件）

### 1. `agent_components/generators/yaml_gen.py`
- 模块级常量 `YAML_ANALYSIS_GUIDE`（5 点引导：接口匹配/请求参数/数据传递/断言设计/动态值）。
- 抽取共享助手（`_generate_one_yaml` 内联逐字搬移，行为不变）：
  - `_build_annotation_injector(api_defs_json)` → `_inject_annotations` 闭包（`_lookup_api` 精确/前缀匹配 + 注入 `_annotations`/is_export 占位断言）。
  - `_write_yaml_result(result, output_path)`：路径参数替换 → `_takeover_export_assertions` → 序列化去 `_annotations` → yaml.dump → 原子写盘。
- 新增 `_generate_one_yaml_single`：data_analysis = 引导（首轮 `generate_yaml_data_single` / 修复轮 `repair_yaml_data_single_ROUND{n}` 带错误上下文）；一次 `bind(temperature=0.4, response_format={"type":"json_object"}, extra_body={"thinking":{"type":"enabled"}})` → `_invoke_think(..., reasoning_label=node_label)` → `json.loads` → 注入注解 → `TestData.model_validate` → `_write_yaml_result`。
- `_generate_all_yamls` **两处** `_run_yaml_rounds`（主轮 334 + 后校验修复轮 357-361）传 `gen_func = self._generate_one_yaml_single if config.YAML_SINGLE_NODE else None`；`_run_yaml_rounds` 既有 `gen = gen_func or self._generate_one_yaml`（414）天然回退两段式。

### 2. `agent_components/nodes.py`
- `_invoke_think`（769-772）恢复 `reasoning_label: str | None = None` 并转发 `invoke_think`（89c6e4b 签名，向后兼容）。

### 3. `agent_components/llm_client.py`
- 新增 `_extract_reasoning_content(result)`：统一读 `additional_kwargs["reasoning_content"]`（ChatResult `.generations[0].message` / AIMessage 两路径）。
- 修复 `invoke_think` 内死块（现 `getattr(result, "reasoning_content")` 读不到，实为 no-op）→ 改 `_extract_reasoning_content`；有 `reasoning_label` 记一次 `{label}_thinking`，无则保持 `{label} 思考内容`（**防双写**，既有调用行为不变）。

### 4. `tests/test_phase_bc_unit.py`
- `TestYamlSingleNodeFlag`：monkeypatch `config.YAML_SINGLE_NODE` True/False → stub 三读取器 + patch `_run_yaml_rounds` 捕获 gen_func → 断言 `_generate_one_yaml_single` / None。
- `TestYamlSingleNodeGenerate`：`_invoke_think` mock 返回合法 TestData JSON → 断言 `_invoke_structured` 未调用、yaml 落盘、`reasoning_label` 正确。

### 5. `tests/test_llm_adapter.py`
- `TestInvokeThinkReasoning`：fake LLM 返回 `AIMessage(content=..., additional_kwargs={"reasoning_content": ...})` → 断言有 `reasoning_label` 时落 `{label}_thinking` 且不落 `{label} 思考内容`；无时保持 `{label} 思考内容`。

## 不做

- `config.py`/`settings.py`/`observability.py`/`prompts/extraction_prompts.py` 不动（flag 已存在默认 True；`log_thinking` 现成；`format_yaml_data_prompt` 直接复用）。
- 不恢复 D4 锚定 / `_validate_against_anchor`。

## 验证

```bash
python -m pytest "tests/test_phase_bc_unit.py::TestYamlSingleNodeFlag" "tests/test_phase_bc_unit.py::TestYamlSingleNodeGenerate" -q
python -m pytest "tests/test_llm_adapter.py::TestInvokeThinkReasoning" -q
python -m pytest tests/test_phase_bc_unit.py tests/test_thinking_log.py tests/test_llm_adapter.py -q
python -m pytest tests/test_yaml_db_export.py tests/test_yaml_ref_check.py -q
```

### 实际结果（2026-08-20）

- 定向新测试 3 类 6 条：**6 passed**（`TestYamlSingleNodeFlag` ×2、`TestYamlSingleNodeGenerate` ×1、`TestInvokeThinkReasoning` ×3）。
- 防回归套件（test_phase_bc_unit / test_thinking_log / test_llm_adapter / test_yaml_db_export / test_yaml_ref_check）：**137 passed**，3 条既有 pydantic 收集告警（`TestCaseRow`/`TestData`/`TestCase`，与本次无关）。
- 附加安全网 test_phase_a_analysis + test_phase_c_api：**62 passed, 2 skipped, 1 xfailed**；2 failed 为 `TestConfirmPlanValidation` 的 `httpx.ConnectError`（需启动本地 web 服务，环境性失败，与本次改动无关）。

全绿 = 恢复无回归 + 监测被锁定。可选手工冒烟（需真实 key）：`YAML_SINGLE_NODE=True` 跑一次生成，grep `logs/thinking_trace.log` 中 `*** generate_yaml_data_single_thinking ***` 与 `*** generate_yaml_data_single ***` 块。

## 风险

- `_generate_one_yaml` 共享助手抽取为行为保持的逐字搬移，仍是最重改动 → TestYamlRepairLoop + yaml 相关套件防回归。
- 默认配置行为翻转：`yaml_single_node` 默认 True → 接线后默认路径变单节点（正是本次恢复意图）；依赖两段式的环境设 `yaml_single_node: false` 回退。
- `json.loads` 遇 thinking 泄入 content → 抛错进修复轮（与两段式 json_mode 同机制，`response_format=json_object` 已缓解）。
- 修复轮 data_analysis = 引导 + 4 段错误上下文偏长（89c6e4b 时代已验证可接受）。
