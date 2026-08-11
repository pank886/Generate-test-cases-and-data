# Phase C 接口按需取源改造 — 执行方案

| 项目 | 内容 |
|:---|:---|
| 日期 | 2026-08-07 |
| 状态 | ✅ Step 1-6 + D5 + 清理项 + **单节点已实现、灰度通过并默认开启**（`YAML_SINGLE_NODE=True`，single 87/92 > two_stage 83/92）；置 False 可回退两段式 |
| 背景 | `2026-08-07_phase_c_flow.md` 讨论结论：Phase C 把全量接口快照 JSON 灌进每个用例 prompt（`_generate_one_yaml`），而 Phase B 早已对接口做瘦身（只喂 name/method/url/description）。接口多时 context 撑爆，2026-08-03 在 B 修过的病在 C 复发 |
| 目标 | ① 门禁保留且升级（SQLite api 文档非空，快照取消）；② prompt 只喂轻量索引；③ 接口详情按 url 从 SQLite 精准取；④ 查不到的接口 → 告警 + 该用例不生成 |
| 关联 | `2026-08-07_phase_c_flow_checklist.md`（P0/P1 之外的新改造）、`2026-08-07_phase_c_flow.md` 四/五节 |

---

## 一、方案总览

```
Phase A  commit_api_docs（url 入库统一规范化）          ← D1 新增
              │
              ▼
Phase B  test_plan.xlsx（人工校验 = 数据正确性兜底）
              │
              ▼
Phase C  Step0 _generate_dependency_map（thinking）→ dep_map（case_api_sequences 锚）
              │
              ▼
         每个 YAML 用例：
           前置拦截（代码）：case_api_sequences[case_id] 每个 url → normalize + get_by_url
               ├─ 查不到 → 该用例 skipped_api_missing（不跑 LLM）
               └─ 查到 → 取回该用例涉及接口的全量详情
           prompt 注入 = 该用例 api_sequence 锚 + 该用例接口详情（不灌整批索引）
           生成 data → 数量/url 按 api_sequence 下标对齐校验 → 不符进修复轮重新生成
           写盘：canonical url + 详情 + annotations → 校验 → 写盘
```

**核心原则不变（并收敛）**：SQLite（`documents.api_*` 结构化列）为接口详情**单一事实源**，也是轻量索引的**唯一来源**；**快照 `api_defs.json` 取消**——数据正确性由 Phase B 人工校验兜底，不保留第三套数据源（减少冗余，减少方法数与分歧面）。**接口选择的语义决策由上游 dep_map 单点做出，下游按 `case_api_sequences` 锚定执行，不重复选接口。**

---

## 二、决策记录

### D1 — Phase A 入库时统一规范化 url

**问题**：`commit_api_docs`（`ingest/pipelines.py:328`）把 LLM 提取的 `url` **原样**写入 `api_url`。LLM 输出可能带域名（`http://host/path`）也可能只有 path；而 `normalize_base_info`（`prompts/response_model.py:570-576`）只在 YAML 生成时才去域名。于是 SQLite 的 url 与 Excel 里 LLM 生成的 url **形态不一致**，查库必落空。

**决策**：在 `commit_api_docs` 写入前统一规范化 `api["url"]`：
- 去域名：`urlparse` 取 `path`；**去掉 query**（query 一律走 `testCase.params`，URL 保持纯路径，与 `validate_url_no_placeholder` 约定一致）；路径参数 `{param}` 保留字面量
- 去尾部斜杠，**保留 path 原始大小写**（2026-08-11 定稿：不统一小写——真实后端路径可能区分大小写，且现有 `normalize_base_info`/测试即保留大小写；与 `normalize_base_info` 同一套逻辑，抽公共函数避免两处漂移）
- **同步修改 `normalize_base_info`**（`response_model.py:570-576`）：复用同一 `normalize_api_url`，去 query 一并生效（原实现保留 query，与去 query 冲突，必须统一）——生成端与入库端同形，查库不落空
- 规范化作用于：SQLite `api_url`、`_build_api_search_text` 检索文本、`file_name`（`f"{method} {url}"`）、doc_id 派生——保证「检索返回 url == 库中 url == 索引 url」

