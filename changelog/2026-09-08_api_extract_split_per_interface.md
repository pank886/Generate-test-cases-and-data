# 2026-09-08 LLM 接口提取切批改「按接口切」（marker-less 分层 md）

> **状态：已施工并验证（2026-09-08）。** 用户决策（AskUserQuestion 09-08）：`llm改成按接口切`。
> 触发文件：`D:\0-导出文件\0907-测试接口api(1).md`。原则：①先确认②最小改动③连带项汇总④方案含测试改完跑⑤文档先行。

## 一、背景与问题

`0907-测试接口api(1).md` 结构：`# 文档标题`(1) → `## 模块(Controller)`(5) → `### 接口`(28)，
每接口正文为 `` `路径` `` + `请求方式：xx` + 内联 JSON 示例。**无任何 YApi/clean 提取标记**。

- 实测 LLM 提取（`process_api_doc_extract`）5 个批次中 **2 批首抽触发
  `LengthFinishReasonError`（completion_tokens=8192 截断、JSON 解析失败）**，靠第 2 次重试兜底；
  再大些/重试仍超限则该批 3 次全败 → 整次导入 500。
- 根因：`_detect_api_level`（`ingest/api_parser.py:291`）只按「有 YApi 标记→2 / 有 clean 标记→1 / 其余→2」
  判定，**不认识无标记但接口藏在 `##` 模块下层的 `###`** → 把 0907 切成**模块级 5 批**而非接口级 28 批；
  大模块（如 saveOrUpdate 整段）一次要吐整模块 JSON → 超 8192 输出 token。
- 连带：切到接口级后，prompt system「提取其中那一个接口」与「1 批=1 接口」重新一致（无需改 prompt 文本）。

## 二、方案（最小改动）

只改 `ingest/api_parser.py` 的 `_detect_api_level` **无标记 else 分支**：

- **有标记分支不变**（保持历史判定，避免回归）：
  - `has_yapi`（`**Path：**`/`**Method：**`）→ 2
  - `has_clean`（`**接口路径**`/`**接口名称**`）→ 1
- **无标记分支**（现直接返回 2 → 改为）：
  统计 1~4 级各标题层切出的段落中「含接口端点签名」的段落数，取**最多**的层级；
  全零（无端点线索）→ 回退 2（历史默认）。
  端点签名 = `**Path/Method：**`、`**接口路径/名称**：`、反引号路径行 `` `/xxx` ``、`请求方式/请求方法`。

**判别校验（预实现，已跑）**：
| 样本 | L1 | L2 | L3 | 选中 |
|------|----|----|----|------|
| 0907（无标记 loose） | 1/1 | 5/5 | **28/28** | 3 ✓ |
| YApi-like（有标记） | — | 走 has_yapi 分支 | — | 2 ✓ |
| Bug1 合成（无标记平铺） | 0 | 0 | 0 | 回退 2 ✓ |
| GYM clean-like（有标记） | — | 走 has_clean 分支 | — | 1 ✓ |

**不改**：外层 `pipelines.py:259` 的 `len(full_text)>MAX_INGEST_CHARS_PER_BATCH` 触发门槛（≤30k 的单文件仍整篇 1 批——同属 8192 溢出的潜在面，但非本次触发场景，登记为后续可选项）。

## 三、波及面 / 未覆盖（原则③汇总）

1. `_detect_api_level` 同时被**代码提取器** `_split_clean_api_sections`（`extract_apis_from_yapi_md`）复用。
   YApi/clean 走原分支 → 不变；无标记 0907 走代码提取**仍 0**（解析器正文仍需 `**Path/Method/接口路径**`
   标记提取 url/method，与切分层级无关）——即代码提取对 0907 维持现状，本方案只修 LLM 路径。
2. 切接口级后批数变多（28 批并发 5），DeepSeek 调用次数增加、单批变小；module 仍取「首批返回名」，
   多模块单文件依旧只会标到一个 module 下（本次 0907 含 5 个 controller，属既有「每文件一模块」设计，
   不在本方案）。
3. `test_api_format_new.py`（GYM clean 代码提取）与 `test_regression_extraction.py` Bug1 系列为相邻回归，
   必须保持全绿。

## 四、测试设计

新增到 `tests/test_regression_extraction.py`（Bug1 同类切分话题）：
1. **marker-less 分层**（镜像 0907：`#` 标题 + `##` 模块 + `###` 接口 + 反引号路径）：
   `_detect_api_level` 返回 3；`_split_text_by_headers` 段数 = `###` 数，且每段含各自 `###` 标题。
2. **YApi 无回归**：含 `**Path：**` 样本 → `_detect_api_level`==2（沿用既有 Bug1 段数断言）。
3. **无标记平铺回退**：`#`+`##` 无标记合成 → 返回 2（既有 Bug1 test_only_splits_on_h2 覆盖段数，补显式断言）。
4. **真实文件 smoke（可选）**：读 `0907-测试接口api(1).md`（缺失则 skip）→ 28 段。

## 五、文档顺序

changelog 先行（本文件）→ 改代码 → 跑测试全绿 → 无用户可见 README 变更，不更新 README。

---

## 六、实施记录（2026-09-08）

| 位置 | 改动 |
|------|------|
| `ingest/api_parser.py` | `_detect_api_level` 拆分：`has_yapi→2` / `has_clean→1` 分支原样保留；无标记 else 分支改为「统计 1~4 级标题层含端点签名段落数取最大、全零回退 2」。新增常量 `_ENDPOINT_SIGN_RE` 与辅助 `_count_endpoint_sections` |
| `tests/test_regression_extraction.py` | +类 `TestDetectApiLevelLayeredNoMarker` 5 用例：marker-less 分层判 3 且按 `###` 切 4 段不互相污染、YApi 仍判 2、无标记平铺回退 2、真实 0907 文件切 28 段（缺文件 skip） |

### 验证结果

- 新 5 用例全绿；`tests/test_regression_extraction.py` + `tests/test_api_format_new.py`（GYM clean 代码提取）
  **44 passed / 3 skipped**（skip = 缺真实 md 的 slow 用例）。
- 0907 实测：`_detect_api_level`→3，切 **28 批**，最大批 3324 字符（原模块级 5 批最大 12100）→
  单接口 JSON 输出远低于 DeepSeek 8192 输出 token 上限，`LengthFinishReasonError` 崩溃类消除。
- 修复前离线实测：5 批中 2 批首抽触发 8192 溢出（LengthFinishReasonError）靠重试兜底、最终 28/28 成功；
  修复后每批 1 接口无此风险。

### 生效前提（重要）

运行中的服务进程（16:20 重启、含上轮花括号修复）**不含本改动**——api_parser.py 是本轮新改。
**需再次重启服务**后，LLM 提取对 0907 这类文档才按接口切批。

### 未覆盖 / 后续可选项（同第三节）

- 代码提取器对 0907 格式仍返回 0（正文解析需 `**Path/Method/接口路径**` 标记，属另一层解析器扩展，另议）。
- 单文件多模块仍只标一个 module（首批返回名），既有「每文件一模块」设计。
- ≤30k 且多接口的文档仍整篇 1 批（外层门槛 `MAX_INGEST_CHARS_PER_BATCH` 未动）——同属 8192 溢出的潜在面，需另议。
