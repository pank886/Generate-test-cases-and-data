"""Phase B/C: Excel 修复 + YAML 生成 Prompt 模板"""
from langchain_core.prompts import ChatPromptTemplate


# ============================================================
# 辅助类：多段上下文拼接
# ============================================================

class _MsgBuilder:
    """多段 prompt 拼接器：累积 Excel / api_defs / 产品文档 / 工厂方法 / 模块树等上下文，
    渲染为结构化的 human message，供 dependency_map 生成节点使用。
    """

    def __init__(self):
        self._sections: list[tuple[str, str]] = []

    def add(self, title: str, content: str) -> "_MsgBuilder":
        if content:
            self._sections.append((title, content))
        return self

    def build(self) -> str:
        return "\n\n".join(
            f"### {title}\n{content}" for title, content in self._sections
        )


def product_doc_extract_prompt() -> ChatPromptTemplate:
    """产品文档模块提取 prompt"""
    return ChatPromptTemplate.from_messages([
        ("system",
         "你是文档分析师。阅读以下产品文档内容，提取其所属模块和关联模块。\n\n"
         "### 提取规则\n"
         "1. module_name：本文档描述的核心功能模块名称。\n"
         "2. related_modules：文档中明确提到的其他关联模块（如依赖、集成、数据交互）。\n"
         "3. business_summary：200 字以内的业务功能摘要。\n"
         "4. tags：功能标签，如 核心流程、配置管理、报表统计。\n\n"
         "### 输出\n"
         "直接输出 JSON 对象，包含以上四个字段。不包含 Markdown。"),
        ("human", "### 文档内容\n{doc_text}\n\n请提取模块信息：")
    ])


def glossary_extract_prompt() -> ChatPromptTemplate:
    """提取产品文档中的业务术语表"""
    return ChatPromptTemplate.from_messages([
        ("system",
         "你是业务分析师。阅读以下产品文档内容，提取其中的业务术语和定义。\n\n"
         "### 提取规则\n"
         "1. 只提取有明确业务含义的术语（专业名词、状态值、缩写）。\n"
         "2. 跳过通用词汇（用户、系统、数据等）。\n"
         "3. 每个术语包含三个字段：term（名称）、definition（解释）、notes（备注，如取值范围、使用场景、关联模块等补充信息，可为空字符串）。\n\n"
         "### 输出\n"
         '输出 JSON 对象：{{"terms": [{{"term": "...", "definition": "...", "notes": "..."}}]}}\n'
         "不包含 Markdown。"),
        ("human", "### 文档内容\n{doc_text}\n\n请提取业务术语表：")
    ])


def analyze_data_deps_prompt() -> ChatPromptTemplate:
    """数据依赖分析 prompt（thinking 节点用）：输出自由文本分析报告。"""
    return ChatPromptTemplate.from_messages([
        ("system",
         "你是测试数据架构师。根据【接口定义】和【用例步骤】，分析测试数据依赖。\n\n"
         "请分析以下方面（自由文本输出，不要输出 JSON）：\n"
         "1. **数据覆盖**：正常值、边界值、异常值分别需要哪些数据\n"
         "2. **数据传递链**：步骤间存在哪些数据依赖（步骤 B 依赖步骤 A 的哪个返回值）\n"
         "3. **断言策略**：每个接口调用的关键校验点\n"
         "4. **动态数据**：哪些字段需要使用工厂方法生成\n\n"
         "分析要详细、具体，后续将基于你的分析生成结构化的数据规划。\n\n"
         "### 断言关键词说明（预期结果中可能出现）\n"
         "- [eq]: 精确相等断言 — 该校验需要特定的期望值，请分析期望值的来源\n"
         "- [contains]: 包含断言 — 该校验需要数据中包含特定内容，请分析该内容的产生步骤\n"
         "- [ne]: 不等断言 — 该校验需要确认数据已变更，请分析变更发生在哪个步骤\n"
         "- [db]: 数据库断言 — 该校验需要数据库中存在对应记录，请确保数据已写入"),
        ("human",
         "### 接口定义\n{api_definitions}\n\n"
         "### 用例步骤\n{test_case_steps}\n\n"
         "### 用户意图\n{user_context}\n\n请分析以上场景的数据依赖：")
    ])


