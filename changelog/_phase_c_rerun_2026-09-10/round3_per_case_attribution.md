# 智慧停车 Round 3 逐例归属报告（2026-09-10）

输入：`round3_junit.xml`（82 节点）+ `round3_pytest.log`（GBK，全量实收发）+ 58 个 `*.last_run.json` 侧车（`run_ts=20260910163000`）。
判障协议：**连通性 → 契约 → 业务逻辑**；业务层允许 1 次数据自查。

## 0. 连通性层

**0 例**。全量请求均拿到响应，无超时 / 拒连 / TLS 失败。

## 1. 总量对比（同口径全量跑，含 4 例 getDetail 相关）

| 指标 | 基线(09:08) | Round 1 | Round 2 | **Round 3** |
|:---|:---|:---|:---|:---|
| passed | 12 | 4 | 12 | **18** |
| failure | 20 | 40 | 42 | **38** |
| setup-error | 45 | 33 | 25 | **24** |
| teardown-error | 5 | 11 | 4 | **2** |
| 节点合计 | 82 | 88 | 83 | 82 |
| 占位串调用 | — | 83 | 2 | **0** |
| 提取键全缺失条目 | — | 52/80 | 7/94 | **0/94** |

**口径 B（排除 3 个本轮实际失败的 getDetail 相关节点）：failure 35 / 79 节点**。
（第 4 例 `test_edit_parking_pk_home_save_echo_positive` 本轮 **PASSED**，无需排除。）

## 2. failure 38 例归属

| 编号 | 主因 | 层 | 归属 | 例数 |
|:---|:---|:---|:---|:---|
| **G1** | 测试 YAML 引用**裸键** `get_extract_data(code)` / `(parkCode)`；setup 实际产出 `preXXX…` 前缀键 | 业务（链路） | **生成侧** | 12 |
| **G2** | 测试 YAML 引用**未声明的前缀键**（`pre008DeviceCode` / `pre010DeviceInCode`，fixture 的隔离克隆前缀不符） | 业务（链路） | **生成侧** | 2 |
| **G3** | 断言**把集合当标量** / 产出**非法 JSONPath** | 业务 | **生成侧** | 6 |
| **G4** | 服务端**未拦截**已声明的非法输入（输入本身构造正确） | 业务 | **接口缺陷候选** | 10 |
| **G5** | 前置条件/取值不自洽（含 setup 级联） | 业务 | **生成侧** | 8 |

### G1 明细（裸键残留，12 例）

`get_extract_data(code)` 未加引号且无前缀，全模块 **10 处**；
`get_extract_data(parkCode)` **3 处**。而模块内 setup 声明的 **129 个键全部带 `preXXX` 前缀** → 引用必然 `KeyError`。

| 裸键 | 用例 |
|:---|:---|
| `code` | `test_ZCGZXZHBZSTQJFLCZDZX`、`test_add_amount_coupon_bind_pk_home_positive`、`test_add_main_parking_temp_charge_rule_echo_positive`、`test_edit_charge_rule_price_positive`、`test_get_car_by_code_nonexistent_negative`、`test_get_car_by_code_whitelist_detail_positive`、`test_get_coupon_by_code_nonexistent_negative`、`test_get_device_by_code_positive`、`test_nonexistent_device_code_negative` |
| `parkCode` | `test_add_main_parking_config_positive`、`test_main_parking_detail_by_park_code_positive`、`test_nonexistent_parking_code_detail_negative` |

> ⚠️ 后 3 例即 §15.6 登记的 **getDetail 相关用例**。本轮它们在碰到 getDetail 端点**之前**就死在裸键 `KeyError` —— 端点缺陷仍存在，只是被更早的错误掩盖。

### G2 明细（2 例）
`test_get_all_with_devices_parking_counts_positive`（`pre008DeviceCode`）、
`test_main_whitelist_blocked_temp_closed_subparking_negative`（`pre010DeviceInCode`）。

### G3 明细（6 例）
`test_get_list_device_options_positive`、`test_add_whitelist_all_parking_positive`、
`test_get_parking_payment_config_list_positive`、`test_get_list_available_coupons_pk_home_positive`
—— 断言 `$.data` 期望标量，实际是对象/数组；
`test_get_page_devices_by_parking_positive` —— `Parse error at 1:12 near token ]`（非法 JSONPath）；
`test_deleted_monthly_car_not_queryable_positive` —— 返回值无法转字典。

### G4 明细：接口缺陷候选（10 例，输入正确 → 记录不重试）

