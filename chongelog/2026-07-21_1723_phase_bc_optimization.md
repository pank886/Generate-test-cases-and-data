# Phase B/C 联动优化：模块级接口去重 + 依赖映射表 + 下游分流

| 项目 | 内容 |
|:---|:---|
| 讨论日期 | 2026-07-21 |
| 变更类型 | Phase B 新增节点 + Excel 扩列 + Phase C 下游重构 |
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

### 2.1 Excel Sheet1 新增列：`api_sequence`

**格式**：用例主体步骤的 URL 序列（前置步骤不重复写入），List 结构的字符串。

```
["创建订单:POST /order/create", "查询订单:GET /order/query/{order_id}"]
```

**前置条件的 api_sequence 单独存放**：同 story 下所有用例共享相同的前置步骤，由 LLM 在 `dependency_map.json` 中输出一次 `story_pre_api_sequence`，Phase C 代码负责拼接：

```
完整序列 = story_pre_api_sequence + case.api_sequence

示例:
  story_pre_api_sequence: ["前置鉴权:POST /login", "前置用户:GET /get_test_user"]
  TC-001 api_sequence:   ["创建订单:POST /order/create"]
  → 拼接结果: ["前置鉴权:POST /login", "前置用户:GET /get_test_user", "创建订单:POST /order/create"]
```

**优于纯 LLM 方案**：
- LLM 不需要在每条用例中重复输出相同的前置 URL 序列
- Excel 列宽不膨胀
- 前置步骤变更时只改一处（dependency_map.json）

**提取方式**：Phase B LLM 在生成 Excel 计划时已经知道哪条用例属于哪个 story，在 `generate_dependency_map` 节点中，LLM 根据 story 的共享前置和接口定义，一次性输出 `story_pre_api_sequence` 和各用例的 `api_sequence`。

#### 2.1.1 `api_sequence` 重复性权衡

`api_sequence` 中的步骤名（如"创建订单"）与 `steps` 列的自然语言描述存在语义重复。这是**有意的设计取舍**：

| 维度 | steps 列 | api_sequence 列 |
|---|---|---|
| 内容 | 自然语言：`"调用新增订单接口，传入商品ID=SKU-001"` | 结构化：`"创建订单:POST /order/create"` |
| 作用 | 人类可读的测试逻辑 | 下游 LLM 的结构化映射 |
| 消费者 | 人 | 代码 + LLM |

**代价**：Excel 多一列，每行多几十个字符。  
**收益**：下游 Phase C 的 LLM 不再需要从自然语言 steps 中做"这句话对应哪个 API"的语义匹配（容易出错且费 token），而是直接拿到确定性映射 `步骤名 → URL`。validator 也可以用 `api_sequence` 中的 URL 校验生成的 YAML 是否调对了接口。

**标签策略**：不引入额外的标签系统。直接用 Excel 中已有的用例 `title` 或 steps 首行动词作为步骤名，格式统一为 `步骤名:HTTP方法 URL`。Phase C 代码解析时用 `split(":", 1)` 拆分即可。

### 2.2 新增节点：Phase B-2 依赖映射表生成

在 `generate_excel_plan` 之后新增一个节点 `generate_dependency_map`，输入：

| 输入 | 来源 |
|------|------|
| Excel 计划 | Node 6 产出 |
| 接口定义 | `api_definitions`（state 中已缓存） |
| 产品文档 | `product_docs`（ChromaDB 检索结果） |
| Axure 分析 | `product_docs` 中 `type=axure` 的部分 |

#### 2.2.1 `generate_dependency_map_prompt()` 设计要点

Phase B-2 的 Prompt 除给出 Schema 定义外，必须明确三条铁律：

**① 强制输出 `teardown_api_sequence`**
```
对每个 story，识别所有写操作（POST/PUT/DELETE）对应的清理/回滚接口，
填入 teardown_api_sequence。如果确实无法确定清理接口，写 ["UNKNOWN"]。
"UNKNOWN" 会被代码校验拦截进入修复轮，禁止随意编造。
```

**② `decision_map` 中 `params` 的赋值原则**
```
- 用例步骤中明确写死的值 → 直接输出（如 "pageSize": 10）
- 需要动态生成的值 → 输出 ${} 字符串（如 "plate": "${random_plates(1)}"）
- 依赖前置步骤的值 → 输出 ${get_extract_data(xxx)} 占位符
- 禁止编造任何非确定性值（随机字符串、假手机号等），一律用 ${} 交给框架
```

