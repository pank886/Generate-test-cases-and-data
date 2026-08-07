# 智慧用电_26 执行失败 → 三阶段生成链路优化分析

| 项目 | 内容 |
|:---|:---|
| 日期 | 2026-08-05 |
| 分析来源 | `logs/EXECUTION_FAILURE_REPORT_26.md`（智慧用电_26，113 个测试方法，真实 API 执行 4 轮） |
| 分析对象 | 接口提取（Phase A）→ 接口关系分析（Phase B）→ 接口数据生成（Phase C）三阶段 |
| 目的 | 从三阶段梳理"还能优化哪些步骤与内容"，输出优化点清单 + 优先级 + 验证方法 |
| 状态 | 📋 分析稿（待定稿实施） |

> **核心结论**：第 4 轮后剩余阻塞已无"断言约定"级问题，全部是**生成数据与真实 API 的契约问题**（路径 404、payload 缺字段/缺值、setup 依赖未满足）。这些问题在提取/分析/生成三阶段各有具体根因，多数**能在框架侧代码/prompt 修复，且部分问题（如 retCode 约定）若不修 prompt 会在新批次必然复发**。

---

## 〇、报告问题 → 三阶段归属映射

| 报告问题 | 频次/影响 | 主要归属阶段 | 本分析中的优化点 |
|:---|:--|:---|:---|
| ① 业务接口 405 → 加 `/park-energy-electric-web` 前缀 | 已解决 | 源文件/环境信息（非提取缺陷） | ⏭ 不处理（2026-08-06 决议） |
| ② retCode 成功约定错误（生成 0，真实 1） | ~350 处 | **生成**（prompt 示例未改，必复发） | 3.1 |
| ③ 电表新增缺必填字段 | ~24+ | **提取 + 生成** | 1.1 + 3.2 |
| ④ 个别接口路径 404 | 116 | 源文件问题（开发提供文档路径与真实部署不符） | ⏭ 不处理（2026-08-06 决议） |
| ⑤ 批处理 CRLF 行尾 YAML 损坏 | 128 文件 | **生成**（写盘/改写工具） | 3.6 |
| ⑥ 未授权用例断言 401 必失败 | 1 | **关系分析**（用例设计） | 3.3 |
| ⑦ teardown 生成 8192 token 触顶 | 1 | **生成** | 3.4 |
| 🔴 充值失败:租户不存在 | 24 | **关系分析**（setup 依赖链） | 2.1 + 2.2 |
| 🟡 初次配置后无法修改! | 18 | **关系分析**（setup 幂等） | 2.2 |
| 🟡 计费方案不存在 | 10 | **关系分析**（依赖引用未落地） | 2.1 |
| 🟡 电表场景/级别不能为空 | ~24 | **提取 + 生成** | 1.1 + 3.2 |

---

## 一、接口提取阶段（Phase A）优化点

### 1.1 🔴 接口参数污染 + 必填字段失真 —— 问题 ③ 的真正根因

**实锤证据**（`智慧用电_26/api_defs.json`，72 个接口）：
```
POST /electricMeter/add 的 parameters 混入了分页查询参数：
  id / pageNum / pageSize / searchKey / startTime / endTime / month /
  orderKey / sortKey / orderByPersonCount / orderByAddress ...   ← 明显是 getPage 的 Query
且缺少真正必填：accessMethod / accessType / meterTypeCode / level /
                whetherToCount / personCode                          ← 生成 YAML 因此缺字段
```
这与报告「api_defs 参数列表把全部字段标 required（质量问题），但生成器并未真正填充」互为印证——**更上游的问题是参数本身被污染**：add 接口带上了 query 接口的参数，同时丢掉了自己的关键必填字段。

**代码根因**：
- `ingest_v2.py` `_merge_api_defs`：仅按 `method+url` 合并同名接口 → 两个不同语义的接口（`electricMeter/add` 与某 query 接口）若 url 相同或截断后相同会被合并，参数并集互相污染。
- `ingest_v2.py` `extract_apis_from_yapi_md`：请求参数区正则 `### 请求参数\s*\n(.*?)(?=\n### 返回数据|\Z)` + `_subsection` 的 `\*\*Query\*\*/\*\*Body\*\*` 严格标题匹配——任一标题格式偏差都会导致把下一节内容吞进本节。
- `prompts/extraction_prompts.py` `api_def_extract_prompt`：**没有"区分新增必填 / 查询可选"指令**，也没有"参数必须归属正确 section（headers/query/body）"约束。

