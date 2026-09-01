# ⚠️ 2026-09-01 评审收尾：本文件为遗留注释容器。
#   - generate_excel_plan_thinking / confirm_user_intent 已移入 prompts/extraction_prompts.py
#     （展平为模块级函数 generate_excel_plan_thinking_prompt / confirm_user_intent_prompt）
#   - generate_excel_plan_node / analyze_test_points_raw（旧分段式生成）已注释于下，保留原文；
#     全量运行确认无影响后删除本文件。
# ============================================================

# from langchain_core.prompts import ChatPromptTemplate

# class PromptFactory:

#     def generate_excel_plan_node(self) -> ChatPromptTemplate:
#         """
#         生成 Excel 测试计划 V2（双 Sheet，format 节点用，thinking off + json_mode）。
#         """
#         return ChatPromptTemplate.from_messages([
#         ("system",
#          "你是数据转换专家。根据下方【共享前置】和【测试用例描述】，"
#          "照此填入 shared_preconditions 和 test_cases，输出严格 JSON。\n\n"
#          "### 输出 JSON 格式\n"
#          "必须输出以下结构的 JSON 对象：\n\n"
#          "  {{\n"
#          '    "shared_preconditions": [\n'
#          '      {{"id": "PRE-001", "name": "已创建测试跑步机",\n'
#          '        "steps": "1.调用新增设施接口\\n2.校验创建成功",\n'
#          '        "expected": "设施列表中出现测试跑步机"}}\n'
#          '    ],\n'
#          '    "test_cases": [\n'
#          '      {{"id": "TC-001",\n'
#          '        "story": "设施添加",\n'
#          '        "title": "设施管理-新增设施-正向",\n'
#          '        "preconditions": [],\n'
#          '        "steps": "1.调用新增设施接口\\n2.查询详情",\n'
#          '        "expected": "1.创建成功\\n2.信息一致",\n'
#          '        "mutates_data": true,\n'
#          '        "is_negative_test": false}},\n'
#          '      {{"id": "TC-002",\n'
#          '        "story": "设施修改",\n'
#          '        "title": "设施管理-修改设施-正向",\n'
#          '        "preconditions": ["PRE-001"],\n'
#          '        "steps": "1.调用修改接口\\n2.查询详情",\n'
#          '        "expected": "1.修改成功\\n2.信息已更新",\n'
#          '        "mutates_data": true,\n'
#          '        "is_negative_test": false}}\n'
#          '    ],\n'
#          '    "file_name": "test_plan.xlsx"\n'
#          '  }}\n\n'
#          "### 字段说明\n"
#          "**shared_preconditions**：id/name/steps/expected，直接复制下方【共享前置】中的内容\n"
#          "**test_cases**：\n"
#          "- id: TC 编号\n"
#          "- story: 子模块名（对应 @allure.story），从文档提取的模块名，如「设施管理」\n"
#          "- title: 用例名称（对应 @allure.title），如「设施管理-新增设施-正向」\n"
#          "- preconditions: PRE 编号数组，无则为 []\n"
#          "- steps/expected: 文本，\\n 分隔，条数一致\n"
#          "- mutates_data: 分析【执行步骤】，含增/删/改/状态变更/重置/清理 → true；仅查询 → false\n"
#          "- is_negative_test: 分析【预期结果】，含失败/报错/异常/不存在/无权/冲突/重复 → true；否则 false\n"
#          "- file_name: 固定 test_plan.xlsx\n\n"
#          "### 字段硬约束（违反即校验失败）\n"
#          "- 字段名必须是 story，禁止写 sub_module / module / feature_name 等变体\n"
#          "- steps 和 expected 必须是**字符串**（\\n 分隔），禁止输出数组/列表\n"
#          "- steps 和 expected 条数必须一致\n\n"
#          "### 规则\n"
#          "1. 每个 PRE-xxx → 一个 shared_preconditions 对象，直接复制不修改\n"
#          "2. 每个 TC-xxx → 一个 test_cases 对象\n"
#          "3. preconditions 的 PRE 必须存在于 shared_preconditions\n"
#          "4. 禁止 Markdown、禁止解释，只输出 JSON"
#         ),
#         ("human",
#          "{gen_warning}"
#          "### 测试场景分析（参考，理解模块间关系和数据流）:\n{analysis_section}\n\n"
#          "### 共享前置（照此填入 shared_preconditions，不可遗漏任何一条）:\n{shared_pre_section}\n\n"
#          "### 测试用例描述（照此填入 test_cases）:\n{cases_section}\n\n"
#          "### 模块树:\n{module_tree}\n\n"
#          "### 接口定义列表:\n{all_apis_info}\n\n"
#          "### 用户测试意图:\n{user_context}\n\n"
#          "输出 JSON："
#         )
#     ])

