# 变更计划：Phase B 资源冲突消解 + Phase C 下游适配

| 项目 | 内容 |
|:---|:---|
| 变更日期 | 2026-07-16 |
| 变更类型 | 新增节点 + 适配改造 |
| 涉及文件 | `response_model.py`, `definitions.py`, `nodes.py`, `settings.py`, `generators.py`, `web/tasks.py` |

---

# ============================================================
# 第一部分：Phase B — 资源冲突消解
# ============================================================

## B1. 为什么改

同一场景下多个用例对同一前置资源执行写操作（删除、修改），共享一个 PRE 会导致：
- TC-001 删除设备1（成功）→ TC-002 也删除设备1（失败，因为已被删）
- 两个用例都写"预期成功"，但第二个必失败

需要在填表节点和 Excel 写入之间插入一个**纯代码消解节点**，自动识别并隔离冲突资源。

## B2. `TestCaseRow` 新增字段

```python
class TestCaseRow(BaseModel):
    # ... 现有字段 ...
    mutates_data: bool = Field(
        default=False,
        description="内部元数据：是否为写操作（增删改等）。LLM 输出，不写入 Excel"
    )
    is_negative_test: bool = Field(
        default=False,
        description="内部元数据：是否为负向测试（预期失败等）。LLM 输出，不写入 Excel"
    )
```

**填表 prompt 追加**：

```
分析每个用例的【执行步骤】和【预期结果】：
- mutates_data: 步骤中含增/删/改/状态变更/重置/清理 → true；仅查询 → false
- is_negative_test: 预期结果含"失败/报错/异常/不存在/无权/冲突/重复" → true；否则 false
```

**兼容性**：均有默认值 `False`，validator 不改，Excel 列数不变。

## B3. 可配置关键词库（`settings.py`）

```python
# Phase B 资源冲突消解 — 写操作关键词（中英文），代码层兜底 LLM 漏标
RESOURCE_MUTATE_KEYWORDS: list[str] = [
    # 删除类
    "删除", "移除", "销毁", "删掉", "清空",
    "DELETE", "/del", "/remove", "/delete",
    # 修改类
    "修改", "更新", "编辑", "调为", "变更",
    "UPDATE", "PUT", "PATCH", "/modify", "/edit",
    # 新增类
    "新增", "添加", "创建", "增加",
    "POST", "/add", "/create",
]
```

## B4. 消解器算法（纯代码）

```
全部 test_cases
 → 无前置 或 mutates_data 已标 → 跳过
 → 关键词库匹配 steps → 命中则 mutates_data=true
 → 构建 PRE → 正向写操作用例列表（mutates_data=true 且 is_negative_test=false）
 → 同一 PRE 被 ≥2 个正向写操作用例引用 → 克隆隔离
```

```python
def _resolve_resource_conflicts(plan: ExcelPlanV2) -> ExcelPlanV2:

    # 1. 层层过滤，代码兜底 LLM 漏标
    for tc in plan.test_cases:
        if not tc.preconditions or tc.mutates_data:
            continue
        if any(kw in tc.steps for kw in RESOURCE_MUTATE_KEYWORDS):
            tc.mutates_data = True

    # 2. 构建 PRE → 正向写操作用例列表
    pre_refs = defaultdict(list)
    for tc in plan.test_cases:
        if not tc.mutates_data or tc.is_negative_test:
            continue
        for pid in tc.preconditions:
            pre_refs[pid].append(tc)

    # 3. 检测冲突 → 克隆隔离
    for pre_id, ref_list in pre_refs.items():
        if len(ref_list) <= 1:
            continue
        for tc in ref_list[1:]:
            original = _find_pre(plan, pre_id)
            clone_id = f"{pre_id}_isolated_{tc.id}"
            plan.shared_preconditions.append(SharedPrecondition(
                id=clone_id,
                name=f"{original.name}（{tc.id}专用）",
                steps=original.steps,
                expected=original.expected,
            ))
            tc.preconditions = [
                clone_id if p == pre_id else p for p in tc.preconditions
            ]
```

### B4-1. 克隆标记（可追溯）

`SharedPrecondition` 新增字段：

```python
class SharedPrecondition(BaseModel):
    # ... 现有字段 ...
    cloned_from: Optional[str] = Field(
        default=None,
        description="克隆来源 PRE id，如 PRE-001。非克隆时为 null，不写入 Excel"
    )
```

消解器克隆时自动填入：

```python
plan.shared_preconditions.append(SharedPrecondition(
    id=clone_id,
    name=f"{original.name}（{tc.id}专用）",
    steps=original.steps,
    expected=original.expected,
    cloned_from=pre_id,  # ← 记录克隆来源
))
```

