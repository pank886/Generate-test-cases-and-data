# Phase B Excel 生成 & 修复轮 — 修复计划

> 生成时间: 2026-07-26
> 状态: 待执行
> 更新: 2026-07-26 追加 P0-0（max_tokens 截断）

---

## 一、问题全景图

```
analyze_test_points_raw（思考 prompt）
  → 要求 expected: "1.[eq]xxx"                     ← ✅ 断言格式正确
       ↓
generate_excel_plan_node（格式化 prompt）
  → 示例 expected: "1.创建成功"                     ← ❌ 示例无断言关键词
  → max_tokens=65536 未生效，API 实际返回 8192       ← ❌ JSON 被截断，解析直接崩溃
       ↓
Phase B 校验（_generate_excel_plan_node）
  → 不检查断言格式                                  ← ❌ 错误通过
       ↓
Phase C _parse_assertion
  → 解析失败                                       ← 💥 才发现问题，无法回溯
```

**双重根因**：
1. Prompt 示例 → 校验 → 修复 三段之间存在格式契约断裂，LLM 按照错误示例输出，代码侧不做拦截
2. `max_tokens` 未正确传递到 API 请求，大计划 JSON 在 8192 token 处被截断，结构化输出解析直接报错

---

## 二、问题清单与修复方案

### 🔴 P0-0 — 问题 0：`max_tokens` 未传递到 API，大计划 JSON 被截断在 8192 token

| 项 | 内容 |
|---|------|
| **文件** | `agent_components/nodes.py` |
| **位置** | `_get_llm()` (line 70-82) + `_invoke_structured()` (line 806-867) |
| **现象** | 构造函数设了 `max_tokens=65536`，但实际 API 请求 `completion_tokens=8192`，JSON 输出被截断 |
| **影响** | Excel 用例数多（50+ TC）时 JSON 超过 8192 token → 响应被 API 截断 → `with_structured_output` 的 JSON 解析器报 `Could not parse response content as the length limit was reached` → 首轮 + 修复轮全部失败 |

**现场报错**：
```
LLM 结构化输出校验失败（本调用内重试 2 次，外层修复轮独立计数）:
Could not parse response content as the length limit was reached -
CompletionUsage(completion_tokens=8192, prompt_tokens=20880, total_tokens=29072, ...)
```

**根因分析**：

这是一个 LangChain `bind()` + `with_structured_output()` 链式调用导致的 `max_tokens` 丢失问题。追踪传递链路：

```
DeepSeekChatOpenAI(max_tokens=65536)           ← 构造函数传入
  ↓
.bind(temperature=0.4)                         ← nodes.py:840，返回 RunnableBinding
  ↓
.with_structured_output(model_class,           ← nodes.py:842
    method="json_mode")
  ↓
LangChain 内部再 .bind(response_format=        ← json_mode 的内部实现
    {"type": "json_object"})
  ↓
最终 API 请求                                   ← max_tokens 在层层 bind 中丢失！
```

**核心问题**：LangChain 的 `bind()` 返回 `RunnableBinding`，它只透传显式绑定的 kwargs（`temperature`）。底层 `ChatOpenAI` 构造函数中的 `max_tokens=65536` 存在于 `model_kwargs` 或实例属性中，但 `RunnableBinding` 在生成最终的 API 请求参数时，是从**配置合并链**中提取 `max_tokens`。多层 `bind()` 嵌套后，合并链可能断裂，`max_tokens` 回退到 API 默认值 8192。

此外，DeepSeek API 可能对 `max_tokens` 参数名有特殊要求（如部分兼容层需用 `max_completion_tokens`），LangChain 的 `ChatOpenAI` 默认使用 `max_tokens` 字段名映射。

**修复（两处协同）**：

**修复 A** — `_invoke_structured()` 显式传递 `max_tokens`（`agent_components/nodes.py`）：

```diff
  def _invoke_structured(self, prompt, model_class, max_retries=config.MAX_RETRIES,
                         method="function_calling", thinking=False,
                         temperature=None, log_label="", **kwargs):
      ...
      last_error = None
-     _llm = self.llm.bind(temperature=temperature) if temperature is not None else self.llm
+     # 显式 bind max_tokens + temperature，防止多层 bind 嵌套丢失构造函数参数
+     _bind_kwargs = {"max_tokens": config.LLM_MAX_TOKENS}
+     if temperature is not None:
+         _bind_kwargs["temperature"] = temperature
+     _llm = self.llm.bind(**_bind_kwargs)
      chain = prompt | _llm.with_structured_output(
          model_class, method=method, **llm_kwargs
      )
```

