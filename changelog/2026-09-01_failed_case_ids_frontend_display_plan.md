# 2026-09-01 失败用例展示到前端 + URL 误报修复（方案待用户确认）

> **状态：用户已决策（2026-09-02），方案定稿，开始施工。** 遵循原则：确定方案先确认 / 最小改动 / 只改相关部分 / 方案设计测试用例改完测试 / 先更新文档再看实际情况。

> **用户决策：**
> 1. URL 误报**只修 `<script>` HTML 标签**误报；枚举值 `sharp/peak/flat/valley` 误报**不修**（保留现状）。
> 2. 失败详情（标题/原因）**展示在聊天框 reply 里，不建独立区块**。

## 一、问题背景

生成智慧用电_39 时，Phase B 校验后 **3 行未通过校验**：

```
📄 Excel 已保存: ... (22条/1模块, 4共享前置)（3 行未通过校验，需人工审查）
```

用户反馈两个问题：
1. **前端页面只显示失败数量，未标识是哪几条用例失败**（无法分出来是哪几条、为什么失败）
2. （连带发现）这 3 条失败其实是 **URL 校验器误报**——并非真拼写错误

## 二、失败用例明细（thinking_trace.log 已记录）

| 失败 ID | 标题 | 误报原因 |
|--------|------|---------|
| PRE-002 | 已创建分时测试电表 | 步骤含 `initDetailList 传分时明细，明细类型覆盖枚举 sharp/peak/flat/valley` → 校验器把 `sharp/peak/flat/valley` 当 URL → 误判 `/peak/flat/valley` |
| TC-002 | 电表管理-添加电表-分时电表-正向 | 同上，步骤含 `sharp/peak/flat/valley` 枚举 → 误判 `/peak/flat/valley` |
| TC-018 | 电表管理-添加电表-名称特殊字符-反向 | 步骤含 `name 传 "<script>alert(1)</script>"` → 校验器把 `/script` 当 URL → 误判 |

**根因**：`case_validator.py` 的 `_URL_RE = /[A-Za-z][A-Za-z0-9_/{}.-]*` 会匹配任意"以 / 开头 + 连续英文/斜杠"的串。枚举值 `sharp/peak/flat/valley` 和 HTML 标签 `<script>` 里的 `/script` 都命中此正则，被 `check_urls()` 判为疑似接口路径，未命中真实接口模板 → 误报 `invalid_url`。

## 三、改动方案

### 改动 1：URL 校验器 `<script>` 标签误报修复（`agent_components/validation/case_validator.py`）

**用户决策**：只修 `<script>` HTML 标签误报；枚举值 `sharp/peak/flat/valley` 误报不修。

**现状**：`_URL_RE = /[A-Za-z][A-Za-z0-9_/{}.-]*` 提取所有"斜杠+英文"串。`<script>alert(1)</script>` 中 `</script>` 的 `/script` 被提取为路径 → 未命中接口模板 → 误报。

**改法（最小）**：`_URL_RE` 增加负向后顾，排除紧跟 `<` 的 `/xxx`（即 HTML 结束标签）：
```python
_URL_RE = re.compile(r"(?<!<)/[A-Za-z][A-Za-z0-9_/{}.-]*")
```
`<script>` 中 `/script` 前是 `<` → 不再匹配；`</script>` 同理。真实接口路径（如 `/electricMeter/add`）前通常是空格/方法动词，不受影响。

**不改**：枚举值 `sharp/peak/flat/valley` 误报（用户决策保留现状，TC-002/PRE-002 仍会失败，靠前端展示详情提示人工审查）。

### 改动 2：失败详情（数量 + 编号 + 标题 + 原因）展示到前端 reply

**现状链路**：
- `nodes.py` L676 `fail_warn = f"（{len(failed_details)} 行未通过校验，需人工审查）"` —— **只有数量，无 ID**
- `web/tasks.py` L574-579：从 `thinking_trace.log` 正则提取 `TC-\d+` 得到 `failed_tc_ids`，放入 `thinking_parts` —— **只匹配 TC-，漏 PRE-**，且前端不渲染 thinking
- 前端 `app.js` L1200 只渲染 `result.reply` —— reply 由 `_build_phase_b_reply(result, case_count, fail_warn)` 生成，fail_warn 无 ID

**改法（最小）**：

1. **`nodes.py`**：`_generate_excel_plan_node` 返回结果里追加 `failed_case_ids` 与 `failed_case_details`：
   ```python
   _failed_cases = [
       {"id": d[1].get("id", ""), "title": str(d[1].get("title", "")), "errors": list(d[2])}
       for d in failed_details
   ]
   return {
       "excel_plan": plan, "excel_path": excel_path, "output_dir": output_dir,
       "failed_case_ids": sorted({c["id"] for c in _failed_cases if c["id"]}),
       "failed_case_details": _failed_cases,
       ...
   }
   ```
   （`failed_details` 元素结构：`(idx, case_dict, errs)`，`case_dict["id"]` 即用例编号含 PRE-/TC-，`errs` 为原因列表）

2. **`web/tasks.py`**：
   - `_build_phase_b_reply` 优先取 `result.get("failed_case_details")`，有则回复追加每条详情（编号 + 标题 + 原因）：
     ```python
     details = result.get("failed_case_details")
     if details:
         rows = "\n".join(
             f"  ⚠️ {d.get('id', '?')} {d.get('title', '')}: {'; '.join(d.get('errors') or [])}"
             for d in details)
         return f"{base}\n{len(details)} 行未通过校验，需人工审查：\n{rows}"
     ```
   - 无 `failed_case_details` 时回退现有 log 正则提取（正则扩为同时匹配 `PRE-\d+`）：
     ```python
     failed_tc_ids = list(set(re.findall(r"\| ((?:TC|PRE)-\d+) \|", content)))
     ```

