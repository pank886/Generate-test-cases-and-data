# 2026-09-10 智慧停车：手动结果分析 → Phase C 两轮重跑 → 框架验证

> **状态：方案已确认（2026-09-10）；Round 0 后结论重大更正（见 §九），待按更正版复确认。**
> 触发源：用户要求对 `C:\Users\damai\PyCharmMiscProject\testcase\园区基线\智慧停车` 做手动结果分析，
> 据分析启动 Phase C 重跑生成，生成后用框架验证（`python run.py -p testcase/园区基线/智慧停车`），
> **只重跑两轮**；判障按「连通性 / 契约 / 业务逻辑」三层协议。
> 原则：①先确认②最小改动③只改相关、发现涉及即汇总提醒④方案含测试、改完即测
> ⑤文档先行——先写本文档，再核对实际情况，比对无误才执行。

> ⚠️ **更正声明**：本文档 §二/§九 的**初版**结论（「`parkingPark/saveOrUpdate` 新建路径为接口缺陷」）
> 已被推翻。根因有二，均属**测试数据/生成源**问题，非接口缺陷：
> ①`api_defs.json` 示例值 `parkingPark.parkCode = "PARK001"` 误导生成器传非空 parkCode；
> ②探针自身把请求体多包了一层 `{"json": …}`（框架实际是**裸传** `json:` 块），导致取证失真。
> 更正依据见 §九，全部为可复现的实跑证据。

## 一、基线事实（2026-09-10 09:07 全量执行）

执行对象 = 09-09 20:11–20:41 第 2 轮重生成后的盘上 YAML（`.py` 停在 09-09 15:09，本轮不重生成 `.py`）。
`report/junit.xml` 83 节点（80 个 test 函数 + 参数化）：

| 分类 | 数量 | 占比 |
|:---|--:|--:|
| passed | 12 | 14% |
| 断言失败（body `<failure>`） | 20 | 24% |
| setup 破损（`failed on setup`） | 45 | 54% |
| teardown 破损（`failed on teardown`） | 5 | 6% |

对比第 1 轮 sidecar（`run_ts=20260909200938`：20 passed / 27 failed / 36 broken）→ **第 2 轮重生成负向**。
另：09-10 这轮结果**尚未摄取**，83 个 `.last_run.json` 仍全部停留在第 1 轮 cohort。

setup 破损归因（45 例，按 junit message 的 `[接口名 - 未知]` 前缀）：

| 根因接口 | 例数 |
|:---|--:|
| `parkingPark/saveOrUpdate` 车场维护聚合 | **36** |
| `parkingDevice/add` 设备-添加 | 6 |
| 规则 code 未捕获 `KeyError` | 3 |

## 二、三层排查结论（更正版）

### ① 连通性 ✅ 正常
业务接口全部到达网关，无超时/拒连。09-09 的 url 前缀修复（`/park-access-parking-web`）生效，
nginx 405 已消失。**无环境问题**。

### ② 契约 — 复核后**仅 1 处**成立，另 2 处撤回

| # | 事实 | 证据 | 归属 |
|:---|:---|:---|:---|
| **C1** | `parkingPaymentConfig/add` **丢弃客户端 `code`**（服务端生成 `zhyqC*`）且 `data:null`；`getByCode/<传入code>` 查不到 | S1 成功 + S2 `data:null` + S3 回读真实 `code=zhyqC*` | 接口行为**但可绕**（页序回读，见 §十）→ **数据层可解** |
| ~~C2~~ | ~~`parkingDevice/add` 字段名不符：文档 `placeCode` 而数据发 `parkCode`~~ | **S7 仅发 `placeCode/placeName` 成功；S7b 仅发 `parkCode/parkName` 亦成功** | **撤回：非契约缺陷**。但二者**语义不同**（见 §十）：`parkCode/parkName`=**绑定车场**（回读时 `parkCode` 有值、出现在车场设备清单），`placeCode/placeName`=**设备位置**（仅 `placeCode` 有值、不绑定车场） |
| ~~C3~~ | ~~同一厂家/型号对同轮一次成功一次 91010~~ | **S7/S7b 同对连建两台均 `retCode=1`，未复现** | **撤回：非接口缺陷**，原判据不成立 |

### ③ 业务逻辑 — 重大更正：**车场新建可以成功**

原结论「`parkingPark/saveOrUpdate` 新建路径必然失败，属接口缺陷」**错误**。实跑：

| 实验 | 请求 | 结果 |
|:---|:---|:---|
| **S4** | 新建主场，`parkingPark.parkCode = ""` | ✅ `retCode=1` |
| **S9** | 新建主场，`parkingPark.parkCode = "PROBE_NONEMPTY_…"`（同结构、仅该项不同） | ❌ `请求失败` |
| **S10** | 新建**子场**（`mainPark=false` + `parentCode=<真实主场code>`） | ✅ `retCode=1` |

**根因**：**新建时 `parkingPark.parkCode` 必须传空串 `""`，由服务端生成**；传非空即「请求失败」。
`api_defs.json` 中该项示例值 `"PARK001"`（且 `carInConfig.parkCode` / `paymentConfig.parkCode` /
`specialAccessConfigList.parkCode` 同样为 `"PARK001"`）是误导源——生成器据此产出
`parkCode: ${random_code('PK_HOME')}`，四轮 ~170 次调用因此 0 成功。**属数据问题，非接口缺陷。**

其余业务层（复核后）：
- **B2** 设备字典编码自造：`api_defs.json` 中厂家/型号示例值**全为空** → 生成器无真实来源。**成立**。
- **B3** 提取路径写错：`input_extract: X: $.code` 少了 `json.` 前缀（抽取作用于请求参数
  `{"json": …}`）→ 3 例 `KeyError`。**成立**。
- **B4** 第 2 轮新增 33 个 teardown YAML，5 例 `TypeError`/`KeyError`。**成立**，且叠加 N5 后
  本轮 teardown 仍不可用。

## 三、已确认决策（AskUserQuestion ×4，2026-09-10）

1. **Round 0 探针范围** = 只读采集 + **一次真车场 payload 回放**（判定 B1 归属）。
2. **「两轮」定义** = **2 ×（Phase C 重生成 + 框架执行）**。
3. **疑似接口缺陷处理** = 记录证据 + **排除出重跑目标**，报告归类「接口缺陷」。
   （注：原判定的 3 处中 2 处已撤回，**实际适用对象改为 N4/N5/N2**，见 §十。）
4. **真实接口事实落点** = 写入 **`api_defs.json` 示例值** + 上下文规则；并按 09-09 惯例同步 SQLite `documents` 存档。

## 四、方案

### Round 0（前置一次性）：取权威事实 — ✅ 已完成
只读采集 + 真车场回放 + **黄金链路实证**（`_probe_parking_golden_path.py`），产出证据 JSON。

### Round 1：重生成 + 执行
1. 更新 `phase_c_user_context.md`：R1 改为「创建时 parkCode 传空串 + 回读服务端 code」；
   R2 改为「规则 code 走页序回读或复用存量真实 code」；R3 补真实字典对；R5 补回读链。
   （仍**只含规则不含示例**。）
2. 同步 `api_defs.json` 示例值：`parkingPark.parkCode`/`carInConfig.parkCode` → `""`；
   补真实设备字典对；补 `placeCode/parkName` 别名说明。SQLite `documents` 同步。
3. Phase C 重生成（全量；仅 YAML，不动 `.py`/`dependency_map`）。
4. 框架执行：`python run.py -p testcase/园区基线/智慧停车`（阻塞处理见 §五-5）。
5. 摄取 sidecar（`tests/tools/_ingest_phase_c_run_sidecars.py`）+ 分析。

### Round 2：修正 + 再执行
按 Round 1 证据修正上下文；**接口缺陷项（N4/N5/N2）排除出重跑目标**；重生成 → 执行 → 摄取 → 分析。

### 收尾
出最终报告：逐例带 **Bug 归属**（测试数据 / 接口缺陷 / 环境），附证据；回填 §七。

## 五、原则③汇总：发现但原方案未覆盖，需你知悉/决策

