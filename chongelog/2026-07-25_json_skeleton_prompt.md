# YAML 生成结构准确性优化：JSON 骨架替代文字描述

| 项目 | 内容 |
|:---|:---|
| 讨论日期 | 2026-07-25 |
| 变更类型 | `format_yaml_data_prompt()` 重构 + `_generate_one_yaml()` 流程拆分 |
| 涉及文件 | `prompts/extraction_prompts.py`, `prompts/response_model.py`, `agent_components/generators.py` |

---

## 一、问题背景

### 1.1 当前方案

Phase C YAML 生成使用两段式流程：

```
thinking LLM (自由分析) → analysis 文本
json_mode LLM + with_structured_output(TestData) → Pydantic 解析 → YAML
```

`format_yaml_data_prompt()` 用 13 条手写"铁律"描述 JSON 结构（`baseInfo` / `testCase` 嵌套关系），与 Pydantic 模型 `TestData` / `StepData` 中定义的 Schema 是**两套独立的、可能不一致的结构定义**。

### 1.2 实测结果

DeepSeek V4 Pro **不支持 `json_schema` 模式**（返回 400 — `"This response_format type is unavailable now"`）。只支持 `json_mode`（`response_format: {type: "json_object"}`），而 `json_mode` **不将 Pydantic Schema 传给 LLM**——它只告诉 API"请输出 JSON"，0 结构信息。

结果：**YAML 合格率 ~23%（3/13）**。10 个失败全是结构嵌套错误：

| 错误类型 | 占比 | 根因 |
|---------|:---:|------|
| `data` 被多余 `{testCase: ...}` 包裹 | 7/10 | LLM 不知道 `data` 是 `StepData[]` |
| 缺少 `baseInfo`/`testCase` 包装 | 2/10 | LLM 不知道有三层嵌套 |
| `validation` 写成对象而非数组 | 1/10 | LLM 不知道 validation 是 list |

---

## 二、核心思路：Schema-First Prompting

**Schema 管结构，Prompt 管意图。**

- Pydantic 模型 → 唯一的"结构契约" → 自动渲染为 JSON 骨架注入 prompt
- Prompt 文本 → 只描述任务意图、上下文和业务规则
- `model_validate()` → 最后一步做校验，不参与结构描述

### 2.1 改造后的流程

```
Pydantic模型 ─→ generate_json_skeleton() ─→ JSON骨架文本 ─┐
                                                          ├─→ system prompt ─→ LLM ─→ raw JSON
业务规则 (工厂方法/断言格式/HTTP参数规则等) ──────────────┘                        │
                                                                   ┌──────────────┘
                                                                   ▼
                                            _extract_json_from_thinking(raw)
                                                                   │
                                                                   ▼
                                            TestData.model_validate(parsed_dict)
                                                                   │
                                                           ┌───────┴───────┐
                                                           │ 通过 → 写YAML  │
                                                           │ 失败 → 修复轮  │
                                                           └───────────────┘
```

### 2.2 对比

| 维度 | 当前 | 改造后 |
|------|------|--------|
| 结构定义 | 手写 YAML 格式文字 + Pydantic Schema 两套 | 仅 Pydantic Schema（JSON 骨架自动生成） |
| 结构传达 | `json_mode` 不传 Schema，LLM 从 prompt 文字猜 | JSON 骨架直观看得见嵌套层级 |
| 业务规则 | 与结构约束混在 13 条铁律里 | 精简，只保留 Schema 表达不了的规则 |
| raw output | 藏在 `with_structured_output` 的异常消息里，截断 | 全程可追溯，全文注入修复轮 |
| 校验 | `with_structured_output` 内部解析 + 异常 | `model_validate()` 纯校验 |

---

## 三、技术方案

### 3.1 JSON 骨架生成器

从 Pydantic 模型的 `model_json_schema()` 递归生成。**注意**：`Field(json_schema_extra={...})` 中的键会被 Pydantic **展平到顶层**——`example_keys` 直接出现在字段 schema 的根级别，不在 `json_schema_extra` 嵌套内。代码读取时用 `prop.get("example_keys")` 而非 `prop.get("json_schema_extra", {}).get("example_keys")`。