> 存量数据不兼容：历史入库的带域名/未规范化 url 一律不支持，重新入库即规范化；`get_by_url` 只做规范化比对，不做原始值 fallback。

### D2 — 接口查不到 → 告警 + 该条数据用例均不生成

**决策**：`_generate_one_yaml` 生成 `TestData` 后，对每个 `step.baseInfo.url` 查库；任一步骤接口查不到 → **该 YAML 文件整体不生成**（不写盘、不进 repair 轮——repair 救不了不存在的接口），记告警（`_api_not_found_issues` 清单 + 日志），计入独立的 `skipped_api_missing` 计数（与 failed 区分，前端可读）。

**范围**：test_data.yaml、setup_*.yaml、teardown_*.yaml 一视同仁（setup/teardown 的 steps 是文本，LLM 生成的接口同样按 url 查库拦截）。

**门禁层次**（两级）：
| 层级 | 触发 | 动作 |
|:--|:--|:--|
| 整批阻断 | SQLite 无该模块 api 文档（投影空） | `_resolve_api_defs` 返回 None → 任务 failed（M8） |
| 单接口跳过 | 个别接口查不到 | 告警 + 该用例不生成，批次继续 |

**匹配策略（L3 定稿）**：`get_by_url` 精确规范化比对优先；失败后启动**段级模板回退**——查询 url 与库中模板按 `/` 分段逐段比对，库中 `{param}` 段为**通配**（可匹配任意值段），字面段必须相等：
- **恰好一个**模板完全吻合 → 命中（如 `/order/123` vs 库 `/order/{id}`）；
- **多个**模板吻合（歧义）→ **全部候选列出**（记 `_api_not_found_issues` + 日志）+ 该用例**不生成**（宁缺毋滥，绝不静默错配）。

### D3 — 取消 api_defs.json 快照，索引从 SQLite 投影（L0）

**问题**：快照由 Phase B 落盘（`nodes.py:603-612`），内容来自 ChromaDB 检索子集（`retrievers.py:491-528`），与 SQLite 全集**天然可能不一致**——索引里有、库里没有 → 查库落空。且数据正确性已由 Phase B 人工校验兜底，快照作为「数据传递凭证」价值重复。

**决策**：**取消快照落盘与读取**（不保留第三套数据源，减少冗余）：
- 索引 = Phase C 直接查 SQLite（模块 bound api 文档 → 轻量索引），索引⊆库**构造性成立**，MISS 只剩 LLM 形式漂移/编造；
- M8 门禁改判「SQLite 该模块 api 文档非空」；
- 前端 `/confirm-plan` 显式 `api_defs_json` 入参不再作数据源（门禁与索引全走 SQLite）。

### D4 — 接口锚定：上游 case_api_sequences 锚定 YAML 步骤（index-aligned）

**问题**：Step 0 `_generate_dependency_map`（thinking）已产出 `case_api_sequences`（每用例接口序列）、`story_pre_api_sequence`、`teardown_api_sequence`、`decision_map`（每步参数/断言骨架），但 `_generate_one_yaml` 完全不消费（落日志即弃，`2026-08-07_phase_c_flow_checklist.md:62` 点名的已知根因）——下游每个 YAML 又用 thinking 从零选 url，同一语义决策做两遍、可能不一致（WRONG 翻倍）。

**决策**：下游 YAML 步骤按 `case_api_sequences` **按下标对齐锚定**（`case_id` 为 join key，Excel row[5] `TC-xxx`）：
- 解析 `case_api_sequences[case_id]` → `[(步骤名, method, url)]`；`data[i]` ↔ `api_sequence[i]`；
- url 锚：`data[i].url`（规范化后）== `api_sequence[i].url`（规范化后），`{order_id}` vs `{id}` 类差异走 L3 唯一模板回退；
- 数量锚：该用例必须输出恰好 `len(api_sequence)` 步，不符 → 修复轮强制重打（不静默截断/补齐）；
- 顺序锚：只按下标对齐，不按步骤名/api_name 匹配（名字会漂移）。