| # | 事项 | 现状 | 建议 |
|:---|:---|:---|:---|
| 1 | **teardown** | 09-09 方案写明「本轮不涉及」，但第 2 轮实际生成 33 个、5 例破损。**原以「删除接口不可用」为由建议弃，该前提已被推翻**（删除三端点均可用，仅形状各异，见 §十） | **改为建议本轮修**：形状修正后 teardown 真正可清理，5 例破损应一并修 |
| 2 | **存档同步** | 改接口事实须 `api_defs.json` 与 SQLite `documents` 一致（09-09 惯例） | 决策 4 已含，执行时同步 |
| 3 | **sidecar 落点滞后** | 09-10 09:07 结果未摄取，83 个 sidecar 仍是第 1 轮 | 执行前补摄取（aux 工具） |
| 4 | **`.py` 漂移** | runner 停在 09-09 15:09；晚于它的 YAML 重生成不更新 `.py`，已现漂移 | 本轮不动，登记待议 |
| 5 | **`run.py` 阻塞** | 末尾 `serve_report()` 起 HTTP 服务并开浏览器，不会自行退出 | 后台跑 + 轮询 junit 完成后终止 |
| **6** | **规则 code 策略（新增）** | list 的 `name` 过滤被忽略、page 为**倒序**（最新在前） | **已决策（用户 2026-09-10）**：**一律走 add→页序回读**（`getParkingPaymentConfigPage` → `$.data.list[0].code`）。原本的「污染 dev」顾虑随删除可用而消除（teardown 可回收） |
| **7** | **只读接口缺陷（新增）** | `parkingPark/getDetail/{parkCode}` 对**任意**车场（含 2 个存量真实车场）恒「请求失败」 | 按决策 3 归「接口缺陷」并排除其下游用例；车场信息改装由 `getAllWithDevices` 承载 |
| **8** | **探针残留** | 已清理（`_cleanup_parking_probe_residue.py`）：探针规则全清、车场删 4/7 | 余 3 个（`探针空码04853`/`探针F04846`/`探针D04845`）删除恒「请求失败」，疑似先删规则致其 orphan；已知悉不再追 |

## 六、测试设计

- **探针自查**：采集结果与 api_defs 声明比对；请求体形态与框架一致（**裸传** `json:` 块）。
- **数据层**：改完复查——无重复/双重前缀、`parkingPark.parkCode` 示例值为空串、真实字典值成对。
- **回归**：`docs/RULES_DETAIL.md` 与既有 changelog 口径一致；不动 `agent_components/**` 若不改代码则免跑单测。
- **端到端**：两轮 `run.py` 对比——首轮通过率、`saveOrUpdate` 成功率、setup 破损数；与 §一 基线比对。
- **归属校验**：接口缺陷项必须具备「同 payload 稳定复现」证据，否则不得归类（本轮已据此撤回 C2/C3）。

## 七、实施记录（逐轮回填）

| 阶段 | 结果 |
|:---|:---|
| Round 0 只读采集 | ✅ 完成，证据 `_phase_c_rerun_2026-09-10/round0_probe_*.json` |
| Round 0 回放定性 | ⚠️ **初版结论作废**（探针封装失真 + getDetail 本身不可用）；已由黄金链路实证取代 |
| Round 0 黄金链路实证 | ✅ 完成 —— 见 §九/§十，证据 `round0_golden_raw_full.json` / `round0_delete_matrix.json` |
| 探针残留清理 | ✅ 完成 —— `_cleanup_parking_probe_residue.py`：探针规则全清、车场删 4/7；余 3 个（§五-8） |
| **数据层落地** | ✅ 完成 —— `_apply_parking_api_defs_facts.py` 改 `api_defs.json` 12 处 + 同步 SQLite 存档 12 行；`phase_c_user_context.md` 重写 R1–R7 |
| 数据层测试 | ✅ `tests/test_parking_data_layer_facts.py` 23 passed；全仓 `tests/` 829 passed，9 failed 全在 `test_phase_bc_unit.py`（**与 09-09 记录的既有基线同为 9，未新增**） |
| Round 1 重生成 + 执行 | 待回填 |
| Round 2 重生成 + 执行 | 待回填 |
| 最终报告 | 待回填 |

## 八、波及面 / 未覆盖

- 不改 `agent_components/**`（生成器/prompt/schema）；仅数据层（模块上下文 + `api_defs.json` + 存档）。
- 不改执行框架、不改 `.py` / `dependency_map` / `test_plan.xlsx`。
- 服务无热重载：若走 Web 入口需重启服务；本轮走驱动/端点二者择一。

## 九、Round 0 实测结论（更正版，2026-09-10）

工具：`tests/tools/_probe_parking_park_reality.py`（只读采集）、`tests/tools/_probe_parking_golden_path.py`
（黄金链路实证，aux 不入系统运行）。

### 9.1 初版结论为何错误（两处失真）

1. **探针封装失真**：探针把请求体写成 `{"json": {…}}`，而框架实际是把 YAML 的 `json:` 块
   **裸传**（`apiutil.run_main(**tc)` → `requests(json=…)`）。故「回放失败」实为服务端收到空体，
   取证无效。
2. **取证接口本身不可用**：回放用的 `parkingPark/getDetail/{parkCode}` 对**任意**车场恒「请求失败」
   （N4），据其取到的 payload 为空，回放自然失败。

### 9.2 黄金链路实证（全部 `--envelope raw`，即框架真实形态）

| 步骤 | 请求 | 结果 |
|:---|:---|:---|
| S1 | `parkingPaymentConfig/add`（按 api_defs 字段，`unitPrice` 传数字 10） | ✅ `retCode=1` |
| S2 | `parkingPaymentConfig/getByCode/<客户端传入 code>` | ⚠️ `retCode=1` 但 `data:null` → 客户端 code 被丢弃 |
| S3 | `parkingPaymentConfig/getParkingPaymentConfigList` 回读 | ✅ 命中真实 `code=zhyqC02507707` |
| S4 | `parkingPark/saveOrUpdate` 新建主场，`parkCode=""` | ✅ `retCode=1` |
| S5 | `getAllWithDevices {parkName}` 回读 | ✅ 服务端生成 `parkCode=zhyq134584101911699` |
| S6 | `parkingPark/getDetail/<新码>` | ❌ `请求失败`（N4） |
| S7 | `parkingDevice/add` **仅** `placeCode/placeName` | ✅ `retCode=1` |
| S7b | `parkingDevice/add` **仅** `parkCode/parkName`，同厂家/型号对 | ✅ `retCode=1`（C3 未复现） |
| S9 | 新建主场，`parkCode="PROBE_NONEMPTY_…"` | ❌ `请求失败`（**证明必须传空串**） |
| S10 | 新建子场（`mainPark=false` + `parentCode=<真实主场>`） | ✅ `retCode=1` |
| S11 | `parkingPark/deleteByCodes` 4 种**带壳**形状（`{"codes":[…]}` 等） | ❌ 全部 `参数类型错误`（**形状错，非端点缺陷**） |
| D1 | `deleteByCodes` 再加 `data=`/`params=` 共 5 种编码 | ❌ 全部 `参数类型错误`（同上） |
| **D2** | `parkingPark/deleteByCodes` **裸数组** `["zhyq…"]`（用户指正） | ✅ `retCode=1` —— **N5 撤回** |
| **D3** | `parkingPaymentConfig/delete` **裸数组** `["zhyqC…"]` | ✅ `retCode=1` —— **N2 撤回** |
| **D4** | `parkingDevice/deleteByCodes` `{"codes": "a,b"}` **字符串** | ✅ `retCode=1` —— 设备删除形状再不同 |

### 9.3 采集到的真实事实

| 类别 | 实测值 |
|:---|:---|
| 真实车场 | 主场 `zhyq288682290429943`(test888) / `zhyq318740310078449`(全添加车场) |
| 真实收费规则 | `zhyqC13264664`(主场临停收费规则, mainPark=true) / `zhyqC10695652`(子场临停收费规则, mainPark=false) |
| 真实设备字典对 | `100061`/大华 + `100121`/`DH-IPC-HFW5243K-ZFD-LED`（出现 14 次）/ `100122`/`DH-IPC-HF8249F-FR`；`HIKVISION`/海康威视 + `DS-TCG405-E`/`DS-TCG405-E` |

## 十、已核实的接口事实与可用配方（写入数据层的依据）

**可用配方（实测通过）**
- **新建主场**：`{"parkingPark":{"parkCode":"","parkName":<唯一>,"mainPark":true,"parentCode":"","totalSpots":100,"occupiedSpots":0,"needCount":1,"bindPayCode":<真实规则code>,"needPay":1}}` → ✅
- **新建子场**：同上 + `"mainPark":false` + `"parentCode":<真实主场code>` + 子场规则 code → ✅
- **设备添加**：`placeCode/placeName` 或 `parkCode/parkName` **均可**；`parkingDeviceModelName` **必填非空**
- **车场 code 回读**：`getAllWithDevices {"parkName":<唯一>}` → `$.data[0].parkingPark.parkCode`
- **规则 code 回读**：add 后 `getParkingPaymentConfigPage {pageNum:1,pageSize:1,configType:"3"}`
  → `$.data.list[0].code`（**倒序，最新在前**；`name` 过滤无效）