**③ `internal_dependency` 中 `extract_path` 的来源**
```
extract_path 必须从【接口定义】的 returns 字段中提取，与响应 schema 严格对齐。
禁止凭空猜测 JSONPath。如果 returns 中找不到对应字段，不填 extract_path，
在 used_by 中标注依赖关系即可。
```

### 2.3 依赖映射表 JSON 结构（独立文件）

文件路径：与 `test_plan.xlsx` 同级（按 story 粒度，每个 story 的 `setup_data/` 同级目录），命名为 `dependency_map.json`

```json
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
```

**字段说明**：

| 字段 | 说明 |
|------|------|
| `story_name` | 当前 story 名，与 Excel 中 `@allure.story` 对应 |
| `story_pre_api_sequence` | 该 story 共享前置条件的 API 序列，LLM 输出一次，代码拼接 |
| `case_api_sequences` | **改用 `case_id` 做 key**（TC-001），精确匹配，不依赖 title 字符串 |
| `decision_map` | **Thinking 输出的决策键值对**：每条用例每步骤的字段填什么（工厂方法/依赖变量/静态值） |
| `internal_dependency` | **改用 `case_id` 做 key**：变量产出者、提取路径、被谁消费 |
| `cross_module_dependency` | 跨模块依赖（前置条件依赖的外部模块接口） |
| `teardown_api_sequence` | **必须字段**：该 story 的清理接口序列。无法确定时填 `"UNKNOWN"` |

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

在 `_generate_all_yamls` 之前增加一个纯代码步骤，不放 LLM：

```
输入: test_plan.xlsx + dependency_map.json + api_defs.json

Step 1: 解析 dependency_map.json
        ├─ 提取 story_name / story_pre_api_sequence / case_api_sequences / teardown_api_sequence
        ├─ 从 case_api_sequences 收集所有 URL → 去重
        └─ 按 story 分组 Excel 用例（通过 case_id 精确匹配）

Step 2: 按 story 过滤接口定义
        ├─ 用去重后的 URL 集合从 api_defs.json 中过滤
        │   ├─ URL 路径参数归一化: {order_id}/{id}/{orderId} → {param}
        │   └─ 匹配 key: method + normalize_path(url)
        └─ → 仅该 story 相关的接口定义

Step 3: 拼接完整 api_sequence
        └─ 每条用例: full_sequence = story_pre_api_sequence + case_api_sequences["TC-xxx"]
```

### 3.2 生成粒度：Thinking 决策 + Json_Mode 填表（两层 LLM 调用）

**核心原则**：Thinking 能确定的直接写值，不能确定的输出 `${}` 引用字符串交给框架运行时解析。Json_Mode 只做"填表"（原样复制指令字符串），不做"判断"。中间通过 **Decision Map JSON**（Thinking 的输出产物）衔接。

Thinking 按三类情况决定输出：

| 情况 | Thinking 输出 | 示例 |
|------|-------------|------|
| 静态常量（用例写死不变） | 直接写值 | `"pageSize": 10` |
| 运行时依赖/随机值 | `${}` 引用字符串 | `"order_id": "${get_extract_data(order_id)}"` |
| 资源文件（图片/Word/PDF等） | `${}` 工厂方法 | `"avatar": "${gen_image(avatar, 200x200)}"`（后期补充到 `methods.yaml`） |

```
一个 Story 的 YAML 生成:

  ┌─ Thinking 节点（LLM 调用 × 1）──────────────────────────┐
  │ 输入: ① story_name  ② 去重后的接口定义（仅该 story）     │
  │       ③ 该 story 全部用例（含完整 api_sequence）          │
  │       ④ 工厂方法字典  ⑤ internal + cross dependency      │
  │                                                          │
  │ 输出 → decision_map（JSON 数据，非自由文本）               │
  │       每条用例每步骤的赋值指令：                            │
  │         • 静态常量 → 直接写值      "sku": "SKU-001"       │
  │         • 工厂方法 → ${} 字符串    "plate": "${random_...}│
  │         • 依赖变量 → ${} 占位符    "order_id": "${get_...}│
  └──────────────────────────────────────────────────────────┘
                           │
                   decision_map JSON
                    （数据桥，非节点）
                           │
                           ▼
  ┌─ Json_Mode 节点（LLM 调用 × N，每条 title 一次）─────────┐
  │ 输入: ① 当前 case_id 的 decision_map 条目（~100 tokens）  │
  │       ② 该 case 的 steps / expected / api_sequence       │
  │       ③ 去重后的接口定义                                  │
  │                                                          │
  │ 动作: 遍历字段，从 decision_map 取指令 → 原样填入 YAML    │
  │       不做判断: ${random_plates(1)} 和 ${get_extract_     │
  │       data(order_id)} 都是普通字符串，照抄即可             │
  │                                                          │
  │ 运行时: 测试框架 replace_load() 解析 ${} → 动态执行       │
  └──────────────────────────────────────────────────────────┘
```

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

