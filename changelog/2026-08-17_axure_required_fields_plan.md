# Axure 原型按页面提取并展示必填字段方案

> 日期：2026-08-17
> 状态：**✅ 已按此方案实施完成**
> 背景：智慧用电 Axure 原型（`uploads/axure/智慧用电.zip`）的表单必填项以红色星号标记
> （`<span style="color:#D9001B;">*</span><span>字段名</span>`，全包 85 处 / 61 条去重字段），
> 目前只混在页面文本 chunks 里，前端没有结构化的「必填字段」视图。
> 目标：入库时按页面提取必填字段，前端在 Axure 文档详情面板按页面分块展示，
> 展示形式与逻辑沿用产品文档术语表机制。
> 前提：历史数据不迁移，用户重新导入（`_save_to_sqlite` 内部按 `source_doc` 幂等覆盖）。

---

## 0. 展示格式（已与用户确认）

**每个页面一个块，块名 = 页面路径（有父级时 `父级/页面名`），块内容 = 该页必填字段列表。**

```
⭐ 必填字段（Axure 文档详情右侧面板）
┌──────────────────────────────────────┐
│ ┌─ 电表管理 ─────────────────────┐   │  ← 块名 = 页面名（无父级）
│ │ · 电表名称 · 电表编号 · 安装位置 │   │  ← 块内容 = 必填字段列表
│ └────────────────────────────────┘   │
│ ┌─ 企业预付费管理/企业公摊生成 ──┐   │  ← 块名 = 父级/页面名
│ │ · 公摊用量 · 公摊电价 · 所选住户 │  │     （区分同名页）
│ └────────────────────────────────┘   │
│ ┌─ 企业后付费管理/企业公摊生成 ──┐   │
│ │ · 公摊用量 · 公摊电价 · 所选住户 │  │
│ └────────────────────────────────┘   │
│  （无必填字段的页面不展示块）          │
└──────────────────────────────────────┘
   （搜索框 / 手动增删沿用术语表逻辑，交互一致）
```

**数据映射（复用 `glossary` 表 `GlossaryTerm`: term/definition/notes/doc_id/source_doc）：**

| 字段 | 内容 | 用途 |
|------|------|------|
| `term` | 必填字段名（如「电表名称」） | 块内列表项 |
| `definition` | `"必填"` | 列表项副文本 |
| `notes` | 页面路径（如 `企业预付费管理/企业公摊生成`） | **分块依据**，区分同名页 |
| `source_doc` | zip 文件名 | 重导幂等覆盖 |

---

## 1. 解析器改动（`agent_components/axure_parser.py`）

### 1.1 必填字段提取
- 新增模块级正则 `_STAR_SPAN_RE`：匹配红色星号 span
  `<span[^>]*style="[^"]*color\s*:\s*#\s*[Dd]9001B[^"]*"[^>]*>\s*[*＊]\s*</span>`
  （大小写 / 全角星号兼容）
- 新增静态方法 `_extract_required_fields_from_html(html_path) -> list[dict]`：
  - 对每个星号，取**后随 `<span>`** 文本（截至 `</p>` / `<br`，上限 300 字符）
  - `html.unescape` 解码实体 → 折叠空白 → 去掉结尾 `：`/`、`/`；`
  - 空标签 / 长度 >40（误抓防御）丢弃，按出现顺序去重
  - 返回 `[{"field": "字段名"}]`
- `parse()` 每页写入 `page_details[url]["required_fields"]`

### 1.2 RP9 页面树（父级路径）
- RP9 的 `data/document.js` 是压缩格式（`_(k1,v1,...)` 字典构造器 + 变量字典表），
  页面树含文件夹层级（`type="Folder"` + `children`）——**同名页靠父级区分**
  （`企业公摊生成` vs `企业公摊生成_1.html` 分属 企业预付费管理 / 企业后付费管理）
- 新增递归解码器（括号配平 + 顶层逗号切分 + `_()` 字典解码 + 变量表解析）：
  - `_match_js_bracket` / `_split_js_elements` / `_decode_js_value` / `_parse_rp9_sitemap`
  - 返回 `{url: "父级/页面名"}`；解析失败返回 `{}` 降级为扁平 page_name
- `parse()`：检测到 `global_data_js_content` 含 `rootNodes` 时解析，每页写入
  `page_details[url]["page_path"]`

## 2. 入库改动（`ingest/pipelines.py::process_axure_zip`）

- 遍历 `page_details`，每页 `required_fields` 转术语条目：
  `{"term": field, "definition": "必填", "notes": page_path or page_name or url}`
- 传现有 `_save_to_sqlite(..., glossary_terms=required_field_terms)`（`ingest/storage.py:29` 已支持，
  内部 `GlossaryOps.replace_terms(source_doc=file_name)`）
- 空字段自动跳过；日志打印「必填字段 N 条 / M 页」

## 3. 前端改动（`static/app.js` / `templates/index.html` / `static/style.css`）

- `app.js::toggleDocChunkDetail`（约 598/616 行）：`docType === 'product'` → `'product' || 'axure'`，
  Axure 文档「详情」也显示右侧面板
- `app.js::loadDocGlossary`：按 `t.notes`（页面路径）分组渲染 —— **每页一个块，块名=页面路径**，
  块内罗列字段名（组内同名去重，处理同名页合并）；全空显示空态；面板标题按 docType 切换
  （product →「📝 术语表」/ axure →「⭐ 必填字段」）
- `app.js::loadGlossary`（模块视图聚合）：同步渲染 `notes` 行，Axure 字段带页面归属
- `index.html:98`：面板标题 `<h4>` 加 `id="glossary-title"`
- `style.css`：新增块标题 `.glossary-page-title` 与字段行 `.term-note`，风格对齐 `.glossary-item`

## 4. 测试（`tests/test_regression_axure_parser.py`）

- 新增 `TestExtractRequiredFields`：合成 HTML 覆盖单字段 / 多字段去重 / 带 `：` 与 `&nbsp;` 与全角 `＊` /
  过长误抓 / `html_path=None` / 空文件 / parse() 端到端（迷你 zip）
- 新增 `TestRp9SitemapTree`：构造最小 document.js，验证文件夹层级 url→父级/页面名、
  无 rootNodes 降级、parse() 端到端 page_path + required_fields
- 回归：`pytest tests/test_regression_axure_parser.py tests/test_ingest_main_flow.py`

---

## 验证

1. 单测：`pytest tests/test_regression_axure_parser.py tests/test_ingest_main_flow.py` → 49 passed
2. 真实 zip：`uploads/axure/智慧用电.zip` parse() → 28 页全含父级路径，
   两个 企业公摊生成 分别为 企业预付费管理/… 与 企业后付费管理/…，61 条必填字段
3. 端到端（重新导入）：上传 zip → 入库日志「必填字段 N 条」；绑定模块后点文档「详情」→
   右侧面板按页面块展示，同名页区分
4. 重导覆盖：同 zip 二次导入，必填字段条目不重复
