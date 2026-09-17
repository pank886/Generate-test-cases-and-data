# rule_tables —— 判定表支线参考实现（已归档，2026-09-17）

## 这是什么

智慧停车 `ParkingRuleTable` 的**确定性生成链**：从人工维护的判定表（xlsx）出发，
由 `parking_rule_map.yaml` 推导出前置、触发、断言，**全程不经 LLM**，产出 39 个 YAML
＋ `test_ParkingRuleTable.py`，再部署到框架项目运行。

它解决的问题是「让 LLM 生成 YAML」的不确定性：`yaml_kernel.py` 的模块注释原文是
「判定表 YAML 确定性生成内核（**替代 `yaml_gen` 的 LLM 调用**）」。

## 为什么归档在这里

主流程**零使用**（2026-09-17 核实）：

* `agent_components/`、`web/`、`infrastructure/`、`ingest/`、`database/` 无任何
  `import rule_tables`。`web/tasks.py:409` 调的 `_generate_py_file` 来自系统自己的
  `agent_components/generators/py_export.py`，不经过本目录的 `phase_c_export.py`。
* 唯一的 import 方是 5 个单元测试和一个手动部署工具（`_redeploy_parking_rule_table.py`），
  两者都不是主流程。
* 原目录 `rule_tables/` 与那 5 个测试**从未入库**（`git log --all` 无记录），
  是一次性的本地支线。

按「工具类留存判据 = 新框架 v0.1 有没有节点承接」，这条链的**产物**（39 个 YAML）
不需要，但**方法与数据形态**对应 v0.1 多个节点，故整包留档供移植，源码删除。

## 使用约束（重要）

**本目录是归档，不是可导入的包。** 原 `rule_tables/` 能工作是因为仓库根在 `sys.path`
上（`tests/conftest.py:5-8`、5 个测试的 `sys.path.insert(parents[1])`、
`_redeploy_parking_rule_table.py:27` 的 `dirname×3` 都指向仓库根）。搬离仓库根后
`from rule_tables import ...` 一律失败——包括本目录里的 `_redeploy_parking_rule_table.py`。

要恢复可运行状态：把本目录 5 个文件（不含 `_redeploy_*.py`）复制回仓库根的
`rule_tables/`，并恢复对应的 5 个测试。

## 方法判词

### 一、值得移植（v0.1 有节点，系统侧无等价物）

| 方法 | 位置 | 对应 v0.1 节点 | 为什么系统侧没有 |
|---|---|---|---|
| `build_setup_parts` / `case_pre_ids` / `has_own_blocks` | `yaml_kernel.py:266-303` | Phase B 三段的**「前置」** | `py_export` 只**消费**前置编号，不决定「某个用例需要哪几个前置块」。判据单点在 `case_pre_ids`，`rule_rows` 明确不重写一遍（见 `rule_rows.py` 头部注释） |
| `parse_actions` / `_unknown_tokens` | `rule_rows.py:107-162` | Phase B**「人工导入」**的入口 | 系统侧走 LLM 提取，不解析 xlsx 动作列。未匹配片段**原样保留由调用方报错，不静默丢弃** |
| `_to_translation_cache` | `rule_rows.py:357` | 总原则**「确定性优先」** | 系统侧的 `translation_cache.json` 是 LLM 产出的；这里是确定性映射，**零 LLM** |
| `apply_table_defaults` / `match_group` / `get_park_spec` / `resolve_vehicle_records` | `registry.py:194-380` | Phase B**「权威源」** + **「落点登记」** | 系统侧无「判定表条件单元格 → 分组/车辆记录」的推导层 |
| `action_tokens` / `assert_columns` / `assert_value_map` / `assert_endpoints` | `parking_rule_map.yaml:496-635` | **「后果面登记」的数据雏形** | 这是**动作 → 断言**的显式映射表，不是代码；可作登记表的形态参考 |
| `build_teardown` | `yaml_kernel.py:655` | Phase C 清理 | 统一清理按**反序**（车辆记录 → 设备 → 车场 → 收费规则）。对应已知缺陷类「teardown 成 add」 |
| `_base_dt` / `_times_for` | `yaml_kernel.py:703-757` | 总原则**「确定性优先」** | 时间取**注册表里的基准日期**而非 `now()`；`_times_for` 按动作序列逐个推时刻。这是可复现的关键做法，容易被忽略 |
| `_car_readback_step` | `yaml_kernel.py:615` | Phase C 回读 | 每个写操作配一条回读。对应已知瓶颈「未生成回读链」 |
| `generate(..., dry_run=)` | `yaml_kernel.py:1174` | — | 干跑开关 |

