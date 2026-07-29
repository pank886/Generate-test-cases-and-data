"""Phase B/C: Excel 修复 + YAML 生成 Prompt 模板"""
from langchain_core.prompts import ChatPromptTemplate


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
    """接口文档提取 prompt — 全量存储，不丢弃任何参数细节。"""
    return ChatPromptTemplate.from_messages([
        ("system",
         "你是 API 分析师。阅读以下文本，提取其中包含的那一个接口的定义。\n\n"
         "### 提取规则\n"
         "1. 文本中只包含一个接口，提取它。\n"
         "2. 每个接口必须包含以下字段：\n"
         '   - name: 接口名称（从文档中的"接口描述"或接口标题提取）\n'
         '   - description: 接口功能描述（一句话概括）\n'
         "   - method: 大写的 GET/POST/PUT/DELETE/PATCH\n"
         "   - url: 只提取路径部分，不含域名\n"
         "   - headers: 请求头参数列表，格式为数组\n"
         "   - parameters: 请求参数列表（Body + Query），格式为数组\n"
         "   - returns: 响应字段列表，格式为数组\n\n"
         "### 参数/返回值的数组元素格式\n"
         "每个参数/返回值元素为 JSON 对象，包含以下字段：\n"
         '   - name: 字段名（必填）\n'
         '   - type: 数据类型（必填），如 string/integer/number/boolean/object/array\n'
         '   - required: 是否必填（boolean，必填）\n'
         '   - description: 字段说明/备注（string，无则填空字符串""）\n'
         '   - default: 默认值（string 或 null，无则填 null）\n'
         "   - children: 嵌套子字段，仅在 type=object 或 type=array 时有值，格式同本数组。无嵌套则省略该字段。\n\n"
         "### 输出格式\n"
         "输出一个 JSON 对象，包含 apis 数组和 module_name 字符串。\n"
         "apis 数组中每个元素是一个接口对象，包含以下字段：\n"
         "  name(string), description(string), method(string), url(string),\n"
         "  headers(array), parameters(array), returns(array)\n"
         "每个数组元素的字段：name(string), type(string), required(boolean),\n"
         "  description(string), default(string|null), children(array,可选)\n\n"
         "⚠️ 重要约束：\n"
         '  - headers/parameters/returns 三个字段**必须是数组**，无数据时填空数组 []，绝对不能填 {{}}\n'
         "  - 文档中的 HTML 表格列（名称/类型/是否必须/默认值/备注）逐列提取，一一对应填入 name/type/required/default/description\n"
         "  - 文档中标注\"必须\"→required:true，\"非必须\"→required:false\n"
         "  - 嵌套的子字段用 children 数组表示，保持层级结构\n"
         "  - 不遗漏任何参数，不合并不同层级的参数\n"
         "  - 不包含 Markdown。"),
        ("human", "### 接口文档内容\n{doc_text}\n\n请提取所有接口定义，参数列表必须完整（包含嵌套子字段）：")
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
         "- mutates_data/is_negative_test 为布尔值\n\n"
         "### 测试场景分析（参考上下文）\n{analysis_section}\n\n"
         "### 完整用例描述（参考原始设计）\n{cases_section}\n\n"
         "### 失败的行及错误\n{failed_test_cases}\n\n"
         "### 修复指南\n"
         "1. 逐条对照失败行的错误信息，只修复报错的字段，保持正确字段不变\n"
         "2. 步骤与预期条数不一致：参照上方「完整用例描述」中该用例的原始设计，\n"
         "   补全缺失的步骤（如原始设计包含多个操作），或按步骤条数对齐预期。\n"
         "   使 steps \\n 分隔后的条数 = expected \\n 分隔后的条数\n"
         "3. 字段为空：从用例标题和上下文中推断补全\n"
         "4. 前置引用不存在：修正为正确的 PRE 编号\n"
         "5. shared_preconditions 留空数组 []\n"
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
    """Phase C YAML 数据 — 第一阶段：thinking 自由分析。"""
    return ChatPromptTemplate.from_messages([
        ("system",
         "你是资深测试数据构造专家。根据【接口定义】和【用例逻辑】，深度分析需要生成的测试数据。\n\n"
         "请分析以下方面（自由文本，不要输出 JSON）：\n"
         "1. **接口匹配**：每个步骤对应哪个接口（从接口定义中找匹配的 url/method）。"
         "**分析中描述接口时，url 只写路径**（如 /payConfig/detail），不要写 ${{}} 或完整 URL，"
         "动态参数通过 params 传递即可。\n"
         "2. **请求参数**：每个接口需要哪些请求参数，参数值从哪来（用例指定 / 上游提取 / 模拟）\n"
         "3. **数据传递**：哪些步骤的返回值需要 extract，供下游步骤引用（使用数据工厂清单中的提取函数）\n"
         "4. **断言设计**：每个步骤应该断言什么字段（从接口 returns 中选择），期望值是什么\n"
         "5. **工厂方法**：哪些参数值需要用工厂方法随机生成\n\n"
         "### 可用数据工厂方法\n{data_factory_methods}\n\n"
         "### 输出字段约束（json_mode 阶段会严格按以下 schema 输出，你的分析要覆盖这些字段）\n"
         "- baseInfo: 仅含 api_name/url/method/header 四个字段。**header 必须存在**（GET 请求 header 为空字典，POST/PUT/PATCH 写 Content-Type: application/json）\n"
         "- testCase: case_name/json|params|data/extract|input_extract/validation\n"
         "- 请求参数按 HTTP 方法选择：GET/DELETE → params（query string），POST/PUT/PATCH → json（JSON body）\n"
         "- **url 禁止动态占位符**——url 在框架中不经 replace_load() 解析，动态参数必须用 params 传递，url 保持静态路径\n"
         "- **params/json/data 只能放在 testCase 内**，禁止放在 baseInfo 层级\n"
         "- validation 支持 eq/contains/ne/db 四种断言（不等于是 ne 不是 neq）。**validation 不能为空数组**\n"
         "- extract 从接口返回值中提取数据（JSONPath），供下游步骤用 ${{get_extract_data(key)}} 引用。"
         "input_extract 极少使用，不要把它当数据暂存。禁止填入 PRE 编号或固定字面量\n"
         "- extract/validation 的 JSONPath 必须以 $. 开头（如 $.data.id）\n"
         "- 动态占位符只能从上方数据工厂清单中选择并按 syntax 使用，禁止胡编函数或语法；"
         "清单不支持的能力用合理固定字面量（如远期日期直接写 \"2029-12-31 10:00:00\"）\n"
         "- 分析阶段就要为每个动态值判定：用哪个工厂函数，还是固定字面量"),
        ("human",
         "### 接口定义\n{api_definitions}\n\n"
         "### 用例逻辑\n{test_case_logic}\n\n"
         "### 用户意图\n{user_context}\n\n"
         "请分析测试数据需求：")
    ])


def repair_yaml_data_prompt() -> ChatPromptTemplate:
    """Phase C YAML 数据 — 修复轮思考：带上一轮错误输出与校验错误自查（thinking on）。

    与 analyze_yaml_data_prompt 相同定位（自由文本分析），额外注入：
      - 上一轮原始输出（有错）
      - 本项校验错误明细
      - 全批次错误模式统计（跨文件模式反馈）
    输出接 format_yaml_data_prompt 结构化收敛。
    """
    return ChatPromptTemplate.from_messages([
        ("system",
         "你是资深测试数据构造专家。你上一轮生成的测试数据未通过校验，"
         "请先分析错误原因，再给出修正后的完整数据方案（自由文本，不要输出 JSON）。\n\n"
         "### 本轮全批次错误模式统计（其他文件也在犯的错，注意规避）\n"
         "{error_pattern_summary}\n\n"
         "### 可用数据工厂方法（动态占位符只能从此清单选择，严格按 syntax 填写）\n"
         "{data_factory_methods}\n\n"
         "### 修复要点\n"
         "- 逐条对照【校验错误明细】定位问题字段，说明错在哪、应改成什么\n"
         "- 动态值只能用数据工厂清单内的函数（语法见清单），禁止自创函数或语法"
         "清单不支持的能力写合理固定字面量\n"
         "- 无需提取时省略 extract/input_extract 字段，禁止 {{}} 占位与 null 值条目\n"
         "- json/params/data 三选一，依据接口定义确定正确的请求方式\n"
         "- 修正时保持原有正确部分不动，只改错误部分"),
        ("human",
         "{post_check_issues}"
         "### 接口定义\n{api_definitions}\n\n"
         "### 用例逻辑\n{test_case_logic}\n\n"
         "### 用户意图\n{user_context}\n\n"
         "### 你上一轮的输出（有错）\n{prior_output}\n\n"
         "### 校验错误明细\n{error_detail}\n\n"
         "请分析并给出修正方案：")
    ])


def format_yaml_data_prompt() -> ChatPromptTemplate:
    """Phase C YAML 数据 — 第二阶段：json_mode 结构化输出（thinking off）。

    输出 TestData 模型的 JSON，字段与 Pydantic 严格对齐。
    """
    return ChatPromptTemplate.from_messages([
        ("system",
         "你是数据格式化专家。根据【数据分析】和【接口定义】，输出 TestData 模型结构的 JSON（Pydantic 校验）。\n\n"
         "### 可用数据工厂方法（动态占位符只能从此清单选择，严格按 syntax 填写）\n"
         "{data_factory_methods}\n\n"
         "### ⚠️ 输出 JSON 结构（必须严格遵循，一个字符都不能错）\n\n"
         "整个输出只有一个顶层 key: **data**（数组），数组中每个元素是一个步骤对象。\n\n"
         "```json\n"
         "{{\n"
         '  "data": [\n'
         '    {{\n'
         '      "baseInfo": {{\n'
         '        "api_name": "新增创建",\n'
         '        "url": "/meterDevice/add",\n'
         '        "method": "post",\n'
         '        "header": {{"Content-Type": "application/json;charset=UTF-8"}}\n'
         '      }},\n'
         '      "testCase": [\n'
         '        {{\n'
         '          "case_name": "新增单一费率电表",\n'
         '          "json": {{"code": "${{random_plates(1)}}", "name": "测试电表"}},\n'
         '          "validation": [{{"eq": {{"$.retCode": 0}}}}, {{"contains": {{"$.msg": "成功"}}}}],\n'
         '          "extract": {{"meterCode": "$.data.code"}}\n'
         '        }}\n'
         '      ]\n'
         '    }},\n'
         '    {{\n'
         '      "baseInfo": {{\n'
         '        "api_name": "分页查询",\n'
         '        "url": "/meterDevice/getPage",\n'
         '        "method": "post",\n'
         '        "header": {{"Content-Type": "application/json;charset=UTF-8"}}\n'
         '      }},\n'
         '      "testCase": [\n'
         '        {{\n'
         '          "case_name": "查询电表列表验证新增",\n'
         '          "json": {{"pageNum": 1, "pageSize": 10}},\n'
         '          "validation": [{{"eq": {{"$.retCode": 0}}}}, {{"contains": {{"$.data.records[0].meterName": "${{get_extract_data(meterName)}}"}}}}]\n'
         '        }}\n'
         '      ]\n'
         '    }}\n'
         '  ]\n'
         '}}\n'
         "```\n\n"
         "### 结构铁律（参考上方示例）\n"
         "1. 顶层必须是 **\"data\": [...]** 数组，禁止用 testCase 或其他名字\n"
         "2. data 数组的每个元素是步骤对象，必须包含 **baseInfo** 和 **testCase** 两个键\n"
         "3. **testCase 必须是数组** [...], 禁止写成对象 {{...}}\n"
         "4. **validation 必须是数组** [...], 禁止写成对象 {{...}}\n"
         "5. api_name/url/method 与接口定义完全一致\n"
         "6. method 必须小写（post/get/put/delete）\n"
         "7. url 只写路径，禁止带域名，禁止使用 ${{}} 动态占位符\n"
         "8. **每个 baseInfo 必须有 header 字段**：POST/PUT/PATCH 写 Content-Type: application/json, GET 写空 {{}}\n"
         "9. 请求参数按 HTTP 方法选择：GET/DELETE → params, POST/PUT/PATCH → json\n"
         "10. 动态值使用 ${{函数名(参数)}}，函数必须来自上方清单\n"
         "11. extract/input_extract 用不到就省略整个字段，禁止输出空 {{}} 或 null\n"
         "12. validation 数组不能为空，每步至少一条断言（如 {{eq: {{retCode: 0}}}}）\n"
         "13. 断言运算符只用 [eq, contains, ne, db] 四种，不等于是 ne 不是 neq\n"
         "14. JSONPath 必须以 $. 开头（如 $.data.code）\n"
         "15. 禁止 Markdown，只输出纯净 JSON"),
        ("human",
         "### 数据分析\n{data_analysis}\n\n"
         "### 接口定义\n{api_definitions}\n\n"
         "### 用例逻辑\n{test_case_logic}\n\n"
         "### 用户意图\n{user_context}\n\n"
         "请严格按照上方 JSON 结构输出：")
    ])


def generate_dependency_map_prompt() -> ChatPromptTemplate:
    """Phase C Step 0: 生成 dependency_map.json（thinking 节点用）。

    输入: Excel 行、接口定义、模块树、产品文档、数据工厂方法
    输出: DependencyMap 模型 JSON
    """
    return ChatPromptTemplate.from_messages([
        ("system",
         "你是测试架构师。根据测试计划、接口定义和模块结构，生成依赖映射表。\n\n"
         "### 输出 JSON 结构\n"
         "必须输出以下结构的 JSON（一个字符都不能错）：\n\n"
         "```json\n"
         "{{\n"
         '  "stories": [\n'
         '    {{\n'
         '      "story_name": "子模块中文名",\n'
         '      "story_pre_api_sequence": ["步骤名:POST /api/xxx"],\n'
         '      "case_api_sequences": {{\n'
         '        "TC-001": ["步骤名:POST /api/xxx", "步骤名:GET /api/yyy"]\n'
         '      }},\n'
         '      "decision_map": {{\n'
         '        "TC-001": {{\n'
         '          "steps": [\n'
         '            {{"api": "POST /api/xxx", "params": {{"name": "${{random_plates(1)}}"}}, '
         '"assertions": [{{"eq": {{"$.retCode": 0}}}}]}},\n'
         '            {{"api": "GET /api/yyy", "params": {{"pageNum": 1}}, '
         '"assertions": [{{"eq": {{"$.retCode": 0}}}}]}}\n'
         '          ]\n'
         '        }}\n'
         '      }},\n'
         '      "internal_dependency": {{\n'
         '        "TC-001": {{"output_var": "code", "extract_path": "$.data.code", "used_by": ["TC-002"]}},\n'
         '        "TC-002": {{"output_var": null, "extract_path": null, "used_by": []}}\n'
         '      }},\n'
         '      "cross_module_dependency": {{\n'
         '        "前置步骤名": {{"module": "依赖的外部模块名", "var": "需获取的变量名", '
         '"api": "GET /api/xxx"}}\n'
         '      }},\n'
         '      "teardown_api_sequence": []\n'
         '    }}\n'
         '  ]\n'
         '}}\n'
         "```\n\n"
         "### 铁律\n"
         "1. story_name 与 Excel @allure.story 完全一致\n"
         "2. case_api_sequences 中每个 case_id 至少有一个 API 步骤\n"
         "3. case_api_sequences / internal_dependency / decision_map 的 key 集合必须完全一致\n"
         "4. decision_map 的 api 格式: 'METHOD /url'（如 'POST /meterDevice/add'）\n"
         "5. 动态值使用 ${{函数名(参数)}}，函数必须来自上方数据工厂清单\n"
         "6. extract_path 必须以 $. 开头，从接口 returns 中选择字段\n"
         "7. used_by 引用的 case_id 必须在本 story 的 case_api_sequences 中存在\n"
         "8. teardown 按数据流判断：下游消费本用例产物→不清理；有合法清理路径→填写；否则留空 []\n"
         "9. 禁止 Markdown，只输出纯净 JSON"),
        ("human",
         "### 上下文备注\n{context_note}\n\n"
         "### 数据工厂方法（动态占位符只能从此清单选择）\n"
         "{data_factory_methods}\n\n"
         "### 接口定义\n{all_apis_info}\n\n"
         "### Excel 测试计划\n{excel_rows}\n\n"
         "### 模块树\n{module_tree}\n\n"
         "### 产品文档\n{product_docs}\n\n"
         "### 用户意图\n{user_context}\n\n"
         "请生成依赖映射表 JSON：")
    ])


# ====================================================================
# Phase A: 模块场景分析（入库预处理）
# ====================================================================

def analyze_module_scenarios_prompt() -> ChatPromptTemplate:
    """模块场景分析 — 第一阶段：thinking 自由文本分析。

    不生成测试用例，不分析测试内容（参数值/断言/预期结果）。
    只做两件事：① 接口维度分析 ② 场景维度分析。
    输出自由文本，后续由第二阶段 JSON 格式化。
    """
    return ChatPromptTemplate.from_messages([
        ("system",
         "你是一位资深测试架构师，专注于**接口自动化测试场景分析**。\n\n"
         "根据【产品文档】和【接口定义】，分析该模块的所有测试场景。\n\n"
         "### 分析要求\n"
         "只做两件事，不越界：\n"
         "1. **接口维度分析**：以每个 API 为粒度，分析四类覆盖维度\n"
         "   - 正向：全字段合法值的正常业务场景\n"
         "   - 边界值：字段长度/数值/时间的边界条件\n"
         "   - 逆向：业务规则冲突 + 字段校验的错误场景（含 SQL 注入/XSS）\n"
         "   - 安全：越权、未授权访问、路径遍历等攻击向量\n"
         "   对每个接口标注 produces（产出变量）和 consumes（需从上游获取的变量）。\n\n"
         "2. **场景维度分析**：以业务流程为粒度，描述接口间的依赖和时序约束\n"
         "   每个场景列出步骤顺序、每步涉及的 API、数据依赖关系、跨模块约束。\n\n"
         "### 禁止\n"
         "- 禁止生成测试用例（参数值、断言、预期结果）\n"
         "- 禁止分析单个字段的边界值细节\n"
         "- 禁止编造不存在的接口路径或变量名\n\n"
         "分析要详细、具体，后续将基于你的分析生成结构化 JSON。"),
        ("human",
         "### 产品文档\n{product_docs}\n\n"
         "### 接口定义\n{api_definitions}\n\n"
         "### 模块关系树\n{module_tree}\n\n"
         "### 跨模块依赖\n{cross_module_relations}\n\n"
         "### 用户上下文\n{user_context}\n\n"
         "请分析以上模块的测试场景：")
    ])


def format_module_scenarios_prompt() -> ChatPromptTemplate:
    """模块场景分析 — 第二阶段：json_mode 结构化输出（thinking off）。

    输入为第一阶段 thinking 自由文本分析，输出严格 JSON。
    """
    return ChatPromptTemplate.from_messages([
        ("system",
         "你是数据格式化专家。根据【场景分析报告】和【接口定义】，输出结构化 JSON。\n\n"
         "### 输出 JSON 结构（严格遵循）\n\n"
         "{\n"
         '  "module_name": "模块名",\n'
         '  "api_analysis": [\n'
         '    {\n'
         '      "api_path": "/xxx/add",\n'
         '      "api_method": "POST",\n'
         '      "api_name": "新增XXX",\n'
         '      "scope": {\n'
         '        "正向": ["全字段合法值录入"],\n'
         '        "边界值": ["编号最大长度"],\n'
         '        "逆向": ["编号重复", "必填字段缺失"],\n'
         '        "安全": ["编号字段 SQL 注入"]\n'
         '      },\n'
         '      "produces": ["xxx_code"],\n'
         '      "consumes": ["上游变量名"]\n'
         '    }\n'
         '  ],\n'
         '  "scenario_analysis": [\n'
         '    {\n'
         '      "scenario_id": "S001",\n'
         '      "scenario_name": "流程名称",\n'
         '      "description": "一句话描述",\n'
         '      "steps": [\n'
         '        {\n'
         '          "order": 1,\n'
         '          "api": "METHOD /path",\n'
         '          "role": "步骤角色描述",\n'
         '          "data_depends_on": []\n'
         '        }\n'
         '      ],\n'
         '      "cross_module_deps": []\n'
         '    }\n'
         '  ]\n'
         '}\n\n'
         "### 字段规则\n"
         "- scope 四维度可为空数组 []\n"
         "- produces/consumes 变量名来自接口定义，禁止编造\n"
         "- data_depends_on 引用前序步骤 produces 的变量名\n"
         "- failure_condition 和 cross_module 是可选字段\n"
         "- 输出纯 JSON，不要 Markdown 包裹，不要解释文字"),
        ("human",
         "### 场景分析报告\n{scenario_analysis}\n\n"
         "### 接口定义（供核对路径和方法）\n{api_definitions}\n\n"
         "请输出结构化 JSON：")
    ])
