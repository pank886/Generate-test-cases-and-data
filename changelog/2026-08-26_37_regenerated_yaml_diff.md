# 智慧用电_37 重生成一轮：全量 YAML 对比结论

> 日期：2026-08-26
> 状态：已按「新增加 O2 校验器、其余只改 prompt」实际重生成一轮到 `智慧用电_37_regenerated/`，并与对照组（手工修复 14 passed / 6 skipped）全量对比。本文件记录对比结论与缺口分类。

## 1. 生成结果

- **生成**：`_resolve_api_defs`（SQL 模块作用域 4 接口）→ `_generate_py_file`（2 classes / 20 cases，翻译缓存命中）→ `_generate_all_yamls`（24 文件 = 20 用例 + 2 setup + 2 teardown）
- **结果**：24/24 首轮全过、0 失败、0 回炉、引用完整性通过（O2 校验器零触发——LLM 未输出任何 `$..`）
- **框架执行**：20 用例**全部 setup ERROR**，唯一错误 = `{"retCode":0,"msg":"fail","data":"电表名称请勿重复"}`

## 2. 铁律/数据生效矩阵（新生成 YAML 实证）

| 项 | 结果 | 证据 |
|----|------|------|
| O2 列表断言 | ✅ 生效 | get_parent_meter_list 用 `contains $.data`（对照用 `$..code` 不稳定） |
| O3 ne 边界 | ✅ 生效 | delete 后置 `ne data=${...}` 用简单 key（非 JSONPath） |
| D5 add data=null | ✅ 生效 | 所有 add 成功断言退化 `eq {retCode:1,msg:success}`，不断言 `$.data` |
| O4 delete 数组 | ✅ 生效 | `json: [${get_extract_data('code')}]` |
| O5 负向不臆造 | 🟡 部分 | invalid_category/name_too_long/sql_injection 只断言 retCode=0（未臆造）；但 duplicate/precision/negative_initial/missing_required 仍臆造具体文案 |
| O1 跨步骤引用 | 🟡 部分 | single_rate/bind/tou 的 add→getList 引用连通；但 get_meter_list_pagination 筛选块写死 `ELEC_001` |
| O8 setup 唯一键 | ⚠️ 半失效 | PRE-001/PRE-001_isolated code 动态化；PRE-003/004 code 固定（ELEC_BIND/ELEC_PARENT）；**所有 setup 的 name 全固定** |

## 3. 缺口分类（差异 = 生成器缺口）

### 数据源缺口（修 DB desc 即好，接口事实）

- **N1（致命，当前全挂根因）**：`add.name` desc =「电表名称（必填）」，**未声明「唯一」**。后端实测按 name 判重（「电表名称请勿重复」）。对照组手工修复把 4 个 setup 的 code+name 全部 `${random_code(...)}`。
  - 修复：desc → `电表名称（必填，唯一；建议 random_code 动态生成）`（与 code desc 同口径，有 code 先例证明 LLM 会遵循）。
  - 影响面：setup 4 块 name 固定撞名（当前全挂）；正向独立用例 name 固定（`自动化用例电表001`）单跑 OK、重跑撞名。

### prompt 缺口（生成器不知道规则/铁律覆盖不足）

- **P1·O8 覆盖不全**：铁律 10 强化后 PRE-001/isolated 的 code 动态化生效，但 PRE-003/004（绑定方案/上级电表）的 code 仍固定。铁律未强制「所有创建型步骤的唯一字段都随机化」。
- **P2·O5 执行不力**：铁律 9 已禁臆造失败文案，但 4 个负向用例仍臆造（电表编号已存在/最多保留2位小数/初始电量不能为负数/不能为空）。对照组统一 `contains msg=fail`。
- **P3·O1 部分失效**：get_meter_list_pagination 筛选块写死 `ELEC_001`；single_rate 的 getList 断言写死 `ELEC_TC001` 值而非引用 extract。
- **P4·单用例跑偏**：delete_not_exist（TC-018，test_plan 步骤仅「POST delete 传 ELEC_NOT_EXIST」）被生成成 **4 block 全流程**（add 各类型/负向/getList/getParentList/delete 成功+bind失败+不存在），LLM 无视用例 steps 展开成整个模块回归。对照组 1 block。单次行为待观察。

### 结构性差异（非缺口）

- teardown：新生成 1 block 内 4 个 testCase vs 对照组 4 block（框架 run_blocks 均执行，功能等价）。
- 正向独立用例 code 写死 vs 对照组动态（单跑 OK，重跑隐患，归 N1/P1 类）。
- 新生成 add 精简字段（~15 个，DB required=True 仅 8 个 + 用例所需）vs 对照组全量 payload（30+ 字段，真实 payload 冗余）——精简更合规，非缺口。

## 4. 修复建议清单

| # | 类别 | 改动 | 依据 |
|---|------|------|------|
| F1 | 数据源 | `add.name` desc 补「唯一，建议 random_code 动态生成」 | 后端按 name 判重；code 先例证明 LLM 遵循 desc |
| F2 | prompt（可选） | 铁律 10 强化：接口标注唯一的字段（编号/名称）在创建步骤一律随机化 | PRE-003/004 code 未动态化；name 未动态化 |
| F3 | prompt（可选） | 铁律 9 强化：失败断言无文案依据时统一 `contains msg=fail` | 4 个负向用例臆造文案 |
| F4 | prompt（可选） | 新铁律：单个用例只生成该用例 steps 对应步骤，禁展开模块回归 | delete_not_exist 跑偏 |

> 用户已选「先不动，看完整对比」——本文档即对比结果，修复项待用户确认取舍。
