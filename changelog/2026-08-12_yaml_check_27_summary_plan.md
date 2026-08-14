# YAML 合规问题汇总与处理方案（智慧用电_27）

| 项目 | 内容 |
|:---|:---|
| 日期 | 2026-08-12 |
| 审查报告 | `logs/YAML_CHECK_REPORT3.md`（第 27 次生成） |
| 审查对象 | `testcase/园区基线/智慧用电_27/SmartElectricity/`（210 blocks / 222 用例 / 121 个非空 YAML + 10 setup/teardown） |
| 检查方式 | 脚本自动化 + 框架运行时实测（`RequestsBase().specification_yaml()`、`Assertions.assert_result()`） |
| 总体结论 | ❌ **不可用（4 类致命问题）**。YAML 数据层延续 _26 高水准（静态结构 0 问题、导出断言 12/12、db 0 处），但 **1 个 Python 侧回归（fixture 调用模式错误 → 91% 用例 class 级 ERROR）+ 3 个运行时级问题** |

---

## 一、问题汇总

| # | 问题 | 数量 | 严重度 | 根因层 | 状态 |
|:--|:---|:--|:--|:--|:--|
| 1 | fixture 调用模式回归：`specification_yaml(get_testcase_yaml(...))` 传多 block 列表 → `TypeError` | 5 fixture / 10 处 | 🔴 致命（91% 用例 ERROR） | **Python 生成器回归** | ✅ 已实施（2026-08-13） |
| 2 | `eq: {status_code: ...}` 断言反模式（`status_code` 仅 `contains` 有特殊处理） | 23 处 / 3 文件 | 🔴 致命（断言必失败 + extract 级联） | **prompt 缺口**（无通用铁律） | ✅ 已实施（2026-08-13） |
| 3 | YAML 文件缺失：`test_billing_rule_delete_bound_negative/test_data.yaml`（空目录） | 1 个 | 🔴 致命（`FileNotFoundError`） | **生成遗漏**（未补生成/未检测） | ✅ 已处理：**不做补生成**（设计规划，见九） |
| 4 | `contains` 断言值为裸字符串 → `AttributeError` 崩溃 | 3 处 / 3 文件 | 🔴 致命（非干净断言失败，整方法崩溃） | **prompt 缺口 + schema 未约束** | ✅ 已实施（2026-08-13） |

> 静态结构检查（header、参数层级、URL、断言运算符、extract、`${}`、导出断言、db）**全部通过**——LLM 结构质量已收敛，四类问题均属**运行时级契约不一致**，与 _25 情况一致。

---

## 二、问题 1：fixture 调用模式回归 —— 📋 生成器代码修复（最高优先）

### 根因（已定位）

- **生成器**：`agent_components/generators/py_export.py:153-157` 生成的 class fixture 为：
  ```python
  base.specification_yaml(get_testcase_yaml('.../setup_{class_slug}.yaml'))
  base.specification_yaml(get_testcase_yaml('.../teardown_{class_slug}.yaml'))
  ```
- **框架契约**（`base/apiutil.py`，外部框架）：
  - `get_testcase_yaml()` 返回 YAML **列表**（本批 setup 均多 block：setup_meter 7 / setup_billing_rule 11 / setup_common_area 21 / setup_prepaid 3 / teardown_prepaid 15 / teardown_common_area 20 …）
  - `specification_yaml(case_info)` **只接受单个 block dict**（内部 `case_info['baseInfo']`）→ 传列表必抛 `TypeError: list indices must be integers or slices, not str`
  - `run_blocks(yaml_path)` 接受路径、逐 block 执行 + 汇总断言（**正确姿势**，测试函数 py_export.py:183 已用）
- **回归对比**：_26 的 fixture 用 `base.run_blocks(path)`（磁盘实证 ✅）；_27 改为 `specification_yaml(get_testcase_yaml(...))`（磁盘实证 ❌）
- **影响**：5 个真实 fixture（setup_meter/billing_rule/settlement_config/prepaid/common_area 各 1）+ teardown，**102 / 112（91%）测试方法** class fixture 阶段直接 ERROR