- **别名**：`carInConfig` 可带 `parkName`；`specialCarEnable:true` 时 **必须**带非空 `specialAccessConfigList`

**删除通道的正确形状（实测；三个端点各不相同，是 teardown 可用性的关键）**

| 端点 | 正确入参形状 | 实测 |
|:---|:---|:---|
| `parkingPark/deleteByCodes` | **裸数组** `["zhyq…"]` | ✅（用户指正后复验） |
| `parkingPaymentConfig/delete` | **裸数组** `["zhyqC…"]` | ✅ |
| `parkingDevice/deleteByCodes` | `{"codes": "code1,code2"}` **字符串** | ✅ |

> 删除车场前须先删该车场下的设备，否则返回业务守卫
> 「车场内存在停车设备，请先删除设备或者解除绑定后再尝试删除车场」。
> 因形状搞错，本文档初版一度把 N5/N2 误判为接口缺陷，**现已撤回**。

**接口缺陷（按决策 3 → 记录证据 + 排除出重跑目标）**

| # | 端点 | 现象 | 证据 |
|:---|:---|:---|:---|
| N4 | `parkingPark/getDetail/{parkCode}` | 对**任意**车场（含 2 个存量真实车场）恒「请求失败」 | §9.2 S6 + 3 码复测 |

**接口行为（非缺陷，但需数据层规避）**
- N1 `getParkingPaymentConfigPage`/`List` 的 `mainPark`、`name` 过滤**被忽略**；page 为**倒序**（最新在前）。
- C1 客户端传入的规则 `code` 被丢弃，服务端回写 `zhyqC*` → 走页序回读。

## 十一、归因：接口文档提取 还是 LLM 生成？（2026-09-10 用户提问）

**判据**：对每类失败问一句「LLM 当初**有没有可能知道**？」——若生成的 YAML 是 api_defs 的**忠实渲染**，
责任在文档提取/维护；只有当文档与 prompt 铁律已写明、LLM 仍写错，才算 LLM 问题。

### A 类 · 接口文档提取/维护问题（**主因**）

LLM 无从知道，只能照文档做，文档本身错/缺：

| 现象 | 文档怎么写 | LLM 只能怎么做 |
|:---|:---|:---|
| 车场新建恒失败（36 例 setup 破损） | `parkingPark.parkCode` 示例值 `"PARK001"`、desc 仅「场地编号」，**无一字**说新建须空串 | 照抄示例 → 传非空 → 「请求失败」 |
| 设备 91010/91110（6 例） | 厂家/型号四个 `value` **全是空串** | 无真实字典值可填 → 自造编码 |
| 设备绑定不生效 | `placeCode` 的 desc 写成「车场编码」（实为**设备位置**） | 以为与 parkCode 等价 → 绑定丢失 |
| teardown 参数类型错误（5 例） | body schema 只能表达成「名为 `codes` 的字段」 | 按定义生成 `codes: [...]`，而真实要**裸数组** |
| 「建规则→绑同 code」恒失败 | 未记录「服务端丢弃客户端 code」，也无回读路径 | 按铁律 10 动态生成 code 并绑定 → 该规则服务端并不存在 |
| 字段缺失 | `carInConfig.parkName`、`otherPark*Names` 未收录 | 无从生成 |

### B 类 · LLM 未遵循已写明的铁律（**次因，约 8 例**）

文档/prompt 已给正解，LLM 仍写错：

| 现象 | 已有说明 | 实际生成 |
|:---|:---|:---|
| 4 例 `failed on setup: KeyError 'preXXXRuleCode'` | 铁律 13 明写 `input_extract` 取 key 写作 `$.json.code` | 写成 `$.code` / `$.parkingPark.parkCode` |
| 断言失败（20 例） | — | 需逐例复核（见下） |
| 车牌占位符（如 `PLATE-TEMP-72`） | api_defs 已给合法样例 `粤A12345` | 用不可解析的占位串 |

> **量化**：junit 82 节点中 KeyError 8 例（其中 4 例为 setup、3 例为 `.py` 内 `data['code']`）；
> 全量扫描 217 处 `input_extract` 中 **25 处**缺 `$.json.` 前缀（违反铁律 13）；
> `parkingPaymentConfig/add` 的 `code` **42 处全部**为自造/字面值，**0 处**走回读。
> 按上表归集，**setup 45 例中 42 例属 A 类（36 车场 + 6 设备）**，B 类约 8 例，占比 <10%。

### C 类 · 接口缺陷（非 A 非 B）

`parkingPark/getDetail/{parkCode}` 对任意车场恒「请求失败」——文档与生成都无过，端点自身问题。

### 结论

**主因是接口文档（api_defs）提取出来的事实不全/不对，不是 LLM 生成能力问题。**
LLM 在绝大多数场景是对文档的忠实渲染：文档示例值错它就照抄、文档空着它就自造、文档形状错它就照做。
故修复重心应放在**把实证事实写回 api_defs + 上下文规则**（本轮已做），而非改生成器或 prompt。
B 类 8 例可在 Round 1 的执行结果里复核——若重现，再考虑补强铁律 13 的约束。

> 遗留待复核：20 例**断言失败**（body `<failure>`）未逐例定性，可能是 A（返回定义取值不对）
> 或真实接口逻辑问题；Round 1 执行后按三层协议逐例归属。

**设备字段语义（`parkingDevice/add`，实测回读对照）**

| 字段对 | 落库表现 | 含义 |
|:---|:---|:---|
| `parkCode` / `parkName` | 记录 `parkCode` 有值、`placeCode` 为 null；**出现在车场 `carInDeviceList`** | 所属车场（绑定） |
| `placeCode` / `placeName` | 记录 `placeCode` 有值、`parkCode` 为 null；不进入车场设备清单 | 设备位置 |

→ 「设备绑定到车场」类断言必须发 `parkCode/parkName`；两者都发则各自落库，互不冲突。

---

## 十二、Round 1 执行结果（2026-09-10 10:20–10:55）

### 12.1 重生成事实

`tests/tools/_run_parking_phase_c_rerun.py --out round1_task_result.json`，task_id `8310fbd81fe0444e8a15eb5850a4dcaa`。

| 阶段 | 计划 | 实际 | 修复轮 |
|:---|:---|:---|:---|
| setup | 47 | **47/47** | 第 1 轮 43/47，第 2 轮补 4 → 0 遗留 |
| test | 80 | **80/80** | 第 1 轮 78/80，第 2 轮补 2 → 0 遗留 |
| teardown | 47 | **26/47** | 26/26，未触发修复轮 |
| 合计 | 174 | **153/174**，`yaml_failed=0` | 自查修复 6 |

**teardown 少 21 个不是生成失败**：`_generate_all_yamls` 在 setup 阶段后调用
`_filter_teardown_missing_pres(teardown_tasks, setup_keys)`，把「setup 未产出提取键」的 PRE
对应的 teardown 任务整体剔除（2026-08-27 用户决策 task#10，占位删除必失败故从源头过滤）。
故 47 → 26 是**设计内**行为，`_setup_extract_keys.json` 实际捕获 80 条键。

### 12.2 数据层修复确实落地（护栏实测）

对重生成后的 YAML 逐块解析（`yaml.safe_load`）：

| 检查项 | 结果 |
|:---|:---|
| `parkingPark/saveOrUpdate` 块的 `parkingPark.parkCode` 为空串 | **42 / 42**（非空 0） |
| setup 中带车场 code 回读（`getAllWithDevices` + extract） | **42 / 42** |
| setup 中带规则 code 回读（`getParkingPaymentConfigPage` + extract） | 18 |
| teardown 车场删除 = 裸数组 | 20 / 26 |
| teardown 设备删除 = 逗号串 | 20 / 26 |

→ **根因①（parkCode 示例值）已 100% 修复**，Round 0 的 §十 事实完整进入生成。

### 12.3 框架执行（junit 对比）

`cd PyCharmMiscProject && .venv/Scripts/python.exe run.py -p "testcase/园区基线/智慧停车"`
（**必须用 `.venv` 解释器**：系统 `python` 缺 allure/order 插件，`--alluredir` 会直接报 unrecognized arguments）。

| 指标 | 基线（09:08 junit，82 节点） | Round 1（10:55 junit，88 节点） |
|:---|:---|:---|
| passed | 12 | **4** ↓ |
| failure（断言） | 20 | **40** ↑ |
| setup-error | 45 | **33** ↓ |
| teardown-error | 5 | **11** ↑ |

