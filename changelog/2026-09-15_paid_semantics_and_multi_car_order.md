# 2026-09-15 ParkingRuleTable：`paid` 口径修正 + 一人多车先后时刻

> 状态：**已执行**（2026-09-15，用户批准「方案 α + 两件一起做」）
> 用户附加约束：**「只改用例，执行我自己来」** —— 只改产出用例，接口实跑由用户自己执行

## 0. 输入与范围

- 输入：09-14 11:08 跑批日志（`test.20260914110842.log`）逐例归因 + 用户 2026-09-15 两条口径
- 范围：**只修 ITEM 1（一人多车先后时刻）与 ITEM 2（`paid` 口径）** 两件
- 其余 22 处失败中的接口侧项 → **登记不动**，见 §6

### 用户口径原话

1. **一人多车**
   > 应该有时间先后关系吧？车场配置是否临停准入或白名单多车准入？
   > 包月一人多车，后入场标识为临停
   > 车场不支持白名单多车准入，后入场车辆同包月车一人多车逻辑处理

2. **提前缴费**
   > 提前缴费是否均产生费用？未产生费用应失败，且返回提示
   > （随后修正）**你这个提前缴费断言不对，提前缴费只要有费用产生是必成功的，
   > 后续你实际支付多少都成功，这只是一个动作**

## 1. ITEM 2：`paid` 口径 —— 我方断言错（4/4）

### 定位

`yaml_kernel.py::_paid_step` 用判定表 **动作列（是否缴费）** 的原文当期望值：

```python
expect = bool(col["value_map"].get(raw))   # raw = 动作（是否缴费）的「是/否」
```

而表2-06/07/08/09 这四行的两列**打架**：

| 用例 | 条件（是否提前缴费） | 动作（是否缴费） | 我们断的 | 11:08 实测 |
|:---|:---|:---|:---|:---|
| 表2-06 | **是** | 否 | `paid: false` | `paid: true` |
| 表2-07 | **是** | 否 | `paid: false` | `paid: true` |
| 表2-08 | **是** | 否 | `paid: false` | `paid: true` |
| 表2-09 | **是** | 否 | `paid: false` | `paid: true` |

四例「提前缴费模拟」（`mock/pay/simulate`）实测**全部 `{"success":true}`**。
`payableAmount:0.0` 是券抵扣后的余额（表2-08 是时长券 300 把 20 元抵完），
不影响「已缴」这一事实 —— 用户口径「提前缴费只是一个动作，只要有费用产生必成功，
后续你实际支付多少都成功」⇒ 缴了费 `paid` 必为 `true`。

### 修法（同源，不是新造规则）

口径本就**重复实现**了：`_pay_step`（`:871`）早就按「条件列优先」选出有效缴费动作，
`_paid_step` 却另判一次而分叉。现抽出 `_pay_action_key(case)` 作为「缴不缴费」的
**唯一判据**，缴费步骤与缴费状态断言都从它取。

### 效果

| 用例 | 改前 | 改后 |
|:---|:---|:---|
| 表2-06/07/08/09 | `paid: false` | **`paid: true`** |
| 表2-02…05 / 2-10…13 | `true` | `true`（不变） |
| 表2-01（无缴费动作） | `false` | `false`（不变，反向锁定） |

## 2. ITEM 1：一人多车先后 —— 判定表写了顺序，是我们生成时丢的

> ⚠️ **同日已改写法**：本节下面的**方案 α（偏移 + `(token, car)` 键 + `_time_of`）已全部删除**，
> 改为**逐例字面量、按 c9 动作顺序位置对应**。见
> `changelog/2026-09-15_action_times_literals_and_17_cases.md`。
> 本节的**结论仍然成立**：顺序是判定表明写的、是我们生成时丢的、车场开关映射是有的。
> 保留本节作为「为什么会有先后时刻」的来路记录。

### 判定表原文（c9「条件（出入场动作）」）

| 用例 | 出入场动作（逐字） |
|:---|:---|
| 表1-08 / 1-09 / 1-19 / 1-20 | `A入主-B入主-A出主-B出主` |
| 表1-10 / 1-21 | `A入主-B入主` |

**顺序是判定表明写的**，不是我们推的。

### 我们丢了它

