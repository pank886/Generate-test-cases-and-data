from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder

# 定义 System Prompt 的模板内容
SYSTEM_TEMPLATE = (
    "你是一个资深测试工程师。"
    "请根据以下的【参考资料】和【对话历史】进行回答。"
    "如果【参考资料】里有答案，优先依据资料回答；如果没有，请依据你的专业知识。"
    "\n\n【参考资料】:\n{context}"
)

class PromptFactory:

    def get_prompt_template(self) -> ChatPromptTemplate:
        """
        返回配置好的 Prompt 模板对象
        """
        return ChatPromptTemplate.from_messages([
            ("system", SYSTEM_TEMPLATE),
            MessagesPlaceholder(variable_name="chat_history"),
            ("human", "{user_input}")
        ])

    def parse_api_node(self) -> ChatPromptTemplate:
        """
        分析接口
        """
        return ChatPromptTemplate.from_messages([
        ("system",
         "你是一个资深API架构师。请仔细阅读文档内容，提取其中定义的所有API接口信息。\n"
         "### 提取规则\n"
         "1. **全面性**：提取文档中出现的每一个接口，不要遗漏。\n"
         "2. **结构化**：严格按照 `ApiDefinition` 的字段要求提取（name, url, method, description, parameters）。\n"
         "3. **准确性**：\n"
         "   - `method` 必须是大写的 GET, POST, PUT, DELETE 等。\n"
         "   - `url` 必须是完整的路径（如果有域名请保留）。\n"
         "   - `parameters` 提取关键的请求参数结构，如果文档未提及可留空或填 {{}}。\n"
         "4. **数据清洗（重要）**：提取 `description` 时，**必须去除所有的换行符**，将其合并为一行文本，使用空格或标点分隔。\n"
         "5. **输出格式**：最终结果必须是一个标准的 JSON 数组（List），包含所有提取到的接口对象。"
        ),
        ("human",
         "### 用户需求:\n{user_context}\n\n"
         "### 文档内容:\n{content}\n\n"
         "请结合用户需求，开始提取所有接口定义："
        )
    ])

    def generate_case_node(self) -> ChatPromptTemplate:
        """
        生成测试用例
        """
        return ChatPromptTemplate.from_messages([
        ("system",
         "你是一个高级测试专家。我将提供给你一组**所有相关的接口定义（JSON列表）**。\n"
         "你的任务是设计一个**端到端的业务场景测试用例**。\n"
         "### 核心规则\n"
         "1. **分析依赖**：仔细阅读所有接口。如果接口 B 的功能依赖于接口 A（例如：先'创建/入场'，后'查询/出场'），你必须将它们组合在一个测试用例中。\n"
         "2. **步骤设计**：\n"
         "   - 第一步通常是准备数据（调用写入类接口）。\n"
         "   - 第二步是验证数据（调用查询类接口）。\n"
         "3. **标题**：体现业务价值，例如 '验证车辆入场后能正确查询到在场记录'。\n"
         "4. **前置条件/步骤**：详细描述调用顺序。例如 '1. 调用入场接口 -> 2. 调用查询接口'。\n"
         "5. **输出**：严格只输出 JSON 格式。\n"
         "6. **关键约束**：JSON 字符串中的字段值（如 description, steps）**严禁包含换行符**，请使用空格或标点符号分隔。这会导致 JSON 解析失败。\n"
        ),
        ("human",
         "### 所有可用接口定义:\n{all_apis_info}\n\n"
         "### 用户测试意图:\n{user_context}\n\n"
         "请基于上述接口，设计一个包含完整业务闭环的测试用例："
        )
    ])

    def generate_data_node(self) -> ChatPromptTemplate:
        """
        生成测试数据
        """
        return ChatPromptTemplate.from_messages([
        ("system",
         "你是一个资深测试数据构造专家。\n"
         "### 任务目标\n"
         "根据提供的【接口定义】和【测试用例逻辑】，构造符合接口要求的 JSON 请求体（Payload）。\n\n"
         "### 核心执行步骤（思维链）\n"
         "1. **锁定接口**：根据 `user_context` 的描述以及'test_case_logic'中的测试用例详情，在 `all_apis_info` 中找到匹配的接口（通过 URL 和 Method 匹配）。\n"
         "2. **字段分析**：\n"
         "   - 提取该接口的所有**必填参数**（required=true）。\n"
         "   - 检查每个字段的**类型约束**（如 string, int, enum）和**示例值**（example）。\n"
         "3. **数据映射**：\n"
         "   - 如果 `test_case_logic`和 ‘user_context’ 中指定了具体值（如“车牌号京A88888”），**必须**使用该值。\n"
         "   - 如果未指定，则根据接口定义的示例值或类型生成合理的**模拟数据**。\n"
         "4. **格式校验**：确保生成的 JSON 严格符合接口定义的层级结构。\n\n"
         "### 关键约束\n"
         "- **严禁臆造**：不要生成接口定义中不存在的字段。\n"
         "- **类型严格**：如果是数字类型，不要加引号；如果是枚举，必须在允许范围内。\n"
         "- **单行文本**：JSON 字符串值中严禁包含换行符。\n"
        ),
        ("human",
         "### 接口定义列表:\n{all_apis_info}\n\n"
         "### 测试用例逻辑:\n{test_case_logic}\n\n"
         "### 用户测试意图:\n{user_context}\n\n"
         "### 任务\n"
         "请输出操作所需的 JSON Payload。不要输出任何解释性文字，只输出 JSON 数据。"
        )
    ])

    def generate_assertion_node(self) -> ChatPromptTemplate:
        """
        生成断言
        """
        return ChatPromptTemplate.from_messages([
        ("system",
         "你是一个测试工程师。你的任务是根据**测试用例的业务逻辑**和**输入的测试数据**，定义最终的**业务成功标准**。\n"
         "### 规则\n"
         "1. **分析业务闭环**：\n"
         "   - 如果是“查询”类用例：断言应检查返回的数据列表中包含刚才创建的记录（例如：`data.list` 中包含 `test_data` 里的车牌号）。\n"
         "   - 如果是“状态变更”类用例：断言应检查对象的状态字段是否变为预期值（例如：`data.status` 等于 'PAID'）。\n"
         "2. **关键字段**：优先使用 `test_data` 中的唯一标识字段（如 orderId, plateNo）作为断言的 `expected_value`。\n"
         "3. **通用检查**：始终建议检查 HTTP 状态码或基础 code 字段。\n"
        ),
        ("human",
         "### 测试用例描述:\n{test_case_desc}\n\n"
         "### 前置步骤数据:\n{test_data_payload}\n\n"
         "请定义最终的断言规则："
        )
    ])

    def generate_report_node(self) -> ChatPromptTemplate:
        """
        生成测试报告
        """
        return ChatPromptTemplate.from_messages([
            ("system", "你是一个专业的测试报告生成助手。请根据提供的【测试用例信息】和【执行结果】，生成一份简洁的测试报告。"),
            ("human",
             "### 测试用例信息:\n{test_case_info}\n\n"
             "### 执行结果详情:\n{execution_result}\n\n"
             "请根据以上信息生成测试报告。")
        ])