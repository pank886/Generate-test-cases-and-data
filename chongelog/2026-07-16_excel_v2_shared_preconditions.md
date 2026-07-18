# 变更计划：Excel V2 — 双 Sheet + 共享前置

## 基本信息

| 项目 | 内容 |
|:---|:---|
| 变更日期 | 2026-07-16 |
| 变更类型 | 架构改造 |
| 状态 | ⚠️ 部分已实施，Excel 列结构待调整 |

---

## 1. 为什么改

1. 共享前置不可用：thinking 节点输出引用编号（执行共享前置X），Excel 里看不到实际步骤
2. 前置无法关联用例：缺少反向索引
3. 输出目录不可读：时间戳目录无法区分内容

---

## 2. 最终 Excel 结构（参考 D:\1-ceshi\测试资料\test_plan.xlsx）

### Sheet 1 "测试计划"（9 列，严格对齐参考文件 `D:\1-ceshi\测试资料\test_plan.xlsx`）

| 列 | 含义 | 来源 |
|:---|:---|:---|
| `@allure.epic` | 项目名称（如"园区基线"） | **代码**：模块树父节点 |
| `@allure.feature` | 模块名称（如"健身房管理"） | **代码**：模块树当前节点 |
| `@allure.story` | 子模块名称（如"设施管理"） | **LLM**：`tc.story`，从文档提取，嵌套时 `A-a` |
| `@allure.title` | 用例名称（如"设施管理-新增设施-正向"） | **LLM**：`tc.title` |
| fixture等级 | — | **代码**：固定 `danyuan` |
| 用例编号 | TC-xxx | **LLM**：`tc.id`，全局唯一 |
| 前置步骤 | PRE 列表 | **LLM**：`tc.preconditions`，逗号分隔 |
| 执行步骤 | — | **LLM**：`tc.steps` |
| 预期结果 | — | **LLM**：`tc.expected` |

### Sheet 2 "共享前置"（5 列）

| 列名 | 来源 |
|:---|:---|
| 前置编号 | `pre.id` |
| 前置名称 | `pre.name` |
| 详细步骤 | `pre.steps` |
| 预期结果 | `pre.expected` |
| 关联用例 | 代码反向计算（遍历 test_cases 找引用该 PRE 的 TC） |

> Excel 不存英文名。英文翻译（模块名→`Test*`、用例名→`test_*`）**推迟到 Phase C 下游**完成。测试数据 YAML 文件名也由 Phase C 生成。减少 LLM 翻译带来的幻觉。

---

## 3. Pydantic 模型

```python
class SharedPrecondition(BaseModel):
    id: str              # PRE-001
    name: str            # 已创建测试跑步机
    steps: str           # 步骤文本
    expected: str        # 预期文本

class TestCaseRow(BaseModel):
    id: str              # TC-001
    story: str           # 子模块名（对应 @allure.story），LLM 从文档提取
    title: str           # 用例名称（对应 @allure.title）
    preconditions: list[str]  # ["PRE-001"]
    steps: str
    expected: str
# 注：@allure.epic 和 @allure.feature 由代码从模块树填入，不走 LLM

class ExcelPlanV2(BaseModel):
    shared_preconditions: list[SharedPrecondition]
    test_cases: list[TestCaseRow]
    file_name: str = "test_plan.xlsx"
```

---

## 4. Thinking 节点 prompt

`analyze_test_points_raw` 输出格式：

```
## 共享前置
- PRE-001: 前置名称（模块：所属模块名）
    步骤: 1.操作步骤1\n2.操作步骤2
    预期: 操作完成后的预期状态

## 测试用例
- TC-001: 用例标题
    模块: 所属模块中文名
    场景: 场景名
    前置: PRE-001 或 无
    步骤: 1.操作步骤1\n2.操作步骤2
    预期: 1.预期结果1\n2.预期结果2
```

强制规则：步骤和预期条数必须相等、前置引用只能是 PRE-xxx、编号全局唯一。

---

## 5. 填表节点 prompt

`generate_excel_plan_node` 输出双数组 JSON：

```json
{
  "shared_preconditions": [...],
  "test_cases": [...],
  "file_name": "test_plan.xlsx"
}
```

修复 prompt（重填失败行时）传入原始 thinking 文本 `original_test_analysis`。

---

## 6. 输出目录

按模块树路径组织，智能合并：

```
首次: TESTCASE_BASE/园区基线/健身房管理/test_plan.xlsx
二次: TESTCASE_BASE/园区基线/健身房管理_2/test_plan.xlsx
```

规则：路径为空 → 直接用；路径有内容 → 加 `_2`、`_3`...直到找到空目录。使用 `state["confirmed_module"]` 确定模块树路径，不用 LLM 的 module 字段。

---

## 7. 校验

- Pydantic 逐条校验：必填非空、步骤/预期条数一致、PRE 引用完整性
- 文件层校验：Sheet1 9列表头、Sheet2 5列表头、行数据完整性
- 失败写入 `thinking_trace.log`（格式一致）→ 前端提示

---

## 8. 下游映射（Phase C，本次不动，下次单独计划）

```
python_files     = test_{project_en}.py     ← 翻译项目名称
python_classes   = Test{module_en}          ← 翻译模块名称
python_functions = test_{title_en}          ← 翻译用例标题
yaml 名称        = test_{title_en}.yaml     ← 同 python_functions
```

---

## 9. 涉及文件

| 文件 | 改动 |
|:---|:---|
| `prompts/response_model.py` | 新增 `SharedPrecondition`、`TestCaseRow`、`ExcelPlanV2` |
| `prompts/definitions.py` | `analyze_test_points_raw` + `generate_excel_plan_node` |
| `prompts/extraction_prompts.py` | 修复 prompt 加 `original_test_analysis` |
| `agent_components/nodes.py` | `_generate_excel_plan_node`：双Sheet + 引用校验 + 智能合并路径 |
| `agent_components/validator.py` | 双Sheet 表头 + 行校验 |
| `agent_components/graph_builder.py` | 已删 `format_test_points` 和 `bridge_api_defs` |
| `web/tasks.py` | 前端响应消息、读 `thinking_trace.log` 提失败信息 |
