# Axure 页面块四段 v2（①目录含弹窗 ②必填 ③筛选项 ④说明 + 页面图片）

> 日期：2026-08-17
> 状态：**✅ 理解已与用户逐项确认，探针已验证关键机制，待执行**
> 前置：`2026-08-17_axure_required_fields_plan.md` 已实施（必填字段按页面分块 ✅）
> 目标：每个 Axure 页面 = **主页面 + 弹窗子块**，每块四段切分
> ① 目录（块标题）② 必填字段 ③ 筛选项 ④ 页面说明，用于**详情面板展示** + **语义检索块**。
> 前提：历史数据不迁移，用户重新导入（`replace_terms` 按 `source_doc` 幂等覆盖）。

---

## ★ 新窗口接手清单（README — 先读这里）

**背景**：用户（产品/测试）逐个页面确认 Axure「页面块四段」设计。电表管理 已逐项确认完毕，理解达成一致。本窗口因 UI 无法滚动，用户新开窗口继续。**新窗口直接按本清单执行，无需重新探针。**

### 已确认的设计（勿再改）
1. **每页 = 主页面 + 弹窗子块**；块标题 = ①目录
2. 电表管理 → `[电表管理, 电表管理/添加, 电表管理/查看（弹窗）]`
   - 添加弹窗 = u103 动态面板 state「添加」（16 个必填红星全在此）
   - 查看弹窗 = u103 state「历史电量」= 历史电量表（**用户确认：历史电量表即查看弹窗**）
   - u103 state「State 1」（选项列表）无业务标签 → 跳过
   - 弹窗标题映射：`历史电量 -> 查看（弹窗）`（用户选「按你的」）
3. **③ 筛选项 = 字段名 + 选项值**（用户明确要名称，选项保留）
4. **④ 页面说明 = 除结构树（导航）外的一切**，按块归属
5. **主页面 ② 无必填 → 存页面图片**（"先存图片，后续引入多模态识别"）
6. 弹窗检测 = 隐藏动态面板 + 表单标记双条件（防误判）

### 探针已验证的机制（直接采用，勿重复验证）
| 机制 | 证据 |
|------|------|
| 导航（结构树）= 含 `<a class="link">` 链接的动态面板 | 3 页均唯一（24 链接），电表管理=u53 |
| 弹窗 = `ax_default_hidden` 容器内 `panel_state` 有表单标记（确定/取消/新增） | 电表管理 u103；企业结算配置 41 hidden 中仅 u998 是真弹窗 |
| 主页面④排除导航+弹窗后 = 查询表单+需求规格 | "1.新增字段 字段名称\|字段类型\|必填\|选项/规则\|联动逻辑…5.补充约束" 完整保留 |
| 页面图片 = Edge/Chrome 无头截图 | `msedge --headless --screenshot` 1400x900 成功（176KB） |

### 当前代码状态（已做 vs 待做）
- ✅ **已实现**：glossary `kind` 列迁移（models.py / `__init__.py::_migrate_db` / glossary.py / docs.py:121 / modules.py:132）；`_extract_filters_from_html`（字段名+选项值）；`_extract_page_explanation`（当前=全量复制，**需加排除集**）；`to_product_doc_chunks` 四段重构（header 用 page_path）；pipelines glossary_terms 组装（部分）
- ⏳ **待做**：导航面板识别 `_find_nav_container_id`；弹窗提取 `_extract_dialogs_from_html`；page_explanation 排除集；dialogs 入库；页面图片渲染+`page_images` 表；前端四段展示；测试；清理探针文件
- 🧹 **清理**：`_probe_*.py` `_probe_*.txt` `_verify_*.py` `_show_page.py` `.tmp_axure/` `.tmp_axure2/`（git status 里的 `??` 文件）