def generate_data_plan_prompt() -> ChatPromptTemplate:
    """场景级数据规划 prompt（format 节点用：thinking off + json_mode）。"""
    return ChatPromptTemplate.from_messages([
        ("system",
         "你是测试数据架构师。根据【接口定义】、【数据分析】和【用例步骤】，生成结构化的测试数据规划。\n\n"
         "### 规划要求\n"
         "1. 数据值覆盖：正常值、边界值、异常值。\n"
         "2. 数据传递：如果步骤 B 依赖步骤 A 的返回值，规划 extract_rules。\n"
         "3. 断言策略：每个接口调用必须规划断言，字段从接口 returns 中选择。\n"
         "4. 工厂方法：需要随机/动态生成的数据，标注 data_factory_calls。\n\n"
         "### 输出 JSON 字段\n"
         "- scenario_name: 场景名称\n"
         "- steps[]: 每个 API 调用的数据规划\n"
         "  - api_name: 接口名\n"
         "  - data_values: 请求数据对象\n"
         "  - extract_rules: 从响应提取（可选）\n"
         "  - assertions: 断言列表\n"
         "  - data_factory_calls: 工厂方法列表（可选）\n"
         "- shared_context: 步骤间的数据流转说明\n\n"
         "不包含 Markdown。"),
        ("human",
         "### 数据分析（供参考）:\n{data_analysis}\n\n"
         "### 接口定义\n{api_definitions}\n\n"
         "### 用例步骤\n{test_case_steps}\n\n"
         "### 用户意图\n{user_context}\n\n请规划测试数据：")
    ])


def api_def_extract_prompt() -> ChatPromptTemplate:
    """接口文档提取 prompt（yapi 导出的 MD 格式，含 HTML 参数表格）"""
    return ChatPromptTemplate.from_messages([
        ("system",
         "你是 API 分析师。阅读以下接口文档内容（yapi 导出的 Markdown），提取所有接口定义。\n\n"
         "### 提取规则\n"
         "1. **只提取文档中明确标注了 Path 的接口**。识别标志：\n"
         '   每行以 `**Path：** ` 开头，后跟路径（如 `/electricMeter/update`）。\n'
         "   **禁止**根据参数字段名、路径片段、或你记忆中的其他项目推测接口。\n"
         "   **禁止**看到 `deviceStatus`、`deviceId` 等字段名就编造 `/device/info` 等接口。\n"
         "   **禁止**根据「应该有增删改查」的惯性思维补全文档中不存在的接口。\n\n"
         "2. 每个接口必须包含以下字段：\n"
         '   - name: 接口名称（从该接口所属的 `## ` 标题行提取，如 `## 修改电表`）\n'
         '   - description: 接口功能描述（从文档中概括，如"新增健身房设施"）\n'
         "   - method: 从 `**Method：** ` 行提取的大写 GET/POST/PUT/DELETE/PATCH\n"
         "   - url: 从 `**Path：** ` 行提取的路径部分，不含域名\n"
         "   - parameters: 请求参数结构（字段名→类型），无参数填 {{}}\n"
         "   - returns: 响应字段结构（字段名→类型）\n\n"
         "3. module_name 根据接口所属的 `# ` 一级标题判断（如 `# 电表` → 电表）。\n\n"
         "### 输出格式\n"
         '输出 JSON 对象：{{"apis": [{{"name": "...", "description": "...", "method": "...", "url": "...", "parameters": {{...}}, "returns": {{...}}}}], "module_name": "..."}}\n'
         "每个接口必须包含 name、description、method、url、parameters、returns 六个字段。\n"
         "⚠️ returns 必须是 JSON 对象（dict），即使响应是数组也要用 {{\"data\": [...]}} 包装，绝对不能直接输出数组。\n"
         "⚠️ 不要输出文档中未出现的接口。字段名、路径片段、常识推断都不能作为新增接口的依据。\n"
         "不包含 Markdown。"),
        ("human", "### 接口文档内容\n{doc_text}\n\n请提取所有接口定义：")
    ])


