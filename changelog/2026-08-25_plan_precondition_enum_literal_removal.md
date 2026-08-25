# 计划：前置条件不写死枚举字面量 —— 修复「前置字面量压过 desc」的根因方案

> 日期：2026-08-25（改版：原「body 枚举字段 value 回填」方案废弃，改用根因方案）
> 状态：**已实施并复核通过（2026-08-25）**：断言块键白名单 + api_analysis 枚举标注 + 两处 Phase B prompt 规则 + 智慧用电_36 存量前置修复（3 格 meterDeviceType=1 → 取枚举）。单元测试 34 项通过；全量 662 通过 / 7 失败（TestResolveApiDefs，url 前缀环境失配，与本批无关）。**智慧用电_37 整轮生成复核**：meterDeviceType 0 处 '1'（单相 20/三相 3）、断言 0 非法块、url 全对、负向非法枚举（meterTypeCode='3'）保留、test_plan 前置遵守「取枚举（…）」新规则。
> 观察项（既有模式，非本批引入）：teardown 仍为 POST /add 而非逆向删除，36 原版亦然，如需处理单独立项。
> 关联：[断言块键校验缺口](2026-08-25_validation_assertion_block_operator_key_gap.md)（同一批实施）

## 背景与证据

智慧用电_36 电表新增的 `meterDeviceType` 生成值始终是 `'1'`，而接口文档要求"直接传中文（单相/双相/三相）"。三处事实（2026-08-25 查证）：

1. **前置条件把字面量写死在步骤文字里**（test_plan.xlsx PRE-001）：
   `...电表类型 meterDeviceType=1...` —— 原样进入 Phase C prompt 的「B 用例内容（执行步骤）」段。
2. **接口定义里枚举只在 desc，`value` 为空**：body 字段 `{"desc": "电表类型，直接传中文，区分是 ：单相，双相，三相", "value": ""}`。
   `ingest/api_parser.py` 只在源含「示例/参数值」列或 YApi `value` 字段时才填 `value`；YApi JSON Schema 来源的 body 字段只有 desc → `value` 恒空。
3. **teardown 结构性必踩雷**：teardown 输入 = `根据 PRE-001 的创建步骤逆向操作：{前200字}`，前 200 字含 `meterDeviceType=1`。4 轮 A/B teardown 全错；setup 掷硬币（OLD#1 侥幸 3 错，其余 6 错）。

**根因**：prompt 的 `value > desc > 用例值` 优先级中，最强一支 `value` 从不触发（body 字段 value 全空）；desc 给的是"解析+翻译"任务，用例值给的是"照抄"任务——照抄必赢解析。prompt 层措辞改多少遍都没用，只能从**生成前置条件的源头**去掉字面量，并在**接口分析层**把枚举取值显式化，让 Phase B 有据可依。

## 方案（根因修复）

### Part 1：接口映射分析（api_analysis）输出补充枚举取值 —— 先补数据源

**位置**：`prompts/extraction_prompts.py` `analyze_api_mapping_prompt`（L560，3 步分析 Step 3，产出 `api_analysis` 自由文本）。

**变更**：在分析要求的「关键约束」段增加——对每个**写接口**（POST/PUT/PATCH）的 body 字段，标注哪些是**枚举字段及其合法取值**，如：
```
meterDeviceType：单相/双相/三相
accessMethod：网关接入/电表直连/平台对接
meterTypeCode：1/2/3
```
并进已有"接口映射分析"输出，**不新增独立清单结构**。

**数据流（已有链路，零新增结构）**：
`api_defs_raw`（含 body/desc，`web/tasks.py:681` 注入）→ `analyze_api_mapping_prompt` → `api_analysis`（存 `ModuleAnalysis` 表）→ `_generate_excel_plan_thinking` 组装 `module_analysis`（`nodes.py:121-128`，`### 接口映射分析` 段）→ Phase B `{module_analysis}`。`retrievers.py:576` 拼装的"接口映射分析"同样受益。

**为什么先补数据源**：Phase B 的"不写死枚举字面量"要落地，前提是知道**哪些字段是枚举、合法值是什么**——否则前置只能写"字段名"，写不出"取值来源（单相/双相/三相）"，且「多个用例（>2）需以不同值区分」的例外场景无值可用。

### Part 2：Phase B 生成前置条件不写死枚举字面量 —— 只约束枚举/取值字段

