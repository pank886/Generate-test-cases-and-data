# 2026-09-03 Phase A 产品文档上传支持 Excel (.xlsx)

> **状态：用户已决策（AskUserQuestion，2026-09-03），方案已批准，开始施工。**
> 遵循原则：①确定方案先确认 ②最小改动 ③只改相关部分、发现连带项汇总提醒 ④方案设计测试用例改完测试 ⑤先更新文档再看实际情况。

> **用户决策：**
> 1. 支持范围**仅 `.xlsx`**（openpyxl 可读、零新依赖）；旧版二进制 `.xls` 继续返回 400「不支持的文件类型」。
> 2. Excel 文本化规则 **全 sheet 行式**：每 sheet 输出 `[工作表: 名]` 头，每行非空单元格以 ` | ` 连接、跨行保留；跳过全空行/全空 sheet。
> 3. **流程顺序**：本方案先入 changelog → 全量实现 + 测试 → **测试全部通过后**再同步 README.md（README 后置，不动先）。

## 一、需求背景

Phase A「产品文档上传」卡片目前只接受 `.pdf` / `.docx`（`templates/index.html:26`）。产品资料常以 Excel（`.xlsx`）交付（需求矩阵 / 字段清单表）。需求：让 `.xlsx` 产品文档与 PDF/DOCX 同等对待，走既有产品文档入库链路：

**上传 → 落盘 `uploads/product` → 提取文本 → 切块 → LLM 提模块/术语 → SQLite(documents/document_chunks/glossary) + ChromaDB**

## 二、现状事实（探索与代码核对）

- **类型白名单唯一强校验点**：`web/routes/files.py:44` `type_map`，无 MIME 校验。`.pdf/.docx → "product"`。前端 `accept` 仅文件对话框提示；拖拽/JS 无扩展名过滤（`static/app.js:148-170` `handleDrop/uploadFile/uploadWithFile`）。
- **落盘**：`files.py:52-55`，`.docx/.pdf` 归 `uploads/product`（`type_dir_name = "md" if ext==".md" else file_type`）。`.xlsx` 复用之，无新目录。
- **后台分派**：`web/tasks.py:42-153`，非 `.zip`/`.md`/`.json` 一律走 `process_product_doc`；`.xlsx` 天然落此分支，分派零改动。
- **提取**：`ingest/extractors.py:10-27` `_extract_text` 仅支持 pdf/md/txt/json/docx，否则抛 `ValueError("不支持的文件类型")`。**唯一需要新增解析能力处**。
- **存储（无 schema 变化）**：原文件在磁盘 `uploads/product`；元数据 `documents.file_type` String(20) 存真实扩展名（`process_product_doc` 中 `ext.lstrip(".")`，`.xlsx → "xlsx"`），`doc_type="product"`；正文切块入 `document_chunks`；向量入 ChromaDB `doc_search`。文件查看/二进制分支已含 xlsx（`files.py:335`、`static/app.js:36`）；删除扫描已含 `uploads/product`（`files.py:118`）；下载目录已含 uploads（`files.py:289-292`）。
- **Excel 库**：仅 `openpyxl`（`requirements.txt:38`），项目生成 test_plan.xlsx 已用。无 pandas/xlrd。

## 三、改动方案（最小集）

### 改动 1：文本提取支持 `.xlsx`（`ingest/extractors.py`）

`_extract_text` 增分支：

```python
elif ext == ".xlsx":
    return _extract_xlsx(file_path)
```

新增 `_extract_xlsx` + `_cell_to_text`：

