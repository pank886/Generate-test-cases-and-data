# 变更计划：Phase C YAML 质量治理 — 规整/重生成两分法 + 批量自查修复循环

| 项目 | 内容 |
|:---|:---|
| 变更日期 | 2026-07-18 |
| 变更类型 | 生成质量治理（校验分级 + 修复循环 + 日志补全） |
| 涉及文件 | `prompts/response_model.py`, `prompts/extraction_prompts.py`, `data_factory/methods.yaml`（补全）, `data_factory/registry.py`（新增加载层）, `agent_components/generators.py`, `agent_components/nodes.py`（_load_factory_methods 薄壳化）, `settings.py`, `config.py`, `tests/test_phase_bc_unit.py`, `tests/test_phase_c_api.py` |
| 权威规范 | `chongelog/YAML_SPECIFICATION.md` |
| 状态 | ✅ 已实施（2026-07-18，单元测试 95 通过；E2E 按 §8-2 待服务重启后手动触发） |

---

## 1. 背景与根因

2026-07-18 对 `testcase/园区基线/健身房_4` 的 Phase C 全量生成（63 个 YAML）评审发现五类缺陷：

| 缺陷 | 实例 | 根因 |
|:---|:---|:---|
| 占位符幻觉 | `'{{(get_current_time(ymd) + 1day)}} 11:00:00'` | 框架 `replace_load()` 只解析 `${`；当时函数库无时间偏移能力，LLM 因"需要明天"而发明语法（框架现已新增 `get_offset_time`，见 §6.4——函数库会实时演进，治理必须以注册表为单一事实源，而非硬编码死名单） |
| 类型漂移 | `input_extract: {subscribeId_active: 1}`（int） | inline 重试 3 次均重复同一错误（json_mode 无思考，原地重打无法纠正"信念型错误"） |
| 空文件 | `data: []` 写盘且计为成功 | 模型无 min_length 约束，"成功"仅代表无异常 |
| 格式噪音 | 无 header / 空 `params:{}` / 同类断言拆条 | prompt 示例不完整 + 无输出规整 |
| 生成过程黑盒 | Phase C 思考全文无日志 | `_generate_one_yaml` 阶段1 thinking 未写 `thinking_trace.log` |

**治理原则**（用户裁定）：
- 语义无歧义的格式约定 → 代码静默规整（清单 A）
- 语义性错误 → 不做静默兜底，登记后集中送思考节点自查重生成（清单 B）
- 宁可缺文件 + 明确报错，不要"合法但错误"的文件

---

## 2. 清单 A — 规整类（代码静默修正，不回炉）

| # | 规则 | 动作 | 依据 |
|:---|:---|:---|:---|
| A1 | method 大写 | 转小写 | 规范附录 B |
| A2 | url 带域名 | 截取 path + WARNING | 规范附录 B |
| A3 | header 缺失 | json 体补 `Content-Type: application/json;charset=UTF-8`；仅 params（GET）/文件上传不补；公共头（yq-app-code/token）由框架常量注入，一律不生成 | 规范 §2（已按用户裁定修订） |
| A4 | 表单体判定 | header 明确 `x-www-form-urlencoded` → `data` 为合法表单体保留输出 | 规范 §4.3 条件合法 |
| A5 | data 字段漂移 | 无表单 CT 时 `data`→`json` 迁移 + 漂移率统计 | 既有行为 |
| A6 | 同类断言拆条 | 合并为一条（`eq{a}`+`eq{b}`→`eq{a,b}`）；同字段不同期望值为真冲突，保留独立 | 规范 §7.1 语义等价 |
| A7 | 空 `{}` 占位 | extract/input_extract/extract_list 一律剔除；params 仅当已有 json/data 请求体时剔除（无请求体的 GET 保留 `params: {}` 满足三选一） | 规范 §3 |

> A 类实现均已存在于 `response_model.py`（normalize_base_info / migrate_data_to_json / merge_same_type_validations / strip_empty_optional_dicts），本次仅保留，不改动。

## 3. 清单 B — 重生成类（登记 → 轮末自查 → 重生成）