**位置**（生成前置步骤的两处 Prompt）：
1. `prompts/definitions.py` `generate_excel_plan_thinking`（L178，Phase B 首轮生成）
2. `prompts/extraction_prompts.py` `repair_excel_plan_prompt`（L78，Phase B 修复轮）

**新增规则（写入两处 Prompt 的 system 段）**：
- **只约束枚举/取值类字段**（值来自接口文档 desc 的字段，如 `meterDeviceType`=单相/双相/三相、`accessMethod`=网关接入/电表直连/平台对接、`meterTypeCode`=1/2/3）——前置与步骤中**不写死具体值**，只写字段名 + 取值来源（引用接口映射分析中的枚举清单），如 `电表类型 meterDeviceType 取枚举（单相/双相/三相）`。
- **标识/引用类字段**（`code`/`name`/`sceneCode` 等被跨用例引用的）**不受约束，保留具体值**（如 `code=E_METER_001`）。
- 例外：**仅当需要多个用例（>2）以不同枚举值区分场景时**，才允许在前置/步骤中写出具体枚举值。

**为什么是根因修复**：
1. 字面量消失 → LLM 没得照抄 → 只能查接口映射分析/desc → 取合法枚举；
2. **多样性保留**：无 value 强覆盖，不同用例可自然取不同枚举；
3. **teardown 结构性自愈**：teardown 输入 = PRE 步骤前 200 字，步骤不再含字面量；
4. 无启发式解析、无误伤；与现有规则一致——`definitions.py` 已写明「具体参数值属 Phase C 数据层，这里只描述操作意图」，现规则只禁完整 JSON 请求体、未禁 `field=literal`，本次补全。

**为什么放弃 value 回填**：`value` 单值 + `value > desc > 用例值` 最高优先级 → 永远是第一个枚举（"永远只填第一个"），且强覆盖所有正向用例，无法生成需要不同枚举的用例；只适用于"字段永远用默认枚举"的窄场景。手动/自动填 value 均有此问题，不如从源头不产生冲突字面量。

## 存量计划处理（关键决策点）

方案只约束**未来生成**的计划；智慧用电_36 的 test_plan.xlsx 已含 `meterDeviceType=1` 字面量。处理方式：

- **推荐**：对 36 做**前置定向修复**——Phase B 修复轮只重写 PRE-001/PRE-002 的 steps 去掉枚举字面量，不动 36 个 YAML、不动用例本身（符合「不改已落盘 YAML」约束）。
- 备选：全量重跑 Phase B 重新生成 test_plan.xlsx（重，改变整个计划，慎用）。
- 兜底：若验证后仍有照抄残余，再上写盘前对已知枚举字段确定性归一（维护「字段→合法值集合」映射）。

## 一并实施（同一批）

1. **断言块键白名单校验**：见 [gap 文档](2026-08-25_validation_assertion_block_operator_key_gap.md) —— `validate_validation_element_is_dict` 增补 `op not in (eq,contains,ne,db) → 校验失败进修复轮`；`YamlPostValidator` 补 P0 兜底。
2. **`analyze_api_mapping_prompt` 输出补枚举取值**：`api_analysis` 关键约束段增加 `字段名：枚举值1/枚举值2/...` 标注。
3. **Phase B 两处 Prompt 加"枚举字段不写死字面量"规则**：`definitions.py` + `extraction_prompts.py`。
4. **存量前置定向修复**：对智慧用电_36 test_plan.xlsx 的 PRE-xxx steps 去枚举字面量。
5. **单元测试**：
   - 断言白名单：`{$.retCode: {eq: 1}}`、`{status_code: 200}` 单键块被模型拦截（`tests/test_phase_bc_unit.py` TestYamlOutputHygiene）；
   - Prompt 渲染：`analyze_api_mapping_prompt` 渲染后含"枚举字段取值"标注要求；两处 Phase B prompt 渲染后含"枚举字段不写死字面量"规则要点。

## 验证

- 单元测试全绿（新增白名单 + Prompt 渲染 + 既有 `TestYamlSingleNodePromptRender`）。
- 真实跑一轮智慧用电_36（复用 `logs/prompt_ab_run.py`，输出 `logs/prompt_ab/V4`）：
  - setup/teardown `meterDeviceType` 全部为合法中文枚举（单相/双相/三相），**0 处 `'1'`**；
  - 断言结构 0 非法块；url 前缀全对；负向用例非法枚举（`meterTypeCode='3'` 等）不受影响。
- 抽查智慧用电模块的 `api_analysis`（接口映射分析）含 `meterDeviceType：单相/双相/三相` 等枚举标注。
