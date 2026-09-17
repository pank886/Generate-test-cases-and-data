# 2026-09-09 Phase B 修复轮 json_mode 8192 截断：结构化输出放行 thinking + URL 候选边界收紧

> **状态：方案 A+B 已施工并测试通过（2026-09-09）。** 决策链：
> ①方案C（主模块接口全量直取）生产验证：智慧停车 Hop2b 直取 39 接口、两段式生成 **23 前置 /
> 100 用例**（用例量回到 ≥90 ✅）→ ②首轮校验 98/100 → ③**修复轮**（`_invoke_structured`
> method="json_mode"）被 8192 输出硬顶截断，内层重试 2 次仍失败 → Phase B 整体失败。
> ④本 changelog 记录根因；用户 2026-09-09 确认 **A+B 成对**实施。
> 原则：①先确认②最小改动③连带汇总④方案含测试、改完运行⑤文档先行。

## 一、现象（生产 2026-09-08T11:31 UTC，trace 7addd178…）

```
❌ Phase B 工作流执行失败: LLM 结构化输出校验失败（本调用内重试 2 次，外层修复轮独立计数）:
   Could not parse response content as the length limit was reached -
   CompletionUsage(completion_tokens=8192, prompt_tokens=24417, …)
```

上下文：方案C 后 api_definitions=39 → 两段式生成 23 前置 / 100 用例成功；首轮校验 98/100，
8 行失败 → 进入**修复轮** → 修复结构化调用（json_mode）thinking 强制 off → 输出 >8192 被截。

## 二、根因