### 处理方案

| 层 | 改动 |
|:---|:---|
| **生成器** | `py_export.py:153-157` 两处 `base.specification_yaml(get_testcase_yaml('...'))` → `base.run_blocks('...')`（setup + teardown 各 5 个 fixture，共 10 处调用，全部替换） |
| **单测** | 新增/扩展 py_export 单测：生成 fixture 后断言输出为 `run_blocks(`，且不含 `specification_yaml(get_testcase_yaml` |

### 验证

- 单测：fixture 输出模式断言通过
- 重生成 _27 的 `.py` → 磁盘 fixture 为 `run_blocks` → 实跑 `test_SmartElectricity.py` 前 3 个测试类无 class 级 ERROR

---

## 三、问题 2：`eq: {status_code: ...}` 新反模式（23 处）—— 📋 prompt 铁律 + 代码接管 + 检测

### 根因（框架行为实测）

- 框架 `contains_assert` 对 `status_code` **有特殊处理**（与 HTTP 状态码比对）；`equals_assert` 一律按 JSONPath 解析——响应体 `{retCode, msg, data}` 无 `status_code` 字段 → `eq` 必失败
- 既有 prompt 规则（`extraction_prompts.py:172/212/291`）只覆盖**导出/下载/模板接口**（URL 含 export/import/template 等）；**没有通用规则禁止对 `status_code` 用 `eq`/`ne`**
- 结果：LLM 把「导出接口用 contains 查状态码」的模式推广到所有接口 → 本批首次引入 23 处（_21/_24/_25/_26 均为 0）
- 级联：setup 步骤若**唯一断言**就是 `eq: status_code`（setup_meter 7 处），断言先于 extract 执行 → 失败 → 后续 `${get_extract_data(...)}` 全部取不到值

### 涉及文件

| 文件 | 处数 | 说明 |
|:---|:--|:---|
| `setup_data/setup_meter_management.yaml` | 7 | 每步唯一断言即 `eq: status_code` → extract 级联失败 |
| `setup_data/teardown_prepaid_management.yaml` | 15 | 清理步骤全部断言失败 → 数据残留 |
| `test_meter_notify_report_unknown_negative/test_data.yaml` | 1 | `eq: {status_code: 404}` 负向核心断言失效 |

### 处理方案（三层，类比 _25 db 断言/导出断言）

| 层 | 改动 |
|:---|:---|
| **prompt（核心）** | `analyze_yaml_data` + `format_yaml_data` 加通用铁律：「**对 `status_code` 的断言必须用 `contains: {status_code: X}`，禁止 `eq`/`ne`**（不限于导出接口；导出接口维持既有 contains 规则）」 |
| **代码接管（兜底）** | 将 `_takeover_export_assertions`（py_export.py:31，导出专属）**泛化**为通用断言规范化：写盘前扫描所有 step 的 validation，将 `eq: {status_code: X}` / `ne: {status_code: X}` 改写为 `contains: {status_code: X}`；**同轮一并规范化 `contains: <裸字符串>`（问题 4）** → `contains: {message: <字符串>}`。独立方法 + 单测 |
| **生成后检测**（辅助工具，非系统运行） | `logs/yaml_check_smartpower.py` 增加扫描：`eq: {status_code:` / `ne: {status_code:` 与 `contains: <非 dict>` → 标记回炉（实施偏差见九） |

### 验证

- 代码接管单测：构造含 `eq: {status_code: 200}` 的步骤 → 断言写盘前被改写为 `contains: {status_code: 200}`
- 重跑 `logs/yaml_shape_check.py` → 0 处 `eq/ne: status_code`、0 处裸字符串

---

