# Phase C 接口按需取源改造 — 执行方案

| 项目 | 内容 |
|:---|:---|
| 日期 | 2026-08-07 |
| 状态 | 📝 方案 + 单节点落地方式定稿（待实施） |
| 背景 | `2026-08-07_phase_c_flow.md` 讨论结论：Phase C 把全量接口快照 JSON 灌进每个用例 prompt（`_generate_one_yaml`），而 Phase B 早已对接口做瘦身（只喂 name/method/url/description）。接口多时 context 撑爆，2026-08-03 在 B 修过的病在 C 复发 |
| 目标 | ① 门禁保留且升级（快照完整性校验）；② prompt 只喂轻量索引；③ 接口详情按 url 从 SQLite 精准取；④ 查不到的接口 → 告警 + 该用例不生成 |
| 关联 | `2026-08-07_phase_c_flow_checklist.md`（P0/P1 之外的新改造）、`2026-08-07_phase_c_flow.md` 四/五节 |

---

## 一、方案总览

```
Phase A  commit_api_docs（url 入库统一规范化）          ← D1 新增
              │
              ▼
Phase B  api_defs.json 快照（method+url 全集 + 轻量字段）  ← 门禁载体
              │
              ▼
Phase C  _resolve_api_defs（M8 阻断 + 完整性校验）        ← 门禁升级
              │ 只产「轻量索引」[{name,method,url}]
              ▼
         每个 YAML 用例：
           阶段1/2 prompt 只注入轻量索引                ← context 瘦身
           生成 baseInfo.url 后 → ApiOps.get_by_url(url) ← D2 查库
               ├─ 查到 → 注入全量详情 + annotations → 校验 → 写盘
               └─ 查不到 → 告警 + 该 YAML 不生成（不写盘、不修复）
```

**核心原则不变**：SQLite（`documents.api_*` 结构化列）为接口详情**单一事实源**；快照只做「契约/门禁 + 索引」传递，不再充当全量详情搬运工。

---

## 二、决策记录

### D1 — Phase A 入库时统一规范化 url

**问题**：`commit_api_docs`（`ingest_v2.py:1057`）把 LLM 提取的 `url` **原样**写入 `api_url`。LLM 输出可能带域名（`http://host/path`）也可能只有 path；而 `normalize_base_info`（`prompts/response_model.py:570-576`）只在 YAML 生成时才去域名。于是 SQLite/快照的 url 与 Excel 里 LLM 生成的 url **形态不一致**，查库必落空。

**决策**：在 `commit_api_docs` 写入前统一规范化 `api["url"]`：
- 去域名：`urlparse` 取 `path`（保留 query 可选，建议**去掉** query，路径参数 `{param}` 保留字面量）
- 去尾部斜杠、统一小写 path（与 `normalize_base_info` 同一套逻辑，抽公共函数避免两处漂移）
- 规范化作用于：SQLite `api_url`、`_build_api_search_text` 检索文本、`file_name`（`f"{method} {url}"`）、doc_id 派生——保证「检索返回 url == 库中 url == 快照 url」

**存量数据**：已入库旧 url 可能带域名 → `ApiOps.get_by_url` 查询时先规范化比对，失败再 fallback 原始值比对（兼容期），新数据全走规范化。

### D2 — 接口查不到 → 告警 + 该条数据用例均不生成

**决策**：`_generate_one_yaml` 生成 `TestData` 后，对每个 `step.baseInfo.url` 查库；任一步骤接口查不到 → **该 YAML 文件整体不生成**（不写盘、不进 repair 轮——repair 救不了不存在的接口），记告警（`_api_not_found_issues` 清单 + 日志），计入独立的 `skipped_api_missing` 计数（与 failed 区分，前端可读）。

**范围**：test_data.yaml、setup_*.yaml、teardown_*.yaml 一视同仁（setup/teardown 的 steps 是文本，LLM 生成的接口同样按 url 查库拦截）。

