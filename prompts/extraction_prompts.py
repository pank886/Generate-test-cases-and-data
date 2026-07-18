"""Phase A: 文档提取 Prompt 模板"""
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
    """接口文档提取 prompt"""
    return ChatPromptTemplate.from_messages([
        ("system",
         "你是 API 分析师。阅读以下接口文档内容，提取所有接口定义。\n\n"
         "### 提取规则\n"
         "1. 提取文档中出现的每一个接口，不要遗漏。\n"
         "2. 每个接口必须包含以下字段：\n"
         '   - name: 接口名称（从文档中的"接口名称"字段提取，如"新增创建"、"分页查询"）\n'
         '   - description: 接口功能描述（从文档中概括，如"新增健身房设施"）\n'
         "   - method: 大写的 GET/POST/PUT/DELETE/PATCH\n"
         "   - url: 只提取路径部分，不含域名，如 /gymFacility/add\n"
         "   - parameters: 请求参数结构（字段名→类型），无参数填 {{}}\n"
         "   - returns: 响应字段结构（字段名→类型）\n"
         "3. module_name 根据接口的用途判断所属模块。\n\n"
         "### 输出格式\n"
         '输出 JSON 对象：{{"apis": [{{"name": "...", "description": "...", "method": "...", "url": "...", "parameters": {{...}}, "returns": {{...}}}}], "module_name": "..."}}\n'
         "每个接口必须包含 name、description、method、url、parameters、returns 六个字段。\n"
         "⚠️ returns 必须是 JSON 对象（dict），即使响应是数组也要用 {{\"data\": [...]}} 包装，绝对不能直接输出数组。\n"
         "不包含 Markdown。"),
        ("human", "### 接口文档内容\n{doc_text}\n\n请提取所有接口定义：")
    ])