`_times_for`（`:532`）的 key 只有 `token`，而 **A/B 两车共用同一个 token**
（都是「入主」/「出主」）⇒ 两车被赋成同一时刻；表1 又无 timeline，一律落基准时刻。
09-14 那轮实测 6 例 A/B 全是 `2029-12-31 10:00:00` 同秒进、同秒出 ——
**「后入场」在产物里根本不存在**，这正是用户问「应该有时间先后关系吧？」指的东西。

### 车场开关：映射有、且逐字忠于 xlsx（不是缺配置）

| 车场 | 临停准入 `tempCarIn` | 一人多车白名单 `carWhiteOtherIsCasual` | 覆盖的用例 |
|:---|:---|:---|:---|
| P1 | 否 | 否 | 表1-10、表1-21 |
| P2 | 是 | 否 | 表1-09、表1-20 |
| P3 | 否 | 是 | 表1-08、表1-19 |

⚠️ 但实测这 4 个「不支持」例 mock **一个都没按判定表走**（子车该转临停却保持原类型、
子车该拒却放行），且 P3 连主车都拒 —— 属 **H19 根因 B2「车场开关语义未实现」**，
是**接口侧**、已登记（见 §6 E1/E2）。**补先后时刻不保证这 4 例翻绿。**

### 修法（方案 α：map 写显式 timeline）

- `_times_for` 的 key 支持 `(token, car)`，时间线项带 `car` 时用它、不带时仍用 `token`
- 新增 `_time_of(times, token, car)`：优先 `(token, car)`，退回 `token`；**两处都没有就报错**，
  不静默兜底 —— 静默兜底正是「两车同秒」当初不被发现的原因
- `build_case` 取动作时刻改走 `_time_of`
- `_parking_type_call` 的 `startTime` 改取**该车自己**的入场时刻（原来写死基准时刻
  `_fmt(_base_dt())`，子车查询时刻会早于它实际的入场时刻）
- map 里 6 例各写一条显式 timeline：A 入主 `0h` → B 入主 `1h` → A 出主 `4h` → B 出主 `5h`
  （表1-10/21 只有前两条）。表1 不校验金额，数值本身不参与断言

## 3. 变更清单

| # | 对象 | 动作 |
|:---|:---|:---|
| 1 | `rule_tables/yaml_kernel.py` | 新增 `_pay_action_key`；`_pay_step` 改用之；`_paid_step` 期望值改同源（签名去掉未用的 `col`/`raw`） |
| 2 | `rule_tables/yaml_kernel.py` | `_times_for` 支持 `(token, car)`；新增 `_time_of`、`_primary_car_slot`；`build_case` / `_parking_type_call` / `_coupon_steps` / `_fee_steps` 取时刻改走新机制 |
| 3 | `rule_tables/parking_rule_map.yaml` | 6 个一人多车用例各写**显式 timeline**（带 `car`）；`是否缴费` 列的 `value_map` 注明不再是期望值来源 |
| 4 | `tests/test_rule_table_export.py` | 新增 `TestOnePersonMultiCarOrder`（3 项）；paid 锁定测试改口径 + 新增全表2 不变式（1 项） |
| 5 | 部署产物 | `tests/tools/_redeploy_parking_rule_table.py` 重导出 |
| 6 | 文档 | 本文件 + `_phase_c_selective_2026-09-09/MANIFEST.md` 新增 **H22** 节 |

## 4. 测试设计（原则④）

### 新增/改动

| 测试 | 断言 |
|:---|:---|
| `TestOnePersonMultiCarOrder::test_b_enters_after_a` | 6 例两个入场步，顺序必须是 A→B，且 **B 的时刻严格晚于 A** |
| `TestOnePersonMultiCarOrder::test_exit_order_matches_the_table` | 4 例 A 先出、B 后出；表1-10/21 判定表无出场动作 → **不得凭空多出来** |
| `TestOnePersonMultiCarOrder::test_type_query_uses_that_car_own_entry_time` | 类型查询的 `startTime` == 该车自己的入场时刻；并锁定**检查到 8 处**（不做静默通过） |
| `TestFeeQueryAndPayActions::test_paid_follows_the_payment_action_not_the_column` | 表2-06/07/08/09 断 `true`；**表2-01 仍断 `false`**（反向锁定） |
| `TestFeeQueryAndPayActions::test_paid_agrees_with_the_pay_step_for_every_case` | 不变式：全表2 逐例「有缴费动作 ⟺ 断 `true`」，且锁定表2 用例数 = 13 |

