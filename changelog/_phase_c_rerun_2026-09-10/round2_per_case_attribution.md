# 智慧停车 Round 2 逐例归属报告（2026-09-10）

输入：`round2_junit.xml`（42 failure）+ `round2_pytest.log`（547 次请求实收发）+ `_setup_extract_keys.json`。
判障协议：**连通性 → 契约 → 业务逻辑**；业务层允许 1 次数据自查（输入错=修正数据，输入对=接口逻辑 Bug）。

## 0. 连通性层

**0 例**。547 次请求全部拿到响应，无超时 / 拒连 / TLS 失败。本轮不存在环境类失败。

## 1. 全局口径更正（影响判读）

| 事实 | 证据 | 含义 |
|:---|:---|:---|
| 服务端**业务失败时返回 HTTP 400**，body 仍是业务 JSON `{"retCode":0,"msg":"fail","data":"<文案>"}` | `test_sub_parking_without_parent_negative`：`retCode=0` 断言**通过**，唯一失败是 `状态码断言失败：期望 200，实际 400` | "状态码 400" **不等于**链路故障，本模块 400 是正常业务语义 |
| 业务错误文案放在 **`$.data`**，不是 `$.msg` | `test_remark_length_over_256_negative`：服务端 `data:"备注长度不能超过256"`，用例断言 `$.msg` | 断言取值路径错 |
| `parkCode:'ALL'` 是**合法通配值** | 存在正向用例 `test_add_all_parking_monthly_car_*` | 以 `ALL` 为前提的负向用例前提无效 |

## 2. 主因分类（42 例，按主因去重；个别用例跨两类已标注）

| 编号 | 主因 | 层 | 归属 | 例数 | 用例 |
|:---|:---|:---|:---|:---|:---|
| **R1** | 负向用例一律断言 HTTP 200；服务端业务失败返 400 | 业务 | **测试用例缺陷（生成器）** | 4（+4 混合） | `TestParentChildParkingLinkage::test_sub_parking_without_parent_negative`、`TestPaginationParamBoundary::test_black_white_pagination_page_invalid_negative`、`TestEditBlackWhiteList::test_update_nonexistent_black_white_list_negative`、`TestChargeRuleRequiredValidation::test_charge_rule_missing_price_free_duration_negative` |
| **R2** | 业务错误文案断言在 `$.msg`，实际在 `$.data` | 业务 | **测试用例缺陷（生成器）** | 3 | `TestWhitelistRequiredValidation::test_whitelist_without_allowed_parking_negative`、`TestDeleteBlackWhiteList::test_delete_nonexistent_black_white_record_negative`、`TestChargeRuleRemarkLength::test_remark_length_over_256_negative` |
| **R3** | **臆造**业务文案（文档无此约定） | 业务 | **测试用例缺陷（生成器）** | 2 | `…remark_length_over_256_negative`（"备注长度不能超过256"）、`…free_duration_negative_value_negative`（"免费时长不能为负数"） |
| **R4** | 把**名称**当 `code` 硬编码传入 | 业务 | **测试数据 / 用例缺陷** | 6 | `test_add_main_parking_config_positive`（`bindPayCode:'RULE-HOME'`→"绑定的临停收费规则不存在或类型不正确"）、`test_edit_charge_rule_price_positive`（`code:'RULE-HOME'`→HTML *Invalid Request*）、`test_update_whitelist_access_scope_positive`（`WL-HOME-001`）、`test_edit_coupon_applicable_parking_scope_positive`（`CPN-HOME-001`→"金额优惠券面额不能小于0.01"）、`test_edit_nonexistent_charge_rule_negative`（`CONFIG_rrpjal` 反被接受）、`test_update_device_parking_counts_positive`（`parkName:'PK-CHILD-LOCK_9oxo'`→`91110`） |
| **R5** | 断言把**集合当标量**（路径少一层下标） | 业务 | **测试用例缺陷（生成器）** | 2（+2 混合） | `test_add_whitelist_all_parking_positive`（断言 `$.data.list` = "苏J8JUC4"）、`test_get_list_available_coupons_pk_home_positive`（断言 `$.data` = "时长优惠券_DUR_ykoz96"） |
| **R6** | 删除通道形状错：传 `{"codes":[…]}` → `参数类型错误` | **契约** | **生成器契约缺口**（登记 §12.7 第 3 项，用户本轮明确不做） | 5 | `test_delete_nonexistent_device_code_negative`、`test_deleted_whitelist_no_pagination_positive`、`test_delete_nonexistent_black_white_record_negative`、`test_delete_coupons_by_codes_positive`、`test_delete_nonexistent_coupon_code_negative` |
| **R7** | 提取键未语义化（裸字段名 `code`）/ 来源接口未产出 → `KeyError` 或 JSONPath 未匹配 | 业务（链路） | **生成器产出缺陷** | 15 | `test_edit_parking_pk_home_save_echo_positive`、`TestDeviceDetail`×2、`TestBlackWhiteListDetail`×2、`TestAddChargeRule::…echo_positive`、`test_ZCGZXZHBZSTQJFLCZDZX`、`test_add_amount_coupon_bind_pk_home_positive`、`test_add_main_parking_monthly_car_positive`、`test_add_all_parking_monthly_car_query_positive`、`test_deleted_monthly_car_not_queryable_positive`、`test_temp_car_main_parking_generate_bill_positive`、`test_temp_car_prepaid_no_double_charge_positive`、`test_main_parking_select_sub_charge_rule_negative`、`test_edit_coupon_applicable_parking_scope_positive` |
| **R8** | 产出**非法 JSONPath** 表达式 | 业务 | **生成器产出缺陷** | 1 | `test_get_page_filter_whitelist_type_allowed_parking_positive`（`Parse error at 1:12 near token ]`） |
| **R9** | 取值未唯一化 / 臆造 `parkCode` | 业务 | **测试数据** | 4（与 R4/R5 重叠） | `test_add_whitelist_all_parking_positive`（`parkCode:'PARK001'` 查 `total=0`）、`test_update_whitelist_access_scope_positive`（"此车牌号已存在:白名单 - 川EDNP1Q"）、`test_add_device_without_binding_parking_negative`（`parkCode:'ALL'` 是合法值 → **用例前提无效**） |
| **R10** | 输入确为非法值，服务端**未拦截**（接口逻辑 Bug 候选） | 业务 | **接口缺陷候选** | 6 | 见下节 |