**门禁层次**（两级）：
| 层级 | 触发 | 动作 |
|:--|:--|:--|
| 整批阻断 | 快照为空 / 快照接口**全部**查不到 / SQLite 无任何 api 文档 | `_resolve_api_defs` 返回 None → 任务 failed（M8） |
| 单接口跳过 | 个别接口查不到 | 告警 + 该用例不生成，批次继续 |

---

## 三、执行步骤（按文件）

### Step 1 — 抽公共 url 规范化函数
- 新增 `agent_components/api_normalizer.py`（或挂 `api_annotations.py`）：`normalize_api_url(url) -> str`，与 `response_model.normalize_base_info` 的去域名逻辑共用，消除两处实现漂移。

### Step 2 — Phase A 入库规范化（D1）
- `ingest_v2.py:commit_api_docs`（1038-1057）：写入前对 `api["url"]` 调 `normalize_api_url`；`_build_api_search_text` 一并使用规范化结果。
- `process_api_doc_extract`（858）/ `extract_apis_from_yapi_md`（585）：提取产物在合并去重前规范化（`_merge_api_defs` 键 `method+url` 天然受益，url 一致后去重更准）。

### Step 3 — SQLite 查询层
- 新增 `database/operations/api_ops.py`：`ApiOps.get_by_url(session, method, url) -> dict | None`（`doc_type='api'`，规范化比对 + 原始值 fallback），返回 `{api_name, api_url, api_method, api_headers, api_parameters, api_returns, api_annotations}` 反序列化后的完整 dict。

### Step 4 — Phase C 门禁升级（`_resolve_api_defs`）
- `web/tasks.py:162-183`：保留 M8「文件非空」阻断；新增「整批完整性校验」——快照接口集合与 SQLite 存在集合比对，全部缺失 → 返回 None 阻断。
- 产出由「全量 JSON」改为「轻量索引」：`[{name, method, url}]` 传入后续步骤（与 Phase B `api_summaries` 同规格）。全量详情不再跨节点传递。

### Step 5 — `_generate_one_yaml` 按需取源（D2）

> ⚠️ 定位修正：2026-08-07 已做**大文件拆分**，`generators/__init__.py` 现为组合层（仅 re-export），实现迁移至 `yaml_gen.py`（YamlMixin）/ `excel.py`（ExcelMixin）。以下均为拆分后定位。

- `agent_components/generators/yaml_gen.py:31-178`（`_generate_one_yaml`）：
  - prompt 变量 `api_definitions` 由 `api_defs_json`（全量）→ `lightweight_index`（轻量）。
  - `_lookup_api`（`yaml_gen.py:103`）内存匹配 → 改为 `ApiOps.get_by_url` 查库；查不到抛 `ApiNotFound`。
  - `_inject_annotations`（`yaml_gen.py:114`）改用查库返回的 `annotations`；url 路径参数注入（写盘前段）不再依赖快照 `has_path_params`，改从查库详情取。
  - `ApiNotFound` → 上层捕获，登记 `_api_not_found_issues` + 跳过该文件。
- `_run_yaml_rounds`（`yaml_gen.py:356`）：`api_defs_json` 参数 → 轻量索引；repair 轮同样注入轻量索引（repair 聚焦格式修复，不需要全量详情）。
- `_generate_dependency_map`（`excel.py`，ExcelMixin）：`all_apis_info=api_defs_json` 同样改为轻量索引——dep_map 分析依赖关系只需要 method+url+name，全量 parameters 无必要。

### Step 6 — 前端消息与计数
- `web/tasks.py:335-357` 终态消息增加 `yaml_skipped`（接口缺失跳过数），与 failed 区分展示。

---

## 四、讨论项：C 路径双节点 → 单节点（thinking + json_mode）

### 现状（两段式）
```
调用1: analyze_yaml_data_prompt  free_text + thinking ON  → 自由文本分析（全文落 log）
调用2: format_yaml_data_prompt   json_mode + thinking OFF → 结构化 TestData
```
- 实现于 `_generate_one_yaml`（`yaml_gen.py:31-178`）。
- 第二阶段走 `_invoke_structured(method="json_mode")`，`METHOD_FEATURES`（`nodes.py:42-47`）把 `json_mode` 标为 `supports_thinking=False` → **即使传 thinking 也被强制关闭**。所以"思考"只能靠调用1，必须拆两次调用。
- 代价：每个 YAML 用例 2 次 LLM 调用。批次几十上百用例时，调用数翻倍、时延与成本双涨。

