# YAML 合规问题汇总与修复方案（智慧用电_25）

| 项目 | 内容 |
|:---|:---|
| 日期 | 2026-08-04 |
| 审查报告 | `logs/YAML_CHECK_REPORT3.md` |
| 审查对象 | `testcase/园区基线/智慧用电_25/SmartPower/`（193 blocks / 216 用例 / 126 个非空 YAML） |
| 范围 | **排除「18 个文件缺失」（生成中断导致）**，汇总其余 3 类问题及修复方案 |
| 状态 | **📌 已定稿（2026-08-04）**：问题 1 ✅ 已解决；问题 2/3 方案确认，待实施 |

---

## 一、问题汇总（排除文件缺失）

| # | 问题 | 数量 | 严重度 | 状态 |
|:--|:---|:--|:--|:--|
| 1 | `${}` 表达式参数带引号 | 36 处 / 14 文件 | 🔴 致命 | ✅ **已解决**（框架侧 `parse_dollar_args` 兼容） |
| 2 | `db` 断言后接裸 SQL 字符串 | 9 处 / 7 文件 | 🔴 致命 | 📋 数据库信息参数化（占位） |
| 3 | 导出/下载/模板接口断言模式全错 | 12 文件（全部） | 🟡 警告 | 📋 schema + prompt + 生成后检测 |

> 结构性检查（header、参数层级、`{body:}` 包裹、URL 占位符、extract 前缀、断言运算符、DebugTalk 函数）**全部通过**——LLM 结构质量已收敛，三类问题均属**运行时级契约不一致**。

---

## 二、问题 1：`${}` 参数带引号 —— ✅ 已解决（框架侧兼容完成）

### 框架改动（已完成，最小改动 3 文件）

| 文件 | 改动 |
|:---|:---|
| `base/apiutil.py` | 新增纯函数 `parse_dollar_args(params_str)`：**引号感知逗号拆分 + 剥引号**（含 `json.dumps` 的 `\"` 转义还原）；`replace_load` 内 `split(',')` → `parse_dollar_args(funcs_params)`，url/cookies/json/params/data/validation 所有调用方经同一方法自动全覆盖 |
| `testcase/unit/conftest.py`（新增） | 屏蔽根 conftest 的 session 级网络登录/环境 fixture，单元测试不发网络请求、不污染 `extract.yaml` |
| `testcase/unit/test_replace_load_quotes.py`（新增） | 20 个测试用例 |

### 验证结果（全部通过）

- 单元测试 `pytest testcase/unit/ -v`：✅ **20 passed**（0.17s）
- 根 conftest 网络登录：✅ 未触发（无 accessToken 写入）
- `extract.yaml` 未被污染：✅
- 本批 22 种表达式形态逐一解析：✅ 22/22 成功
- 兼容矩阵：不带引号/单引号/双引号 × 单参/多参 全部兼容

### 效果

- **智慧用电_25 的 36 处 / 14 文件带引号 `${}`，YAML 侧无需任何改动即恢复可用**
- 框架对所有写法（带引号/不带引号）向后兼容，历史批次（_21/_24）一并受益

### 残留建议（可选，非必须）

- 框架已兼容，但生成 prompt 仍建议加「`${}` 参数禁引号」铁律，避免新批次持续产出带引号写法（仅为产出一致性，非功能必需）

---

## 三、问题 2：`db` 断言 —— 数据库信息参数化（新方案）

### 关键修正

生成侧**只有接口定义，没有数据库表结构**，LLM 写的 SQL（表名/字段）是编造的——即使改成 dict 结构 SQL 仍错误。故正确方向是：**无表结构信息时禁止生成 db 断言**，而非教会 LLM 写 dict 格式。

### 设计（2026-08-04 定稿）

生成流程新增 **`db_schema` 参数**（数据库表结构信息）：
- **当前仅占位**：参数存在但无取值逻辑，不传实际值（`db_schema=""`）
- `db_schema` 占位传入**用例生成节点 + 测试数据生成节点**两处
- `db_schema` 为空 → **两个节点都监测断言**，检测到 `db` 类型断言即**拦截，让 LLM 重新生成其他类型断言（eq/contains/ne）**：
  - **用例生成节点**：Excel `expected` 中含 `[db]` → 拦截该用例 → 修复轮改为其他断言
  - **YAML 数据生成节点**：`validation` 中含 `db:` → 拦截 → 修复轮改为其他断言
- 未来接入真实表结构后，`db_schema` 非空 → 才允许生成 `db` 断言

### 落地（三层）

| 层 | 改动 |
|:---|:---|
| **prompt** | 两处都新增 `db_schema` 入参：用例生成 `generate_excel_plan_thinking` + YAML 生成 `analyze_yaml_data`/`format_yaml_data`；为空时铁律「禁止 db 断言，改用 eq/contains/ne」 |
| **schema** | 用例生成：`ExcelPlanValidator` 校验 expected 含 `[db]` → 回炉；YAML 生成：`TestData` validation 含 `db` 键 → 回炉 |
| **生成后检测** | `logs/yaml_check_smartpower.py` 等扫描 `[db]` / `db:` 断言，标记回炉 |

---

## 四、问题 3：导出/下载/模板接口断言（根因 = prompt 缺口，非注解管线）

**运行时根因**：导出返回二进制流，`res.json()` 失败降级 `{}`，`eq: {$.status_code}` 在空 dict 上必败；`contains: {status_code: 200}` 有特殊处理可正确执行。

### 调查结论（2026-08-04 逐层核实）