**优化步骤**：
1. **去重键升级**：接口合并从 `method+url` 升级为 `method+url+name 语义`；url 相同但 name/描述语义不同 → 告警 + 不合并或取交集。
2. **参数区严格分界**：`extract_apis_from_yapi_md` 对每张参数表打标 `section=headers/query/body`，逐表解析；标题不匹配时宁可少收不可错收。
3. **prompt 铁律**：`api_def_extract_prompt` 增加「区分新增必填与查询可选：写接口标真正必填（不随查询混入）；required 只标接口真实约束」。
4. **入库自检**：对 add/insert/update 类接口，`required` 字段数为 0 或缺失常见写字段（name/code/type）→ 打 `param_integrity` 告警 annotation，供生成阶段补全。

---

## 二、接口关系分析阶段（Phase B）优化点

### 2.1 🔴 dependency_map 与数据生成断链 —— 充值失败/计费方案不存在

**现状**：`web/tasks.py` 生成并加载 `dependency_map.json`（含 `story_pre_api_sequence` / `internal_dependency` / `cross_module_dependency` / `teardown_api_sequence`），但 `_generate_one_yaml` / setup 生成**完全不消费**该文件 → 依赖分析结果只落日志，不驱动生成。

**后果即报告数据**：`充值失败:租户不存在`(24) 说明 setup 里充值步骤依赖的租户/账户**没有先创建**；`计费方案不存在`(10) 说明引用的计费方案未在依赖链前部落地。

**优化步骤**：
1. **把 dep_map 接入生成**：`_generate_one_yaml` 与 setup 生成注入 `dependency_map` 字段（`story_pre_api_sequence` 决定 setup 步骤顺序；`internal_dependency.used_by` 决定 extract 是否必需）。
2. **前置 DAG 生成**：将 PRE 关系提升为有向无环图（先建租户 → 再充值 → 再绑定），分析阶段产出 `pre_dependency_order`。
3. 无 dep_map（生成失败）时降级：按 PRE 步骤文本顺序执行 + 告警，而非静默忽略。

### 2.2 🟡 setup 依赖链 / 幂等性分析缺失 —— 初次配置后无法修改! / 电表场景为空

**现状**：
- `_resolve_resource_conflicts`（`nodes.py`）只做「同一 PRE 被 ≥2 个正向写用例引用 → 克隆隔离」，**不建前置 DAG、不设计幂等**。
- 报告 `初次配置后无法修改!`(18)：结算配置 setup 保存两次 → 真实 API 拒绝；`电表场景/级别不能为空`(~24)：setup 里 add meter 全部被拒（缺 sceneCode/level，见 1.1/3.2）。

**优化步骤**：
1. **幂等设计**：分析阶段把「一次性配置」类前置（结算配置、计费方案、公摊配置）标注 `once=true`；setup 生成策略改为 **query-then-create**（先查存在 → 存在跳过 / 不存在才创建）。
2. **PRE 依赖完整性校验**：`ExcelPlanValidator` 增加「PRE 步骤中引用的资源是否在本 story 或其他 PRE 创建」的静态检查（关键词租户/账户/方案/配置），缺失 → 拦截修复。
3. **共享前置资源命名约定**：租户/账户/电表 code 在 dep_map 中显式声明，跨 PRE 复用同一 code（避免重复创建同一资源）。

### 2.3 🟡 接口映射分析无契约闭环

**现状**：`analyze_api_mapping_prompt`（3 步分析第 3 步）输出自由文本，无 schema、不校验 url 是否命中真实接口；产物 `api_analysis` 直接作为权威输入注入 `generate_excel_plan_thinking`。

