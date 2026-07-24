# YAML 生成后校验方案

> 基于：`logs/YAML_CHECK_REPORT3.md`（智慧用电_13：63 文件，1 致命 + 2 中等，通过率 98.4%）
> 策略：三层防御 + 统一后校验节点 + 修复轮消费
> 状态：方案设计，待实施

---

## 一、背景

智慧用电_13 的 YAML 质量较 _3 提升了 94%（致命问题 16→1），Schema 校验器 + Prompt 铁律覆盖了 7 类结构性问题。但仍有 3 类偶发问题无法被 Schema 拦截，需要代码层兜底。

## 二、整体架构

```
Phase C 生成完成（_generate_all_yamls 返回）
    │
    ▼
_post_validate_yamls(output_dir)  ← 新增节点，纯代码
    │
    ├─ 检查 1: delete body 包裹检测 ({body: [...]} → [...])
    ├─ 检查 2: 断言 key 动态值检测 (key 位置不能用 ${})
    ├─ 检查 3: 断言格式拼合检测 (key/value 拼合错误)
    ├─ ... (后续可扩展)
    │
    ▼
    issues 列表 → 每个 issue 含: {yaml_path, check, current, expected, fix_hint}
    │
    ├─ issues 为空 → 流程结束，写 VALIDATION_INTERCEPT
    │
    ├─ issues > 0 且 ≤ 10% 文件总数 → 注入修复轮
    │   └─ repair_ctx["post_check_issues"] = issues
    │   └─ 修复轮 LLM 看到 fix_hint 精准修改
    │
    └─ issues > 10% 文件总数 → 不触发修复轮
        └─ 写入 _post_validation_errors.json
        └─ 日志告警: "提示词存在系统性问题，建议排查 Prompt"
```

## 三、三层防御

| 层 | 位置 | 作用 | 示例 |
|----|------|------|------|
| **Prompt 铁律** | `format_yaml_data_prompt` | 预防: 提前告知 LLM 规则 | "断言的 key 必须是静态字段名" |
| **Schema 校验** | `response_model.py` validators | 拦截: 结构化错误在生成阶段拦截 | `validate_placeholders` 检查 key 中的 `${}` |
| **代码后检查** | `post_validator.py` | 兜底: Schema 放过的偶发问题统一扫描 | 遍历所有 YAML 检查 delete body 包裹 |

三层不重复——各司其职。Prompt 管"别犯错"，Schema 管"结构化拦"，代码检查管"漏网之鱼"。

## 四、统一管理方法

新增文件：`agent_components/post_validator.py`

```python
class YamlPostValidator:
    """YAML 生成后快速验证，纯代码，不放 LLM。

    挂在 Phase C _generate_all_yamls 返回后、ValidationInterceptor 写报告前。
    产出结构化错误信息，可被 _run_yaml_rounds 修复轮直接消费。
    """

    def validate_all(self, output_dir: str) -> list[dict]:
        """遍历所有 YAML 文件，执行全部注册的检查项。"""
        issues = []
        yaml_files = glob.glob(f"{output_dir}/**/*.yaml", recursive=True)
        for path in yaml_files:
            try:
                with open(path, encoding="utf-8") as f:
                    data = yaml.safe_load(f) or {}
            except Exception:
                continue
            issues.extend(self._check_delete_body_wrapper(data, path))
            issues.extend(self._check_assertion_dynamic_key(data, path))
            issues.extend(self._check_malformed_assertion(data, path))
        return issues
```

每个检查项返回统一结构：

```python
{
    "yaml_path": "SmartElectricity/.../test_data.yaml",
    "check": "delete_body_wrapper",        # 检查项标识
    "severity": "P0",                       # P0/P1/P2
    "line": 42,                             # 问题所在行号
    "current": "json: {body: [...]}",       # 当前错误写法
    "expected": "json: [...]",              # 期望正确写法
    "fix_hint": "数组 body 直接用 json: [...]，去掉 body 包裹层"
}
```

## 五、检查项明细

### 检查 1：delete body 包裹检测

| 项目 | 内容 |
|------|------|
| 严重度 | 🔴 P0 |
| 触发条件 | `method` 为 post/put/patch → `json` 中**存在且仅有 `body` 一个 key** → `json.body` 为**非空数组** → 数组元素为 dict（有 key） |
| 修复方式 | 移除 `body` 包裹层，直接输出数组 |
| Schema 层 | 不改——`{body: [...]}` 是合法 dict，Schema 管不了语义 |
| Prompt 层 | 已有——"数组 body 直接写 json: [...]，不要用 {body: ...} 包裹" |

**触发条件细化（防误报）**：

| 场景 | 触发 | 原因 |
|------|------|------|
| `{"body": [...]}` | ✅ | 典型包裹层误用 |
| `{"body": "text"}` | ❌ | 可能是业务字段 |
| `{"body": {"nested": "value"}}` | ❌ | 可能是业务对象 |
| `{"body": [...], "extra": "field"}` | ❌ | 有额外字段，不是纯包裹层 |

### 检查 2：断言 key 动态值检测

| 项目 | 内容 |
|------|------|
| 严重度 | 🟡 P1 |
| 触发条件 | `validation` 中 `eq/contains/ne/db` 的 **key** 匹配 `\$\{[^}]+\}`（模板变量，非 `$.data.xxx` JSONPath） |
| 修复方式 | key 改为静态 JSONPath，动态值移到 `:` 右边 |
| Schema 层 | 新增——`validate_placeholders` 扩展，用 `r'\$\{[^}]+\}'` 正则精确匹配 key 中的 `${}` 占位符，不误伤 `$.data.code` |
| Prompt 层 | 新增铁律——"断言的 key（`:` 左边）必须是静态字段名或 JSONPath，禁止在 key 位置使用 `${}` 动态值" |
| 正反例 | ❌ `${get_extract_data('code')}: meterCode02` → ✅ `$.data.records[0].code: ${get_extract_data('code')}` |