**前置确定性拦截（核心收益）**：跑 LLM 前，代码对每 case 的 `api_sequence` 每个 url 做 normalize + `get_by_url`（含 L3）：查不到 → 该用例直接 `skipped_api_missing`（**不跑 LLM**）；查到 → 顺手取回该用例涉及接口的全量详情（按需取源，prompt 只注入这些）。

**校验与再生（定稿）**：**生成后校验**（数量 + 各步 url 按锚）→ 不通过 → **只修改错误位置**：`repair_ctx.anchor` 注入 `repair_yaml_data_prompt`，prompt 限定「只修标注的错误步骤 + 受其影响的下游 extract，其余步骤原样保留」→ 统一修复完成 → **校验通过即落盘**（到修复轮上限仍不过 → 计 failed、不写假文件）。

**现有修复轮无锚定兼容入口，需补两处**：
1. gen 按 `case_id` 自己从 dep_map 解析锚 → 循环仍传共享 dep_map，参数保持批级统一（不按任务携带 per-case 数据）；
2. `repair_ctx` 增 `anchor` 字段 + `repair_yaml_data_prompt` 增 anchor 注入（"第 i 步预期 url 是 X，你输出的是 Y"）；`format_yaml_data_prompt` 的 `api_definitions` 变量本身就是锚 + 详情 → 修复轮两段（repair thinking + format json）都看得到预期 url。

**edge：数量不符**时 index 对齐断裂、"错误位置"无法定位 → 该用例退化为整文件重打。

**再生位置 = A 定稿**：gen 内校验 + 抛异常 → 现有 except 捕获 → 修复轮重打。B（外挂后置校验，需回退已写盘文件再重排队）否决。

**索引注入规则**：**不把整批索引灌给 YAML LLM**——prompt 只注入「该用例 api_sequence 锚 + 该用例接口详情」；LLM 从索引做语义检索/按需取。轻量索引仍喂 Step 0 的 dep_map 生成。

**setup/teardown 锚定**：
- setup YAML 锚 `story_pre_api_sequence`，**不允许为空**（有前置必有序列，空 = 异常 raise）；
- teardown YAML 锚 `teardown_api_sequence`，**同样不允许为空**（2026-08-11 定稿：有前置必有其清理序列，空 = 异常 raise——"只要有用例，绝不接受空数据"）。

**url 由谁写：A1 定稿**——LLM 写 url + 代码校验（TestData 契约不变；`baseInfo` 仅 api_name/url/method/header 四字段，url 禁 `${}`、`{param}` 仅 has_path_params 放行）。A2（代码填 canonical url）否决：覆盖发生在校验之后，LLM 填错反而先触发修复轮，负收益。

### D5 — dep_map 覆盖校验 + 定向修复（补漏 case）

**问题**：dep_map 的 `case_api_sequences` 可能漏掉 Excel 中的某些 case（LLM 覆盖不全）→ 该 case 无锚。

**决策**：
- **第一轮生成保持 lean**：**不喂** `ModuleAnalysis` 分析——首轮输出量大（整模块 dep_map），加分析会挤占输出上下文，反而更易漏/截断。
- **覆盖校验（纯代码，O(N) 集合比对）**：dep_map 生成后，用 Excel case_id 集合 vs `case_api_sequences` 键集合，找出漏的 case。
- **定向修复（带分析入参）**：漏了 → 只为缺失 case 生成（小 prompt：缺失 case rows + 接口索引 + `ModuleAnalysis` 分析），输出小、加分析安全 → merge 进现有 dep_map → 再校验。
- 两次仍不全（极端）→ 剩余 case 走「dep_map 无锚」跳过（A 兜底）。

**实现（2026-08-11 已落地）**：
- 新增 `repair_dependency_map_prompt`（`extraction_prompts.py`）：结构同 `generate_dependency_map_prompt`，入参加 `repair_cases` + `analysis`，铁律"只补漏、三表 key 一致、不重复已生成用例"。
- `_generate_dependency_map`（`excel.py`）加 `repair_cases=None, analysis=""` 参数；补漏模式过滤 rows、换 repair prompt、注入两参、日志标签区分。
- `_confirm_plan_bg`（`web/tasks.py`）：覆盖校验（`_find_missing_dep_map_cases`）→ 定向修复（`_load_module_analysis` 取 Phase B 分析）→ `_merge_dep_map` 合并写回。
- 测试：`TestDepMapCoverageAndMerge` 6 个（覆盖校验 / merge 新增·覆盖·新 story / repair prompt 格式化）。