```python
def generate_json_skeleton(model_class: type[BaseModel]) -> str:
    """从 Pydantic 模型生成 JSON 骨架，用于注入 prompt。

    处理 Pydantic model_json_schema() 的所有结构：
      - object / $ref → 递归解析
      - anyOf 含 null → Optional 字段，有 example_keys 则渲染 {}，否则跳过
      - array / Dict[str, Any] / 标量类型

    注意：example_keys 被 Pydantic 展平，直接从 prop dict 读取。
    """
    schema = model_class.model_json_schema()
    _defs = schema.get("$defs", {})

    def _resolve(prop: dict) -> dict:
        """解析 $ref 和 anyOf（Optional 字段），返回实际类型定义。"""
        ref = prop.get("$ref", "")
        if ref.startswith("#/$defs/"):
            return _defs.get(ref[len("#/$defs/"):], prop)
        any_of = prop.get("anyOf")
        if any_of:
            for opt in any_of:
                r = _resolve(opt)
                if r.get("type") != "null":
                    return r
        return prop

    def _is_optional(prop: dict) -> bool:
        """anyOf 中含 null → Optional 字段。"""
        if "anyOf" in prop:
            return any(o.get("type") == "null" for o in prop["anyOf"])
        return isinstance(prop.get("type"), list) and "null" in prop["type"]

    def _build(prop: dict) -> Any:
        # Optional[object/list] 且无 example_keys → 跳过不渲染（§5.10）
        # example_keys 在原始 prop（anyOf 外层），不在 _resolve() 后的内层
        if _is_optional(prop):
            resolved = _resolve(prop)
            rtype = resolved.get("type")
            if isinstance(rtype, list):
                rtype = next((t for t in rtype if t != "null"), None)
            if rtype in ("object", "array"):
                if prop.get("example_keys") is None:
                    return None

        prop = _resolve(prop)
        ptype = prop.get("type")
        if isinstance(ptype, list):
            ptype = next((t for t in ptype if t != "null"), ptype[0] if ptype else None)

        if ptype == "object":
            props = prop.get("properties", {})
            if not props:
                ex = prop.get("example_keys")
                if ex is not None:
                    return {k: _build({"type": "string"}) for k in ex}
                return {}
            result = {k: _build(v) for k, v in props.items()}
            return {k: v for k, v in result.items() if v is not None}

        if ptype == "array":
            items = prop.get("items", {})
            return [_build(_resolve(items))]

        if ptype == "string":   return ""
        if ptype in ("integer", "number"): return 0
        if ptype == "boolean":  return False
        return None

    return json.dumps(_build(schema), indent=2, ensure_ascii=False)
```

### 3.2 Pydantic 模型适配

给 `Dict[str, Any]` 字段添加 `json_schema_extra` 示例键，让骨架生成器能展开：

```python
# response_model.py

class StepData(BaseModel):
    baseInfo: Dict[str, Any] = Field(
        json_schema_extra={
            "example_keys": {"api_name": "", "url": "", "method": "", "header": {}}
        }
    )
    testCase: List[TestCase] = Field(min_length=1)

class TestCase(BaseModel):
    case_name: str
    json: Dict[str, Any] = Field(
        default_factory=dict,
        json_schema_extra={"example_keys": {}}  # 空 → prompt 中展开为 {}
    )
    params: Dict[str, Any] = Field(
        default_factory=dict,
        json_schema_extra={"example_keys": {}},
    )
    extract: Dict[str, str] | None = None
    validation: List[dict] = Field(default_factory=list)
```

生成的骨架示例：

```json
{
  "data": [
    {
      "baseInfo": {
        "api_name": "",
        "url": "",
        "method": "",
        "header": {}
      },
      "testCase": [
        {
          "case_name": "",
          "json": {},
          "params": {},
          "validation": []
        }
      ]
    }
  ]
}
```

### 3.3 Prompt 重构

`format_yaml_data_prompt()` 调整：

**删掉：**
- 手写 YAML 结构描述（`- baseInfo:` / `testCase:` 等）
- 与 Schema 重复的结构约束规则（嵌套层级、字段名、必填/可选、类型）

**保留：**
- 业务规则：工厂方法只能用注册表函数、断言格式 `[eq/contains/ne/db]`、HTTP 方法决定 params/json/data 选择、header 规则、url 禁止动态占位符、extract JSONPath 必须以 `$.` 开头等

**新增：**
- `{skeleton}` 占位符，注入自动生成的 JSON 骨架

```python
def format_yaml_data_prompt() -> ChatPromptTemplate:
    return ChatPromptTemplate.from_messages([
        ("system",
         "你是数据格式化专家。根据【数据分析】和【接口定义】，输出测试数据的 JSON。\n\n"
         "### 输出 JSON 结构（必须严格遵循此骨架，键名、层级不能增减）\n"
         "```json\n{skeleton}\n```\n\n"
         "### 可用数据工厂方法\n{data_factory_methods}\n\n"
         "### 业务规则\n"
         "...（保留的规则）...\n\n"
         "禁止 Markdown，只输出 JSON"
        ),
        ("human", "...")
    ])
```

### 3.4 流程拆分：LLM 调用与校验解耦

```python
# _generate_one_yaml 改造

# 阶段 1：thinking（不变）
analysis = thinking_llm.invoke(...)

# 阶段 2：json_mode（不再用 with_structured_output）
format_prompt = format_yaml_data_prompt()
llm_with_json = self.llm.bind(response_format={"type": "json_object"})

raw_text = llm_with_json.invoke(format_prompt.format_messages(
    skeleton=skeleton_text,
    data_analysis=analysis,
    ...
))

# 阶段 3：提取 JSON + Pydantic 校验（纯代码）
parsed = _extract_json_from_thinking(raw_text)
test_data = TestData.model_validate(parsed)

# 阶段 4：写 YAML（不变）
yaml_text = yaml.dump([step.model_dump(...) for step in test_data.data], ...)
```

**raw text 全程保留**：修复轮时全文注入，LLM 自查有完整上下文：

```
### 你上一轮的输出（有错）
{raw_text}

### 校验错误明细
{error_detail}

请分析并给出修正方案：
```

### 3.5 修复轮适配

