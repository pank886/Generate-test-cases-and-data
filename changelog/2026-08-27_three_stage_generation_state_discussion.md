# 2026-08-27 生成流程重构讨论：三阶段生成 + state 传递

> 本文件为**讨论稿**，记录方案可行性分析与待决策点。实现前需与用户逐条敲定。

## 一、触发背景（两个待办根因复核）

### 问题 1：引用键名不匹配（铁律 13 机制缺口）

**根因（代码级确认）**：`_generate_all_yamls`（`agent_components/generators/yaml_gen.py:317`）把 setup / teardown / test 全部 YAML **平铺进一个 `yaml_tasks` 列表**，由 `_run_yaml_rounds`（`yaml_gen.py:511`）**并发**生成（`_BoundedThreadPoolExecutor`，`yaml_gen.py:555`）。每次调用是独立 LLM 上下文，只注入**本文件**的 B 文本（`yaml_gen.py:262` `test_case_logic = row['steps']`）。

- setup 调用自拟 key：`pre003MeterCode`（thinking_trace 40152-40156）
- delete 用例把 B 文本字面值 `ELEC_BIND` 当 key：`get_extract_data('ELEC_BIND')`（thinking_trace 11675/19600/27995）

两次决策之间**无任何对齐机制**。铁律 13 只约束「用 get_extract_data 引用」的语法，不约束 key 名。

### 问题 4：teardown 生成成 add（非 delete）

**根因修正（代码级确认）**：teardown 文本是**代码拼接**的，非用户数据问题。`yaml_gen.py:404-407`：

```python
teardown_lines.append(
    f"# 清理 {pid}: {pre['name']}\n"
    f"根据 {pid} 的创建步骤逆向操作：{pre['steps'][:200]}"   # ← pre['steps'] 本身是 add 步骤
)
```

LLM 收到「清理/逆向操作 + add 步骤原文」的自相矛盾 prompt，thinking 里明确识别出「inverse of create is delete」（thinking_trace 9610/9788），最终选择「信任 B 字面步骤，输出 adds」（9796）。**修复点在代码层 teardown 构造**，不在用户 excel 数据。

**历史先例**：teardown 拼接大量 PRE 步骤导致输出超 `completion_tokens=8192`（`GEN-FAIL-R2-007`，见 changelog `2026-08-05_execution_failure_26_optimization.md` §3.4）——上下文超限问题**已真实发生过**。

### 复核新发现：capture 错放

生成的 `setup_meter_management.yaml` 四个 add 块 **`input_extract=[]`（未捕获创建 code）**；`teardown_meter_management.yaml` 反而每个块带 `input_extract: {pre00xMeterCode: $.json.code}`。

即：本应捕获资源标识的 **setup 没捕获**，捕获被 LLM 放进了**执行顺序最后的 teardown**（其捕获对测试用例毫无用处，公共参数运行时只在 setup 之后、teardown 之前消费）。这加深了缺陷——当前生成态下，测试用例根本无法引用 setup 创建的资源。

## 二、用户方案

**问题 1**：
1. 生成中间文件或通过 state 传递参数
2. 分三阶段生成：setup → test → teardown，只在每个阶段内并发

**问题 4**：
1. phase B 只告诉「生成数据清理」，不给参考或其他任何内容
2. teardown 阶段根据 state 参数生成删除数据用例

**约束**：State 字典只接收键值对。
**担忧**：一次生成上百个用例会超出上下文限制。

## 三、现状核实（支撑可行性判断的代码事实）

| 事实 | 证据 |
|---|---|
| 用例→setup 依赖已知 | excel Sheet1 `前置步骤` 列：TC-017→PRE-003（实测） |
| PRE→用例关联已知 | excel Sheet2 `共享前置` 关联用例列 |
| key 解析器已存在 | `tests/tools/_check_37_unique_keys.py` 第 4 步 `collect_extract_keys`（纯代码） |
| 跨阶段交接先例 | M8 规则：接口定义靠**产物传递**（api_defs.json 落盘），禁止依赖内存态（`agent_components/nodes.py:632-641`） |
| api_defs 规模实测 | 37 快照 828B / 4 接口（百接口项目需另评） |
| 生成入口现状 | `_generate_all_yamls` 平铺并发；`_generate_one_yaml_single` 单次调用注入 api_defs+guide+本文件 B 文本 |

## 四、可行性分析

### 4.1 设计可行性：高

三阶段 = 把 `yaml_tasks` 按类型切分，`_run_yaml_rounds` 复用 3 次（每阶段一轮次循环），阶段间 barrier + 产物落盘。**现有积木全部具备**：

1. **阶段内并发**：`_BoundedThreadPoolExecutor` 照用，仅需把全量列表换成阶段列表
2. **setup key 解析**：setup 阶段完成后，纯代码遍历生成的 setup YAML 的 `input_extract` 收集 key（复用 `collect_extract_keys` 逻辑）
3. **依赖过滤**：`preconditions` 列已给 case→PRE 映射，逐用例注入**仅其依赖的 PRE 的 key**
4. **teardown 语义**：B 内容改为「清理 PRE-xxx」（去掉 `pre['steps'][:200]` 拼接），注入本 class 的 setup key + delete 接口契约（已在 api_defs）
5. **中间文件**：`_setup_extract_keys.json`（M8 先例的产物交接）

### 4.2 上下文超限担忧：明确回答

**结论：100 用例总量不会超窗口——窗口限制在单次调用，由 `api_defs_json` + 输出长度决定，与用例数量无关。** 分阶段设计**减小**了上下文：

- 注入的 key 映射**按 preconditions 过滤**后每用例极小（TC-017 只需 PRE-003 几个 key，个位数 KV）
- teardown B 内容从「PRE 步骤原文拼接」降为「清理 PRE-xxx」，直接消除 `GEN-FAIL-R2-007` 超限风险
- 既有风险（与本次设计无关）：`api_defs_json` **全量注入每次调用**。37 实测仅 828B/4 接口；百接口项目需另立「按用例裁剪 api_defs」优化，不属于本方案范围，但应在规划时知晓

**红线**：key 注入**必须**按 preconditions 过滤。若全局注入全部 setup key 到每个用例，100 用例 × 50 setup × 5 key 才可能膨胀——过滤后不成立。

### 4.3 需一并解决的配套问题

**setup 必须捕获资源标识**。当前生成态 setup `input_extract=[]`，不捕获 code。分阶段设计的 key 解析建立在「setup 块带 input_extract」之上，因此需在 setup 生成时约束：**setup 每个创建块必须 `input_extract` 其资源标识（code）**（铁律或后校验）。否则 state 无物可传。