### 待确认的开放项
- **主页面 ④ 顶栏**（"XXXX运营管理平台 资源中心 中心引擎 应用中心 智慧用电 x / 面包屑 智慧大脑 工作台"）：建议排除（框架性重复），但**用户未拍板** → 执行到此处时向用户确认
- 弹窗标题映射表 `_DIALOG_TITLE_OVERRIDES` 的维护方式

### 执行顺序（参照 §3→§9）
解析器（§3）→ 分块（§4）→ 入库（§5）→ 页面图片（§6）→ 前端（§7）→ 测试（§8）→ 端到端验证（§9）

---

## 0. 用户逐项确认的理解（电表管理 实例）

| 段 | 规则 | 电表管理 实测 |
|----|------|--------------|
| ① 目录 | 主页面 + 弹窗子块；标题 = `页面路径/弹窗名` | **[电表管理, 电表管理/添加, 电表管理/查看（弹窗）]** |
| ② 必填 | 红星字段归属具体块 | 16 个必填全在**添加弹窗** → 归「电表管理/添加」块；主页面 ② 无必填 → 存页面图片（先存，后续多模态识别） |
| ③ 筛选 | **字段名 + 选项值**（用户确认，不要只留名称） | 计费方案:分时\固定、对接协议:modbusRTU\modbusTCP\BA\HTTP、计费方式:网关\直连\三方 |
| ④ 说明 | **除结构树外的一切**，按块归属 | 主页面：查询表单 + 完整需求规格（1.新增字段…5.补充约束）；弹窗：各自表单/表格内容 |

**块划分（电表管理，探针确认）：**

| 块 | 内容载体 | 说明 |
|----|---------|------|
| 电表管理 | 可见页（导航+查询表单+需求规格） | ④ 排除导航面板 + 隐藏弹窗面板 |
| 电表管理/添加 | u103 动态面板 state0「添加」 | 新增电表表单（16 必填） |
| 电表管理/查看（弹窗） | u103 动态面板 state2「历史电量」 | = 历史电量表（用户确认此即查看弹窗） |
| （跳过） | u103 state1「State 1」 | 无业务含义标签，跳过 |

> 弹窗标题说明：状态标签为「历史电量」，用户确认块标题用「查看（弹窗）」。
> 实现用 `_DIALOG_TITLE_OVERRIDES` 小映射（历史电量→查看（弹窗）），其余用状态标签。

---

## 0.1 探针验证的关键机制（2026-08-17，真实 zip）

| # | 机制 | 验证结果 |
|---|------|---------|
| ① | **弹窗 = 隐藏动态面板** | 电表管理 u103：`ax_default_hidden` 容器含 `panel_state`，状态 [添加][State 1][历史电量] |
| ② | **导航（结构树）= 含 `<a class="link">` 的面板** | 3 页（电表管理/企业余额/计费规则）均有且仅 1 个面板含 24 个页面链接 → 即左侧菜单树，整体可排除 |
| ③ | 主页面 ④ 排除导航+弹窗后 | 保留查询表单 + 完整需求规格（"1.新增字段 字段名称\|字段类型\|必填\|选项/规则\|联动逻辑…5.补充约束"）|
| ④ | 页面图片渲染 | Edge 无头 `--screenshot` 成功（1400x900 PNG，176KB 有效内容）|
| ⑤ | 企业结算配置 41 个 hidden 容器 | 多数是导航/顶栏/表单区（非弹窗），真弹窗仅 u998（"新增结算配置 确定 取消"）→ **弹窗规则需「隐藏动态面板 + 表单内容标记」双条件防误判** |

**弹窗检测规则（双条件）**：
1. 容器 `class` 含 `ax_default_hidden` 且含 `panel_state`（动态面板）
2. 状态内容含**表单标记**：`确定|取消` 按钮，或 `新增|添加|编辑|查看` 标题，或状态标签为业务含义词（非 `状态 N`/`State N`）

---

## 1. 展示格式（四段全显示，每页 = 主块 + 弹窗子块）