**优化步骤**：
1. 分析产物入库前对引用 url 逐条跑 `ExcelPlanValidator.check_urls` 静态校验（纯代码，复用现成函数），未命中的接口引用 → 打标剔除或修复。
2. 分析结果结构化（API→场景映射表），供用例生成按「接口覆盖度」核对（每个接口至少 1 条直接用例，prompt 已要求，代码未核验）。

---

## 三、接口数据生成阶段（Phase C）优化点

### 3.1 🔴 prompt 示例仍写 retCode:0 —— 问题 ② 必复发（最高优先，改动最小）

**实锤证据**（`prompts/extraction_prompts.py`）：
```
252:  "validation": [{{"eq": {{"$.retCode": 0}}}}, ...          ← format_yaml_data_prompt 示例
268:  "validation": [{{"eq": {{"$.retCode": 0}}}}, ...          ← 同上
287:  "每步至少一条断言（如 {{eq: {{retCode: 0}}}}）"           ← 结构铁律示例
328/330: "assertions": [{{"eq": {{"$.retCode": 0}}}}]           ← generate_dependency_map_prompt 示例
```
报告虽已批量把 240 处**产物**改为 `retCode:1`，但 **prompt 示例与铁律仍是 0** → 下一个新批次会再次生成 `retCode:0`，问题必然复发。

**优化步骤**：
1. **成功码参数化**：`config.API_SUCCESS_RETCODE`（本项目=1）注入 `format_yaml_data_prompt` / `repair_yaml_data_prompt` / `generate_dependency_map_prompt`，prompt 示例改用 `{success_retcode}` 占位。
2. **schema 回炉**：正向用例 validation 若出现 `eq: {$.retCode: <非成功码>}` → 拦截回炉（`TestData` 增加校验）。
3. 报告对「反向失败断言保留 0」的区分（正向=1、失败=0、SETUP 弱断言 `ne:200`）固化为 prompt 铁律，避免一刀切。

### 3.2 🔴 必填字段未强制填充 —— 问题 ③ 的生成侧根因

**现状**：`_inject_annotations` 只注入 `_annotations`，不校验 json/params 是否覆盖 api_defs 的 `required` 参数；写盘前无任何 required 完整性检查。

**优化步骤**：
1. **写盘前 required 检查**（纯代码）：遍历步骤 api 的 `required` 字段，缺失 → 用「业务字典值 + 数据工厂方法」自动补齐；补不出的（枚举未知）→ 告警 + 进修复轮。
2. **必填值来源优先级**：api_defs `default` 字段 → 业务字典枚举（见 3.5）→ 工厂方法 → 合理固定字面量。禁止填空字符串 / 省略。
3. 与 1.1 自检联动：提取侧参数污染未清 → 生成侧按「干净 required」填充，避免把 pageNum 等查询参数当 add 必填填进 payload。

### 3.3 🟡 未授权（无 token）用例生成 —— 问题 ⑥

**现状**：`generate_excel_plan_thinking` 逆向用例设计含「无权限访问 / 越权操作」，而框架**自动注入 token** → `test_global_unauthorized_export_negative` 断言 401 必失败（实际 200）。

**优化步骤**：
1. 生成策略**禁止「无 token / 未授权访问」类用例**（token 自动注入场景下无意义）。
2. 「无权限」改测**无权限角色 token**（需在 PRE 中创建低权限账号），或降级为业务级越权（非 401 断言）。

### 3.4 🟡 teardown 生成 8192 token 触顶 —— 问题 ⑦

**现状**：`_generate_all_yamls` 把共享前置文本逆向拼接为 teardown 文本，一个 teardown YAML 含大量步骤 → `GEN-FAIL-R2-007`：`Could not parse response content as the length limit was reached - completion_tokens=8192`。

**优化步骤**：
1. **分块生成**：teardown 按 step 数切块（每块 ≤ N 步）分别生成，再合并为多 step YAML。
2. **失败告警**：终态失败登记中把「输出超限」单列为 `GEN-FAIL-OVERSIZE`，前端可见，而非与其他错误混在一起。
3. 生成前按「逆向操作量」预估 token，超阈值提前拆块。

### 3.5 🟡 业务字典 / 枚举值工厂缺失