---

## 三、执行步骤（按文件）

### Step 1 — 抽公共 url 规范化函数
- `normalize_api_url(url) -> str` 挂现有模块 `agent_components/api_annotations.py`（**不新增文件/类**，减少方法面）：与 `response_model.normalize_base_info` 共用同一套逻辑（去域名 + 去 query + 去尾斜杠，**保留大小写**），消除两处实现漂移。

### Step 2 — Phase A 入库规范化（D1）
- `ingest/pipelines.py:commit_api_docs`（328-379）：写入前对 `api["url"]` 调 `normalize_api_url`；`ingest/chunking.py:_build_api_search_text`（106）一并使用规范化结果。
- `ingest/pipelines.py:process_api_doc_extract`（234，合并去重在 310-322）/ `ingest/api_parser.py:extract_apis_from_yapi_md`（78）：提取产物在合并去重前规范化（`_merge_api_defs` 键 `method+url` 天然受益，url 一致后去重更准）。

### Step 3 — SQLite 查询层
- 新增 `database/operations/api_ops.py`：`ApiOps.get_by_url(session, method, url) -> dict | None`（`doc_type='api'`，规范化比对 + L3 唯一模板回退），返回 `{api_name, api_url, api_method, api_headers, api_parameters, api_returns, api_annotations}` 反序列化后的完整 dict。

### Step 4 — Phase C 门禁升级 + 索引投影（`_resolve_api_defs`）
- `web/tasks.py` `_resolve_api_defs`：M8 门禁升级——**取消 `api_defs.json` 快照**（读文件分支与显式入参数据源已删；`nodes.py:603-612` 落盘块一并删除，D3）；改为检查「SQLite 该模块 api 文档非空」；空 → 返回 None 阻断。
- 产出「轻量索引」`[{name, method, url}]`：**直接从 SQLite 投影**（`BindingOps.get_bound_docs` 模块作用域），与库构造性一致；全量详情不再跨节点传递。
- **模块名解析（已定）**：Excel **feature 列**（row[1]）——Phase B 写入的 `_feature = path_parts[-1] = confirmed_module`（`nodes.py:501-503`）；新增 `_read_excel_module` 助手。注意：`get_bound_docs` 不级联子模块，父模块绑定会空 → API 文档需绑定在叶子模块。

### Step 5 — `_generate_one_yaml` 按 `case_api_sequences` 锚定生成（D4）

> ⚠️ 定位修正：2026-08-07 已做**大文件拆分**，`generators/__init__.py` 现为组合层（仅 re-export），实现迁移至 `yaml_gen.py`（YamlMixin）/ `excel.py`（ExcelMixin）。以下均为拆分后定位。

- `web/tasks.py:265-288`：dep_map 产物**接入** `_generate_all_yamls`（当前只落日志即弃）——按 `excel_path` 目录 → 模块 → story → `case_id` 定位该用例的 `case_api_sequences`。
- **前置拦截（新增，`_generate_all_yamls` 内、跑 LLM 前）**：遍历每 case 的 `case_api_sequences[case_id]`，每个 url normalize + `get_by_url`（含 L3）→ 查不到 → `skipped_api_missing`（不跑 LLM）；查到 → 组装该用例「api_sequence 锚 + 接口详情」。
- `agent_components/generators/yaml_gen.py:31-178`（`_generate_one_yaml`）：
  - 入参：`api_defs_json` 替换为「该用例 api_sequence 锚 + 接口详情」；prompt 注入不再用整批索引。
  - 数量/url 按锚校验（写盘前）→ 不符抛校验异常 → `_run_yaml_rounds` 修复轮重新生成（再生位置待定，见 D4）。
  - `_lookup_api`（`yaml_gen.py:103`）内存匹配 → 改为 `ApiOps.get_by_url`（精确 → L3）；`_inject_annotations`（`yaml_gen.py:114`）改用查库返回的 `annotations`。
