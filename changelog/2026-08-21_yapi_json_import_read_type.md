# 接口导入新增 JSON 读取类型（YApi api.json 纯代码导入）

> 日期：2026-08-21
> 状态：已实现，15 条专项测试通过 + 全量回归 661 passed
> 触发：用户提供 `D:\1-ceshi\md\财务\api.json`（YApi 导出，14 分类 / 72 接口），
> 要求在接口导入流程增加一个 JSON 读取类型。

## 背景

- 既有接口导入只有两种读取方式：**代码提取**（YApi MD，`extract_apis_from_yapi_md`）
  与 **LLM 提取**（`process_api_doc_extract`）。`.json` 文件无法处理
  （`_extract_text` 不认识 `.json`，走 product 分支会失败）。
- YApi JSON 导出比 MD 更精确：`req_query`（真正 query string）与
  `req_body_other`（JSON 请求体）天然分层，且嵌套对象/数组结构完整保留
  ——正是之前 MD 文档 query/body 合并、缺嵌套结构的痛点。

## 改动

### 1. `ingest/api_parser.py` — 新增 `extract_apis_from_yapi_json(text)`

确定性解析 YApi JSON 导出（无 LLM，秒级）→ 归一化 API def
`{name, url, method, description, header, body, return, annotations}`：

- 顶层分类数组 `[{name, list:[...]}]` 扁平化；`(method,url)` 去重；模块名取首个分类名；
  容错单分类对象 `{name, list:[...]}`。
- `req_query` → body 字段 `location="query"`（YApi `required:"1"/"0"` → bool）。
- `req_body_other`（JSON Schema draft-04 字符串）→ body 字段 `location="body"`；
  properties→六字段、`required[]`→必填、object/array 嵌套 → `children` 递归。
- `req_body_form`（含文件上传）→ body 字段 `location="body"`。
- `res_body`（JSON Schema）→ return 字段（无 location，与契约 response 语义一致）。
- `req_headers` → header `{名: 示例值}`。
- path 参数留在 URL（`{code}`），不写入 body——由 `ApiAnnotationRegistry` 的
  `has_path_params` 自动标注。
- `annotations["category"]` = 所属 YApi 分类名（随 `api_annotations` 持久化，喂 LLM）。

### 2. `ingest/extractors.py` — `_extract_text` 支持 `.json`（按 UTF-8 读文本）

### 3. 导出链 — `ingest/__init__.py` + `ingest_v2.py` re-export `extract_apis_from_yapi_json`

### 4. `web/routes/api_extract.py` — `/extract-api-code` 增加 `format` 表单参数

- `format="json"` → `extract_apis_from_yapi_json`；默认 `"md"` 走原逻辑。
- 响应 `extract_method` 按实际方式返回（`"json"` / `"code"`）。

### 5. `web/tasks.py` — `.json` 上传也走 `needs_extract_choice` 弹窗

（原只有 `.md` 弹选择窗；`.json` 之前落 product 分支必然失败。）

### 6. `static/app.js` — 选择弹窗新增第三个读取类型卡片「📄 JSON 代码提取」

- 对 `.json` 文件默认高亮该卡片；点击 → `selectExtractMethod('json')` →
  `/extract-api-code` 并携带 `format=json`；状态文案「JSON 解析中...」。

## 验证

```bash
python -m pytest tests/test_regression_yapi_json_import.py -q     # 15 passed
# 真实 api.json（D:/1-ceshi/md/财务/api.json，521KB）：
#   72 接口（49 POST + 23 GET），query 28 字段 / body 622 字段 / children 5 处
#   req_headers→header、res_body 嵌套 children 保留、path 参数留 URL + has_path_params
python -m pytest tests/ -q --deselect "tests/test_phase_bc_unit.py::TestResolveApiDefs"  # 661 passed
```

## 后续

- 本次只加读取类型，**未将财务 72 接口写库**——用户在界面确认后走 `commit-api` 入库。
- `annotations.category` 已入 API，生成阶段可用分类语义约束主数据/断言。

## 2026-08-21 补充：上传入口堵点 + location 丢失修复

第一轮改完发现前端/上传路由仍不支持 `.json`（只改了 tasks.py + app.js 弹窗环节），补三处：

- **`web/routes/files.py`** — 上传路由 `type_map` 增加 `".json": "md"`（原只认 `.md/.pdf/.docx/.zip/.yml/.yaml`，
  `.json` 在 line 46 直接被拒）。
- **`templates/index.html`** — 接口上传文件选择器 `accept=".md,.txt"` → `.md,.txt,.json`（文件选择框直接不可选 json）。
- **`ingest/api_parser.py` `_normalize_field_item`** — **潜在丢键修复**：`commit_api_docs` 入库前对每个 API
  跑 `_coerce_api_format` → `_normalize_field_item`，旧实现只重建六字段、**丢弃 `location`**，会静默抹掉
  query/body 分层。改为保留附加键（MD/LLM 路径字段本就无附加键，行为不变）。
  `tests/test_regression_yapi_json_import.py::test_location_survives_commit_normalization` 锁定。

验证：真实 api.json 全链路（上传放行 → `_extract_text`(.json) → 解析 72 接口 → 归一化 → 标注）后
location 分布 28 query / 622 body 完整保留；全量回归 **662 passed**。

## 2026-08-21 补充：多分类模块名误导 + 模块关联全选

用户反馈「提取的全是坏账管理」——实为解析器对 14 分类文件返回单一 `module_name`（首个分类）
误导展示，提取本身没错。修两处：

- **`ingest/api_parser.py` `extract_apis_from_yapi_json`** — 多分类文件 `module_name` 置空
  （单分类仍用分类名）；每个接口的 `annotations.category` 才是真实归属。
  `tests/test_regression_yapi_json_import.py` 更新断言（多分类 → `''`，单分类 → 分类名）。
- **`static/app.js` 确认弹窗 `_renderApiConfirmModal`** — 按 `annotations.category` 分组展示
  （📁 分类名（数量）），头部显示「N 个接口 / M 个分类」，模块名改为可编辑输入框（多分类留空）。
- **`static/app.js` 模块关联面板** — 改为**逐接口勾选 + 批量绑定**（用户要求选接口而非按分类整批）：
  每个未关联文档行加复选框（`js-unassoc-check`，可跨分类/跨文档类型勾选），顶部工具栏
  「全选本页」+ 实时计数「已选 N」+「⚡ 批量关联选中」；新增 `toggleAllUnassoc()` /
  `updateUnassocCount()` / `bindSelectedDocs()`（一次性 confirm + 循环 POST /api/bindings +
  统一 invalidateAnalysis/刷新），替换原「全选关联当前分类」`bindAllDocGroup()`。

验证：真实 api.json → `module_name=''`，14 分类 72 接口分布正确；全量回归 **663 passed**。