def repair_excel_plan_prompt() -> ChatPromptTemplate:
    """Excel 计划修复 prompt：按错误信息修正失败用例，代码侧根据 failed_ids 裁剪输出。"""
    return ChatPromptTemplate.from_messages([
        ("system",
         "你正在修复一个 Excel 测试计划中的失败用例。按以下要求修正每个失败用例。\n\n"
         "### 输出 JSON 格式（必须严格遵循，一个字符都不能错）\n"
         "必须输出以下结构的 JSON 对象：\n\n"
         "  {{\n"
         '    "shared_preconditions": [],\n'
         '    "test_cases": [\n'
         '      {{"id": "TC-001",\n'
         '        "story": "设施添加",\n'
         '        "title": "设施管理-新增设施-正向",\n'
         '        "preconditions": ["PRE-001"],\n'
         '        "steps": "1.调用新增设施接口\\n2.查询详情",\n'
         '        "expected": "1.[eq]创建成功\\n2.[eq]信息一致",\n'
         '        "mutates_data": true,\n'
         '        "is_negative_test": false}}\n'
         '    ],\n'
         '    "file_name": "test_plan.xlsx"\n'
         '  }}\n\n'
         "### 字段硬约束（违反即校验失败）\n"
         "- id/story/title/steps/expected **五字段缺一不可**，字段名是 story 不是 sub_module\n"
         "- steps 和 expected 必须是**字符串**（\\n 分隔各条），禁止输出数组/列表\n"
         "- steps 和 expected 的条数必须一致（\\n 分隔后 count 相等）\n"
         "- preconditions 是 PRE ID 数组，无则为 []\n"
         "- mutates_data/is_negative_test 为布尔值\n"
         "- expected 中每条预期必须以断言关键词开头：[eq]/[contains]/[ne]/[db]，"
         "格式为 \"序号.[关键词]内容\"。禁止缺关键词、含空格、用错括号类型\n\n"
         "### 共享前置定义（参考，用于修正 PRE 引用）\n{shared_pre_section}\n\n"
         "### 模块树（参考，用于修正 story 字段）\n{module_tree}\n\n"
         "### 接口定义（参考，用于补全步骤）\n{all_apis_info}\n\n"
         "### 测试场景分析（参考上下文）\n{analysis_section}\n\n"
         "### 完整用例描述（参考原始设计）\n{cases_section}\n\n"
         "### 失败的行及错误\n{failed_test_cases}\n\n"
         "### 修复指南\n"
         "1. 逐条对照失败行的错误信息，只修复报错的字段，保持正确字段不变\n"
         "2. 步骤与预期条数不一致：参照上方「完整用例描述」中该用例的原始设计，\n"
         "   补全缺失的步骤（如原始设计包含多个操作），或按步骤条数对齐预期。\n"
         "   使 steps \\n 分隔后的条数 = expected \\n 分隔后的条数\n"
         "3. 字段为空：从用例标题和上下文中推断补全\n"
         "4. 前置引用不存在：参照上方共享前置定义修正为正确的 PRE 编号\n"
         "5. shared_preconditions：如果失败行包含\"引用前置 XXX 不存在\"错误，"
         "将 XXX 的定义补充到 shared_preconditions 中；否则留空数组 []\n"
         "6. 禁止 Markdown，只输出 JSON"),
        ("human", "请输出修正后的测试用例 JSON：")
    ])


def translate_to_en_prompt() -> ChatPromptTemplate:
    """Phase C 英文翻译 prompt：将中文 feature/story/title 翻译为合法的英文标识符。"""
    return ChatPromptTemplate.from_messages([
        ("system",
         "你是专业的中英翻译专家，将中文测试术语翻译为合法的 Python/英文标识符。\n\n"
         "### 翻译规则\n"
         "1. **驼峰命名**：feature 和 story 用 PascalCase（如 FacilityManagement, FacilityAdd）\n"
         "2. **下划线小写**：title 用 snake_case（如 facility_add_positive_001）\n"
         "3. **保留编号**：title 中的 TC-xxx 转为 xxx，如「设施管理-新增设施-正向」→ facility_add_positive_001\n"
         "4. **简洁优先**：在保留语义的前提下尽量短，3-5 个英文单词以内\n"
         "5. **一致性**：相同的功能名称使用统一的英文翻译\n\n"
         "### 输出格式\n"
         '输出 JSON: {{"feature_en": {{"中文1": "English1", ...}}, '
         '"story_en": {{"中文1": "English1", ...}}, '
         '"title_en": {{"中文1": "english1", ...}}}}\n'
         "只输出 JSON，禁止解释。"),
        ("human",
         "### 待翻译\n"
         "Feature: {features}\n"
         "Story: {stories}\n"
         "Title: {titles}\n\n"
         "请翻译：")
    ])


