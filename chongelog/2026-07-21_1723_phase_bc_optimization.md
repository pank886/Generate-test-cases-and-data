# Phase B/C 联动优化：模块级接口去重 + 依赖映射表 + 下游分流

| 项目 | 内容 |
|:---|:---|
| 讨论日期 | 2026-07-21 |
| 变更类型 | Phase C 入口新增 dep_map 生成 + 下游重构（Excel 不加列，Phase B 不变） |
| 涉及文件 | `response_model.py`, `extraction_prompts.py`, `nodes.py`, `graph_builder.py`, `generators.py`, `state.py`, `web/tasks.py` |

---

## 一、问题背景

### 1.1 Token 浪费

Phase C YAML 生成时，每条用例独立调 LLM（50 条 × 2 次 = 100 次调用），每次 prompt 都注入**完整的 `api_defs_json`**（全部接口定义，不分模块）。同一份接口文档被重复发送 100 次，token 浪费严重。根因是 `api_defs.json` 中的 `ApiDefinition` 不含 `module` 字段，无法按模块过滤。

### 1.2 用例逻辑为空（setup/teardown）

- **teardown**：steps 硬编码为 `"调用删除/清理接口"`，无具体 API/参数/断言，LLM 无法生成有效 YAML。
- **setup**：steps 来自 Sheet2 共享前置的 `pre['steps']`，若 LLM 未填写则内容为空。

两者都缺少必要的上下文：不知道调哪个 API、传什么参数、断言什么字段。

---

## 二、Phase B 上游改造

### 2.1 `api_sequence`：用例的 API 调用序列

**不存入 Excel**。`api_sequence` 仅存在于 `dependency_map.json` 的 `case_api_sequences` 字段中（见 §2.3）。

**格式**：`步骤名:HTTP方法 URL`，List 结构。

```
["创建订单:POST /order/create", "查询订单:GET /order/query/{order_id}"]
```

**前置条件的 api_sequence 单独存放**：同 story 下所有用例共享相同的前置步骤，由 LLM 在 `dependency_map.json` 中输出一次 `story_pre_api_sequence`，Phase C 代码负责拼接：

```
完整序列 = story_pre_api_sequence + case_api_sequences["TC-xxx"]

示例:
  story_pre_api_sequence: ["前置鉴权:POST /login", "前置用户:GET /get_test_user"]
  TC-001 的 api_sequence: ["创建订单:POST /order/create"]
  → 拼接结果: ["前置鉴权:POST /login", "前置用户:GET /get_test_user", "创建订单:POST /order/create"]
```

**提取方式**：Phase B LLM 在 `generate_dependency_map` 节点中，根据 story 的共享前置和接口定义，一次性输出 `story_pre_api_sequence` 和各用例的 `case_api_sequences`。

**优于纯 LLM 方案**：
- LLM 不需要在每条用例中重复输出相同的前置 URL 序列
- 前置步骤变更时只改一处（dependency_map.json）
- Excel 不需要新增列，现有 9 列结构不变

**步骤名来源**：直接用 Excel 中已有的用例 `title` 或 steps 首行动词，格式统一为 `步骤名:HTTP方法 URL`。Phase C 代码解析时用 `split(":", 1)` 拆分。不引入额外标签系统。

### 2.2 `generate_dependency_map` 节点的放置位置

**最终决策：放在 Phase C 入口（`_confirm_plan_bg` 内），不作为 Phase B 的独立 LangGraph 节点。**

**原因**：

| 问题 | 说明 |
|------|------|
| 审核不可行 | `dependency_map.json` 是嵌套 JSON，人无法审核。放在 Phase B 只会让用户对着 Excel 点确认后就"卡住" |
| 时效浪费 | Phase B 结束时已生成 dep_map，但用户可能不确认。如果重跑 Phase B，旧的 dep_map 残留 |
| 数据新鲜度 | Excel 和 dep_map 生成之间存在时间差（用户确认过程）。放在 Phase C 同一会话内生成，上下文一致 |
| 原子性 | 同一个 `_confirm_plan_bg` 任务内生成 → 加载 → 消费，失败一起报错，不产生半成品 |

**新流程**：

```
Phase B（LangGraph，不变）:
  confirm_intent → retrieve_docs → ... → generate_excel_plan → END
  产物: test_plan.xlsx + api_defs.json

Phase C（_confirm_plan_bg，后台任务）:
  Step 0: [新增] 生成 dependency_map.json（原 dep_map 生成 节点逻辑移植）
  Step 1: 加载 dep_map + 预校验
  Step 2: 生成 .py 文件
  Step 3: Prefetch 流水线生成 YAML
```

**`generate_dependency_map` 的实现保持不变**：仍然是 thinking 模式 LLM 调用，输出 JSON → `json.loads()` → Pydantic 校验 → 修复轮。只是调用方从 LangGraph 节点变为 `_confirm_plan_bg` 中的同步函数。

**`product_docs` 的获取**：原来 Phase B state 中有 `product_docs`（ChromaDB 检索结果）。移到 Phase C 后，用 `confirmed_module`（用户选择的模块名）作为查询 key 重新检索 ChromaDB，开销很小（单次检索，~1-2s）。

**对 `graph_builder.py` 的影响**：删除 `generate_dependency_map` 节点，`generate_excel_plan → generate_dependency_map → END` 恢复为 `generate_excel_plan → END`。Phase B 工作流回到原有 7 个节点。

### 2.3 `generate_dependency_map` 实现（保留原 dep_map 生成 逻辑）

```python
def _generate_dependency_map_node(state: WorkflowState) -> WorkflowState:
    # 1. 从 state 读取 api_defs, excel_path, product_docs
    # 2. 组装 prompt: Excel 全量用例 + story 分组要求 + JSON Schema
    # 3. 调用 LLM（thinking=on，thinking trace 落 thinking_trace.log）
    # 4. 尝试 json.loads() 解析 LLM 输出的 JSON
    # 5. 调用 _validate_dependency_map()（§4.1.2 校验规则）
    #    ├─ 校验通过 → 写临时文件 .tmp → os.replace 正式落盘
    #    └─ 校验失败：
    #        ├─ REPAIR_ATTEMPTS < 阈值 → 错误详情注入 prompt，goto step 3（修复轮）
    #        └─ 超限 → 删临时文件，标记 state["error"]，终止流程
```