## 五、决策点（已敲定，2026-08-27 用户）

| # | 决策点 | 决定 |
|---|---|---|
| D1 | 交接机制 | **中间文件** `_setup_extract_keys.json`（M8 先例） |
| D2 | key 命名 | **自由命名** + 资源值前缀保留 B 文本、随机后缀仅保唯一性。解析器按块 case_name 关联 PRE（无需命名约定） |
| D3 | B 字面值→key 名映射 | **必做**：注入时给每个 key 标注字段语义（`pre003MeterCode ← code`），LLM 从「编码 ELEC_BIND」正确映射 |
| D4 | setup 失败兜底 | **B+C 混合（优雅降级）**：① Setup 后校验缺失 code 提取 → `Warning: PRE-003 missing 'code' extraction` 日志；② `_setup_extract_keys.json` 仍建条目，缺失 key 值= `__MISSING_KEY__`；③ Test 阶段 prompt 明示「该 key 提取失败=__MISSING_KEY__，用硬编码占位值或标注预期失败」；④ 后置校验扫描 Test YAML，`get_extract_data` 解析出 `__MISSING_KEY__` → 标 P1（人工复核），不丢弃 |
| D5 | teardown 删除顺序 | **由子到父删除**：先删子/先解绑绑定关系，再删父（纳入 teardown B 内容或铁律约束） |
| D6 | 修复轮 | **接受**三阶段串行墙钟（测试阶段仍并发主导） |

## 六、改动面预估（实现阶段）

1. `agent_components/generators/yaml_gen.py`：
   - `_generate_all_yamls` 拆 `setup_tasks` / `test_tasks` / `teardown_tasks`
   - 三阶段串行调用 `_run_yaml_rounds`，阶段间解析+写/读 `_setup_extract_keys.json`
   - `_generate_one_yaml_single` 支持注入 `setup_keys`（渲染进 prompt）
   - `yaml_gen.py:404-407` teardown 文本改为「清理 PRE-xxx」
2. `prompts/extraction_prompts.py`：setup 创建块必须 input_extract（铁律追加）
3. 后校验 `YamlPostValidator`：setup 块无 input_extract 判 P0/P1（与 D4 联动）

## 七、验收标准（讨论稿）

- 生成后：setup 块全部含 `input_extract`（code），key 可被下游引用
- delete_bound 用例引用 key ∈ setup 提取 key 集（静态检查第 4 步通过）
- teardown YAML 全为 `POST /delete`，body 引用 `${get_extract_data(...)}`
- 100 用例级批：单调用上下文可测（记录 max tokens / 调用）

## 八、并行方案：按用例裁剪 api_defs（用户重点讨论，2026-08-27）

### 8.1 问题

`api_defs_json`（模块作用域接口详情）**全量注入每次 YAML 调用**（`_generate_one_yaml_single` → `api_definitions`）。百接口项目模块作用域可达数十接口，每接口详情（body/return/header/annotations）1-5KB → 单调用 30-150KB，是**单调用上下文的主成本**。37 实测快照 828B/4 接口，问题未暴露。

### 8.2 方案：按 YAML 任务裁剪接口子集

每个 YAML 调用只注入**该任务真正需要的接口**：

| 阶段 | 裁剪源 |
|---|---|
| setup | PRE 步骤文本显式引用的接口路径（add 等） |
| test | 用例自身 steps/expected 显式引用的接口路径 |
| teardown | 被清理资源的「逆向接口」（delete） |

**路径提取**：regex 从步骤文本提取 `调用 POST /xxx` / `POST /xxx` / `park-energy-[\w/-]+` 路径 → api_defs path 后缀匹配 → 子集 = 命中的接口详情。

**兜底（安全优先）**：
1. 命中 0 个路径 → **回退模块作用域全量**（不饿死 LLM，宁全勿缺）
2. 路径规范化比较（去协议/域名/query，忽略大小写）
3. 裁剪是**保守超集**：只删「确定不用」的接口，绝不删「可能用」的

### 8.3 风险与缓解

| 风险 | 缓解 |
|---|---|
| 步骤文本未写全路径（「调用删除接口」无路径）→ 匹配 0 | 回退全量（保守） |
| 用例需「未明说」的辅助接口（getList 验证删除效果） | 命中的子集 + 可配置的「验证辅助接口」白名单；或裁剪后 prompt 明示可用接口全集 + 说明 |
| 路径写法和 api_defs 不一致 | 后缀/规范化匹配，宽松兜底 |
| 裁剪后 LLM 无接口可抄而瞎编 | 兜底全量优先于裁剪收益 |

### 8.4 与三阶段方案的交互

**正交，可组合**：三阶段管「跨文件 key 对齐」；裁剪管「单调用接口子集」。两者都减单调用上下文，互不依赖。test 阶段 = 自身接口子集 + 依赖 PRE 的 key 注解；teardown = delete 接口子集 + 本 class setup key。

### 8.5 小型实践范围（本次）

37（4 接口 / 20 用例）验证**机制正确性**而非规模收益：
1. 裁剪逻辑：delete 用例子集 = {delete, getList}（2/4），add 用例子集 = {add}，其余不注入
2. 度量：记录裁剪前后单调用 `api_definitions` 字符数，证明切片生效
3. 与三阶段组合跑通，生成不回归（对照 v5 基线 9 PASS / 11 FAIL，预期仅修复 delete_bound KeyError + teardown 变 delete）
4. 规模收益留给百接口项目验收（本方案只交付机制 + 度量点）

### 8.6 裁剪规则（已敲定，2026-08-27 用户）

- **写接口（POST/PUT/DELETE）**：白名单策略，**严格按步骤路径匹配裁剪**（动作主角）
- **读接口（GET）**：默认**保留**（验证辅助工具，返回结构固定、Token 小，降低 LLM 验证步骤幻觉）
- **关系图谱相关接口**：除上述两类外，按 `api_relations.json` 分析结构中与该用例接口**相关**的接口（`consumes_from` / `verify_with` / `cleanup_with` 目标）**也纳入**
- 裁剪作用范围：**三个阶段都裁**

## 九、接口关系图谱 api_relations.json（新方案 1，2026-08-27 讨论中）

### 9.1 结构（用户示例）

```json
{
  "POST /park-energy-electric-web/electricMeter/add": {
    "produces": ["code", "name"],
    "consumes_from": ["/payConfig/list", "/electricControlDevice/list", "/region/list"],
    "verify_with": ["/electricMeter/getList"],
    "cleanup_with": "/park-energy-electric-web/electricMeter/delete"
  },
  "POST /park-energy-electric-web/electricMeter/delete": {
    "consumes_from": ["/park-energy-electric-web/electricMeter/add"],
    "verify_with": ["/electricMeter/getList"]
  }
}
```

