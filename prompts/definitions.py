from langchain_core.prompts import ChatPromptTemplate

# ============================================================
# 字段 Schema 定义已迁移至 response_model.py
# LLM 输出结构由 Pydantic 模型 + Function Calling 约束
# 本文件仅维护 Prompt 文本，不再包含 JSON Schema 字符串
# 详见 prompts/response_model.py
# ============================================================

class PromptFactory:

    def parse_api_node(self) -> ChatPromptTemplate:
        """
        分析接口
        """
        return ChatPromptTemplate.from_messages([
        ("system",
         "你是一个资深API架构师。请仔细阅读文档内容，提取其中定义的所有API接口信息。\n"
         "### 提取规则\n"
         "1. **全面性**：提取文档中出现的每一个接口，不要遗漏。\n"
         "2. **结构化**：严格按照 `ApiDefinition` 的字段要求提取（name, url, method, description, parameters, returns）。\n"
         "3. **准确性**：\n"
         "   - `method` 必须是大写的 GET, POST, PUT, DELETE 等。\n"
         "   - `url` **只提取路径部分，不含域名和基础地址**（测试框架会自动拼接 base_url）。\n"
         "     正确示例: `/api/login`、`/park-access-parking-rule-new/mock/delAllForMock`\n"
         "     错误示例: `http://localhost:8000/api/login`、`https://dev.damaiiot.com:40443/api/login`\n"
         "   - `parameters` 提取关键的请求参数结构，如果文档未提及可留空或填 {{}}。\n"
         "   - **`returns` 提取接口响应的返回字段结构**，包括字段名、类型、说明。\n"
         "     ⚠️ returns 必须是 JSON 对象（dict），即使响应是纯数组也要用 {{\"data\": [...]}} 包装，绝不能直接输出数组。\n"
         "     例如响应为 {{\"success\": true, \"code\": 0, \"data\": {{...}}}} 则 returns = {{\"success\": \"boolean\", \"code\": \"integer\", \"data\": \"object\"}}\n"
         "4. **数据清洗（重要）**：提取 `description` 时，**必须去除所有的换行符**，将其合并为一行文本，使用空格或标点分隔。\n"
         "5. **输出格式**：必须输出一个 JSON **对象**，对象中包含 `apis` 键，值为接口列表。\n"
         '   ✅ 正确格式: {{"apis": [{{"name": "接口名", "url": "路径", "method": "POST", "description": "描述", "parameters": {{}}, "returns": {{}}}}]}}\n'
         "   注意：最外层必须是 `{{...}}` 对象，不是 `[...]` 数组。"
        ),
        ("human",
         "### 用户需求:\n{user_context}\n\n"
         "### 文档内容:\n{content}\n\n"
         "请结合用户需求，开始提取所有接口定义："
        )
    ])

    def generate_data_node(self) -> ChatPromptTemplate:
        """
        生成结构化测试数据（由 function_calling 约束输出结构，无需 prompt 内嵌 Schema）
        """
        return ChatPromptTemplate.from_messages([
        ("system",
         "你是资深测试数据构造专家。根据【接口定义】与【用例逻辑】，生成测试数据。\n\n"
         "### 输出结构（仅限以下字段，禁止编造）\n\n"
         "```yaml\n"
         "- baseInfo:\n"
         "    api_name: \"接口名称\"          # 必填，与接口定义一致，中文就中文\n"
         "    url: /path/to/api            # 必填，与接口定义一致\n"
         "    method: post                 # 必填，get/post/put/delete/patch\n"
         "    header:                      # 必填\n"
         "      Content-Type: application/json\n"
         "    cookies: {{}}                 # 可选\n"
         "  testCase:\n"
         "    - case_name: \"场景描述\"      # 必填，中文简要描述\n"
         "      # ---- 请求参数（三选一） ----\n"
         "      json: {{ ... }}              # JSON 请求体（post/put/patch 用）\n"
         "      params: {{ ... }}            # URL query 参数（get/delete 用）\n"
         "      data: {{ ... }}              # form 表单体（极少用）\n"
         "      # ---- 数据传递（可选） ----\n"
         "      extract: {{ key: \"$.jsonpath\" }}\n"
         "      extract_list: {{ key: \"$.jsonpath[*]\" }}\n"
         "      input_extract: {{ key: \"$.json.字段名\" }}\n"
         "      # ---- 断言 ----\n"
         "      validation:\n"
         "        - eq: {{ retCode: 0 }}\n"
         "        - contains: {{ msg: \"success\" }}\n"
         "        - ne: {{ retCode: 0 }}\n"
         "        - db: {{ sql: \"SELECT ...\", data: [...] }}\n"
         "```\n\n"
         "### 铁律\n"
         "1. **字段仅限上面列出的**：禁止编造 json_data / request_body / body / form 等变体。\n"
         "2. **api_name / url / method 与接口定义完全一致**，中文就写中文，禁止翻译。\n"
         "3. **case_name 中文简要描述**（如 新增设施成功），禁止带 TC-xxx/PRE-xxx 前缀。\n"
         "4. **json 对应 JSON 请求体（90% 场景），params 对应 GET URL 参数，data 对应表单**。三者都会被框架处理 ${} 占位符。\n"
         "5. 参数值从接口定义的示例/枚举中选取，有枚举说明（如 status: 0-正常/1-维修）则用枚举值（0/1），禁止写中文描述。\n"
         "6. 断言字段必须从接口 returns 中实际存在的字段中选择，不得捏造。\n"
         "7. 仅输出有实际数据的字段，可选字段为空时不输出。\n"
         "8. **数据工厂方法**（在参数值中使用 ${{方法名}}）：\n"
         "{data_factory_methods}\n\n"
         "禁止 Markdown、禁止解释文字，只输出 YAML。"
        ),
        ("human",
         "### 接口定义\n{all_apis_info}\n\n"
         "### 用例逻辑\n{test_case_logic}\n\n"
         "### 用户意图\n{user_context}\n\n"
         "请输出测试数据："
        )
    ])
    def analyze_scenarios(self) -> ChatPromptTemplate:
        """
        Phase A — 场景分析（thinking 节点用）：输出自由文本分析报告。
        """
        return ChatPromptTemplate.from_messages([
            ("system",
             "你是高级测试设计专家。根据【接口定义】和【用户意图】，分析测试场景。\n\n"
             "请分析以下方面（自由文本输出，不要输出 JSON）：\n"
             "1. **场景划分**：根据接口功能划分测试场景，列出每个场景的标题和包含的接口\n"
             "2. **用例设计思路**：每个场景需要哪些测试用例（边界值、异常、主流程）\n"
             "3. **数据依赖**：接口间的数据传递关系和依赖顺序\n"
             "4. **前置条件**：需要哪些前置数据准备\n\n"
             "分析要全面、详细，后续将基于你的分析生成 Excel 测试计划。"
            ),
            ("human",
             "### 接口定义列表:\n{all_apis_info}\n\n"
             "### 用户测试意图:\n{user_context}\n\n"
             "请分析以上接口的测试场景："
            )
        ])

    def generate_excel_plan_node(self) -> ChatPromptTemplate:
        """
        生成 Excel 测试计划 V2（双 Sheet，format 节点用，thinking off + json_mode）。
        """
        return ChatPromptTemplate.from_messages([
        ("system",
         "你是数据转换专家。根据【测试分析报告】，提取所有共享前置和测试用例，输出严格 JSON。\n\n"
         "### 输出 JSON 格式\n"
         "必须输出以下结构的 JSON 对象：\n\n"
         "  {{\n"
         '    "shared_preconditions": [\n'
         '      {{"id": "PRE-001", "name": "已创建测试跑步机",\n'
         '        "steps": "1.调用新增设施接口\\n2.校验创建成功",\n'
         '        "expected": "设施列表中出现测试跑步机"}}\n'
         '    ],\n'
         '    "test_cases": [\n'
         '      {{"id": "TC-001",\n'
         '        "story": "设施添加",\n'
         '        "title": "设施管理-新增设施-正向",\n'
         '        "preconditions": [],\n'
         '        "steps": "1.调用新增设施接口\\n2.查询详情",\n'
         '        "expected": "1.创建成功\\n2.信息一致",\n'
         '        "mutates_data": true,\n'
         '        "is_negative_test": false}},\n'
         '      {{"id": "TC-002",\n'
         '        "story": "设施修改",\n'
         '        "title": "设施管理-修改设施-正向",\n'
         '        "preconditions": ["PRE-001"],\n'
         '        "steps": "1.调用修改接口\\n2.查询详情",\n'
         '        "expected": "1.修改成功\\n2.信息已更新",\n'
         '        "mutates_data": true,\n'
         '        "is_negative_test": false}}\n'
         '    ],\n'
         '    "file_name": "test_plan.xlsx"\n'
         '  }}\n\n'
         "### 字段说明\n"
         "**shared_preconditions**：id/name/steps/expected，从测试分析报告中直接提取\n"
         "**test_cases**：\n"
         "- id: TC 编号\n"
         "- story: 子模块名（对应 @allure.story），从文档提取的模块名，如「设施管理」\n"
         "- title: 用例名称（对应 @allure.title），如「设施管理-新增设施-正向」\n"
         "- preconditions: PRE 编号数组，无则为 []\n"
         "- steps/expected: 文本，\\n 分隔，条数一致\n"
         "- mutates_data: 分析【执行步骤】，含增/删/改/状态变更/重置/清理 → true；仅查询 → false\n"
         "- is_negative_test: 分析【预期结果】，含失败/报错/异常/不存在/无权/冲突/重复 → true；否则 false\n"
         "- file_name: 固定 test_plan.xlsx\n\n"
         "### 字段硬约束（违反即校验失败）\n"
         "- 字段名必须是 story，禁止写 sub_module / module / feature_name 等变体\n"
         "- steps 和 expected 必须是**字符串**（\\n 分隔），禁止输出数组/列表\n"
         "- steps 和 expected 条数必须一致\n\n"
         "### 规则\n"
         "1. 每个 PRE-xxx → 一个 shared_preconditions 对象\n"
         "2. 每个 TC-xxx → 一个 test_cases 对象\n"
         "3. preconditions 的 PRE 必须存在于 shared_preconditions\n"
         "4. 禁止 Markdown、禁止解释，只输出 JSON"
        ),
        ("human",
         "### 模块树:\n{module_tree}\n\n"
         "### 测试分析报告:\n{test_analysis}\n\n"
         "### 接口定义列表:\n{all_apis_info}\n\n"
         "### 用户测试意图:\n{user_context}\n\n"
         "请提取所有共享前置和测试用例，输出 JSON："
        )
    ])

    def analyze_test_points_raw(self) -> ChatPromptTemplate:
        """
        Phase B — 测试点原始分析（thinking 节点用）：输出自由文本分析报告。
        """
        return ChatPromptTemplate.from_messages([
            ("system",
             "你是一位资深测试架构师，专注于**接口自动化测试用例设计**。\n\n"
             "根据【产品文档】和【接口定义】，设计详细的测试用例，按以下**固定模板**输出。\n\n"
             "### 输出模板（必须严格遵守）\n\n"
             "## 共享前置\n"
             "列出所有模块共用的数据准备步骤。每个前置使用**全局唯一编号**（PRE-001 开始递增）。\n"
             "格式：\n"
             "- PRE-001: 前置名称（模块：所属模块名）\n"
             "    步骤: 1.具体操作步骤1\n2.具体操作步骤2\n"
             "    预期: 操作完成后的预期状态\n"
             "示例：\n"
             "- PRE-001: 已创建测试跑步机（模块：设施管理）\n"
             "    步骤: 1.调用新增设施接口，名称\"测试跑步机\"\n2.校验创建成功\n"
             "    预期: 设施列表中出现\"测试跑步机\"\n\n"
             "## 测试用例\n"
             "每个用例一个条目，使用**全局唯一编号**（TC-001 开始递增）。\n"
             "格式：\n"
             "- TC-xxx: 用例标题\n"
             "    子模块: 从文档中提取的模块名（如「设施管理」，不是功能点「设施添加」）。嵌套时用 A-a 格式\n"
             "    前置: PRE-xxx 或 无\n"
             "    步骤: 1.操作步骤1\n2.操作步骤2\n"
             "    预期: 1.[eq]预期结果1\n2.[eq]预期结果2\n"
             "示例：\n"
             "- TC-001: 设施管理-新增设施-正向\n"
             "    子模块: 设施管理\n"
             "    前置: 无\n"
             "    步骤: 1.调用新增设施接口，传入名称、介绍及图片\n2.调用查询详情接口查看设施信息\n3.调用分页查询接口搜索设施\n"
             "    预期: 1.[eq]接口返回成功，生成ID\n2.[eq]设施信息与新增一致\n3.[contains]分页列表包含该设施\n\n"
             "### 断言关键词（预期结果中必须使用，下游按关键词生成断言数据）\n"
             "- [eq] 相等断言：验证返回值与预期完全相等，用于增删改返回的标识字段（如 success/retCode）\n"
             "- [contains] 包含断言：验证返回值包含预期内容，用于查询结果校验\n"
             "- [ne] 不相等断言：验证返回值不等于预期值，用于确认删除/变更后旧数据不存在\n"
             "- [db] 数据库断言：验证数据库中是否存在对应记录，用于数据持久化校验\n\n"
             "### 强制规则（违反将导致用例被丢弃）\n"
             "- PRE-xxx 从 PRE-001 开始，全局唯一，递增\n"
             "- TC-xxx 从 TC-001 开始，全局唯一，递增\n"
             "- 前置字段直接引用 PRE 编号（如 PRE-001, PRE-002），禁止写「执行共享前置X」\n"
             "- 步骤和预期必须一一对应、条数精确相等：步骤有 N 条 → 预期必须有 N 条。每个用例写完立即自查\n"
             "- 前置引用只能是 PRE-xxx，禁止写 TC-xxx 或其他格式\n\n"
             "### 用例设计规范（必须严格遵守）\n\n"
             "**模块与功能点识别**：\n"
             "- 必须先从产品文档中识别出所有子模块和子模块下的嵌套模块\n"
             "- 每个模块下必须识别出所有功能点（新增、查询、修改、删除、导出、审批等）\n"
             "- 每个功能点至少对应 3-5 条测试用例，确保充分覆盖\n"
             "- 当某个功能点文档描述不详细时，至少编写 3 条用例（正常+边界+异常）\n\n"
             "**正向测试**：\n"
             "- 每个操作（新增/修改/删除/查询）至少一个正向用例\n"
             "- 审批类操作（提交审批、审批通过、审批驳回）各至少一个正向用例\n\n"
             "**反向逻辑**：\n"
             "- 业务取消类（取消预约、取消订单、取消审批等）需独立用例\n"
             "- 逆向场景至少覆盖 5 类（非法输入、权限不足、流程跳转异常、数据冲突、状态不匹配）\n"
             "- 每条逆向用例必须明确异常触发条件和预期报错信息\n\n"
             "**字段校验**：\n"
             "- 必填字段缺失、格式错误、超长输入各至少一个异常用例\n"
             "- 特殊字符、SQL 注入类字段校验\n\n"
             "**边界值**：\n"
             "- 数值字段：最小值-1、最小值、最大值、最大值+1\n"
             "- 时间字段：临界时刻（如免费时段最后一秒、过期前一秒）\n"
             "- 空值、零值、负值\n\n"
             "**异常场景**：\n"
             "- 权限不足、数据冲突、并发操作、依赖接口不可用\n"
             "- 网络超时模拟\n\n"
             "**跨模块联动**：利用关联模块文档设计端到端场景\n\n"
             "**智能发现**：基于业务逻辑理解，主动发现规则之外可测试的关键点\n\n"
             "**用例质量要求**：\n"
             "- 无冗余、无重复、无遗漏，逻辑严谨\n"
             "- 完整覆盖等价类划分、边界值分析、场景法、错误推测法\n"
             "- 逆向用例数量不低于正向用例的 1/3\n\n"
             "请输出**自由文本分析报告**，不要输出 JSON。"
            ),
            ("human",
             "### 用户需求\n{user_context}\n\n"
             "### 产品文档片段\n{product_docs}\n\n"
             "### 关联模块产品文档\n{related_docs}\n\n"
             "### 接口定义\n{api_definitions}\n\n"
             "请分析以上信息的测试场景："
            )
        ])

    def format_test_points(self) -> ChatPromptTemplate:
        """
        Phase C — 格式化测试点为 JSON（thinking off + json_mode）。
        接受 test_point_analysis 作为分析上下文。
        """
        return ChatPromptTemplate.from_messages([
            ("system",
             "你是一位资深测试架构师。\n"
             "根据【产品文档】、【测试分析】和【接口定义】，生成结构化的测试点列表。\n\n"
             "### 输出 JSON 字段要求（严格遵循）\n"
             "输出格式：\n"
             "- project_name: 字符串\n"
             "- summary: 字符串\n"
             "- test_points: 数组，每个元素含 module, scenario, description, priority (P0/P1/P2/P3), test_type (功能/边界/异常/兼容), related_requirement (可选), risk (true/false)\n"
             "- risk_areas: 字符串数组，每个元素为一个风险点名称\n"
             "每个 test_point 都必须包含以上所有字段，禁止使用 id 字段。\n"
             "禁止输出 Markdown、禁止解释文字。"
            ),
            ("human",
             "### 测试分析（供参考）:\n{test_point_analysis}\n\n"
             "### 用户需求\n{user_context}\n\n"
             "### 产品文档片段\n{product_docs}\n\n"
             "### 关联模块产品文档\n{related_docs}\n\n"
             "### 接口定义\n{api_definitions}\n\n"
             "请根据以上信息，生成结构化的测试点列表："
            )
        ])

    def confirm_user_intent(self) -> ChatPromptTemplate:
        """Phase C 节点1：根据用户输入匹配候选模块名。"""
        return ChatPromptTemplate.from_messages([
        ("system",
         "你是一个智能模块匹配助手。根据用户的自然语言描述，从模块列表中找出最相关的模块。\n\n"
         "### 匹配规则\n"
         "1. **语义匹配优先**：用户可能用不同措辞描述同一个功能，你需要理解语义。\n"
         "   例如用户说「下单功能」→ 可能对应「销售订单管理」「购物车服务」等。\n"
         "2. **最多 3 个**：返回你认为最可能的前 1-3 个模块，按相关性从高到低排列。\n"
         "3. **宁缺毋滥**：如果都不匹配，返回空列表 []，confidence 设为 low。\n"
         "4. **confidence 标准**：\n"
         "   - high：用户描述与某个模块高度吻合，无需怀疑\n"
         "   - medium：有候选但存在不确定性\n"
         "   - low：无法确定匹配，建议用户重新描述\n"
         "5. **只输出 JSON**：禁止任何解释文字、禁止 Markdown。\n"
         '6. **输出格式**：{{"matched_modules": ["模块名1", "模块名2"], "confidence": "high"}}'
        ),
        ("human",
         "用户输入: {user_input}\n\n"
         "可用模块列表:\n{module_list}\n\n"
         "请匹配最相关的模块："
        )
    ])

    def generate_py_class_node(self) -> ChatPromptTemplate:
        """
        生成单个 Python 测试类 — V2 fixture + parametrize 结构（供外层循环组装）。
        """
        return ChatPromptTemplate.from_messages([
        ("system",
         "你是一个资深测试开发工程师。\n"
         "根据【模块数据】生成一个 Python 测试类。\n\n"
         "### 类模板（V2 fixture + parametrize）\n"
         "```python\n"
         "@pytest.fixture(scope=\"class\")\n"
         "def setup_<class_slug>():\n"
         "    read = ReadYamlData()\n"
         "    read.write_yaml_data({...})\n"
         "    base = RequestsBase()\n"
         "    base.specification_yaml(get_testcase_yaml(\n"
         "        './testcase/<feature_en>/setup_data/setup_<class_slug>.yaml'))\n"
         "    yield\n"
         "    base.specification_yaml(get_testcase_yaml(\n"
         "        './testcase/<feature_en>/setup_data/teardown_<class_slug>.yaml'))\n"
         "\n"
         "@allure.story('<story>')\n"
         "@pytest.mark.danyuan\n"
         "@pytest.mark.usefixtures(\"setup_<class_slug>\")\n"
         "class Test<story_en>:\n"
         "    @allure.title('<title>')\n"
         "    @pytest.mark.order(1)\n"
         "    @pytest.mark.parametrize('params', get_testcase_yaml(\n"
         "        './testcase/<feature_en>/<func1_en>/step1.yaml'))\n"
         "    def <func1_en>(self, params):\n"
         "        RequestsBase().specification_yaml(params)\n"
         "```\n\n"
         "### 生成规则\n"
         "1. **fixture 生成**：从 Sheet2 共享前置去重后生成 setup_<class_slug> fixture\n"
         "   - yield 前 = setup（创建资源），yield 后 = teardown（清理资源）\n"
         "   - 无共享前置的 class → fixture 只写 pass + yield\n"
         "2. **parametrize 生成**：每个 function 用 @pytest.mark.parametrize 加载 step YAML\n"
         "3. **命名映射**：\n"
         "   - <class_slug> = story_en 小写下划线（如 facility_mgmt）\n"
         "   - <feature_en> / <story_en> / <func1_en> / <title> 由翻译步骤提供\n"
         "4. **order 编号**：同 class 内 function 按 @pytest.mark.order(N) 递增\n"
         "5. **YAML 路径**: ./testcase/<feature_en>/<func_en>/step1.yaml\n\n"
         "### 输出格式\n"
         "输出 JSON 对象，class_code 字段包含完整的类定义代码。\n\n"
         "### 注意\n"
         "- 不生成 import（外层已处理）\n"
         "- 输出字段 class_code 仅包含 fixture + class 定义\n"
        ),
        ("human",
         "### 模块数据:\n{module_data}\n\n"
         "### feature_en:\n{feature_en}\n\n"
         "请生成该测试类的 Python 代码："
        )
    ])

    def generate_dependency_map(self):
        """
        Phase B-2: 生成 dependency_map.json（thinking 节点用）
        返回带 format_messages(**kwargs) 接口的对象。
        """
        from prompts.extraction_prompts import generate_dependency_map_prompt
        return generate_dependency_map_prompt()

