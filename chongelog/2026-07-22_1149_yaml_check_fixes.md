# YAML 合规审查 — 生成侧修复方案

> 基于：`logs/YAML_CHECK_REPORT.md`（57 个 YAML 文件，9 类问题）
> 策略：**Prompt 强化 + Schema 严格校验**，不静默修正。校验失败抛 ValueError 带清晰错误信息，倒逼 LLM 在修复轮中自查改正。

---

## 设计原则

| 原则 | 说明 |
|------|------|
| 不静默修正 | 不做 neq→ne、不做 JSONPath 补 $、不做 params 迁移——LLM 必须自己写对 |
| 校验即教学 | 每条 ValueError 包含：① 错在哪 ② 为什么错 ③ 正确做法是什么 |
| 拦截可度量 | 每次校验失败记录到 `ValidationInterceptor`，汇总写入 `logs/VALIDATION_INTERCEPT.md` |
| 搭配修复轮 | 校验失败 → 进重生成循环 → LLM 看到错误信息自查改正 |

---

## 改动汇总

### 已实施

| # | 问题 | 严重度 | 改动位置 | 方式 |
|---|------|--------|---------|------|
| 1 | body 被 `{body: [...]}` 包裹 | 🔴 | `response_model.py` | Schema 类型放宽 ✅ |
| 2 | URL 含未解析 `${}` | 🔴 | `response_model.py` + `extraction_prompts.py` | Schema 校验拦截 + Prompt 强化 |
| 3 | 断言运算符 `neq` 不支持 | 🔴 | `response_model.py` + `extraction_prompts.py` | Schema 校验拦截 + Prompt 强化 |
| 4 | 缺少 `header` 字段 | 🔴 | `response_model.py` + `extraction_prompts.py` | Schema 校验拦截 + Prompt 强化 |
| 5 | `params` 错放 baseInfo 层级 | 🟠 | `response_model.py` + `extraction_prompts.py` | Schema 校验拦截 + Prompt 强化 |
| 6 | POST 用 params 而非 json | 🟠 | `response_model.py` + `extraction_prompts.py` | Schema 校验拦截 + Prompt 强化 |
| 7 | GET 用 json 传参 | 🟠 | `response_model.py` + `extraction_prompts.py` | Schema 校验拦截 + Prompt 强化 |
| 8 | validation 为空 | 🟡 | `response_model.py` + `extraction_prompts.py` | Schema 校验拦截 + Prompt 强化 |
| 9 | extract 非 `$` 开头 | 🟡 | `response_model.py` + `extraction_prompts.py` | Schema 校验拦截 + Prompt 强化 |

---

## 涉及文件

| 文件 | 改动 |
|------|------|
| `prompts/response_model.py` | ① `request_body` 类型放宽 ② `ValidationInterceptor` 拦截统计类 ③ StepData 4 个 validator ④ TestCase 3 个 validator |
| `prompts/extraction_prompts.py` | ① `analyze_yaml_data_prompt` thinking 阶段约束更新 ② `format_yaml_data_prompt` 铁律全面重写（12→13 条） |
| `agent_components/generators.py` | ① `_run_yaml_rounds` 开始时 reset 拦截器 ② 结束时 write_report 写入 `logs/VALIDATION_INTERCEPT.md` |

---

## Schema 校验器清单

### StepData 层（4 个）

| 校验器 | 拦截规则 key | 触发条件 |
|--------|-------------|---------|
| `validate_url_no_placeholder` | `url含动态占位符` | baseInfo.url 含 `${` |
| `validate_header_exists` | `baseInfo缺header` | baseInfo 缺 header 键 |
| `validate_no_params_in_baseinfo` | `params错放baseInfo` | baseInfo 含 params/json/data |
| `validate_method_body_match` | `GET/DELETE误用json` / `POST误用params` | GET+json 或 POST+params+JSON头 |

### TestCase 层（3 个）

| 校验器 | 拦截规则 key | 触发条件 |
|--------|-------------|---------|
| `validate_no_neq_operator` | `断言运算符neq` | validation 数组含 `neq` |
| `validate_extract_jsonpath` | `extract缺$前缀` | extract 值不以 `$` 开头 |
| `validate_validation_not_empty` | `validation为空` | validation 为空数组 |

---

## 拦截日志

每次 generation run 结束后，`logs/VALIDATION_INTERCEPT.md` 包含：

- 总拦截次数
- 各规则拦截次数与占比
- 各规则的错误信息样本（最多 3 条）

用于分析提示词优化方向：命中次数最多的规则 → 强化对应 Prompt 铁律。

---

## Prompt 铁律变更对照

### `format_yaml_data_prompt`（json_mode 阶段）

| # | 改前 | 改后 |
|----|------|------|
| 1 | url 只写路径，禁止带域名 | **新增**：url 禁止 ${}，动态参数用 params 传递 |
| 3 | 请求体三选一（json/params/data 按用途） | 按 HTTP 方法约束：GET/DELETE→params，POST/PUT/PATCH→json |
| 4 | "仅 params 或文件上传时不写 header" | **修正**：每个 baseInfo 必须有 header（GET 写 {}） |
| - | （无） | **新增 #5**：params/json/data 只能放在 testCase 内 |
| 9 | 断言从 returns 中选，extract 以 $ 开头 | **扩展**：断言运算符只能用 eq/contains/ne/db，neq 不支持 |
| 10 | 同类型断言合并 | **合并进 #11**：validation 至少一条，同类合并 |
| 12 | - | **新增 #13**（原 #12）：禁止 Markdown，只输出 JSON |

### `analyze_yaml_data_prompt`（thinking 阶段）

- baseInfo 约束新增：header 必须存在、仅含四个字段
- 新增：url 禁止 ${}、params/json/data 归属 testCase
- 新增：neq 非法、validation 不能为空、JSONPath 必须 $. 开头

---

## 工作流影响

```
LLM 输出 JSON
  → Pydantic 校验
    → 通过 → 写入 YAML
    → 失败 → ValueError（含错误原因 + 正确做法）
      → 进入修复轮
        → LLM 读取错误信息 → 自查改正 → 重新输出
        → 仍失败 → 终态登记到 _generation_errors.json
      → 同时记录到 ValidationInterceptor
        → 运行结束汇总写入 logs/VALIDATION_INTERCEPT.md
```
