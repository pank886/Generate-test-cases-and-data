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
         "3. 每个术语包含 term（名称）和 definition（解释）。\n\n"
         "### 输出\n"
         "直接输出 JSON 对象，包含 terms 数组。不包含 Markdown。"),
        ("human", "### 文档内容\n{doc_text}\n\n请提取业务术语表：")
    ])


def generate_data_plan_prompt() -> ChatPromptTemplate:
    """场景级数据规划 prompt（thinking 节点用）"""
    return ChatPromptTemplate.from_messages([
        ("system",
         "你是测试数据架构师。根据【接口定义】和【用例步骤】，规划测试数据。\n\n"
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
         "2. method 必须是大写的 GET/POST/PUT/DELETE/PATCH。\n"
         "3. url 只提取路径部分，不含域名。\n"
         "4. parameters 提取请求参数结构，无参数填 {{}}。\n"
         "5. returns 提取响应字段结构。\n"
         "6. module_name 根据接口的用途判断所属模块。\n\n"
         "### 输出\n"
         "直接输出 JSON 对象，包含 apis 数组和 module_name。不包含 Markdown。"),
        ("human", "### 接口文档内容\n{doc_text}\n\n请提取所有接口定义：")
    ])


def repair_excel_plan_prompt() -> ChatPromptTemplate:
    """Excel 计划修复 prompt：基于错误上下文重新生成"""
    return ChatPromptTemplate.from_messages([
        ("system",
         "你正在修复一个 Excel 测试计划。\n\n"
         "### 原始任务说明\n{original_system}\n\n"
         "### 用户输入\n{user_vars}\n\n"
         "### 之前的错误输出\n{bad_output}\n\n"
         "### 校验失败原因\n{repair_errors}\n\n"
         "请修复以上错误，输出修正后的完整 JSON。禁止 Markdown。"),
        ("human", "请输出修正后的 Excel 测试计划 JSON：")
    ])