Sidecar 摄取（`tests/tools/_ingest_phase_c_run_sidecars.py`）：写 **94** 个 `.last_run.json`
（passed 28 / failed 40 / broken 26）。

**基线第一大失败桶 36×「车场维护聚合-添加/修改：msg 期望 success 实际 fail」，Round 1 已消失**
——车场新建本身不再因 parkCode 报错。但被一个新桶取代（下节）。

### 12.4 Round 1 的单一主因：回读 jsonpath 少一层 `parkingPark`

**证据链**：

1. 真实响应：`{"data":[{"parkingPark":{"parkCode":"zhyq702480062052042","parkName":"PK-HOME_kx4eoe",...},"carInDeviceList":[],...}]}`
2. 生成用的路径：`$.data[0].parkCode` —— **少一层 `parkingPark`**。全量扫描生成物：
   **62 处**（57 `extract` + 5 `extract_list`）。
3. 框架 `apiutil.extract_data` 取不到 → 写入占位串 `未提取到数据，该接口返回结果可能为空`（`base/apiutil.py:302`）。
4. 占位串被后续步骤当真实 code 发出 → 全轮 **83 次调用**带占位串，按端点分布：

| 端点 | 次数 | 后果 |
|:---|:---|:---|
| `parkingDevice/add` | 66 | 服务端 `{"retCode":0,"data":"91110"}` |
| `parkingPark/deleteByCodes` | 8 | `{"data":"请求失败"}` |
| `parkingPark/saveOrUpdate` | 5 | HTTP **400** |
| 其余（getPage / List / delete） | 4 | 各种 400/0 |

→ 66 设备 + 8 删除 + 5 车场 ≈ **79 例**由这一个路径缺陷级联而来，占 Round 1 setup-error 的绝大多数。

**归因：仍是 A 类（接口文档提取不全），不是 LLM。**
`api_defs.json` 的 `getAllWithDevices.return` 里 `data` 只写了
`{"name":"data","type":"array","desc":""}` —— **没有任何嵌套结构描述**。
LLM 无从知道元素是 `{parkingPark:{...}, carInDeviceList:[], carOutDeviceList:[]}`，只能按「数组里直接就是对象」猜成 `$.data[0].parkCode`。
对照：`getParkingPaymentConfigPage` 的返回**写了** `data.list[0].code` 层级，其回读 **全部成功**（`pre002RuleCode`/`pre004PaymentCode`/`pre004HomeRuleCode` 均取到真实 `zhyqC…`）。同一条铁律、同一个 LLM，文档写全的对了、没写全的错了 —— 这正是 §十一 结论的第二个独立复现。

### 12.5 其他失败桶

| 桶 | 数 | 初判归属 |
|:---|:---|:---|
| `failed on teardown: TypeError: 'NoneType' object is not iterable` | 6 | 12 个 `.py` 引用的 teardown YAML **磁盘缺失**（见 12.6），`get_testcase_yaml` 返回 None |
| `failed on setup: KeyError 'ruleHomeCode' / 'preChildRuleCode' / 'pre004HomeRuleName'` | 7 | setup 提取键未产出；**疑同 12.4 回读路径**，Round 2 复测 |
| `KeyError: 'code'` / `'parkCode'`（`.py` 内读 `data['…']`） | 6 | 同上，下游取键失败 |
| `carWhiteBlack/delete` 传 `["['zhyqB99614346']"]` → 「参数类型错误」 | 1+ | 形状错：把 list 的 repr 字符串又包了一层 list（同类还有 `update` 的 `code: "['zhyqB31690044']"`） |
| 断言失败（负向用例期望 retCode 0 实得 1 等） | 40 | 待逐例三层协议复核 |

### 12.6 ⚠️ 原则③汇总：本轮发现、原方案未覆盖，需你决策

1. **9 个 09-09 残留 teardown 文件仍在盘上**（`teardown_add_device.yaml`/`teardown_chargeable_rule_flag.yaml`/
   `teardown_coupon_exit_deduct_scope.yaml`/`teardown_delete_coupon.yaml`/`teardown_delete_monthly_car.yaml`/
   `teardown_delete_whitelist.yaml`/`teardown_entry_vehicle_process_rule.yaml`/
   `teardown_main_sub_parking_payment_rule.yaml`/`teardown_one_person_multi_car.yaml`）。
   它们被 `_filter_teardown_missing_pres` 剔出重生成队列，**因而保留修复前的旧内容**，
   但 pytest 仍会发现并执行。实测：**全部 6 处「带壳」车场删除（`{"codes":[…]}`）都出自这批文件**，
   Round 1 重生成的 20 处全部是裸数组。→ 建议：**清理或强制重生成这批**，否则结果里混着 Round 0 前的旧料。
2. **12 个 `.py` 引用的 teardown YAML 磁盘不存在**（`teardown_add_blacklist`/`add_whitelist`/`charge_rule_pagination`/
   `coupon_pagination`/`delete_device`/`edit_charge_rule`/`exit_payment`/`main_camera_device_list`/
   `main_sub_parking_combined_charge`/`monthly_car_management`/`parking_detail`/`temp_parking_charge_calculation`）。
   生成器按设计跳过，直接导致 §12.5 的 6 例 teardown TypeError。→ 建议：**修 `.py` 引用或补齐 YAML**。
3. 上述两项都不在「两轮重跑」方案的原始范围内，**等你决策后再动**。

### 12.7 Round 2 的修正清单（待你确认后执行）

| # | 改动 | 层级 | 依据 |
|:---|:---|:---|:---|
| 1 | `getAllWithDevices` 的 `return.data` 补真实嵌套：元素为 `{parkingPark:{parkCode,parkName,…}, carInDeviceList:[], carOutDeviceList:[]}`，并注明「按 `parkName` 过滤后**精确命中位于 `data[0]`**」 | api_defs + 存档 | §12.4 |
| 2 | 上下文补规则：**凡按名称回读车场，编码路径必须下钻到 `parkingPark`**（只述规则，不含示例） | `phase_c_user_context.md` | §12.4 |
| 3 | `carWhiteBlack/delete` 的 `codes` 形状订正（现生成 `["['zhyqB…']"]`） | api_defs | §12.5 |
| 4 | 处理 §12.6 的 9 + 12 项 | 模块目录 | 待决策 |
| 5 | 重生成 + 复跑，与 Round 1 同口径对比 | — | — |

---

## 十三、Round 2 方案（2026-09-10 用户决策后修订）

### 13.1 用户决策（AskUserQuestion ×2）

| 问题 | 决策 |
|:---|:---|
| §12.6 的 9 残留 + 12 缺失 teardown 怎么处理 | 先选「清理并补齐后再跑」；**追查后该口径作废**（见 13.2），改为 **「补命名约定后一并重生」** |
| §12.7 数据层改动范围 | **先只补 `getAllWithDevices` 一条**（暂不做上下文规则、暂不做 `carWhiteBlack` 形状） |

### 13.2 ⚠️ 口径作废说明：删残留文件是反效果（实测证据）

初版建议「删掉 9 个 09-09 残留 teardown，让它们随 Round 2 重生」**不成立**，理由：

1. 驱动日志逐条证据 —— 被剔除的 21 个任务 = 9 残留 + 12 缺失，**恰好**吻合：
   ```
   🧹 teardown …\teardown_parking_detail.yaml 全部 PRE 缺失提取键，跳过清理任务    （共 21 条）
   ```
2. `_filter_teardown_missing_pres`（`_repair_helpers.py:161`）把「PRE 提取键全为 `__MISSING_KEY__`」
   的 teardown 任务**从任务源移除** → 不生成。故删文件 ≠ 会重生，只是把「盘上残留」变成「磁盘缺失」。
3. 后果：21 个 `.py` teardown fixture 全部 `get_testcase_yaml → None` →
   **teardown TypeError 由 6 例涨到 21 例**，Round 2 反而更差。

**结论：9 个残留文件不删**——补好命名约定后它们会被正常重生成并覆盖。

### 13.3 新发现的第三个根因：隔离克隆 PRE 的 case_name 命名约定缺失

这是本轮最重要的新发现，既非 api_defs 问题、也非 LLM 发挥失常，而是**流水线少一条约定**：

