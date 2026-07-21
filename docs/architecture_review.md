# 架构审查报告

> 审查引擎: Skill B (risk-detective) | 规则集: docs/RULES_INDEX.md + docs/RULES_DETAIL.md
> 扫描范围: Phase B/C 优化相关源文件 | 扫描日期: 2026-07-21

---

## 第一部分：审查摘要

| 项目 | 内容 |
| :--- | :--- |
| 扫描范围 | Phase B/C 优化涉及的全部源文件（10 个文件） |
| 审查时间 | 2026-07-21 |
| P0 问题数 | **6（全部已在审查中修复）** |
| P1 问题数 | 0 |
| P2 问题数 | 1（producer.join 无超时） |
| **存在规则盲区** | **FALSE** |
| 审查结论 | ✅ **通过** — 所有 P0 已修复，P2 为低风险改进项 |

---

## 第二部分：问题统计概览

| 规则 | 违反次数 | P0 | P1 | P2 |
| :--- | :--- | :--- | :--- | :--- |
| M2: LLM/Embedding 交互规范 | 3 | 3 | 0 | 0 |
| M3: 异常处理与日志 | 3 | 3 | 0 | 0 |
| M4: 并发安全 | 1 | 0 | 0 | 1 |
| M8: 数据真实性与缺失阻断 | 1 | 1 | 0 | 0 |

---

## 第三部分：风险详情清单

### [P0-已修复] ISSUE-001：Producer 线程异常时丢弃已计算的 API 定义

- **触发规则**: `M8` + `M2`
- **风险位置**: `agent_components/generators.py:817`
- **问题**: thinking 失败时，`except` 块传递空列表 `[]` 作为 `filtered_apis`。此时 `filtered` 已在 thinking 调用之前成功计算，不应丢弃。
- **风险推演**: LLM 在无接口定义的情况下盲写 YAML → 所有字段为幻觉值 → 运行时必失败。
- **修复**: 在 try 块外初始化 `filtered = []`，except 块使用 `(story, filtered, None)` 保留已计算的 API 定义。

### [P0-已修复] ISSUE-002：Producer 线程异常日志缺少 exc_info=True

- **触发规则**: `M3`
- **风险位置**: `agent_components/generators.py:815`
- **问题**: 后台线程异常时唯一的诊断信息即日志行。缺 `exc_info=True` 导致完整回溯丢失。
- **修复**: 添加 `exc_info=True`。

### [P0-已修复] ISSUE-003：`_thinking_per_story` 硬编码 thinking=enabled

- **触发规则**: `M2`
- **风险位置**: `agent_components/generators.py:188-189`
- **问题**: 未检查 `config.ENABLE_THINKING`。禁用 thinking 时仍发送参数可能出错。
- **修复**: 添加条件检查 `if config.ENABLE_THINKING: ... else: disabled`。

### [P0-已修复] ISSUE-004：`_generate_dependency_map_node` 硬编码 thinking=enabled

- **触发规则**: `M2`
- **风险位置**: `agent_components/nodes.py:622-623`
- **问题**: 同 ISSUE-003。
- **修复**: `"enabled" if config.ENABLE_THINKING else "disabled"`。

### [P0-已修复] ISSUE-005：`dir()` 检查变量存在性

- **触发规则**: `M3`
- **风险位置**: `agent_components/nodes.py:687-689`
- **问题**: `'content' in dir()` 在重试时可能引用旧值。`dir()` 模式不常见且脆弱。
- **修复**: 在循环外初始化 `content = ""`，except 块直接使用 `content[:3000]`。

### [P0-已修复] ISSUE-006：`_invoke_structured` 不捕获临时 API 错误

- **触发规则**: `M3`
- **风险位置**: `agent_components/nodes.py:1011-1022`
- **问题**: `APITimeoutError`、`RateLimitError`、`InternalServerError` 不参与重试，直接传播导致长时批量任务中断。
- **修复**: except 元组增加这三个异常类。

---

### [P2] ISSUE-007：`producer.join()` 无超时

- **触发规则**: `M4`
- **风险位置**: `agent_components/generators.py:962`
- **问题**: 无限等待。若 producer 挂起，主线程永久阻塞。
- **修复建议**: `producer.join(timeout=300)` + `is_alive()` 检查。

---

## 第四部分：审查员备注

### Queue(maxsize=1) 设计说明（非缺陷）

`ready_queue = Queue(maxsize=1)` 是实现 Prefetch 流水线"前置依赖锁"的核心机制——maxsize=1 确保任何时候 LLM 调用并发 ≤ 1 thinking + YAML_CONCURRENCY json_mode。这与方案文档 §3.2 一致，属于有意的架构取舍，不是并发缺陷。

### _MsgBuilder 设计说明（非缺陷）

`_MsgBuilder` 用 `str.replace` 代替 `ChatPromptTemplate`，绕过了 LangChain 对 JSON 示例中花括号的验证。代价是模板变量缺失时静默通过（而非报错）。在 thinking prompt 场景下可接受——变量由调用方显式控制。

---

## 第五部分：已通过审查的文件清单

| 文件 | 违规 | 状态 |
|:---|:---|:---|
| `prompts/response_model.py` | 0 | ✅ 通过 |
| `prompts/extraction_prompts.py` | 0 | ✅ 通过 |
| `prompts/definitions.py` | 0 | ✅ 通过 |
| `agent_components/state.py` | 0 | ✅ 通过 |
| `agent_components/graph_builder.py` | 0 | ✅ 通过 |
| `settings.py` | 0 | ✅ 通过 |
| `config.py` | 0 | ✅ 通过 |
| `web/tasks.py` | 0 | ✅ 通过 |
| `agent_components/generators.py` | 4 (已修复) | ✅ 通过 |
| `agent_components/nodes.py` | 3 (已修复) | ✅ 通过 |