```
⭐ 页面结构（Axure 详情面板）
┌─ 电表管理 ─────────────────────┐
│ 🖼 页面图                        │   ← 主页面渲染截图（先存，后续多模态识别）
│ 📋 必填字段   （主页面无 → 不显示）│
│ 🔽 筛选项                        │
│  · 计费方案: 分时\固定            │
│  · 对接协议: modbusRTU\modbusTCP\BA\HTTP
│  · 计费方式: 网关\直连\三方       │
│ 📄 页面说明                      │
│  添加电表时字段分类展示、字段联动… │
│  1.新增字段 字段名称|字段类型|…   │
└────────────────────────────────┘
┌─ 电表管理/添加 ─────────────────┐
│ 📋 必填字段  · 电表名称 · 电表编号…（16个）
│ 🔽 筛选项     （弹窗内下拉）      │
│ 📄 页面说明  新增电表 确定 取消…  │
└────────────────────────────────┘
┌─ 电表管理/查看（弹窗） ─────────┐
│ 📄 页面说明  历史电量表（电表名称/│
│ 电表类型/关联房间/…模拟行）       │
└────────────────────────────────┘
```

**检索块（to_product_doc_chunks）同构**，每子块一个 `## 页面: 块标题`，四段带标题：

```
## 页面: 电表管理/添加
### 必填字段: 电表名称 | 电表编号 | …
### 筛选项: …
### 页面说明: …
```

---

## 2. 数据模型

### 2.1 glossary `kind` 列（已实现迁移）
| kind | term | definition | notes |
|------|------|-----------|-------|
| `required` | 字段名 | `"必填"` | 块标题（如 电表管理/添加） |
| `filter` | 字段名 | `"分时\\固定"` | 块标题 |
| `explanation` | 块标题 | 说明文本 | 块标题 |

### 2.2 页面图片（兜底内嵌图，**不截图**）
> **2026-08-17 用户修正**：不做 Edge/Chrome 无头截图（全部删除）。页面内嵌图片仅作
> **兜底**——只有页面**无可提取内容**（②必填/③筛选/④说明 全空）时，才取页面 HTML
> 内 `<img>` 引用的图片展示；文件本身无图片 → **置空**，不生成记录。

- 提取：parser `_extract_embedded_images(html)` → `<img src>`（排除 data:/http(s)，去重）
- 入库：`_save_embedded_page_images` 复制 `data/page_images/{doc_id}/{图片名}`（重名加序号）
- 新表 `page_images(id, doc_id, page_path, page_url, image_path)`，幂等覆盖（重导先删旧）
- 判定：`_page_has_extractable_content(detail)` 为空且 `embedded_images` 非空才复制
- 用途：纯图形页块 🖼 展示（如 电表管理 全 28 页均有文本 → 智慧用电不产生图片）

### 2.3 比对发现（已核实际，修正）
| # | 差异点 | 修正 |
|---|--------|------|
| ① | `/api/docs/{id}/glossary`（docs.py:121）只序列化 `{term,definition,notes}` | 补 `kind`；modules.py:136 同步 |
| ② | `replace_terms`/`add_term` 不带 kind | 已改（kind 默认 required） |
| ③ | `to_product_doc_chunks` header 现用 `page_name` | 改为块标题（含弹窗） |
| ④ | 现块含「交互流程」段 | 保留为第 5 段（检索不回归） |
| ⑤ | 模块聚合视图 `loadGlossary` 混入筛选/说明行 | axure 行加 kind 小标签 |
| ⑥ | `process_axure_zip` mock 测试缺字段安全 | `filters`/`page_explanation`/`dialogs` 均 `.get()` 默认 |

---

## 3. 解析器改动（`agent_components/axure_parser.py`）

### 3.1 导航面板识别 `_find_nav_container_id(html) -> str|None`
- 遍历 `panel_state` 容器，找内含 `<a class="link">` 链接的面板（导航树）
- 返回容器 div id（如 `u53`），供主页面 ④ 排除