修复轮使用错误诊断包（§5.3），注入骨架 + failed_yaml（YAML 渲染）+ error_roadmap。不注入 raw_text——failed_yaml 是其 YAML 版本，缩进直观且语义等价，同时给两份 JSON+YAML 是冗余。

```python
repair_prompt = repair_yaml_data_prompt()  # 复用同一个 prompt
repair_vars = dict(
    skeleton=skeleton_text,
    failed_yaml=repair_ctx["failed_yaml"],     # ← 替代 prior_output（YAML 格式）
    error_roadmap=repair_ctx["error_roadmap"], # ← 替代 error_detail + error_pattern_summary
    data_factory_methods_section=...,           # ← 条件注入
    api_definitions_section=...,                # ← 条件注入
)
```

---

## 四、预期效果

| 指标 | 当前 | 预期 |
|------|:---:|:---:|
| 结构嵌套错误（data 被包裹） | 7/13 (54%) | 0 |
| 缺少 baseInfo/testCase 包装 | 2/13 (15%) | 0 |
| validation 写成对象非数组 | 散见 | 显著减少 |
| YAML 合格率 | ~23% | 80%+ |
| 修复轮自查质量 | 截断的 snippet | 全文 raw text |

---

## 五、关键技术细节

### 5.1 TestData 类型注解确认

`prompts/response_model.py:602` 已正确定义：

```python
class TestData(BaseModel):
    data: List[StepData] = Field(min_length=1, ...)
```

`data` 是 `List[StepData]`，不是单个 `StepData`。后续写 YAML 的 `[step.model_dump() for step in test_data.data]` 依赖此类型，无需修改。

### 5.2 JSON 提取函数防御性设计

`_extract_json_from_thinking()` 当前版本（`generators.py:108`）已存在于 dep_map 生成流程中，YAML 生成拆分后也需要它。当前实现有两步：正则匹配 markdown 代码块 → `find("{")` / `rfind("}")` 截取。

由于 `response_format: {"type": "json_object"}` 会约束 LLM 只输出 JSON，正常路径下直接 `json.loads()` 即可成功。但 DeepSeek 偶尔会在 JSON 前后附加 markdown 标记或简短注释，需要多层降级。优化后的版本：

```python
import re
import json

def _extract_json_from_thinking(raw_text: str) -> dict:
    """从 LLM 输出中提取 JSON 对象。多层降级，由快到慢。

    设计决策（2026-07-25 确认）：L1 直接解析优先。DeepSeek 上
    response_format: json_object 绝大多数情况输出纯净 JSON，
    L1 的 O(n) json.loads() 直接命中，跳过正则编译和匹配开销。
    L2/L3 仅作安全网。"""

    # L1: 直接解析（response_format: json_object 下的最快路径）
    try:
        return json.loads(raw_text)
    except json.JSONDecodeError:
        pass

    # L2: ```json ... ``` 代码块（LLM 偶尔在 JSON 外加说明文字 + markdown）
    m = re.search(r'```json\s*(.*?)\s*```', raw_text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass

    # L2b: ``` ... ``` 无语言标记的代码块
    m = re.search(r'```\s*(\{.*?\})\s*```', raw_text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass

    # L3: 全文查找第一个 { 到最后一个 }（最后防线）
    start = raw_text.find("{")
    end = raw_text.rfind("}")
    if start != -1 and end != -1 and end > start:
        return json.loads(raw_text[start:end + 1])

    raise json.JSONDecodeError("无法从 LLM 输出中提取 JSON 对象", raw_text, 0)
```

**设计决策**：

| 层级 | 方法 | 复杂度 | 说明 |
|------|------|:---:|------|
| L1 | `json.loads(raw)` | O(n) | `response_format: json_object` 下纯 JSON 直接命中，跳过正则开销 |
| L2 | `re.search` (json 代码块) | O(n) | DeepSeek 偶尔附加说明文字 + markdown，正则提取 |
| L2b | `re.search` (无标记代码块) | O(n) | 兜底：无语言标记的代码块 |
| L3 | `find("{")` + `rfind("}")` | O(n) | 最后防线：JSON 混在文本中，无回溯 |

**为什么 L3 不用正则 `(\{.*\})` 而用 `find` + `rfind`**：

- `re.search(r'(\{.*\})', raw_text, re.DOTALL)` — `.*` 贪婪匹配到字符串末尾再回溯找最后一个 `}`。对于 LLM thinking 输出（可能数千字符），回溯开销不可忽略
- `raw_text.find("{")` + `raw_text.rfind("}")` — 两次 O(n) 扫描，无回溯，对长字符串更友好
- 两者结果等价的场景（单个顶层 JSON 对象）：都从第一个 `{` 匹配到最后一个 `}`
- 两者都不正确的极端场景（LLM 输出包含多个 JSON 对象）：都不可靠，但此场景几乎不会出现（`response_format: json_object` 约束）

此外，如果 `response_format: json_object` 强制生效，L1 优先级最高且几乎总是命中，L2/L3 仅作安全网。

### 5.3 修复轮"错误诊断包"设计

修复轮的根本目标：**让 LLM 看到"错在哪" + "原本想填什么"**。

但 Pydantic 校验错误分为两类，修复需求不同：

