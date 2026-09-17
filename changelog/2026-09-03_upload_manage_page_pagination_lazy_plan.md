# 2026-09-03 上传页 / 管理页主列表 分页 + 惰性加载 方案

> **状态：已施工并验证（2026-09-04）。** 用户已决策（AskUserQuestion 09-03）+ 09-04 两页范围澄清与
> 「2.保留」（axure 当页聚合）。先文（本 changelog）→ 代码+测试 → **测试全绿后同步 README**（已同步）。
> 遵循原则：①确定方案先确认 ②最小改动 ③只改相关、连带项汇总提醒 ④方案含测试改完跑 ⑤文档顺序。

## 一、需求与决策

**背景**：页面加载采用「一次全量返回」模式，文档量增长后整页渲染/传输变重。用户提出参考方案
（分页 + 惰性加载，长期方向）：

> 1. 不一次全量返回；2. 前端默认只加载第一页；3. 滚动到底/「加载更多」翻页；
> 4. 删除用已有 id 直接发 DELETE；5. 按页缓存，每页独立失效。

**应用页面（用户澄清）**：**文档上传**（Tab `upload`）、**文档管理**（Tab `manage`）两个页面。

**决策（AskUserQuestion 2026-09-03 + 用户回复 2026-09-04）**：
1. **改造范围 = 仅两页主列表**：上传页「已入库文档」列表 + 管理页「可关联文件」列表。
   模块树 / 已关联文档 / 右栏术语表（现总量 231，量级小且分组语义复杂）**不纳入本方案**。
2. **搜索/筛选 = 服务端查询参数**：列表接口增加 `type/q/page/page_size`，服务端过滤 + 按类型计数；
   保证分页后全局搜索/筛选仍正确（不做「仅对已加载页生效」的语义退化）。
3. **全选 = 跨页全选、批量删除 = 删除全部已选（用户 09-04 定）**：
   section 表头「全选」勾选的是**当前筛选（type+q）下的全部 N 条**，而非仅当前已加载行；
   「批量删除」对全部已选生效（如：全选 → 全部删除）。因分页后客户端只有已加载行，
   需要**后端批量删除接口**按筛选/名单执行（见三·接口 3）。管理页「可关联文件」的表头本就是
   「全选本页」文案 + 批量关联到模块，维持现页级语义不动。

## 二、现状事实（代码核对 + 量级实测）

- 探测 data/app.db：documents **148**（api 144 / product 3 / axure 1）、modules 5、glossary 231。
  长尾风险主要在**文档/文件数**，故主列表分页收益明确；术语表量小，暂不动。
- 上传页：`switchTab('upload')`/`init` 均调 `refreshFileList()` → `GET /api/files/uploaded-files`
  （`web/routes/files.py:220`）一次返回全部文档（SQLite `DocOps.get_all_documents` 已按
  `upload_time DESC` + 内存合并），前端 `renderFileList→renderFileSections`（`static/app.js:344/367/373`）
  按 product/api/axure 三段整表渲染；类型筛选 + 文件名搜索 + 全选/批量删除**全在客户端**。
- 管理页：点模块后 `loadUnassociatedDocs()` → `GET /api/docs/unassociated`
  （`web/routes/docs.py:14`）返回全部未关联文档，axure 每条**内嵌整页页面块**（由 glossary 聚合
  `_axure_pages_from_glossary`，`docs.py:39`）；右栏另有点详情才加载的术语表。
- 两列表操作层：`DocOps.get_all_documents`（docs.py:20，按 upload_time DESC）、
  `DocOps.get_unassociated_docs`（docs.py:45，先求差集再按 upload_time DESC）——分页前序已稳定，
  补 file_name 次级排序键即可严格稳定。
- 服务端无任何列表分页参数；无前端 JS 测试框架（回归靠后端 + 手动清单）。

## 三、后端改造（2 接口，全部**向后兼容**）

> 兼容原则：分页参数**全部可选**。不传 `page_size` → 返回现有全量 + 旧字段（存量前端与
> 测试零改动）；传参才走分页。`counts`/`total`/`has_more` 为**新增字段**，旧调用忽略即可。

### 接口 1：`GET /api/files/uploaded-files`（web/routes/files.py:220）

请求参数：`type ∈ {product,api,axure} | ""(全部)`、`q`（文件名包含，不区分大小写）、
`page ≥1`、`page_size ≥1`（`page_size=0` → 仅返回计数、不返回 rows）。