**作用**：审查者可通过 `cloned_from` 追溯到原始 PRE，了解哪些用例之间存在资源冲突。该字段不写入 Excel，仅存在于内存模型中供日志/调试使用。

## B5. 流程位置（嵌入方式：方案 A）

**不拆分图节点**，`_resolve_resource_conflicts` 直接在 `_generate_excel_plan_node` 内部调用：

```
analyze_test_points_raw (thinking)
 → generate_excel_plan (json_mode, 填表+mutates_data+is_negative_test)
   → 【新增】_resolve_resource_conflicts (纯代码消解，LLM 输出 → Excel 写入之间)
     → 校验 + 写 Excel
```

**选择理由**：
- 不改变 LangGraph 图拓扑，`graph_builder.py` 无需修改
- 消解器紧邻校验逻辑，代码内聚性更好
- 消解后的 `valid_cases` 直接用于 Sheet 写入，数据流清晰

具体插入位置（`nodes.py::_generate_excel_plan_node`）：

```
LLM 生成 plan、Pydantic 校验、修复循环 → confirmed_rows 确定
  → 【在此调用】_resolve_resource_conflicts(plan)
    → 基于 confirmed_rows 写双 Sheet Excel

---

# ============================================================
# 第二部分：Phase C — 下游适配（YAML + PY 生成）
# ============================================================

## C1. 为什么改

Excel V2 改成了 9 列双 Sheet 结构，`_read_excel_rows` 还按旧的 10 列格式读，列索引全错位。英文翻译推迟到了 Phase C。

## C2. 新旧列对照

| 旧 10 列 | 新 9 列 | Phase C 用途 |
|:---|:---|:---|
| 项目名称 | `@allure.epic` | — |
| Allure Epic | —（同 epic） | — |
| 模块名称(en) | `@allure.feature` | → `test_{feature_en}.py` |
| Allure Feature | —（同 feature） | — |
| Allure Story | `@allure.story` | → `class Test{story_en}` |
| 用例名称(en) | ❌ 已删除 | Phase C 翻译：`test_{title_en}` |
| 执行步骤 | 执行步骤 | YAML 步骤 |
| 测试数据YAML | ❌ 已删除 | Phase C 生成：`test_{title_en}.yaml` |
| 是否启用 | ❌ 已删除 | — |
| — | 用例编号 | TC-xxx |
| — | 前置步骤 | YAML 前置数据 |
| — | 预期结果 | YAML 断言（[eq]/[contains]/[ne]/[db]） |
| — | Sheet2 共享前置 | YAML 前置复用 + PY 前置方法 |

## C3. `_read_excel_rows` 适配 9 列 + Sheet2

```python
{
    "epic": row[0],          # @allure.epic
    "feature": row[1],       # @allure.feature
    "story": row[2],         # @allure.story
    "title": row[3],         # @allure.title
    "fixture_level": row[4], # fixture等级
    "case_id": row[5],       # 用例编号 TC-xxx
    "preconditions": row[6], # 前置步骤
    "steps": row[7],         # 执行步骤
    "expected": row[8],      # 预期结果
}
```

## C4. 英文翻译（新增 LLM 调用）

```
输入: feature 列表 + story 列表 + title 列表
输出: JSON {feature_en: {...}, story_en: {...}, title_en: {...}}

python_files     = test_{feature_en}.py        ← 翻译自 @allure.feature
python_classes   = Test{story_en}              ← 翻译自 @allure.story
python_functions = test_{title_en}             ← 翻译自 @allure.title
yaml 文件名       = test_{title_en}.yaml        ← 同 python_functions
```

| 输出项 | 来源列 | 示例 |
|:---|:---|:---|
| PY 文件名 | `@allure.feature` | `test_GymManagement.py` |
| PY class 名 | `@allure.story` | `TestFacilityMgmt` |
| PY function 名 | `@allure.title` | `test_facility_add_positive_001` |
| YAML 文件名 | 同 function | `test_facility_add_positive_001.yaml` |

### C4-1. 翻译幂等性保障（三层防御）

**问题**：LLM 翻译不是幂等的——同一段中文两次调用可能得到不同英文（`GymManagement` vs `GymMgmt`），导致重新生成时文件名漂移。

**措施一：翻译缓存**

翻译结果写入 Excel 同级目录 `translation_cache.json`：

```json
{
  "feature_en": {"设施管理": "FacilityManagement", "订单管理": "OrderManagement"},
  "story_en": {"设施添加": "FacilityAdd", "设施修改": "FacilityModify"},
  "title_en": {"设施管理-新增设施-正向": "facility_add_positive_001"}
}
```

每次翻译前先查缓存，命中则直接使用。缓存 key 为中文原文，确保同一输入总是得到同一输出。

**措施二：Sanitize 后处理**

LLM 输出后强制清洗，确保合法 Python identifier：

```python
def _sanitize_en(name: str) -> str:
    # 去空格、去特殊字符、驼峰保持
    sanitized = re.sub(r'[^a-zA-Z0-9_]', '', name.replace(' ', '_'))
    if not sanitized or sanitized[0].isdigit():
        sanitized = '_' + sanitized
    return sanitized