#     def analyze_test_points_raw(self) -> ChatPromptTemplate:
#         """
#         Phase B — 测试点原始分析（thinking 节点用）：输出自由文本分析报告。
#         """
#         return ChatPromptTemplate.from_messages([
#             ("system",
#              "你是一位资深测试架构师，专注于**接口自动化测试用例设计**。\n\n"
#              "根据【产品文档】和【接口定义】，设计详细的测试用例，按以下**固定模板**输出。\n\n"
#              "### 输出模板（必须严格遵守，三个段落缺一不可）\n\n"
#              "## 测试场景分析\n"
#              "按模块逐一分析：模块功能概述、涉及接口、测试策略（正向/反向/边界），"
#              "以及该模块与其他模块的数据依赖关系。\n\n"
#              "## 共享前置\n"
#              "列出所有模块共用的数据准备步骤。每个前置使用**全局唯一编号**（PRE-001 开始递增）。\n"
#              "- PRE-xxx: 前置名称（模块：所属模块名）\n"
#              "    步骤: 1.具体操作步骤1\\n2.具体操作步骤2\n"
#              "    预期: 操作完成后的预期状态\n\n"
#              "## 测试用例\n"
#              "每个用例一个条目，使用**全局唯一编号**（TC-001 开始递增）。\n"
#              "- TC-xxx: 用例标题\n"
#              "    子模块: 从文档中提取的模块名（如「设施管理」，不是功能点「设施添加」）\n"
#              "    前置: PRE-xxx 或 无\n"
#              "    步骤: 1.操作步骤1\\n2.操作步骤2\n"
#              "    预期: 1.[eq]预期结果1\\n2.[eq]预期结果2\n\n"
#              "### 🏆 黄金参考模板（唯一标准，严格模仿此格式）\n"
#              "<reference>\n"
#              "## 共享前置\n"
#              "- PRE-001: 已创建测试电表（模块：设备管理-电表管理）\n"
#              "    步骤: 1.调用新增电表接口，电表分类选择\"单一费率电表\"，填写必填字段\\n2.校验创建成功，获取电表 code\n"
#              "    预期: 接口返回成功，code 不为空\n"
#              "\n"
#              "## 测试用例\n"
#              "- TC-001: 电表管理-新增单一费率电表-正向\n"
#              "    子模块: 设备管理-电表管理\n"
#              "    前置: 无\n"
#              "    步骤: 1.调用新增电表接口，名称\"测试电表A\"\\n2.调用查询详情接口查看电表信息\\n3.调用分页查询接口搜索电表\n"
#              "    预期: 1.[eq]接口返回成功，生成ID\\n2.[eq]电表信息与新增时一致\\n3.[contains]分页列表包含\"测试电表A\"\n"
#              "</reference>\n\n"
#              "### 断言关键词（预期结果中必须使用，下游按关键词生成断言数据）\n"
#              "- [eq] 相等断言：验证返回值与预期完全相等，用于增删改返回的标识字段\n"
#              "- [contains] 包含断言：验证返回值包含预期内容，用于查询结果校验\n"
#              "- [ne] 不相等断言：验证返回值不等于预期值，用于确认删除/变更后旧数据不存在\n"
#              "- [db] 数据库断言：验证数据库中是否存在对应记录，用于数据持久化校验\n\n"
#              "### ⚠️ 强制规则（违反将导致用例被丢弃）\n"
#              "- **共享前置段落绝对不能为空！** 每个测试计划至少有一条共享前置。"
#              "常见的共享前置：创建测试数据（测试电表、测试住户、测试企业）、"
#              "创建配置（结算配置、计费方案、公摊配置）。"
#              "参照 <reference> 中 PRE-001 的格式，至少输出一条。\n"
#              "- PRE-xxx 从 PRE-001 开始，全局唯一，递增。TC-xxx 同理\n"
#              "- 前置字段直接引用 PRE 编号（如 PRE-001, PRE-002），禁止写「执行共享前置X」\n"
#              "- 前置引用只能是 PRE-xxx，禁止写 TC-xxx 或其他格式\n"
#              "- **严禁步骤与预期不对齐！** 严格遵守上方 <reference> 的格式：步骤有 N 条 → 预期必须有 N 条。"
#              "若步骤只有 1 条，预期绝对不能写 3 条——必须拆分步骤或合并预期。每个用例写完立即自查\n\n"
#              "### 用例设计规范（必须严格遵守）\n\n"
#              "**模块与功能点识别**：\n"
#              "- 必须先从产品文档中识别出所有子模块和子模块下的嵌套模块\n"
#              "- 每个模块下必须识别出所有功能点（新增、查询、修改、删除、导出、审批等）\n"
#              "- 每个功能点至少对应 3-5 条测试用例，确保充分覆盖\n"
#              "- 当某个功能点文档描述不详细时，至少编写 3 条用例（正常+边界+异常）\n\n"
#              "**正向测试**：\n"
#              "- 每个操作（新增/修改/删除/查询）至少一个正向用例\n"
#              "- 审批类操作（提交审批、审批通过、审批驳回）各至少一个正向用例\n\n"
#              "**反向逻辑**：\n"
#              "- 业务取消类（取消预约、取消订单、取消审批等）需独立用例\n"
#              "- 逆向场景至少覆盖 5 类（非法输入、权限不足、流程跳转异常、数据冲突、状态不匹配）\n"
#              "- 每条逆向用例必须明确异常触发条件和预期报错信息\n\n"
#              "**字段校验**：\n"
#              "- 必填字段缺失、格式错误、超长输入各至少一个异常用例\n"
#              "- 特殊字符、SQL 注入类字段校验\n\n"
#              "**边界值**：\n"
#              "- 数值字段：最小值-1、最小值、最大值、最大值+1\n"
#              "- 时间字段：临界时刻（如免费时段最后一秒、过期前一秒）\n"
#              "- 空值、零值、负值\n\n"
#              "**异常场景**：\n"
#              "- 权限不足、数据冲突、并发操作、依赖接口不可用\n"
#              "- 网络超时模拟\n\n"
#              "**跨模块联动**：利用关联模块文档设计端到端场景\n\n"
#              "**智能发现**：基于业务逻辑理解，主动发现规则之外可测试的关键点\n\n"
#              "**用例质量要求**：\n"
#              "- 无冗余、无重复、无遗漏，逻辑严谨\n"
#              "- 完整覆盖等价类划分、边界值分析、场景法、错误推测法\n"
#              "- 逆向用例数量不低于正向用例的 1/3\n\n"
#              "请输出**自由文本分析报告**，不要输出 JSON。"
#             ),
#             ("human",
#              "### 用户需求\n{user_context}\n\n"
#              "### 模块场景与接口分析（已预分析，权威数据源）\n{module_analysis}\n\n"
#              "### 产品文档片段\n{product_docs}\n\n"
#              "### 关联模块产品文档\n{related_docs}\n\n"
#              "### 接口定义\n{api_definitions}\n\n"
#              "请基于以上信息设计测试用例。"
#              "如果「模块场景与接口分析」不为空，说明场景和接口映射已预分析完成，"
#              "请直接据此生成测试用例，不要重复分析场景。"
#              "如果为空，请按产品文档和接口定义自行分析。"
#             )
#         ])