**代码骨架（探针已验证 3 页）**：
```python
def _find_nav_container_id(html):
    # 每个 panel_state 容器（含 data-label），取内容检查 a.link
    for m in re.finditer(r'<div id="(u\d+_state\d+)"[^>]*data-label="([^"]*)"[^>]*>', html):
        nxt = re.search(r'<div id="u\d+_state\d+"', html[m.end():])
        inner = html[m.end(): m.end() + (nxt.start() if nxt else 30000)]
        if re.search(r'<a[^>]*class="link"', inner):       # 含页面链接 = 导航树
            pid = m.group(1).split("_state")[0]            # "u53_state0" -> "u53"
            return pid
    return None
```

### 3.2 弹窗提取 `_extract_dialogs_from_html(html, page_path) -> list[dict]`
- 遍历 `ax_default_hidden` 容器，取含 `panel_state` 的动态面板
- 每个状态的 `data-label` + 内容文本；**表单标记**判定是否真弹窗
- 状态标签无业务含义（`状态 N`/`State N`）且无表单标记 → 跳过
- 返回 `[{"panel_id": "u103", "state": "添加", "title": "电表管理/添加", "html": ...}]`
- `_DIALOG_TITLE_OVERRIDES = {("电表管理","历史电量"): "查看（弹窗）"}`

**代码骨架**：
```python
_FORM_MARKERS = ("确定", "取消", "新增", "添加", "编辑", "查看")
def _extract_dialogs_from_html(html, page_path):
    dialogs = []
    for m in re.finditer(r'<div id="(u\d+)"[^>]*class="[^"]*ax_default_hidden[^"]*"[^>]*>', html):
        pid = m.group(1)
        # 容器内是否有 panel_state（动态面板）？
        inner = html[m.end(): m.end() + 8000]
        states = re.findall(r'data-label="([^"]*)"', inner[:3000])
        if not states: continue                             # 非动态面板，跳过
        # 每个状态判表单标记
        for sid, lbl in re.finditer(r'<div id="(u\d+_state\d+)"[^>]*data-label="([^"]*)"', inner):
            stxt = re.sub(r'<[^>]+>', ' ', inner[sid.end():sid.end()+2000])
            if not re.search('|'.join(map(re.escape, _FORM_MARKERS)), stxt):
                if re.match(r'^(状态|State)\s*\d*$', lbl):  # 无意义标签且无表单标记 → 跳过
                    continue
            title = f"{page_path}/{lbl}"
            title = _DIALOG_TITLE_OVERRIDES.get((page_path, lbl), title)
            dialogs.append({"panel_id": pid, "state": lbl, "title": title})
    return dialogs
```

### 3.3 页面说明 `_extract_page_explanation(html_path, exclude_ids) -> str`
- **整体复制原结构**（用户确认不改结构），但先从 HTML **移除排除集容器**
- 排除集：导航面板 + 全部隐藏弹窗面板
- 主页面 ④ = 剩余文本（查询表单 + 需求规格）；弹窗 ④ = 各自状态内容
- 保留 div 配平剥离（含自闭合标签防护）

**代码骨架（已在探针 `_probe_main4.py` 验证）**：
```python
def _strip_container(html, div_id):
    """按 div id 找到容器，配平 </div> 剥离整个容器。"""
    m = re.search(r'<div id="' + re.escape(div_id) + r'"', html)
    if not m: return html
    start, depth, pos = m.start(), 0, m.start()
    while pos < len(html):
        o = re.search(r'<div\b', html[pos:])
        c = re.search(r'</div>', html[pos:])
        oo = o.start() + pos if o else 10**9
        cc = c.start() + pos if c else 10**9
        if oo < cc:
            depth += 1; pos = oo + 4
        else:
            depth -= 1; pos = cc + 6
            if depth == 0: return html[:start] + html[pos:]
    return html

# 主页面 ④：
h = _strip_container(h, nav_panel_id)              # 排除导航（结构树）
for d in dialogs: h = _strip_container(h, d["panel_id"])  # 排除隐藏弹窗
text = _extract_ui_text_from_html(h)               # 复用现有文本提取
```