**现状**：`data_factory/methods.yaml` 只有基础类（get_extract_data）、数据生成类（random_plates）、时间类——**无电表场景 sceneCode、meterTypeCode、whetherToCount、useType、deviceStatus 等业务枚举**。LLM 只能猜值，导致 sceneCode 缺失 / 语义错误。

**优化步骤**：
1. data_factory 增加「**业务值字典**」分类：从 api_defs 的 returns/description 枚举说明（如 `电表场景` 的取值列表）+ 术语表提取，注册为 `{field_name: [合法值]}`。
2. 生成时优先用字典值填充必填业务字段，随机化仅在字典标记可随机的字段上做。

### 3.6 🟡 文件写盘换行策略 + 批量改写保护 —— 问题 ⑤

**现状**：YAML 写盘 `open(..., "w")`（默认 LF），`.py` 用 `newline="\r\n"`；报告批量加 url 前缀时因 CRLF 被吞（`readlines()` 文本模式 + 正则 `$` 吞行尾）导致 128 文件 YAML 损坏。

**优化步骤**：
1. 框架内写盘统一 `newline` 策略（建议全部 LF，或按模板指定），并保留该约定到后续批量改写脚本。
2. 提供**按行处理 + 保留行尾**的文本改写工具函数（供 url 前缀 / 批量替换类操作使用），禁止用 `$` 匹配整行并丢弃行尾。
3. 改写类操作前先 `git diff --stat` 或 YAML 解析冒烟校验，损坏即回滚。

---

## 四、优化优先级与落地清单

| 优先级 | 优化点 | 改动面 | 对应报告问题 |
|:--|:---|:---|:---|
| **P0** | 3.1 retCode 成功码参数化 + prompt 示例修复 | prompt + config + schema | ②（必复发） |
| **P0** | 1.1 参数污染清理 + 必填字段自检 | 提取 + api_defs | ③ |
| **P0** | 3.2 写盘前 required 强制填充 | 生成器 + TestData | ③ |
| **P1** | 2.1 dependency_map 接入生成 | web/tasks + 生成器 | 充值失败/计费方案不存在 |
| **P1** | 2.2 setup 幂等 + PRE DAG | 分析 + ExcelPlanValidator | 初次配置/租户不存在 |
| **P2** | 3.3 401 用例策略 | 生成 prompt | ⑥ |
| **P2** | 3.4 teardown 分块生成 | 生成器 | ⑦ |
| **P2** | 3.5 业务值字典工厂 | data_factory | ③ 补全质量 |
| **P2** | 3.6 写盘换行 + 改写工具 | 框架工具 | ⑤ |

---

## 五、验证方法

| 优化点 | 验证方法 | 目标 |
|:---|:---|:---|
| 3.1 | 重跑一个新批次，扫描产物 retCode 断言 | 正向用例 retCode=成功码 100%；0 处误写 |
| 1.1+3.2 | `electricMeter/add` 实调（补全 payload） | getPage 查到真实入库（报告已验证基线 total:1） |
| 2.1+2.2 | 重跑智慧用电_26 | `充值失败:租户不存在`=0、`初次配置后无法修改`=0 |
| 3.3/3.4/3.5/3.6 | 重跑 + 检查错误登记 | 无 401 前置用例、无 OVERSIZE、必填字典齐全、YAML 可解析 |

---

## 六、结论

- **提取阶段**（1.1）：最大问题是**参数污染 + 必填字段失真**（add 混入 query 参数），是入库/提取侧可修复的确定性缺陷，优先于 LLM 侧优化。（问题① url 前缀、问题④ 404 路径均确认为源文件问题，2026-08-06 决议不处理）
- **关系分析阶段**（2.1~2.3）：`dependency_map.json` 已生成却**未接入生成链路**是最大浪费；setup 依赖链与幂等性缺失直接造成报告里的租户/配置/方案三类高频失败。
- **数据生成阶段**（3.1~3.6）：`retCode:0` 示例仍残留在 prompt 中是**最易复发、改动最小**的问题；required 强制填充 + 业务字典是补全质量的关键。
- 三阶段共性结论：**已能在纯代码层（非 LLM）拦截/修复的问题优先做**（1.1/3.1/3.2/3.6），LLM prompt 铁律作为第二道防线。