### 反向验证（绿灯不算数）

把两处改动**各自换回改动前的实现**，确认新测试会红（一次性脚本，不入库）：

| 场景 | 换回的旧实现 | 结果 |
|:---|:---|:---|
| A | `_times_for` 只按 token 取时刻 | `test_b_enters_after_a`、`test_exit_order_matches_the_table` **红**<br>（`表1-08': 'B 必须后入场（同秒 = 顺序丢了）'`），其余不红 |
| B | `_paid_step` 照动作列原文 | `test_paid_follows_...`、`test_paid_agrees_...` **红**<br>（`表2-06': '有缴费动作却断 paid=false'`），其余不红 |
| C | `_parking_type_call` 的 `startTime` 写死基准时刻 | `test_type_query_uses_that_car_own_entry_time` **红**，其余不红 |

**如实登记一处局限**：`test_type_query_uses_that_car_own_entry_time` 在**场景 A 下不会红** ——
它断的是「查询时刻 == 自身入场时刻」，而两车同秒时**两侧一起塌**，等式仍成立。
它守的是场景 C 那个 Bug；顺序塌陷由另两项守。

## 5. 验证方式与结果

| 步骤 | 结果 |
|:---|:---|
| 生成到 tmp 自证（13 例 paid + 6 例先后 + 查询时刻） | 全部通过 |
| `--check`：重导出前「部署目录 vs 当场重生成」 | 差异面**恰好 10 个文件**，无连带 |
| `pytest tests/test_rule_table_export.py tests/test_rule_table_yaml.py tests/test_rule_table_rows.py -q` | **100 passed**（原 96 + 新增 4） |
| 重导出部署 + 自检 | `相同 41 / 不同 0 / 仅部署侧有 0 / 仅重生成侧有 0` |

差异面 10 个文件（= 该改的全部，无连带）：

- 6 个 `test_t1_08/09/10/19/20/21_*/test_data.yaml`（先后时刻 + 查询时刻）
- 4 个 `test_t2_06/07/08/09_*_prepay/test_data.yaml`（`paid` 口径）
- `setup_data/setup_parking_rule_table.yaml` **逐字未变**（由测试反向锁定）

## 6. 需要你决策（原则③：方案外发现，我不顺手改）

| # | 项 | 结论 |
|:---|:---|:---|
| E1 | 表1-08/19 在 P3 **连主车都被拒** | 承接 H19-B2；`carWhiteOtherIsCasual` 的列→字段映射当初是**穷举挑的、未验证**，其字面语义是「白名单**其他车场**按**临停**」，与列名「是否支持一人多车白名单=是 ⇒ 应支持」**反向**。09-11 单变量探针已证：同一 add 体、仅车场不同 → 白名单 `multi:true` 在 P3 入场 **false**、在 P1 **true** |
| E2 | 4 个「不支持」例 mock 不按判定表走 | 表1-09 子车类型查得 `4`、表1-20 得 `1`（期望都是 `2` 临停）；表1-10/21 子车该拒却放行。同属 H19-B2 接口侧 |
| E3 | 副车牌 `mock/access/exit`（出场）返 400 | H21 已登记，**非本次引入** |
| E4 | 口径另一半「**未产生费用应失败，且返回提示**」**无用例覆盖** | 表2-01（不产生费用）根本没有缴费动作 ⇒ **不写无测试覆盖的死代码**，等有用例时与用例一起加 |
| E5 | `carInConfig` 字段全集里**没有**「一人多车」对应字段 | 见 E1；接口方需澄清该开关的正式字段 |

## 7. 不做的事（逐条列明）

- 未改 E1–E5 任何一项（登记待决策）
- 未跑接口实跑（用户自己执行）
- 未碰 git（无提交、无暂存、无 stash）
- 未动 `bill/unpaid`（账单-未缴费查询）断言、`carWhiteOtherIsCasual` 映射、表1-11/1-22/1-24