### 3.4 筛选项 `_extract_filters_from_html`（现状已实现）
- 字段名 + 选项值，`"\\".join(options)`，占位符过滤，8 组里 3 组有标签

### 3.5 `parse()` 每页写入
```python
page_details[url]["nav_panel_id"]     # 导航面板 id（排除用）
page_details[url]["dialogs"]          # [{"state","title","html"}]
page_details[url]["filters"]          # 已有
page_details[url]["page_explanation"] # 主页面 ④（排除导航+弹窗）
```

---

## 4. 分块改动（`to_product_doc_chunks`）

- 每页主块 + 每弹窗子块，块标题 = ①目录
- 四段结构（必填/筛选/说明 + 保留交互流程），空段跳过
- 说明段 = 主页面说明（主块）或弹窗状态内容（弹窗块）

---

## 5. 入库改动（`ingest/pipelines.py::process_axure_zip`）

- 主页面 → 术语条目 `notes=页面路径, kind=required/filter/explanation`
- 弹窗 → 术语条目 `notes=块标题（含弹窗名）`
- 页面图片 → `_save_embedded_page_images(doc_id, page_details, zip_path)`（兜底内嵌图）

---

## 6. 页面内嵌图片兜底（新增，**不截图**）

- 解压 zip → 对**无可提取内容**且 `<img>` 非空的页面，复制 HTML 内嵌图片
- 存储：`data/page_images/{doc_id}/{原名}.{ext}`（重名加序号），DB 记 `page_images` 行
- 文件本身无图片 → 置空，不生成记录；失败静默降级（不阻断入库）
- 移除原 Edge/Chrome headless 截图方案（用户否决）

---

## 7. 前端改动（`static/app.js` / `templates/index.html` / `static/style.css`）

- `loadDocGlossary`：按 `notes` 分组（块标题）→ 每块四段渲染（📋🔽📄 + 🖼 图片）
- 面板标题「⭐ 页面结构」；空态同步
- 模块聚合 `loadGlossary`：axure 行加 kind 小标签（必填/筛选/说明）
- `style.css`：段标题样式 + 图片样式

---

## 8. 测试

- `TestFindNavContainer`（含链接面板/无链接/None）
- `TestExtractDialogs`（双条件/表单标记/跳过无意义状态）
- `TestExtractExplanationExcluding`（导航+弹窗排除后内容正确）
- 回归：`pytest tests/test_regression_axure_parser.py tests/test_ingest_main_flow.py`

---

## 9. 验证步骤

1. 核实际：`_extract_page_explanation` 现状（含导航+弹窗，需排除）、`process_axure_zip` 组装处、前端分组逻辑
2. 执行：3→4→5→6→7→8 顺序
3. 端到端：重新导入 `智慧用电.zip`，详情面板按块显示四段 + 主页面图片
4. 清理临时探针文件（`_probe_*.py` / `_probe_*.txt` / `_verify_*.py` / `_show_page.py`）

---

# v3 重构：顶栏剔除 / 每面板一块 / 可关联文件显示 / 面板命名（2026-08-17 晚）

> 状态：**最终规则已确认**（2026-08-17，用户 企业余额 例子 +「名称根据能提取到的内容来，无 LLM」）。
> 本 v3 **覆盖/取代**上方 v2 的相应部分：
> - 切块由「主页面 + 每弹窗子块」改为 **每页 = 1 主块 + 每个隐藏动态面板 = 1 面板块**（面板内所有状态合并）
> - 块标题由「状态标签」改为 **内容标题优先**（默认可见状态内第一个非表单正文段落文本），无则回退标签/启发式
> - 每个块保留 **①目录②必填③筛选④说明 四段结构，缺项留空**
> - 弹窗标题映射 `历史电量 -> 查看（弹窗）` **取消**（历史电量并入 电表管理 面板块）

## v3.1 用户 4 条反馈