| # | 错误 | 校验位置 | 现状 → 改动 |
|:---|:---|:---|:---|
| B1 | `{{}}` 双花括号占位符 | `TestData.validate_placeholders`（已写好） | raise 后由 inline 重试改为登记回炉 |
| B2 | `${}` 内运算/拼接/未闭合/嵌套 | 同上 | 同上 |
| B3 | 非白名单占位符函数 | 同上，白名单来自 `data_factory/methods.yaml` **既有注册表**（§6.4，需补全至 6 个方法），当前：`random_plates` / `get_extract_data` / `get_extract_data_list` / `get_current_time` / `get_offset_time` / `split_extract_data` | 同上 |
| B4 | 占位符实参不符合注册表规则（如 fmt 非 `ydm`/`hms`、实参个数越界） | 同上，规则读 methods.yaml 各条目的 `validation` 块 | 同上 |
| B5 | extract/input_extract/extract_list 值为 int/float/bool | **撤销现行 int→str 强转**，改为 raise | "1" 未必是本意，强转产出"合法但错误"文件 |
| B6 | 文件级空输出 `data: []` | `TestData.data` min_length=1（已有） | 失败登记而非 inline 重试 |
| B7 | 块级空用例 `testCase: []` | `StepData.testCase` min_length=1（已有） | 同上 |
| B8 | 结构性解析失败（缺 case_name / 类型错 / JSON 坏） | LangChain + Pydantic | 同上 |
| B9 | json/params/data 多个非空并存 | **新增** `TestCase.validate_body_exclusivity`（mode=after，A7 剔空后判非 None 计数 > 1 → raise） | 用户裁定：回炉（代码删哪个都是猜） |
| B10 | extract 系字段含 null 值条目 | **撤销现行"丢弃 None 条目"**，改为 raise | 用户裁定：回炉；配套 prompt 规则"无需提取就省略字段" |

---

## 4. 修复循环设计（`_generate_all_yamls` 改造）

### 4.1 流程

```
第 1 轮（全量，并发 YAML_CONCURRENCY）
  每个任务: analyze(thinking) → format(json_mode, 无 inline 重试)
    ├─ 校验通过 → 原子写盘
    └─ 校验失败 → 不写盘，登记: {占位ID, case_id, yaml相对路径, round,
                                  error全文, 原始输出片段(≤500字), 任务row}
轮末（有失败且未达轮次上限）
  错误模式摘要 = 按 B1..B10 类别聚合计数（跨文件模式反馈）
  对每个失败项: repair_yaml_data_prompt(thinking)
    输入 = 原始用例逻辑 + 上一轮原始输出 + 本项错误明细 + 轮级错误模式摘要 + 规范规则摘录
    输出 = 自查分析 → format(json_mode) → 校验 → 写盘 或 再登记
第 2 轮结束（YAML_REPAIR_ROUNDS=1，即全量轮外最多 1 个修复轮，总计 2 轮）
  仍失败 → 终态: 不写假文件 + 计入 failed
           + 输出目录写 _generation_errors.json
           + thinking_trace.log 标记 generate_yaml_FAILED
```

### 4.2 占位符 ID 规则

`GEN-FAIL-R{轮次}-{序号:03d}`，本轮内唯一（如 `GEN-FAIL-R1-007`），贯穿登记表、日志、错误清单，便于三处互查。

### 4.3 `_generation_errors.json`（写在 Excel 同级目录）

```json
[
  {
    "placeholder_id": "GEN-FAIL-R2-001",
    "case_id": "TC-021",
    "yaml_path": "Gym/test_reservation_page_query_positive_021/test_data.yaml",
    "rounds_attempted": 2,
    "error_type": "B5",
    "error": "data.0.testCase.0.input_extract.subscribeId_active: Input should be a valid string ...",
    "raw_output_snippet": "{\"data\": [{...前500字...}]}"
  }
]
```

全部成功时**不生成**该文件；新一轮全量生成开始时若存在旧文件则删除。

### 4.4 配置（`settings.py` + `config.py`）

```python
# Phase C YAML 修复轮数：第 1 轮全量之外的自查重生成轮数（对齐 EXCEL_REPAIR_ATTEMPTS 风格）
yaml_repair_rounds: int = 1
```