**_validate_dependency_map() 的 Pydantic 优先原则**：每个 story 对象内 7 个字段齐全检查 + case_api_sequences / internal_dependency / decision_map 的 Key 集合一致性检查，尽量写在 Pydantic 模型的 `@model_validator` 中自动校验，减少手写 if 代码。仅 Pydantic 无法表达的逻辑（如 Excel 中 TC 是否实际存在、case_api_sequences 是否为空数组）放在外部函数中。

输入：

| 输入 | 来源 |
|------|------|
| Excel 计划 | `_read_excel_rows()` 读取 |
| 接口定义 | `api_defs.json`（Phase B 落盘的快照） |
| 产品文档 | ChromaDB 检索（用 `user_ctx` 查询） |
| 数据工厂方法 | `data_factory/methods.yaml`（`_load_factory_methods()` 渲染） |
| 模块树 | `module_tree.get_tree()` |

#### 2.2.1 `generate_dependency_map_prompt()` 设计要点

dep_map 生成 的 Prompt 除给出 Schema 定义外，必须明确四条铁律：

**① 输出 `teardown_api_sequence`（按数据流判断）**
```
对每个 story，判断写操作（POST/PUT/DELETE）的产物是否需要清理：
- 下游 case 需消费本 case 的产物 → 不清理，teardown_api_sequence 留空 []
- 有合法的清理路径（产品规则允许删除/回滚）→ 填写具体步骤
- 不存在合法清理路径（如被引用实体不可删除）→ 留空 []
禁止编造无法执行的清理步骤。
```

**② `decision_map` 中 `params` 的赋值原则**
```
dep_map 生成时已注入数据工厂方法字典（methods.yaml），LLM 可以输出精确的 ${} 语法：
- 用例步骤中明确写死的值 → 直接输出（如 "pageSize": 10）
- 需要动态生成的值 → 从工厂方法字典中选择正确的函数名和参数
  （如 "plate": "${random_plates(1)}"）
- 依赖前置步骤的值 → 输出 ${get_extract_data(xxx)} 占位符，
  变量名 xxx 来自 internal_dependency 中定义的 output_var
- 禁止编造工厂字典中不存在的函数名
```
> **分发**：Phase C Thinking 拿到 dep_map.decision_map 后，可结合去重 API 定义做进一步精炼（补充漏填字段、修正参数值），但 dep_map 中的 `${}` 引用应该是基本可用的。这降低了 Phase C Thinking 的工作量——从"从零填写"变成"校验 + 补充"。

**③ `internal_dependency` 中 `extract_path` 的来源**
```
extract_path 必须从【接口定义】的 returns 字段中提取，与响应 schema 严格对齐。
禁止凭空猜测 JSONPath。如果 returns 中找不到对应字段，不填 extract_path，
在 used_by 中标注依赖关系即可。
```

**④ `case_id` 格式一致性（禁止格式转换）**
```
所有 key（case_api_sequences / decision_map / internal_dependency）中的 case_id
必须与 Excel 中"用例编号"列的值逐字符一致，严禁做任何格式转换。
例如: Excel 中写 "TC-1" 则 JSON 中必须写 "TC-1"，不能写成 "TC-001"；
Excel 中写 "TC-001" 则 JSON 中必须写 "TC-001"，不能写成 "TC-1"。
格式不一致将导致 Phase C 的 case_id 精确匹配断裂，全部用例 YAML 生成失败。
```

#### 2.2.2 模型设计理念：Thinking 决策 → Json_Mode 填表 → 代码拼接

整个 DependencyMap 模型体系遵循一条核心信条：**Thinking 做决策，Json_Mode 做填表，代码做拼接。三者通过 JSON 桥接，各司其职。**

**两层 Thinking 的分工**：

| 层级 | 谁 | 做什么 | 输入 | 输出 | 落盘？ |
|------|---|------|------|------|:---:|
| 结构性决策 + 初步赋值 | dep_map 生成（Phase C Step 0） | 决定"哪个接口、哪些字段、谁依赖谁"，并用工厂字典填写初步 `${}` 值 | Excel + api_defs + 产品文档 + **methods.yaml** | `decision_map`（写入 dep_map.json） | ✅ 落盘 |
| 补充校验 | Phase C Thinking | 结合去重 API 定义，补充漏填字段、修正参数值 | dep_map.decision_map + 去重 api_defs | `refined_decision_map`（内存） | ❌ 不落盘 |

> **关键**：dep_map 生成时已有 `methods.yaml`，可以输出基本正确的 `${}` 引用。Phase C Thinking 的角色从"从零填写"降级为"校验 + 补充 + 去重 API 上下文精炼"，工作量显著减少。

```
                ┌─────────────────────────────────┐
                │     dep_map 生成: Thinking LLM      │
                │  "厘清关系，输出结构性决策"        │
                │  输入: Excel + api_defs + 产品文档 │
                └──────────────┬──────────────────┘
                               │
          ┌────────────────────┼────────────────────┐
          ▼                    ▼                    ▼
story_pre_api_sequence   decision_map        internal_dependency
case_api_sequences       (结构性，缺工厂方法)  "谁产出→谁消费"
teardown_api_sequence                        extract_path
          │                    │                    │
          └────────────────────┼────────────────────┘
                               │  dependency_map.json（落盘）
                               │
                ┌──────────────▼──────────────────┐
                │       Phase C: 代码层             │
                │  "拼接、过滤、构造"                │
                │  full_seq = pre + case            │
                │  api_defs = filter_by_urls(...)   │
                │  YAML.extract = 代码生成           │
                └──────────────┬──────────────────┘
                               │
                ┌──────────────▼──────────────────┐
                │   Phase C: Thinking LLM（每story）│
                │  "精炼赋值方案"                   │
                │  输入: dep_map.decision_map       │
                │       + methods.yaml 工厂字典     │
                │       + 去重 api_defs             │
                │  输出: refined_decision_map       │
                │        （内存传递，不落盘）         │
                └──────────────┬──────────────────┘
                               │
                ┌──────────────▼──────────────────┐
                │    Phase C: Json_Mode LLM        │
                │  "纯填表，不做判断"                │
                │  输入: refined_decision_map       │
                │       + 去重 api_defs             │
                │  输出: YAML (params + assertions) │
                └──────────────────────────────────┘
```

**各字段的消费关系**：