| 环节 | 位置 | 行为 |
|:---|:---|:---|
| ① 隔离消解器 | `agent_components/graph/nodes.py:803` | 被多用例引用的 PRE 逐例克隆：`clone_id = f"{pre_id}_isolated_{tc.id}"` → `PRE-004_isolated_TC-008` |
| ② 键归属 | `_repair_helpers.py:36` `_match_pre_label` | 靠 **case_name 里的 `isolated_TC<数字>`** 把 setup 块归到克隆 id |
| ③ 缺失 | `prompts/extraction_prompts.py` `SETUP_CAPTURE_RULE` | **只写了「必须 input_extract」，一字未提 case_name 命名** |
| ④ 后果 | — | LLM 时对时错：`test_…_PRE004_Isolated_TC072`（归得上）/ `test_PRE004_PaymentConfigAdd_001`（归一化成基 `PRE-004`） |

量化：`_setup_extract_keys.json` 80 条中 **52 条为 `__MISSING_KEY__`，全部是 `*_isolated_TC-*`**。
旁证：`setup_parking_detail.yaml` 内确有该 story 的 setup 块且含 `input_extract`，
**但全被归到基础 `PRE-004`**，克隆 id 一个键都没拿到 → 该 story 的 teardown `_pre_ids` 全由克隆组成 → 整任务被丢。

### 13.4 Round 2 改动清单（确定执行）

| # | 层级 | 改动 | 依据 |
|:---|:---|:---|:---|
| 1 | 数据层（模块 `api_defs.json`） | `getAllWithDevices` 的 `return` 按**既有扁平约定**（对照 `getParkingPaymentConfigPage` 的 `data`→`list`→`code`）补出嵌套层级，使 LLM 可得 `$.data[0].parkingPark.parkCode` | §12.4 |
| 2 | 数据层存档 | 同步 SQLite `documents.api_parameters`（幂等，沿用 `_apply_parking_api_defs_facts.py` 写法） | 09-09 惯例 |
| 3 | prompt（系统侧） | `SETUP_CAPTURE_RULE` 增补**纯规则**一条：每个块的 `case_name` 必须原样携带 PRE 标签（含 `_isolated_TC-xxx` 后缀） | §13.3 |
| 4 | 测试 | ① 扩展 `tests/test_parking_data_layer_facts.py` 护栏 `getAllWithDevices.return` 嵌套；② 新增 `SETUP_CAPTURE_RULE` 含命名规则的断言 | 原则④ |
| 5 | 执行 | 全量重生成（setup/test/teardown）→ 框架复跑 → 与 Round 1 同口径对比 | — |

**本轮不做**（用户明确暂缓）：§12.7 的「上下文补 parkCode 回读下钻规则」、「`carWhiteBlack/delete` 形状订正」。

**预期**：①`parkCode` 回读恢复 → 占位串级联（83 次调用）消失；②克隆键就位 → 52 条 `__MISSING_KEY__` 大幅收敛
→ 21 个被剔除的 teardown 复活、12 个缺失文件生成 → teardown TypeError 归零。

---

## 十四、Round 2 执行结果与逐例归属（2026-09-10 12:18–12:20）

> 逐例明细：`_phase_c_rerun_2026-09-10/round2_per_case_attribution.md`
> 证据：`round2_junit.xml`、`round2_pytest.log`（547 次请求实收发）、`_setup_extract_keys.json`。

### 14.1 重生成事实

| 阶段 | 结果 |
|:---|:---|
| setup | **47/47** |
| test | **80/80**（自查修复 2） |
| teardown | **45/45** |
| 合计 | **172/174**；`yaml_failed=0` |
| 引用完整性 | **全部 YAML 引用存在**（Round 1 有 12 处 MISSING） |
| 回读路径 | `$.data[0].parkingPark.parkCode` **70 处**（残留错路径 1 处） |

### 14.2 框架执行对比（同口径）

| 指标 | 基线 | Round 1 | Round 2 |
|:---|:---|:---|:---|
| passed | 12 | 4 | **12** |
| failure | 20 | 40 | **42** |
| setup-error | 45 | 33 | **25** |
| teardown-error | 5 | 11 | **4** |
| junit 节点 | — | 88 | 83 |
| 占位串下游调用 | — | **83** | **2** |
| 提取键全缺失条目 | — | 52/80 | **7/94** |

**R1 主因（回读路径少一层）已清除**：占位串调用 83 → 2，retCode=0 调用 93 → 102。

### 14.3 关键口径更正

- 服务端**业务失败返回 HTTP 400**、body 仍为业务 JSON → 「状态码 400」**不是**链路故障。
  证据：`test_sub_parking_without_parent_negative` 的 `retCode=0` 断言**通过**，唯一失败是状态码。
- 业务错误文案在 **`$.data`**，非 `$.msg`。
- `parkCode:'ALL'` 是**合法通配值**（存在正向用例），以其为前提的负向用例前提无效。

### 14.4 42 例归属（详见归属报告 §2）

| 编号 | 主因 | 归属 | 例数 |
|:---|:---|:---|:---|
| R1 | 负向用例一律断言 HTTP 200 | 测试用例缺陷（生成器） | 4（+4 混合） |
| R2 | 错误文案断言路径 `$.msg` vs `$.data` | 测试用例缺陷 | 3 |
| R3 | 臆造业务文案 | 测试用例缺陷 | 2 |
| R4 | 名称当 `code` 硬编码 | 测试数据 / 用例缺陷 | 6 |
| R5 | 集合当标量断言 | 测试用例缺陷 | 2（+2 混合） |
| R6 | 删除通道形状 `{"codes":[…]}` → 参数类型错误 | 生成器契约缺口（§12.7③ 暂缓） | 5 |
| R7 | 提取键未语义化（裸 `code`）/ 来源未产出 | 生成器产出缺陷 | 15 |
| R8 | 产出非法 JSONPath | 生成器产出缺陷 | 1 |
| R9 | 取值未唯一化 / 臆造 parkCode | 测试数据 | 4（重叠） |
| R10 | 输入确为非法值、服务端未拦截 | **接口缺陷候选** | 6 |

**连通性层 0 例**（547 次请求全部有响应）。**生成侧 35 例，接口缺陷候选 6 例，待确认 2 例。**

**failure 由 40 升至 42 的解释**：Round 1 的失败大量是 setup 阶段崩溃的连带；Round 2 setup 修好后用例真正跑到断言，把原本被掩盖的**用例产出瑕疵**暴露出来——是暴露面变大，非退化。

### 14.5 收口

按用户「只重跑两轮」约束，**本轮到此收口**，不触发第三轮重生成。
生成侧第一梯队（R7 提取键语义化规则、R1 负向状态码预期规则）可由**纯规则补丁**解决，不含取值示例，符合「prompt 只写规则、接口事实维护数据」约束；留待下一批决策。

---

## 十五、Round 3 方案（按更正后归因，经用户授权："按更正后重跑一轮"）

### 15.1 归因更正（撤回 Round 2 报告 §2 中"需扩 schema"的判断）

用户指正成立，实证核验后**撤回 A 类"schema 缺位"定性**：

| 原判 | 核验结果 | 证据 |
|:---|:---|:---|
| "缺接口文档" | ❌ **接口覆盖 100%**（39/39） | YAML 实际调用 39 个 url，api_defs 全有；先前报的 5 个"缺失"是 `${get_extract_data(...)}` 模板串造成的误判 |
| "状态码无位可写" | ❌ **`status_code: 200` 是生成器写进 YAML 的** | 80 个 test_data.yaml 中 63 个含该断言 |
| "文案落点无位可写" | ❌ **`return[].desc` 是自由文本**，写进去即可 | Round 2 修 parkCode 只改 `data.desc` → 回读路径 70 处正确、占位串 83→2 |

**结论**：返回值差异**靠补 `return`/`body` 的 `desc` 说明即可解决**，无需扩 schema。

同时按实收发**修正 Round 2 归类的两处误差**：
- `test_delete_nonexistent_device_code_negative`：`{"codes":"DEV-NOT-EXIST"}` **形状正确**（desc 已写"逗号分隔的设备编码字符串"）→ 实为 **R1**（状态码预期），非 R6。
- R6 实为 **4 例**（黑白名单删除 ×2、优惠券删除 ×2），三者都传了 JSON 数组而被拒"参数类型错误"。

### 15.2 数据层改动（模块 `api_defs.json` + SQLite `documents` 双写）✅ 已执行 D1–D8

