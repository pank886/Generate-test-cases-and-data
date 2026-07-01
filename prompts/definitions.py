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
         "2. **结构化**：严格按照 `ApiDefinition` 的字段要求提取（name, url, method, description, parameters）。\n"
         "3. **准确性**：\n"
         "   - `method` 必须是大写的 GET, POST, PUT, DELETE 等。\n"
         "   - `url` 必须是完整的路径（如果有域名请保留）。\n"
         "   - `parameters` 提取关键的请求参数结构，如果文档未提及可留空或填 {{}}。\n"
         "4. **数据清洗（重要）**：提取 `description` 时，**必须去除所有的换行符**，将其合并为一行文本，使用空格或标点分隔。\n"
         "5. **输出格式**：必须输出一个 JSON **对象**，对象中包含 `apis` 键，值为接口列表。\n"
         '   ✅ 正确格式: {{"apis": [{{"name": "接口名", "url": "路径", "method": "POST", "description": "描述", "parameters": {{}}}}]}}\n'
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
         "   - `${{get_extract_data(key, n)}}`：在下游用例的 json 字段中引用 extract.yaml 的数据。\n"
         "4. 断言规则：每个用例必须配置断言不可为空；增删改用 eq/contains 校验返回的标识字段，查询用 eq/contains 校验结果数据。\n\n"
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
         "Excel 的每一行 = 一个完整的测试用例（= 一个测试场景 = 一个 Class）。\n"
         "一个测试场景包含多个 API 调用步骤，对应 Class 中的多个方法。\n\n"
         "例如：\n"
         "  行1: TestVehicleAccess_005  — 临停车辆入场→出场失败→查费→出场（1场景4步骤）\n"
         "  行2: TestVehicleAccess_001  — 添加包月车→入场→出场→删除包月车（1场景4步骤）\n\n"
         "### 设计规则\n"
         "1. **模块划分**：\n"
         "   - `module_name` 格式 `Test{{项目名}}_{{NNN}}`（如 TestVehicleAccess_005）\n"
         "   - NNN 为 3 位数字序号（001, 002...），每个 module_name 唯一对应一个 Class\n"
         "2. **命名规范**：\n"
         "   - `case_name`：`test_xxx` 格式，如 `test_VehicleAccess_005`\n"
         "   - `project_name` 取自用户意图（如 VehicleAccess）\n"
         "   - `test_data_yaml`：该场景需要的所有 YAML 文件名，**多个用逗号分隔**\n"
         "     例如：carIn_005.yaml,carOutFalse_005.yaml,carPay_005.yaml,carQuery_005.yaml\n"
         "3. **步骤描述**：\n"
         "   - `steps` 用分号 `;` 分隔各步骤，简明描述每个 API 调用意图\n"
         "     例如：随机车辆入场; 出场失败; 查费; 出场\n"
         "   - `precondition` 填写场景级前置条件（无则填「无」）\n"
         "4. **标签填写**：\n"
         "   - `fixture_level`：逗号分隔，如 `smoke,danyuan`\n"
         "   - Allure 标签（`allure_epic`/`feature`/`story`）按层级描述业务\n"
         "   - `allure_title` 填写场景标题\n"
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
         "    @pytest.mark.order(n)\n"
         "    @pytest.mark.parametrize('params', get_testcase_yaml('./testcase/<项目名>/{module_subdir}/<yaml文件名>'))\n"
         "    def test_xxx(self, params):\n"
         "        \"\"\"前置条件: xxx\n"
         "        执行步骤: xxx\"\"\"\n"
         "        RequestsBase().specification_yaml(params)\n"
         "```\n\n"
         "### 生成规则\n"
         "1. **class 装饰器**：\n"
         "   - `@allure.feature('<feature>')` → 从数据取 `allure_feature`\n"
         "   - `@allure.story('<story>')` → 从数据取 `allure_story`\n"
         "   - `@pytest.mark.<fixture_level>` → 从数据取 `fixture_level`（多个用逗号隔开则生成多个）\n"
         "2. **方法装饰器**：\n"
         "   - `@allure.title('<标题>')` → `allure_title`\n"
         "   - `@pytest.mark.order(n)` → 用数据中显式标注的 `order=N` 值\n"
         "   - `@pytest.mark.parametrize('params', get_testcase_yaml('./testcase/<项目名>/{module_subdir}/<yaml文件名>'))`\n"
         "3. **方法体**：方法名用 `case_name`，docstring 写前置条件+步骤，内容 `RequestsBase().specification_yaml(params)`\n"
         "4. **是否启用=N**：该用例方法生成 `pass`，整个类全 N 则 class 生成 `pass`\n\n"
         "### 输出格式\n"
         "输出 JSON 对象，`class_code` 字段包含完整的类定义代码。\n\n"
         "### 命名规范（必须遵守）\n"
         "- 文件: `test_*.py`（外层已处理）\n"
         "- class: `Test*` 格式，直接用数据中的 `module_name`\n"
         "- 方法: `test_*` 格式，直接用数据中的 `case_name`\n"
         "- YAML 所在子目录由参数 {module_subdir} 提供，必须拼入路径\n\n"
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