| 用例 | 期望（文档已声明） | 服务端 |
|:---|:---|:---|
| `test_add_device_bind_nonexistent_parking_negative` | 400 | **200 + success** |
| `test_device_pagination_param_invalid_negative` | 400 | **200 + success** |
| `test_add_device_without_binding_parking_negative` | `data` 含"车场" | `None` |
| `test_add_coupon_without_applicable_parking_negative` | msg=fail | **success** |
| `test_add_monthly_car_without_effective_parking_negative` | success=False | **True** |
| `test_delete_nonexistent_monthly_relation_negative` | success=False | **True** |
| `test_free_duration_negative_value_negative` | retCode=0 | **retCode=1** |
| `test_edit_nonexistent_charge_rule_negative` | `data`="规则不存在" | `None` |
| `test_whitelist_without_allowed_parking_negative` | `data`="准入车场不能为空" | "请求失败" |
| `test_black_white_pagination_page_invalid_negative` | msg=fail | **success** |

## 3. setup-error 24 例：**单一主因 = 车场创建失败**

全量扫日志：`parkingPark/saveOrUpdate` 共 **79 次调用，47 次失败**（OK 32）。

**按实收请求里 `bindPayCode` 的实际形态分类**（79 次 park-create 全量扫；「形态」比服务端文案更能定性）：

| 类 | `bindPayCode` 实收形态 | 次数（全部 79） | 失败 | 服务端文案 |
|:---|:---|:---|:---|:---|
| **B** | 规则**名称字面量** `'RULE-HOME'`(18) / `'RULE-CHILD'`(8) / `'RULECHILD_wcwrj9'`(2) | **28** | 28 | `绑定的临停收费规则不存在或类型不正确` |
| **D** | 空串 `''` | 10 | 10 | `开启计费后必须绑定临停收费规则` / `主场类型必须要绑定计费` |
| **E** | **列表被 `str()` 序列化成字符串** `"['zhyqC…']"` | **4** | 4 | `绑定的临停收费规则不存在或类型不正确` |
| **A** | 标量编码 `'zhyqC…'`（**正确形态**） | 34 | 2 | `此车场名称已存在`（**与规则无关**）/ 负向用例 |
| **C** | 字段不存在 | 3 | 3 | `车场主/子场信息不能为空` / `主场必须存在`（子场负向用例，预期内） |

> **44/47 次失败同属一条根因：`bindPayCode` 最终没有以"标量编码"形态送达服务端。**
> 其中 **38 次压根没生成回读链**（写死名称 / 空串），**4 次生成了却在替换时丢了类型**。
> 形态正确的 34 次里 32 次创建成功。

### 3.1 「回读」到底做了没有？——**两类情况，机制本身是好的**

**① 10 个 setup 连"建规则"都没做。** 写死 `RULE-HOME`/`RULE-CHILD` 的 setup 全查：

| setup | 写死值 | 有 `parkingPaymentConfig/add` | 有回读步 |
|:---|:---|:---|:---|
| `setup_edit_whitelist` / `setup_exit_payment` / `setup_special_plate_release` / `setup_advance_payment` / `setup_blacklist_process_rule` / `setup_monthly_car_process_rule` | `RULE-HOME` | ❌ | ❌ |
| `setup_change_device_parking` / `setup_main_sub_parking_combined_charge` | `RULE-CHILD`+`RULE-HOME` | ❌ | ❌ |
| `setup_main_sub_parking_payment_rule` / `setup_monthly_car_per_parking_effect` | `RULE-CHILD` | ❌ | ❌ |

这些 setup 把规则**内联**进聚合接口 `parkingPark/saveOrUpdate` 的 `paymentConfig` 块创建，
然后在同一个 `parkingPark` 块里要求一个 `bindPayCode` —— 服务端生成的编码此时**无从取得**，
生成器遂回退到**测试计划里给的规则名**。

**② 另一部分 setup 建了规则、也回读了，编码也取对了 —— 但绑定时没把结果接上。**
实证：全量 **47 次 `parkingPaymentConfig/getParkingPaymentConfigPage` 调用**，
`$.data.list[0].code` 取回的正是刚建的规则（倒序第一），提取记录佐证
`JSON 提取 [pre002PaymentConfigCode]: zhyqC92872031`。