| 字段 | 谁产出 | 谁消费 | 设计动机 |
|------|--------|--------|---------|
| `stories[]` | Thinking | Phase C 代码 | 一个 feature 一个文件，一次 `json.load()` 拿到全量 |
| `story_name` | Thinking | Phase C 代码+日志 | 中文存储。LLM 用中文思考，Phase C 用 `story_en_map` 映射目录 |
| `story_pre_api_sequence` | Thinking | Phase C 代码 | 独立于 case。同 story N 条用例共享，存一份。代码拼接 `pre + case` |
| `case_api_sequences` | Thinking | Phase C 代码+Json_Mode | `Dict[case_id, List[str]]`，O(1) HashMap 查找 |
| `decision_map` | Thinking | Json_Mode | 存"指令"不存"值"。`"${random_plates(1)}"` 和 `10` 都是 Json_Mode 照抄的字符串 |
| `internal_dependency` | Thinking | Phase C **代码** | **代码层生成 extract**。Json_Mode 不接触 extract 字段，保持纯填表 |
| `cross_module_dependency` | Thinking | Phase C thinking+前端 | 文档型字段。标注"setup 需要哪个外部模块的哪个接口"，实际 schema 从 api_defs 取 |
| `teardown_api_sequence` | Thinking | Phase C 代码 | 允许 `[]`。LLM 按数据流判断是否需要清理 |

**为什么 `internal_dependency` 和 `decision_map` 必须分离**：

如果合并，Json_Mode 会看到 `extract_path` 和 `output_var`。一旦看到这些信息，它就"想做判断"——在哪里加 extract、用什么 JSONPath。分离后：
- 代码层从 `internal_dependency` 读 `extract_path` → 直接构造 YAML 的 `extract` / `input_extract` 块
- Json_Mode 只从 `decision_map` 拿 `params` + `assertions` → 原样填入 YAML

职责边界清晰，每个环节只做自己该做的事。

### 2.3 依赖映射表 JSON 结构（独立文件）

**文件路径**：与 `test_plan.xlsx` 同级目录（即 `output_dir/`），命名为 `dependency_map.json`。**一个 feature 一个文件**，内含该 feature 下所有 story 的依赖映射。

**顶层结构**：`stories` 数组，每个元素为一个 story：

```json
{
  "stories": [
    {
      "story_name": "订单CRUD",
      "story_pre_api_sequence": [
        "前置鉴权:POST /login",
        "前置用户:GET /get_test_user"
      ],
      "case_api_sequences": {
        "TC-001": ["创建订单:POST /order/create"],
        "TC-002": ["查询订单:GET /order/query/{order_id}"],
        "TC-003": ["取消订单:POST /order/cancel"]
      },
      "decision_map": {
        "TC-001": {
          "steps": [
            {
              "api": "POST /order/create",
              "params": {
                "amount": 500,
                "plate": "${random_plates(1)}",
                "start_time": "${get_current_time(hms)}"
              },
              "assertions": [
                {"eq": {"retCode": 1}},
                {"contains": {"$.msg": "成功"}}
              ]
            }
          ]
        },
        "TC-002": {
          "steps": [
            {
              "api": "GET /order/query/{order_id}",
              "params": {
                "order_id": "${get_extract_data(order_id)}"
              },
              "assertions": [
                {"eq": {"retCode": 1}},
                {"eq": {"$.data.status": "active"}}
              ]
            }
          ]
        },
        "TC-003": {
          "steps": [
            {
              "api": "POST /order/cancel",
              "params": {
                "order_id": "${get_extract_data(order_id)}"
              },
              "assertions": [
                {"eq": {"retCode": 1}}
              ]
            }
          ]
        }
      },
      "internal_dependency": {
        "TC-001": {
          "output_var": "order_id",
          "extract_path": "$.data.id",
          "used_by": ["TC-002", "TC-003"]
        },
        "TC-002": { "output_var": null, "extract_path": null, "used_by": [] },
        "TC-003": { "output_var": null, "extract_path": null, "used_by": [] }
      },
      "cross_module_dependency": {
        "前置鉴权": {
          "依赖模块": "用户模块",
          "需获取变量": "user_token",
          "获取接口": "POST /login"
        },
        "前置用户": {
          "依赖模块": "用户模块",
          "需获取变量": "test_user_id",
          "获取接口": "GET /get_test_user"
        }
      },
      "teardown_api_sequence": ["取消订单:POST /order/cancel"]
    }
  ]
}
```

**字段说明**：

| 字段 | 层级 | 说明 |
|------|------|------|
| `stories` | 顶层 | story 数组，一个 feature 的所有 story |
| `story_name` | story 内 | 中文 story 名，与 Excel `@allure.story` 列对应 |
| `story_pre_api_sequence` | story 内 | 该 story 共享前置条件的 API 序列，LLM 输出一次，代码拼接 |
| `case_api_sequences` | story 内 | **用 `case_id` 做 key**（TC-001），精确匹配，不依赖 title 字符串 |
| `decision_map` | story 内 | **Thinking 输出的决策键值对**：每条用例每步骤的字段填什么 |
| `internal_dependency` | story 内 | **用 `case_id` 做 key**：变量产出者、提取路径、被谁消费 |
| `cross_module_dependency` | story 内 | 跨模块依赖（前置条件依赖的外部模块接口） |
| `teardown_api_sequence` | story 内 | 该 story 的清理接口序列。LLM 判断无需清理时为空数组 `[]` |

**Key 匹配策略**：全链路统一使用 `case_id`（TC-001）作为 key。`case_id` 是 Phase B LLM 显式生成的编号，Excel 中有专属列，确定性远高于 title。Phase C 代码直接按 `case_id` 索引，无需模糊匹配。

**Decision Map 的赋值指令**：

| 数据类型 | 来源 | Thinking 输出 | Json_Mode 动作 | 框架运行时 |
|---------|------|-------------|---------------|-----------|
| 静态常量 | 用例步骤写死 | `"pageSize": 10` | 照抄到 YAML | 原值使用 |
| 依赖变量/随机值 | internal_dependency 或需动态生成 | `"order_id": "${get_extract_data(order_id)}"` `"plate": "${random_plates(1)}"` | 原样复制 `${...}` 到 YAML | `replace_load()` 解析执行 |
| 资源文件 | 图片/Word/PDF 等（后期补充到 `methods.yaml`） | `"avatar": "${gen_image(avatar, 200x200)}"` | 原样复制 `${...}` 到 YAML | `replace_load()` 调用工厂生成文件 |