有了 `story_pre_api_sequence` + `cross_module_dependency`：

- **setup**：`story_pre_api_sequence` 明确列出前置 API（如 `POST /login` + `GET /get_test_user`）。`cross_module_dependency` 说明依赖哪个外部模块、需要什么变量。Phase C thinking 阶段一次性分析整个 story 的前置依赖，json_mode 阶段直接填写。
- **teardown**：基于 `internal_dependency` 中 `output_var` 为 null 的步骤（终结步骤）推断清理接口。若不够明确，可在 `dependency_map.json` 中增加可选的 `teardown_api_sequence`：

```json
{
  "teardown_api_sequence": ["取消订单:POST /order/cancel"]
}
```

### 3.5 校验粒度

保持不变：每条 YAML 整体过 Pydantic `TestData` 校验（`data: list[TestStep]` 逐字段检查）。修复轮逻辑不变，失败项携带错误上下文自查 → 重新 json_mode 输出 → 再校验。

### 3.6 全部用例是否仍需传给 LLM

讨论结论：**不需要**。`dependency_map.json` 已完整描述模块关系结构。LLM 拿到的是该 story 内的全部用例（非全量全部模块的用例），配合 `internal_dependency` + `cross_module_dependency` 足以理解跨用例数据流。

---

## 四、校验与回滚

### 4.1 Phase B 产出校验

#### 4.1.1 Excel `api_sequence` 列校验

在现有 `validate_excel_file()` 中增加：

```
api_sequence 列校验规则:
  ├─ 非空：每行必须有一个合法的 JSON 数组字符串
  ├─ 格式：每个元素匹配 ^[^:]+:(GET|POST|PUT|DELETE|PATCH) /\S+
  ├─ URL 存在性：每个 URL 必须在 api_definitions 中有对应接口
  └─ 与 steps 一致性：api_sequence 元素数 ≈ steps 换行数（允许 ±1 偏差）
```

校验失败 → 触发 Excel 修复重试（现有 `EXCEL_REPAIR_ATTEMPTS`），修复 prompt 中注入校验错误明细。

#### 4.1.2 `dependency_map.json` 校验

Phase B-2 节点输出 JSON 后，纯代码校验：

```
dependency_map.json 校验规则:
  ├─ JSON 可解析（非法 JSON → 不落盘，直接进入修复轮）
  ├─ 7 个顶层字段齐全：story_name, story_pre_api_sequence, case_api_sequences,
  │                      decision_map, internal_dependency,
  │                      cross_module_dependency, teardown_api_sequence
  ├─ story_pre_api_sequence：格式同 api_sequence，URL 存在性检查
  ├─ case_api_sequences / internal_dependency / decision_map：
  │   ├─ 所有 key 必须使用 case_id（TC-xxx），且在 Excel 中实际存在
  │   ├─ 三个 map 的 key 集合必须一致（无遗漏、无多余 case）
  │   └─ internal_dependency 中 used_by 引用的 case_id 必须存在
  ├─ decision_map：每步的 api 字段 URL 必须在 api_definitions 中存在
  ├─ cross_module_dependency："获取接口"URL 必须在 api_definitions 中存在
  └─ teardown_api_sequence：必须存在、非空、且不含 "UNKNOWN"
       └─ 不通过 → 进入修复重试（prompt 已要求显式输出，LLM 必须决策）
```

**teardown 的三层保障**（全在 Phase B 侧，不推到 Phase C）：

| 层级 | 位置 | 动作 |
|------|------|------|
| 1. Prompt 约束 | Phase B-2 的 system prompt | 显式要求 LLM 输出 `teardown_api_sequence`，无法确定时标注原因（触发第 2 层） |
| 2. 代码校验 | Phase B-2 输出后 | 缺失/空/含 `"UNKNOWN"` → 不通过，进入修复重试 |
| 3. 回滚 | 修复重试耗尽后 | dependency_map.json **不落盘**，Excel 临时文件删除，工作流标记失败 |

