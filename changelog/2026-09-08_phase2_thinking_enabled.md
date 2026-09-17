# 2026-09-08 Phase 2 恢复 thinking=enabled（绕开 8192 输出硬顶）+ 截断兜底方案设计（暂不执行）

> **状态：已决策、施工中（2026-09-08）。** 用户决策链：
> ①取证（4 形态对照 + A2 压测 n=5）→ ②恢复 `enabled` 先解阻塞 → ③生产环境完整验证流程 →
> ④**截断兜底方案仅设计记录，本次不执行**。
> 原则：①先确认②最小改动③连带汇总④方案含测试改完跑⑤文档先行。

## 一、问题与根因（2026-09-08 生产失败）

智慧停车（22 接口 / 92 用例）Excel 计划生成失败，日志 `exception`：
`openai.LengthFinishReasonError ... completion_tokens=8192, prompt_tokens=18860`，截在
`nodes.py` Phase 2（`generate_plan_thinking`）→ 一步生成失败 → 走 requires_review。

**取证（离线 4 形态对照，同一 92 用例大负载，max_tokens 绑 20000/393216）：**

| 形态 | thinking | response_format | 结果 |
|:---|:---|:---|:---|
| A1 | **off** | json_object | `finish=length`，**8192 截断**，`LengthFinishReasonError`（=生产复现） |
| A2 | on | json_object | `finish=stop`，输出 28,906 token、45k 字符完整 JSON |
| B1 | **off** | 无 | `finish=length`，**8192 截断**（带 ```json 围栏） |
| B2 | on | 无 | `finish=stop`，输出 48,550 token 完整 JSON |

**结论：8192 输出硬顶跟 `thinking=off` 走，与 `response_format` 无关，且 `max_tokens` 重绑
无效（服务端硬上限）。`thinking=on` 请求不受限（实测到 49k token，且服务端不执行客户端
max_tokens 钳制）。**

**09-02 误诊修正**：`2026-09-02_phase_b_two_stage_restore.md` 曾把 Phase 2 的 8192 截断判定为
「bind() 丢构造时 max_tokens → 回落 8192」，靠显式重绑 `max_tokens` 修复、第 3 轮 38 条通过即
确认。**实为误诊**：thinking=off 无论 max_tokens 填多少都被钉 8192；38 条只是刚好没超阈值。
两段式当初为躲「双开空 content（09-02 实测 83%）」而让 Phase 2 强制 thinking=off，恰恰落入
8192 硬顶——病①与病②在 thinking 开关上互斥，无「off 且超 8192」的第三配置。

## 二、A2 压测（n=5，同 92 用例负载，thinking=on + json_object）

| 指标 | 结果 |
|:---|:---|
| 空 content | **0 / 5** |
| finish=length | **0 / 5** |
| json.loads 可解析 | 5 / 5 |
| ExcelPlanV2 校验 | 5 / 5（**每次均 13 前置 / 92 用例**） |
| 单次输出 | 29k~49k token / 37k~45k 字符 |

**09-02 的空 content 坑已随模型行为消失**（当前 deepseek-v4-flash 双开不再空）。
→ 决策：Phase 2 恢复 thinking=enabled，一行解阻塞。

## 三、本轮方案（最小改动，施工中）

| 位置 | 改动 |
|:---|:---|
| `agent_components/graph/nodes.py` | Phase 2 `_json_llm.bind` 的 `extra_body` `thinking: disabled → enabled`（保留 `response_format=json_object` 与 `max_tokens`）；同步更新 Phase 1/Phase 2 头注释（清除 09-02 误诊表述、标注 8192 跟 off 走的实证结论、指向本 changelog 兜底） |
| `tests/test_phase_b_two_stage.py` | bind 参数断言 Phase 2 `thinking: disabled → enabled`；docstring/注释同步 |
| `changelog/2026-09-08_phase2_thinking_enabled.md` | 本文档（先行） |

**不改**：prompt 文本（Phase 1/2 均不动，无示例约束不受影响）、graph 链、
`generate_excel_plan` 处理节点、validator/落盘。

**生产验证（判据）**：真实智慧停车 全流程跑通，Phase 2 产出完整 ExcelPlanV2 → 落盘 xlsx；
日志无 LengthFinishReasonError、无空 content。

## 四、截断兜底方案设计（本次**不执行**，服务端行为再调整时启用）

背景：thinking=on 的解是**经验性**的（09-02 空坑在现网消失，但服务端/模型行为可能再调整，
如空 content 复发、或对 thinking=on 也加输出上限）。故设计分级兜底，登记不施工：

### 触发监测（先行可观测，低代价）
- 在 `invoke_think` / Phase 2 处累计两类信号并记日志：**空 content 次数**、
  **`finish_reason=length` 次数**（含各自阈值）。空 content 已由 `invoke_think` 复用输入重试兜底；
  length 目前 thinking=on 不触发，一旦出现即说明服务端对 on 也加了上限。
- 建议阈值（登记）：连续同一批 空≥3 或 length≥1 → 判「服务端行为回退」，转下方案。

### 兜底 A：空 content 复发（thinking=on + json_object 再次高概率空）
- 回退 Phase 2 `thinking=disabled`（避开双开空坑）+ 同时启用兜底 B 的拆批
  （否则 thinking=off 必 8192 截断）。二者必须成对，不能只回退 disabled。

### 兜底 B：拆批 json_object + Python 合并（通用大输出解，thinking=off/on 皆可）
目标：任意规模模块单批输出都压在 8192 输出 token 内。
1. Phase 1 照旧产出全量自由文本分析（三段式、PRE-xxx/TC-xxx）。
2. Phase 2 按接口子集分批：每批覆盖 K 个接口，提示词带「该批接口清单 + Phase 1 对应段 +
   json_schema」，要求只输出该批的 `{shared_preconditions, test_cases}`（子集 JSON）。
   K 的选取：以「单批期望输出 token 距 8192 留余量」为准，可先按接口数试跑校准（参考今晨
   api-extract 按接口切批：单接口批输出远低于上限）。
3. Python 合并：
   - shared_preconditions 跨批**按内容去重**（同内容 PRE 只留一份）；
   - TC 全量**顺序重编号**（TC-001… 连续）；
   - test_cases.preconditions 引用改写为合并后的 PRE 编号；
   - 合并结果过 `ExcelPlanV2.model_validate` 再走既有校验/修复。
4. 注意：跨批共享前置在分批边界可能被重复生成（同内容去重兜住）；「忠实转换」语义需在
   每批提示词重申（禁止省略该批用例），仍无示例。

### 兜底决策边界
- 兜底 B 是**结构恒解**，不依赖 thinking 开关/模型行为 → 若服务端再次不稳定，优先 A+B 成对启用；
- 若仅偶发、非系统性，先不加码（维持 enabled + 空 content 重试），避免过度工程。

## 五、测试设计（本轮）

`tests/test_phase_b_two_stage.py`：
1. bind 参数正确性：Phase 1 无 response_format + thinking enabled（不变）；
   **Phase 2 response_format=json_object + thinking enabled**（断言翻转）。
2. 其余（Phase 1/2 prompt 渲染、两段 wiring、回写 test_point_analysis）不变，跑全量确认。

运行：`pytest tests/test_phase_b_two_stage.py -q` + 相邻回归
（test_thinking_log / test_phase_b_prompt_chain / test_quality_gate 等）。

## 六、文档顺序

本 changelog 先行 → 改代码/测试 → 单测绿 → 生产验证 → 与实况比对；README 本次无用户可见
变更（nodes.py 内部开关），不更新。

## 七、实施记录（2026-09-08）

| 位置 | 改动 |
|:---|:---|
| `agent_components/graph/nodes.py` | Phase 2 `_json_llm.bind` `extra_body` `thinking: disabled → enabled`（保留 json_object + max_tokens）；Phase 1 头注释清除 09-02「bind 丢 max_tokens 回落 8192」误诊表述、标注实证结论；Phase 2 头注释说明改回 enabled 的原因与拆批兜底入口 |
| `tests/test_phase_b_two_stage.py` | bind 参数断言 Phase 2 `thinking: disabled → enabled`；模块/类 docstring 与注释同步（含 8192 跟 thinking=off 走的依据） |
| `tests/test_thinking_log.py` | docstring 同步（Phase 2 现为 thinking enabled；本测试无 bind 断言，仅文档串） |

### 单测结果
- `tests/test_phase_b_two_stage.py` + `tests/test_thinking_log.py`：**11 passed**。
- 相邻回归（test_phase_b_prompt_chain / test_quality_gate / test_enum_prompt_rules /
  test_regression_import_smoke / test_new_node_evaluation）：**46 passed**。

### 生产验证（待补）
服务进程无热重载（`web_app.py` 纯 uvicorn）→ 需重启加载新 nodes.py 后，真实「智慧停车」
全流程跑通判据：Phase 2 无 LengthFinishReasonError、无空 content、产出完整 ExcelPlanV2 → xlsx。

## 八、未覆盖 / 后续可选项（原则③）

- thinking=on 单发无客户端上限，成本/耗时高于 thinking=off（一次可达 49k token）——
  属「换完整输出」的预期代价；若后续要省成本可再评估兜底 B。
- 探针脚本 `tests/tools/_probe_8192_clamp.py`、`tests/tools/_probe_a2_n5.py` 为取证证据，
  保留于 tests/tools（不参与系统运行），可随时删除。
- `invoke_think` reasoning_label 未用于 Phase 2 → Phase 2 的 thinking 草稿只走通用
  `_log_reasoning_content` 记录，无独立标签；如需更细观测可后补。