**Json_Mode 的输入边界**：只接收 `params` + `assertions`，不接收 `extract`。
**extract 由代码层生成**：Phase C 代码从 `internal_dependency` 中读取 `extract_path`，直接构造 YAML 的 `extract` / `input_extract` 块，无需 Json_Mode 参与。这保证了 Json_Mode 是纯粹的"填表工"——它只看到"填什么值"，不需要知道"值从哪来"。

**assertions 格式**：decision_map 中的 assertions 直接使用 YAML 原生结构（`{"eq": {...}}`），与 `YAML_SPECIFICATION.md` 第 7 节完全对齐。Json_Mode 照抄到 YAML 的 `validation` 数组，不做格式翻译。

> **关键**：Thinking 能确定的直接写值，不能确定的输出 `${}` 字符串。Json_Mode 不区分类型——都是字符串或普通值，照抄即可。运行时由 `base/apiutil.py replace_load()` 统一解析。

**Token 节省效果**：Json_Mode 每条只需 ~100 tokens 的决策条目，而非 ~3000 tokens 的自由文本分析。

---

## 三、Phase C 下游改造

### 3.1 代码预处理（新增分流步骤）

在 `_generate_all_yamls` 之前增加以下纯代码步骤（全部在 `_confirm_plan_bg` 内）：

```
Step 0: [新增] 生成 dependency_map.json
        ├─ 用 confirmed_module 从 ChromaDB 检索 product_docs
        ├─ 读取 Excel + api_defs.json
        ├─ 调用 LLM（thinking=on）→ json.loads() → Pydantic 校验
        └─ 校验通过 → 写入 output_dir/dependency_map.json
            校验失败 → 修复重试（DEPENDENCY_REPAIR_ATTEMPTS），超限 → 报错退出

Step 1: 加载 + 预校验 dependency_map.json
        ├─ 文件存在性、JSON 可解析、stories 非空
        ├─ 提取所有 story（保持 LLM 原始顺序）
        └─ 按 story 分组 Excel 用例（通过 case_id 精确匹配）

Step 2: 按 story 过滤接口定义
        ├─ URL 路径参数归一化 + 索引查找
        └─ 去重后至少 1 个匹配 → 0 个则报错退出

Step 3: 拼接完整 api_sequence
        └─ 每条用例: full_sequence = story_pre_api_sequence + case_api_sequences["TC-xxx"]
```

#### 3.1.1 URL 归一化算法详解

**目的**：`api_sequence` 中 LLM 写的 URL 参数名（如 `{order_id}`）可能与 `api_defs.json` 原始定义（如 `{id}`）不同，归一化后统一匹配。

**核心函数**：

```python
import re
from collections import defaultdict

_PARAM_RE = re.compile(r'\{[^}]+\}')

def normalize_url(url: str) -> str:
    """将 URL 中所有 {xxx} 替换为 {param}。
    
    /order/query/{order_id}  →  /order/query/{param}
    /user/{userId}/profile   →  /user/{param}/profile
    /order/create            →  /order/create  (不变)
    """
    url = url.strip().rstrip("/")
    if not url.startswith("/"):
        url = "/" + url
    return _PARAM_RE.sub("{param}", url)


def build_api_index(api_defs: list[dict]) -> dict[tuple, list[dict]]:
    """构建 api_defs 查找索引。一个 key 可对应多个 API 定义（处理碰撞）。
    
    Returns:
        {(method_upper, normalized_url): [api_def_dict, ...]}
    """
    index = defaultdict(list)
    for api in api_defs:
        key = (api["method"].strip().upper(), normalize_url(api["url"]))
        index[key].append(api)
    return index


def filter_apis_by_urls(
    api_index: dict,
    url_set: set[tuple[str, str]],  # {(method, url)} from api_sequences
) -> list[dict]:
    """用 URL 集合过滤接口定义，结果去重。"""
    seen, result = set(), []
    for method, url in url_set:
        key = (method.upper(), normalize_url(url))
        for api in api_index.get(key, []):
            uid = (api.get("name"), api.get("url"))
            if uid not in seen:
                seen.add(uid)
                result.append(api)
    return result
```

**碰撞处理**：当两个不同接口归一化后相同（如 `GET /api/{user_id}/profile` 和 `GET /api/{org_id}/profile` 都变为 `GET /api/{param}/profile`），索引的 value 是 list，**两个都会被包含**在过滤结果中。

```
碰撞示例:
  api_defs 中有:
    A: GET /api/{user_id}/profile  →  key ("GET", "/api/{param}/profile")
    B: GET /api/{org_id}/profile   →  key ("GET", "/api/{param}/profile")
    C: POST /api/order/create      →  key ("POST", "/api/order/create")

  索引:
    ("GET", "/api/{param}/profile")  → [A, B]   ← 碰撞，value 是 list
    ("POST", "/api/order/create")    → [C]

  api_sequence 中有 "查询用户档案:GET /api/{user_id}/profile"
    → 归一化 key = ("GET", "/api/{param}/profile")
    → 命中 [A, B]  ← 两个都包含
```

**为什么碰撞不影响结果**：

| 场景 | 影响 | 评估 |
|------|------|------|
| 同一 story 需要两个接口 | 都命中，都包含 | ✅ 正确，一个不漏 |
| 同一 story 只需要一个 | 多引入 1 个定义 (~200 tokens) | ✅ 过滤后 7/30 接口，仍节省 77% token |
| 碰撞发生在不同 story | 各自多引入 1 个定义 | ✅ 互不影响 |

**策略本质**："宁可多匹配，不可漏匹配"。多匹配代价 ~200 tokens/碰撞，漏匹配代价是 LLM 不知道接口参数格式 → 生成错误 YAML。碰撞概率极低（同一模块下相同方法+相同路径段数+每段参数化在实际 API 设计中几乎不存在）。

```

### 3.2 生成粒度：Thinking 决策 + Json_Mode 填表（两层 LLM 调用）

**核心原则**：Thinking 能确定的直接写值，不能确定的输出 `${}` 引用字符串交给框架运行时解析。Json_Mode 只做"填表"（原样复制指令字符串），不做"判断"。中间通过 **Decision Map JSON**（Thinking 的输出产物）衔接。

**执行模式**：Prefetch（预加载）流水线。用一个 `Queue(maxsize=1)` 实现生产者-消费者模式，thinking 提前预取下一个 story，但不允许同时跑 2 个 thinking。

```
时间线:

story-1: [thinking(60s)] → [json_mode × 5(30s)] ──────┐
story-2:                   [thinking(45s)] → [json_mode × 3(25s)] ──┐
story-3:                                         [thinking(80s)] → [json_mode × 7(40s)]

