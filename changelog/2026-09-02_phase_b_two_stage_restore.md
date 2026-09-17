# 2026-09-02 Phase B 两段式结构恢复

> **状态：用户已决策（2026-09-02），方案定稿，开始施工。** 遵循写作原则：①确定方案先确认 ②最小改动 ③只改相关部分、发现连带项汇总提醒 ④方案设计测试用例改完测试 ⑤先更新文档再看实际情况，实况确认后与文档比对。

> **用户决策（AskUserQuestion，四点全部确认）：**
> 1. **落点**：方法内部两次顺序 LLM 调用（`_generate_excel_plan_thinking` 内），graph 两节点不恢复。
> 2. **Phase 1 函数名**：保留现名 `generate_excel_plan_thinking_prompt`（语义=thinking 分析段）。
> 3. **test_point_analysis 回写**（连带修复项）：Phase 1 分析回写 state。
> 4. **真实 API 验证**：单测通过后跑 `test_new_node_evaluation`。

## 一、问题背景

Phase B 生成节点 `_generate_excel_plan_thinking` 目前是 **thinking + json_object 双开一步生成**（`nodes.py` L168-183）：

```python
_think_llm = self.llm.bind(
    temperature=0.4,
    response_format={"type": "json_object"},
    extra_body={"thinking": {"type": "enabled"}},   # ← 双开
)
```

**实测**：deepseek-v4-flash 下 thinking + json_object 双开 → 高概率空 content（约 83%）。用户重试记录为「2 次空、第 3 次成功」；2026-08-03 稳定性第 5 轮同样空响应。`invoke_think` 的应用层重试只能缓解，不能根治。

**根因（DeepSeek 官方机制）**：thinking 模式下模型存在内部收敛预算，同时被强令输出 JSON 时，长推理未收敛 → 服务端返回 HTTP 200 + 空 content。

## 二、用户要求与设计文档要点

用户指令（两次，逐字）：**「先恢复PhaseB的两段式结构，进行测试，并按文件建议优化」**

"文件建议" = 用户粘贴的两段式架构设计文档，核心：

| 阶段 | 行为 | 关键参数 |
|:---|:---|:---|
| **Phase 1（纯思考）** | 无 JSON 约束的自由推理，输出 reasoning_content（~9452 tokens）+ content | `extra_body={"thinking": {"type": "enabled"}}`，**不设 response_format** |
| **Phase 2（json_object）** | 基于 Phase 1 content，产出结构化 JSON | `response_format={"type": "json_object"}` + **`extra_body={"thinking": {"type": "disabled"}}`（必须显式关 thinking）** |

机制要点：
1. **reasoning_content 在非工具调用场景不进入下一轮上下文**（API 忽略该字段，不算输入 token）→ Phase 2 不会「输入爆炸」。因此 Phase 2 只传 Phase 1 的 content + 新的 user prompt，**不传 reasoning_content**。
2. Phase 2 若忘记显式关 thinking，模型会再次启动思考流程 → 重新踩双开空 content 的坑。
3. 两段式 = 把「重思考」与「结构化输出」解耦：Phase 1 不用边收敛边挤 JSON；Phase 2 关思考专注格式。

## 三、落点决策

**在 `_generate_excel_plan_thinking` 方法内部拆两次顺序 LLM 调用，不恢复 graph 两节点。**

理由：
- `generate_excel_plan` 已于 2026-08-02 方案3 改成**纯处理节点**（校验/修复/消解/落盘），恢复旧的 `analyze_test_points_raw → generate_excel_plan` 两节点会把生成职责塞回处理节点，破坏生成/处理解耦架构。
- 用户设计文档描述的是 **LLM 调用层面的两段式**（Phase 1 → Phase 2），不是 graph 拓扑层面的两节点。
- 改动最小：graph 链 `retrieve_related_data → generate_plan_thinking → generate_excel_plan → END` 不变；质量门禁重试调用 `_generate_excel_plan_thinking(state, gen_warning=...)` 签名不变。

