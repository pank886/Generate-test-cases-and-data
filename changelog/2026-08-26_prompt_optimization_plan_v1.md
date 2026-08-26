# Prompt 优化分类决策（回滚后定稿）

> 日期：2026-08-26
> 状态：**代码/DB 改动已全部回滚到 HEAD（`git diff` 干净）**。本文档记录最终分类决策，供你审查「具体要维护哪些数据」。

## 0. 结论一句话

**维护范围收敛：只管 `add`（添加）和 `delete`（删除）两个接口的数据**；查询接口（getList/getParentList/getPage 等）定义数据错误一律不维护，相关失败用例保持 skip。

6 个失败用例归因（范围内）：
- 数据源：O4 delete 数组 ✅、O5 失败返回语义 ✅、O9 add 字段层级 ✅、**O7 add data=null ⚠️（唯一待维护）**
- 非数据源：O1/O2/O3/O8 **= 真正需要改 prompt**
- 计划设计问题：invalid_page/sort 2 个（查询接口，不处理，保持 skip）

## 1. 回滚清单（已执行完毕）

| 改动 | 回滚操作 | 状态 |
|------|---------|------|
| `extraction_prompts.py` 铁律 12→18 条 | 恢复原始 12 条（新增 10-18 全删） | ✅ |
| `extraction_prompts.py` api_def_extract_prompt 3 处（annotations 提取行 / 输出格式行 / 字段层级约束） | 逐条还原 | ✅ |
| `response_model.py` ApiDefinition.annotations 字段 | 移除 | ✅ |
| `api_annotations.py` is_abnormal 注册块 | 移除（编号还原为「# 3」） | ✅ |
| `data/app.db` getParentList api_annotations | `{"is_abnormal":...}` → `{}` | ✅ |

回归验证：`tests/test_phase_bc_unit.py + test_phase_a_analysis.py + test_api_normalizer.py + test_yaml_db_export.py` = **205 passed / 4 failed / 2 skipped**。4 个失败全在 `TestResolveApiDefs`（DB url 带 `park-energy-electric-web/` 前缀 vs 测试断言无前缀），经 git stash 验证为**既有测试债务**（2026-08-21 DB 补前缀后未同步），与本次改动无关。

## 2. 分类决策

| 优化项 | 类别 | 依据 | 解法 |
|--------|------|------|------|
| O1 跨步骤/前置引用 | **非数据源** | 断链是生成规则缺失，不是文档数据错 | prompt 新铁律 |
| O2 列表断言语义 | **非数据源** | `$..code` 不稳定是框架语义，LLM 需被告知 | prompt 新铁律 |
| O3 ne 能力边界 | **非数据源** | 框架 ne 不解析 JSONPath，需 LLM 知道 | prompt 新铁律 |
| O8 setup 唯一键 | **非数据源** | 铁律 10 未覆盖 setup，需强化 | prompt 铁律强化 |
| invalid_page / invalid_sort 2 个失败 | **计划设计问题（查询接口，不处理）** | 见 §4 末尾 | 保持 skip，不补 prompt |
| O4 delete 数组参数 | **数据源** | DB delete code type=array required=True 已对，LLM 未按 type 生成 | 数据已维护 ✅ |
| O5 失败返回语义 | **数据源** | DB retCode/msg desc 已写「成功=1/失败=0」「成功时返回 success」 | 数据已维护 ✅ |
| O9 字段层级 | **数据源** | DB add 顶层 electricity/initDetailList 已对 | 数据已维护 ✅ |
| O7 add 成功 data=null | **数据源** | DB add 返回 data.desc 为空 → 待维护 | **数据待维护 ⚠️** |
| O6 接口异常（getParentList） | **查询接口，不维护** | 源文档把 add 表单错贴到该查询接口下，定义错误 | 不处理，用例保持 skip |

> 分类准则：**DB/文档里能如实反映「接口事实」的，一律归数据源，维护数据**；只有「生成器不知道某个规则/框架语义」的，才需要 prompt。

## 3. 数据源维护清单（你要审的具体数据）

### ✅ 已维护（无需再动）

| 项 | 位置 | 现状 |
|----|------|------|
| D1·O4 | `electricMeter/delete` code 参数 | `type=array, required=True`，desc 已含「实测 JSON 数组，2026-08-26」 |
| D2·O5 | `delete`/`add` 的 retCode/msg | desc=「业务信封：成功=1，失败=0」/「成功时返回 success，2026-08-21」 |
| D3·O9 | `add` body 字段层级 | `electricity` 顶层 string + `initDetailList` 顶层 array（不再落入子对象） |
| ~~D4·分页~~ | `getList` pageNum/pageSize/sortKey | 查询接口，**不维护**（用例保持 skip） |

### ⚠️ 待维护（本次需要动数据库的只有这 1 处）

**D5·O7：add 返回 data 描述为空**
- 位置：`documents.api_returns` 中 `/electricMeter/add` 的 `data` 字段 `desc`
- 当前值：`""`（空）
- 目标值：`成功时 data 为 null，2026-08-26 实测 add 返回 {"retCode":1,"msg":"success","data":null}`
- 理由：对照组 add 成功断言 `$.data` 失败，根因是生成时不知道 data=null；`delete` 的 data.desc 已按此维护，add 漏了。LLM 读到 desc 后按铁律 9 退化为 retCode/msg 断言。

> getParentList 等查询接口定义错误（源文档把 add 表单错贴其下）**不维护**，相关用例保持 skip。
> 除 D5 外，**没有其他数据要改**。

## 4. 非数据源 prompt 项（待你确认后实施，不影响先维护数据）

| # | 规则 | 铁律位置 | 来源缺陷 |
|---|------|---------|---------|
| O1 | 前置/上游创建资源标识，下游必须 `input_extract` 提取 + `${get_extract_data(key)}`，禁写死 | 新增铁律 13 | 对照组 getList 写死 code 断链 |
| O2 | data 为数组时用 `contains: $.data`（框架拼接子串包含）；禁 `$..字段`（取首个匹配，不稳定） | 新增铁律 14 | 对照组 4 个 `$..code` 不稳定 |
| O3 | `ne` 仅简单字段比较，禁 JSONPath（框架 ne 不解析 JSONPath 必败） | 新增铁律 15 | delete 正向 ne 必败 |
| O8 | 唯一键动态化覆盖 setup/前置创建步骤 | 铁律 10 强化 | setup 固定 code/name 重跑撞名 |

> **invalid_page / invalid_sort 2 个失败 = 用例计划设计问题（查询接口，不处理）**：负向用例的非法值类型多样（类型不匹配/枚举越界/范围越界/格式错误…），不能逐类成铁律；生成器**按用例描述忠实生成**即可。对照组这 2 个用例失败，根因是**用例计划**设计了后端宽容的负向值（pageNum=0/-1、sortKey=2）且 sortKey 断言臆造失败文案。因属查询接口，**保持 skip，不补 prompt、不改计划**。

## 5. 建议的执行顺序

1. **先维护数据**：D5（add 返回 data desc）——1 处 SQL UPDATE，无需代码。
2. **再定 prompt**：确认 §4 的 4 条铁律（O1/O2/O3/O8）是否采纳。
3. 确认后：应用到 `extraction_prompts.py` → 重新生成到 `智慧用电_37_regenerated` → 跑框架对比（查询接口用例沿用对照组 skip）。

## 6. 待你决策

- [ ] D5：add 返回 data desc 是否按目标值维护？
- [ ] §4 的 4 条 prompt 铁律（O1/O2/O3/O8）是否全部采纳？