**Schema 校验正则（精确区分 JSONPath 和模板变量）**：

```python
PLACEHOLDER_PATTERN = re.compile(r'\$\{[^}]+\}')  # 只匹配 ${xxx}，不匹配 $.data.xxx

def validate_assertion_keys(validation: list[dict]) -> list[str]:
    errors = []
    for item in validation:
        for key in item.keys():
            if PLACEHOLDER_PATTERN.search(key):
                errors.append(f"断言 key 中不能使用 ${{}} 占位符: {key}")
    return errors
```

### 检查 3：断言格式拼合检测

| 项目 | 内容 |
|------|------|
| 严重度 | 🟡 P2 |
| 触发条件 | `validation` 中 key 或 value 包含未配对的引号（单引号/双引号数量为奇数，忽略转义） |
| 修复方式 | **仅告警不修复**——写入 `_post_validation_issues.json`，不注入修复轮 |
| Schema 层 | 不做——value 是任意字符串，无法判断"格式正确" |
| Prompt 层 | 不改——偶发 1/63，json_mode 输出的极低概率 corrupt |

**为什么只告警不修复**：

LLM 拿到 "格式明显错乱，请修正" 没有明确的目标值。修复轮可能根据上下文乱猜，反而引入新错误。对于 P2 问题，安全网比自动修更重要。

```python
def _has_unmatched_quotes(self, s: str) -> bool:
    """检测字符串中是否有未配对的引号（忽略转义）"""
    single_quotes = s.count("'") - s.count("\\'")
    double_quotes = s.count('"') - s.count('\\"')
    return single_quotes % 2 == 1 or double_quotes % 2 == 1
```

---

### 策略汇总

| 检查 | 严重度 | 修复轮 | 动作 |
|------|--------|--------|------|
| delete body 包裹 | P0 | ✅ 注入 | 有确切 expected |
| 断言 key 动态值 | P1 | ✅ 注入 | 有确切 expected |
| 断言格式拼合 | P2 | ❌ 不注入 | 只打日志 + 写入报告 |

## 六、修复轮消费

`_run_yaml_rounds` 读取结构化错误，注入到 `repair_ctx`：

```python
repair_ctx = {
    "prior_output": ...,           # 上一轮原始输出（原有）
    "error_detail": ...,           # Pydantic 校验错误（原有）
    "error_pattern_summary": ...,  # 全批次错误模式统计（原有）
    "post_check_issues": [         # 新增：代码后检查问题列表
        {
            "yaml_path": "SmartElectricity/.../test_data.yaml",
            "check": "assertion_dynamic_key",
            "current": "${get_extract_data('code')}: meterCode02",
            "expected": "$.data.records[0].code: ${get_extract_data('code')}",
            "fix_hint": "断言的 key 必须是静态 JSONPath，动态值放在 : 右边"
        }
    ]
}
```

修复轮 LLM 拿到 `fix_hint` 后精准修改，不需要自己猜。

## 七、调用位置

```
_generate_all_yamls(excel_path, api_defs_json, user_ctx)
    │
    ├─ _run_yaml_rounds(yaml_tasks, ...)
    │     └─ 返回 {total, success, failed, repaired, rounds, errors_file}
    │
    ├─ [新增] _post_validate_yamls(output_dir)
    │     ├─ YamlPostValidator().validate_all(output_dir)
    │     ├─ 返回 issues 列表
    │     └─ 写入 _post_validation_issues.json
    │
    ├─ [新增] 如果 issues 非空 且 修复轮未耗尽 (rounds < YAML_REPAIR_ROUNDS):
    │     ├─ 将 affected tasks 注入 post_check_issues
    │     └─ 追加一轮修复: _run_yaml_rounds(affected_tasks, ..., post_check_issues)
    │
    ├─ ValidationInterceptor.write_report("logs")
    └─ 返回结果
```

## 八、实施清单

| 序号 | 文件 | 改动 | 对应问题 |
|------|------|------|---------|
| 1 | `agent_components/post_validator.py` | **新建** YamlPostValidator 类 + 3 个检查方法 | 1, 2, 3 |
| 2 | `agent_components/generators.py` | `_generate_all_yamls` 返回前调用 `_post_validate_yamls` | 调用点 |
| 3 | `agent_components/generators.py` | `_run_yaml_rounds` 新增 `post_check_issues` 参数 | 修复轮消费 |
| 4 | `prompts/extraction_prompts.py` | `format_yaml_data_prompt` 新增铁律：断言 key 禁止 `${}` | 2 |
| 5 | `prompts/extraction_prompts.py` | `repair_yaml_data_prompt` 新增 `post_check_issues` 输入 | 修复轮 |
| 6 | `prompts/response_model.py` | `validate_placeholders` 扩展：检查 validation key 中的 `${}` | 2 |

## 九、验证标准

实施后用智慧用电_13 的 test_plan.xlsx 重新生成 YAML，对比：
- 致命问题: 1 → 0
- 中等问题: 2 → 0
- 修复轮触发: 仅当 issues < 10% 总数
- `_post_validation_issues.json`: 非空时需人工审查

## 十、扩展性

后续新增检查项只需三步：
1. 在 `YamlPostValidator` 中加 `_check_xxx` 方法
2. 在 `validate_all` 中注册
3. 按需加铁律/Schema

不需要改生成链路代码。