| # | 接口 | 字段 | 改动 | 对症 |
|:---|:---|:---|:---|:---|
| D1 | `carWhiteBlack/delete` | `body.codes` | 补**实测形状**（现仅"黑白名单编码集合"） | R6 (2) |
| D2 | `parkingCouponIssue/deleteByCodes` | `body.codes` | 补**实测形状**（现仅"优惠券编码集合"） | R6 (2) |
| D3 | `parkingPark/saveOrUpdate` | `body.parkingPark.bindPayCode` | 补"必须传**回读到的真实编码**，不是规则名称" | R4 |
| D4 | `parkingPaymentConfig/update` | `body.code` | 补同上约束 | R4 |
| D5 | `carWhiteBlack/update` | `body.code` | 补同上约束 | R4 |
| D6 | `parkingCouponIssue/update` | `body.code` | 补同上约束 | R4 |
| D7 | `parkingDevice/add` | `return.data` | 补"本接口**不回传**设备编码；编码需经 `getByCode`/`getPage` 回读" | R7 |
| D8 | `parkingDevice/getPage` | `return` | 补 `data.list[].…` 嵌套（对齐 `carWhiteBlack/getPage` 现有写法） | R5 |
| D9 | ~~`parkingPark/getAllWithDevices` 等~~ | ~~`return.data.desc`~~ | **本轮不做**：与原 D9 同义的约束改由**规则 R9**承担（见 15.3），避免在 39 个接口上重复写同一条文案 | R2 (3) |

> **D9 撤并说明**：D9 原计划给每个接口的 `return.data.desc` 都补一句"业务失败时文案在 `data`"。核验发现这是**跨接口通用的服务口径**（不是某一个接口的字段事实），按"接口事实维护数据、通用口径维护规则"的边界，**改由 R9 规则承担**，`api_defs.json` 不动。故本轮实际落地 **D1–D8**。

> ⚠️ **D1/D2 的形状必须先实证**（Round 0 只验过 `parkingPark/deleteByCodes` 裸数组、`parkingDevice/deleteByCodes` 逗号串）。按"接口事实一律维护数据"，**不猜**——先跑只读+回放探针确认。

### 15.3 规则层增补（`phase_c_user_context.md`，**纯规则表述、无示例**）✅ 已执行 R8–R12

| # | 规则 | 对症 |
|:---|:---|:---|
| R8 | 本服务**业务失败返回 HTTP 400**；负向用例不得断言状态码 200 | R1 (5) |
| R9 | 业务失败时错误文案在 `data` 字段，`msg` 固定为 `fail` | R2 (3) |
| R10 | `input_extract` 键名必须**带 PRE 前缀语义化**，禁止裸字段名（如 `code`） | R7 |
| R11 | 断言**集合元素**必须带下标；不得把集合本身当作标量断言 | R5 (2) |
| R12 | 负向用例的期望**只能来自文档已声明的校验**，禁止臆造文案 | R3 (2) |

### 15.4 测试设计（原则④）

- `tests/test_parking_data_layer_facts.py` 增补：**D1–D8** 逐字段护栏（含**文件与 DB 存档一致**断言，防单边写入）。
- 同文件增补：R8–R12 的**规则文本存在性**断言 + **反例断言**（确认规则段不含取值示例，延续既有 `test_context_contains_no_concrete_examples` 模式）。
  > 说明：R8–R12 是写进**模块目录**的 `phase_c_user_context.md`（运行时按模块读入），护栏与 D 系列同置于该文件，不另开 `test_three_stage_generation.py`。

### 15.5 执行顺序（原则⑤：先文档后实际）

1. **探针** ✅ 已完成：确认 D1/D2 删除形状（只读 + 一次回放），证据落 `_phase_c_rerun_2026-09-10/round3_probe_delete_shapes.json`
2. **改数据** ✅ 已完成：模块 `api_defs.json`（备份 `api_defs.json.20260910155014.bak`）→ `--sync-db` 同步 SQLite `api_parameters`/`api_returns`（`rows=9`）
3. **改规则** ✅ 已完成：`phase_c_user_context.md` 增补 R8–R12（R6 同步扩到黑白名单/优惠券删除形状）
4. **跑测试** ✅ 已完成：`tests/test_parking_data_layer_facts.py` **46 passed**（原 27）
5. **重生成** + **框架执行** + 同口径对比 ⏳ 进行中

### 15.6 本轮不做（原则③汇总，供你决策）

| 项 | 原因 |
|:---|:---|
| **`parkingPark/getDetail/{parkCode}` 相关用例** | Round 0 已实证该端点**本身返回「请求失败」**（接口缺陷，非文档缺口）→ 建议**排除出重跑目标**，而非改数据。影响 4 个用例目录 |
| `parkingDevice/getPage` 与 `carWhiteBlack/getPage` 的 `return` **命名约定不一致**（前者扁平、后者点号 `data.list[].code`） | 同文件内两套约定并存，属**结构性问题**，改动面大，单列待决策 |
| 3 个无法删除的探针车场 | 已登记，不再追 |

---

## 十六、Round 3 执行结果（2026-09-10）

> 逐例归属详见 `_phase_c_rerun_2026-09-10/round3_per_case_attribution.md`；
> 取证 `round3_junit.xml` / `round3_pytest.log` / `r3_fail_forensics.txt`（58 侧车）。

### 16.1 重生成事实

| 项 | Round 2 | Round 3 |
|:---|:---|:---|
| YAML 产出 | 172/174 | **172/174**（setup 47/47 修复 1；test 80/80 修复 5；teardown 45/45） |
| 引用完整性 | 全部存在 | **全部存在** |
| `_setup_extract_keys.json` 全缺失条目 | 7/94 | **0/94** |

task_id `f6cfd751b95445a997a0d567f1ac293c`。

### 16.2 框架执行（同口径全量 `python run.py -p testcase/园区基线/智慧停车`）

| 指标 | 基线(09:08) | Round 1 | Round 2 | **Round 3** |
|:---|:---|:---|:---|:---|
| passed | 12 | 4 | 12 | **18** |
| failure | 20 | 40 | 42 | **38** |
| setup-error | 45 | 33 | 25 | **24** |
| teardown-error | 5 | 11 | 4 | **2** |
| 占位串调用 | — | 83 | 2 | **0** |
| 提取键全缺失 | — | 52/80 | 7/94 | **0/94** |

**口径 B（排除 getDetail 相关）**：failure **35** / 79 节点。
（4 例 getDetail 相关中 `test_edit_parking_pk_home_save_echo_positive` 本轮 PASSED，
余 3 例死于裸键 `KeyError`，尚未触达 getDetail 端点。）

> **getDetail 排除的执行口径说明**：`run.py` 用 `parse_known_args()` 丢弃扩展参数，
> 无 pytest 参数透传；按**最小改动原则未改框架**，故仍跑全量，仅在**归因基数**上给出含/不含两个口径。

### 16.3 归属结论

| 归属 | 例数 | 构成 |
|:---|:---|:---|
| **生成侧** | **54** | failure 28（裸键残留 12 / 前缀键不匹配 2 / 断言形态 6 / 前置取值 8）+ setup 级联 24 + teardown 2 |
| **接口缺陷候选** | **10** | 输入构造正确、服务端未按文档拦截（含 400→200、success 反例未拦） |

> **与 Round 2 可比的 failure 节点口径**（仅比 failure 38 vs 42，不含 setup/teardown）：
> 生成侧 **35 → 28**（↓）；接口缺陷候选 **6 → 10**（↑）。
> 后者的上升是**识别能力提升**而非缺陷变多——R8 生效后负向用例改断言 400，
> 服务端不拦截才显形；Round 2 时它们被"期望 200"的错误预期掩盖。

### 16.4 规则有效性的分段核验

| Round 2 主因 | Round 3 状态 |
|:---|:---|
| R1 负向断言 HTTP 200 | ✅ 完全消除（负向现一致断言 400） |
| R2/R9 文案断言 `$.msg` | ✅ 完全消除（现取 `$.data`） |
| R6 删除形状错 | ✅ 完全消除 |
| R7/R10 提取键 | 🟡 setup 侧消除（0/94）；**测试 YAML 侧残留 12 处裸键** |
| R5/R11 集合当标量 | 🟡 部分残留（4 例） |
| **R4 名称当 code** | 🔴 **未消除，且是本轮第一大主因** |

### 16.5 关键发现：缺口在"遵循"，不在"说明"

`parkingPark/saveOrUpdate` 全量 **79 次调用、47 次失败**。按实收请求里 `bindPayCode` 的**实际形态**分类：