| 错误类型 | 示例 | 修复需要 |
|---------|------|---------|
| **纯结构** | `data` 被 `testCase` 包裹、缺 `baseInfo` | 只需要骨架 |
| **数据值** | `${random_plate(1)}` 写成 `${random_plates(1)}`、断言字段不在 returns 中 | 需要工厂方法清单 / 接口定义才能对照 |

**如果修复轮只给骨架不给数据上下文，数据值错误无法自查修正，会永久残留。** 需要按错误类型分级注入上下文:

```
┌──────────────────────────────────────────────────────┐
│                      修复轮输入                        │
├──────────┬───────────────────────────────────────────┤
│ 所有错误  │ failed_yaml + error_roadmap + skeleton    │  ← 选代包，必给
├──────────┼───────────────────────────────────────────┤
│ 含数据错误 │ + data_factory_methods                    │  ← 仅在 error_roadmap 含占位符/断言相关错误时注入
├──────────┼───────────────────────────────────────────┤
│ 含 API 错误│ + api_definitions                        │  ← 仅在 error_roadmap 含 url/method/参数相关错误时注入
└──────────┴───────────────────────────────────────────┘
```

**不再注入的内容**（修复轮不需要，且可以分散 LLM 注意力）：
- `user_context` — 已经在前一轮 thinking 中使用过，修复轮不涉及用户意图
- `test_case_logic`（Excel 的 steps/expected 原文）— 数据含义已在 failed_yaml 中体现
- `error_pattern_summary`（跨文件错误模式）— 被逐条 error_roadmap 替代

**修复轮 prompt 变量汇总**：

| 变量 | 首轮 | 修复轮 | 说明 |
|------|:---:|:---:|------|
| `skeleton` | ✅ | ✅ | JSON 骨架，始终需要 |
| `data_factory_methods` | ✅ | ⚠️ 条件 | 仅在含 `${}` 错误时注入 |
| `api_definitions` | ✅ | ⚠️ 条件 | 仅在含 url/method 错误时注入 |
| `data_analysis` (thinking 结果) | ✅ | ❌ | 修复轮不需要重新 thinking |
| `user_context` | ✅ | ❌ | 不涉及用户意图 |
| `test_case_logic` | ✅ | ❌ | failed_yaml 已经体现数据含义 |
| `failed_yaml` | — | ✅ | 新增，替代 prior_output |
| `error_roadmap` | — | ✅ | 新增，替代 error_detail + error_pattern_summary |
| `raw_text` | — | ✅ | 新增，JSON 解析失败时的保底 |

**实现**：

```python
import json
import yaml
from pydantic import ValidationError

def prepare_repair_context(raw_text: str, error: Exception) -> dict:
    """从 raw text + 异常构造修复诊断包。

    处理三种异常类型：
      - ValidationError → 解析 JSON → YAML + 错误路径摘要
      - JSONDecodeError  → 直接用 raw_text 作为 failed_yaml
      - 其他 Exception   → 返回 {"failed_yaml": raw_text, "error_roadmap": str(error)}
    """
    from json import JSONDecodeError

    # ① 尝试解析 JSON → YAML
    if isinstance(error, ValidationError):
        try:
            parsed_dict = json.loads(raw_text)
            failed_yaml = yaml.dump(parsed_dict, allow_unicode=True, indent=2)
        except Exception:
            failed_yaml = raw_text

        # ② 精简错误路径（不输出原始值，省 token + 聚焦）
        error_lines = []
        for err in error.errors():
            path = " -> ".join(str(p) for p in err["loc"])
            error_lines.append(f"  - [{path}] {err['msg']}")
        error_roadmap = "\n".join(error_lines)

    elif isinstance(error, JSONDecodeError):
        failed_yaml = raw_text
        error_roadmap = f"JSON 解析失败（第 {error.lineno} 行第 {error.colno} 列）: {error.msg}"

    else:
        failed_yaml = raw_text
        error_roadmap = str(error)[:500]

    return {
        "failed_yaml": failed_yaml,
        "error_roadmap": error_roadmap,
        "raw_text": raw_text,
    }
```

**修复 Prompt 模板**：

```text
### 输出 JSON 结构骨架（必须严格遵循，键名、层级不能增减）
```json
{skeleton}
```

上一轮你的输出（YAML 格式），标注了校验不通过的结构：
```yaml
{failed_yaml}
```

校验错误定位（对照上述 YAML 的缩进层级排查）：
{error_roadmap}

{data_factory_methods_section}
{api_definitions_section}

请修正上述问题，严格按照骨架重新输出完整的 JSON。
```

其中 `{data_factory_methods_section}` 和 `{api_definitions_section}` 由调用方按错误类型条件注入：

```python
# _run_yaml_rounds 修复轮构造逻辑
repair_vars = {
    "skeleton": skeleton_text,
    "failed_yaml": repair_ctx["failed_yaml"],
    "error_roadmap": repair_ctx["error_roadmap"],
    "data_factory_methods_section": "",
    "api_definitions_section": "",
}

# 条件注入：错误涉及工厂方法时
if _error_mentions_placeholder(error_roadmap):
    repair_vars["data_factory_methods_section"] = (
        "### 数据工厂方法（对照修正占位符）\n" + factory_methods_text
    )

# 条件注入：错误涉及 API 匹配时
if _error_mentions_api(error_roadmap):
    repair_vars["api_definitions_section"] = (
        "### 接口定义（对照修正 url/method/参数）\n" + api_defs_json
    )
```