**③ 4 次走了链却"丢了类型"——丢点已定位到框架替换层**
`extract_list` 提取出来的值是**列表** `['zhyqC16859653']`（非标量，共 12 次列表形态产出）；
`common/debugtilk.py:23` 的 `get_extract_data(key)` 在 `randoms=None` 时**原样返回该列表**；
`base/apiutil.py:99`
```python
str_data = str_data.replace(ref_all_params, str(extract_data), 1)
```
把列表 `str()` 成 `"['zhyqC16859653']"`，于是 `bindPayCode: ${get_extract_data('pre004PayCode')}`
（YAML 里**无引号**）最终以**字符串** `"['zhyqC16859653']"` 送达服务端 → `绑定的临停收费规则不存在或类型不正确`。

**正确写法是带索引**：`${get_extract_data('pre004PayCode', 1)}`（`randoms=N` → `data[N-1]` → 标量）
或 `${split_extract_data('pre004PayCode', 0)}`。

> **小结**：回读机制本身是好的。断点有**两个**：① 38 次**压根没生成**回读链（"绑定"字段回退成测试计划字面量/空串）；
> ② 4 次生成了链，但**引用 `extract_list` 键时漏了索引**，在替换层被降级成字符串。

逐 setup 核验（`bindPayCode` 实际取值）：

| setup | bindPayCode | 症状 |
|:---|:---|:---|
| `setup_edit_whitelist` / `setup_exit_payment` / `setup_special_plate_release` | **`RULE-HOME`**（硬编码名称） | R4 未消除 |
| `setup_add_device` | **`''`** 且 needPay=1 | 空串绑定 |
| `setup_delete_device` | `${…('pre004PayCode')}` | 键名不匹配（实际产出 `pre004PaymentConfigCode`） |
| `setup_entry_vehicle_process_rule` | `${…('pre003RuleCode')}` | 键未经回读产出 |
| `setup_sub_parking_temp_permit` | `${…(ruleChildCode)}` | **裸键**（R10） |
| `setup_other_parking_whitelist_permit` | `${…('pre001RuleHomeCode')}` ×2 | 键未产出 |
| `setup_delete_charge_rule_restriction` | `${…('pre002RuleCode')}` | 键未产出 |
| `setup_delete_parking` | 第 2 步 `''` + needPay=0 | 空串绑定 |

**反证（规则确实生效过）**：`setup_parking_detail.yaml` 完整走通
`回读收费规则编码 → pre004PaymentRuleCode → bindPayCode: ${get_extract_data('pre004PaymentRuleCode')}`，
车场创建成功。**同一模块、同一条规则，一半生效一半失效。**

## 4. teardown-error 2 例
`test_black_white_pagination_page_invalid_negative`、`test_main_whitelist_blocked_temp_closed_subparking_negative` —— 其 setup 未产出清理所需编码（级联）。

## 5. Round 3 对 Round 2 主因的处置核验

| Round 2 主因 | Round 3 状态 | 证据 |
|:---|:---|:---|
| **R1** 负向断言 HTTP 200 | ✅ **已消除** | 负向 YAML 现一致断言 `status_code: 400`；`test_add_device_bind_nonexistent_parking_negative` 等失败原因已变为"服务端未返 400" |
| **R7/R10** 提取键未语义化 | 🟡 **setup 侧已消除**（全缺失 7/94 → **0/94**）；**测试 YAML 侧残留 12 处裸键** | §2 G1 |
| **R6** 删除形状错 | ✅ **已消除** | 删除用例不再报「参数类型错误」 |
| **R2/R9** 错误文案断言 `$.msg` | ✅ **已消除** | 文案断言现取 `$.data` |
| **R5/R11** 集合当标量 | 🟡 **部分残留**（4 例） | §2 G3 |
| **R4** 名称当 code | 🔴 **未消除**（32 次车场创建失败） | §3 |

## 6. 结论

1. **Round 3 = 生成侧 28（failure）+ 24（setup 级联）+ 2（teardown）= 54 例；接口缺陷候选 10 例。**
   （口径 B 排除 getDetail 相关：生成侧 51 / 接口缺陷候选 10。）

2. **规则补丁有效但未收敛**：R1/R2/R6/R9 完全消除，R5/R7/R10 部分消除，
   但 **`bindPayCode` 这条同一根因在 43 次车场创建里复发** —— 而数据层 `desc`（D3）已写明
   "必须是经查询回读到的真实编码"。**生成器读到了说明，却没有遵循。**

3. **因此"再补一轮规则/说明"不能收敛**：缺口在**生成器的遵循一致性**，不在接口说明的完备性
   （接口覆盖 100%，`return[].desc` / `body[].desc` 均已有位可写）。
   下一轮若仍只调 prompt/desc，预期收益递减。
