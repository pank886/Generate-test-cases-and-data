# 2026-09-08 URL 校验白名单口径修复：主模块接口全量直取（方案C）

> **状态：已取证、方案 C 已定、施工中（2026-09-08）。** 用户决策链：
> ①生产 10:22 智慧停车生成被质量门禁终止（两轮均 <50%）→ ②排查「为何只有 60 条用例」与
> 「为何生成不存在的接口地址」→ ③结论：**地址非幻觉、prompt 无示例；Hop2b 对主模块也做
> 语义 top-K（39 条绑定接口截成 22），api_definitions 与 URL 白名单都是截断快照** →
> ④用户定方案 C（2026-09-08）：**主模块直接关联接口应全量直取，只有关联模块绑定的接口
> 才需要语义分析**。
> 原则：①先确认②最小改动③连带汇总④方案含测试、改完运行⑤文档先行。

## 一、现象（2026-09-08 生产 10:22 UTC，trace 0ecbc6d0…）

智慧停车（用户新增 mock 接口后）Excel 计划生成：两段式正常跑完（无 LengthFinish、无空
content，thinking=enabled 修复生效），但**校验被整批误报拖死**：

- 首轮：`两段式生成完成: 16 前置, 60 用例` → `校验失败 55 行 / 共 60 行, 通过 21` → `<50%` 触发全量重生成
- 重试：`两段式生成完成: 24 前置, 60 用例` → `校验失败 64 行 / 共 60 行, 通过 20` → `<50%` → **终止生成**

拦截原因**单一**：`被拦截：疑似URL拼写错误`，示例
`/park-access-parking-rule-new/mock/delAllForMock（未匹配 api_definitions 中任一真实接口）`。
被拦的不只 mock：`/parkingPaymentConfig/add`、`/parkingPark/saveOrUpdate` 等**纯业务真实接口也被拦**。

## 二、问题 2 取证：接口地址不是幻觉，prompt 也没有示例

被拦截地址**是真实存在的接口**，来自 09:08 新增入库的 `api (1).md`（浙大停车测试Mock接口，
11 条，均带 `park-access-parking-rule-new` 前缀）。证据：

| 事实 | 证据 |
|:---|:---|
| `/park-access-parking-rule-new/mock/delAllForMock` 真实存在于 `documents`（`POST` 同路径，upload 09:08:03） | SQLite 查询 |
| 智慧停车绑定接口 = **39 条**：0907 业务 28 + api(1).md mock 11；全部索引进 ChromaDB `doc_search`（doc_id 与 sqlite `documents.id` 一致） | bindings + chroma 扫描（0907:28 / mock:11） |
| 模型知道这些 mock 接口的来源是 **module_analysis 预分析文本**（09:12 重抽取 v2）：明确写了「`delAllForMock` 清理数据 → 准备 `monthlyCar/add`、`carWhiteBlack/add`、收费规则/车场/设备 → `access/enter`、`access/exit`…」等测试辅助链 | `module_analysis.api_analysis`（9529 字符） |
| prompt 文本无示例字面量；这批 URL 只出现在「数据」里（module_analysis / 接口文档），不在任何 prompt 常量 | `test_no_example_literal` 等回归 + 逐源 grep |
| Phase 1 分析输出**同时含 full 与 short 两种 mock 引用**（full 出现在步骤/前置清单，short 出现在叙述）；Phase 2 忠实采用 full 形式 | thinking_trace 18:28 / 18:34 analyze 块 |

**根因（非模型幻觉）**：URL 有效性校验白名单 `api_urls` 取自 `state["api_definitions"]`
（`nodes.py:306-310`），而 `api_definitions` 是 **Hop2b 本次检索的 top-K 快照 = 22 条**。
39 条绑定接口里，mock 11 条 + 若干业务接口**没进前 22** → 模型在 module_analysis/产品上下文里
正确引用了它们，校验器却以「未匹配 api_definitions」整批判假。**白名单口径窄于模块真实接口集**，
`api_definitions` 只是给模型的上下文裁剪，不该充当「真实接口全集」判据。

## 三、问题 1 取证：为什么只有 60 条用例，没到 90+

| 运行 | 库内已入库接口 | Hop2 检索到(api_definitions) | Phase 1 枚举用例 | module_analysis |
|:---|:---|:---|:---|:---|
| 08:57（撞 8192 那次，92 用例） | 28 业务（0907 于 08:35 入库） | **22** | 92 | 无（本模块预分析 09:12 才生成/更新） |
| 10:22（本次） | 28 业务 + 11 mock（09:08 入库） | **22** | 60 | **09:12-09:14 重抽取 v2（含 mock、以车场隔离场景为中心）** |