### 9.2 设计意图

裁剪后的 api_definitions 之外，注入一段**极短关系提示**（几十 Token，数据驱动的「上帝视角」），不让 LLM 靠看接口文档自己「悟」上下游：

```
### 接口联动提示（供参考）
- 涉及 /electricMeter/add 的用例，验证时可使用 /electricMeter/getList 确认数据落库。
- 涉及 /billing/bind 的用例，清理时必须调用 /billing/unbind。
```

### 9.3 与两个待办的统一价值（讨论重点）

关系图谱**把问题 1 和问题 4 都下沉到数据层解决**：

- **问题 4（teardown add 非 delete）**：`cleanup_with` 直接给出「创建接口的逆向接口」。teardown 不再「信任 B 字面 add 步骤」，而是**数据驱动**：PRE 创建接口 → `cleanup_with` → delete/unbind + D5 子到父顺序。彻底替代 `yaml_gen.py:404-407` 的机械拼接
- **问题 1（引用键不对齐）**：`produces`（code/name）标出创建接口产出的资源标识，`consumes_from`（POST /delete ← POST /add）标出消费关系。delete 用例拿到「delete 消费 add 的产出」→ 引用 setup 提取的 code key（配合 D3 注解）

### 9.4 现状核查与来源决策（2026-08-27 用户确认）

**断链核查（回答 data_analysis 与 dependency_map）**：
- `data_analysis`（YAML 生成注入）= `YAML_ANALYSIS_GUIDE`（yaml_gen.py:52-59）固定 5 点通用引导，**不含接口映射分析内容**
- 接口映射分析（`analyze_api_mapping_prompt`，Phase A Step 3，web/tasks.py:802）输出自由文本 `api_analysis`，存 DB `ModuleAnalysis.api_analysis`，只在 Phase B 检索时注入（retrievers.py:577），**从未进入 YAML 生成**——语义内容（produces/consumes）生成了但断链
- `generate_dependency_map_prompt`（extraction_prompts.py:419）= Phase C Step 0 死产物：仍被调用（web/tasks.py:380）但产物只日志消费（web/tasks.py:395-402 `dep_map` 从不传下游）、失败非致命

**来源决策（用户）**：
1. **1.a**：Step 3 同一次 LLM 调用**同时**输出自由文本 api_analysis + 结构化 `api_relations`（JSON）
2. **2**：存 **DB** `ModuleAnalysis.api_relations_json` 列，与 api_analysis **共享生命周期**（一起 upsert，接口变更时一起重析）
3. **停用 `_generate_dependency_map`**（用户待确认后执行）：其内容被取代——
   - `teardown_api_sequence` → api_relations.`cleanup_with`
   - `case_api_sequences` → 步骤文本路径正则（裁剪种子，确定性）
   - `story_pre_api_sequence` → api_relations.`produces/consumes_from` + 步骤解析
   - `decision_map` / `internal_dependency` → YAML_ANALYSIS_GUIDE 2/3 点已覆盖
   停用点：web/tasks.py:347-402（省每次 confirm-plan 的 LLM 成本 + 重试）

## 十、小型实践实现设计（拆分方案，确认稿，2026-08-27）

### 10.1 `_generate_all_yamls` 三列表拆分

现状：单循环构建 `yaml_tasks`（setup+teardown+test 混排）→ 单次 `_run_yaml_rounds`（yaml_gen.py:377-452）。

改：**同一循环分流三个列表**，row 带元数据（不新增 `_run_yaml_rounds` 签名，元数据随 row 走，repair 轮自动继承）：

```python
setup_tasks    # (row, setup_<slug>.yaml)      row['_pre_ids'] = {PRE-001,...}
teardown_tasks # (row, teardown_<slug>.yaml)   row['_pre_ids'] = {...}
test_tasks     # (row, test_*/test_data.yaml)  row['preconditions'] 已由 excel 列给出
```

### 10.2 三阶段串行编排（顺序已确认，2026-08-27）

> **顺序确认：setup 全部完成 → test 全部完成 → teardown 全部完成。阶段间严格 barrier。**
> 「完成」= `_run_yaml_rounds` 整个多轮循环走完（第 1 轮 + 修复轮 + 终态失败登记），不是仅第 1 轮。

```python
# Stage 1 setup：并发 → 解析 key → 落盘（barrier：整个多轮循环结束后放行）
r1 = _run_yaml_rounds(setup_tasks, api_defs_json, user_ctx, ...)
setup_keys = _parse_setup_keys(output_base)          # 纯代码；D4 缺失→__MISSING_KEY__+Warn
_write_json(output_base/_setup_extract_keys.json, setup_keys)

# Stage 2 test：逐任务注入依赖 PRE 的 key 注解（barrier 后放行）
for row, path in test_tasks:
    row['_setup_keys_note'] = _build_keys_note(row['preconditions'], setup_keys)  # D3 注解+D4 提示
r2 = _run_yaml_rounds(test_tasks, api_defs_json, user_ctx, ...)

# Stage 3 teardown：注入本 class setup key + 内容只「清理 PRE-xxx」
for row, path in teardown_tasks:
    row['_setup_keys_note'] = _build_keys_note(row['_pre_ids'], setup_keys)
r3 = _run_yaml_rounds(teardown_tasks, api_defs_json, user_ctx, ...)

# 后校验 + missing_refs：三阶段之后，位置不变
```

依赖关系：test 注入的 key 来自 setup 已落盘的 `_setup_extract_keys.json`；teardown 清理 setup 创建资源，同样引用 setup key（不依赖 test 输出）。

### 10.3 最小改动点（仅 yaml_gen.py）

| # | 位置 | 改动 |
|---|---|---|
| 1 | yaml_gen.py:377-436 | 构建循环分流三列表，row 加 `_pre_ids`（setup/teardown） |
| 2 | yaml_gen.py:404-407 | teardown_lines 去掉 `pre['steps'][:200]` 拼接，只留「清理 {pid}: {name}」 |
| 3 | yaml_gen.py:449-452 | 一次 `_run_yaml_rounds` → 三次 + 阶段间 `_parse_setup_keys`/写文件 |
| 4 | yaml_gen.py:262 | `test_case_logic` 构建处：`data_analysis` 追加 `row.get('_setup_keys_note')`（无则空） |
| 5 | — | `_run_yaml_rounds` / `_generate_one_yaml_single` 签名**不动** |