条件判断函数通过关键词匹配 error_roadmap。**error_roadmap 格式固定为 `"  - [路径] 错误描述"`**，路径使用 `->` 分隔（由 `prepare_repair_context()` 生成）。任何对 error_roadmap 格式的修改必须同步更新以下匹配逻辑，否则条件注入会静默失效：

```python
def _error_mentions_placeholder(roadmap: str) -> bool:
    """错误是否涉及占位符/工厂方法。依赖 error_roadmap 的 " - [路径] 描述" 格式。"""
    return any(kw in roadmap for kw in ("占位符", "get_extract_data", "${",
                                         "factory", "unknown function"))

def _error_mentions_api(roadmap: str) -> bool:
    """错误是否涉及 API 匹配（url/method/参数）。同上格式约束。"""
    return any(kw in roadmap for kw in ("url", "method", "api_name",
                                         "baseInfo", "header", "参数"))
```

**设计原理**：

| 给 LLM 什么 | 为什么 |
|------------|------|
| `error_roadmap`（精简路径+错误类型） | 快速定位，不靠 LLM 自己从几千字文档里找 |
| `failed_yaml`（YAML 可视化） | 一眼看出"testCase 写到 data 外面了"，比 JSON 数括号快 |
| `skeleton`（结构骨架） | 修正时的正确目标结构 |
| `data_factory_methods`（条件注入） | 仅当错误涉及占位符/工厂函数时给，让 LLM 对照注册表修正 |
| 不注入 `user_context` / `test_case_logic` | 这些已在 failed_yaml 中体现，再注入反而分散注意力 |

#### 后续优化项：纯结构快速修复路径

> **状态：暂缓，Phase 4 验证后按需启用。**

当前修复轮保留完整上下文（工厂方法 + API 定义），两种错误都能修。但全量上下文的 Token 成本高。

**启用条件**（需同时满足）：

1. Phase 4 验证跑通后发现：**所有失败都是嵌套层级错误**（数据本身填对了，只是放错了位置）
2. **修复轮 Token 消耗占比超过总用量的 30%**

满足后，新增第三轮"纯结构修复"——只给骨架 + failed_yaml + error_roadmap，不给工厂方法和 API 定义。失败后依旧有现有修复轮兜底。

```
第 1 轮：全量上下文（当前方案，不动）
第 2 轮：全量上下文（修复数据值 + 结构）
第 3 轮：纯结构修复（新增，仅当满足启用条件时触发）
  输入：skeleton + failed_yaml + error_roadmap（不给工厂方法和 API 定义）
  目标：修复嵌套层级错误，Token 成本 ~1/3
```

### 5.4 骨架生成位置与缓存

同一个 feature 下所有 YAML（含 setup/teardown 和每条用例）共用相同的 `TestData` 骨架。每次调用 `_generate_one_yaml()` 都重新 `model_json_schema()` 是浪费。

**单次生成，沿链路传递**：

```
_generate_all_yamls()     ← 入口：调用 generate_json_skeleton(TestData) 一次
       │
       ├→ _generate_yamls_v2()  → _run_yaml_rounds(story_tasks, ...)
       │                                    │
       │                                    └→ _generate_one_yaml(row, ..., skeleton=skeleton_text)
       │
       └→ _generate_yamls_v1()  → _run_yaml_rounds(yaml_tasks, ...)
                                            │
                                            └→ _generate_one_yaml(row, ..., skeleton=skeleton_text)
```

**不是模块级全局变量**的原因：`generate_json_skeleton()` 依赖 Pydantic 模型的 `json_schema_extra`，模型定义可能在运行时被插件/配置动态修改。每次 `_generate_all_yamls()` 入口调用一次在"一次生成任务内不变"和"响应模型变更"之间取平衡。

### 5.5 `_invoke_structured()` 的变更范围

`_invoke_structured()` 在 `nodes.py:806` 中被多处使用：

| 调用方 | method | 是否改动 |
|--------|--------|:---:|
| `_generate_one_yaml()` (YAML 生成) | `json_mode` | ✅ 改：拆分为直接调用 + model_validate |
| `_generate_excel_plan_node()` (Excel 生成) | `json_mode` | ❌ 不动 |
| `_format_data_plan()` (数据规划) | `json_mode` | ❌ 不动 |
| 其他格式转换节点 | `json_mode` / `function_calling` | ❌ 不动 |

**`_invoke_structured()` 本身保留不动**。只有 YAML 生成的调用路径从 `_invoke_structured(TestData, method="json_mode")` 改为手动 `llm.bind(response_format=...)` + `model_validate()`。其他调用方（Excel 生成、数据规划等）的 Pydantic 模型结构简单（扁平 dict，无深层嵌套），当前流程合格率没问题，不需要改。

### 5.6 `Dict[str, Any]` 字段的 `example_keys` 动态生成

`TestCase.json` 和 `TestCase.params` 的 `example_keys` 设为 `{}` 意味着骨架中这些字段渲染为 `{}`，LLM 不知道该往里填什么键。

