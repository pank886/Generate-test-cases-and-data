# AxureParser 去掉 2000 字符截断 + 补齐切块

| 项目 | 内容 |
|:---|:---|
| 日期 | 2026-08-17 |
| 涉及文件 | `agent_components/axure_parser.py` |
| 触发场景 | 排查"智慧用电"原型必填项时发现必填信息丢失 |

---

## 一、问题

`agent_components/axure_parser.py::_clean_html_to_text` 中 `body[:2000]` 把每个 Axure 页面
清洗后的文本**截断到前 2000 字符**，导致页面后半段内容被静默丢弃：

- **需求注释规则**（顶层 HTML 里的 `必填字段：…`、字段联动逻辑等）——28 页中仅电表管理 1 页幸存
- **必填星号标签**（红色 `color:#D9001B` 的 `*` + 字段名）——企业公摊生成 7 个星号只进 4 个

## 二、根因

三条入库链路中，产品文档 / 接口文档都有真实切块
（`RecursiveCharacterTextSplitter` / `_split_text_by_headers`），
唯独 **Axure 链路每页一整块**（`to_product_doc_chunks` 1 页 = 1 chunk），
`body[:2000]` 成了唯一的体积控制手段——切块缺位 + 粗暴截断双重限制。

## 三、修复

1. `_clean_html_to_text`：删除 `body[:2000]` / `text[:2000]` 截断，返回完整 body 文本。
2. `to_product_doc_chunks`：引入与产品文档链路同规格的
   `RecursiveCharacterTextSplitter(chunk_size=config.CHUNK_SIZE, chunk_overlap=config.CHUNK_OVERLAP)`，
   超长页面按 `## 页面: xxx` 头部切分为多块，**每块重挂页面头 + page_name 元数据**，
   保证每块自含来源页信息、`_extract_page_name` / `page_name` 归属不丢。
3. 页数上限语义保留：最多处理 50 个页面（`all_pages[:50]`），不再对块总数二次截断。

## 四、验证（智慧用电.zip）

| 指标 | 修复前 | 修复后 |
|:---|:---|:---|
| ui_text 超 2000 字符的页面 | 10 页被截断 | 0 页截断 |
| 含 `必填/选填` 的页面 | 仅电表管理 | 电表管理、计费规则、企业公摊生成、企业公摊生成_1 |
| 企业公摊生成 星号数 | 4/7 | 11（全量） |
| 块总数 | 28 | 124（超长页拆分） |
| 块大小 | ≤2000 | ≤1031（头部开销，与产品文档链路同容差） |

回归测试：`tests/test_regression_axure_parser.py` + `tests/test_ingest_main_flow.py` 共 37 个用例全部通过。