## 四、问题 3：文件缺失 `test_billing_rule_delete_bound_negative/test_data.yaml` —— 📋 补生成 + 检测接入

### 根因

- `test_SmartElectricity.py:262-264` 引用该路径，但目录为**空目录**（0 文件）→ 运行时 `FileNotFoundError`
- 生成期未产出该用例 YAML（疑似生成遗漏/被静默跳过），**且缺失未被生成流程拦截**（需查 `VALIDATION_INTERCEPT.md` 2026-08-12 18:32 的拦截记录确认该用例是否被拦截而未补）

### 处理方案

| 层 | 改动 |
|:---|:---|
| **短期（已取消）** | ~~单独补生成 `test_billing_rule_delete_bound_negative/test_data.yaml`~~ —— 用户确认：空目录由生成异常拦截（schema 校验）产生，属设计规划，**不做补生成**（见九） |
| **长期** | `yaml_ref_check` 逻辑**接入生成流程收尾**（`_generate_all_yamls` 末尾 `_find_missing_yaml_refs`）：`.py` 引用 vs 磁盘 yaml 完整性检查，缺失文件显式警告 + `result["missing_refs"]`，禁止静默放行 |

### 验证

- 补生成后 `yaml_ref_check.py` → 0 缺失
- 实跑 `TestBillingRuleManagement.test_billing_rule_delete_bound_negative` → 正常执行

---

## 五、问题 4：`contains` 裸字符串（3 处）—— 📋 prompt + schema 约束

### 根因

- LLM 输出 `- contains: 收费名称不能为空`（裸字符串）；框架 `contains_assert` 内部 `for k, v in value.items()` 对 str 抛 **`AttributeError`**（非 AssertionError）→ `run_blocks` 不捕获 → 整个测试方法崩溃
- prompt 未明确「validation 断言值必须是 dict」

### 涉及文件

| 文件 | 行号 | 期望文本 |
|:---|:--|:---|
| `test_billing_rule_add_required_missing_negative/test_data.yaml` | 11 | 收费名称不能为空 |
| `test_meter_detail_not_found_negative/test_data.yaml` | 10 | 电表不存在 |
| `test_prepaid_balance_push_invalid_amount_negative/test_data.yaml` | 13 | 金额必须大于0 |

### 处理方案

| 层 | 改动 |
|:---|:---|
| **prompt** | `analyze_yaml_data` + `format_yaml_data` 铁律：「**`contains` 的值必须是 dict**（`{字段: 期望}` 或 `{$.路径: 期望}`），**禁止裸字符串**」 |
| **schema** | `TestData` validation 元素约束为 dict —— 裸字符串直接 Pydantic 校验失败 → 修复轮重生成 |
| **代码接管** | 并入问题 2 的通用规范化（见上） |
| **生成后检测**（辅助工具，非系统运行） | `logs/yaml_check_smartpower.py` 扫描 `contains: <裸字符串>`（实施偏差见九） |

### 验证

- schema 单测：validation 含裸字符串 → 校验失败
- 重跑检查脚本 → 0 处裸字符串

---

## 六、修复优先级

| 优先级 | 动作 | 层 | 状态 |
|:--|:---|:---|:---|
| **P0** | fixture `specification_yaml(get_testcase_yaml(...))` → `run_blocks`（py_export.py，10 处） | 生成器 | ✅ 已实施 |
| **P1** | `status_code` 断言通用铁律（prompt）+ 代码接管规范化（泛化 `_takeover_export_assertions`）+ 检测扫描 | prompt + 代码 + 检测 | ✅ 已实施 |
| **P1** | `contains` 裸字符串：prompt 铁律 + schema 操作数 dict 约束 + 检测扫描 | prompt + schema + 检测 | ✅ 已实施 |
| **P2** | ~~补生成缺失文件~~（不做，见九）+ `yaml_ref_check` 接入生成收尾 | 生成流程 | ✅ 已实施（仅引用检查） |