但静态写死 `example_keys` 也不对——不同接口的参数名完全不同。例如：
- 电表新增接口的 `json` 需要 `name`, `code`, `meterTypeCode`
- 计费方案接口的 `json` 需要 `payConfigName`, `feeType`, `details`

**方案：不从 `json_schema_extra` 静态取值，从接口定义动态生成**。

`_generate_one_yaml()` 拿到 `api_defs_json`（当前 story 涉及的接口定义），可以为骨架中的 `json` / `params` 字段填充对应接口的 parameters 键名。

```python
def enrich_skeleton_for_case(
    skeleton_text: str,
    api_defs: list[dict],
    case_api_sequence: list[str],
) -> str:
    """根据当前用例的 API 序列，用接口定义的 parameters 键名填充骨架中的 json/params 空位。

    骨架中 {"json": {}, "params": {}} → {"json": {"name": "", "code": "", ...}, "params": {...}}
    """
    # 从 api_defs 中匹配 case 需要的接口，提取 parameters 键名
    # 回填到 skeleton_text 的对应位置
    ...
```

**风险说明**：当前实现中 json/params 在骨架中渲染为 `{}`，LLM 需要从 thinking 分析中推断参数名。若后续测试发现因缺少参数键名提示导致结构错误，将启用 §5.6 的动态 enrichment 机制——该机制与 `api_defs_json` 的传入绑定，不额外增加调用成本。

**动态 enrichment 进入 Phase 1**：作为骨架生成的增强步骤，从 `api_defs_json` 中提取第一个匹配接口的 parameters 键名填充到 `{}` 中。实现简化版：匹配 url → 取该接口的 parameters keys → 渲染 `{"key1": "", "key2": "", ...}`。

### 5.7 错误类型分发逻辑

`_generate_one_yaml()` 拆分后，校验阶段可能抛出三种异常：

```python
raw_text = llm_with_json.invoke(prompt)
try:
    parsed = _extract_json_from_thinking(raw_text)
    test_data = TestData.model_validate(parsed)
except json.JSONDecodeError as e:
    # JSON 不合法 → prepare_repair_context 会将 raw_text 作为 failed_yaml
    repair_ctx = prepare_repair_context(raw_text, e)
    raise RepairNeeded(repair_ctx) from e
except ValidationError as e:
    # JSON 合法但结构不对 → prepare_repair_context 渲染 YAML + error_roadmap
    repair_ctx = prepare_repair_context(raw_text, e)
    raise RepairNeeded(repair_ctx) from e
except Exception as e:
    # LLM 调用失败、网络超时等 → 不进入修复轮，直接向上抛
    raise
```

`RepairNeeded` 是自定义异常，携带 `repair_ctx`，由 `_run_yaml_rounds()` 捕获后登记到失败清单并构造修复轮 prompt。

### 5.8 thinking 与 json_mode 口径对齐

`analyze_yaml_data_prompt()`（thinking 阶段）的 system prompt 中有旧版结构描述：

```
- baseInfo: 仅含 api_name/url/method/header 四个字段。
- testCase: case_name/json|params|data/extract|input_extract/validation
```

这些是手写的旧结构，与新的 JSON 骨架口径不一致。thinking LLM 可能输出"这一步的 testCase 应该嵌套在 data 数组内"这种旧版理解，而 json_mode 收到的是全新骨架——两个阶段对"正确结构"的认知脱节。

**改造**：`analyze_yaml_data_prompt()` 也注入 `{skeleton}`，并要求 thinking 按骨架分析数据依赖。同时删除旧的手写结构描述行，相当于 skeleton 成为两个阶段共同的"结构词典"。

```text
# analyze_yaml_data_prompt 改造后

你是资深测试数据构造专家。根据【接口定义】、【用例逻辑】和【JSON 结构骨架】，
深度分析需要生成的测试数据。

### 输出 JSON 结构骨架（你的分析必须基于此结构）
{skeleton}

### 你的分析要点
1. 每个步骤对应 skeleton.data 中的一个元素
2. 请求参数（json/params/data）从接口定义中选择，按 HTTP 方法决定
3. 断言字段从接口 returns 中选择
4. 动态值从数据工厂清单中选择
...
```

**效应**：thinking 分析时就知道最终 JSON 长什么样，它的分析文本能自然映射到 skeleton 的每个层级，json_mode 阶段不会出现"thinking 说了一堆 testCase 怎么嵌套，但 skeleton 里没有 testCase 包装层"这种脱节。

**thinking 与 json_mode 共用同一个 skeleton 字符串实例**：`_generate_all_yamls()` 入口调用 `generate_json_skeleton(TestData)` 一次，同一个字符串沿链路传入 `_thinking_per_story()`（thinking 阶段）和 `_generate_one_yaml()`（json_mode 阶段）。不是两次独立生成，确保两阶段看到的骨架逐字符一致。

### 5.9 骨架数组长度标注

骨架示例中 `data: [{"baseInfo": {...}, "testCase": [{...}]}]` 只有一个元素，但实际 YAML 可能有多个步骤（多接口调用）或单步多 case。

**LLM 需要知道数组长度由实际数据决定，不是固定为 1。**

在骨架 JSON 后面追加一行提示：

