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


def api_def_extract_prompt() -> ChatPromptTemplate:
    """接口文档提取 prompt — 全量存储，不丢弃任何参数细节。"""
    return ChatPromptTemplate.from_messages([
        ("system",
         "你是 API 分析师。阅读以下文本，提取其中包含的那一个接口的定义。\n\n"
         "### 提取规则\n"
         "1. 文本中只包含一个接口，提取它。\n"
         "2. 每个接口必须包含以下字段：\n"
         '   - name: 接口名称（从文档中的"接口名称/接口描述"或接口标题提取）\n'
         '   - description: 接口功能描述（一句话概括）\n'
         "   - method: 大写的 GET/POST/PUT/DELETE/PATCH\n"
         "   - url: 只提取路径部分，不含域名\n"
         "   - header: 请求头对象（名→值映射），如 {\"Content-Type\": \"application/json\"}\n"
         "   - body: 请求体字段数组（Body + Query 合并）\n"
         "   - return: 响应字段数组\n\n"
         "### 字段数组元素格式（body/return 每个元素恰好 6 个字段）\n"
         '   - name: 字段名（必填，来自参数表「名称」列）\n'
         '   - type: 数据类型（必填，来自「类型」列），如 string/integer/number/boolean/object/array\n'
         '   - required: 是否必填（boolean，来自「是否必须」列）\n'
         '   - default: 默认值（string，来自「默认值」列，无则填空字符串""）\n'
         '   - desc: 字段说明/备注（string，来自「备注」列，无则填空字符串""）\n'
         '   - value: 请求示例/返回示例代码块中该字段的值（string；示例中没有该字段则填空字符串""）\n\n'
         "### 输出格式\n"
         "输出一个 JSON 对象，包含 apis 数组和 module_name 字符串。\n"
         "apis 数组中每个元素是一个接口对象，包含以下字段：\n"
         "  name(string), description(string), method(string), url(string),\n"
         "  header(object), body(array), return(array)\n"
         "body/return 每个数组元素恰含：name(string), type(string), required(boolean),\n"
         "  default(string), desc(string), value(string)\n\n"
         "⚠️ 重要约束：\n"
         '  - header 必须是对象（名→值映射），无请求头时填空对象 {{}}\n'
         "  - body/return 必须是数组，无数据时填空数组 []，绝对不能填 {}\n"
         "  - 字段数组每个元素必须是 6 个字段，一个不少（无值填空字符串\"\"）\n"
         "  - value 只从文档的「请求示例/返回示例」代码块中提取，示例没有该字段则 value 为\"\"，绝不把默认值/说明填进 value\n"
         "  - 文档中标注\"必须\"→required:true，\"非必须\"或未注明→required:false\n"
         "  - 不遗漏任何参数；嵌套子字段保持层级\n"
         "  - 不包含 Markdown。"),
        ("human", "### 接口文档内容\n{doc_text}\n\n请提取所有接口定义，参数列表必须完整：")
    ])


