from langchain_core.prompts import ChatPromptTemplate

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
        根据 JSON Schema 生成结构化测试数据
        """
        return ChatPromptTemplate.from_messages([
        ("system",
         "你是资深测试数据构造专家。\n"
         "根据【接口定义】与【用例逻辑】，严格按下方 JSON Schema 生成测试数据（外层为对象，内含 data 数组）。\n\n"
         "### JSON Schema\n"
         "{json_schema}\n\n"
         "### 映射铁律\n"
         "1. 数据优先级：用例指定值 > 接口示例值 > 智能模拟（数字填0/1，字符串加test_，布尔false）。\n"
         "2. 禁止捏造字段，仅使用接口定义中的字段；类型与枚举必须严格匹配。\n"
         "3. **数据传递**（三个机制互不替代）：\n"
         "   - `extract`：从接口响应提取数据到 extract.yaml。语法：key: \"$.jsonpath\" 或 key: \"正则\"\n"
         "   - `input_extract`：从本用例的请求参数提取数据到 extract.yaml。语法：key: \"$.json.字段名\"\n"
         "   - 数据引用（在下游用例的 json 字段中引用 extract.yaml 的数据）：\n"
         "     - `${{get_extract_data(key)}}`：取指定 key 的第 0 个值（默认），如 `${{get_extract_data(token)}}`\n"
         "     - `${{get_extract_data(key, randoms=0)}}`：从随机列表取第 0 个，如 `${{get_extract_data(plates, randoms=0)}}`\n"
         "     - `${{get_extract_data(key, sec_node_name)}}`：指定从某接口节点的响应中取值，如 `${{get_extract_data(token, login)}}`\n"
         "4. 断言规则：每个用例必须配置断言不可为空。断言字段**必须从接口的 `returns` 中实际存在的字段中选择**，不得捏造。增删改用 eq/contains 校验返回的标识字段（如 success、code），查询用 eq/contains 校验结果数据。\n"
         "5. **数据工厂方法**（在 json 字段值中使用 `${{方法名}}`，运行时自动替换）：\n"
         "{data_factory_methods}\n\n"
         "### 输出要求\n"
         "直接输出一个 JSON 对象，该对象必须包含一个名为 `data` 的键，其值为符合上述 Schema 的数组。\n"
         "仅输出有实际数据的字段，可选字段为空时不要输出，避免出现空对象或空数组。\n"
         "禁止 Markdown、禁止解释文字。"
        ),
        ("human",
         "### 接口定义\n{all_apis_info}\n\n"
         "### 用例逻辑\n{test_case_logic}\n\n"
         "### 用户意图\n{user_context}\n\n"
         "请输出 JSON："
        )
    ])

    @staticmethod
    def get_data_schema() -> str:
        """返回测试数据的 JSON Schema 描述"""
        return """{
  "type": "object",
  "properties": {
    "data": {
      "type": "array",
      "items": {
        "type": "object",
        "properties": {
          "baseInfo": {
            "type": "object",
            "properties": {
              "api_name": {"type": "string"},
              "url": {"type": "string"},
              "method": {"type": "string", "enum": ["get", "post", "put", "delete", "patch"]},
              "header": {
                "type": "object",
                "properties": {
                  "Content-Type": {"const": "application/json;charset=UTF-8"}
                },
                "additionalProperties": true
              },
              "cookies": {"type": "object", "additionalProperties": {"type": "string"}}
            },
            "required": ["api_name", "url", "method", "header"]
          },
          "testCase": {
            "type": "array",
            "items": {
              "type": "object",
              "properties": {
                "case_name": {"type": "string"},
                "json": {"type": "object"},
                "data": {"type": "object"},
                "params": {"type": "object"},
                "validation": {
                  "type": "array",
                  "items": {
                    "oneOf": [
                      {"type": "object", "properties": {"eq": {"type": "object"}}, "required": ["eq"]},
                      {"type": "object", "properties": {"ne": {"type": "object"}}, "required": ["ne"]},
                      {"type": "object", "properties": {"contains": {"type": "object"}}, "required": ["contains"]},
                      {"type": "object", "properties": {"db": {"type": "object"}}, "required": ["db"]}
                    ]
                  }
                },
                "extract_list": {"type": "object", "additionalProperties": {"type": "string"}},
                "extract": {"type": "object", "additionalProperties": {"type": "string"}},
                "input_extract": {"type": "object", "additionalProperties": {"type": "string"}}
              },
              "required": ["case_name", "json", "validation"]
            }
          }
        },
        "required": ["baseInfo", "testCase"]
      }
    }
  },
  "required": ["data"]
}"""

    def generate_excel_plan_node(self) -> ChatPromptTemplate:
        """
        生成 Excel 测试计划
        """
        return ChatPromptTemplate.from_messages([
        ("system",
         "你是高级测试设计专家。根据【接口定义】和【用户意图】，生成 Excel 测试计划，严格按以下规则输出 JSON。\n\n"
         "### 核心概念\n"
         "Excel 的每一行 = 一个测试用例（= 一个 def 方法）。\n"
         "多个相同场景的用例（相同的 allure_story + allure_feature + fixture_level）合并在同一个 Class 中。\n"
         "一个测试用例包含多个 API 调用步骤。\n\n"
         "例如：\n"
         "  行1: test_CarEntry_001  — 临停车辆入场（1步骤，属于「60分免费场景」）\n"
         "  行2: test_CarExit_002   — 临停59分59秒出场（1步骤，属于「60分免费场景」）\n"
         "  → 合并到 class TestParkingFree（story=「60分免费场景」）\n\n"
         "### 设计规则\n"
         "1. **模块划分**：\n"
         "   - `module_name` 格式 `Test{{场景名}}`（如 TestParkingFree）\n"
         "   - **相同 `allure_story + allure_feature + fixture_level` 的行，`module_name` 填入相同的值**\n"
         "   - 不要加 3 位数字序号，编号放在 `case_name` 上\n"
         "2. **命名规范**：\n"
         "   - `case_name`：`test_xxx` 格式，用序号区分，如 `test_CarEntry_001`、`test_CarExit_002`\n"
         "   - `project_name` 取自用户意图（如 VehicleAccess）\n"
         "   - `test_data_yaml`：**单文件**，格式 `{{case_name}}.yaml`\n"
         "     例如：testCarEntry_001.yaml\n"
         "3. **步骤描述**：\n"
         "   - `steps` 为数组，每个元素一步，简明描述各步骤的 API 调用意图\n"
         "   - 如果存在前置条件（如清空数据），写入 `steps[0]`，格式 `'前置清空:xxx'`\n"
         "4. **标签填写**：\n"
         "   - `fixture_level`：逗号分隔，如 `smoke,danyuan`\n"
         "   - Allure 标签（`allure_epic`/`feature`/`story`）按层级描述业务\n"
         "5. **启用字段**：`enabled` 统一填 `Y`\n"
         "6. **完整性**：所有必填字段不得为空\n\n"
         "### 输出格式\n"
         "必须输出 JSON 对象：\n"
         "- `rows`：ExcelRow 对象数组（每条用例一个元素）\n"
         "- `file_name`：固定为 `test_plan.xlsx`\n"
         "示例：{{\"rows\": [{{\"project_name\": \"VehicleAccess\", ...}}], \"file_name\": \"test_plan.xlsx\"}}\n"
         "禁止输出纯数组，禁止解释或 Markdown。"
        ),
        ("human",
         "### 接口定义列表:\n{all_apis_info}\n\n"
         "### 用户测试意图:\n{user_context}\n\n"
         "请根据以上信息，设计完整的测试计划："
        )
    ])

    def analyze_test_points(self) -> ChatPromptTemplate:
        """
        分析测试点（thinking 节点用）
        """
        return ChatPromptTemplate.from_messages([
        ("system",
         "你是一位资深测试架构师。\n"
         "根据【产品文档】和【接口定义】，分析测试点。\n\n"
         "### 分析要求\n"
         "1. **覆盖度**：覆盖产品文档中描述的所有功能模块和业务场景。\n"
         "2. **关联追溯**：注意产品文档中提到的跨模块依赖关系，涉及其他模块的功能也要纳入测试范围。\n"
         "3. **分层**：\n"
         "   - P0：核心业务流程（Happy Path）\n"
         "   - P1：重要功能分支\n"
         "   - P2：边界条件和异常场景\n"
         "   - P3：兼容性和体验类\n"
         "4. **测试类型**：明确标注每个测试点的类型（功能/边界/异常/兼容）。\n"
         "5. **风险标注**：识别业务复杂、依赖多、改动频繁的区域作为风险点。\n\n"
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
         "### 用户需求\n{user_context}\n\n"
         "### 产品文档片段\n{product_docs}\n\n"
         "### 关联模块产品文档\n{related_docs}\n\n"
         "### 接口定义\n{api_definitions}\n\n"
         "请根据以上信息，分析测试点："
        )
    ])

    def generate_py_class_node(self) -> ChatPromptTemplate:
        """
        生成单个 Python 测试类（供外层循环组装）
        """
        return ChatPromptTemplate.from_messages([
        ("system",
         "你是一个资深测试开发工程师。\n"
         "根据【模块数据】生成一个 Python 测试类。\n\n"
         "### 类模板\n"
         "```python\n"
         "@allure.feature('<feature>')\n"
         "@allure.story('<story>')\n"
         "@pytest.mark.<fixture_level>\n"
         "class <ModuleName>:\n"
         "    @allure.title('<用例标题>')\n"
         "    def test_xxx(self):\n"
         "        RequestsBase().run_blocks('./testcase/<项目名>/{module_subdir}/<yaml文件名>')\n"
         "```\n\n"
         "### 生成规则\n"
         "1. **class 装饰器**：\n"
         "   - `@allure.feature('<feature>')` → 从数据取 `allure_feature`\n"
         "   - `@allure.story('<story>')` → 从数据取 `allure_story`\n"
         "   - `@pytest.mark.<fixture_level>` → 从数据取 `fixture_level`（多个用逗号隔开则生成多个）\n"
         "2. **方法装饰器**：\n"
         "   - `@allure.title('<标题>')` → 使用用例标题\n"
         "3. **方法体**：方法名用 `case_name`，调用 `RequestsBase().run_blocks('./testcase/<项目名>/{module_subdir}/<yaml文件名>')`\n"
         "   - `test_data_yaml` 来自数据，即为 yaml 文件名\n"
         "4. **是否启用=N**：该用例方法生成 `pass`，整个类全 N 则 class 生成 `pass`\n\n"
         "### 输出格式\n"
         "输出 JSON 对象，`class_code` 字段包含完整的类定义代码。\n\n"
         "### 命名规范（必须遵守）\n"
         "- 文件: `test_*.py`（外层已处理）\n"
         "- class: `Test*` 格式，直接用数据中的 `module_name`\n"
         "- 方法: `test_*` 格式，直接用数据中的 `case_name`\n"
         "- YAML 路径: `./testcase/<项目名>/{module_subdir}/<yaml文件名>`\n"
         "  - `{module_subdir}` 和 `test_data_yaml` 由数据提供\n\n"
         "### 注意\n"
         "- 不生成 import 和 @allure.epic（外层已处理）\n"
         "- YAML 路径: `./testcase/<项目名>/{module_subdir}/<yaml文件名>`\n"
         "- 输出字段 `class_code` 仅包含类定义本身\n"
        ),
        ("human",
         "### 模块数据:\n{module_data}\n\n"
         "### 项目名称:\n{project_name}\n\n"
         "### 模块子目录:\n{module_subdir}\n\n"
         "### 任务\n"
         "请生成该测试类的 Python 代码："
        )
    ])