---

## 七、验证方法

| 项 | 方法 | 目标 |
|:---|:---|:---|
| 问题 1 | py_export 单测（fixture 输出模式）+ 重生成实跑 | fixture 为 `run_blocks`，0 class 级 ERROR |
| 问题 2 | 代码接管单测 + `yaml_shape_check.py` 重扫 | 0 处 `eq/ne: status_code` |
| 问题 3 | 补生成 + `yaml_ref_check.py` | 0 缺失 |
| 问题 4 | schema 单测 + `yaml_shape_check.py` 重扫 | 0 处裸字符串 |
| 端到端 | `test_SmartElectricity.py` 实跑 | 无 TypeError / AttributeError / FileNotFoundError |

---

## 八、结论

- **问题 1 是本批最大回归，纯 Python 生成器侧**：`py_export.py:153-157` 把 fixture 从 `run_blocks(path)` 改成 `specification_yaml(get_testcase_yaml(path))`，违反框架契约（`specification_yaml` 只收单 block dict），5 个真实 fixture 全崩 → 91% 用例 class 级 ERROR。**修复 = 生成器一行级改动**（改回 `run_blocks`），与 YAML 数据质量无关。
- **问题 2/4 均为 prompt 缺口 + 缺代码兜底**：与 _25 的导出断言、db 断言同类——LLM 结构质量已收敛，但「运行时契约」（status_code 只能 contains、validation 值必须 dict）没有在 prompt 铁律 + 代码接管 + 检测三层落地。修复沿用 _25 已验证的三层模式（prompt 铁律为核心，代码接管兜底，生成后检测扫描）。
- **问题 3 是生成遗漏**：补生成 + 把已有引用完整性检查接入生成收尾，杜绝静默放行。
- `logs/` 下的检查脚本（`yaml_shape_check.py` / `yaml_ref_check.py`）复用为后续批次分析工具。

---

## 九、实施偏差说明（2026-08-13 补充）

### 偏差 1：检测扫描加在 `yaml_check_smartpower.py`，不是 `yaml_shape_check.py`

- `logs/yaml_shape_check.py` 实为 `$` 参数解析兼容性小工具（22 行），**并非** shape 检查器；
  真正产出 `logs/YAML_CHECK_REPORT3.md` 的合规检查脚本是 `logs/yaml_check_smartpower.py`（~400 行）。
- 故两条扫描（`eq_status_code` / `contains_bare_string`）加在 `yaml_check_smartpower.py`，
  并将其 `ROOT` 改为命令行第 1 参数覆盖，按批次复用。
- ⚠️ **`logs/` 下脚本均为批次结果检验辅助工具，不在系统中使用、不参与运行**。
  系统侧三层防御仅：**prompt 铁律 + schema 校验（`response_model.py`）+ 代码接管（`py_export.py`）**；
  `logs/` 脚本只用于生成后人工/自动检验输出质量。

### 偏差 2："validation 元素约束为 dict" 语义修正

- 元素必须是 dict 已由 Pydantic `validation: List[Dict[str, Any]]` 字段类型天然保证
  （裸字符串元素直接 `dict_type` 校验失败），无需新增校验器；
- _27 实际崩溃形态是 `contains: <裸字符串>`——**元素是 dict、操作数是字符串**，类型检查拦不住；
- 新增校验器 `TestCase.validate_validation_element_is_dict` 按此形态实现：校验 **操作数**
  （eq/contains/ne 的值）必须为 dict，才是真正拦截点。

### 决策更新：问题 3 缺失文件不做处理

- 用户确认：空目录由生成异常拦截（schema 校验两轮拦截）产生，属设计规划，**不做补生成**；
- P2 仅保留 `yaml_ref_check` 逻辑接入 `_generate_all_yamls` 收尾（`_find_missing_yaml_refs`），
  缺失文件显式警告 + `result["missing_refs"]`，禁止静默放行。