```python
def _extract_xlsx(file_path: str) -> str:
    """Excel 工作簿全 sheet 行式文本化：每行非空单元格以 ' | ' 连接，空行/sheet 跳过。

    data_only=True 取公式缓存值；read_only=True 流式。finally close 防 Windows 手柄泄漏
    （docs/fixes_summary.md P2 _read_excel_rows 同源规则）。
    """
    from openpyxl import load_workbook
    wb = load_workbook(file_path, read_only=True, data_only=True)
    parts = []
    try:
        for ws in wb.worksheets:
            rows = []
            for row in ws.iter_rows(values_only=True):
                cells = [_cell_to_text(v) for v in row]
                cells = [c for c in cells if c]
                if cells:
                    rows.append(" | ".join(cells))
            if rows:
                parts.append(f"[工作表: {ws.title}]\n" + "\n".join(rows))
    finally:
        wb.close()
    return "\n\n".join(parts)


def _cell_to_text(value) -> str:
    """单元格值转文本：None→""，datetime/date/time 格式化，其余 str。"""
    import datetime as _dt
    if value is None:
        return ""
    if isinstance(value, _dt.datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(value, _dt.date):
        return value.isoformat()
    if isinstance(value, _dt.time):
        return value.strftime("%H:%M:%S")
    return str(value).strip()
```

合并单元格：openpyxl 中仅左上角取值、其余 None → 天然不重复。

### 改动 2：类型白名单（`web/routes/files.py:44`）

```python
type_map = {..., ".docx": "product", ".xlsx": "product", ".zip": "axure", ...}
```

### 改动 3：后台进度文案（`web/tasks.py:94`）

```python
source = {".docx": "Word 文档", ".pdf": "PDF 文档", ".xlsx": "Excel 文档"}.get(ext, "文档")
```

### 改动 4：前端卡片（`templates/index.html:25-26`）

- hint：`PDF / DOCX / Excel，自动提取模块和术语`
- accept：`.pdf,.docx,.xlsx`

### 改动 5：注释（`database/models.py:79`）

`file_type` 注释 `pdf/docx/md/zip` → 补 `xlsx`（纯注释，无迁移）。

### 改动 6：孤儿文件扫描规格（`web/app.py:104-107`）——施工核对补全，用户已决策「一并补上」

施工实际核对时发现：启动诊断 `_scan_orphan_files` 的 `_scan_specs` 按扩展名扫 `uploads/*`，
含 `("uploads/product", ".pdf"), ("uploads/product", ".docx")` 但**不含 `.xlsx`**。若磁盘
`uploads/product` 下存在 SQLite 无记录的孤儿 `.xlsx`（上传中断/手动删库等异常态），启动诊断
将漏报（只影响诊断告警 + 孤儿 .meta.json 清理，不自动删文件）。**用户决策：一并补上**。

```python
_scan_specs = [
    ("uploads/pdf", ".pdf"), ("uploads/docx", ".docx"),
    ("uploads/product", ".pdf"), ("uploads/product", ".docx"),
    ("uploads/product", ".xlsx"),          # ← 新增
    ("uploads/axure", ".zip"), ("uploads/md", ".md"),
]
```

同步 `tests/test_orphan_detection.py` 补「孤儿 .xlsx 检出」用例。

### 文档（顺序要求）

1. **本 changelog**（先行，本文）
2. README 同步 **放在测试全部通过后**（Step 后置，见四/五）

## 四、测试用例设计

**新增 `tests/test_phase_a_xlsx_upload.py`**，沿用 `test_ingest_main_flow.py`（mock LLM+ChromaDB 全链）与 `test_files_bindings_api.py`（TestClient + mock）模式。

1. **提取单测 `_extract_text`/`_extract_xlsx`**（openpyxl 造临时 .xlsx）
   - 单 sheet：含 `[工作表: ...]`；各行单元格以 ` | ` 合并；行顺序保持。
   - 多 sheet：两 sheet 均在；中间空行被跳过；空 sheet 不输出。
   - 单元格类型：数字/布尔/字符串入文本；日期型输出格式化串。
   - 全空工作簿 → 空串（进 `process_product_doc` 触发「文档内容为空」）。
2. **全链入库**：真 `.xlsx` 喂 `process_product_doc`（mock `ChatTestAgentGraph._invoke_structured` + `get_chroma_db` + 本地 SQLite）→ chunks>0、SQLite `file_type=="xlsx"`、`doc_type=="product"`、ChromaDB 写入被调用。镜像 `TestProcessProductDoc.test_full_flow`。
3. **HTTP 上传路由**：TestClient POST `.xlsx`（BytesIO 构造），patch `config.BASE_DIR`→tmp、`web.state._create_task`（async 假 id）、`web.tasks._process_file_bg`（async no-op）→ 200/成功、物理文件落 `tmp/uploads/product/*.xlsx`、后台收到 `file_type="product"`。
4. **范围守卫**：POST `.xls` → 400「不支持的文件类型」。
5. **孤儿扫描**（改动 6）：`test_orphan_detection.py` 补用例——`uploads/product/*.xlsx` 且不在 known_names → 被检出为孤儿。