要点：
1. **两次 api_definitions 都是 22 条**。09:08 新增的 11 条 mock **全部落在 top-K 之外，未进模型上下文** →
   「加了接口 → 用例应增加」在现检索机制下不成立（上下文输入没变）。
2. 用例数是 **Phase 1 枚举结果**：60 TC 标记 ↔ 最终 60 用例，Phase 2 忠实转换无丢失。
   08:57 无（旧）module_analysis，模型按 22 条接口自由铺开写了 92；本次 Phase 1 被 09:12 重抽取的、
   以 mock/车场隔离场景为中心的预分析**锚定**，按场景点只写了 60。
3. mock 是**测试脚手架接口**（清理/准备/模拟入场出场），即便进入上下文，主要作前置与断言支撑，
   天然不是「每条独立开花」的用例源。

## 四、根因小结

> **URL 校验白名单取「检索快照 22 条」而非「模块真实绑定接口全集 39 条」→ 整批误报
> 「疑似URL拼写错误」→ 质量门禁两轮 <50% → 终止。** 与用例数量无关；与 prompt 是否有示例无关。
> 白名单窄的**更上游根因**是 Hop2b 对主模块也做语义 top-K：方案 C 从源头让 `api_definitions`
> = 主模块绑定全量，白名单随之自然变全。

## 五、修复方案（方案 C 已定：主模块接口全量直取）

### 方案 C（已定）：Hop2b 主模块直接关联接口「全量直取」，语义检索只用于关联/公共模块

用户 2026-09-08 指出架构本意：**「本模块直接关联的接口 → 全量获取；只有关联模块绑定的接口
才需要语义分析」**。Hop2b `_retrieve_related_data`（`agent_components/retrievers.py:493-545`）
目前对 `confirmed_module` 也走 `_search_api_defs`（ChromaDB 语义 top-K）——主模块 39 条绑定
接口被相似度截成 22，mock 11 条 + 部分业务接口落在 `api_definitions` 之外。

改动（仅 `retrievers.py`，nodes.py 白名单**不改**）：
1. 新增静态 `_load_api_defs_full(doc_ids, limit=None)`：从 SQLite `documents`（doc_type='api'）
   按 id 精确过滤、默认不截断读全量，构造与 `_compensate_api_defs_from_sqlite` 同构的 api dict
   （name/url/method/description/header/body/return/annotations/source）。SQLite 为权威源。
2. `_compensate_api_defs_from_sqlite` 改为委托 `_load_api_defs_full(doc_ids, limit=RETRIEVAL_K)`，
   保留 `_compensated` 标记与日志（行为不变；ChromaDB 兜底路径测试不受影响）。
3. Hop2b 循环：主模块分支改走 `_load_api_defs_full`（**放最后追加** → 同 method+url 冲突时
   `_dedup_api_defs` 后写覆盖先写，直取全量版胜出）；关联模块 + 公共基础服务维持
   `_search_api_defs` 语义检索（这正是「才需要语义分析」的对象）。

**同时解决两个问题**：
- **问题 1（用例量 92→60）**：`api_definitions` 22 → 全 39（28 业务 + 11 mock），模型上下文
  变全，不再被窄输入锚定低枚举；mock 脚手架接口进入上下文，作前置/断言支撑。
- **问题 2（URL 整批判假）**：白名单 `api_urls` 取 `state["api_definitions"]`（nodes.py:306-310），
  主模块现在全量 39 → mock 与未进 top-K 的业务 url 全部在白名单内，不再「疑似URL拼写错误」。
  **校验对象与生成上下文同源同全量，无需单独改白名单口径。**
- 附赠稳健性：主模块接口检索不再依赖 ChromaDB 可用性/相似度排序（确定性直取）。

### 备选 A（不单独采用）：校验白名单 = 模块真实绑定全集
A 只修校验、不修生成上下文。被 C 吸收：C 让白名单取 api_definitions 即已是主模块全集。
若后续要求白名单覆盖「关联/公共模块全部绑定（超出其语义 top-K 子集）」，再按 A 加 state 字段
`api_url_whitelist` —— 本轮不做（相关/公共接口引用量小、属既有行为，见 §七）。

### 备选 B（不采用）：主模块检索 K 提高到覆盖全部绑定接口
C 已是确定性全量，比 B（加大 K 仍依赖语义相似度、非稳健口径）彻底；B 作废。

## 六、测试设计

新增 `tests/test_hob2_main_module_full_fetch.py`（方案 C 回归）：
1. **主模块全量直取、不走语义**：`_retrieve_related_data`（mock `BindingOps.get_bound_docs` +
   `database.get_session_ctx` + `_mod_exists_in_tree`）→ 断言主模块 doc_ids 传给
   `_load_api_defs_full`、`_search_api_defs` **只**收到关联模块 doc_ids（主模块绝不语义检索）。