- **TestData 契约（现状，A1/A2 参照）**：`data[].baseInfo` 仅含 `api_name/url/method/header` 四字段；url 禁 `${}`、路径参数 `{param}` 仅 `has_path_params` 标注放行；params/json/data 只能进 testCase（`response_model.py:605-679`）。
- **L2 prompt 纪律**：`format_yaml_data_prompt` 增补（`extraction_prompts.py:280/282` 已有「与接口定义完全一致 / url 只写路径禁域名」）——锚定后仍要求 url 与 api_sequence 一致、不填路径参数实际值、不拼 query。
- `_run_yaml_rounds`（`yaml_gen.py:356`）：入参改为「锚 + 详情」；repair 轮同样注入锚 + 详情。
- setup/teardown：`story_pre_api_sequence`（不可空）锚 setup；`teardown_api_sequence`（空 = 无需清理）锚 teardown。

**实现要点（2026-08-11 已落地）**：
- 锚经 `row["_api_sequence"]` 附加（`_generate_all_yamls` 从 dep_map 按 story_name + case_id 解析）；gen 读 row 即得锚（首轮 + 修复轮一致），repair 锚经 `api_definitions` 文本注入——**未新增 repair_ctx.anchor 字段**（等效，减少改动）。
- 前置拦截 = `_generate_all_yamls` 内单 session 遍历 tasks，`get_by_url` 查不到 → skipped（func 无锚也跳过，reason=`dep_map 无锚`）。
- 生成后校验 = `_validate_against_anchor`（数量 + 各步 url 规范化比对）→ 不符抛异常 → `_run_yaml_rounds` except 捕获 → 修复轮。
- **edge（默认，可调）**：① func 无锚 → D5 覆盖校验 + 定向修复（带分析）先补，残留仍跳过（A）；② setup/teardown 锚为空（有前置时）→ **异常 raise**（2026-08-11 定稿：绝不接受空数据）；③ dep_map 整体缺失 → 所有 func 跳过。

### Step 6 — 前端消息与计数
- `web/tasks.py:334-356` 终态消息增加 `yaml_skipped`（接口缺失跳过数），与 failed 区分展示。

---

## 四、讨论项：C 路径双节点 → 单节点（thinking + json_mode）

### 现状（两段式）
```
调用1: analyze_yaml_data_prompt  free_text + thinking ON  → 自由文本分析（全文落 log）
调用2: format_yaml_data_prompt   json_mode + thinking OFF → 结构化 TestData
```
- 实现于 `_generate_one_yaml`（`yaml_gen.py:31-178`）。
- 第二阶段走 `_invoke_structured(method="json_mode")`，`METHOD_FEATURES`（`nodes.py:47-52`）把 `json_mode` 标为 `supports_thinking=False` → **即使传 thinking 也被强制关闭**。所以"思考"只能靠调用1，必须拆两次调用。
- 代价：每个 YAML 用例 2 次 LLM 调用。批次几十上百用例时，调用数翻倍、时延与成本双涨。

### 单节点方案
参考 Phase B 节点5（`nodes.py:150-154`，`generate_plan_thinking` 的 thinking+json_object bind）已验证的组合：
```python
_llm = self.llm.bind(temperature=0.4,
                     response_format={"type": "json_object"},
                     extra_body={"thinking": {"type": "enabled"}})
```
- **一次调用同时 thinking + json_object 输出**，deepseek-v4-flash 在 Phase B 已稳定运行（`generate_plan_thinking` 正是此形态）。
- 单节点作为**独立方法**手动 bind + `_invoke_think` + `json.loads` + `model_validate`，不经过 `_invoke_structured` 的 json_mode 路径 → 天然避开 METHOD_FEATURES 限制，**无需改 METHOD_FEATURES**。

### 利弊对比