总耗时 = thinking[0] + max(json[0], think[1]) + max(json[1], think[2]) + json[2]
       = 60 + max(30, 45) + max(25, 80) + 40 = 225s
```

**Queue(maxsize=1) 如何天然实现"前置依赖锁"**：maxsize=1 意味着队列最多放 1 个 item。thinking 完成 → put 入队（队列满）→ producer 开始下一个 thinking。如果 json_mode 还在跑 → 队列满 → 下一个 thinking 完成后尝试 put → BLOCK，直到 consumer 取走。任何时候 LLM 调用并发 ≤ 1 thinking + YAML_CONCURRENCY json_mode。

**_generate_all_yamls 的实现伪代码**（`generators.py`）：

```python
from queue import Queue
import threading

def _generate_all_yamls_v2(self, excel_path, api_defs_json, user_ctx):
    dep_map = self._load_dependency_map(excel_path)
    stories = dep_map["stories"]
    api_index = build_api_index(json.loads(api_defs_json))
    output_base = os.path.dirname(excel_path)
    
    ready_queue = Queue(maxsize=1)  # prefetch 1 story ahead
    all_results = []
    
    def thinking_producer():
        """线程：串行产出 thinking 结果，给 consumer 消费。"""
        for story in stories:
            urls = collect_urls(story)
            filtered = filter_apis_by_urls(api_index, urls)
            refined = self._thinking_per_story(
                story=story,
                filtered_apis_json=json.dumps(filtered),
                factory_methods=self._load_factory_methods(),
            )
            ready_queue.put((story, filtered, refined))  # 队列满时阻塞
        ready_queue.put(None)  # 哨兵：全部 thinking 完成
    
    producer = threading.Thread(target=thinking_producer, daemon=True)
    producer.start()
    
    while True:
        item = ready_queue.get()
        if item is None:
            break
        story, filtered_apis, refined = item
        
        story_tasks = build_story_tasks(story, refined, output_base)
        result = self._run_yaml_rounds(          # 复用现有修复轮逻辑
            story_tasks,
            json.dumps(filtered_apis),
            user_ctx, output_base,
        )
        all_results.append(result)
        # 追加写入 _generation_errors.json（每个 story 独立，不覆盖）
        if result.get("failed"):
            append_errors(output_base, story["story_name"], result)
    
    producer.join()
    return merge_results(all_results)
```

**_run_yaml_rounds 保持不变**：每个 story 独立调用一次，修复轮逻辑完全复用——`round_no`, `failures`, `registry` 均不变。唯一变化：`_generation_errors.json` 改为追加模式（每 story 一个 section），避免后一个 story 覆盖前一个。

**并发安全性**：

| 时刻 | 活跃 LLM 调用 | 说明 |
|------|:---:|------|
| thinking 期间 | 1 (thinking) | producer 线程独占 LLM |
| thinking + json_mode 重叠 | 1 (thinking) + N (json_mode) | thinking 单线程 + json_mode 线程池，不冲突 |
| json_mode 期间 (thinking 已完成) | N (json_mode) | consumer 阻塞等待下一个 prefetch item |

**对现有并发配置的影响**：
- `YAML_CONCURRENCY`：**不变**，仍控制 json_mode 的并发数
- `TASK_MAX_WORKERS`：**不变**，`_run_yaml_rounds` 内部 ThreadPoolExecutor 隔离
- `_run_yaml_rounds` 内部：**零改动**
- `_generate_all_yamls` 入口签名：**不变**，调用方（`web/tasks.py`）无感知

Thinking 按三类情况决定输出：

| 情况 | Thinking 输出 | 示例 |
|------|-------------|------|
| 静态常量（用例写死不变） | 直接写值 | `"pageSize": 10` |
| 运行时依赖/随机值 | `${}` 引用字符串 | `"order_id": "${get_extract_data(order_id)}"` |
| 资源文件（图片/Word/PDF等） | `${}` 工厂方法 | `"avatar": "${gen_image(avatar, 200x200)}"`（后期补充到 `methods.yaml`） |

```
一个 Story 的 YAML 生成:

  ┌─ Phase C Thinking 节点（LLM 调用 × 1 / story）────────────┐
  │ 输入: ① story_name  ② 去重后的接口定义（仅该 story）     │
  │       ③ 该 story 全部用例（含完整 api_sequence）          │
  │       ④ 工厂方法字典（methods.yaml）                      │
  │       ⑤ dep_map 中的 internal_dependency                  │
  │       ⑥ dep_map 中的 decision_map（dep_map 生成 产出）       │
  │       ⑦ dep_map 中的 cross_module_dependency              │
  │                                                          │
  │ 角色: 消费 dep_map 生成 的 decision_map，结合工厂字典和      │
  │       去重 API 定义，精炼为最终赋值方案。                  │
  │       dep_map 生成 决定"哪个接口、哪些字段、谁依赖谁"；       │
  │       Phase C Thinking 决定"这个字段用哪个工厂方法、       │
  │       那个字段从哪个 extract 变量取、断言具体校验什么"。   │
  │                                                          │
  │ 输出 → refined_decision_map（精炼后的赋值指令，内存传递）  │
  │       每条用例每步骤的最终赋值方案：                        │
  │         • 静态常量 → 直接写值      "sku": "SKU-001"       │
  │         • 工厂方法 → ${} 字符串    "plate": "${random_...}│
  │         • 依赖变量 → ${} 占位符    "order_id": "${get_...}│
  └──────────────────────────────────────────────────────────┘
                           │
                refined_decision_map
                （内存传递，不落盘）
                           │
                           ▼
  ┌─ Json_Mode 节点（LLM 调用 × N，每条 case 一次）───────────┐
  │ 输入: ① 当前 case_id 的 refined_decision_map（~100 tokens）│
  │       ② 该 case 的 steps / expected / api_sequence       │
  │       ③ 去重后的接口定义                                  │
  │                                                          │
  │ 动作: 遍历字段，从 refined_decision_map 取指令 → 填入YAML │
  │       不做判断: ${random_plates(1)} 和 ${get_extract_     │
  │       data(order_id)} 都是普通字符串，照抄即可             │
  │                                                          │
  │ 运行时: 测试框架 replace_load() 解析 ${} → 动态执行       │
  └──────────────────────────────────────────────────────────┘