响应新增：
```
{ files: [...当前 type+q 过滤后的本页行...],   // 行字段不变
  total,                                        // 当前 type+q 过滤总数
  counts: {product:int, api:int, axure:int},    // 按 q（不含 type）分组各类型总数，供 section 头/Tab
  page, page_size, has_more, vector_ready }
```

实现要点：
- 现 merge 逻辑（DB 全量 + 内存仅上传中条目）抽成辅助后：先按 `q`/`type` 过滤，再
  `page_size` 存在时切片；`counts` 用一次 `GROUP BY doc_type` 计数（应用 `q`）。
- 排序稳定：`upload_time DESC, file_name ASC`；内存「处理中」条目无时间戳 → 排最后。
- 兼容分支：`page_size is None`（缺省）→ 不切片、不加分页字段，行为与今日完全一致。

### 接口 2：`GET /api/docs/unassociated`（web/routes/docs.py:14）

请求参数：`type`、`page`、`page_size`（同上兼容；`page_size=0` → 仅计数，供 Tab 构建）。
响应：
```
{ docs: [...本页...], total, counts:{...}, page, page_size, has_more }
```
实现要点：
- 现全部返回 → 过滤 type → 排序 → 切片；axure 的 `pages` 聚合（`_axure_pages_from_glossary`）
  **只对当页 axure 行执行**（现在全量 axure 都聚合，axure 多时是隐性 N 全量开销）。
- `counts` 一次分组计数（未关联集合内，按 doc_type）。

### 接口 3（新增，支撑决策 3）：`POST /api/files/batch-delete-files`（web/routes/files.py）

body（二选一）：
```
{ "filter": { "type": "product|api|axure|''", "q": "文件名包含" } }   // 全选路径：按筛选删全部
{ "names": ["a.pdf", "b.xlsx", ...] }                                  // 部分勾选路径：按名单删
```
响应：`{ success: true, deleted: N, failed: M, errors?: [name, ...] }`。

实现要点：
- 把现 `delete_file` 的清理流程（SQLite bindings+doc、ChromaDB、物理文件+meta、内存 imported
  移除，`web/routes/files.py:106-217`）抽成可复用辅助 `_delete_file_cleanup(filename)`，
  `delete_file` 与批量接口共用。
- `filter` 分支：按 `type+q` 从「SQLite + 内存」集合解析出匹配名单（与列表接口同一过滤口径）→
  逐条 cleanup，聚合 `deleted/failed`。删除全程在服务端一次完成，避免前端对未加载页逐条请求。
- `names` 分支：逐条 cleanup（含不存在 → failed，不阻断）。
- 无候选 → `{success:true, deleted:0, failed:0}`（幂等）。

## 四、前端改造（static/app.js）

统一模式：**按类型分页 + 每类型独立「已加载页集合」= 天然按页缓存**（各页独立、增删失效仅该类型），
容器滚动惰性 + 「加载更多」按钮兜底。

### 上传页「已入库文档」
- 新增 `_fileState = { product/api/axure: {page, hasMore, total, items[]} }` + 全局 `_fileFilter`/`_fileQuery`。
- 类型筛选/搜索：改为带 `?type&q&page=1&page_size=50` 请求后端，**回车即服务端查询并复位第一页**；
  section 头计数用响应的 `counts`（真实总量，不再只算已加载行）。
- 惰性：`file-section-body` 底部放哨兵，IntersectionObserver 到顶自动取下一页并 `items` 追加；
  无更多 → 显示「已全部加载(N)」。切换类型 Tab 保留各类型已加载状态（缓存语义），`🔄 刷新`清缓存重取。
- 单删：沿用现有 `js-delete-file` → `DELETE`/`POST delete-file`，成功 → 只失效并重取该类型第 1 页。
- **全选/批量删除（决策 3，跨页）**：
  - section 表头「全选」勾选 = 置位「该 type 在当前 q 下全选」，选中数显示服务端 `total`（如
    「已选 86 / 共 86」），与已加载页数无关；再勾取消、或勾掉任一单行 → 回到逐行选择模式。
  - 「批量删除」：全选模式 → 一次 `POST batch-delete-files {filter:{type,q}}`；逐行模式 →
    `{names:[已勾选行]}`。成功后清空选中态并重取该类型第 1 页。
  - 单行 checkbox 仍可跨已加载页累计勾选（逐行模式），不再逐条请求后端。