### 单节点方案
参考 Phase B 节点5（`nodes.py:174-177`）已验证的组合：
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
| thinking 可见性 | 分析全文落 `thinking_trace.log` | thinking 是 `reasoning_content`，**不进 content**，log 只能记最终 JSON → 分析过程不可读 |
| 失败重试 | 调用2 失败可复用调用1 分析 | 失败只能整次重来 |
| 修复轮 | repair 单次调用，天然单节点 | 一致 |
| 风险 | — | thinking 内容质量波动是否影响 json_object 字段正确性（Phase B 未现异常，需灰度验证） |

### 建议
**倾向单节点**，理由：
1. Phase B 节点5 已证明「thinking + json_object」组合在 deepseek-v4-flash 上可用且稳定，不是新试验；
2. YAML 是 Phase C 最大头、用例数量级大，省一半 LLM 调用收益显著；
3. 可观测性损失可控：`thinking_trace.log` 仍记录最终 JSON + 失败明细（`_generation_error_details.log`），缺失的是"分析草稿"而非结果。

**落地方式：新增单节点、开关路由、旧节点原样保留**（用户定稿）：

1. 新增 `YamlMixin._generate_one_yaml_single(self, row, api_defs_json, user_ctx, output_path, repair_ctx=None)`——**签名与 `_generate_one_yaml` 完全一致**，内部为「thinking + json_object」一次调用（手动 bind，参考 Phase B 节点5）。
2. `_run_yaml_rounds` **已内置 `gen_func` 注入参数**（`yaml_gen.py:378`：`gen = gen_func or self._generate_one_yaml`）——接缝点天然存在，**旧节点 `_generate_one_yaml` 一行不改**。
3. `_generate_all_yamls` 主轮（`yaml_gen.py:312`）与后校验轮（`yaml_gen.py:335`）调用 `_run_yaml_rounds` 时传 `gen_func=self._generate_one_yaml_single if config.YAML_SINGLE_NODE else None`。
4. 新增 `YAML_SINGLE_NODE` 配置开关（`settings.py` + `config.py`，默认 `False` = 两段式）；灰度置 `True` 对比修复率/通过率后再默认开启。
5. 旧节点两段式路径与 `analyze_yaml_data_prompt` 独立调用**完整保留**，作为回退。
6. **与 Step 5（轻量索引）正交**：新单节点签名天然接收 `api_defs_json`，轻量索引改造同时作用于新旧两节点。

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
| D1 url 规范化 | 入库带域名 url → 库中为 path；新旧检索返回 url 与库中一致 |
| D2 查库 | 造接口缺失：单接口缺失 → 该 YAML 不生成 + `skipped_api_missing`+1 + 告警；全缺失 → 门禁阻断任务 failed |
| Step 5 context | 构造 50 接口，断言 prompt 注入的 `api_definitions` 字段 ≤ 轻量索引体积（无 parameters/returns） |
| 回归 | 正常用例：生成 url 与库 url 匹配 → 详情注入 → 与改造前 YAML 产物 diff 一致 |
| 单节点 | `YAML_SINGLE_NODE` 开/关各跑一批（开关只切 `gen_func`，两节点并存）：断言关=走 `_generate_one_yaml`、开=走 `_generate_one_yaml_single`（mock gen_func 计数）；对比修复率、通过率、`thinking_trace.log` 可读性 |

---

## 六、待办顺序

1. Step 1（规范化函数）→ Step 2（A 入库）→ Step 3（查询层）→ Step 4（门禁）→ Step 5（C 取源）→ Step 6（消息计数）
2. 单节点改造（第四节）在轻量索引落地后单独灰度
3. 存量 url 兼容期 fallback 保留一个版本周期后清理
