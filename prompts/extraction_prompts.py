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
         "分析要详细、具体，后续将基于你的分析生成结构化的数据规划。"),
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
    """Excel 计划修复 prompt：基于原始思考文本重新生成失败行。"""
    return ChatPromptTemplate.from_messages([
        ("system",
         "你正在修复一个 Excel 测试计划。已通过校验的行不需要修改，你只需要重新填写失败的行。\n\n"
         "### 原始任务说明\n{original_system}\n\n"
         "### 原始测试分析报告（供补全缺失信息）\n{original_test_analysis}\n\n"
         "### 已通过的行（无需修改，仅作参考）\n{passed_rows_summary}\n\n"
         "### 失败的行及错误（仅需修复这些行）\n{failed_rows_detail}\n\n"
         "### 修复指南\n"
         "1. 仔细阅读原始测试分析报告，找到失败行对应的用例，补全缺失的步骤或预期\n"
         "2. 如果步骤和预期条数不一致，根据原始报告补齐缺失的条目\n"
         "3. 如果前置引用不存在，检查原始报告中的 PRE 编号，修正为正确编号\n"
         "4. 只修复失败的行，已通过的行原样保留\n"
         "5. 输出完整的 JSON（shared_preconditions + test_cases），禁止 Markdown。"),
        ("human", "请输出修正后的完整 Excel 测试计划 JSON：")
    ])