**修复 B** — config 中增加 `LLM_MAX_TOKENS` 显式配置（`config.py` + `settings.py`）：

```python
# settings.py 中增加:
llm_max_tokens: int = Field(
    default=65536, ge=1024, le=131072,
    description="LLM API 请求的 max_tokens 参数。需大于单次 JSON 输出的 token 估算上限。"
)

# config.py 中导出:
LLM_MAX_TOKENS = settings.llm_max_tokens
```

> **说明**：构造函数里的 `max_tokens=65536` 保留不动（作为兜底），修复 B 增加一个显式的 config 变量，修复 A 在每次 API 调用时显式 bind。这样无论 LangChain 内部合并链如何变化，`max_tokens` 一定在绑定参数中。

**修复 C（备选，如果 DeepSeek API 不认 `max_tokens`）** — 改用 `max_completion_tokens`：

如果修复 A/B 后仍然截断在 8192，说明 DeepSeek API 的 LangChain 适配层可能映射到了错误字段。此时改为：

```python
_bind_kwargs = {"max_completion_tokens": config.LLM_MAX_TOKENS}
```

---

### 🔴 P0 — 问题 1：主生成 Prompt 示例缺少断言关键词

| 项 | 内容 |
|---|------|
| **文件** | `prompts/definitions.py` |
| **位置** | 第 86-88 行，`generate_excel_plan_node()` 方法 |
| **现象** | 示例 `"expected": "1.创建成功\\n2.信息一致"` 不含 `[eq]` 等断言关键词 |
| **影响** | LLM 照抄示例格式，导致 expected 字段缺少断言关键词；Phase B 校验不拦截，Phase C 才崩溃 |

**修复**：

```diff
 # prompts/definitions.py: generate_excel_plan_node()

- "expected": "1.创建成功\\n2.信息一致",
+ "expected": "1.[eq]创建成功\\n2.[eq]信息一致",
```

同步修正示例中的 TC-001（无前置的新增操作）和 TC-002（依赖 PRE-001 的修改操作），TC-001 的 expected 也需要加断言：

```diff
  '        "steps": "1.调用新增设施接口\\n2.查询详情",\n'
- '        "expected": "1.创建成功\\n2.信息一致",\n'
+ '        "expected": "1.[eq]创建成功\\n2.[eq]信息一致",\n'
```

同时在「字段硬约束」段增加一条：

```
- expected 中每条预期必须以断言关键词开头：[eq]/[contains]/[ne]/[db]，格式为 "序号.[关键词]内容"
  正确: "1.[eq]创建成功" / "2.[contains]列表包含设备" / "3.[ne]id不等于旧值"
  错误: "1.创建成功" / "1.(eq)创建成功" / "1.[ eq ]创建成功"
```

---

### 🔴 P0 — 问题 2：Phase B 校验不做断言格式检查

| 项 | 内容 |
|---|------|
| **文件** | `agent_components/nodes.py` |
| **位置** | 第 168-193 行，`_generate_excel_plan_node()` 的校验循环 |
| **现象** | 只检查空字段、PRE 引用、条数一致性，不校验 expected 中的断言格式 |
| **影响** | 带格式错误的 expected（缺关键词、含空格、双层括号）静默通过，到 Phase C 才暴露 |

**修复**：在首轮和修复轮的校验循环中，增加断言格式检查。提取 `GenerationMixin._parse_assertion` 的校验逻辑或内联一个轻量校验：

```python
# 在 nodes.py 顶部导入或内联
import re
_ASSERT_OK = re.compile(r'^\d+\.\s*\[(eq|contains|ne|db)\]', re.IGNORECASE)
_ASSERT_BAD_SPACE = re.compile(r'\[\s+(eq|contains|ne|db)\s*\]|\[\s*(eq|contains|ne|db)\s+\]')
_ASSERT_DOUBLE = re.compile(r'\[\[|\]\]')

# 校验循环内增加（约 line 188 附近，在 steps/expected 条数检查之后）:
if tc.expected:
    for ei, line in enumerate(tc.expected.split('\n'), 1):
        line = line.strip()
        if not line:
            errs.append(f"预期第{ei}条为空行")
            continue
        if _ASSERT_DOUBLE.search(line):
            errs.append(f"预期第{ei}条含双层括号: {line[:40]}")
        elif _ASSERT_BAD_SPACE.search(line):
            errs.append(f"预期第{ei}条断言关键词含空格: {line[:40]}")
        elif not _ASSERT_OK.search(line):
            errs.append(
                f"预期第{ei}条缺少断言关键词 [eq]/[contains]/[ne]/[db]: {line[:40]}"
            )
```

同理修复轮校验（line 279-293）也需加入相同检查。