### 4.5 结果回执扩展（`_generate_all_yamls` 返回 → `/confirm-plan` 任务 result）

```python
{"total": 63, "success": 61, "repaired": 2, "failed": 0,
 "rounds": 2, "errors_file": None}          # 仍失败时 errors_file 为路径
```

`web/tasks.py` 的完成消息追加：`YAML: 61/63 直出 + 2 修复` / 失败时 `仍失败 1（详见 _generation_errors.json）`。

---

## 5. 日志设计（thinking_trace.log，对齐 Phase B 风格）

| 时机 | 写法 |
|:---|:---|
| 轮次开始 | `log_phase_header("Phase C — YAML 生成 第1轮 (63 个)")` / `"...修复轮 (3 个)"` |
| 每个 YAML 阶段1思考 | `log_thinking("analyze_yaml_data", <case_id + yaml相对路径>, <thinking全文>, "analyze_yaml_data_prompt")` |
| 生成失败 | `log_thinking("generate_yaml_FAILED", ...)` 内容首行含 `\| {case_id} \| {yaml相对路径} \| {占位ID} \|`，正文为错误全文 —— 与 `generate_excel_plan_FAILED` 同风格，`web/tasks.py` 失败提取逻辑可直接复用 |
| 修复轮自查思考 | `log_thinking("repair_yaml_data_ROUND2", <错误清单摘要>, <自查思考全文>, "repair_yaml_data_prompt")` |
| 轮次汇总 | `ROUND1: 60/63 通过, 3 登记` / `ROUND2: 修复 2, 仍失败 1` |
| 终态失败 | `FINAL_FAILED: 1 个 → _generation_errors.json` |

---

## 6. Prompt 修改（`prompts/extraction_prompts.py`）

### 6.1 `format_yaml_data_prompt` 铁律追加/修订

- **补注入 `{data_factory_methods}`**（与思考节点同一份 methods.yaml 渲染文本）；动态占位符只能使用清单内函数，**禁止发明任何函数或语法**（`{{}}`、占位符内运算/拼接一律非法）
- 时间偏移一律用 `${get_offset_time(fmt, days, ...)}`（偏移量可负=过去；"明天上午10点" = `${get_offset_time(ydm, 1)} 10:00:00` 拼固定时分秒）
- **清单不支持的能力 → 设计合理的固定字面量**（如远期截止日期直接写 `"2029-12-31 10:00:00"`），禁止胡编占位符
- **无需提取时直接省略 extract/input_extract/extract_list 字段，禁止输出 null 值条目**（用户裁定 A8→B10 的配套源头治理）
- json/params/data 三选一，多个非空并存视为错误

### 6.2 `analyze_yaml_data_prompt` 输出字段约束追加

- `{data_factory_methods}` 已注入（现有机制不动），追加核心原则一句话：**只能从上方数据工厂清单中选择函数并按 syntax 填写，禁止胡编；清单不支持的能力用合理固定字面量**
- 分析阶段就要判断"该值用哪个函数还是固定字面量"，避免格式化阶段临时发明

### 6.3 新增 `repair_yaml_data_prompt`（修复轮思考）

```
system: 你上一轮生成的测试数据未通过校验。请先分析错误原因，再说明正确写法。
        ### 本轮错误模式统计（全批次）
        {error_pattern_summary}
        ### 规范要点（与错误相关的规则摘录）
        {spec_rules}
human:  ### 用例逻辑
        {test_case_logic}
        ### 你上一轮的输出（有错）
        {prior_output}
        ### 校验错误明细
        {error_detail}
        请分析并给出修正方案：
```

输出接原 `format_yaml_data_prompt` 结构化收敛。

### 6.4 占位符函数清单：复用现有 `data_factory/methods.yaml` 注册表（单一事实源）

**现有结构（本计划完全沿用，不新造）**：

```
data_factory/methods.yaml          ← 注册表文件（name/syntax/description/params/usage_tips）
        ↓ nodes.py::_load_factory_methods()（缓存加载 + 渲染文本）
        ↓ {data_factory_methods} 注入 analyze_yaml_data_prompt
thinking 节点看到文件内容 + 工具用法 → LLM 选择填写
```