### 10.4 key 解析（纯代码，复用 `_check_37_unique_keys.py` 思路）

- 遍历 `setup_data/*.yaml`：每 block 的 case_name 取 `PRE\d+` → 该块 `input_extract` keys
- 缺失 code key → `Warning: PRE-xxx missing 'code' extraction` + 占位 `__MISSING_KEY__`（D4）
- 写 `_setup_extract_keys.json`：`{"PRE-003": {"pre003MeterCode": "$.json.code", ...}}`

### 10.5 测试用例（改完后必须执行）

1. **三阶段顺序**：mock LLM 记录调用序，断言 setup 全部先于 test/teardown
2. **key 解析**：给定构造 setup YAML → PRE→keys 映射正确
3. **D3 过滤注入**：TC-017（preconditions=PRE-003）只注入 PRE-003 keys，不注入其他
4. **D4 兜底**：setup 缺 code → `__MISSING_KEY__` 落盘 + 下游提示文本 + 后校验 P1 标记
5. **teardown 内容**：生成 YAML 全为 `POST /delete`，body 引用 `${get_extract_data(...)}`，B 内容只含「清理 PRE-xxx」
6. **37 实生成回归**：对照 v5 基线（9 PASS/11 FAIL），预期仅消除 delete_bound KeyError、teardown 由 add 变 delete

### 10.6 实现中可能触及但方案未含（原则 3：发现即汇总提醒）

以下为实际代码核实后发现的、§10.1-10.5 未覆盖但拆分必然涉及的点，处置待用户确认：

1. **三阶段 `_run_yaml_rounds` 结果合并语义**：`_generate_all_yamls` 最终只返回一个 `result` 字典（web/tasks.py:467 直接取 `errors_file` 展示）。三阶段结果必须合并：
   - `total/success/failed/repaired` → **求和**（各阶段独立计数）
   - `rounds` → 用 **max** 而非求和（后校验轮触发条件 `result["rounds"] < YAML_REPAIR_ROUNDS` 与单次调用等价，不会因三阶段求和提前耗尽修复预算）
   - 注：该触发条件本身有已知缺陷（2026-08-07 changelog 已记载「rounds 只有两种取值」），**本次不改它**（最小改动原则），仅保证三阶段化不使其更糟
2. **`_generation_errors.json` 跨阶段覆盖**：`_run_yaml_rounds` 硬编码写 `output_base/_generation_errors.json`（yaml_gen.py:629），且签名不动（§10.3#5）。三阶段都失败时，后阶段覆盖前阶段 → 前阶段失败清单丢失（仅当 ≥2 阶段同时有失败时触发）。运行时消费方仅 web/tasks.py:467 拿路径展示（无内容解析，已核实），合并安全。**建议**：三阶段各自写盘后，收尾把多份数组拼接重写一次 `_generation_errors.json`（placeholder_id 重新编号防冲突）。
3. **后校验 affected_tasks 查找来源**：现有 yaml_gen.py:468-471 用 `yaml_tasks` 按路径筛选后校验问题影响的任务。拆分后改从合并的 `all_tasks = setup_tasks + test_tasks + teardown_tasks` 筛选（纯机械改动，无行为变化）。

4. **⚠️ 后校验格式错位（D4 P1 标记的阻塞项，且为既有 bug）**：`YamlPostValidator.validate_all`（post_validator.py:29-40）只处理 `{data: [...]}` dict 结构 YAML（`if not isinstance(data, dict) or "data" not in data: continue`），而生成器写盘是**顶层 list**（yaml_gen.py `_write_yaml_result` 的 `yaml.dump(_clean_steps, ...)`，已实测 37 全部产物为 list）。→ **后校验对真实生成产物全部跳过，2026-07-24 post-validation 在当前格式下形同虚设**（非本次引入，独立 bug）。D4 第 4 组件（扫描 Test YAML 中引用 `__MISSING_KEY__` 的用例 → P1）因此无法走 `validate_all` 实现。

5. **⚠️ setup 块 case_name 实际命名格式与 §10.4 假设不符（parser 正则 bug，实生成暴露，2026-08-27）**：§10.4 假设 case_name 是手工对照组格式 `PRE-003_创建绑定计费方案的收费电表`（带连字符），但生成器实际输出 `test_PRE001_add_meter_001`（`test_` 前缀 + 无连字符 `PRE001`）、isolated 为 `test_PRE001_isolated_TC007_add_meter_001`。原 `re.match(r"PRE-(\d+)")` 全部失配 → **D4 对全部 5 个 PRE 误判缺失**（v6 实测 5/5 注入 `__MISSING_KEY__`，污染 test/teardown 阶段提示）。
   - **修复**：新增 `_match_pre_label(case_name)`，`re.search(r"PRE[-_]?(\d+)")` 同时兼容 `PRE001`/`PRE-001`，isolated 变体按 `isolated[-_]?TC[-_]?(\d+)` 归一化为 `PRE-001_isolated_TC-007` 并**独立成条目**（不并入 base）——修正 §10.4「isolated 并入 base 前缀匹配」假设：TC-007 单独引用 isolated 前置，必须独立取键。
   - 单测扩展：`test_generated_format_case_names`（§10.5 用例 2 补生成式命名）。
6. **PRE-002 的 setup 块存在但 case_name 无 PRE 锚点（D4 判缺，2026-08-27 复核修正）**：37 有 5 个共享前置（PRE-001 / PRE-002 已存在分时电表 / PRE-003 / PRE-004 / PRE-001_isolated_TC-007）。v7 实测：**分时电表确实被创建了**（setup_smart_power.yaml 的 `test_CreateTimeMeter_001`，code=ELEC_002/自动化电表-002，对照组同款块名），但块 case_name **不携带 PRE-002 锚点** → parser 无法关联 → D4 判缺、`pre002_code=__MISSING_KEY__`。根因 = case_name 锚点机制脆弱（LLM 自由命名，对照组同）；非「漏生成」，是「生成了但无锚」。连锁影响：
   - teardown_smart_power 收到缺失键提示 → LLM 硬编码 `PRE002_PLACEHOLDER_CODE` 占位 → 删除必失败（v7 实测 teardown error）
   - TOU 用例收到缺失键提示 → D4 降级自建分时电表（v7 实测该用例反而 PASS）
   **处置待用户决策**：A. 记录不修（TOU/teardown 维持降级表现）；B. teardown 对缺失键 PRE 跳过清理（不生成占位删除）；C. 排查 setup 块命名无锚机制（超本次范围）。

