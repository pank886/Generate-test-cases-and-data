# API 提取/存储格式重构方案

> 日期：2026-08-13
> 状态：**✅ 已按此方案实施完成**（代码提取路径为本，LLM 提取路径同步兼容）
> 背景：当前提取出的每个接口是逐字段 verbose JSON（`{name,type,required,description,default}` 列表），
> 人类不可读、喂给 LLM 时 token 膨胀（250KB ≈ 67k tokens）且无法命中缓存。
> 目标：改为紧凑、人类可读、保留示例值与字段类型的新结构。
> 前提：历史数据由用户重新导入，本次只做功能修复，不做存量数据迁移。

## 0. 新存储格式（已与用户确认）

每个接口一条 JSON：

```json
{
  "name": "新增健身房设施",
  "method": "POST",
  "url": "/gymFacility/add",
  "description": "新增健身房设施",
  "header": {
    "Content-Type": "application/json"
  },
  "body": [
    { "name": "id",             "type": "string", "required": false, "default": "", "desc": "设施唯一标识", "value": "faci_001" },
    { "name": "code",           "type": "string", "required": false, "default": "", "desc": "",               "value": "GYM-2024-001" },
    { "name": "facName",        "type": "string", "required": false, "default": "", "desc": "",               "value": "智能跑步机" }
  ],
  "return": [
    { "name": "retCode", "type": "integer", "required": false, "default": "", "desc": "", "value": "0" }
  ]
}
```

**六字段语义（分开存，互不歧义）：**

| 字段 | 来源 | 用途 |
|------|------|------|
| `name` | 参数表「名称」列 | 键 |
| `type` | 参数表「类型」列 | 数据类型 |
| `required` | 参数表「是否必须」列；**未注明默认非必填** | 必填标识 |
| `default` | 参数表「默认值」列 | 生成兜底 |
| `desc` | 参数表「备注」列 | LLM 理解字段含义 |
| `value` | 请求示例/返回示例代码块 | **唯一用于填充的值**；无示例 = `""` |

**核心规则：**
- `body` / `return` 是**数组**（保序），每个字段 6 项，一个不少
- `header` 是**对象**（名→值映射），示例值或 `""`
- 字段集合 = 参数表全部字段；value 从示例块按字段名对齐，示例没有才留空
- value 只取自示例块，**不**混入默认值（`default` 独立存放，value 永远可追溯）

---

## 1. 解析器改动（`ingest/api_parser.py`）

### 1.1 新输出结构

API dict 的 key 改名（与用户批准的结构对齐）：

| 旧 key | 新 key | 内容 |
|--------|--------|------|
| `headers` (list) | `header` (object) | 名→值映射 |
| `parameters` (list) | `body` (list) | `[{name,type,required,default,desc,value}]` |
| `returns` (list) | `return` (list) | 同上 |
| — | — | `name/method/url/description/annotations` 不变 |

### 1.2 新增能力：解析纯 Markdown 接口文档

当前只解析 YApi HTML 表格（`**Path：**`/`**Method：**` + HTML `<table>`）。健身房类文档是纯 Markdown，现解析失败（输出 3 个假接口全空）。

新增解析路径，识别形态：
- `## 接口说明` / `### 接口名称` 标题段
- `**接口路径：**` / `**接口名称：**` 键值行
- Markdown 表格（`### Headers` / `### Body 参数` / `### 返回参数说明`），列名自动映射（名称/类型/是否必须/默认值/备注）
- `### 请求示例` / `### 返回示例` 代码块 → 解析 JSON，按字段名对齐提取 `value`

### 1.3 示例捕获算法（两种格式共用）

1. 定位 `### 请求示例` / `### 返回示例` 小节，取代码块（```json ... ```）
2. `json.loads` 解析；对象则按字段名取值，数组则按顺序取
3. 遍历参数表字段：`value = 示例[字段名]`（存在才填，否则 `""`）
4. 参数表无此字段、但示例里有 → 以示例为准补入（保序排后）

### 1.4 Query 参数归属

YApi 格式中 Headers/Query/Body 分小节。现 `parameters` 是 Query+Body 拼接。
新结构按用户批准格式只保留 `header` + `body`，**Query 参数并入 `body`**（保序）。

### 1.5 `_merge_api_defs` 同步

`parameters/returns` 合并逻辑改为 `body/return` 的并集合并（incoming 优先）。

---

## 2. 存储层改动（`ingest/pipelines.py`）

`commit_api_docs` 写入 SQLite 时，key 映射更新：

```python
api_headers    = json.dumps(api.get("header", {}))    # 对象，非 list
api_parameters = json.dumps(api.get("body", []))      # 六字段数组
api_returns    = json.dumps(api.get("return", []))    # 六字段数组
```

**DB 列名不变**（`api_headers/api_parameters/api_returns`）——列名是内部存储名，无用户价值，改名需迁移 schema，不做。
`database/models.py` 列注释更新为六字段说明。

---

## 3. LLM 提取 prompt + response_model

`prompts/extraction_prompts.py` 的 `api_def_extract_prompt` 与 `prompts/response_model.py` 的 `ApiDefinition`：
- 输出字段从 `headers/parameters/returns` 改为 `header/body/return`
- 数组元素从 `{name,type,required,description,default,children}` 改为 `{name,type,required,default,desc,value}`
- 铁律：value 从文档请求示例/返回示例提取，无示例填空串；未注明必填默认非必填
- 说明：用户当前全用代码提取，此路径为兼容保留，改动后保持可用即可

---

## 4. 展示 / 喂 LLM / 检索文本