def analyze_yaml_data_prompt() -> ChatPromptTemplate:
    """Phase C YAML 数据 — 第一阶段：thinking 自由分析。

    注入 {skeleton} 使 thinking 与 json_mode 共用同一结构词典（§5.8）。
    """
    return ChatPromptTemplate.from_messages([
        ("system",
         "你是资深测试数据构造专家。根据【接口定义】、【用例逻辑】和【JSON 结构骨架】，"
         "深度分析需要生成的测试数据。\n\n"
         "### 输出 JSON 结构骨架（你的分析必须基于此结构）\n"
         "```json\n{skeleton}\n```\n\n"
         "### 你的分析要点（自由文本，不要输出 JSON）\n"
         "1. **接口匹配**：每个步骤对应 skeleton.data 中的哪个元素，"
         "匹配接口定义中的哪个 url/method。分析中描述接口时 url 只写路径"
         "（如 /payConfig/detail），不要写 ${{}} 或完整 URL。\n"
         "2. **请求参数**：骨架中 json/params 的每个键应该填什么值"
         "（用例指定 / 上游提取 / 工厂方法模拟），从接口定义的 parameters 中确认键名。"
         "**关键：json 和 params 互斥——按 HTTP 方法只选一个，在分析中明确标注「本步骤用 json」或「本步骤用 params」，"
         "不要同时分析两者。**\n"
         "3. **数据传递**：哪些步骤的返回值需要 extract（写入骨架中不显示的 extract 字段），"
         "供下游步骤通过 ${{get_extract_data(key)}} 引用。\n"
         "4. **断言设计**：骨架中 validation 数组每步应该包含哪些断言，"
         "字段从接口 returns 中选择，断言运算符用 eq/contains/ne/db。\n"
         "5. **工厂方法**：哪些参数值需要用工厂方法随机生成，从清单中选择正确的函数名和参数。\n\n"
         "### 可用数据工厂方法\n{data_factory_methods}\n\n"
         "### 关键约束\n"
         "- url 禁止动态占位符，动态参数通过 params 传递，url 保持静态路径\n"
         "- json/params/data 三选一，按 HTTP 方法决定：GET/DELETE→params，POST/PUT/PATCH→json\n"
         "- extract JSONPath 必须以 $. 开头，禁止填 PRE 编号或固定字面量\n"
         "- 断言运算符只用 [eq, contains, ne, db]，不等于是 ne 不是 neq\n"
         "- 动态占位符只能从上方工厂方法清单选择，禁止编造函数或语法；"
         "清单不支持的能力写合理固定字面量"),
        ("human",
         "### 接口定义\n{api_definitions}\n\n"
         "### 用例逻辑\n{test_case_logic}\n\n"
         "### 用户意图\n{user_context}\n\n"
         "请分析测试数据需求：")
    ])


def repair_yaml_data_prompt() -> ChatPromptTemplate:
    """Phase C YAML 数据 — 修复轮：带诊断包自查 + 骨架对照（thinking on）。

    诊断包包含 failed_yaml（可视化）+ error_roadmap（导航）+ skeleton（目标结构）。
    工厂方法和接口定义按错误类型条件注入（§5.3）。
    """
    return ChatPromptTemplate.from_messages([
        ("system",
         "你是资深测试数据构造专家。你上一轮生成的测试数据未通过校验，"
         "请根据【诊断信息】和【结构骨架】分析错误原因，给出修正后的完整数据方案"
         "（自由文本，不要输出 JSON）。\n\n"
         "### 输出 JSON 结构骨架（修正目标结构）\n"
         "```json\n{skeleton}\n```\n\n"
         "### 上一轮输出（YAML 格式，错误位置一目了然）\n"
         "```yaml\n{failed_yaml}\n```\n\n"
         "### 校验错误定位（: 左边的路径对应上述 YAML 的缩进层级）\n"
         "{error_roadmap}\n\n"
         "{data_factory_methods_section}"
         "{api_definitions_section}"
         "### 修复要点\n"
         "- 逐条对照【校验错误定位】找到错误位置，只改错误的字段，保持正确部分不动\n"
         "- 结构问题：对照【结构骨架】的键名和层级修正嵌套关系\n"
         "- 数据问题：动态值使用上方注入的工厂方法，断言字段从上方注入的接口定义选择\n"
         "- 无需提取时省略 extract/input_extract 字段，禁止 null 值条目\n"
         "- json/params/data 三选一，按 HTTP 方法决定：GET/DELETE→params，POST/PUT/PATCH→json\n"
         "- 保持原有正确部分不动，只改错误部分"),
        ("human",
         "请分析错误原因并给出修正方案：")
    ])