def repair_excel_plan_prompt() -> ChatPromptTemplate:
    """Excel 计划修复 prompt：只修复失败行，不返回已通过的用例。"""
    return ChatPromptTemplate.from_messages([
        ("system",
         "你正在修复一个 Excel 测试计划。**只输出以下失败用例的修复版本，不要包含已通过的用例**。\n\n"
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
         "### 原始测试分析报告（供补全缺失信息）\n{original_test_analysis}\n\n"
         "### 失败的行及错误（仅需修复这些行）\n{failed_test_cases}\n\n"
         "### 修复指南\n"
         "1. 找到失败行对应的用例，补全缺失字段，修正步骤与预期条数不一致\n"
         "2. 前置引用不存在则修正为正确的 PRE 编号\n"
         "3. **必须保持的 TC ID**（只能输出以下 ID 的用例，不可新增、不可删除）：{failed_ids}\n"
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
         "1. **接口匹配**：每个步骤对应哪个接口（从接口定义中找匹配的 url/method）\n"
         "2. **请求参数**：每个接口需要哪些请求参数，参数值从哪来（用例指定 / 上游提取 / 模拟）\n"
         "3. **数据传递**：哪些步骤的返回值需要 extract，供下游步骤引用（使用 ${{get_extract_data(key)}}）\n"
         "4. **断言设计**：每个步骤应该断言什么字段（从接口 returns 中选择），期望值是什么\n"
         "5. **工厂方法**：哪些参数值需要用工厂方法随机生成\n\n"
         "### 可用数据工厂方法\n{data_factory_methods}\n\n"
         "### 输出字段约束（json_mode 阶段会严格按以下 schema 输出，你的分析要覆盖这些字段）\n"
         "- baseInfo: api_name/url/method/header（api_name 必须与接口定义一致，中文就中文）\n"
         "- testCase: case_name/json|params|data/extract|input_extract/validation\n"
         "- json 对应 JSON 请求体（post/put/patch），params 对应 URL query（get/delete），data 对应表单\n"
         "- validation 支持 eq/contains/ne/db 四种断言\n"
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
         "- 动态值只能用清单内函数；时间偏移用 ${{get_offset_time(fmt, days, ...)}}；"
         "清单不支持的能力写合理固定字面量\n"
         "- 无需提取时省略 extract/input_extract 字段，禁止 {{}} 占位与 null 值条目\n"
         "- json/params/data 三选一，依据接口定义确定正确的请求方式\n"
         "- 修正时保持原有正确部分不动，只改错误部分"),
        ("human",
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
         "你是数据格式化专家。根据【数据分析】和【接口定义】，输出测试数据的 JSON（代码会自动转为 YAML）。\n\n"
         "### 输出 JSON 格式（必须严格遵循，与 Pydantic TestData 模型对齐）\n"
         "  {{\n"
         '    "data": [\n'
         '      {{\n'
         '        "baseInfo": {{\n'
         '          "api_name": "接口名称",\n'
         '          "url": "/path/to/api",\n'
         '          "method": "post",\n'
         '          "header": {{ "Content-Type": "application/json;charset=UTF-8" }}\n'
         '        }},\n'
         '        "testCase": [\n'
         '          {{\n'
         '            "case_name": "场景描述",\n'
         '            "json": {{ "key": "value" }},\n'
         '            "extract": {{ "key": "$.data.jsonpath" }},\n'
         '            "validation": [\n'
         '              {{ "eq": {{ "retCode": 0, "msg": "success" }} }},\n'
         '              {{ "contains": {{ "$.msg": "期望值" }} }}\n'
         '            ]\n'
         '          }}\n'
         '        ]\n'
         '      }}\n'
         '    ],\n'
         '    "file_name": "test_data.yaml"\n'
         '  }}\n\n'
         "### 可用数据工厂方法（动态占位符只能从此清单选择，严格按 syntax 填写）\n"
         "{data_factory_methods}\n\n"
         "### 铁律（依据 YAML 规范）\n"
         "1. api_name/url/method 与接口定义完全一致，中文就中文，禁止翻译；"
         "method 必须小写；url 只写路径，禁止带域名。\n"
         "2. case_name 中文简要描述，禁止带 TC-xxx/PRE-xxx 前缀。\n"
         "3. 请求体三选一，禁止同时出现：json（JSON 体，post/put/patch）/ "
         "params（URL 查询，get/delete）/ data（表单，仅当 Content-Type 为 "
         "application/x-www-form-urlencoded 时才允许使用）。\n"
         "4. header 规则：json 体必须带 Content-Type application/json;charset=UTF-8；"
         "表单 data 必须带 Content-Type application/x-www-form-urlencoded；"
         "仅 params 或文件上传时不写 header；token 等公共头由框架注入，禁止手写。\n"
         "5. 只输出实际用到的字段：extract/input_extract 无需提取时直接省略整个字段，"
         "禁止输出 {{}} 占位、禁止输出值为 null 的条目；有 json 请求体时禁止再带空 params。\n"
         "6. 动态值只能写成 ${{函数名(参数)}} 且函数必须来自上方数据工厂清单，"
         "禁止 {{{{}}}} 双花括号、禁止在占位符内做运算或拼接（如 + 1day）、禁止发明函数。\n"
         "7. 时间偏移一律用 ${{get_offset_time(fmt, days, ...)}}（偏移量可为负=过去，"
         "如明天10点 = ${{get_offset_time(ydm, 1)}} 10:00:00）；"
         "清单不支持的能力写合理固定字面量（如 \"2029-12-31 10:00:00\"）。\n"
         "8. 参数值从接口定义枚举中选取，禁止写中文值。\n"
         "9. 断言字段从接口 returns 中选择，禁止捏造；extract 与断言的 JSONPath 必须以 $ 开头。\n"
         "10. 同一断言类型只输出一个对象，多个字段合并其中（如 eq 同时断言 retCode 和 msg），"
         "禁止拆成多条同类型断言。\n"
         "11. data 数组不能为空，每个步骤至少输出一条。\n"
         "12. 禁止 Markdown，只输出 JSON"),
        ("human",
         "### 数据分析\n{data_analysis}\n\n"
         "### 接口定义\n{api_definitions}\n\n"
         "### 用例逻辑\n{test_case_logic}\n\n"
         "### 用户意图\n{user_context}\n\n"
         "请输出：")
    ])