另核实到的关键事实（不影响实现，记录备查）：
- setup YAML 的 case_name 即 PRE 锚点，但**生成式命名无连字符**（`test_PRE001_add_meter_001` / `test_PRE001_isolated_TC007_add_meter_001`），手工对照组才是 `PRE-003_...`（带连字符）——parser 必须双格式兼容（见 §10.6#5，`_match_pre_label` 已实现）；isolated 变体独立成条目，不并入 base
- 盘上 智慧用电_37/SmartPower 是手工修复对照组：键名 `ELEC_001/meterCode/ELEC_BIND/ELEC_PARENT`（v4 原始是 `pre003MeterCode` 风格）→ 佐证 D3 键名不对齐根因，且 D3 注解必须用 setup 实际输出键名而非假设

## 十一、§10.5 测试用例执行结果（2026-08-27）

### 用例 1-5（单元测试，tests/test_three_stage_generation.py，10 passed）

三阶段顺序 / key 解析 / D3 过滤注入 / D4 兜底 / teardown 内容（构造场景）全过。
- parser 修复后新增 `test_generated_format_case_names`（生成式 `test_PRE001_...` 命名归一化）。
- 对照组与生成式两种 case_name 均解析正确，isolated 独立成条目。

### 用例 6（37 实生成回归 v7，对照 v5 基线）

**v5 基线：9 PASS / 11 FAIL / 0 ERROR** → **v7：10 PASS / 10 FAIL / 2 ERROR**

| 类别 | v5 | v7 | 变化 | 根因 |
|---|---|---|---|---|
| gateway_protocol_required | FAIL | PASS | ✅ 改善 | — |
| get_list_pagination | FAIL | PASS | ✅ 改善 | — |
| tou_positive | FAIL | PASS | ✅ 改善 | D4 降级自建 TOU 电表（PRE-002 键缺失） |
| single_rate_positive | PASS | FAIL | ❌ 回归 | input_extract 路径错 `$.code`（应 `$.json.code`）→ 提取落空 KeyError。**纯 LLM 随机方差**（v6 同 prompt 生成正确） |
| bind_billing_positive | PASS | FAIL | ❌ 回归 | input_extract **反置** `$.json.code: $.meterCode` → 空存 KeyError。**纯 LLM 随机方差**（v6 方向正确） |
| delete_meter_positive | FAIL | FAIL | 持平（根因变） | KeyError → 消除；现失败于 getList code 过滤不生效（既有查询接口问题） |
| delete_meter_bound_billing | FAIL | FAIL | 持平（根因变） | KeyError `ELEC_BIND` → **消除**（正确引用 `pre003ElectricMeterCode`）；现失败于「后端允许删除绑定计费方案电表」（retCode=0 断言，B 类无校验，同 v5 三负向） |
| 查询接口 4 项（getParent/getList 分页/非法页/非法排序） | FAIL | FAIL | 持平 | 既有查询接口问题 |
| 3× B 类负向 | FAIL | FAIL | 持平 | 后端无枚举/必填校验（既有） |
| **teardown error ×2** | 0 | 2 | ⚠️ 新增 | ① teardown_meter_management 双删：delete_meter_positive/bound 用例已删除 isolated/绑定电表，teardown 再删 → retCode=0；② teardown_smart_power 删 `PRE002_PLACEHOLDER_CODE` 占位 → 必失败（D4 降级连锁，见 §10.6#6） |

**§10.5 用例 6 达成项**
- ✅ teardown 由 add 变 delete（delete API + get_extract_data 引用真实键）
- ✅ delete_bound KeyError 消除（正确引用 `pre003ElectricMeterCode`）
- ✅ D3 键名对齐生效：delete 用例引用 `pre001IsolatedElectricMeterCode` 等真实键（非 v5 的 ELEC_BIND 字面量）
- ✅ 三阶段 barrier、跨阶段提取键传递、D4 仅对真实缺失（PRE-002）告警

**方案外新发现（待用户决策）**
1. **teardown 双删冲突**：delete 类用例消费 setup 资源后，teardown 二次删除必然 retCode=0。teardown 断言需容错「已删除/不存在」（retCode ∈ {0,1}）或改用例不消费 setup 资源。
2. **teardown 对缺失键 PRE 不应生成占位删除**（见 §10.6#6 选项 B）：无提取键的 PRE 直接跳过清理块。
3. **LLM 提取路径/方向方差**（single_rate `$.code`、bind_billing 反置）：非三阶段机制问题，但暴露 input_extract 无校验器。可选：静态检查扩 `_check_37_unique_keys.py` 校验 input_extract 方向（key 必须非 `$` 开头）+ 路径必须命中 `$.json.`。
4. **test 自建电表残留**：single_rate/bind_billing 等用例自建电表（ELEC_TC/ELEC_TC003）不在 teardown 清理范围 → 跨轮残留累积机制仍在（仅 setup 资源被清理）。

### 工具脚本修复（2026-08-27）

- `_run_37_framework.py`：case 路径改相对框架根（原 `PROJECT_ROOT / 'testcase/...'` 在脚本迁入 tests/tools/ 后拼出 E:\... 目录不存在）。

## 十二、v7 后续修复（2026-08-27 用户决策三项，task #9/#10/#11）

v7 暴露的 teardown 2 ERROR 与 LLM 提取方差问题，经用户决策后实现：

### 1. teardown 删除块剥断言（task #9，决策「teardown 断言容错」）

- **问题**：delete 类用例消费 setup 资源（delete_positive 删 isolated、delete_bound 删绑定电表），teardown 收尾二次删除返回 retCode=0 → 严格 `eq: {$.retCode: 1}` 必失败（v7 2 ERROR 之一）。
- **约束核实**：框架断言仅 `eq/ne/contains/db`，无集合/或语义可表达 `retCode∈{0,1}`（common/assertions.py assert_result，2026-08-27 核实）。
- **实现**：`_relax_teardown_validation(output_base)` 在 teardown 阶段后扫描 `teardown_*.yaml`，删除块剥除 validation key（清理清扫幂等语义，delete 照发不校验）。
- **单测**：`test_relax_teardown_validation_strips_assertions` / `leaves_other_files`。

### 2. teardown 对缺失键 PRE 跳过清理块（task #10，决策「跳过清理块」）

- **问题**：PRE-002 无锚点块 → 缺失键 → teardown 硬编码 `PRE002_PLACEHOLDER_CODE` 占位删除 → 必失败（v7 2 ERROR 之二）。
- **实现**：`_filter_teardown_missing_pres(teardown_tasks, setup_keys)` 在 setup_keys 解析后、Stage 3 前过滤：steps 去掉 `# 清理 {missing_pid}` 行、`_pre_ids` 剔除缺失 PRE；某任务全部 PRE 缺失 → 整任务移除。
- **单测**：`test_filter_teardown_missing_pres_skips_block` / `all_missing_drops_task` / `no_missing_noop`。