**连带发现（原则③提醒，非新决策点，属修复收益）**：
- 当前一步生成**从不写 `test_point_analysis`**，导致下游 `_prepare_plan_prompt_vars` 的 repair 上下文三段恒为 `（无）`、validator 的 test_analysis 也是 `（无）`。恢复两段式后 Phase 1 天然产出分析文本，应**回写 `test_point_analysis`**，顺带修复 repair/validator 上下文缺失。`State` 已有 `test_point_analysis` 注解（state.py L26），无需新增。

## 四、改动方案

### 改动 1：Phase 1 分析 prompt（纯思考，自由文本）

**函数**：改造现有 `generate_excel_plan_thinking_prompt()` 语义为 **Phase 1 分析段**（保留函数名，减少 test/import 连带改动；命名语义即「thinking 段」）。

- 移除：JSON Schema 注入块、「严格按 JSON Schema 输出/禁止 Markdown/只输出纯 JSON」
- 保留：全部设计规则（逆向 1/3、断言规范 [eq]/[contains]/[ne]/[db]、共享前置引用规范、思维链三段式、枚举不写死、用例设计规范、强令使用上下文、只使用提供信息）、输出前柔性自检
- 输出要求改为：**三段式自由文本分析报告（`## 测试场景分析` / `## 共享前置` / `## 测试用例`），共享前置用 PRE-xxx、用例用 TC-xxx 展开完整步骤/预期（含断言标签），不要输出 JSON**
- 占位符仍含：`{module_analysis} {api_definitions} {related_docs} {user_context} {db_schema} {gen_warning}`；去掉 `{json_schema}`
- ⚠️ 不新增任何示例（用户约束）；沿用历史 analyze 三段模板，但不引入历史 `<reference>` 黄金示例

### 改动 2：Phase 2 格式化 prompt（新函数）

**新增** `generate_excel_plan_format_prompt()`（Phase 2 结构化输出段）：

- system：数据转换专家角色 + JSON Schema 注入（`{json_schema}`）+ 字段硬约束（story 五字段、steps/expected 字符串化且条数一致、preconditions 为 PRE 编号数组、mutates_data/is_negative_test 布尔、shared_preconditions 四字段）+「忠实转换」要求：**只把分析报告中已设计的 PRE/TC 逐个落入 JSON，禁止省略/新增/改写用例语义**
- human：`{analysis_text}`（Phase 1 全文）+ `{api_definitions}`（URL 溯源校对用）+ `{db_schema}` + `{gen_warning}`
- ⚠️ 不新增任何示例（用户约束）

### 改动 3：`_generate_excel_plan_thinking` 拆两段调用

```python
# ── Phase 1：纯思考自由文本分析（无 JSON 约束）──
llm_think = self.llm.bind(temperature=0.4,
                          extra_body={"thinking": {"type": "enabled"}})
analysis = self._invoke_think(llm_think, msgs_analysis,
                              label="analyze_test_points_raw",
                              reasoning_label="analyze_test_points_raw")
log_thinking("analyze_test_points_raw", ..., analysis,
             prompt_label="generate_excel_plan_thinking")

# ── Phase 2：json_object 结构化输出（显式关 thinking）──
llm_json = self.llm.bind(temperature=0.4,
                         response_format={"type": "json_object"},
                         extra_body={"thinking": {"type": "disabled"}})
text = self._invoke_think(llm_json, msgs_format,
                          label="generate_plan_thinking")
log_thinking("generate_plan_thinking", ..., text,
             prompt_label="generate_excel_plan_format")
plan = ExcelPlanV2.model_validate(json.loads(text))
```

- Phase 1 输出（analysis）作为 Phase 2 human 的 `{analysis_text}`；reasoning_content 不进 Phase 2（LangChain 不携带历史消息，天然满足「不传 reasoning_content」）
- 返回 dict 追加 `"test_point_analysis": analysis`（修复下游 repair/validator 上下文，见三）
- 其余返回字段（excel_plan/plan_source/api_full_for_snapshot/module_tree_json）不变
- 数据准备段（模块分析/API 概要/模块树/关联模块）不变，两段共用

### 改动 4：不动的部分

- graph_builder.py 节点链、`generate_excel_plan` 纯处理节点逻辑、validator/消解器/落盘 —— **一行不改**
- `repair_excel_plan_prompt` —— 不动（它已含 JSON 结构约束，修复轮保持原样）

## 五、测试用例设计