| # | 反馈 | 对应实现 |
|---|------|---------|
| 1 | 顶栏没用，不需要再提取 | 页面说明 ④ 从导航容器起点截断，顶栏天然剔除（零硬编码） |
| 2 | 不应把同一页面不同字段切成块（如 企业余额 账户记录/扣款记录） | **每面板=1块**：隐藏动态面板为块单元，面板内所有状态（含 tab）合并 |
| 3 | 可关联文件内容=必填字段，不放整个页面结构；说明也=必填；标题=页面名 | `/api/docs/unassociated` 对 axure 返回 `pages:[{title, required_fields}]`，前端渲染 页面名+必填 |
| 4 | axure 绑定面板展示名称不应与产品术语表相同 | 面板标题恒为「⭐ 页面结构」；修复 `loadGlossary` 标题残留 bug；新增表单保持 术语+解释说明 |

## v3.2 最终切块规则（纯结构，无任何业务描述）

> **通用功能**：切块方法不允许出现任何具体业务描述；块标题不依赖 LLM，只取结构上可提取的内容。

| 规则 | 说明 |
|---|---|
| ① 每页 | **1 主块**，标题 = 页面路径（如 企业预付费管理/企业余额） |
| ② 每个隐藏动态面板 | `ax_default_hidden` + 直接子级 `panel_state`。**含 ≥1 个非占位状态（标签非 状态 N/State N）或 含表单标记** → **1 个面板块**；面板内所有状态合并为该块的 ②③④ |
| ③ 全占位面板 | 全部状态为 状态 N/State N 且无表单标记 → **不成块**（并入主页面 ④） |
| ④ 块标题 | 默认（可见）状态内**第一个非表单字段的正文段落文本**（`_文本段落`/标题类 widget，跳过表格/按钮/box/`*` 开头必填星号/`：` 结尾字段标签）→ 无则回退**默认状态标签** → 默认状态也是占位 → 首个业务状态标签 → 内容启发式 |
| ⑤ 块结构 | 每个块 = **①目录②必填③筛选④说明 四段，缺项留空** |
| ⑥ 嵌套面板 | 深度感知跳过，不误判为独立块 |

## v3.2b 切块范围（智慧用电.zip，最终产出）

> 每页 = 1 主块 + 每面板 1 块（面板内状态合并）。

| 页面 | 面板（states） | 面板块标题（内容标题优先） |
|---|---|---|
| 企业余额 | u1935 [账户记录,扣款记录] | **企业余额/账户明细**（默认态内容标题） |
| 公寓余额 | u3039 [账户记录,扣款记录] | **公寓余额/账户明细** |
| 电表管理 | u103 [添加,State1,历史电量] | **电表管理/新增电表**（默认态内容标题） |
| 计费规则 | u354 [State1,选择电表,已绑定电表,绑定电表] | **计费规则/新增收费配置**（默认态 State1 内容标题） |
| 企业/公寓结算配置 | u998/u1381 [State1,选择住户,绑定住户,已绑定电表] + u795/u… [状态1 占位] | **结算配置/新增结算配置**；占位面板不成块 |
| 企业/公寓公摊生成(_1) | u2295/u4532/u3354/u5665 [公摊导入,公摊修正,…] | **公摊生成/公摊生成**（默认态内容标题） |
| 企业/公寓账单 | u4233/u5367 [State1 占位] | 不成块 → 仅 **[账单]** 主块 |

## v3.3 数据/展示变更

- **提取原文（chunks）**：主块仍输出 ②③④⑤ 完整段（检索不回退）；面板块随 v3.2 由「每状态一块」降为「每面板一块」。
- **glossary 术语**：**保留 required/filter/explanation 三种 kind**（每个块 = 1234 结构，④ 说明仍在详情面板展示）。
- **可关联文件**：axure 条目 = 页面名/块名（标题）+ 必填字段（内容）；说明也 = 必填字段。
- **解析器**：`_extract_dialogs_from_html` 面板级成块 + `_extract_block_title` 内容标题；`_extract_page_explanation` 收 html 字符串 + `nav_panel_id`（顶栏截断）。