### 管理页「可关联文件」
- 首次进入先 `?type=&page_size=0` 取 `counts` → 构建类型 Tab（total=0 的隐藏）；默认激活第一个
  非空类型，仅取该类型 page1；切 Tab 首次才惰性取该类型页（每类型页集合缓存，同上）。
- axure 条目行内页面块随行返回（本页行才聚合），量自然变小。
- 勾选「关联到模块」沿用现有，选中作用域 = 当前已加载页行（提示同上传页）。

## 五、波及面 / 未覆盖（原则③汇总，供确认）

1. ~~全选/批量删除仅作用于已加载行~~ → **已按用户 09-04 改为跨页全选 + 全部删除**（决策 3，见三·接口 3）。
2. **axure 页面块只随当页 axure 文档计算**（未关联列表每次请求现会对所有未关联 axure 文档全量聚合
   页面块，纯性能优化，见三·接口 2）。
3. `INITIAL_FILES`（`web/app.py:338 index` 服务端整表直出嵌入 HTML）保留兼容不动；
   其随后被 `refreshFileList` 的分页结果覆盖 → 建议后续把 index 直出改为空/仅第一页（本方案不动，
   避免模板改动，登记为后续可选）。
4. 无服务端 HTTP 缓存层；「按页缓存」以前端状态集合形式实现（每类型/每页独立、增删按类型失效），
   不引入 Redis/服务端 TTL（量级不需要，登记说明）。
5. api 类型 144 条中含「处理中/未确认」条目，计数与筛选对其同样生效；`_imported_files` 内存条目
   需带 `type/name` 参与过滤（缺失时归 `product` 兜底，保持现 UI 归类）。
6. 管理页默认仅激活 Tab、切换惰性取（现 UI 已是 i===0 显示、其余 hidden，语义未变，予以保留）。

## 六、测试设计

**后端（pytest，镜像 `test_files_bindings_api.py` TestClient 模式）**：
1. uploaded-files 分页：种子 N 条跨类型文档 → `?type=api&page=1&page_size=2` 断言切片/`total`/
   `counts`/`has_more`；`q` 命中；翻到末页 `has_more=false`。
2. uploaded-files 兼容：**不带参数** → 全量返回、无分页字段（存量行为回归）。
3. uploaded-files `page_size=0` → 仅 counts。
4. unassociated 分页：种子未关联 product/api/axure + 绑定部分 + axure glossary（页面块）→
   `?type=axure&page_size=1` 断言本页行含 pages、`counts`、末页 `has_more=false`。
5. unassociated 兼容：不带参数 → 全量（含 axure 全 pages）。
6. 关联数稳定性：分页不改变 `total`。
7. **batch-delete-files**：
   a. `{filter:{type,q}}` → 删除该筛选全部匹配，SQLite/物理文件/mem 均清，返回 deleted=N；
   b. `{filter:{...无匹配}}` → deleted=0，幂等；
   c. `{names:[子集]}` → 仅删名单内，不存在项进 failed 不阻断；
   d. `delete_file`（单条）与批量接口共用清理逻辑后回归不破坏。

**前端**：项目无 JS 测试框架 → 后端测试 + 手动清单（三类型切 Tab、滚动翻页、搜索命中服务端、
删除后仅该类型重置、批量删已加载行、刷新按钮重置）。

**相邻回归**：`test_files_bindings_api.py` / `test_ingest_main_flow.py` /
`test_phase_a_xlsx_upload.py` / `test_doc_glossary_delete.py`；已知 `test_phase_bc_unit.py`
9 项在途失败与本方案无关（不触碰其文件）。

## 七、文档更新（顺序）

1. 本 changelog（先行）。
2. 代码 + 测试（四/六）。
3. **测试全绿后**同步 `README.md`（按用户 09-03 顺序约定）——接口列表加两处分页参数说明、
   上传页/管理页功能描述补「主列表分页 + 惰性加载」。

---

## 八、实施记录（2026-09-04，全部改动 + 验证结果）

### 后端（web/routes/files.py、web/routes/docs.py）

| 位置 | 改动 |
|------|------|
| `files.py` | 新增 `_merged_uploaded_state()`：SQLite 全量 + 内存合并，供列表与批量删除共用口径 |
| `files.py uploaded_files` | 增 `type/q/page/page_size`（兼容：`page_size` 缺省→全量+旧字段；`=0`→仅 total/counts；`>0`→切片）。响应新增 `total/counts/page/page_size/has_more` |
| `files.py` | 新增 `POST /api/files/batch-delete-files`：body `{names:[...]}` 或 `{filter:{type,q}}` |
| `docs.py get_unassociated_docs` | 增 `type/page/page_size`（同上兼容；axure 页面块 `_axure_pages_from_glossary` 只对返回行聚合） |