---

### 🟡 P1 — 问题 3：修复轮 Prompt 缺少关键上下文

| 项 | 内容 |
|---|------|
| **文件** | `prompts/extraction_prompts.py` + `agent_components/nodes.py` |
| **位置** | `repair_excel_plan_prompt()` 模板（line 140-180）和调用处（nodes.py line 245-252） |
| **现象** | 修复 prompt 只接收 `analysis_section` + `cases_section`，缺 `shared_pre_section`、`module_tree`、`all_apis_info` |
| **影响** | LLM 修复时不知道 PRE 定义 → 无法修正引用错误；不知道模块结构 → 无法修正 story 字段 |

**修复**：

Step 1 — 在 `repair_excel_plan_prompt()` 模板中增加占位变量（`prompts/extraction_prompts.py`）：

```diff
  ("system",
   "你正在修复一个 Excel 测试计划中的失败用例。按以下要求修正每个失败用例。\n\n"
   ...
+  "### 共享前置（参考，用于修正 PRE 引用）\n{shared_pre_section}\n\n"
+  "### 模块树（参考，用于修正 story 字段）\n{module_tree}\n\n"
+  "### 接口定义（参考，用于补全步骤）\n{all_apis_info}\n\n"
   "### 测试场景分析（参考上下文）\n{analysis_section}\n\n"
   "### 完整用例描述（参考原始设计）\n{cases_section}\n\n"
   "### 失败的行及错误\n{failed_test_cases}\n\n"
   ...
```

Step 2 — 调用处传入变量（`agent_components/nodes.py` line 245-252）：

```diff
  repair_prompt = repair_excel_plan_prompt()
  plan = self._invoke_structured(repair_prompt, ExcelPlanV2,
      method="json_mode",
      failed_test_cases=failed_tc_text,
+     shared_pre_section=_sections["preconditions"],
+     module_tree=module_tree_json,
+     all_apis_info=all_apis_json,
      analysis_section=_sections["analysis"],
      cases_section=_sections["cases"],
  )
```

---

### 🟡 P1 — 问题 4：资源冲突消解只处理最后一轮 plan

| 项 | 内容 |
|---|------|
| **文件** | `agent_components/nodes.py` |
| **位置** | 第 422-424 行 |
| **现象** | `_resolve_resource_conflicts(plan, all_shared_pres)` 在 for 循环外调用，此时 `plan` 是最后一轮（可能只含修复的 TC） |
| **影响** | 首轮通过校验的 TC 对象不在最后一轮 `plan.test_cases` 中，它们的冲突永远不会被消解 |

**修复**：传入 `valid_cases`（已包含所有轮的 TC），或构造一个包含全部 TC 的临时 plan：

```diff
- if all_shared_pres:
-     self._resolve_resource_conflicts(plan, all_shared_pres)
+ if all_shared_pres:
+     # 构造包含全部 confirmed + 降级接纳 case 的临时 plan，确保所有 TC 都经过冲突消解
+     _full_plan = ExcelPlanV2(
+         shared_preconditions=list(all_shared_pres),
+         test_cases=list(valid_cases),
+     )
+     self._resolve_resource_conflicts(_full_plan, all_shared_pres)
+     # 将消解后的 preconditions 回写到 valid_cases（对象引用相同，自动同步）
```

---

### 🟡 P1 — 问题 5：修复 Prompt 强制 shared_preconditions 为空

| 项 | 内容 |
|---|------|
| **文件** | `prompts/extraction_prompts.py` |
| **位置** | 第 177 行，`repair_excel_plan_prompt()` |
| **现象** | 指南写 `"5. shared_preconditions 留空数组 []"` |
| **影响** | 如果某个 TC 引用了首轮漏生成的 PRE → LLM 无法在修复轮补充 PRE 定义 → `"引用前置 XXX 不存在"` 变成死循环 |

**修复**：

```diff
- "5. shared_preconditions 留空数组 []\n"
+ "5. 如果失败行包含"引用前置 XXX 不存在"错误，请将 XXX 的定义补充到 shared_preconditions 中；否则留空数组 []\n"
```

---

### 🟢 P2 — 问题 6：`_split_thinking_sections` 段落匹配过于严格

| 项 | 内容 |
|---|------|
| **文件** | `agent_components/nodes.py` |
| **位置** | 第 640-668 行 |
| **现象** | 精确匹配 `"## 测试场景分析"` / `"## 共享前置"` / `"## 测试用例"`，LLM 输出略有偏差即全部解析为 `"（无）"` |
| **影响** | 所有上下文丢失，LLM 在空上下文中生成 Excel → 质量极差 |