**现状问题**：methods.yaml 只登记了 2 个方法（random_plates / get_current_time），
而框架实际有 6 个 —— LLM 对 get_extract_data 等只能靠示例"猜"，**清单不全本身就是幻觉诱因**。

**本计划改动**：

1. **methods.yaml 重构为"目录 + 大类"结构**（方法会随时间越来越多，扁平列表不可扩展）：

```yaml
# ============================================================
# 数据工厂方法注册表 v2（分类结构）
# 在此注册的所有方法通过 ${} 语法在 YAML 中使用，由测试框架运行时解析。
# 新增方法：先归大类（无合适大类则新增大类），在该类 methods 下追加条目，无需改代码。
# 目录由加载器自动渲染，无需手工维护，不会与条目脱节。
# ============================================================

categories:
  - name: 基础类
    description: 每次生成都会使用 —— 跨步骤数据传递。写入侧为用例的
      extract（返回数据提取，保存至公共参数列表）/ input_extract（输入数据提取，
      保存至公共参数列表）字段；本类方法负责读取与拆分公共参数列表。
    methods:
      - name: get_extract_data
        syntax: '${get_extract_data(key)}'
        description: 从公共参数列表读取已保存变量（列表则取第一个）
        params: { key: 公共参数列表中的 key }
        usage_tips:
          - '读取上游保存的ID: ${get_extract_data(resourceId)}'
        validation: { min_args: 1, max_args: 3 }
      - name: get_extract_data_list
        validation: { min_args: 1, max_args: 2 }
      - name: split_extract_data
        validation: { min_args: 1, max_args: 2 }

  - name: 数据生成类
    description: 随机/构造业务测试数据
    methods:
      - name: random_plates            # 现有条目迁入
        validation: { min_args: 1, max_args: 1 }

  - name: 时间类
    description: 当前时间与偏移时间（未来/过去场景）
    methods:
      - name: get_current_time         # 现有条目迁入
        validation: { min_args: 1, max_args: 1, arg0_enum: [ydm, hms] }
      - name: get_offset_time          # 新增，按规范 §5.2
        validation: { min_args: 1, max_args: 5, arg0_enum: [ydm, hms] }
```

（上方为结构示意，实施时每个方法的 syntax/description/params/usage_tips 按
`YAML_SPECIFICATION.md` §5.2 完整填写，含 get_offset_time 的"负偏移=过去"、
"日期占位符拼固定时分秒"写法）

2. **`render_for_prompt()` 渲染为"目录 + 分类详情"两段**，目录从 categories 自动派生：

```
【数据工厂方法目录】
- 基础类（每次生成都会使用）: get_extract_data / get_extract_data_list / split_extract_data
- 数据生成类: random_plates
- 时间类: get_current_time / get_offset_time

【方法详情】
== 基础类 == <类说明>
  <逐方法: syntax + description + params + usage_tips>
...
```

   LLM 先看目录建立"有哪些能力"的全景，再查详情按 syntax 填写 —— 目录即"选择器"，
   详情即"填写说明"，对应思考节点"传文件内容和工具用法，让 LLM 选择填写"的既有设计。

3. **每个条目可选 `validation` 块**（min_args / max_args / arg0_enum），校验器消费，
   渲染 prompt 时忽略。

4. **加载器归位**：新增 `data_factory/registry.py` 提供 `load_methods() -> list[dict]`
   （缓存，扁平化 categories→methods 供校验器用）与 `render_for_prompt() -> str`
   （目录+详情两段）；`nodes.py::_load_factory_methods()` 改为薄壳调用后者（对外行为不变）。
   放 data_factory 包是因为 `response_model.py`（prompts 层）不能反向 import
   `agent_components/nodes.py`（循环依赖）；import 方向 prompts → data_factory 无环。
   加载器同时兼容旧版扁平 `methods:` 结构（迁移保险）。

5. **三处同源消费**：
   - prompt：analyze 已注入 `{data_factory_methods}`；**format / repair prompt 补注入同一变量**
     （铁律"只能用清单内函数"必须让格式化阶段也看得见清单）
   - 校验器：`validate_placeholders` 从 `load_methods()` 取函数名白名单 + `validation` 块实参规则
   - 单元测试：遍历 methods.yaml 生成合法用例，防止注册表与校验器脱节