def repair_excel_plan_prompt() -> ChatPromptTemplate:
    """Excel 计划修复 prompt：按错误信息修正失败用例，代码侧根据 failed_ids 裁剪输出。"""
    return ChatPromptTemplate.from_messages([
        ("system",
         "你正在修复一个 Excel 测试计划中的失败用例。按以下要求修正每个失败用例。\n\n"
         "严格按下方 JSON Schema 输出，禁止 Markdown，只输出纯 JSON。\n\n"
         "### JSON Schema（必须严格遵循此结构）\n"
         "```json\n"
         "{json_schema}\n"
         "```\n\n"
         "### 输出 JSON 格式（结构示意，字段定义以上方 JSON Schema 为准）\n"
         "必须输出以下结构的 JSON 对象：\n\n"
         "  {{\n"
         '    "shared_preconditions": [\n'
         '      {{"id": "PRE-001", "name": "已创建测试电表",\n'
         '        "steps": "1.调用 POST /electricMeter/add，填写电表名称\\"测试电表B\\"。\\n2.确认返回创建成功。",\n'
         '        "expected": "1.[eq]返回200，创建成功。"}}\n'
         '    ],\n'
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
         "- shared_preconditions 元素 = {{id, name, steps, expected}}，name 必填；禁止输出 story/title/preconditions/mutates_data/is_negative_test/cloned_from 等用例字段\n\n"
         "### 测试场景分析（参考上下文）\n{analysis_section}\n\n"
         "### 共享前置（参考原始设计，如有错误接口路径需一并修正）\n{shared_pre_section}\n\n"
         "### 完整用例描述（参考原始设计）\n{cases_section}\n\n"
         "### 模块树\n{module_tree}\n\n"
         "### 接口定义列表（核对接口路径用）\n{all_apis_info}\n\n"
         "### 数据库表结构信息（为空时禁止 [db] 断言）\n{db_schema}\n\n"
         "### 失败的行及错误\n{failed_test_cases}\n\n"
         "### 拦截方法提示（以下为被拦截的用例与原因提示，修正时需消除对应问题）\n{block_reasons}\n\n"
         "### 修正要求\n"
         "1. 依据上方「失败的行及错误」与「拦截方法提示」修正失败用例，保持正确字段不变\n"
         "2. 步骤中引用的接口路径必须能在「接口定义列表」中匹配到真实接口；疑似 URL 拼写错误时改为正确的 url\n"
         "3. 若共享前置（PRE-xxx）的步骤含错误接口路径，在 shared_preconditions 中输出修正后的版本（按原 id 修正即可）\n"
         "4. 若「数据库表结构信息」为空，禁止在 expected 中使用 [db] 断言，改用 [eq]/[contains]/[ne]\n"
         "5. 修正后必须满足上方「字段硬约束」：五字段齐全、步骤/预期条数一致、前置引用有效\n"
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
         "- **db 断言禁止**：若「数据库表结构信息」为空，禁止生成 db 断言（无表结构无法写正确 SQL），改用 eq/contains/ne\n"
         "- **导出/下载/模板接口**（URL 含 export/import/template/download/upload 或接口标注 is_export）：返回二进制流，"
         "断言必须用 `contains: {{status_code: 200}}`，禁止 eq/ne 检查状态码\n"
         "- **对 `status_code` 的断言必须用 `contains: {{status_code: X}}`，禁止 eq/ne**（不限于导出接口；导出接口维持 contains: {{status_code: 200}}）\n"
         "- **`contains` 的值必须是字典对象**（`{{字段: 期望}}` 或 `{{$.JSONPath: 期望}}`），禁止裸字符串/标量\n"
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
         "### 数据库表结构信息（为空时禁止 db 断言）\n{db_schema}\n\n"
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
         "- **若「数据库表结构信息」为空，禁止 db 断言**，改用 eq/contains/ne\n"
         "- **导出/下载/模板接口**（URL 含 export/import/template/download/upload）：断言用 contains: {{status_code: 200}}，禁止 eq/ne 检查状态码\n"
         "- **对 `status_code` 的断言必须用 `contains: {{status_code: X}}`，禁止 eq/ne**（不限于导出接口）\n"
         "- **`contains` 的值必须是字典对象**（`{{字段: 期望}}` 或 `{{$.JSONPath: 期望}}`），禁止裸字符串\n"
         "- 修正时保持原有正确部分不动，只改错误部分"),
        ("human",
         "{post_check_issues}"
         "### 接口定义\n{api_definitions}\n\n"
         "### 用例逻辑\n{test_case_logic}\n\n"
         "### 用户意图\n{user_context}\n\n"
         "### 数据库表结构信息（为空时禁止 db 断言）\n{db_schema}\n\n"
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
         '          "validation": [{{"contains": {{"$.msg": "成功"}}}}],\n'
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
         '          "validation": [{{"contains": {{"$.data.records[0].meterName": "${{get_extract_data(meterName)}}"}}}}]\n'
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
         "7. url 必须与接口序列（锚）中的 url **逐字一致**（含 {{param}} 字面量）；"
         "只写路径、禁域名、禁 query、禁 ${{}}；路径参数禁止替换成具体值"
         "（具体值/query 走 testCase.params 或 json）\n"
         "8. **每个 baseInfo 必须有 header 字段**：POST/PUT/PATCH 写 Content-Type: application/json, GET 写空 {{}}\n"
         "9. 请求参数按 HTTP 方法选择：GET/DELETE → params, POST/PUT/PATCH → json\n"
         "10. 动态值使用 ${{函数名(参数)}}，函数必须来自上方清单\n"
         "11. extract/input_extract 用不到就省略整个字段，禁止输出空 {{}} 或 null\n"
         "12. validation 数组不能为空，每步至少一条断言；断言的期望值须按规则 19 取自接口返回定义\n"
         "13. 断言运算符只用 [eq, contains, ne, db] 四种，不等于是 ne 不是 neq\n"
         "14. **若「数据库表结构信息」为空，禁止 db 断言**（无表结构无法写正确 SQL），改用 eq/contains/ne\n"
         "15. **对 `status_code` 的断言必须用 contains: {{status_code: X}}，禁止 eq/ne**（适用于所有接口，"
         "不限于导出；导出/下载/模板接口返回二进制流，维持 contains: {{status_code: 200}}）\n"
         "16. JSONPath 必须以 $. 开头（如 $.data.code）\n"
         "17. 禁止 Markdown，只输出纯净 JSON\n"
         "18. **`contains` 的值必须是字典对象**（`{{字段: 期望}}` 或 `{{$.JSONPath: 期望}}`），禁止裸字符串/标量\n"
         "19. **成功/失败断言的期望值取自接口返回定义**：断言的字段与期望值必须取自「接口定义」"
         "返回定义中真实给出的字段/取值/语义；正向用例断言业务成功对应的返回取值，反向用例"
         "断言失败返回取值；返回定义未给出明确取值时，退化为 contains 字段存在性或 status_code "
         "断言，禁止臆造固定取值"),
        ("human",
         "### 数据分析\n{data_analysis}\n\n"
         "### 接口定义\n{api_definitions}\n\n"
         "### 用例逻辑\n{test_case_logic}\n\n"
         "### 用户意图\n{user_context}\n\n"
         "### 数据库表结构信息（为空时禁止 db 断言）\n{db_schema}\n\n"
         "请严格按照上方 JSON 结构输出：")
    ])