3. **前端 `app.js`**：reply 已含编号 + 标题 + 原因，无需额外改动（**默认不加独立字段，保持最小**）。

### 改动 3：State 注解（防 LangGraph 静默丢弃）

`agent_components/graph/state.py` 追加：
```python
failed_case_ids: Optional[List[str]]    # 校验失败用例编号（generate_excel_plan 产出，供前端展示）
failed_case_details: Optional[List[dict]]  # 校验失败用例详情 [{id, title, errors}]，供 reply 展示标题/原因
```

> ⚠️ **必须加**：LangGraph 的 State channel 由 TypedDict 注解定义，未注解键会被静默丢弃（此前 `excel_plan` 断链同因）。若不加此注解，`nodes.py` 返回的 `failed_case_ids`/`failed_case_details` 在 graph.invoke 后消失，前端拿不到。

## 四、测试用例设计

**新增 `tests/test_workflow_init.py`（或独立 `tests/test_failed_case_ids.py`）：**

1. **State 注解存在**：`"failed_case_ids" in State.__annotations__`（防静默丢弃回归）
2. **`_build_phase_b_reply` 带 failed_case_ids → reply 含编号**：
   ```python
   r = _build_phase_b_reply({"failed_case_ids": ["PRE-002","TC-002","TC-018"]}, 22)
   assert "3 行" in r and "PRE-002" in r and "TC-018" in r
   ```
3. **`extract_url_paths` 误报回归**（如确认改动 1）：
   - `sharp/peak/flat/valley` 枚举 → 不提取
   - `<script>alert(1)</script>` → 不提取 `/script`
   - 真实接口 `/electricMeter/add`（或模板首段命中）→ 正常提取
4. **check_urls 合法用例不受影响**：构造真实接口 URL 的步骤 → 校验通过

**回归**：现有 `tests/test_workflow_init.py`、`tests/test_quality_gate.py` 全部保持通过。

## 五、文档更新

- 本文档（changelog）随方案确认后追加"用户决策"与"施工结果"
- `docs/fixes_summary.md` 归档追加（按 CLAUDE.md 约定，Skill C 修复完成后追加记录）

## 六、待用户决策点（已决策，见头部）

1. **URL 误报过滤规则** → 已决策：仅修 `<script>` HTML 标签误报，枚举误报暂放。
2. **失败编号展示粒度** → 已决策：展示每条失败详情（标题/原因）在聊天框 reply 里，不建独立区块。

## 七、施工结果（2026-09-02）

### 施工中发现并补全的方案差距（原则③汇总）

changelog 原改动 2 只写了 `failed_case_ids`（数量+编号），但用户决策要求"每条失败详情（标题/原因）"。仅编号无法在 reply 展示标题/原因。**补全**：nodes.py 额外透传 `failed_case_details`（含 `{id, title, errors}`），state 注解同步补全（改动 2/3 已更新为上文的完整版）。方向由用户决策指明，非新增决策点。

### 代码变更清单（全部已落地）

| 文件 | 变更 |
|------|------|
| `agent_components/validation/case_validator.py` | `_URL_RE` 加负向后顾 `(?<!<)`，排除 HTML 结束标签 `/xxx`；枚举值误报保留 |
| `agent_components/graph/state.py` | 新增 `failed_case_ids` / `failed_case_details` 注解（防 LangGraph 静默丢弃） |
| `agent_components/graph/nodes.py` | `_generate_excel_plan_node` return 追加 `failed_case_ids` / `failed_case_details` |
| `web/tasks.py` | `_build_phase_b_reply` 优先用 `failed_case_details` 展示每条详情；log 兜底正则扩为 `(?:TC\|PRE)-\d+`，仅在无 details 时走 |

### 测试用例（已新增 + 结果）

**`tests/test_plan_validator.py` 追加**（纯逻辑，无外部依赖）：
- `TestExtractUrlPaths.test_excludes_html_end_tag_script` — `<script>alert(1)</script>` 不提取 `/script`
- `TestCheckUrls.test_html_script_tag_not_flagged` — check_urls 对 script 标签步骤返回空

**`tests/test_workflow_init.py` 追加** `TestFailedCaseDisplay`：
- `test_state_annotates_failed_case_ids` — State 注解存在（防静默丢弃回归）
- `test_phase_b_reply_with_failed_details` — reply 含编号+标题+原因
- `test_phase_b_reply_without_failed_details_falls_back` — 无 details 时沿用原 fail_warn

**结果**：`tests/test_plan_validator.py + tests/test_workflow_init.py + tests/test_quality_gate.py` → **52 passed**。运行时行为验证：枚举值仍提取 `/peak/flat/valley`（保留现状）、script 排除、真实接口 `/electricMeter/add` 正常。全量回归状态见下：

> 全量回归结果：`tests/` 全量 **693 passed / 9 failed**。9 个失败全部经 `git stash` 基线验证为**既有失败**（HEAD 下同样失败），与本次改动无关：
> - 6 个 `test_phase_bc_unit.py` 既有失败：YAML 单节点 prompt 断言（`"接口唯一性" in sys_text` 等）、`TestResolveApiDefs` SQL 读取断言
> - 3 个 `TestResolveApiDefs` 在**全量顺序**下失败、**单独运行通过**（3 passed）——测试间数据库状态依赖（前面测试污染 documents 表），非本次改动引入
>
> **本次改动引入的新失败：0**。本次相关测试（plan_validator + workflow_init + quality_gate）52 passed。