def format_yaml_data_prompt() -> ChatPromptTemplate:
    """Phase C YAML 数据 — 第二阶段：json_mode 填表（thinking off）。

    结构由 Pydantic 模型自动生成的 JSON 骨架注入，Prompt 只描述骨架表达不了的业务规则。
    """
    return ChatPromptTemplate.from_messages([
        ("system",
         "你是数据格式化专家。根据【数据分析】和【接口定义】，输出测试数据的 JSON。\n\n"
         "### 输出 JSON 结构骨架（键名、层级不能增减，数组长度按实际步骤数展开）\n"
         "```json\n{skeleton}\n```\n\n"
         "注意：data 数组的每个元素对应一个 API 调用步骤（多步骤 = 多个元素）。"
         "testCase 数组的每个元素对应该步骤的一条用例变体。"
         "数组长度由【用例逻辑】中的步骤数决定，骨架中只展示 1 个元素作为示例。\n\n"
         "### ⚠️ json 与 params 互斥（最重要规则，违反率最高）\n"
         "骨架中 json 和 params 各出现一次仅为结构占位。**每条 testCase 只能保留其中一个，"
         "另一个必须从输出中删除。**\n"
         "- GET/DELETE → 只保留 params，删除 json\n"
         "- POST/PUT/PATCH → 只保留 json，删除 params\n"
         "   ❌ 错误: {{\"json\": {{\"name\": \"test\"}}, \"params\": {{\"pageSize\": 10}}}}\n"
         "   ✅ 正确: {{\"json\": {{\"name\": \"test\"}}}}  ← POST 请求只保留 json\n"
         "   ✅ 正确: {{\"params\": {{\"pageSize\": 10}}}}  ← GET 请求只保留 params\n\n"
         "### 可用数据工厂方法（动态占位符只能从此清单选择，严格按 syntax 填写）\n"
         "{data_factory_methods}\n\n"
         "### 业务规则\n"
         "1. api_name/url/method 与接口定义完全一致，中文就中文，禁止翻译；"
         "method 必须小写；url 只写路径，禁止带域名。"
         "**url 字段禁止使用动态占位符**——url 在框架中不经 replace_load() 解析，"
         "动态占位符放在 url 中会被原样发送到服务端导致 404。"
         "GET 请求的动态参数一律通过 testCase 内的 params 传递，URL 保持静态路径。\n"
         "   ❌ 错误: url: /payConfig/detail/${{get_extract_data(code)}}\n"
         "   ✅ 正确: url: /payConfig/detail, params: {{code: ${{get_extract_data(code)}}}}\n"
         "2. case_name 中文简要描述，禁止带 TC-xxx/PRE-xxx 前缀。\n"
         "4. header 规则：baseInfo.header 是骨架中的必填字段：\n"
         "   - json 请求体 → {{Content-Type: application/json;charset=UTF-8}}\n"
         "   - 表单请求体 → {{Content-Type: application/x-www-form-urlencoded}}\n"
         "   - GET 无请求体 → {{}}（空字典，框架注入公共头）\n"
         "   token/yq-app-code 等公共头由框架常量自动注入，禁止手写。\n"
         "5. 动态值只能写成 ${{函数名(参数)}} 且函数必须来自上方数据工厂清单，"
         "禁止 {{{{}}}} 双花括号、禁止占位符内运算或拼接（如 + 1day）、禁止发明函数。"
         "清单不支持的能力写合理固定字面量（如 \"2029-12-31 10:00:00\"）。\n"
         "6. 断言运算符只能用 [eq, contains, ne, db] 四种；**不等于是 ne 不是 neq**。"
         "断言字段从接口 returns 中选择，禁止捏造。"
         "断言的 key（: 左边）禁止使用 ${{}} 动态值——key 必须是静态字段名或 JSONPath。"
         "正例: {{$.data.code: ${{get_extract_data(code)}}}}。\n"
         "7. extract 从接口返回值提取数据供下游使用，值必须是 JSONPath（$.data.id）。"
         "步骤间数据传递：步骤1 extract: {{code: $.data.code}}，步骤2 json: {{code: ${{get_extract_data(code)}}}}。"
         "禁止将 PRE 编号、固定字面量、数据工厂表达式填入 extract。\n"
         "8. validation 数组不能为空，每步至少包含一条断言"
         "（如 {{eq: {{retCode: 0}}}}）。"
         "导出/下载类接口（export/download/importTemplate）的 response 是二进制文件，"
         "validation 统一写 {{contains: {{status_code: 200}}}}。\n"
         "9. 骨架中未出现的字段（如 extract/input_extract）不需要时可省略，禁止输出 null 值。\n"
         "10. 禁止 Markdown，只输出 JSON"),
        ("human",
         "### 数据分析\n{data_analysis}\n\n"
         "### 接口定义\n{api_definitions}\n\n"
         "### 用例逻辑\n{test_case_logic}\n\n"
         "### 用户意图\n{user_context}\n\n"
         "请输出：")
    ])