def generate_yaml_data_single_prompt() -> ChatPromptTemplate:
    """Phase C YAML 数据 — 单节点专用：schema 驱动，无手写示例。

    thinking + json_object 一次调用（thinking 走 reasoning_content）。
    与两段式的 format_yaml_data_prompt 区别：
      - 格式唯一来源 = 下方 json_schema（TestData.model_json_schema()），无示例块；
      - json_schema 为固定内容，放 system 段（提升 schema 遵循命中率 + system 消息
        字节一致利于 prompt 缓存）；human 只放逐用例/逐接口的可变内容；
      - 铁律为抽象规则措辞，不含可照抄的断言字面量（防 LLM 照抄）；
      - 含 19 号文件 prompt 可修缺陷：成功/失败断言约定、数据唯一化、delete 按定义。
    """
    return ChatPromptTemplate.from_messages([
        ("system",
         "你是数据格式化专家。yaml 格式 schema（固定内容）见下方 system 段；B 用例内容、"
         "A 数据分析、接口详情文档在 human 段。一次输出 TestData 模型的 JSON（Pydantic "
         "校验，字段与类型一个都不能错）。本 prompt 不含任何具体业务示例，禁止编造与输入"
         "无关的固定数据。\n\n"
         "### 可用数据工厂方法（动态占位符只能从此清单选择，严格按 syntax 填写）\n"
         "{data_factory_methods}\n\n"
         "### yaml 格式 schema（输出结构唯一来源，禁止自创结构/推断字段）\n"
         "{json_schema}\n\n"
         "### 铁律（抽象规则，无示例；内容唯一来源 = 上方 yaml 格式 schema + "
         "human 段 B 用例内容 / A 数据分析 / 接口详情文档）\n"
         "1. 顶层只能有一个 data 数组，每元素含 baseInfo 与 testCase 两个键\n"
         "2. 请求体三选一（json/params/data 只出现一个）：按接口定义 HTTP 方法选——"
         "GET/DELETE 用 params，POST/PUT/PATCH 用 json\n"
         "3. url 与接口定义中的路径完全一致：只写路径、禁 query/域名/占位符表达式；"
         "路径参数不得替换成具体值\n"
         "4. 每个 baseInfo 必有 header 键：JSON 请求体方法写 Content-Type=application/json，"
         "其余写空对象\n"
         "5. validation 数组不得为空，每步至少一条断言；运算符只允许 eq/contains/ne/db\n"
         "6. status_code 只能被 contains 断言，且 contains 的值必须是字典对象"
         "（键=字段名、值=期望值）；禁止用 eq/ne 断言 status_code\n"
         "7. JSONPath 一律以 $. 开头\n"
         "8. extract/input_extract 用不到就整字段省略，禁止输出空对象或 null\n"
         "9. **成功/失败断言的期望值取自接口返回定义**：断言的字段与期望值必须取自"
         "「接口详情文档」返回定义中真实给出的字段/取值/语义；正向用例断言业务成功对应的"
         "返回取值，反向用例断言失败返回取值；返回定义未给出明确成功/失败取值时，退化为"
         "contains 字段存在性或 status_code 断言，禁止臆造返回定义之外的固定取值\n"
         "10. **数据唯一化**：设备名/编码等唯一键必须动态生成（${{ }} 工厂方法或时间戳/"
         "随机后缀），禁止输出固定假值——防跨套件重名与套件内自碰撞\n"
         "11. **delete 参数按接口定义**：delete 请求参数严格按接口定义填写，禁止输出"
         "接口定义之外的任何字段\n"
         "12. 禁止 Markdown；只输出纯净 JSON（Pydantic 校验，字段/类型一个都不能错）"),
        ("human",
         "### B 用例内容（本用例执行步骤 + 预期结果）\n{test_case_logic}\n\n"
         "### A 数据分析（生成前思考要点引导）\n{data_analysis}\n\n"
         "### 接口详情文档（路径/请求字段/返回语义，取值唯一来源）\n{api_definitions}\n\n"
         "### 用户意图\n{user_context}\n\n"
         "### 数据库表结构信息（为空时禁止 db 断言）\n{db_schema}\n\n"
         "请严格按照 system 段的 yaml 格式 schema 输出 TestData JSON：字段名与结构以 schema 为准，字段值根据上述分析和用例上下文合理生成，可优先参考用例中已出现的具体取值，禁止使用示例或占位数据。"
         )
    ])