| 维度 | 两段式（现状） | 单节点 |
|:--|:--|:--|
| LLM 调用次数 | 2×/用例 | 1×/用例 |
| context 注入 | 轻量索引后两边都小 | 同 |
| thinking 可见性 | 分析全文在 content → 落 `thinking_trace.log` | thinking 在 `reasoning_content`（模型照常产出分析），当前 `invoke_think` 只读 content（`llm_client.py:87`）不采集 → **补 reasoning_content 日志采集即可消除损失** |
| 失败重试 | 调用2 失败可复用调用1 分析 | 失败整次重来（thinking 重生成） |
| 修复轮 | repair 单次调用，天然单节点 | 一致 |
| 风险 | — | thinking 内容质量波动是否影响 json_object 字段正确性（Phase B 未现异常，需灰度验证） |

### 建议
**倾向单节点**，理由：
1. Phase B 节点5 已证明「thinking + json_object」组合在 deepseek-v4-flash 上可用且稳定，不是新试验；
2. YAML 是 Phase C 最大头、用例数量级大，省一半 LLM 调用收益显著；
3. 可观测性损失已消：thinking 在 `reasoning_content`，补日志采集（读 `result.reasoning_content` 写 `thinking_trace.log`，`invoke_think` 现只读 content，`llm_client.py:87`）即可，非结构性丢失。

**落地方式：新增单节点、开关路由、旧节点原样保留**（用户定稿）：

1. 新增 `YamlMixin._generate_one_yaml_single(self, row, api_defs_json, user_ctx, output_path, repair_ctx=None)`——**签名与 `_generate_one_yaml` 完全一致**，内部为「thinking + json_object」一次调用（手动 bind，参考 Phase B 节点5）。
2. `_run_yaml_rounds` **已内置 `gen_func` 注入参数**（`yaml_gen.py:378`：`gen = gen_func or self._generate_one_yaml`）——接缝点天然存在，**旧节点 `_generate_one_yaml` 一行不改**。
3. `_generate_all_yamls` 主轮（`yaml_gen.py:312`）与后校验轮（`yaml_gen.py:335`）调用 `_run_yaml_rounds` 时传 `gen_func=self._generate_one_yaml_single if config.YAML_SINGLE_NODE else None`。
4. 新增 `YAML_SINGLE_NODE` 配置开关（`settings.py` + `config.py`，默认 `False` = 两段式）；灰度置 `True` 对比修复率/通过率后再默认开启。
5. 旧节点两段式路径与 `analyze_yaml_data_prompt` 独立调用**完整保留**，作为回退。
6. **与 Step 5（轻量索引）正交**：新单节点签名天然接收 `api_defs_json`，轻量索引改造同时作用于新旧两节点。

**定稿（2026-08-10）**：单节点落地。每 YAML 文件 **1 次 LLM 调用**（thinking + json_object）；修复轮**只重跑失败项** → 调用量 ≈ 文件数 + 失败修复数（100 文件仅 1 失败 ≈ 101 次），仅批量全错才近似翻倍。前置：`reasoning_content` 日志采集。

**实现（2026-08-11 已落地，待灰度）**：
- `settings.py`/`config.py`：`YAML_SINGLE_NODE`（默认 `False` = 两段式）。
- `llm_client.invoke_think` 加 `reasoning_label`：采集 `result.reasoning_content` 落 `thinking_trace.log`；`nodes._invoke_think` 转发该参。
- `yaml_gen.py`：`YAML_ANALYSIS_GUIDE` + `_generate_one_yaml_single`（签名/锚定/校验/写盘与旧节点一致，thinking+json_object 一次调用，repair 上下文拼进 `data_analysis`）。
- `_generate_all_yamls` 主轮 + 后校验轮按 `config.YAML_SINGLE_NODE` 传 `gen_func`（默认 None = 旧节点，旧路径零改动）。
- 测试：`TestSingleNodeReasoning`（reasoning 采集 / 无采集不采 / guide / 签名契约一致）。
- **灰度结果（2026-08-12，智慧用电 92 用例）**：
  - two_stage: 首轮 77/92 → 修复 6/15 → **83/92**；2 次 LLM 调用/文件。
  - single: 首轮 84/92 → 修复 3/8 → **87/92**；1 次 LLM 调用/文件。
  - **结论：single 更优（通过率 + 失败更少 + 调用省半），建议默认开启 `YAML_SINGLE_NODE=True`。**