**运行**：`pytest tests/test_phase_a_xlsx_upload.py -v` + 相邻回归 `test_ingest_main_flow.py` / `test_files_bindings_api.py` / `test_orphan_detection.py`。

## 五、文档更新

- 本 changelog 施工结果待施工后追加（本次「测试通过后再同步 README.md」由用户指定）。
- `README.md:211-215` 上传类型列表加一条：`📊 Excel (.xlsx) — 产品字段清单/需求矩阵（全工作表文本化后入库）`。
- `README.md:16` Phase A 概览「产品文档 (Word/PDF/MD)」补 Excel。
- `docs/fixes_summary.md` 归档追加（按 CLAUDE.md 约定，完成后由修复流程归档）。

## 六、波及面预登记（供施工后比对）

- 公式未缓存值（`data_only=True` 取不到）→ 提取为空 → 任务「文档内容为空」；属既有空文档语义，不额外处理。
- `uploaded-files` 列表/「产品文档」筛选按 `doc_type=product`，Excel 自动归类，无需改。
- 无 DB 迁移 / 无新目录 / 无新依赖。
- 若施工核对发现清单外硬编码点（原则③）→ 不擅自扩大，汇总回本方案提醒决策。

## 七、施工结果（2026-09-03，方案已全部落地）

### 施工中发现并补全的方案差距（原则③汇总）

1. **孤儿扫描规格缺 `.xlsx`**（`web/app.py _scan_orphan_files._scan_specs` 按扩展名扫，
   含 product 目录的 pdf/docx 但无 xlsx）→ 已通过 AskUserQuestion 用户决策**一并补上**
   （改动 6，见上），并补孤儿 xlsx 检出测试。
2. **实现细节（非决策点）**：纯 date 单元格经 openpyxl `data_only=True` 读回为午夜
   datetime → 文本化为 `2026-09-03 00:00:00`（`_cell_to_text` 的 date 分支只在外部
   文件含 time-only 值时触发，测试断言已按此校准）。

### 代码变更清单（全部已落地）

| 文件 | 变更 |
|------|------|
| `ingest/extractors.py` | `_extract_text` 增 `.xlsx` 分支；新增 `_extract_xlsx`（全 sheet 行式、`read_only+data_only`、`try/finally wb.close()`）与 `_cell_to_text`（None/date/time 处理） |
| `web/routes/files.py` | `type_map` 增 `".xlsx": "product"`（上传白名单） |
| `web/tasks.py` | `source` 名称映射补 `.xlsx → "Excel 文档"`（进度文案） |
| `templates/index.html` | 产品文档卡 hint「PDF / DOCX / Excel」+ `accept=".pdf,.docx,.xlsx"` |
| `database/models.py` | `Document` docstring/`file_type` 注释补 xlsx（纯注释，无迁移） |
| `web/app.py` | `_scan_orphan_files._scan_specs` 增 `("uploads/product", ".xlsx")`（改动 6） |
| `tests/test_orphan_detection.py` | 补 `test_product_xlsx_scanned` |
| `tests/test_phase_a_xlsx_upload.py` | **新增**（提取单测 5 + 全链入库 1 + 上传路由 2） |

### 测试结果

- 新增相关：`test_phase_a_xlsx_upload.py + test_orphan_detection.py` → **18 passed**。
- 相邻回归：`test_ingest_main_flow.py + test_files_bindings_api.py +
  test_regression_import_smoke.py + test_delete_file.py` → **70 passed**，无新增失败。
- 范围守卫确认：`.xls` 上传 400「不支持的文件类型」、`.xls` 提取仍抛「不支持的文件类型」。

### README 同步（按用户决策：测试通过后执行）

于测试全部通过后同步 `README.md` 两处（见下「五、文档更新」对照）。