**新增 `tests/test_phase_b_two_stage.py`：**

1. **Phase 1 prompt 渲染**：`generate_excel_plan_thinking_prompt` 无 `{json_schema}`、无「只输出纯 JSON/禁止 Markdown 输出 JSON」字样；含三段关键词（测试场景分析/共享前置/测试用例）；含自由文本输出要求；保留设计规则与柔性自检；无示例字面量
2. **Phase 2 prompt 渲染**：`generate_excel_plan_format_prompt` 含 `{json_schema}` 与字段硬约束（story 五字段、PRE 数组）；human 含 `{analysis_text}/{api_definitions}/{db_schema}/{gen_warning}`；无示例字面量
3. **两段调用 wiring**：mock `_invoke_think` side_effect=[analysis_text, plan_json] → 断言第 1 次调用 label=analyze_test_points_raw、第 2 次 label=generate_plan_thinking；解析取自第 2 次文本；返回含 test_point_analysis=analysis_text；plan_source="thinking"
4. **bind 参数正确性**：mock `llm.bind` 捕获 kwargs → 第 1 次 bind 无 response_format + thinking enabled；第 2 次 bind 有 response_format json_object + **thinking disabled**
5. **日志**：mock `log_thinking` → Phase 1/Phase 2 各写一条（node 与 prompt_label 断言）

**受影响的既有测试（需同步更新）：**

| 文件 | 影响 | 处理 |
|:---|:---|:---|
| `tests/test_thinking_log.py` | monkeypatch 单步 `_invoke_think`，断言单条日志 | 改为 side_effect 两步 + 两条日志断言（monkeypatch 两个 prompt 函数为简单模板） |
| `tests/test_phase_b_prompt_chain.py` | 断言内容在设计规则段（保留在 Phase 1） | 预期**无需改**，跑一遍确认 |
| `tests/test_enum_prompt_rules.py` | 枚举规则在设计规则段（保留在 Phase 1） | 预期**无需改**，跑一遍确认 |
| `tests/test_quality_gate.py` | monkeypatch 整个 `_generate_excel_plan_thinking` | 不受内部拆分影响，跑一遍确认 |
| `tests/test_new_node_evaluation.py` | 真实 API 调 `_generate_excel_plan_thinking` | 不受接口影响（耗时翻倍），跑一遍确认 |

## 六、验证方式

1. 单测：新增 `test_phase_b_two_stage.py` + 全量回归相关文件
2. （可选，需真实 API）跑 1-2 次 `test_new_node_evaluation`，确认两段式产出正常、空 content 消失

## 七、待用户确认点

1. **落点**（推荐）：方法内部两次调用，graph 两节点不恢复 —— 是否同意？
2. **Phase 1 函数名**：保留 `generate_excel_plan_thinking_prompt` 名（语义=thinking 分析段），避免改 import —— 是否同意？
3. **test_point_analysis 回写**（连带修复项）：Phase 1 分析回写 state，修复下游 repair/validator 上下文恒为「（无）」—— 是否同意？
4. 是否在改完后跑一次真实 API 生成验证（耗时较长）？

## 八、文档更新

- 本 changelog 施工结果追加（方案确认后）
- `docs/fixes_summary.md` 归档追加（按 CLAUDE.md 约定）

## 九、施工结果（2026-09-02，方案已全部落地）

### 施工中发现并补全的方案差距（原则③汇总）

1. **真实 API 第 2 轮暴露 Phase 2 输出截断（8192 tokens）**：
   `thinking disabled` 的请求若未在 `bind()` 里显式 `max_tokens`，构造时注入的
   `config.LLM_MAX_TOKENS` 会因 LangChain 链式 `bind()` 丢失 → 回落模型默认 8192
   → 大计划 JSON 截断（`Could not parse response content as the length limit was reached`,
   `completion_tokens=8192`）。**修复**：Phase 1 / Phase 2 的 `bind()` 均显式加
   `max_tokens=config.LLM_MAX_TOKENS`（settings.py L146-148 注释本就要求"每次 API 调用时
   bind"）。复测第 3 轮 38 条用例正常产出，证明截断消除。