### 3. input_extract 静态检查扩展（task #11，决策「扩展静态检查」）

- **问题**：LLM 提取路径/方向方差（v7 single_rate `$.code` 应 `$.json.code`、bind_billing 反置 `$.json.code: $.meterCode`）→ 提取落空 KeyError，暴露 input_extract 无校验器。
- **实现**：`_check_37_unique_keys.py` 第 5 步——键不得以 `$` 开头（防反置）；表达式须命中框架三条解析路径之一（`_JSONPATH_OK`/`_DOT_OK`/`_TOKEN_OK`，依据 base/apiutil.py extract_input_data）。
- **实测**：v7 输出命中 6 个问题（bind_billing 反置 ×2 + 未定义引用 ×3 + single_rate `$.code` ×1），合法 `$.json.code` 无误报。

**验证**：tests/test_three_stage_generation.py 15 passed；`test_setup_then_test_then_teardown` 因 task#10 新行为（缺失 PRE 过滤 teardown）需 mock parser 返回真实键，已更新。

## 十三、v8 实生成与 setup 捕获键根因（2026-08-27，决策「注入 setup 标记」）

### v8 结果（task #9/#10/#11 集成验证）

- 生成 22/22 成功、0 失败、errors_file null（teardown 2 被 task#10 全部跳过）。
- ✅ **task #10 集成生效**：setup 全部无键 → 两个 teardown 任务整任务移除（日志 `🧹 teardown ... 全部 PRE 缺失提取键，跳过清理任务` ×2）。
- ⚠️ **task #9 无法在 v8 验证**：teardown 空、无删除块可容错。
- ✅ **task #11**：已对 v7 输出验证（6 处精确命中）。
- 全量回归：675 passed / 9 failed（9 个均为既有 `test_phase_bc_unit` 基线失败，无新增回归；675 = 基线 670 + 新增 5）。

### 根因：setup 是否捕获键是 LLM 掷骰子（结构性缺口，非偶发）

v8 setup 块**创建了电表但无 input_extract**（D4 全部 5 个 PRE 降级 `__MISSING_KEY__`），
下游 test 退化为**硬编码 code**（如 `ELEC_001`），与 setup `random_code('ELEC')` 创建的电表对不上
→ 共享数据流断裂、teardown 全空。对比 v6/v7 有键、v8 无键，两层根因：

1. **prompt 无强制规则**：`generate_yaml_data_single_prompt` 铁律 13 只约束「引用 setup 的用例必须
   get_extract_data」，铁律 8 反而说「extract 用不到就整字段省略」——setup 文件内无下游消费，LLM 便省略。
2. **LLM 无法识别 setup 任务**：prompt 输入只有 `执行步骤`，无「这是共享前置」标记，LLM 不知道
   创建的资源会被别的文件引用。

### 方案（用户确认：「注入 setup 标记」）

在 `_generate_all_yamls` 构造 setup 任务时，steps 前置规则说明（走 `test_case_logic` 注入，复用 D3
注解机制），强制 setup 块 input_extract 捕获资源标识：

> 「本块为共享前置 setup，创建的资源标识（code 等唯一键）必须通过 input_extract 捕获（键名 camelCase 语义化），
> 供后续用例与清理引用；即使本文件内无引用也必须捕获」

- 符合 prompt 无示例约束（规则表述注入任务数据，非 prompt 模板示例）。
- 最小改动：setup 任务构造处 + 可单测小函数。
- 防守纵深：修复（稳定捕获）→ D4（罕见失败兜底）→ task#10（真失败才跳 teardown）。

**验证**：改后跑 v9——setup 应带键 → teardown 真正生成删除块 → task#9 可集成验证。单元测试验证
setup 任务 steps 含标记、无标记行为不变。

**v8 框架跑跳过**：teardown 全空 + 硬编码 code 跑出来是「资源不存在」噪音，对比较无意义。

## 十四、v9 实生成验证（2026-08-27，根因修复 + task#9 集成交付）

### 生成结果

- 24/24 成功、0 失败、errors_file null。**setup 5 组提取键全部真实**（D4 零告警），
  `_setup_extract_keys.json` 完整；teardown 两个文件真正生成删除块（引用 `pre001MeterCode` /
  `pre001IsolatedMeterCode` / `pre003MeterCode` / `pre004MeterCode`）。
- 静态检查（task#11 扩展版）全绿。

### 框架结果：9 FAILED / 11 PASSED / 0 ERROR（v5=9P/11F/0E，v7=10P/10F/2E）

| 项目 | v5 | v7 | v9 | 说明 |
|---|---|---|---|---|
| single_rate_positive | PASS | FAIL | **PASS** | v9 input_extract 路径正确（v7 是 LLM `$.code` 方差） |
| bind_billing_positive | PASS | FAIL | **PASS** | v9 input_extract 方向正确（v7 反置方差） |
| gateway_protocol_required | FAIL | PASS | PASS | 保持 |
| get_list_pagination | FAIL | PASS | PASS | 保持 |
| delete_meter_positive | FAIL | FAIL | **PASS** | v9 用真实键（v7 KeyError） |
| delete_meter_bound_billing | FAIL | FAIL | FAIL | 后端允许删除绑定电表（B 类，既有） |
| **teardown ERROR** | 0 | **2** | **0** | ✅ task#9 集成验证通过 |
| tou_positive | FAIL | PASS | FAIL | ⚠️ 见下 |
| 查询接口 4 项 / 3×B 负向 | FAIL | FAIL | FAIL | 既有问题 |

**PASS 9→11**：delete_meter_positive 恢复（真实键）+ tou 之外的断言方差好转。
**FAIL 9**：查询接口 4 项（既有查询问题）+ 4×B 类负向（后端无校验，既有）+ tou_positive（见下）。

### 关键修复：task#9 剥断言须置空列表 `[]`，不能删键（v9 框架实测新发现）

- **框架契约**：`assert_result` 首个检查 `isinstance(expected, list)`；`base/apiutil.py` 读块时
  `validation = tc.pop('validation', '未配置断言')` —— 缺键 → 默认字符串 → `'expected' 必须是一个列表`。
- v9 首跑框架因此 2 ERROR（teardown 全块报 `'expected' 必须是一个列表`）。
- **修复**：`_relax_teardown_validation` 由 `pop("validation")` 改为置 `validation: []`（零断言，通过
  isinstance）；并覆盖「缺键也补 `[]`」场景（防旧产物/手写文件缺键）。