# ============================================================
# Phase C Step 0: dependency_map.json 生成 prompt（thinking 模式）
# ============================================================

def generate_dependency_map_prompt() -> str:
    """dep_map 生成 thinking 节点的系统消息文本。

    返回纯字符串（含 {factory_methods} 占位符），由调用方 replace 后
    通过 SystemMessage(content=...) 直传 LLM，不经过 LangChain 模板解析。

    只描述 JSON 结构的字段名和类型（不给出可复制的具体值），
    与 YAML 生成节点一致：结构约束靠 Pydantic 模型，prompt 只管规则。
    """
    return (
        "你是资深测试架构师，负责分析测试用例之间的**数据依赖关系**和**API调用序列**。\n\n"
        "根据【Excel 测试计划】、【接口定义】、【产品文档】和【数据工厂方法清单】，"
        "生成结构化的 dependency_map JSON。\n\n"

        "### 你的任务\n"
        "1. **API 序列提取**：为每个 story 提取共享前置的 API 序列（story_pre_api_sequence），"
        "以及每条用例的 API 调用序列（case_api_sequences），格式为「步骤名:METHOD /url」。\n"
        "2. **数据依赖分析**：判断哪些用例产出变量、哪些用例消费变量，输出 internal_dependency。\n"
        "3. **参数赋值决策**：在 decision_map 中为每条用例的每个步骤决定 params 和 assertions。\n"
        "4. **跨模块依赖**：标注前置条件依赖的外部模块接口（cross_module_dependency）。\n"
        "5. **清理策略**：按数据流判断是否需要 teardown_api_sequence。\n\n"

        "### ⚠️ 四条铁律（违反将导致 dependency_map 校验失败）\n\n"

        "**① 输出 teardown_api_sequence（按数据流判断）**\n"
        "对每个 story，判断写操作（POST/PUT/DELETE）的产物是否需要清理：\n"
        "- 下游 case 需消费本 case 的产物 → 不清理，teardown_api_sequence 留空 []\n"
        "- 有合法的清理路径（产品规则允许删除/回滚）→ 填写具体步骤\n"
        "- 不存在合法清理路径（如被引用实体不可删除）→ 留空 []\n"
        "禁止编造无法执行的清理步骤。\n\n"

        "**② decision_map 中 params 的赋值原则**\n"
        "使用下方【数据工厂方法清单】：\n"
        "- 用例步骤中明确写死的值 → 直接输出（如 \"pageSize\": 10）\n"
        "- 需要动态生成的值 → 从工厂方法清单中选择正确的函数名和参数\n"
        "  （如 \"plate\": \"${random_plates(1)}\"）\n"
        "- 依赖前置步骤的值 → 输出 ${get_extract_data(xxx)} 占位符，\n"
        "  变量名 xxx 来自 internal_dependency 中定义的 output_var\n"
        "- 禁止编造工厂清单中不存在的函数名\n\n"

        "**③ internal_dependency 中 extract_path 的来源**\n"
        "extract_path 必须从【接口定义】的 returns 字段中提取，与响应 schema 严格对齐。\n"
        "禁止凭空猜测 JSONPath。如果 returns 中找不到对应字段，不填 extract_path，\n"
        "在 used_by 中标注依赖关系即可。\n\n"

        "**④ case_id 格式一致性（禁止格式转换）**\n"
        "所有 key（case_api_sequences / decision_map / internal_dependency）中的 case_id\n"
        "必须与 Excel 中「用例编号」列的值逐字符一致，严禁做任何格式转换。\n"
        "例如: Excel 中写 \"TC-1\" 则 JSON 中必须写 \"TC-1\"，不能写成 \"TC-001\"；\n"
        "Excel 中写 \"TC-001\" 则 JSON 中必须写 \"TC-001\"，不能写成 \"TC-1\"。\n"
        "格式不一致将导致 Phase C 的 case_id 精确匹配断裂，全部用例 YAML 生成失败。\n\n"

        "### story_pre_api_sequence 与 cross_module_dependency 的边界\n"
        "**story_pre_api_sequence 只能包含当前模块接口定义中存在的 API**。\n"
        "如果某前置步骤需要调用其他模块的接口（如获取登录 token、查询外部服务数据），"
        "该 API 不应出现在 story_pre_api_sequence 中，而应放入 cross_module_dependency。\n"
        "- story_pre_api_sequence: 当前模块内可执行的 API（URL 必须在【接口定义】中能找到）\n"
        "- cross_module_dependency: 前置步骤需要但不在当前模块的接口（标注依赖的模块名、变量、接口）\n"
        "- 禁止将 login、get_test_user 等通用鉴权/用户接口写入 story_pre_api_sequence，"
        "除非它们确实存在于当前模块的接口定义中\n\n"

        "### 步骤名格式\n"
        "格式统一为「步骤名:METHOD /url」。步骤名从 Excel 中用例 title 或 steps 首行动词提取。\n"
        "⚠️ URL 中的路径参数必须使用**单花括号** {param}（如 {order_id}、{code}），\n"
        "禁止使用双花括号 {{param}}。\n\n"

        "### 输出 JSON 结构\n"
        "输出一个 JSON 对象，顶层字段为 \"stories\"（数组），每个 story 对象包含以下字段：\n\n"

        "| 字段 | 类型 | 说明 |\n"
        "|------|------|------|\n"
        "| story_name | string | 中文 story 名，与 Excel @allure.story 列完全一致 |\n"
        "| story_pre_api_sequence | string[] | 共享前置 API 序列，每项格式「步骤名:METHOD /url」 |\n"
        "| case_api_sequences | object | key=case_id(TC-xxx)，value=API序列数组（必须非空） |\n"
        "| decision_map | object | key=case_id，value={\"steps\": [每步含 api/params/assertions]} |\n"
        "| internal_dependency | object | key=case_id，value={\"output_var\":string|null, \"extract_path\":\"$.xxx\"|null, \"used_by\":[case_id]} |\n"
        "| cross_module_dependency | object | key=前置步骤名，value={\"依赖模块\":\"…\", \"需获取变量\":\"…\", \"获取接口\":\"METHOD /url\"} |\n"
        "| teardown_api_sequence | string[] | 清理 API 序列，LLM 判断无需清理时为空数组 [] |\n\n"

        "**重要约束**：\n"
        "- case_api_sequences、decision_map、internal_dependency 三个 map 的 key 集合必须完全一致\n"
        "- case_api_sequences 中每个 case_id 的值必须为非空数组（至少一个 API）\n"
        "- internal_dependency 中 used_by 引用的 case_id 必须存在\n"
        "- decision_map 每步的 api 字段格式为「METHOD /url」，URL 必须来自【接口定义】\n"
        "- assertions 使用 YAML 原生结构：[{\"eq\": {...}}, {\"contains\": {...}}, {\"ne\": {...}}, {\"db\": {...}}]\n"
        "- params 中：静态值直接写，动态值用 ${...} 字符串，禁止编造工厂清单中不存在的函数名\n\n"

        "### 数据工厂方法（已注入，只能从此清单选择）\n"
        "{factory_methods}\n\n"

        "### 注意事项\n"
        "- 一个 feature 一个 dependency_map.json，stories 数组包含该 feature 下所有 story\n"
        "- 禁止 Markdown，只输出 JSON（可直接被 json.loads 解析）\n"
        "- 如果你不确定某个字段的值，留空或不填，后续校验会反馈具体错误"
    )