| 类 | `bindPayCode` 实收形态 | 次数(79) | 失败 |
|:---|:---|:---|:---|
| B | 规则**名称字面量** `'RULE-HOME'`(18) / `'RULE-CHILD'`(8) / `'RULECHILD_wcwrj9'`(2) | **28** | 28 |
| D | 空串 `''` | 10 | 10 |
| **E** | **列表被 `str()` 序列化成字符串** `"['zhyqC…']"` | **4** | 4 |
| A | 标量编码 `'zhyqC…'`（**正确形态**） | 34 | 2（车场名重复 / 负向用例） |
| C | 字段不存在 | 3 | 3（子场负向用例，预期内） |

**44/47 同属一条根因：`bindPayCode` 最终没以"标量编码"形态送达服务端。**
形态正确的 34 次里 **32 次创建成功**。

### 16.5.1 「回读」丢在哪一步——两个断点，都已定位

**断点①：38 次压根没生成回读链。**
写死名称的 **10 个 setup 连"建规则"都没做**——无 `parkingPaymentConfig/add`、无回读步。它们把规则
内联进聚合接口 `parkingPark/saveOrUpdate` 的 `paymentConfig` 块，却在**同一个 `parkingPark` 块里**
要一个服务端生成的 `bindPayCode`——无处可取，遂回退成**测试计划里给的规则名**。

**断点②：4 次生成了链、取到了值，但在框架替换层丢了类型。**
- `extract_list` 产出的值是**列表** `['zhyqC16859653']`（全量 12 次列表形态产出）；
- `common/debugtilk.py:23` `get_extract_data(key)` 在 `randoms=None` 时**原样返回列表**；
- `base/apiutil.py:99` `str_data.replace(ref_all_params, str(extract_data), 1)`
  把列表 `str()` 成字符串 `"['zhyqC16859653']"`；
- YAML 里写的是**无引号**的 `bindPayCode: ${get_extract_data('pre004PayCode')}`，
  于是该字符串被服务端当成编码 → `绑定的临停收费规则不存在或类型不正确`。
- **正确写法**：`${get_extract_data('pre004PayCode', 1)}`（`randoms=N` → `data[N-1]` → 标量）
  或 `${split_extract_data('pre004PayCode', 0)}`。

> **反证回读链本身可用**：全量 **47 次 `getParkingPaymentConfigPage` 调用**，
> `$.data.list[0].code`（倒序第一）取回的正是刚建的规则；`setup_parking_detail.yaml`
> 整链走通（add → 回读 → 绑定）并创建成功，全量 32 次成功创建亦为证。

故本轮结论修正为：接口覆盖 **100%（39/39）**、`body[].desc`/`return[].desc` 均已有位可写
—— **缺的不是接口说明**；断点全在**产出侧**（未生成链 / 引用漏索引），继续加 prompt/desc 收益递减。

### 16.6 累计三轮后的推荐下一步（供决策，原则③汇总）

| 选项 | 说明 | 预判 |
|:---|:---|:---|
| A. 产出侧强制校验（推荐） | 生成后校验 `bindPayCode` 的形态/键存在性/前置链，命中即判失败重生成。**规则清单见 §16.7** | 直击 44 次复发的两个断点 |
| B. 继续补规则/desc | 再写 R13/R14 | 收益递减，Round 3 已证（D3 的 desc 已写明，生成器未遵循） |
| C. 收口 | 以 Round 3 为终态，交接口缺陷清单 | 生成侧 54 例留存 |

### 16.7 产出侧校验方案（2026-09-10 用户决策后定稿）

**先回答"要不要在用例里加说明"：不要。** 两条理由——
1. **已证无效**：D3 已把该字段 `desc` 写成"必须是经查询回读到的真实编码"，生成器照样写死 `RULE-HOME`；
2. **说明管不了这个缺陷**：断点①是"缺一整个前置步骤（规则 add + 回读）"、断点②是"跨步引用的**类型**"，
   都是**步骤序列级**的结构问题，不是单字段能描述清楚的——只有拿到整份 setup 才能判。

**校验器落点（更正 §16.7 初稿）**：不是 `yaml_gen.py` 内联，而是**单独管理的校验器包**
`agent_components/validation/yaml_validator.py`（README §项目结构：`case_validator.py` = Phase B /
`yaml_validator.py` = Phase C 产物层）。理由：`yaml_gen.py:466-513` 已有完整后校验管线
（问题落盘 `_post_validation_issues.json` → P0/P1 注入追加修复轮），`yaml_gen.py` 只是**调用点**。
新增扫描函数挂在 `yaml_gen.py:471`（`_scan_missing_key_refs` 旁）即可，**回炉机制零改动**。

**新增函数形状**：`_scan_extract_value_forms(output_base)`，**必须直接遍历顶层 list**。
不能加进 `YamlPostValidator.validate_all`：`validate_all` 只认 `{data: [...]}` 形状（见 §16.8），
对生成器实际写盘的顶层 list 全部 `continue`，加了也是死代码；且 V4 要跨 step 看序列，逐 step 检查看不到。

**校验对象**：`setup_data/setup_*.yaml` 中 `parkingPark/saveOrUpdate` 步骤的 `json.parkingPark.bindPayCode`。

| # | 规则 | 本轮采纳 | 对应断点 |
|:---|:---|:---|:---|
| V1 | `needPay=1` 时 `bindPayCode` 必须存在且非空串 | ✅ 实现 | D 空串 10 次 |
| V2 | 值必须是 `${get_extract_data('key'[,N])}` / `${split_extract_data('key'[,N])}` 形态，**不得任何字面量** | ✅ 实现 | B 字面量 28 次 |
| V3 | `key` 必须由本 setup 的**更早步骤**声明（`extract`/`extract_list`/`input_extract`） | ✅ 实现 | 键不存在 |
| V4 | `key` 的声明必须来自**读回链**：本 setup 存在 `parkingPaymentConfig/add` + `getParkingPaymentConfigPage` 两步且 add 在前，key 由后者声明 | ✅ 实现 | 断点①（未生成链） |
| V5 | 若 `key` 由 `extract_list` 声明，引用**必须带索引**——裸引用会在 `apiutil.py:99` 被 `str()` 成 `"['…']"` | ✅ 实现 | 断点②（E 类 4 次） |
| V6 | 泛化：`extract_list` 键的裸引用检查推广到**全模块全字段** | 📌 **只登记不实现** | 防同族复发 |

**命中任一 → 判生成失败**：复用现有 P0/P1 注入通道进追加修复轮（`yaml_gen.py:483-505`，按 `severity` 消费，
仅排除 `missing_extract_key`），无需新增机制。

**已知限制（记入，不本轮解）**：追加修复轮受 `yaml_gen.py:486` `result["rounds"] < config.YAML_REPAIR_ROUNDS`
闸门限制；若正规修复轮已耗尽，V-rules 只能落进 `_post_validation_issues.json` **报告而不回炉**（与 D4 同待遇）。

### 16.8 ⚠️ 原则③汇总：发现的涉及项（另立一项）

**`YamlPostValidator.validate_all` 对当前产物形状失明。**
入口 `yaml_validator.py:45` 的 `if not isinstance(data, dict) or "data" not in data: continue` 只认 `{data: [...]}`；
而生成器实际写盘为**顶层 list**（`setup_delete_device.yaml:1` 即 `- baseInfo:`）。
→ `validate_all` 的 4 个检查项（`delete_body_wrapper` / `assertion_dynamic_key` / `assertion_op_key` /
`malformed_assertion`）**全部休眠**。`_scan_missing_key_refs` docstring 已记录该"格式错位"（2026-08-27），
但当时只给 D4 单独绕开，未回修入口本身。

**用户决策（2026-09-10）**：顺带修（让 `validate_all` 兼容顶层 list），但**另立一项**，不与 V1–V5 混提。

## 十七、V1–V5 校验器落地执行（2026-09-10）

用户决策：**V1–V5 实现、V6 只登记**；`validate_all` 形状失配顺带修但另立一项；修完再跑一轮（= Round 4）。

### 17.1 规则标定（实现前先用真实产物验误报面）

拿 Round 3 真实产物 47 个 setup 跑规则，并与 Round 3 实际失败的 setup 清单对撞：

| 版本 | 命中文件 | 真阳性 | 误报 | 漏报 |
|:---|:---|:---|:---|:---|
| 初版（V1 闸门误加 needPay、V4 取「第一个分页步」） | 16 | 14 | **2** | 6 |
| **定稿** | **20** | **20** | **0** | **0** |

**两处误报与六处漏报的根因（都已修正）：**

1. **V4 误报 2 例**（`setup_charge_rule_parking_type_match` / `setup_device_list_query`）：
   一个 setup 可含**多组** add/回读对，我用 `next()` 取「第一个分页步」比对，把第二组的键判成非回读。
   定稿改为「**声明该 key 的那一步本身是分页回读步**，且其之前存在 add 步」。