## 3. R10 明细：接口缺陷候选（输入正确 → 记录，不重试）

按协议，这些用例的输入本身是合法构造的非法值，服务端返回 success，应判 **接口逻辑 Bug**：

| 用例 | 输入 | 服务端 |
|:---|:---|:---|
| `TestAddDeviceValidation::test_add_device_bind_nonexistent_parking_negative` | `parkCode:'PARK-NOT-EXIST'` | `retCode:1, msg:success` — 设备绑定了不存在的车场 |
| `TestMonthlyCarRequiredValidation::test_add_monthly_car_without_effective_parking_negative` | 无生效车场 | `{"success":true}` |
| `TestDeleteMonthlyCar::test_delete_nonexistent_monthly_relation_negative` | `codes:['MCDEL_m00r4vu1']`（不存在） | `{"success":true}` |
| `TestCouponRequiredValidation::test_add_coupon_without_applicable_parking_negative` | 无 `parkCode` | `retCode:1, msg:success` |
| `TestParkingDetailQuery::test_nonexistent_parking_code_detail_negative` | 不存在的 parkCode | `retCode:1, msg:success, data:{parkingPark:null,…}` |
| `TestChargeRuleValueBoundary::test_free_duration_negative_value_negative` | `freeTime:'-1'` | `retCode:1, msg:success` |

**⚠️ 待二次确认 2 项**（不强行定性）：
- `TestParkingDetail::test_main_parking_detail_by_park_code_positive`：响应 `data` 内**只有 `parkingPark`**，缺文档声明的 `carInConfig` → 疑**契约缺字段**（属 R6 同族的文档侧缺口），非用例问题。
- `TestParkingDetailQuery::test_nonexistent_parking_code_detail_negative`：negative 用例实际查的是 setup 捕获到的**真实** parkCode，用例与数据不自洽。

## 4. 与 Round 1 的对照

| 指标 | 基线 | Round 1 | Round 2 | 判读 |
|:---|:---|:---|:---|:---|
| passed | 12 | 4 | **12** | 回到基线并消除 R1 级联 |
| failure | 20 | 40 | **42** | ↑，但**性质变了**：R1 是占位串级联（83 次），R2 是用例自身瑕疵 |
| setup-error | 45 | 33 | **25** | 持续下降 |
| teardown-error | 5 | 11 | **4** | 回到基线以下 |
| 占位串调用 | — | 83 | **2** | **主因已清除** |
| 提取键全缺失条目 | — | 52/80 | **7/94** | 命名约定生效 |

**failure 上升的解释**：Round 1 的 40 例里大量是 setup 阶段就崩掉的连带失败；Round 2 setup 修好后，用例**真正跑到了断言**，于是把原本被掩盖的**用例产出瑕疵**（R1–R9）暴露出来。这是暴露面变大，不是退化。

## 5. 结论

- **无环境类失败、无新增接口缺陷定性**；Round 2 的 42 例中，**35 例（R1–R9）归属生成侧**（测试用例/测试数据/契约知识缺口），**6 例列为接口逻辑 Bug 候选**，1 例待确认。
- 生成侧的第一大项是 **R7（15 例，提取键未语义化）** 与 **R1（负向状态码预期，4+4 例）**——两者都是**规则未表述清楚**，符合"prompt 只写规则、接口事实维护数据"的既定约束，可用规则补丁解决。
- 按"只重跑两轮"约束，**本轮到此收口**，不再触发第三轮重生成。