- 单测更新：`test_relax_teardown_validation_strips_assertions` 断言 `validation: []`；
  新增 `test_relax_teardown_validation_fills_missing_key`。共 17 passed。

### ⚠️ tou_positive 回归（v7 PASS → v9 FAIL）与新隐患

- **tou 失败根因**：用例自建 TOU 电表后，getList contains 断言 `$.data` 含 `sharpElectricity` /
  `peakElectricity` / `flatElectricity` / `valleyElectricity` —— 这些字段名**不在返回定义中**
  （分时明细在 `initDetailList[].elepayTypeCode`，返回里无顶层 `sharpElectricity` 等），违反铁律 9
  「断言字段须取自返回定义」。属 LLM 断言字段方差（v7 未做此断言故 PASS），非三阶段机制问题。
- **PRE-002 提取键名与 PRE-001 重名**（均为 `pre001MeterCode`/`pre001MeterName`）：setup_smart_power
  里 PRE-002 复用 pre001 前缀，teardown_smart_power 引用 `pre001MeterCode` 恰好拿到本文件刚提取的值，
  本次运行未串键；但若同一输出内两个 setup 文件键名重叠、且跨文件用例引用，会取错值。属 LLM 命名
  方差，task#11 静态检查可扩展「提取键名全局唯一」校验（待用户决策）。

### 后续修复（2026-08-27 用户决策两项：静态检查扩展，task #12/#13）

1. **断言字段校验（task #12）**：`_check_37_unique_keys.py` 第 6 步——`contains $.data` 列表断言的
   值若形如 camelCase 字段名，必须出现在「接口返回定义」（api_returns）字段集合中。
   - 基准仅取 api_returns（返回定义），**不含 api_parameters**：`sharpElectricity` 只在请求参数中
     （update/getPage/getList/getParentList 的 api_parameters），返回定义无此字段 → 判定臆造（铁律 9）。
   - 实测 v9：精确命中 tou 的 4 个臆造字段（sharpElectricity/peakElectricity/flatElectricity/valleyElectricity）；
     合法返回字段（code/name/retCode/msg 等）无误报；v7 备份回归命中 6 个（与既有报告完全一致，无新增）。
2. **提取键名全局唯一校验（task #13）**：`_check_37_unique_keys.py` 第 7 步——所有 setup 文件
   input_extract 的「存储键」去重，重名报错（防 LLM 复用 pre001 前缀导致跨文件串键）。
   - 实测 v9：精确命中 PRE-001 与 PRE-002 的 `pre001MeterCode` 冲突（跨 setup_meter_management /
     setup_smart_power）。v7 备份无冲突（PRE-002 当时无真实键），零误报。

> ⚠️ 说明：两项检查均为**静态告警**（校验器降级替代，不进运行流程）。v9 已生成的 tou 用例含 4 个
> 臆造断言（框架实测 FAIL），需下轮重生成或人工修；静态检查从源头拦截后续同类问题。

---

## §15 V9 遗留问题解决方案设计（2026-09-01，用户四项决策已确认）

### 15.1 背景

v9 框架结果 9 FAILED / 11 PASSED / 0 ERROR。本设计对 9 个 FAIL + PRE-002 键名冲突逐项定位根因，
区分「生成器可修」「后端缺陷」「接口契约数据缺漏」，并给出处置方案。用户已确认全部 4 项决策。

### 15.2 根因调查结论（逐用例实证）

#### A. getParentList URL 缺前缀（生成器可修 → 决策：代码层接管 + 静态检查）

- **产物** `test_get_parent_meter_list_positive`：baseInfo.url = `/electricMeter/getParentList`（丢
  `/park-energy-electric-web/` 前缀）→ 请求打到前端服务器 → 返回 HTML 页面 → 断言 JSONPath
  `$.data` 匹配失败。
- **实证**：DB `documents.api_url` = `/park-energy-electric-web/electricMeter/getParentList`（完整前缀）；
  `_load_all_api_defs` 注入 api_defs_json 直接读该字段 → **LLM 看到了完整 URL 仍丢弃前缀**。
- 既有 prompt 规则「url 必须与接口序列中的 url 逐字一致」已被 LLM 违反（非首次，2026-08-21 也手工补过
  前缀）→ 仅靠 prompt 规则不可靠，需**代码层确定性接管**。

#### B. tou_positive 4 个断言字段（接口契约数据缺漏 → 决策：记数据缺漏 + 产物修复）

- **产物**：add 成功后 getList 断言 `contains $.data: sharpElectricity/peakElectricity/flatElectricity/valleyElectricity`，
  4 断言全失败（getList 返回 data 为电表对象数组，无这些字段）。
- **数据来源链条**（查生成 thinking 证实，非 LLM 臆造）：
  1. DB 接口定义：getList **请求参数**含 `sharpElectricity` 等（分项初始读数筛选条件）；
     add 用 `initDetailList`（`elepayTypeCode: sharp/peak/flat/valley/value`）。
  2. 测试计划 TC-002（excel）引用 `sharpElectricity` 命名分项初始读数，预期「分时电表展示尖峰平谷
     分项初始读数」。
  3. 生成 LLM 照抄进 getList 的 `contains $.data: sharpElectricity` 断言（thinking 中把请求参数名
     当返回字段用）。
  4. **实测** getList 返回 data 项 `initDetailList:null` → 后端 getList **不返回分项初始读数**。
- **本质**：getList 返回定义未标注「分项初始读数是否/如何返回」→ **接口文档不清晰**。
  铁律 9 的「断言字段取自返回定义」LLM 无从遵守（返回定义缺该结构信息）。

#### C. precision 文案臆造（接口契约数据缺漏 → 决策：记数据缺漏）

- **产物** `test_add_meter_initial_reading_precision_negative`：断言 `contains $.msg: 读数最多保留2位小数`，
  实际返回 `{"retCode":0,"msg":"fail","data":"初始电量格式不正确"}` → 断言失败。
- **数据来源链条**：测试计划 TC-012 预期结果直接写死「提示读数最多保留2位小数」；生成 LLM 在 thinking
  中**明确意识到**接口返回契约未定义失败 msg 值，但判断「B 用例预期结果是用户输入，不算臆造」→ 照抄。
- **实证矛盾**：后端**有**精度校验（拒绝 3 位小数），但错误文案在 **data** 字段（`初始电量格式不正确`），
  非 msg；DB `api_returns` msg 标注「失败时返回 fail，后端无具体失败文案」与实测不符。