## v3.4 实施完成（2026-08-17，代码已验证）

### 顶栏剔除最终机制（v3.1 点 1 落地）

经 25 页实测，「从导航容器起点截断」仅剔除 22/25 页顶栏；**3 页（电表管理/概览/企业结算配置）** 顶栏分两段：导航前散件 + **导航后散件簇**（面包屑/智慧大脑/工作台/资源中心，DOM 在导航之后）。最终方案：

| 环节 | 机制 | 关键点 |
|---|---|---|
| 导航识别 | `_find_nav_container(html, tops)` 多候选取 **top 最小** | 企业结算配置 整页包装面板 u795(top=1940) 含链接，真实导航 u970(top=107) → 取 u970 |
| pre-nav | 从导航容器起点截断（原有） | 剔除 运营管理平台/资源中心/中心引擎/应用中心 |
| post-nav 簇 | `_strip_post_nav_cluster(html, tops, nav_top)` | 顶栏特征类（box_1/box_2/_图片/_二级标题/_三级标题/_线段1/_形状1/nopointer）或 面包屑形态（自身 `uNN_text` = `X / Y`，且 top < nav_top+40）→ **DOM 连续段剔除**，遇第一个不匹配（弹窗/正文/表格）即停 |
| 兜底 | `nav_top` 异常（≥500）或样式缺失 | 跳过 post-nav 簇剔除，仅 pre-nav 截断（保守降级） |

实测：**25/25 页 ④ 无 运营管理平台/资源中心/中心引擎/应用中心/智慧大脑/工作台/菜单名称 残留**；表格 cell（top 0/29 等）不受影响（按顶层 widget 扫描，非任意深度）。

### 后端/前端（v3.1 点 3、4 落地）

- `web/routes/docs.py`：`GET /api/docs/unassociated` 对 axure 返回 `pages:[{title, required_fields}]`。`_axure_pages_from_glossary` 按术语 notes（=块标题）枚举所有块（缺必填则 `required_fields:[]`），required 术语去重。
- `static/app.js`：`loadUnassociatedDocs` axure 条目渲染 页面名/块名 + 必填字段 chips（无必填显示「无必填字段」）；`loadGlossary` 显式复位 `#glossary-title` 为「📝 术语表」（修复 axure 详情后标题残留）。`loadDocGlossary` axure 分支（1234 结构）保持不变。
- `static/style.css`：`.axure-pages` / `.axure-page-row` / `.axure-page-name` / `.axure-page-empty` 样式。

### 测试（v3.4 全部通过）

- `tests/test_regression_axure_parser.py`：`TestExplanationExclusion` 改 html 字符串入参；新增 `TestTopBarTrim`（pre-nav+post-nav 剔除、无导航全保留、nav_top 异常降级）；新增 `TestPanelMerge`（每面板 1 块、多状态合并 ②③④、内容标题）。
- `python -m pytest tests/test_regression_axure_parser.py tests/test_ingest_main_flow.py` → **51 + 20 全绿**。
- 真实 zip 端到端：`parse()` 块结构 = 每页主块 + 每面板块（企业余额 → `[企业余额, 企业余额/账户明细]` 等），弹窗必填完整（新增电表 16 项）。

---

# v4 重构：关联前后展示一致 / 术语表式分组 / 分析去重复 / axure 文案去术语化 / 图片 BLOB 直存（2026-08-18）

## v4.1 用户 5 条反馈