| 层 | 状态 |
|:--|:--|
| 用例生成节点打标 `is_export` | ✅ 正常（`nodes.py` `apply_all` 写入快照，16 个导出接口 `active=True`） |
| 快照传递注解 | ✅ 正常（thinking 日志证实 LLM **能看到** `is_export.active=true`） |
| **prompt 指令（导出接口用 contains）** | ❌ **缺失——主因** |
| 代码接管兜底（`generators` line 619-621） | ⚠️ 存在且模拟验证能生效，但 _25 实际未生效 |

**决定性证据**（`logs/thinking_trace.log` 18:14 运行，LLM thinking 原文）：

```
该接口 annotations 中 `is_export.active=true`，属于导出接口，返回内容为 Excel 文件流...
- 断言 HTTP 状态码等于 200，使用 `eq` 断言：`status_code = 200`
```

LLM **明确知道是导出接口，却仍计划 `eq`**——因为 `analyze_yaml_data` / `format_yaml_data` prompt 没有告知「导出接口必须用 `contains`」。thinking 计划 eq → format 照抄 eq → 落盘 eq。这是 **12 个导出文件全错、0 个对** 的直接原因：LLM 每次都"合理"地选了 eq，prompt 没拦。

### 修复（prompt 为核心，代码接管兜底）

| 层 | 改动 |
|:---|:---|
| **prompt（核心）** | `analyze_yaml_data` + `format_yaml_data` 加铁律：「URL 含 export/import/template/download/upload 或标注 is_export 的接口，断言**必须用 `contains: {status_code: 200}`**，禁止 `eq`；验证非空用 `ne: {$: ''}`」 |
| **schema** | `is_export` 标注接口的断言非 `contains` 系 → 回炉 |
| **代码接管（保留兜底）** | `generators` line 619-621 保留，并**加单测**：构造 is_export 接口 + 错误 `eq` 断言 → 验证被强制改为 `contains` |
| **生成后检测** | `logs/yaml_check_smartpower.py` 加一条：导出接口 URL + `eq:` 断言 → 标记 |

---

## 五、修复优先级

| 优先级 | 动作 | 状态 |
|:--|:---|:---|
| **P0** | 框架 `parse_dollar_args` 剥引号（问题 1） | ✅ **已完成**（20 单测通过） |
| **P1** | `db_schema` 参数化 + 禁 db 断言（schema + prompt + 检测） | 📋 待实施 |
| **P1** | 导出断言 `contains`（schema + prompt + 检测） | 📋 待实施 |
| **P2** | 补全 18 个缺失文件（生成中断导致） | 📋 待 |
| **P2** | 纠正 skill 文档「陷阱 9」（带引号说法） | 📋 待 |

---

## 六、验证方法

| 项 | 方法 | 目标 |
|:---|:---|:---|
| 问题 1 | `pytest testcase/unit/ -v` + 兼容矩阵实测 | ✅ 已通过 |
| 问题 2 | `db_schema` 为空重生成一批 → 无 db 断言；监测节点拦截 db | 0 db 断言 |
| 问题 3 | 重跑 `logs/yaml_check_smartpower.py` | 导出 eq 断言 0 处 |
| 端到端 | `test_SmartPower.py` 实跑 | 运行无 KeyError / AssertionError |

---

## 七、结论

- **问题 1（`${}` 带引号）已由框架侧 `parse_dollar_args` 一次性解决**——本批 36 处 + 历史批次 YAML 零改动恢复，且经 20 单测 + 22 种表达式形态验证。
- **问题 2（db 断言）** 按「数据库信息参数化」处理：`db_schema` 当前占位为空 → 生成侧禁 db 断言、监测节点不放行，杜绝无表结构下的编造 SQL。
- **问题 3（导出断言）根因已定位 = prompt 缺口**：注解管线（打标→快照→YAML生成）全程正常，LLM 能看到 `is_export.active=true` 但仍计划 `eq`，因为 prompt 未告知「导出接口必须用 contains」。修复以 **prompt 铁律为核心**，代码接管（line 619-621）保留兜底 + 单测，生成后检测加导出 eq 扫描。
- `logs/` 下的检查脚本（`yaml_shape_check.py` 等）留作后续批次复用的分析工具。

## 八、待确认决议记录（2026-08-04 全部确认，已定稿）

| 项 | 决议 |
|:--|:---|
| A. `test_workflow_api.py`（15 个环境依赖失败） | **保留现状**——本地起 Web 服务（`--base-url`）后运行，不注释不跳过 |
| B. 问题 2 `db_schema` 落法 | 占位传**用例生成 + 测试数据生成**两节点；`db_schema` 为空时**两节点都监测 db 类型断言**：用例生成 expected 含 `[db]`、YAML 生成 validation 含 `db:` → **拦截，重新生成其他断言类型**（eq/contains/ne） |
| C. 问题 3 实施范围 | **全做**：prompt 铁律 + schema 回炉 + 代码接管单测 + 生成后检测扫描 |
| D. 实施时机 | **文档定稿后**再实施（本文档为定稿版） |

**定稿后实施清单（P1）**：
1. 问题 2：`db_schema` 占位入参（两节点）→ 用例生成 prompt 禁 `[db]` + YAML 生成 prompt 禁 `db:` 铁律 → 用例生成 `ExcelPlanValidator` 拦 `[db]`、YAML `TestData` 拦 `db:` → `logs/` 扫描 `[db]`/`db:`
2. 问题 3：`analyze_yaml_data`/`format_yaml_data` prompt 导出接口 `contains` 铁律 → schema 拦截导出 eq → 代码接管单测 → `logs/yaml_check_smartpower.py` 扫描导出 eq