2. **连带发现（只提醒不处理）**：其他 `thinking disabled` 的大型 JSON 输出路径
   （如 `invoke_structured` json_mode 走 `extra_body thinking disabled`）理论上同受
   8192 回落影响，当前未达阈值（结构体输出偏小）暂不处理；后续若某节点输出膨胀，
   按同一模式在 `bind()` 显式补 `max_tokens` 即可。
3. **Phase 1 自由文本无界 → 轮次间规模方差大**：第 1 轮设计 16 条、第 3 轮 38 条、
   失败轮 35+ 条。属模型固有方差（旧一步生成同样存在），非本次回归；记录备查。

### 代码变更清单（全部已落地）

| 文件 | 变更 |
|------|------|
| `prompts/extraction_prompts.py` | `generate_excel_plan_thinking_prompt` 改为 **Phase 1 纯思考自由文本段**：去掉 JSON Schema 注入/「只输出纯 JSON」，保留全部设计规则与柔性自检，末尾加「三段式自由文本输出要求（## 测试场景分析 / ## 共享前置 / ## 测试用例，PRE-xxx/TC-xxx 展开步骤+断言标签），不要输出 JSON」；新增 **`generate_excel_plan_format_prompt`（Phase 2）**：json_schema 注入 + 字段硬约束（story 五字段、steps/expected 字符串、PRE 编号数组、布尔）+ 忠实转换要求，无示例 |
| `agent_components/graph/nodes.py` | `_generate_excel_plan_thinking` 拆 **Phase 1（thinking enabled、无 response_format，label=analyze_test_points_raw）→ Phase 2（json_object + thinking disabled，label=generate_plan_thinking）** 两次顺序调用；两段 `bind()` 显式 `max_tokens`；两条 `log_thinking`（Phase 1 prompt_label=generate_excel_plan_thinking / Phase 2 prompt_label=generate_excel_plan_format）；返回 dict 追加 **`test_point_analysis`**（修复下游 repair/validator 上下文恒为「（无）」）；reasoning_content 由 `invoke_think` reasoning_label 采集，不进 Phase 2 |
| `tests/test_thinking_log.py` | 改为两步 `_invoke_think` side_effect + 两条日志断言（Phase 1/Phase 2 node 与 prompt_label）；断言回写 `test_point_analysis` |
| `tests/test_phase_b_two_stage.py` | **新增**：Phase1/Phase2 prompt 渲染（无 json_schema/纯 JSON 字样、三段式输出、规则与自检保留、无示例字面量）、两段 wiring（两次调用 label + 解析取自 Phase 2 + 回写）、bind 参数正确性（Phase 1 无 response_format+thinking enabled / Phase 2 json_object+thinking disabled，且都显式 max_tokens） |

### 测试结果

- 相关单测 **11 passed**（test_phase_b_two_stage 10 + test_thinking_log 1）；回归
  test_enum_prompt_rules / test_phase_b_prompt_chain / test_quality_gate /
  test_regression_import_smoke 均通过。
- **全量 tests/ 回归：703 passed / 9 failed（与 2026-09-01 基线 9 个既有失败完全一致，
  本次新增失败 0）**。9 个失败全在 test_phase_bc_unit（YAML 单节点 prompt 断言 +
  TestResolveApiDefs SQL/DB 状态），单独运行同样失败、与本改动无关。

### 真实 API 验证（test_new_node_evaluation，模块=智慧用电）

| 轮 | 结果 | 数据 | 说明 |
|----|------|------|------|
| 1 | ✅ OK | 16 cases / 3 pres / pos7·neg9 / 覆盖 100% | 两阶段 200，无空 content |
| 2 | ❌ FAIL (119s) | — | **截断复现**：Phase 2 thinking disabled 输出落回 8192 → 截断（见上差距 1） |
| 3（修复后） | ✅ OK (94s) | 38 cases / 6 pres / pos12·neg26 / 覆盖 100% / 幻觉 URL 0 / 空步骤·空预期 0 / 无效前置引用 0 | 38 条 > 截断点，证明 max_tokens 显式 bind 修复生效 |

两段式空 content 根因（thinking+json_object 双开约 83%）已消除：三次运行 Phase 1/Phase 2
均 200 + 有效 content；唯一失败是输出上限截断，已修复并锁入单测（bind 参数断言含 max_tokens）。