def generate_dependency_map_prompt() -> ChatPromptTemplate:
    """Phase C Step 0: 生成 dependency_map.json（thinking 节点用）。

    输入: Excel 行、接口定义、模块树、产品文档、数据工厂方法
    输出: DependencyMap 模型 JSON
    """
    return ChatPromptTemplate.from_messages([
        ("system",
         "你是测试架构师。根据测试计划、接口定义和模块结构，生成依赖映射表。\n\n"
         "### 输出 JSON 结构（严格按下方 json_schema 的字段与类型输出，一个都不能错）\n" 
         "{json_schema}\n\n"
         "### 铁律\n"
         "1. story_name 与 Excel @allure.story 完全一致\n"
         "2. case_api_sequences 中每个 case_id 至少有一个 API 步骤\n"
         "3. case_api_sequences / internal_dependency / decision_map 的 key 集合必须完全一致\n"
         "4. decision_map 的 api 格式: 'METHOD /url'（如 'POST /meterDevice/add'）\n"
         "5. 动态值使用 ${{函数名(参数)}}，函数必须来自上方数据工厂清单\n"
         "6. extract_path 必须以 $. 开头，从接口 returns 中选择字段\n"
         "7. used_by 引用的 case_id 必须在本 story 的 case_api_sequences 中存在\n"
         "8. **有共享前置的 story（其用例引用 PRE-xxx）必须输出非空 story_pre_api_sequence 与 teardown_api_sequence**"
         "——setup/teardown 锚绝不允许为空（有前置必有其前置序列与清理序列）\n"
         "9. 无共享前置的 story：story_pre_api_sequence / teardown_api_sequence 可留空 []\n"
         "10. case_api_sequences / story_pre_api_sequence / teardown_api_sequence 每个元素格式:"
         "'步骤名:METHOD /url'（如 '创建订单:POST /order/create'）；步骤名可省略，但 METHOD 与 /url 必须给出\n"
         "11. 禁止 Markdown，只输出纯净 JSON"),
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


def repair_dependency_map_prompt() -> ChatPromptTemplate:
    """Phase C Step 0 补漏修复：只补全第一轮遗漏的用例/story 前置序列（D5）。

    结构与 generate_dependency_map_prompt 一致；入参增加：
      - repair_cases: 第一轮遗漏的用例行 JSON（待补数据）
      - repair_stories: 有共享前置但 story_pre/teardown 为空的 story（story_name + preconditions）
      - analysis: Phase B 模块分析（ModuleAnalysis，辅助接口匹配）
    系统指令强调"只补漏、三表 key 一致、不重复已生成、story 级补 pre/teardown"。
    """
    return ChatPromptTemplate.from_messages([
        ("system",
         "你是测试架构师。第一轮生成的依赖映射遗漏了部分用例，或部分 story 的前置/清理序列为空，"
         "请**只补全这些缺口**。\n\n"
         "### 输出 JSON 结构（严格按下方 json_schema 的字段与类型输出，一个都不能错）\n"
         "{json_schema}\n\n"
         "### 补漏铁律\n"
         "1. **只输出【待补用例 / 待补 story】的映射**（来自 repair_cases / repair_stories），不重复已生成的\n"
         "2. 待补 case_id 必须在 case_api_sequences / decision_map / internal_dependency 三表同时出现（key 集合一致）\n"
         "3. **待补 story（repair_stories）：必须输出非空 story_pre_api_sequence 与 teardown_api_sequence**"
         "（其用例有共享前置 PRE-xxx，有前置必有其前置序列与清理序列，绝不允许留空）\n"
         "4. story_name 与 Excel story 列 / 待补列表一致；若该 story 已在首轮存在，只输出该 story 中新增的 case 与缺失的 pre/teardown\n"
         "5. decision_map 的 api 格式: 'METHOD /url'（如 'POST /meterDevice/add'）\n"
         "6. 动态值使用 ${{函数名(参数)}}，函数必须来自数据工厂清单\n"
         "7. extract_path 必须以 $. 开头，从接口 returns 中选择字段\n"
         "8. used_by 引用的 case_id 必须存在\n"
         "9. case_api_sequences / story_pre_api_sequence / teardown_api_sequence 每个元素格式:"
         "'步骤名:METHOD /url'（如 '创建订单:POST /order/create'）；步骤名可省略，但 METHOD 与 /url 必须给出\n"
         "10. 禁止 Markdown，只输出纯净 JSON"),
        ("human",
         "### Phase B 模块分析（辅助接口匹配，可直接参考）\n{analysis}\n\n"
         "### 待补用例（第一轮遗漏，只补这些；若无则写 []）\n{repair_cases}\n\n"
         "### 待补 story 的前置/清理序列（有共享前置但首轮留空；若无则写 []）\n{repair_stories}\n\n"
         "### 数据工厂方法（动态占位符只能从此清单选择）\n"
         "{data_factory_methods}\n\n"
         "### 接口定义\n{all_apis_info}\n\n"
         "### 模块树\n{module_tree}\n\n"
         "### 产品文档\n{product_docs}\n\n"
         "### 上下文备注\n{context_note}\n\n"
         "### 用户意图\n{user_context}\n\n"
         "请生成补漏依赖映射 JSON：")
    ])


# ====================================================================
# Phase A: 批量 chunk 摘要（入库时生成 simple_summary）
# ====================================================================

def batch_chunk_summary_prompt() -> ChatPromptTemplate:
    """批量 chunk 摘要：5 chunks/批，LLM 输出 ===CHUNK_SUMMARY=== 分隔词，正则解析。"""
    return ChatPromptTemplate.from_messages([
        ("system",
         "你是文档总结专家。为以下文本块分别生成一句话摘要（50字以内），概括核心内容。\n\n"
         "### 输出格式\n"
         "每个摘要以 ===CHUNK_SUMMARY=== 开头，独占一行，紧接着是摘要内容。\n"
         "不要输出 JSON，不要编号，不要 Markdown。\n\n"
         "===CHUNK_SUMMARY===\n"
         "摘要1的内容\n"
         "===CHUNK_SUMMARY===\n"
         "摘要2的内容\n"
         "..."),
        ("human",
         "以下是文档《{file_name}》的连续文本块，第 {start_idx}-{end_idx} 块 / 共 {total} 块。\n"
         "每块位于页面「{page_name}」。\n\n"
         "{chunks}\n\n"
         "请为每个文本块生成一句摘要（50字以内），以 ===CHUNK_SUMMARY=== 分隔：")
    ])


# ====================================================================
# Phase A: 三步分析管线（2026-07-31 讨论确认）
# ====================================================================

def analyze_product_scenarios_prompt() -> ChatPromptTemplate:
    """Step 1: 产品文档 → 测试场景总结（thinking 模式，自由文本输出）。"""
    return ChatPromptTemplate.from_messages([
        ("system",
         "你是测试分析师。阅读以下产品需求文档，提取所有测试场景。\n\n"
         "### 分析要求\n"
         "1. 识别文档中描述的所有业务场景（如增删改查、导入导出、审批流程等）\n"
         "2. 每个场景下列出所有功能点（测试点）\n"
         "3. 每个测试点标注覆盖维度（正向/边界/反向-业务/反向-字段/安全）\n"
         "4. 注意跨模块依赖和数据约束\n\n"
         "### 输出\n"
         "自由文本分析报告，不需要 JSON 格式。\n"
         "结构建议：场景名 → 描述 → 功能点列表（含 scope）→ 关键数据约束。"),
        ("human",
         "### 模块名\n{module_name}\n\n"
         "### 产品文档\n{product_docs}\n\n"
         "### 跨模块关系\n{cross_module_relations}\n\n"
         "请分析以上产品文档的测试场景：")
    ])


def analyze_axure_ui_flow_prompt() -> ChatPromptTemplate:
    """Step 2: 场景总结 + Axure → 页面交互逻辑总结（thinking 模式，自由文本输出）。"""
    return ChatPromptTemplate.from_messages([
        ("system",
         "你是 UI/UX 分析师。根据已知的测试场景，分析 Axure 原型页面中的交互逻辑。\n\n"
         "### 分析要求\n"
         "1. 识别每个场景涉及的页面（列表页、表单页、详情页等）\n"
         "2. 分析页面间的跳转关系（触发动作 → 跳转目标）\n"
         "3. 提取页面中的数据表单结构（字段名、类型、必填等）\n"
         "4. 标注 UI 层面的约束（按钮状态、表单联动、权限控制）\n\n"
         "### 输出\n"
         "自由文本分析报告，不需要 JSON 格式。\n"
         "结构建议：场景 → 关联页面 → 页面跳转关系 → 表单字段 → UI 约束。"),
        ("human",
         "### 模块名\n{module_name}\n\n"
         "### 测试场景总结（Step 1 输出）\n{scenario_analysis}\n\n"
         "### Axure 原型页面内容\n{axure_pages}\n\n"
         "请根据场景分析 Axure 页面交互逻辑：")
    ])


def analyze_api_mapping_prompt() -> ChatPromptTemplate:
    """Step 3: 场景 + 逻辑关系 + API → 接口映射总结（thinking 模式，自由文本输出）。"""
    return ChatPromptTemplate.from_messages([
        ("system",
         "你是接口分析师。根据已知的测试场景和页面交互逻辑，分析接口定义与业务场景的映射关系。\n\n"
         "### 分析要求\n"
         "1. 将每个 API 接口映射到对应的业务场景和功能点\n"
         "2. 分析接口间的数据依赖关系（produces → consumes）\n"
         "3. 识别跨模块接口调用链\n"
         "4. 标注数据流向（哪个接口产出什么数据 → 哪个接口消费）\n\n"
         "### 输出\n"
         "自由文本分析报告，不需要 JSON 格式。\n"
         "结构建议：接口→场景映射 → 数据依赖链 → 跨模块调用链 → 关键约束。"),
        ("human",
         "### 模块名\n{module_name}\n\n"
         "### 测试场景总结（Step 1 输出）\n{scenario_analysis}\n\n"
         "### 页面交互逻辑（Step 2 输出）\n{ui_flow_analysis}\n\n"
         "### 接口定义\n{api_definitions}\n\n"
         "### 模块树\n{module_tree}\n\n"
         "### 跨模块关系\n{cross_module_relations}\n\n"
         "请分析接口与场景的映射关系：")
    ])