校验失败 → Phase B-2 修复重试（`DEPENDENCY_REPAIR_ATTEMPTS`，默认 2 次），修复 prompt 注入错误明细。超过重试 → 原子回滚（见 4.1.3），不产出半成品。

#### 4.1.3 Phase B 原子写入

Excel 和 dependency_map.json 必须同时有效才能落盘，避免半成品进入 Phase C：

```
Node 6: 生成 Excel → 写临时文件 .tmp
Node 7: 生成 dependency_map.json → 写临时文件 .tmp
原子提交:
  两者校验都通过 → os.replace(.tmp → 正式文件)
  任一失败 → 删临时文件，进入对应节点的修复轮
```

### 4.2 Phase C 输入预校验

Phase C 启动时，纯代码校验（不放 LLM）。注意：结构化完整性（字段齐全、teardown 非空等）已在 Phase B 侧保证，Phase C 只校验数据可用性。

```
Phase C 启动校验:
  ├─ dependency_map.json 存在性 → 不存在则直接报错退出（不降级，拒绝执行）
  ├─ JSON 解析 → 失败报错，给出具体行号和错误原因
  ├─ story_pre_api_sequence + 各用例 api_sequence 的 URL 并集
  │   → 逐一在 api_defs.json 中查找
  │   → 有找不到的：WARNING 日志 + 跳过该 URL（不注入 prompt）
  └─ 去重后至少 1 个 URL 匹配 → 0 个则报错退出
```

### 4.3 thinking 节点超时

改后 thinking 按 story 共享，输入包含该 story 全部用例（可能数十条），分析时间会显著长于单条。需要设定合理的 LLM 超时：

```
thinking 超时策略:
  ├─ 与 Phase B analyze_test_points_raw 一致（同为 thinking 节点，同等量级输入）
  ├─ settings.py 新增配置项: thinking_timeout（默认 300s）
  ├─ 超时 → 该 story 整体标记失败，所有用例写入 _generation_errors.json
  │         （标注 "THINKING_TIMEOUT"）
  └─ 不做 story 用例数上限约束（实际业务中一个 story 可能包含大量用例）
```

### 4.4 json_mode 修复轮

与现有逻辑一致。修复轮输入追加 `dependency_map` 上下文，LLM 自查时可参考完整数据流。修复轮超时沿用现有 LLM 调用超时。

### 4.5 回滚策略汇总

| 场景 | 回滚动作 |
|------|---------|
| Excel 校验失败 | 修复重试（EXCEL_REPAIR_ATTEMPTS），超限标记人工审查 |
| dependency_map 校验失败 | 修复重试（DEPENDENCY_REPAIR_ATTEMPTS），超限→Excel 不落盘 |
| Phase C thinking 超时 | story 整体标记失败，写 errors.json，继续处理其他 story |
| Phase C json_mode 逐条失败 | 现有修复轮逻辑不变 |
| dependency_map.json 缺失 | Phase C 直接报错退出，不降级 |

---

## 五、实施步骤

### Phase 1：Phase B 扩列 + 新节点

1. `response_model.py`：新增 `DependencyMap` / `ModuleApiMap` Pydantic 模型
2. `extraction_prompts.py`：新增 `generate_dependency_map_prompt()`
3. `nodes.py`：新增 `_generate_dependency_map_node()` 方法（thinking on → json_mode）
4. `graph_builder.py`：在 `generate_excel_plan` 后插入新节点 + 条件边
5. `state.py`：新增 `dependency_map` / `dependency_map_path` 状态字段

### Phase 2：Phase C 下游重构

6. `generators.py`：
   - 新增 `_load_dependency_map()` 读取 JSON
   - 新增 `_filter_apis_by_module()` 按 URL 过滤接口定义
   - 新增 `_build_module_context()` 组装模块级 prompt 上下文
   - 改造 `_generate_all_yamls()` 为按模块分流 + 去重 API
   - 改造 `_generate_one_yaml()` 为 `_generate_module_yamls()`（模块级批量生成）
7. `web/tasks.py`：适配新的 Phase C 入口参数

### Phase 3：验证