**灰度中发现并修复的 3 个 bug**：
1. `invoke_structured` json_mode 的 `pre_validate` 被 chain 内部校验跳过（`_annotations` 永不注入 → `{code}` 路径参数被 `validate_url_no_placeholder` 拦截）→ 修复：json_mode 不绑 pydantic，`pre_validate` + `model_class(**result)` 统一处理。
2. `_parse_api_sequence` 只认「步骤名:METHOD /url」；json_schema 化后 LLM 输出「METHOD /url」（无前缀）→ 解析空 → 前置拦截全跳 → 修复：兼容两种格式 + dep_map prompt 加格式铁律。
3. dep_map 的 `story_pre/teardown_api_sequence` 为空（有前置的 story）→ A+B 修复（D5 story 级定向修复 + 首轮 prompt 强约束「有前置必有其序列」）。

### 单节点字段契约（定稿，待调整）

#### ① 接收字段（gen_func 契约锁死，与旧节点逐字一致）
```python
def _generate_one_yaml_single(self, row: dict, api_defs_json: str, user_ctx: str,
                              output_path: str, repair_ctx: dict | None = None) -> str
```
| 字段 | 来源 | 说明 |
|:--|:--|:--|
| `row` | Excel 行 | 派生 `test_case_logic`（steps/expected）、`case_label` |
| `api_defs_json` | 门禁后 | Step 5 后为轻量索引 `[{name,method,url}]` |
| `user_ctx` | 前端 | 用户意图 |
| `output_path` | 目录规划 | 目标 yaml 路径 |
| `repair_ctx` | 修复轮 | `None`=首轮；非空 `{prior_output, error_detail, error_pattern_summary, round_no, post_check_issues}` |

内部仍读 `config.DB_SCHEMA` + `self._load_factory_methods()`，与旧节点完全对称。

#### ② 产出字段（共享旧节点写盘管线，`yaml_gen.py:144-177`）
- 产出单个 `test_data.yaml`，返回 `output_path`；运行时消费零影响。
- 后处理 100% 复用：url 路径参数注入 → `_takeover_export_assertions` → `model_dump(exclude_none, by_alias)` → 清 `_annotations` → `yaml.dump` → tmp → `os.replace`。
- **中间产物变化**：无独立分析文本，thinking 走 `reasoning_content`，content 直接是 TestData JSON。

#### ③ json_mode 方式（手动 bind，绕开 METHOD_FEATURES）
```python
llm = self.llm.bind(temperature=0.4,
                    response_format={"type": "json_object"},
                    extra_body={"thinking": {"type": "enabled"}})
raw = self._invoke_think(llm, prompt.format_messages(**vars), label="generate_yaml_data")
parsed = json.loads(raw)   # _invoke_think 已兜底空 content 重试
```
- 用 `json_object`（Phase B 节点5 同款，deepseek-v4-flash 已验证）；不用 `json_schema`（thinking+json_schema 未验证，V2 再议）。
- 不走 `_invoke_structured(json_mode)`（METHOD_FEATURES 强制 thinking off）。

#### ④ pydantic 校验
```python
parsed = _inject_annotations(parsed)          # pre_validate：注入 _annotations + is_export 占位断言（复用 yaml_gen.py:114）
set_db_schema_empty(not bool(db_schema))
result = TestData.model_validate(parsed)       # 失败抛 ValidationError → _run_yaml_rounds 登记 → 修复轮
```
- 语义与旧节点阶段2 一致（`max_retries=0`：不在调用内重试，靠修复轮），不引入双重重试。

#### ⑤ prompt 复用（不新增 prompt，用户定稿）
**只复用 `format_yaml_data_prompt`**——它是唯一"直接输出 TestData JSON"的旧 prompt；`analyze`/`repair` 的 system 明说"不要输出 JSON"，绑 `json_object` 会与提示词冲突，单节点不经过它们。

`data_analysis` 变量充当"引导 + 错误上下文"通道：