```

**措施三：拼音 Fallback**

LLM 调用失败或超时时，使用 `pypinyin` 生成首字母缩写作为降级方案：

```
设施管理 → SSGL → 作为 feature_en 的兜底值
```

降级时打 WARNING 日志，提醒用户检查并手动修正。

## C5. PY 生成适配

- 同一 `feature` 下所有 class 写入同一个 `.py` 文件
- 同一 `story` 下所有 function 合并在同一个 class 中

**目录结构**（参考 `testcase/Example/`）：

```
testcase/<feature_en>/
├── __init__.py
├── test_<feature_en>.py               ← PY 文件
├── setup_data/                         ← class 级 fixture YAML
│   ├── setup_<class_slug>.yaml        ← 前置：创建该 class 需要的资源
│   └── teardown_<class_slug>.yaml     ← 后置：清理资源
├── <func1_en>/                         ← TC-001 的 YAML 目录
│   ├── step1_xxx.yaml
│   └── step2_xxx.yaml
└── <func2_en>/                         ← TC-002 的 YAML 目录
    └── ...
```

**PY 文件结构**（一个 class 示例）：

```python
@pytest.fixture(scope="class")
def setup_<class_slug>():
    # yield 前 → 创建资源（来自 Sheet2 共享前置，去重后写入 setup_xxx.yaml）
    read = ReadYamlData()
    read.write_yaml_data({...})                    # 写入动态参数
    base = RequestsBase()
    base.specification_yaml(get_testcase_yaml(     # 调用前置 YAML
        './testcase/<feature_en>/setup_data/setup_<class_slug>.yaml'))
    yield  # ← 交接给测试方法
    # yield 后 → 清理资源（teardown_xxx.yaml）
    base.specification_yaml(get_testcase_yaml(
        './testcase/<feature_en>/setup_data/teardown_<class_slug>.yaml'))

@allure.story('<story>')
@pytest.mark.danyuan
@pytest.mark.usefixtures("setup_<class_slug>")
class Test<story_en>:
    @allure.title('<title>')
    @pytest.mark.order(1)
    @pytest.mark.parametrize('params', get_testcase_yaml(
        './testcase/<feature_en>/<func1_en>/step1_xxx.yaml'))
    def <func1_en>(self, params):
        RequestsBase().specification_yaml(params)
```

## C6. YAML 生成适配

- 从 `expected` 解析 `[eq]`/`[contains]`/`[ne]`/`[db]` 断言关键词，映射为 `validation` 字段
- `setup_data/` 目录：一个 class 一个 `setup_<slug>.yaml` + `teardown_<slug>.yaml`，内容从 Sheet2 共享前置去重后生成
- `<func_en>/` 目录：每个 TC 一个目录，steps 拆分为独立 YAML 文件（step1/step2...）
- 用例自身的前置步骤（TC 的 preconditions 列）不在 func 目录中重复，仅引用 fixture

### C6-1. 断言关键词校验（源头治理）

**原则**：解析时**不区分大小写**（`[EQ]`、`[Eq]`、`[eq]` 均接受），但**不允许**出现以下变体，检测到即报错，阻断生成流程：

| 非法格式 | 说明 | 
|:---|:---|
| `[ eq ]` | 关键词内有空格 |
| `[[eq]]` | 双层括号 |
| `[eq] [contains]` | 同一步骤多个关键词（语义不明确） |
| `eq` / `(eq)` | 缺少 `[]` 或用了 `()` |

**校验逻辑**：

```python
import re

ASSERTION_PATTERN = re.compile(r'\[(eq|contains|ne|db)\]', re.IGNORECASE)
INVALID_PATTERN = re.compile(r'\[\s*(eq|contains|ne|db)\s*\]', re.IGNORECASE)

def _parse_assertion(expected_text: str) -> tuple[str, str]:
    """从预期结果文本解析断言关键词。返回 (keyword_lower, rest_of_text)。"""
    # 1. 检查非法格式
    if re.search(r'\[\[|\]\]', expected_text):
        raise AssertionParseError(f"断言格式非法（双层括号）: {expected_text[:60]}")
    if INVALID_PATTERN.search(expected_text):
        raise AssertionParseError(f"断言关键词含空格: {expected_text[:60]}")
    
    # 2. 匹配（不区分大小写）
    m = ASSERTION_PATTERN.search(expected_text)
    if not m:
        raise AssertionParseError(f"未找到断言关键词 [eq/contains/ne/db]: {expected_text[:60]}")
    
    # 3. 检查是否有多余关键词
    all_matches = ASSERTION_PATTERN.findall(expected_text)
    if len(all_matches) > 1:
        raise AssertionParseError(f"同一步骤包含多个断言关键词 {all_matches}: {expected_text[:60]}")
    
    keyword = m.group(1).lower()
    rest = expected_text[m.end():].strip()
    return keyword, rest