### 二、已被系统侧覆盖（**别重复留**）

| 名称 | 系统侧对应物 |
|---|---|
| `pre_id_slug` / `setup_file_name` / `FIXTURE_LEVEL_PER_PRE` | `agent_components/generators/py_export.py:103-110`（`_pre_id_slug`）与 `:22`（`_PER_PRE_LEVEL = "逐前置"`）**逐字同规则**，且是主流程真用的那份 |
| `pre_id_group` / `pre_id_case` 的 `PRE-` 编号前缀 | `py_export.py:270` 承认该前缀（`pid.startswith("PRE-")`） |

移植时以系统侧为准，不要从这里带第二份镜像过去。

### 三、停车专用（不可移植，留作例证）

`_park_steps` / `_device_steps` / `_car_add_step` / `_coupon_steps` / `_fee_steps` /
`_pay_step` / `_parking_type_plan` / `park_entities` / `vehicle_recipes` 等——都是停车
业务的具体步骤拼装。价值在于「写操作必配回读」「清理必反序」这两个**模式**，
不在步骤内容本身。

## 删除记录（2026-09-17）

随源码删除的：

| 项 | 规模 | 说明 |
|---|---|---|
| `rule_tables/__pycache__/` | 4 个 / 124,403 B | 编译缓存，未归档（会自动重建） |
| `tests/test_rule_table_{yaml,export,rows,registry,fixture_split}.py` | 5 个 / 2,333 行 / 157 个 test | 全部未入库；pytest 收集由 1094 → 937 |
| `有作用/PhaseC/_redeploy_parking_rule_table.py` | 1 个 | 导出+部署+自检工具，import 本包；随包归档到此目录 |

**连带损失（需知晓）**：`tests/test_rule_table_fixture_split.py::TestNaming::test_py_export_agrees`
是 `py_export._PER_PRE_LEVEL` 与 `yaml_kernel.FIXTURE_LEVEL_PER_PRE` 之间的**跨模块
一致性锁**，随归档消失。两侧常量值都是 `"逐前置"`，**值本身没有变化**，丢的是锁。
`py_export.py:20-21` 的 docstring 指针已同步改指向本目录。

## 原始文件清点

| 文件 | 体积 | 行数 | 职责 |
|---|---|---|---|
| `yaml_kernel.py` | 54,683 B | 1210 | 确定性 YAML 生成内核（替代 `yaml_gen` 的 LLM 调用） |
| `parking_rule_map.yaml` | 46,318 B | 890 | 单一事实源：车场分组 / 车辆配方 / 动作→断言 |
| `rule_rows.py` | 14,784 B | 363 | xlsx → Phase C 行结构适配器 |
| `registry.py` | 14,120 B | 380 | 注册表加载层（模块级缓存 + 双检锁 + `reset_cache()`） |
| `phase_c_export.py` | 9,675 B | 217 | 判定表 → Phase C 落盘接线 |
| `_redeploy_parking_rule_table.py` | — | — | 导出 + 部署 + 自检（唯一一条命令消除「改了内核忘导出」） |

背景见 `changelog/2026-09-16_parking_rule_table_chain_facts.md` 与
`changelog/2026-09-17_general_case_generation_architecture_draft_v0.1.md`。