**更新流程**（框架新增/修改函数时）：改 `data_factory/methods.yaml` + `YAML_SPECIFICATION.md` §5.2
两处即可，prompt / 校验器 / 测试自动跟随 —— 与文件头"新增方法只需追加条目，无需修改代码"的
原设计承诺一致。本文档不维护函数清单副本。

---

## 7. 需撤销/调整的现有实现

| 位置 | 调整 |
|:---|:---|
| `data_factory/methods.yaml` | **重构为"目录 + 大类"结构**（基础类/数据生成类/时间类），补全至 6 个方法（现仅 2 个）+ 每条新增可选 `validation` 块（§6.4） |
| `data_factory/registry.py` | **新增薄加载层**：`load_methods()`（缓存）+ `render_for_prompt()`；`nodes.py::_load_factory_methods()` 改为薄壳调用（对外行为不变） |
| `response_model.py::validate_placeholders` | 由硬编码 frozenset 白名单改为读 `load_methods()`（函数名 + 实参个数范围 + arg0 枚举） |
| `response_model.py::coerce_extract_values_to_str` | 撤销（int→str 强转、None 丢弃均取消）→ 替换为 `validate_extract_value_types`：值非 str 一律 raise（B5/B10） |
| `response_model.py` | 新增 `TestCase.validate_body_exclusivity`（B9） |
| `generators.py::_generate_one_yaml` | 阶段2 改用无重试调用（`_invoke_structured` 重试参数化为 0 或新增单次调用方法）；阶段1 thinking 写日志 |
| `generators.py::_generate_all_yamls` | 轮次循环 + 登记表 + 修复轮 + `_generation_errors.json` + 日志 + 回执扩展 |
| `tests/test_phase_bc_unit.py` | B5 相关 4 个强转测试改为期待 `ValidationError`；null 丢弃测试改为期待 raise；新增 B9/B10 用例；占位符测试改为遍历注册表生成合法用例（含 `${get_offset_time(ydm, 1)} 10:00:00` 拼接、负偏移）；A 类规整测试保持不变 |
| `tests/test_phase_c_api.py` | E2E 校验器占位符检查同步读注册表；保留 B 类检查作防线（理论上失败不落盘，落盘即防线告警）；新增：存在非空 `_generation_errors.json` → 按已知失败输出清单；回执断言改为 `success + failed == total` 且 `failed == 0` 为通过 |

---

## 8. 验收标准

1. 单元测试全绿（含新增 B9/B10/循环逻辑用例；修复循环用可注入的假生成函数测试，不依赖 LLM；占位符合法用例由注册表遍历生成，覆盖 `get_offset_time` 负偏移与"日期占位符拼固定时分秒"写法）
2. E2E（服务重启后手动触发）：
   - 落盘 YAML 中 0 个 B 类问题（校验防线无告警）
   - `thinking_trace.log` 含：Phase C 阶段1思考全文、轮次汇总、失败标记（如有）
   - 若有终态失败：`_generation_errors.json` 与回执 failed 数一致、`.py` 中对应引用在报告中明确列为已知失败
3. 回执字段 `repaired`/`rounds`/`errors_file` 正确
4. 回滚方式：`git checkout -- <涉及文件>`（本计划不改数据库/不改 Excel 结构）

---

## 9. 明确不做的事

- 不写占位假 YAML 文件（终态失败 = 缺文件 + 清单）
- 不在代码层做任何 B 类错误的静默兜底
- 不改 LangGraph 图拓扑、不改 Phase B、不改 Excel 读写

---

## 10. 后续事项（另行讨论，不阻塞本计划）

- **methods.yaml 维护自动化**：✅ 方案已定型（B 半自动编译 + A 哨兵防漂移，手动触发，
  §5.2 文档改由注册表渲染），详见 `chongelog/2026-07-18_data_factory_registry_compiler.md`，
  待本计划落地后开工。届时 §6.4 的"更新流程：改两处"升级为"改 methods.yaml 一处 +
  跑 --render-spec 渲染"。本计划内 methods.yaml 仍按人工维护交付。