- **本质**：add 接口失败返回契约标注不准确（失败文案实际在 data，且具体文案未记录）→ **接口文档不清晰**。

#### D. B 类 6 项（后端缺陷，既有 → 决策：剔除 + 记缺陷）

| 用例 | 后端缺陷 | 实测 |
|------|---------|------|
| invalid_category | 无枚举校验 | retCode=1/msg=success（接受 meterTypeCode='0'） |
| billing_not_selected | 无必填校验 | retCode=1/msg=success（接受缺 payConfigCode） |
| gateway_protocol_not_selected | 无必填校验 | retCode=1/msg=success（接受缺 accessType） |
| delete_meter_bound_billing | 允许删除绑定电表 | retCode=1/msg=success |
| get_list_invalid_page | 无分页参数校验 | msg=success（接受 pageNum=0/-1） |
| get_list_invalid_sort | 无排序参数校验 | msg=success（接受 sortKey=2） |

- 测试意图合理但后端未实现校验，生成即失败，持续浪费轮次 → 从负向用例清单剔除 + 记录缺陷，等后端修复后重新加入。

### 15.3 已确认决策汇总

| # | 决策点 | 结论 |
|---|--------|------|
| 1 | B 类 6 项处置 | **剔除 + 记缺陷**（沿用 32/35 模式） |
| 2 | getParentList URL 前缀根治 | **代码层接管**（yaml_gen 后处理后缀匹配补前缀）+ 静态检查补 URL 校验 |
| 3 | precision 文案臆造 | **记数据缺漏**（接口失败返回契约标注不准确） |
| 4 | tou 断言字段 | **记数据缺漏 + 产物修复**（删 4 断言，getList 不返回分项字段） |

### 15.4 执行计划（含测试设计）

1. **URL 前缀代码层接管**：`yaml_gen.py` 后处理 baseInfo.url 与注入 api_defs 后缀匹配补前缀；
   静态检查 `_check_37_unique_keys.py` 补「url 与 DB api_url 精确匹配」校验（task#14）。
   - 测试：单测（给定 api_defs + 丢前缀产物 → 断言补全）；静态检查单测（丢前缀 url → 拦）。
2. **B 类 6 项剔除 + 记缺陷**：从测试计划/生成范围剔除；记录缺陷到 changelog 待处理清单。
3. **precision/tou 数据缺漏记录**：DB api_returns 补 add 失败返回结构标注（失败文案在 data）；
   getList 返回定义补分项结构说明（实测不返回 → 标注）。
4. **产物修复**：tou 删 4 断言（保留 add 成功 + getList 查到电表）；precision 断言改 `eq retCode: 0`。
5. **回归**：17 个现有单测 + 重生成后框架回归（预期 9 FAIL 收敛）。

### 15.5 待处理清单（2026-09-01 记录）

#### B 类：后端缺陷（6 项，已从生成清单剔除，等后端修复后重新加入）

| TC | 用例 | 后端缺陷 | 实测返回 |
|----|------|---------|---------|
| — | invalid_category | add 无枚举校验 | 接受 meterTypeCode='0'，retCode=1/msg=success |
| — | billing_not_selected | add 无必填校验 | 接受缺 payConfigCode，retCode=1/msg=success |
| — | gateway_protocol_not_selected | add 无必填校验 | 接受缺 accessType，retCode=1/msg=success |
| — | delete_meter_bound_billing | delete 允许删除绑定电表 | retCode=1/msg=success |
| — | get_list_invalid_page | getList 无分页参数校验 | 接受 pageNum=0/-1，msg=success |
| — | get_list_invalid_sort | getList 无排序参数校验 | 接受 sortKey=2，msg=success |

#### 数据缺漏：接口契约标注（2026-09-01 已补 DB 标注）

| 接口 | 缺漏 | 处置 |
|------|------|------|
| add | 失败返回契约标注不准确：原标注「失败无具体失败文案」，实测失败文案在 data 字段（`初始电量格式不正确`） | 已更新 DB api_returns msg/data 标注 |
| getList | 返回定义未标注分项初始读数结构：实测 `initDetailList:null`，不返回分项明细 | 已更新 DB api_returns data 标注 |
| — | 测试计划 TC-012 期望值「读数最多保留2位小数」与后端真实文案不符 | 待修正测试计划（数据维护） |
| — | 测试计划 TC-002 用 `sharpElectricity` 命名分项字段并断言 getList 返回 | 待修正测试计划（数据维护） |
| — | 无电表明细接口可验证分项初始读数保存 | 接口能力缺口，待后端补充 |

> ⚠️ 数据来源调查结论（2026-09-01）：tou/precision 的「臆造断言」非 LLM 捏造——LLM 忠实抄写测试计划
> 期望值（TC-012「读数最多保留2位小数」、TC-002「sharpElectricity 等」），而测试计划期望值与后端真实
> 契约不符。生成 thinking 实证（thinking_trace.log 3095-3318/5414-5487）：LLM 明确意识到接口返回契约
> 未定义失败 msg 值，但判断「B 用例预期结果是用户输入，不算臆造」而照抄 → 根因是接口文档不清晰，
> 非 prompt/schema 缺陷。

### 15.6 产物修复完成状态（2026-09-01 复核）

| 产物 | 修复内容 | 静态检查 |
|------|---------|---------|
| test_add_meter_tou_positive/test_data.yaml | 删 4 条臆造断言（sharp/peak/flat/valley）保留 add 成功 + getList 查到电表 | ✅ |
| test_get_parent_meter_list_positive/test_data.yaml | URL 补业务前缀 `/park-energy-electric-web/electricMeter/getParentList` | ✅ |
| setup_data/setup_smart_power.yaml + teardown_smart_power.yaml | PRE-002 提取键 `pre001MeterCode/Name` → `pre002MeterCode/Name`（消除与 PRE_001 串键）；teardown 改删 `pre002MeterCode` | ✅ |
| test_add_meter_initial_reading_precision_negative | **按决策保持原样**（数据缺漏留证，不做产物修复） | — |

> 产物修复改动已备份至 `backups/v9_products_20260901/`（移出 SmartPower 检查树，避免污染静态检查 glob）。

#### 修复前产物快照（备份）
- `backups/v9_products_20260901/tou_positive.yaml` — 修复前含 4 条臆造断言
- `backups/v9_products_20260901/get_parent_list.yaml` — 修复前 URL 丢前缀

静态检查全量通过（唯一键动态化/引用变量化/引用键一致性/input_extract 方向路径/断言字段/提取键名唯一/url 前缀 8 项，0 问题）。