8. 端到端测试：上传文档 → Phase B 生成 Excel + dependency_map.json → Phase C 按模块生成 YAML
9. Token 用量对比（改前 vs 改后）
10. setup/teardown 生成质量验证

---

## 五、设计决策记录

| 决策点 | 结论 | 理由 |
|--------|------|------|
| api_sequence 格式 | `用例名称:HTTP方法 URL`，字符串存 Excel | 用例名称已存在，无需额外标签；代码 `split(":")` 解析 |
| 前置 api_sequence 存放 | `dependency_map.json` 的 `story_pre_api_sequence`，LLM 输出一次 | 避免每条用例重复相同前置 URL；代码拼接完整序列 |
| Excel 存储格式 | api_sequence 为 JSON 数组字符串 | openpyxl 写字符串单元格，Phase C `json.loads()` 解析 |
| 依赖映射表粒度 | 每个 story 一份 `dependency_map.json` | feature 下多 story 的 internal_dependency 独立，不交叉 |
| 依赖映射表存放 | 独立 JSON 文件（`setup_data/` 同级目录） | 嵌套结构不适合 Excel；代码 `json.load()` 直接解析 |
| Phase B 新节点 | 独立节点 Phase B-2（`generate_dependency_map`） | 不增加现有 prompt 复杂度；失败隔离；输入含产品文档 + Axure |
| thinking 调用粒度 | 1 个 story = 1 次 thinking | 共享上下文；决定好所有填写内容；从 N 次降到 1 次 |
| json_mode 调用粒度 | 每条 title = 1 次 json_mode | 保持输出可控；只做填写不做分析 |
| Phase C 接口过滤 | 代码按 URL 过滤 + **路径参数归一化**（`{xxx}→{param}`） | Phase B 输出的 URL 参数名可能与 api_defs.json 原始定义不同；归一化避免断裂 |
| extract 生成 | **代码层**从 `internal_dependency` 读取 `extract_path` 生成 | Json_Mode 不接触 extract，保持纯填表 |
| 全部用例传给 LLM | 不需要（仅传该 story 用例） | `dependency_map.json` 已覆盖跨模块关系 |
| setup 生成 | `story_pre_api_sequence` + `cross_module_dependency` | 替代硬编码字符串；thinking 一次性分析 |
| teardown 生成 | 可选 `teardown_api_sequence`，否则由 `output_var=null` 推断 | 保留扩展性 |
| 校验粒度 | 不变：一条 YAML 整体过 Pydantic `TestData` | 修复轮逻辑复用 |
| Phase C 降级 | **不降级**：dependency_map.json 缺失直接报错退出 | 半成品数据会导致静默错误 |
| thinking 超时 | settings.py 新增 `thinking_timeout`（默认 300s），与 Phase B 一致 | 同为 thinking 节点，同等量级输入 |
| story 用例数上限 | **不做限制** | 实际业务中一个 story 可能包含大量用例，不应人为约束 |
| Phase B 原子写入 | 临时文件 + os.replace | Excel 和 JSON 必须同时有效才落盘 |
| Phase C 输入预校验 | 代码层：JSON 解析 + URL 存在性 + 去重后至少 1 个匹配 | 早发现早报错，不浪费 LLM 调用 |
| **Key 匹配策略** | **全链路统一 `case_id`（TC-001）**，不用 title | TC-xxx 是 LLM 显式生成的编号，确定性强，不会断裂 |
| **Thinking 输出格式** | **Decision Map JSON**（赋值指令），非自由文本 | Json_Mode 只需 ~100 tokens/条；避免上下文自我膨胀 |
| **Json_Mode 职责** | **纯填表工**：只填 `params` + `assertions`，不接触 `extract` | extract 由代码从 `internal_dependency` 生成；assertions 已为 YAML 原生格式，无需 LLM 翻译 |
| **assertions 格式** | decision_map 中直接使用 YAML 原生结构 `{"eq": {...}}` | Json_Mode 照抄到 validation 数组；格式翻译由 Phase B Thinking 完成 |
| **URL 匹配** | `{xxx} → {param}` 路径参数归一化后再匹配 | Phase B 输出的参数名可能与 api_defs.json 原始定义不同 |
| **teardown 策略** | **Phase B 三层保障**（prompt + 代码校验 + 回滚），不推到 Phase C | Phase B 有人工审核节点，Phase C 报错会让用户困惑；上游拦截更清晰 |