| # | 事实 | 证据 |
|:---|:---|:---|
| 1 | **修复轮走 `_invoke_structured(method="json_mode")`**，而 `METHOD_FEATURES`（nodes.py:45-50）把 `json_mode/json_schema` 标为 `supports_thinking=False` → client.py:169-172 **强制 thinking=disabled** | client.py + nodes.py:423 调用（未传 thinking） |
| 2 | 8192 输出硬顶**跟 thinking=off 走**（2026-09-08 已实证）；修复轮 prompt 要求重出完整 ExcelPlanV2（23 前置 + 失败用例），输出超 8192 → `LengthFinishReasonError` | 日志两次 LengthFinish，completion_tokens 恒 8192 |
| 3 | 两段式 Phase 2 用**手动 bind(json_object + thinking enabled)** 绕过该方法特性表，故能出 100 用例；修复轮仍被表锁死，**是 09-08 thinking=enabled 修复漏网的同类调用** | Phase 2 成功 vs 修复轮失败同一次运行并存 |
| 4 | 触发修复的 8 行里 **7 行是 URL 校验误报**：步骤文本「parkCode/**placeCode** 取 MAIN-A」「denominationHour/**denominationMinite** 取…」中的**字段名斜杠**被 `_URL_RE` 切出伪路径 `/placeCode`、`/denominationMinite` | thinking_trace + case_validator.py:92 |
| 5 | 8 行里仅 TC-082 `/parkDevice/add` 为**真实笔误**（库内为 `/parkingDevice/add`，无 `/parkDevice`） | SQLite 查询 |

> 即：主模块直取已解决用例量与整批 URL 误报；剩余两类问题——
> **A. 修复轮 json_mode 被 8192 顶死**（结构性阻塞）；**B. URL 候选把「字段名 a/b」误判成路径**
> （把本可 98% 首轮达标的计划拖进修复轮、制造噪音，且修复重出仍会复现同一斜杠）。

## 三、修复方案（A 主修 + B 消噪，推荐成对）

### 方案 A：json_mode/json_schema 结构化输出放行 thinking（修复轮不再被 8192 顶死）
- `agent_components/graph/nodes.py` `METHOD_FEATURES`：`json_mode`、`json_schema` 改
  `supports_thinking: True`（`function_calling` 保持 False：函数调用 + 深度思考可能冲突，无需动）。
  依据：09-08 A2 实证 thinking=on + json_object 5/5 零空 content、可输出 28k+ token；两段式
  Phase 2 已同配置跑通 100 用例。
- 修复轮调用（nodes.py:423）补 `thinking=True`，让它走 enabled 分支。
- **波及**：其余 json_mode 调用方（意图识别 retrievers:401、入库提取 pipelines、翻译）均未传
  thinking=True → 维持 off 不变，仅修复轮变化。`config.ENABLE_THINKING=True` 已满足 gate。
- 效果：修复轮输出不再受 8192 顶，可完整重出被修计划（同 Phase 2 上限 ~49k token）。

### 方案 B：URL 候选只认「词边界处的 / 」，字段名斜杠不再切出伪路径
- `agent_components/validation/case_validator.py:92` `_URL_RE`：
  `(?<!<)/…` → `(?<![A-Za-z0-9_<])/…`。
- 效果：`parkCode/placeCode`、`denominationHour/denominationMinite` 等**字段名连写**不再产生
  `/placeCode`、`/denominationMinite` 伪候选（斜杠前是 ASCII 字母，被边界后顾排除）；
  真实接口路径（前为空格/标点/中文）不受影响；HTML `</script>` 的 `<` 排除仍保留。
- **不改变**既有「sharp/peak/flat/valley 枚举仍命中」的保留现状（其斜杠前为空格/非字母，
  仍会命中，与 test_workflow_init 既有断言一致）。
- 效果：本批 8 行失败里 7 行误报消除 → 首轮 99/100 只剩真实笔误 TC-082 需修复，修复轮输出量
  大减（成本/时长下降），且不会因同一斜杠反复拉低通过率。

### 不改 / 不扩
- 修复轮 prompt 语义（仍重出完整计划 + 代码侧按 failed_ids 裁剪）不改——A 已解除其输出上限。
- 若 A 落地后仍想进一步省成本，另立「修复轮只输出失败行子集」的小方案，本轮不做。

## 四、测试设计

1. **METHOD_FEATURES 门**（nodes.py）：断言 `json_mode`/`json_schema` supports_thinking=True、
   `function_calling`=False（回归守卫）。
2. **client 门**：`invoke_structured` method="json_mode" + thinking=True → bind 的 extra_body
   为 `{"thinking": {"type": "enabled"}}`；thinking=False 仍 off（mock chain，参照
   test_phase_b_two_stage bind 断言风格）。
3. **URL 候选边界**（test_plan_validator.py）：`extract_url_paths("…parkCode/placeCode 取
   MAIN-A")` 不含 `/placeCode`；`denominationHour/denominationMinite` 不含 `/denominationMinite`；
   真实路径 `/parkingDevice/add` 仍提取且 check_urls 命中；`/parkDevice/add`（笔误）仍报。
4. 回归：test_plan_validator / test_phase_b_two_stage / test_thinking_log / test_quality_gate /
   test_retrieval_api_dedup / test_hob2_main_module_full_fetch 等相邻。

## 四·五、实施记录（2026-09-09）

| 方案 | 位置 | 改动 |
|:---|:---|:---|
| A | `agent_components/graph/nodes.py` `METHOD_FEATURES` | `json_mode`/`json_schema` 改 `supports_thinking: True`（`function_calling` 保持 False），注释记录 09-09 实证依据 |
| A | `agent_components/graph/nodes.py` 修复轮 `_invoke_structured` | 补 `thinking=True`，走 enabled 分支 |
| B | `agent_components/validation/case_validator.py` `_URL_RE` | `(?<!<)` → `(?<![A-Za-z0-9_<])`（词边界后顾），注释记录 |
| 测试 | `tests/test_plan_validator.py` | 新增 `test_excludes_field_name_slash_list`（字段名斜杠不再切出伪路径）+ TestCheckUrls 两则（字段名斜杠不误报 / 真实笔误 `/parkDevice/add` 仍报） |
| 测试 | `tests/test_phase_b_repair_round_thinking.py` | 新增：METHOD_FEATURES 门（json_mode/json_schema True、function_calling False）+ client 门（json_mode + thinking=True → `extra_body={"thinking":{"type":"enabled"}}`；未传 thinking → 不附加；ENABLE_THINKING=False 熔断） |

**单测结果**：`test_plan_validator.py + test_phase_b_repair_round_thinking.py` **41 passed**；
相邻回归 test_phase_b_two_stage / test_thinking_log / test_quality_gate / test_retrieval_api_dedup /
test_hob2_main_module_full_fetch / test_workflow_init / test_llm_adapter **71 passed**。
（`test_phase_bc_unit.py` 9 failed 仍为既有未提交工作区问题，见 §五，与本方案无关。）

## 五、波及面 / 未覆盖（原则③）

- `function_calling` 不支持 thinking 的表意未变；Phase C YAML 走 `thinking + json_object` 手动路径
  （generators），不受 METHOD_FEATURES 影响。
- `tests/test_phase_bc_unit.py` 9 failed 为既有未提交工作区问题（已在 schemeC changelog §九 登记），
  与本方案无关。
- 服务无热重载：需重启后按生产判据验证（智慧停车 首轮通过率提升、修复轮不再 LengthFinish、落盘）。

## 六、生产验证判据

重启后真实「智慧停车」：方案C 直取 39 接口 → 生成 ≥90 用例 → 首轮 URL 误报收敛到只剩真实笔误
（若有）→ 修复轮无 `LengthFinishReasonError`（completion 不再钉 8192）→ 通过率达标落盘 xlsx。