| 文件 | 改动 |
|------|------|
| `agent_components/retrievers.py` | `_format_params` 渲染改为 `name(type, 必填/可选): value`（value 为空只渲染 `name(type)`）；`_fallback_api_text`、`_compensate_api_defs_from_sqlite` 改用 `header/body/return` key |
| `agent_components/nodes.py` | `api_full_for_snapshot` 传 `body/return/header`（Phase C 快照 api_defs.json 数据源） |
| `ingest/chunking.py` | `_build_api_search_text` 读 `body/return`（新 key + 六字段），构建检索文本 |
| `web/routes/*`、`web/tasks.py`、`web/compensation.py`、`agent_components/dual_chroma.py`、`scripts/migrate_chroma_to_sqlite.py` | 读 DB 列构造 dict 处，key 改为 `header/body/return` |

**前端零改动**：`static/app.js` 仅 `JSON.stringify` 整个 API dict 展示，不按 key 名读取，新结构直接呈现。

---

## 5. 测试（方案设计）

新增/更新的用例：

| 用例 | 断言 |
|------|------|
| 纯 Markdown 文档解析 | 提取出正确接口数；body 字段集合 = 参数表全字段（一个不少） |
| 示例捕获 | value 从请求示例/返回示例正确对齐；示例没有的字段 value="" |
| required 默认值 | 未注明必填 → required=False；「非必须」→ False；「必须/是」→ True |
| 字段合并 | 参数表字段 ∪ 示例字段，顺序正确 |
| 六字段完整性 | 每个 body/return 元素恰好含 name/type/required/default/desc/value |
| YApi HTML 兼容回归 | 现有 `test_regression_extraction.py` 全部通过（含 72 接口提取、必填修复） |
| 检索文本 | `_build_api_search_text` 用新 key 正常构建 |
| 展示渲染 | `_format_params` 输出 `name(type, 必填/可选): value` 格式 |

---

## 6. 明确不改的部分

- DB 列名 `api_headers/api_parameters/api_returns` 不变
- 前端展示逻辑不变（JSON dump）
- `annotations` 机制、Phase C 依赖映射（`extract_path` 对齐 return 字段名）不变
- 历史数据不迁移，由用户重新导入

## 7. 实施顺序

1. 解析器（新格式 + 纯 MD 支持 + 示例捕获）→ 单测
2. 存储层（pipelines.py）
3. LLM prompt + response_model
4. 展示/喂 LLM/检索文本 + 其余消费点
5. 全量回归测试

---

## 8. 实施记录（2026-08-13 完成）

**改动文件**：

| 层 | 文件 | 改动 |
|----|------|------|
| 解析 | `ingest/api_parser.py` | 重写：`_is_required` 去 markdown 加粗；`_split_text_by_headers` 自动识别 h1/h2 切分级；新增 `_parse_clean_md_section`（纯 MD）；`_parse_md_table`/`_parse_html_table` 输出六字段；新增 `_parse_header_table`（名→值映射）、`_extract_json_example`+`_apply_examples`（示例捕获）；`_merge_api_defs` 改为 body/return 并集；`_coerce_api_format`（旧格式归一化） |
| 存储 | `ingest/pipelines.py` | `commit_api_docs` 写 `header`/`body`/`return`，入口 `_coerce_api_format` 归一；LLM 提取 `model_dump(by_alias=True)`+coerce |
| 存储 | `database/models.py` | 列注释更新为六字段说明（列名不变） |
| Prompt | `prompts/extraction_prompts.py` | `api_def_extract_prompt` 改 header/body/return + 六字段元素，value 只取示例 |
| 模型 | `prompts/response_model.py` | `ApiDefinition` 改 header/body/returns(alias="return")，`model_validator` 兼容旧 key |
| 展示 | `agent_components/retrievers.py` | `_format_params` 渲染 `name(type, 必填/可选): value (desc)`；`_fallback_api_text`/`_compensate` 改新 key；检索路径 `_coerce_api_format` 归一 |
| 展示 | `agent_components/nodes.py` | 两处 Phase C 快照改普通 dict（header/body/return），移除 `ApiDefinition` 使用 |
| 检索 | `ingest/chunking.py` | `_build_api_search_text` 读 body/return（含 value） |
| 消费点 | `web/routes/modules.py`、`docs.py`、`web/compensation.py`、`web/tasks.py`、`agent_components/dual_chroma.py`、`scripts/migrate_chroma_to_sqlite.py` | 读 DB 列构造 dict 处 key 改为 header/body/return |
| 测试 | `tests/test_api_format_new.py`（新增）、`tests/test_regression_extraction.py`、`test_ingest_main_flow.py`、`test_commit_api.py`、`test_new_node_evaluation.py` | 新结构用例 + 旧断言更新 |

**验证结果**：
- 纯 Markdown（健身房接口文档）：21 个接口，name/method/url/header/body/return 全正确，value 与示例对齐，`facName(**必须**)→required=true`，未注明必填→false
- YApi（用电/api.md）：72 个接口完整提取，Query 并入 body，file 参数保留，响应信封不误塞 body，`/electricMeter/updateAll` 空参数保持空
- 回归：`tests/test_regression_extraction.py`（30）、`test_api_format_new.py`（22）、`test_ingest_main_flow.py`、`test_commit_api.py`、`test_retrieval_api_dedup.py` 共 101 项全过；Phase B/C 套件 200 项全过
- 前端零改动：`static/app.js` 仅 `JSON.stringify` 展示，不按 key 读取

**明确不做**：DB 列名不变、前端不改、`annotations`/Phase C 依赖映射不改、历史数据不迁移（用户重新导入）。