```

**多步骤用例的 Json_Mode 输出**：`decision_map` 中 `steps` 数组的每个元素对应 YAML 的一个 `- baseInfo/testCase` 块。如果某用例有 2 个步骤（如"创建 + 查询"），**单条 Json_Mode 调用负责生成该用例的全量 YAML**（`data: [step1, step2]`），而不是按步骤拆分调用。

| 场景 | steps 数组长度 | Json_Mode 调用次数 | 输出 YAML |
|------|:---:|:---:|------|
| 单步骤用例 | 1 | 1 次 | `data: [step1]` |
| 多步骤用例 | 2+ | **1 次** | `data: [step1, step2, ...]` |

原因：单次调用生成完整 YAML 避免了多次调用拼接带来的格式断裂风险，同时保持 YAML 文件的内聚性。

### 3.3 LLM 输入变化对比

| 项目 | 改前 | 改后 |
|------|------|------|
| 接口定义 | 全量 `api_defs.json`（30+ 接口） | 仅该 story 涉及的接口（~6 个） |
| Thinking 输入 | 单条用例 steps+expected | 全 story 用例 + dependency_map |
| Thinking 输出 | 自由文本分析（~1500 tokens） | Decision Map JSON（~100 tokens/case） |
| Thinking 次数 | N 条用例 = N 次 | 1 个 story = 1 次 |
| Json_Mode 输入 | 单条 thinking 全文 + 全量 API | decision_map[case_id] + 去重 API |
| Json_Mode 职责 | 分析 + 填写 | **纯填表**（不做任何判断） |
| Json_Mode 次数 | N 次 | N 次（不变） |
| 前置步骤来源 | 硬编码字符串 | `story_pre_api_sequence` 拼接 |

### 3.4 Setup/Teardown 改善

新方案**不改变 setup/teardown YAML 的生成方式**（所有 YAML 仍走 LLM json_mode）。变化在于输入侧：

- **setup 有明确的 API 序列**：`story_pre_api_sequence` 列出了前置 API（如 `POST /login`），不再依赖硬编码字符串
- **teardown 有数据流感知**：`teardown_api_sequence` 由 LLM 按数据流判断，而非固定的"调用删除接口"
- **前置步骤拼接**：每条用例的 YAML 生成时，Phase C 代码将其 `story_pre_api_sequence` 拼接到该用例的 `case_api_sequences` 前面，作为该用例的完整 URL 步骤序列。Phase C Thinking 阶段一次性看到完整上下文

有了 `story_pre_api_sequence` + `cross_module_dependency`：

- **setup**：`story_pre_api_sequence` 明确列出前置 API（如 `POST /login` + `GET /get_test_user`）。`cross_module_dependency` 说明依赖哪个外部模块、需要什么变量。Phase C thinking 阶段一次性分析整个 story 的前置依赖，json_mode 阶段直接填写
- **teardown**：基于 `teardown_api_sequence`。LLM 按数据流判断：下游 case 需消费的数据不清理，有合法清理路径时才填写清理步骤。空数组 `[]` 是合法值（表示 LLM 判断无需清理）

### 3.5 校验粒度

保持不变：每条 YAML 整体过 Pydantic `TestData` 校验（`data: list[TestStep]` 逐字段检查）。修复轮逻辑不变，失败项携带错误上下文自查 → 重新 json_mode 输出 → 再校验。

### 3.6 全部用例是否仍需传给 LLM

讨论结论：**不需要**。`dependency_map.json` 已完整描述模块关系结构。LLM 拿到的是该 story 内的全部用例（非全量全部模块的用例），配合 `internal_dependency` + `cross_module_dependency` 足以理解跨用例数据流。

---

## 四、校验与回滚

### 4.1 Phase B 产出校验

#### 4.1.1 `dependency_map.json` 校验

dep_map 生成 节点输出 JSON 后，纯代码校验：

```
dependency_map.json 校验规则:
  ├─ JSON 可解析（非法 JSON → 不落盘，直接进入修复轮）
  ├─ 顶层字段：stories 数组非空
  ├─ 每个 story 对象内 7 个字段齐全：story_name, story_pre_api_sequence,
  │   case_api_sequences, decision_map, internal_dependency,
  │   cross_module_dependency, teardown_api_sequence
  ├─ story_pre_api_sequence：格式同 api_sequence，URL 存在性检查
  ├─ case_api_sequences / internal_dependency / decision_map：
  │   ├─ case_api_sequences 中每个 case_id 的值必须为非空数组（接口自动化必须知道调哪个 API）
  │   ├─ 所有 key 必须使用 case_id（TC-xxx），且在 Excel 中实际存在
  │   ├─ 三个 map 的 key 集合必须一致（无遗漏、无多余 case）
  │   └─ internal_dependency 中 used_by 引用的 case_id 必须存在
  ├─ decision_map：每步的 api 字段 URL 必须在 api_definitions 中存在
  ├─ cross_module_dependency："获取接口"URL 必须在 api_definitions 中存在
  └─ teardown_api_sequence：字段必须存在（空数组 [] 合法），非空时 URL 格式校验
       └─ 字段缺失 → 进入修复重试；为空 → 合法（LLM 判断无需清理）
```

**teardown 的设计原则**：LLM 按数据流判断——
- 下游 case 需消费本 case 产物 → 不清理，teardown_api_sequence 留空
- 有合法的清理路径（产品规则允许，如未被引用的实体可直接删除）→ 填写具体步骤
- 不存在合法清理路径（如被引用的实体无法删除）→ 留空，不设计无法成功的清理步骤

| 层级 | 位置 | 动作 |
|------|------|------|
| 1. Prompt 约束 | dep_map 生成 的 system prompt | 要求 LLM 输出 teardown，明确：下游需消费则不清理，有合法路径才填写 |
| 2. 代码校验 | dep_map 生成 输出后 | 字段缺失 → 修复重试；字段为空 `[]` → 合法 |
| 3. 回滚 | 修复重试耗尽后 | dependency_map.json 不落盘，工作流标记失败 |

校验失败 → dep_map 生成 修复重试（`DEPENDENCY_REPAIR_ATTEMPTS`，默认 2 次），修复 prompt 注入错误明细。超过重试 → 原子回滚（见 4.1.2），不产出半成品。

#### 4.1.2 控制流与原子性

dep_map 的生成和消费都在同一个后台任务 `_confirm_plan_bg` 中，天然形成原子性保障：

```
_confirm_plan_bg:
  Step 0: 生成 dependency_map.json → .tmp → os.replace 落盘
      失败 → 报错退出，不继续
  Step 1: 加载 + 预校验 dep_map
      失败 → 报错退出，不继续
  Step 2: 生成 .py 文件
  Step 3: Prefetch 流水线生成 YAML（消费 dep_map）