**修复**：改用子串匹配 + 回退策略：

```python
@staticmethod
def _split_thinking_sections(text: str) -> dict:
    """将 thinking 分析输出按三个段落拆分为独立输入。"""
    result = {"analysis": "（无）", "preconditions": "（无）", "cases": "（无）"}
    if not text:
        return result

    # 宽松匹配：包含关键词即视为段落起始
    patterns = [
        ("analysis",      re.compile(r'测试场景分析|场景分析|测试分析', re.IGNORECASE)),
        ("preconditions", re.compile(r'共享前置|前置条件|前置准备', re.IGNORECASE)),
        ("cases",         re.compile(r'测试用例|用例设计|用例列表', re.IGNORECASE)),
    ]

    # 找到每个段落在文本中的起始位置
    positions = []
    for key, pat in patterns:
        m = pat.search(text)
        if m:
            positions.append((m.start(), key))
    if not positions:
        return result
    positions.sort()

    for i, (pos, key) in enumerate(positions):
        next_pos = positions[i + 1][0] if i + 1 < len(positions) else len(text)
        result[key] = text[pos:next_pos].strip()

    return result
```

---

### 🟢 P2 — 问题 7：修复轮中失败用例数据未更新

| 项 | 内容 |
|---|------|
| **文件** | `agent_components/nodes.py` |
| **位置** | 第 301-306 行 |
| **现象** | `_still_failed` 保留的是原始失败数据（首轮的 `f_dict`），不是修复轮最新输出 |
| **影响** | 再次进入修复轮时，传回的是旧数据而非最新尝试，LLM 缺少"上次怎么改的但还是失败"的闭环 |

**修复**：修复轮失败时使用本轮 LLM 输出更新 `f_dict`：

```diff
  _still_failed = [
-     (f_idx, f_dict, f_errs)
+     (f_idx, f_dict, f_errs)
      for f_idx, f_dict, f_errs in failed_details
      if f_dict.get("id", "") not in fixed_ids
  ]
- failed_details = _still_failed + _new_failed
+ # _new_failed 中的条目使用本轮最新数据；_still_failed 保留原始（本轮未尝试修复）
+ failed_details = _still_failed + _new_failed
```

> 当前逻辑实际上已是正确的：`_new_failed` 已经是本轮最新数据。本条降级为 **P2 确认无需修改**。

---

## 三、执行顺序

| 优先级 | 序号 | 修改文件 | 预计改动量 | 依赖 |
|--------|------|---------|-----------|------|
| **P0-0** | 0 | `agent_components/nodes.py` + `settings.py` + `config.py` | ~15 行 | 无 |
| **P0** | 1 | `prompts/definitions.py` | ~5 行 | 无 |
| **P0** | 2 | `agent_components/nodes.py` | ~25 行 | 无 |
| **P1** | 3 | `prompts/extraction_prompts.py` + `nodes.py` | ~10 行 | 无 |
| **P1** | 4 | `agent_components/nodes.py` | ~8 行 | 无 |
| **P1** | 5 | `prompts/extraction_prompts.py` | ~1 行 | 无 |
| **P2** | 6 | `agent_components/nodes.py` | ~25 行 | 无 |

**建议执行顺序**: 0 → 1 → 2 → 3 → 4 → 5 → 6

**P0-0 必须最先修**：`max_tokens` 截断导致整个结构化输出解析崩溃，不管 Prompt 示例和校验怎么改，LLM 返回的 JSON 根本解不出来。仅当 JSON 能完整返回后，P0 的问题 1+2 才有意义。
P0 的 1+2 必须一起修：单修 Prompt 示例而校验不拦截，仍有漏网之鱼；单修校验而 Prompt 示例未改，LLM 仍按错误格式输出导致大面积失败。

---

## 四、验证方案

1. **单元测试**：在 `tests/test_phase_b_dedup.py` 或 `tests/test_phase_bc_unit.py` 中增加：
   - `TestAssertionInExpected`: 模拟 expected 不含 `[eq]` → 校验应报错
   - `TestAssertionInExpected`: 模拟 expected 含 `[ eq ]` 空格 → 校验应报错
   - `TestSplitThinkingSections`: 模拟 LLM 输出各种标题变体 → 应正确解析

2. **集成测试**：跑一次完整 Phase B → 检查输出的 `test_plan.xlsx`：
   - Sheet1 的「预期结果」列每条都含 `[eq]`/`[contains]`/`[ne]`/`[db]`
   - 步骤数与预期数一致
   - 资源冲突隔离的 PRE 正确写入 Sheet2

3. **回归测试**：确保现有 `test_phase_bc_unit.py` 全部通过