2. **V1 闸门错**：初版把 `needPay` 闸门加到**全部**规则上。实测三条事实：
   - `mainPark=True` + `needPay=0` + 空串 → 服务端拒（`主场类型必须要绑定计费`）→ V1 闸门是 **mainPark 或 needPay**；
   - `mainPark=False` + `needPay=0` + **字面量** `'RULE-CHILD'` → 照样拒 → **V2 不加任何闸门**；
   - `mainPark=False` + `needPay=0` + 空串 → 合法（子场未开计费）→ 不报。

定稿规则表见 §16.7；标定探针 `tests/tools/_probe_v1v5_calibration.py`（复核命令：
`python tests/tools/_probe_v1v5_calibration.py`）。

### 17.2 改动清单

| 文件 | 改动 |
|:---|:---|
| `agent_components/validation/yaml_validator.py` | 新增 `_BIND_CHAIN` 常量 + `_scan_extract_value_forms()` + `_check_bind_forms_in_setup()` + `_bind_issue()`（V1–V5，约 200 行） |
| 同上 | `validate_all()` 形状兼容顶层 list（§16.8 的涉及项，**另立一项**） |
| `agent_components/generators/yaml_gen.py` | 导入 + 在 `_scan_missing_key_refs` 旁注册调用 + V 规则汇总日志 |
| `agent_components/generators/yaml_gen.py` | **修后校验修复轮的死代码闸门**（见 §17.3） |
| `tests/test_bind_value_form_validator.py` | 新增 24 个用例（V1–V5 逐条正反向 + 多组 add/回读对回归 + 形状/范围集成） |
| `tests/test_regression_post_validator.py` | 新增 `test_top_level_list_shape_scanned`（锁定形状回归） |

### 17.3 ⚠️ 顺带发现的第二个死代码：后校验修复轮从未执行过

修 V1–V5 时发现：光判 P0 没用 —— `yaml_gen.py:500` 的注入闸门
`if _fixable and result["rounds"] < config.YAML_REPAIR_ROUNDS:` **恒为假**。

- `_run_yaml_rounds` 里 `rounds_run = round_no`，**第 1 轮就是全量轮** → 只要生成了文件 `rounds_run ≥ 1`；
- `_merge_stage_results` 的 `rounds` **取各阶段 max** → `result["rounds"] ≥ 1`；
- `YAML_REPAIR_ROUNDS` 默认 **1** → `1 < 1` 为假。

**结论：后校验（含此前的 D4 之外所有 P0/P1）从未真正回炉过**，只写进 `_post_validation_issues.json`。
这与 §16.8 的 `validate_all` 形状失配同族 —— 都是「守卫条件永远满足不了」。

**修法（最小改动）**：给后校验修复轮**独立预算** `_post_repair_budget = config.YAML_REPAIR_ROUNDS`，
上限仍受同一配置约束，不再与阶段修复轮共享计数。

**链路核验（该分支此前从未跑通，逐环确认）**：
`_scan_extract_value_forms` → `_fixable` → `_affected_tasks`（`abspath` 与 `all_tasks` 匹配，
setup 路径同为 `output_base/feature_en/setup_data/setup_*.yaml`）→ `post_check_issues=_fixable`
→ `_format_post_issues_for_prompt`（消费 `check/current/expected/fix_hint`，与我的结构同构）
→ 注入修复轮 prompt 的 `### 后校验问题`（`yaml_gen.py:207`）。**全链路通。**

### 17.4 测试

```
tests/test_bind_value_form_validator.py + test_regression_post_validator.py  42 passed
+ test_three_stage_generation / test_yaml_ref_check / test_regression_import_smoke  93 passed
全量 tests/  881 passed / 9 failed
```

**9 个失败均为既有失败，与本轮改动无关**（已用 `git stash` 撤掉本轮改动复验：同样失败）。
断言内容为 prompt 文案（`数据唯一化`）与 SQL 中 URL 是否带 `/park-energy-electric-web` 服务前缀 ——
属 09-09 服务前缀改造与 prompt 调整的遗留，登记待另修。

### 17.5 `validate_all` 形状修的误报面

修复前先量影响：对 Round 3 真实产物跑修好的 `validate_all` → **0 命中**。
即复活的 4 个检查项对当前产物零误报（合理：R6/R11 已在 prompt 层消除了那几类畸形），可安全启用。

## 十八、Round 4 执行结果（2026-09-10，用户指令「修复后再跑一轮」）

**首轮校验器实测：V1–V5 判 P0 → 后校验回炉轮真的执行了。**

```
⚠️ V1–V5: 30 处车场绑定值 bindPayCode 形态非法（21 个 setup），已判 P0 进修复轮
🔧 后校验发现 30 个 P0/P1 问题，追加一轮修复（21 个文件）
✅ 完成: 21/21，修复 1，仍失败 0，轮次 2
```

### 18.1 回炉效果：30 处 → 残留 20 处

对重生成后的产物**再跑一次** `_scan_extract_value_forms`：

| | 命中处数 | 涉及 setup |
|:---|--:|--:|
| 回炉前 | 30 | 21 |
| **回炉后残留** | **20** | **13** |

残留分类：`bind_pay_code_literal` 8 / `bind_pay_code_undeclared` 8 / `bind_pay_code_empty` 4。

**一次回炉只消掉约 1/3。** 修复轮 prompt 里已带 `当前/期望/修复指引` 三段（§17.3 核验过链路），
LLM **照旧改不干净** —— 这是"遵循一致性"缺口比 Round 3 更硬的实证：
Round 3 证明"给了规则不遵循"，Round 4 证明**"把违规点逐条指到脸上仍不遵循"**。

### 18.2 框架结果：同口径节点数已变，不能直比总数

| 指标 | R3 | **R4** |
|:---|--:|--:|
| junit 节点 | 82 | **86** |
| passed | 18 | **16** |
| failure | 38 | **40** |
| setup-error | 24 | **23** |
| teardown-error | 2 | **7** |

> ⚠️ 节点数 82→86：本轮重生成产出了**不同的用例集**（新增 `setup_sub_parking_temp_permit_and_cross` 等），
> 故 passed 等**绝对数不可直接与 R3 比**。且 R3→R4 的差异混入了 LLM 重生成的随机性，
> **不能把变化全部归因于 V1–V5**。

### 18.3 决定性口径：服务端拒绝文案计数（两轮同一统计方法）

| 服务端拒绝文案 | R3 | R4 | 变化 |
|:---|--:|--:|--:|
| **绑定的临停收费规则不存在或类型不正确**（V1–V5 的目标根因） | 38 | **26** | **−12** |
| 开启计费后必须绑定临停收费规则（空串） | 13 | 16 | +3 |
| 主场类型必须要绑定计费 | 6 | 6 | 0 |
| 此车场名称已存在 | 2 | **10** | **+8** |
| 车场主/子场信息不能为空 | 2 | 1 | −1 |
| 主场必须存在 | 1 | **10** | **+9** |
| **合计** | **62** | **69** | **+7** |

**读法**：
1. **目标根因确实下降 12 次** —— V1–V5 不是无效的；
2. **但冒出两个新类别**：`此车场名称已存在` +8、`主场必须存在` +9；
3. 合计 **62 → 69（+7）**，车场创建问题**没有解决，只是换了形态**。

`此车场名称已存在` 上升疑与回炉轮重生成 setup 时的命名竞争/重复创建有关；
`主场必须存在` +9 是子场父码相关的新暴露。两者**均需单独查**，本轮未定位。

### 18.4 结论

| 问题 | 答案 |
|:---|:---|
| V1–V5 校验器是否有效？ | **有效**：精准命中（20/20 标定）、判 P0、成功触发回炉（此前该分支是死代码） |
| 是否解决了车场创建根因？ | **没有**：目标根因 −12，但新增两类 +17，合计 +7 |
| 单靠"校验 + 回炉"够不够？ | **不够**。LLM 收到逐条指正后仍修不干净（30→20 残留） |

**下一步只剩两条路**（供决策）：
- **A. 确定性修补**：校验器不只报错，而是**直接改写 YAML**（把字面量/空串替换为
  `${get_extract_data('键', 1)}` 并**补插 add+回读步骤**）。代码接管，不依赖 LLM 遵循 —— 与既有
  "三层防御 = prompt + schema + 代码接管"的**第三层**一致。
- **B. 收口**：承认 LLM 生成侧到此为止，出接口缺陷清单交人工。

（另需单独排查：`此车场名称已存在` 与 `主场必须存在` 两个新上升类别。）