```

如果 dep_map 生成失败，后续步骤不会执行——用户看到明确的错误信息而非半成品。如果 Excel 重新生成后用户再次确认，dep_map 会被重新生成（覆盖旧文件），不会残留过期数据。

### 4.2 Phase C 输入预校验

Phase C 启动时（`_confirm_plan_bg` 中、`.py` 和 YAML 生成之前），纯代码校验（不放 LLM）。结构化完整性（字段齐全、teardown 字段存在等）已在 Phase B 侧保证，Phase C 只校验数据可用性。

```
Phase C 启动校验（在 web/tasks.py _confirm_plan_bg 中，api_defs 解析后插入）:
  ├─ dependency_map.json 存在性 → 不存在则直接报错退出（不降级，拒绝执行）
  ├─ JSON 解析 → 失败报错，给出具体行号和错误原因
  ├─ stories 数组非空 → 空则报错退出
  ├─ 遍历所有 story 的 story_pre_api_sequence + case_api_sequences
  │   → URL 并集逐一在 api_defs.json 中查找（URL 归一化匹配，算法见 §2.3 讨论）
  │   → 有找不到的：WARNING 日志 + 跳过该 URL（不注入 prompt）
  └─ 去重后至少 1 个 URL 匹配 → 0 个则报错退出
```

**对 `web/tasks.py` 的影响**：在现有 `_confirm_plan_bg` 中（约第 334 行），`api_defs_json = resolved_defs` 之后、`_generate_py_file` 调用之前，插入上述校验块。校验通过后，`dep_map` 对象传入 `_generate_py_file` 和 `_generate_all_yamls`。

### 4.3 thinking 节点超时

两个节点使用 thinking 模式：dep_map 生成（`generate_dependency_map`）和 Phase C（`_thinking_per_story`）。均沿用 LangChain 默认超时。

```
thinking 超时策略（适用两个节点）:
  ├─ 沿用 LangChain 默认超时（底层 openai 客户端默认约 600s），不做额外限制
  │   └─ 原因：与 Phase B analyze_test_points_raw 同为 thinking 节点，同等量级输入
  │      现有流程中无显式超时配置，thinking 节点未出现超时问题
  ├─ dep_map 生成 输入: 全量 Excel + 全量 api_defs + 产品文档 → 一次性分析，超时风险低
  ├─ Phase C 输入: 单 story 用例 + 去重 api_defs + 工厂字典 → 比全量小，超时风险更低
  ├─ 如需显式控制：在 settings.py 新增 thinking_timeout（可选，默认 None=沿用客户端默认）
  ├─ dep_map 生成 超时 → 整个 dependency_map.json 生成失败，工作流终止
  ├─ Phase C 超时 → 该 story 整体标记失败，所有用例写入 _generation_errors.json
  │         （标注 "THINKING_TIMEOUT"），继续处理其他 story
  └─ 不做 story 用例数上限约束（实际业务中一个 story 可能包含大量用例）
```

### 4.4 json_mode 修复轮

与现有逻辑一致。修复轮输入追加 `dependency_map` 上下文，LLM 自查时可参考完整数据流。修复轮超时沿用现有 LLM 调用超时。

### 4.5 回滚策略汇总

| 场景 | 回滚动作 |
|------|---------|
| Excel 校验失败 | 修复重试（EXCEL_REPAIR_ATTEMPTS），超限标记人工审查 |
| dependency_map 校验失败 | 修复重试（DEPENDENCY_REPAIR_ATTEMPTS），超限→不落盘，工作流标记失败 |
| Phase C thinking 超时 | story 整体标记失败，写 errors.json，继续处理其他 story |
| Phase C json_mode 逐条失败 | 现有修复轮逻辑不变 |
| dependency_map.json 缺失 | Phase C 直接报错退出，不降级 |
| api_sequence 中 URL 在 api_defs 找不到 | 单个 URL WARNING 跳过，去重后 0 个匹配则报错退出 |
| case_api_sequences 为空数组的用例 | 校验拦截（接口自动化框架必须知道调哪个 API），修正 prompt |

### 4.6 后置变量读写审计（纯代码）

全部 YAML 生成完后，做一次跨文件的变量读写一致性扫描（不放 LLM）：

```
扫描全部 YAML 文件:
  ├─ 收集所有 extract / input_extract 的 key → "写入集"
  ├─ 收集所有 ${get_extract_data(xxx)} 中的 xxx → "读取集"
  ├─ 写入但从未读取 → WARNING（LLM 可能幻觉了不需要的 extract）
  └─ 读取但从未写入 → WARNING（可能缺少 extract 步骤）