```text
### 输出 JSON 结构骨架（键名、层级不能增减，数组长度按实际步骤数展开）
{skeleton}

注意：data 数组的每个元素对应一个 API 调用步骤（多步骤 = 多个元素）。
testCase 数组的每个元素对应该步骤的一条用例变体。
数组长度由【用例逻辑】中的步骤数决定，骨架中只展示 1 个元素作为示例。
```

这比在 JSON 骨架内写 `[..., ...]` 更明确——LLM 不会误解为"恰好 2 个元素"。

### 5.10 可选字段的渲染规则

`Pydantic` 中 `dict | None` / `list | None` 类型的字段在 `model_json_schema()` 中表示为 `anyOf: [{type: object}, {type: null}]`。

`generate_json_skeleton()` 的实际实现规则（与原始方案的差异）：

| 字段 | 类型 | `example_keys` | 骨架行为 |
|------|------|:---:|------|
| `TestCase.json` (`request_body`) | `Optional[Dict]` | `{}` | ✅ 渲染为 `{}`（有 example_keys，LLM 需要知道填什么键） |
| `TestCase.params` | `Optional[Dict]` | `{}` | ✅ 渲染为 `{}`（同上） |
| `TestCase.extract` | `Optional[Dict]` | 无 | ❌ 跳过，不渲染 |
| `TestCase.input_extract` | `Optional[Dict]` | 无 | ❌ 跳过，不渲染 |
| `TestCase.extract_list` | `Optional[Dict]` | 无 | ❌ 跳过，不渲染 |
| `TestCase.form_data` (`data`) | `Optional[Dict]` | 无 | ❌ 跳过，不渲染 |
| `TestCase.validation` | `List[dict]` | — | ✅ 渲染为 `[{}]`（非 Optional，default_factory=list） |

**核心规则**：Optional[dict/list] 字段 → 有 `example_keys` 则渲染为 `{}`，无则跳过。这比原始方案（"所有 Optional 都跳过"）更精细——`json` 和 `params` 虽然在 Pydantic 中 Optional，但每条用例几乎必有其一，骨架中出现 `{}` 占位让 LLM 知道要往里填参数。

骨架中不显示的字段 = 不需要时不用写。需要用时 LLM 从 thinking 分析中知道要加 `"extract": {"key": "$.jsonpath"}`。

### 5.11 骨架字段完整性校验

当前 `_generate_json_skeleton()` 从 `model_json_schema()` 递归生成，理论上不会遗漏任何字段。但需要确认以下字段是否被正确渲染：

| 字段 | 所在模型 | 类型 | 当前 example_keys |
|------|---------|------|:---:|
| `input_extract` | TestCase | `dict \| None` | 可选，骨架应为 `null`（按 §5.10 规则 LLM 会删除） |
| `extract_list` | TestCase | `list \| None` | 同上 |
| `data`（表单体） | TestCase | `dict \| None` | 同上 |
| `cookies` | baseInfo | `dict` | 骨架应为 `{}`（baseInfo 的 example_keys 需要补充） |
| `header` | baseInfo | `dict` | 已在 §3.2 的 example_keys 中 |

**验证步骤（Phase 1 步骤 2 的单元测试里加）**：对 `TestData.model_json_schema()` 的输出做断言——确保所有字段出现在骨架中，没有静默遗漏。

### 5.12 `_generate_one_yaml` 签名变更

当前签名：

```python
def _generate_one_yaml(self, row, api_defs_json, user_ctx, output_path, repair_ctx=None)
```

拆分后需要新增 `skeleton_text` 参数（由 `_generate_all_yamls` 入口生成一次后传入）：

```python
def _generate_one_yaml(self, row, api_defs_json, user_ctx, output_path,
                       skeleton_text: str, repair_ctx=None)
```

`_run_yaml_rounds()` 的 `gen_func` 参数也需对应调整签名。现有代码中 `gen_func` 默认为 `self._generate_one_yaml`，通过 `functools.partial` 或 lambda 绑定 `skeleton_text`：

```python
# _generate_all_yamls 入口
skeleton_text = generate_json_skeleton(TestData)

# 传入 _run_yaml_rounds 时绑定 skeleton
gen = gen_func or (lambda row, api, ctx, path, rctx=None:
                   self._generate_one_yaml(row, api, ctx, path, skeleton_text, rctx))
```

---

## 六、实施步骤

### Phase 1：基础设施

1. `prompts/response_model.py`：`TestCase` / `StepData` 字段添加 `json_schema_extra["example_keys"]`
2. `agent_components/generators.py`：新增模块级函数 `generate_json_skeleton(model_class) -> str`
3. `agent_components/generators.py`：增强 `_extract_json_from_thinking()`，加 L1 直接解析 + L2b 无语言标记代码块
4. `agent_components/generators.py`：新增模块级函数 `prepare_repair_context(raw_text, error) -> dict`
5. 单元测试：验证骨架生成 + JSON 提取各层降级 + 诊断包构造（三种异常类型各测一遍）

### Phase 2：Prompt 重构