1. **关联前后展示一致**：未关联时展示的「页面名+必填字段」内容，关联后点详情也要同步展示。
2. **术语表式分组**：一个「页面标题 + 必填字段」为一组，按术语表形式展示；右侧现在渲染的四段结构（①目录②必填③筛选④说明🖼）丢掉——中栏「提取原文」四段结构（②必填③筛选④说明=原文⑤交互）已含原文。
3. **分析结果去重复**：只在中栏最底部 `#analysis-result` 统一展示；详情面板「📊 分析结果」tab 删除，仅留「📄 提取原文」。
4. **axure 文案去术语化**：右栏搜索框、新增表单、按钮改页面语义（🔍 搜索页面 / 页面名+必填字段 / + 添加页面）。
5. **仅有图片页面**：原图字节直存 SQLite（BLOB），不再存 `## 页面: X\n路径: Y.html` 无用文本（多模态可直接取字节分析）。

## v4.2 实施完成（2026-08-18，已验证）

### 数据/模型
- `database/models.py`：`PageImage` 增加 `image_data = Column(LargeBinary)` 原图字节；`image_path` 改为仅存原文件名。
- `database/__init__.py`：`_migrate_db()` 对 page_images 补 `image_data BLOB` 列（SQLite ALTER ADD COLUMN）。

### 解析器（`agent_components/axure_parser.py`）
- `to_product_doc_chunks`：页面无 ②③④（必填/筛选/说明）且有 `embedded_images` → chunk 内容仅 `## 页面: {page_path}`（去掉 `\n路径: {url}` 无用文本），前端据「无 ### 段 + page_images 有图」渲染原图。

### 入库（`ingest/pipelines.py`）
- `_save_embedded_page_images`：读 `<img>` 原图字节写入 `PageImage.image_data`（BLOB 直存 SQL），不再复制磁盘文件；`image_path` 只存原文件名。

### 后端（`web/routes/docs.py`）
- `GET /{doc_id}/page-images/file/{image_path}`：优先返回 `image_data` BLOB（按扩展名推断 MIME），旧数据回退磁盘文件。
- `POST /{doc_id}/glossary`：接受 `notes` + `kind`（axure 添加页面块：term=字段、notes=页面标题、kind=required），幂等删除按「同名+同组」匹配（跨组同名互不影响）。

### 前端（`static/app.js` / `static/style.css`）
- `loadDocGlossary` axure 分支：每块 = `glossary-page-title`（页面标题 + ✏️ 重命名）+ 每必填字段一行（术语表形式）；无必填显示「无必填字段」。删除 ①目录②必填chips③筛选④说明🖼图片 四段结构。
- `toggleDocChunkDetail` / `renderChunkDetail`：删除 📊 分析结果 tab 与 `_loadDocAnalysis`/`_docAnalysisCache`/`_switchDocDetailTab`；仅保留 📄 提取原文。提取原文对「仅图片页」（chunk 无 ### 段且 page_images 有图）渲染原图替代无用文本。
- `_setGlossaryPanelLabels(mode)`：axure 详情 → 搜索框「🔍 搜索页面...」、输入「页面名 / 必填字段（逗号分隔，可空）」、按钮「+ 添加页面」；模块视图复位术语表文案。
- `addGlossaryTerm`：axure 文档详情分支 → 解析页面名 + 必填字段（逗号/顿号/换行分隔），POST notes+kind；空必填 → 建占位块（kind=explanation）。
- `static/style.css`：新增 `.chunk-img`（提取原文图片渲染）。

### 测试（v4.2 全部通过）
- `tests/test_regression_axure_parser.py`：新增 `TestChunksMainPlusDialogs::test_image_only_page_chunk_omits_path_line`（仅图页面 chunk 无路径行、无 body 段；有字段页面保留完整头+必填段）。
- 真实 zip 端到端：`企业充值记录` chunk = `## 页面: 企业预付费管理/企业充值记录`（无路径行）；`_save_embedded_page_images` BLOB 直存（u37.png 1415B / u2101.png 91639B，PNG 魔数 OK）。
- API 端到端（TestClient）：POST 页面块 notes+kind 幂等；`/unassociated` 返回 `pages:[{title, required_fields}]`；BLOB 图片 `image/png` 200 返回。
- 全量：585 + 2(key_flows) + 20(ingest) + 52(parser) 通过。