```

警告**直接展示到前端聊天界面**（`result.warnings` 字段），同时写入 `_generation_errors.json` 和 `thinking_trace.log`。不阻塞生成，仅作安全网。

---

## 五、实施步骤

### Phase 1：数据模型 + Prompt + 工具函数

1. `response_model.py`：新增 `DependencyMap` / `StoryDependencyMap` / `DecisionStep` / `InternalDependency` / `CrossModuleDep` Pydantic 模型
2. `extraction_prompts.py`：新增 `generate_dependency_map_prompt()` + `_MsgBuilder`
3. `prompts/definitions.py`：`PromptFactory.generate_dependency_map()` 委托方法
4. `settings.py` + `config.py`：新增 `DEPENDENCY_REPAIR_ATTEMPTS` / `THINKING_TIMEOUT`
5. `agent_components/generators.py`：新增 `normalize_url()` / `build_api_index()` / `filter_apis_by_urls()` / `_collect_story_urls()` 模块级函数

### Phase 2：Phase C 下游重构

6. `web/tasks.py`：`_confirm_plan_bg` 改造为:
   - Step 0: 生成 dependency_map.json（LLM thinking → JSON 解析 → Pydantic 校验 → 修复轮 → 原子写入）
   - Step 1: 加载 dep_map + 预校验（存在性、JSON 解析、stories 非空）
   - Step 2: 生成 .py 文件（不变）
   - Step 3: Prefetch 流水线生成 YAML
7. `agent_components/generators.py`：
   - 新增 `_generate_dependency_map(excel_path, output_dir, ...)` — 移植原 dep_map 生成 逻辑
   - 新增 `_load_dependency_map()` 读取 JSON
   - 新增 `_thinking_per_story()` Phase C 单 story 的 thinking 调用
   - 改造 `_generate_all_yamls()`：Prefetch 流水线模式（`Queue(1)` + producer 线程）
   - `_generate_one_yaml()` 增加 `decision_context` 参数
8. `agent_components/graph_builder.py`：**删除** `generate_dependency_map` 节点，恢复 `generate_excel_plan → END`
9. `agent_components/state.py`：**删除** `dependency_map` / `dependency_map_path` 字段（不再跨 Phase 传递）

### Phase 3：验证

8. 端到端测试：上传文档 → Phase B 生成 Excel + dependency_map.json → Phase C 按模块生成 YAML
9. Token 用量对比（改前 vs 改后）
10. setup/teardown 生成质量验证

---

## 六、设计决策记录

| 决策点 | 结论 | 理由 |
|--------|------|------|
| api_sequence 存放位置 | **仅存 `dependency_map.json`**，不存 Excel | 避免 Excel 列膨胀；Phase C 从 dep_map 直接读取 |
| api_sequence 格式 | `步骤名:HTTP方法 URL`，代码 `split(":", 1)` 解析 | 步骤名来自 Excel title/steps，无需额外标签系统 |
| 前置 api_sequence 存放 | `dependency_map.json` 的 `story_pre_api_sequence`，LLM 输出一次 | 避免每条用例重复相同前置 URL；代码拼接完整序列 |
| 依赖映射表粒度 | **一个 feature 一个 `dependency_map.json`**（含多个 story） | 减少文件碎片；Phase C 一次加载全量 |
| 依赖映射表存放 | 与 `test_plan.xlsx` 同级（`output_dir/dependency_map.json`） | 与 api_defs.json 同目录，Phase C 加载路径统一 |
| dep_map 节点放置 | **放在 Phase C 入口**（`_confirm_plan_bg` Step 0），不作为 Phase B LangGraph 节点 | ① JSON 无法人工审核，放 Phase B 无意义；② 避免 Excel 确认前后的时间差导致数据过期；③ 同一后台任务内原子生成→消费，失败一起报错 |
| Phase B 节点 | 依赖映射表生成（`generate_dependency_map`），**thinking 模式** | thinking 推理质量远高于 json_mode；对复杂依赖图的准确性至关重要 |
| 两层 Thinking 分工 | dep_map 生成（Phase C Step 0）：结构性决策 + 初步 `${}` 赋值（有 methods.yaml 注入）；Phase C Thinking：补充校验 + 去重 API 精炼（内存传递） | dep_map 生成已有工厂字典，输出基本可用的 ${} 引用；Phase C Thinking 仅需校验补充 |
| Phase C thinking 调用粒度 | 1 个 story = 1 次 thinking | 共享上下文；决定好所有填写内容；从旧的 N 次/用例 降到 1 次/story |
| Phase C thinking 输出 | `refined_decision_map`（内存传递，不落盘） | 仅 Json_Mode 消费；避免中间文件碎片 |
| json_mode 调用粒度 | 每条 title = 1 次 json_mode | 保持输出可控；只做填写不做分析 |
| story 间 thinking 执行 | **Prefetch 流水线**：`Queue(maxsize=1)` 生产者-消费者，thinking 提前预取，LLM 调用 ≤ 1 thinking + N json_mode | 比严格串行快 ~20%，比全并发安全；Queue 天然实现前置依赖锁 |
| Phase C 接口过滤 | 代码按 URL 过滤 + **路径参数归一化**（`{xxx}→{param}`） | 参数名写法差异不影响匹配；碰撞时多引入 1-2 个定义不影响效果 |
| extract 生成 | **代码层**从 `internal_dependency` 读取 `extract_path` 生成 | Json_Mode 不接触 extract，保持纯填表 |
| 全部用例传给 LLM | 不需要（仅传该 story 用例） | `dependency_map.json` 已覆盖跨模块关系 |
| setup 生成 | `story_pre_api_sequence` + `cross_module_dependency` | 替代硬编码字符串；thinking 一次性分析 |
| teardown 策略 | LLM 按数据流判断：下游需消费不清理，有合法路径才填写；空 `[]` 合法 | 不强制清空；尊重业务规则（如被引用实体不可删除） |
| 校验粒度 | 不变：一条 YAML 整体过 Pydantic `TestData` | 修复轮逻辑复用 |
| Phase C 降级 | **不降级**：dependency_map.json 缺失直接报错退出 | 半成品数据会导致静默错误 |
| thinking 超时 | 沿用 LangChain 默认超时（~600s）；可选 `story_thinking_timeout` | 现有流程中 thinking 无超时问题；与 Phase B 一致 |
| story 用例数上限 | **不做限制** | 实际业务中一个 story 可能包含大量用例，不应人为约束 |
| Phase B 原子写入 | Excel 先落盘（Node 6），dep_map 后生成（Node 7），dep_map 缺失时 Phase C 拒绝执行 | 天然形成原子性保障 |
| Phase C 输入预校验 | 代码层：JSON 解析 + URL 存在性 + 去重后至少 1 个匹配 | 早发现早报错，不浪费 LLM 调用 |
| Key 匹配策略 | **全链路统一 `case_id`（TC-001）**，不用 title | TC-xxx 是 LLM 显式生成的编号，确定性强，不会断裂 |
| Thinking 输出格式 | **Decision Map JSON**（赋值指令），非自由文本 | Json_Mode 只需 ~100 tokens/条；避免上下文自我膨胀 |
| Json_Mode 职责 | **纯填表工**：只填 `params` + `assertions`，不接触 `extract` | extract 由代码从 `internal_dependency` 生成；assertions 已为 YAML 原生格式 |
| assertions 格式 | decision_map 中直接使用 YAML 原生结构 `{"eq": {...}}` | Json_Mode 照抄到 validation 数组；格式翻译由 Phase B Thinking 完成 |
| URL 匹配 | `{xxx} → {param}` 路径参数归一化后再匹配 | 参数名写法差异不影响匹配 |
| 变量读写审计 | 纯代码后置扫描，WARNING 展示到前端 | LLM 可能幻觉不必要的 extract；安全网不阻塞生成 |
| 向后兼容 | **不考虑**。旧 Excel（无 dep_map）Phase C 直接报错 | 新流程依赖 dep_map 作为核心输入 |