2. **同 URL 主模块版本胜出**：主模块与关联模块绑定同 method+url 接口 → 去重后保留主模块直取
   全量版（因主模块最后追加）。
3. **直取函数真读 SQLite 全量**：内存 SQLite 插 N 条 doc_type='api' 行 → `_load_api_defs_full`
   返回全部且 dict 字段与既有补偿路径同构。

既有用例侧：
4. **白名单口径**：api_definitions 含全量（含 mock url）→ 校验器对 mock/业务 url **不报**、
   对真正编造 url **仍报**（补充现有 validator 断言，接口已入 api_definitions 即应通过）。

回归：`tests/test_retrieval_api_dedup.py`（补偿方法改为委托后仍绿）、`tests/test_phase_b_two_stage.py`、
`tests/test_thinking_log.py`、`tests/test_quality_gate.py` 及相邻。

## 七、波及面 / 未覆盖（原则③）

- **上下文变全的代价**：主模块 39 条接口进入 api_definitions → Phase 1/2 提示词 api_definitions
  段变大，单次输出 token/耗时略升（thinking=enabled 无 8192 顶，已实证可承载 92 用例 28k+ token）。
- **Phase 1 用例量将自然变化**：窄输入锚定被移除，枚举量回升多少由实跑观察（见 §八），不预设、
  不改 prompt 计数口径。
- **关联/公共模块仍是语义 top-K 子集**：若计划引用了其绑定但未进 top-K 的接口，校验仍可能
  「疑似URL拼写错误」——属既有行为且引用量小；如需彻底覆盖另走备选 A（本轮不做）。
- Phase 1 输出 short/full mock 双形式并存（叙述 short、步骤 full）：不改 prompt；主模块全量入
  api_definitions 后 full 即匹配。
- 现有 mock 接口在 `documents` 的 file_name/url 均正常，**无需修数据**。
- 无需 DB 迁移 / 新目录 / 新依赖。

## 八、执行与验证

- 方案 C 施工中：changelog 先行 → `retrievers.py` 改动 → `tests/test_hob2_main_module_full_fetch.py`
  新增 + 相邻回归 → 全绿。
- **生产验证判据**：重启服务后真实「智慧停车」全流程跑通 → api_definitions 日志
  `主模块直取全量 (39 个)`（不再 `ChromaDB 命中 22 条 api_definitions` 覆盖主模块）→ 无整批
  「疑似URL拼写错误」→ 通过率 ≥50% 落盘 xlsx。
- **用例量 ~90 口径观察项**（不预设目标）：上下文变全后 Phase 1 枚举量自然回升多少，由实跑
  数据说话；是否另立逐接口覆盖口径，届时再定（本轮不追高、不另改 prompt）。

### 实施记录（2026-09-08）

| 位置 | 改动 |
|:---|:---|
| `agent_components/retrievers.py` | 新增静态 `_load_api_defs_full(doc_ids, limit=None)`（SQLite 全量直取）；`_compensate_api_defs_from_sqlite` 改为委托（保留 `_compensated` 标记 + RETRIEVAL_K 上限）；Hop2b `_retrieve_related_data` 主模块改走直取、放最后追加（同 URL 以主模块直取全量版胜出），关联/公共模块维持语义 `_search_api_defs` |
| `tests/test_hob2_main_module_full_fetch.py` | 新增 4 用例：主模块直取不走语义 / 同 URL 主模块版胜出 / 直取函数真读 SQLite 全量 / 补偿委托标记 |

**单测结果**：
- 新增 4 passed；相邻回归 test_retrieval_api_dedup / test_phase_b_two_stage / test_thinking_log /
  test_quality_gate / test_phase_b_prompt_chain **41 passed**；test_ingest_main_flow / test_key_flows /
  test_regression_import_smoke / test_new_node_evaluation / test_workflow_init / test_api_ops **72 passed**。
- 全量 `tests/` 750 passed，`test_phase_bc_unit.py` 9 failed —— **与本次改动无关**（stash 还原
  retrievers.py 后仍失败，系工作区既有 Phase C prompt/断言未同步的未提交改动所致，另见 §九）。

## 九、连带项（原则③，不擅自处理）

- `tests/test_phase_bc_unit.py` 9 failed 为**既有未提交工作区**问题（YAML 单节点 prompt 规则/
  schema 断言与当前 prompt/代码不一致），非本方案引入；需单独排期对齐，本方案不扩大范围。
- 生产服务无热重载：重启后按 §八 判据验证。