6. `prompts/extraction_prompts.py`：`format_yaml_data_prompt()` 删手写结构、加 `{skeleton}` 占位符、精简规则
7. `prompts/extraction_prompts.py`：`analyze_yaml_data_prompt()` 注入 `{skeleton}`，删除旧手写结构描述行，确保 thinking 和 json_mode 对结构的认知一致（§5.8）
8. `prompts/extraction_prompts.py`：`repair_yaml_data_prompt()` 重构：
   - 新增 `{skeleton}` + `{failed_yaml}` + `{error_roadmap}`
   - 新增 `{data_factory_methods_section}` + `{api_definitions_section}`（条件注入，空字符串时不显示）
   - 删除 `{data_analysis}` + `{user_context}` + `{test_case_logic}` + `{error_pattern_summary}` + `{prior_output}` + `{error_detail}` + `{post_check_issues}`

### Phase 3：流程拆分

9. `agent_components/generators.py`：`_generate_all_yamls()` 入口调用 `generate_json_skeleton(TestData)` 一次，沿链路传入 `_run_yaml_rounds()` → `_generate_one_yaml()`
10. `agent_components/generators.py`：`_generate_one_yaml()` 新增 `skeleton_text` 参数（§5.12），拆分为 LLM 调用 → `_extract_json_from_thinking()` → `TestData.model_validate()` → 写 YAML；校验失败时调 `prepare_repair_context()` 构造诊断包，抛 `RepairNeeded`
11. `agent_components/generators.py`：`_run_yaml_rounds()` 捕获 `RepairNeeded`，调 `_error_mentions_placeholder()` / `_error_mentions_api()` 判断错误类型，条件注入 `data_factory_methods_section` 和 `api_definitions_section`

### Phase 4：验证

12. 单元测试：`_error_mentions_placeholder()` / `_error_mentions_api()` 关键词匹配覆盖
13. 端到端测试：完整跑一轮看合格率变化
14. 对比修复轮自查质量（有完整诊断包 vs 截断 snippet）

### E2E 实测修正确认（2026-07-25）

健身房_4（47 用例，21 个接口定义）端到端跑通后发现两个骨架相关问题，已修复：

| # | 问题 | 表现 | 修复 |
|---|------|------|------|
| 1 | 骨架同时显示 `json: {}` 和 `params: {}` | LLM 两个都填 → 28 次 B9 三选一校验失败 | `format_yaml_data_prompt()` 和 `analyze_yaml_data_prompt()` 均新增醒目互斥警告（§3.3、§5.8），含正反例 |
| 2 | `baseInfo.header` 渲染为 `""` 而非 `{}` | `example_keys` 所有值按 string 处理 → 23 次 header 类型错误 | `generate_json_skeleton()` 更新：`example_keys` 的 value 决定渲染类型（dict→`{}`, list→`[]`, str→`""`） |

---

## 七、实施决策记录（2026-07-25 确认）

| # | 决策点 | 结论 | 理由 |
|---|--------|------|------|
| 1 | 修复轮是否保留工厂/API 定义？ | **保留**。按错误类型条件注入（§5.3）：含 `${}`/占位符错误 → 注入工厂方法，含 url/method 错误 → 注入 API 定义。纯结构快速修复路径为后续优化项（§5.3） | 数据值错误需要工厂方法对照修正，API 匹配错误需要接口定义对照修正 |
| 2 | 骨架生成位置 | `_generate_all_yamls()` 入口调用 `generate_json_skeleton(TestData)` 一次，沿链路传入 `_run_yaml_rounds()` → `_generate_one_yaml()` + `analyze_yaml_data_prompt()`（§5.4、§5.8） | 同 feature 下所有 YAML 共用同一骨架；每次任务入口生成一次平衡"不变"和"响应模型变更" |
| 3 | raw_text 传递方式 | `RepairNeeded` 自定义异常携带 `repair_ctx`，`prepare_repair_context(raw_text, error)` 生成诊断包（§5.7） | 绕过 `_invoke_structured()` 后 raw_text 不再从 LangChain 异常消息提取；三种异常类型各走各的构造逻辑 |
| 4 | `_extract_json_from_thinking` 顺序 | L1=`json.loads(raw)` → L2=代码块正则 → L2b=无标记代码块 → L3=`find+rfind`（§5.2） | DeepSeek json_object 实际表现可靠，L1 O(n) 无正则开销最快；L2/L3 仅作安全网 |
| 5 | 骨架可选字段处理 | `Optional[dict/list]` 字段不渲染到骨架（§5.10），LLM 需要时自行添加，`exclude_none=True` 写 YAML 时自动剔除未填项 | 从源头消除"骨架显示 null 又要删掉"的矛盾，比 prompt 规则更干净 |
| 6 | `_invoke_structured` 变更范围 | 仅 YAML 生成路径改（§5.5）。Excel 生成、数据规划等不动 | 其他调用方模型结构简单（扁平 dict），无嵌套问题，当前流程合格率正常 |
| 7 | thinking 与 json_mode 共享骨架 | `analyze_yaml_data_prompt()` 也注入 `{skeleton}`（§5.8），与 `format_yaml_data_prompt()` 共用同一字符串实例 | 消除两个阶段对结构认知的脱节，thinking 分析能自然映射到 skeleton 层级 |
| 8 | 纯结构快速修复路径 | **暂缓**（§5.3 后续优化项）。启用条件：① Phase 4 验证后所有失败都是嵌套层级错误 ② 修复轮 Token 占比 > 总用量 30% | 当前修复轮保留完整上下文的方案先验证；满足条件后再加第三轮纯结构修复降本 |