```

**校验位置**：仅在 Phase C 读取 Excel 后、YAML 生成前执行。不在 Phase B 做预检（冗余，LLM 输出格式不可靠，代码校验才是最终防线）。

校验失败时：
1. 记录精确错误：`文件:行号:步骤号 -> 错误原因`
2. 返回 `requires_review=True` + 错误详情，**不生成残缺的 YAML**
3. 用户修正 Excel 中的预期结果文本后重新触发 `/confirm-plan`

**YAML 输出简化**：无论预期结果中使用哪种断言关键词（`[eq]`/`[contains]`/`[ne]`/`[db]`），YAML 中统一输出为 `eq: 断言内容`。关键词仅作为语义提示辅助 LLM 生成验证逻辑，不直接映射为 YAML 的 validation 类型。

## C7. 数据依赖分析 prompt 更新

`_analyze_data_deps` 接收的 `case_steps` 中，预期结果字段现在包含 `[eq]`/`[contains]`/`[ne]`/`[db]` 断言关键词。需要在分析 prompt 中简要说明这些关键词的含义，让 LLM 能据此判断数据依赖：

- `[db]` → 该步骤涉及数据库查询/校验，可能需要前置步骤写入的数据
- `[contains]` → 该步骤校验返回内容，可能依赖前置步骤产生的特定数据
- `[ne]` → 该步骤校验数据已变更/删除，依赖前置步骤的状态变更
- `[eq]` → 该步骤校验精确返回值，依赖特定输入参数

Prompt 追加内容（在 `analyze_data_deps_prompt` system 消息末尾）：

```
### 断言关键词说明（预期结果中可能出现）
- [eq]: 精确相等断言 — 该校验需要特定的期望值，请分析期望值的来源
- [contains]: 包含断言 — 该校验需要数据中包含特定内容，请分析该内容的产生步骤
- [ne]: 不等断言 — 该校验需要确认数据已变更，请分析变更发生在哪个步骤
- [db]: 数据库断言 — 该校验需要数据库中存在对应记录，请确保数据已写入
```

## C8. PY 生成 prompt 重写

`generate_py_class_node` prompt（`definitions.py:293-341`）需完全重写，以支持新的 fixture + parametrize 结构。

**旧 prompt 生成的代码**（不再适用）：

```python
@allure.feature('<feature>')
class TestXxx:
    def test_xxx(self):
        RequestsBase().run_blocks('./testcase/<项目名>/{module_subdir}/<yaml文件名>')
```

**新 prompt 生成的代码**（目标结构）：

```python
@pytest.fixture(scope="class")
def setup_<class_slug>():
    read = ReadYamlData()
    read.write_yaml_data({...})
    base = RequestsBase()
    base.specification_yaml(get_testcase_yaml(
        './testcase/<feature_en>/setup_data/setup_<class_slug>.yaml'))
    yield
    base.specification_yaml(get_testcase_yaml(
        './testcase/<feature_en>/setup_data/teardown_<class_slug>.yaml'))

@allure.story('<story>')
@pytest.mark.danyuan
@pytest.mark.usefixtures("setup_<class_slug>")
class Test<story_en>:
    @allure.title('<title>')
    @pytest.mark.order(1)
    @pytest.mark.parametrize('params', get_testcase_yaml(
        './testcase/<feature_en>/<func1_en>/step1_xxx.yaml'))
    def <func1_en>(self, params):
        RequestsBase().specification_yaml(params)
```

**新 prompt 核心规则**：

1. **fixture 生成**：对每个 class，从 Sheet2 共享前置去重后生成 `setup_<class_slug>` fixture（yield 前=setup，yield 后=teardown）
2. **parametrize 生成**：每个 test function 用 `@pytest.mark.parametrize` 加载对应的 step YAML 文件
3. **命名映射**：
   - `<class_slug>` = story_en 的小写下划线形式（如 `facility_mgmt`）
   - `<feature_en>` / `<story_en>` / `<func1_en>` / `<title>` 由 C4 翻译步骤提供
4. **order 编号**：同一 class 内的 function 按 `@pytest.mark.order(N)` 递增
5. **空 fixture 的 class**：如果 class 无共享前置，fixture 只写 `pass` + yield，不生成 setup/teardown YAML