**实现偏差（相对方案第三节「实现要点」）**：批量删除**未**把 `delete_file` 清理流程抽成独立
`_delete_file_cleanup` 辅助，而是批量循环直接调用现端点 `delete_file(filename=nm)`（端点内部完成
SQLite/ChromaDB/物理文件/内存清理，且幂等：无记录也返回成功）。改动面更小、单条与批量天然同语义，
避免了抽辅助时的高回归风险。代价：批量删除按行循环 HTTP 语义（进程内函数调用，无网络开销）。

### 前端（static/app.js，Task #9/#10）

- 顶部新增分页状态：`_fileCounts/_filePages/_fileByName/_fileSentinelObs`、
  `_unassocCounts/_unassocActive/_unassocPages/_unassocSentinelObs`、`_fileQueryNow`、`FILE_PAGE_SIZE=50`。
- 上传页「已入库文档」：`refreshFileList` = 把当前筛选（filter+q）各类型缓存置脏并重取第 1 页；
  `setFileFilter/filterFileList`（Enter 服务端查询并复位第一页）；`loadFileTypePage(type,page)`
  首页替换、后续页追加 = **每类型独立页集合天然按页缓存**；`renderFileSections` + IntersectionObserver
  哨兵惰性翻页 + 「加载更多」按钮兜底 + 「已全部加载(N)」。
- 跨页全选（决策 3）：section 表头「全选」置位 `st.selAll`（行 disabled、title 显示「已全选 N 条（跨页）」）；
  勾满且无更多页自动等价为跨页全选。`batchDeleteFiles`：selAll 类型走一次 `{filter:{type,q:selAllQ}}`，
  逐行勾选走 `{names}`，多类型可混发；完成后各类型重置重取第 1 页。单删 `deleteFile` 沿用（删除后刷新）。
- `renderFileList(INITIAL_FILES)`（服务端直出）改为按类型客户端首页切片作首屏占位，随后被
  `refreshFileList` 服务端分页覆盖（方案波及面 3 语义保留；模板零改动）。
- 管理页「可关联文件」：`loadUnassociatedDocs` 先 `?page_size=0` 取 `counts` 建类型 Tab（total=0 隐藏），
  默认激活第一个非空类型并惰性取第 1 页；`switchUnassocTab` 切 Tab 首次才取该类型（页集合缓存同上传页）；
  axure 页面块随当页行返回；「全选本页」维持页级（已加载行）语义不动。

### 新增测试（tests/test_upload_manage_pagination.py，19 passed）

镜像方案第六节：uploaded-files 分页切片/total/counts/has_more、q 大小写不敏感、
无参全量兼容（无分页字段）、`page_size=0` 仅计数、非法参数 400；unassociated axure 当页含 pages、
末页 has_more=false、无参全量含 axure pages、分页不改变 total、`page_size=0` 仅计数、非法参数 400；
batch-delete filter 命中 N 条、无匹配幂等不触发清理、names 不存在项 failed 不阻断、names 去重、
批量内部调用单条 `delete_file`（同清理源）、空 body 400。全部 mock（数据源/清理函数），无磁盘/SQLite/Chroma 操作。

### 验证结果

- `tests/test_upload_manage_pagination.py` → **19 passed**。
- 相邻回归（files_bindings / doc_binding / doc_glossary_delete / delete_file /
  regression_operations / ingest_main_flow）→ 84 passed，1 失败见下。
- 全量 `pytest tests/` → **739 passed / 9 failed / 19 skipped**（基线 720 → +19 新用例全绿）；
  9 failed 全部在 `test_phase_bc_unit.py`（既有 Phase B/C 工作区在途，与本次无关，不触碰其文件）。

### 测试隔离坑记录（原则③连带项，供知悉、不在本次范围）

- `tests/test_delete_file.py::TestChromaRetrySuccess::test_retry_succeeds` 在**部分子集顺序**
  （其前运行含其它模块级 TestClient/事件循环的模块）下偶发失败：测试内
  `asyncio.get_event_loop().run_until_complete(...)`，前面模块关闭了当前线程事件循环即报
  「There is no current event loop」；**单独运行与全量默认顺序均通过**。属既有测试隔离缺陷，
  与分页改动无关（不含本次新增测试文件的同子集亦复现），未触碰。