```python
# 代码常量（非 prompt 文件）：浓缩 analyze 的 5 条分析要点，引导模型在 thinking 里分析
YAML_ANALYSIS_GUIDE = (
    "请先在思考中完成以下分析，再严格按本 prompt 的 JSON 结构输出：\n"
    "1. 接口匹配：每个步骤对应哪个接口（url/method 与接口定义一致）\n"
    "2. 请求参数：来源（用例指定/上游提取/工厂方法）\n"
    "3. 数据传递：哪些返回值需要 extract 供下游引用\n"
    "4. 断言设计：断言字段与期望值\n"
    "5. 动态值：用哪个工厂函数，还是固定字面量"
)

# 首轮
data_analysis = YAML_ANALYSIS_GUIDE

# 修复轮：引导 + 错误上下文拼进同一通道
data_analysis = (
    YAML_ANALYSIS_GUIDE
    + f"\n\n### 你上一轮的输出（有错）\n{repair_ctx['prior_output']}"
    + f"\n### 校验错误明细\n{repair_ctx['error_detail']}"
    + f"\n### 全批次错误模式\n{repair_ctx['error_pattern_summary']}"
    + f"\n### 后校验问题\n{repair_ctx['post_check_issues']}"
)
```

- 三个旧 prompt 原样保留，旧节点继续两段式；单节点只经过 format。
- **已知代价**：修复轮 `data_analysis` 变长（引导+错误上下文），可能稀释 format 注意力；旧节点的 analyze/repair 成为单节点路径死代码（保留给旧节点回退）。
- **备选**（修复率不足时再启用）：修复轮复用 `repair_yaml_data_prompt` 绑 json_object（变量齐全但有"不要 JSON"冲突，不推荐）。
- 待灰度验证项：`data_analysis` 拼接长度对修复轮通过率的影响。

---

## 五、验证方法

| 项 | 验证 |
|:--|:--|
| D1 url 规范化 | 入库带域名/带 query url → 库中为纯 path（无 query）；检索返回 url 与库中一致 |
| D2 查库 | 造接口缺失：单接口缺失 → 该 YAML 不生成 + `skipped_api_missing`+1 + 告警；SQLite 无该模块 api 文档 → 门禁阻断任务 failed |
| L3 匹配回退 | 造 `/order/123`：库唯一模板 `/order/{id}` → 命中注入；库另有 `/order/{orderId}` → 跳过 + `_api_not_found_issues` |
| L0 快照取消 | 无 `api_defs.json` 落盘；`_resolve_api_defs` 不再读快照文件/显式参，全走 SQLite |
| A 锚定对齐 | 造 `case_api_sequences=2` 条 → 断言 YAML data 恰好 2 步、`data[i].url`==序列[i].url（规范化后） |
| 前置拦截 | api_sequence 某 url 库中不存在 → 该用例 `skipped_api_missing`（不跑 LLM） |
| setup/teardown | `story_pre_api_sequence` 空且该 story 有前置 → 校验拦截；`teardown_api_sequence=[]` → 生成最小/无清理 teardown |
| Step 5 context | 断言 YAML prompt 注入 ≤ 该用例接口详情体积（无整批索引/parameters/returns 冗余） |
| 回归 | 正常用例：生成 url 与库 url 匹配 → 详情注入 → 与改造前 YAML 产物 diff 一致 |
| 单节点 | `YAML_SINGLE_NODE` 开/关各跑一批（开关只切 `gen_func`，两节点并存）：断言关=走 `_generate_one_yaml`、开=走 `_generate_one_yaml_single`（mock gen_func 计数）；对比修复率、通过率、`thinking_trace.log` 可读性 |

---

## 六、待办顺序

1. Step 1（规范化函数）→ Step 2（A 入库）→ Step 3（查询层）→ Step 4（门禁：快照取消 + SQLite 投影）→ Step 5（C 取源 + dep_map 锚定接入 + 前置拦截 + L2 纪律）→ Step 6（消息计数）
2. 单节点改造（第四节）在锚定/取源落地后单独灰度
3. 已定（D4 / 第四节）：再生位置 = A（生成后校验，只修错误位置，通过即落盘）；单节点落地（每文件 1 次调用，修复轮只重跑失败项，含 `reasoning_content` 日志采集）
