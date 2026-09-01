"""全阶段 Prompt 模板（文件内按 Phase 分层）。

Phase A — 提取/分析:
    product_doc_extract_prompt / glossary_extract_prompt / api_def_extract_prompt /
    batch_chunk_summary_prompt / analyze_product_scenarios_prompt /
    analyze_axure_ui_flow_prompt / analyze_api_mapping_prompt
Phase B — Excel 计划生成/修复:
    generate_excel_plan_thinking_prompt / repair_excel_plan_prompt
Phase C — 意图识别 / YAML / 翻译 / 依赖映射:
    confirm_user_intent_prompt / SETUP_CAPTURE_RULE / YAML_ANALYSIS_GUIDE /
    generate_yaml_data_single_prompt / translate_to_en_prompt /
    generate_dependency_map_prompt / repair_dependency_map_prompt
"""
from langchain_core.prompts import ChatPromptTemplate


# ---- Phase A: 产品文档提取 ----
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


# ---- Phase A: 术语表提取 ----
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


# ---- Phase A: 接口定义提取 ----
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


# ====================================================================
# Phase A: 批量 chunk 摘要（入库时生成 simple_summary）
# ====================================================================

# ---- Phase A: 批次摘要 ----
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

# ---- Phase A: 产品场景分析 ----
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


# ---- Phase A: Axure 界面流程分析 ----
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


# ---- Phase A: 接口映射分析 ----
def analyze_api_mapping_prompt() -> ChatPromptTemplate:
    """Step 3: 场景 + 逻辑关系 + API → 接口映射总结（thinking 模式，自由文本输出）。"""
    return ChatPromptTemplate.from_messages([
        ("system",
         "你是接口分析师。根据已知的测试场景和页面交互逻辑，分析接口定义与业务场景的映射关系。\n\n"
         "### 分析要求\n"
         "1. 将每个 API 接口映射到对应的业务场景和功能点\n"
         "2. 分析接口间的数据依赖关系（produces → consumes）\n"
         "3. 识别跨模块接口调用链\n"
         "4. 标注数据流向（哪个接口产出什么数据 → 哪个接口消费）\n"
         "5. 对每个写接口（POST/PUT/PATCH）的 body 字段，标注哪些是枚举/取值字段及其合法取值，"
         "格式为「字段名：枚举值1/枚举值2/...」，如 meterDeviceType：单相/双相/三相、"
         "accessMethod：网关接入/电表直连/平台对接、meterTypeCode：1/2/3；"
         "取值来源以接口定义 body 字段的 desc/备注为准，无明确枚举的不臆造\n\n"
         "### 输出\n"
         "自由文本分析报告，不需要 JSON 格式。\n"
         "结构建议：接口→场景映射 → 数据依赖链 → 跨模块调用链 → 关键约束（含枚举字段取值标注）。"),
        ("human",
         "### 模块名\n{module_name}\n\n"
         "### 测试场景总结（Step 1 输出）\n{scenario_analysis}\n\n"
         "### 页面交互逻辑（Step 2 输出）\n{ui_flow_analysis}\n\n"
         "### 接口定义\n{api_definitions}\n\n"
         "### 模块树\n{module_tree}\n\n"
         "### 跨模块关系\n{cross_module_relations}\n\n"
         "请分析接口与场景的映射关系：")
    ])


# ---- Phase B: Excel 计划生成（thinking+json 一步生成） ----
def generate_excel_plan_thinking_prompt() -> ChatPromptTemplate:
    """
    【新】thinking+json_mode 一步生成 Excel 计划。
    {json_schema} 由调用方注入 ExcelPlanV2.model_json_schema()。
    """
    return ChatPromptTemplate.from_messages([
        ("system",
         "你是一位资深测试架构师，专注于**接口自动化测试用例设计**。\n\n"
         "根据【模块场景分析】和【接口定义】，设计详细的测试用例。\n"
         "严格按下方 JSON Schema 输出，禁止 Markdown，只输出纯 JSON。\n\n"
         "### JSON Schema（必须严格遵循此结构）\n"
         "```json\n"
         "{json_schema}\n"
         "```\n\n"
         "### ⚠️ 逆向用例设计要求（优先级高，但需结合业务合理性灵活应用）\n\n"
         "**整体比例原则**：\n"
         "- 全局逆向用例数（is_negative_test=true）**应尽量不低于** 正向用例数（is_negative_test=false）的 1/3，以保障异常场景覆盖充分。\n"
         "- 若实际业务中某些模块/接口天然异常场景极少（如纯查询导出），允许适当降低比例，但需在 `story` 或 `title` 中体现合理性（如「导出-正常」）。\n\n"
         "**逆向用例设计重点（优先覆盖）**：\n"
         "A. 参数校验类 —— 优先对 **有必填参数** 或 **参数类型/格式要求严格** 的接口（尤其是写接口）：\n"
         "   · 必填字段缺失（每个主要必填字段可考虑一条）\n"
         "   · 参数类型错误 / 格式错误\n"
         "   · 超长输入 / 特殊字符 / SQL 注入 / XSS 注入\n"
         "B. 业务规则类 —— 优先对 **POST/PUT/DELETE** 等写接口：\n"
         "   · 重复创建（唯一约束冲突）\n"
         "   · 操作不存在的资源（如删除/修改不存在的 ID）\n"
         "   · 状态机违规（当前状态不允许该操作）\n"
         "C. 权限与边界类 —— 视接口敏感程度：\n"
         "   · 无权限访问 / 越权操作\n"
         "   · 数值越界（最小值−1、最大值+1）\n"
         "   · 空值 / null / 零值 / 负值\n"
         "   · 并发冲突（同时操作同一资源）\n\n"
         "**以上清单为指导性建议，请根据接口实际业务逻辑选择性应用，不必为每个接口生硬套用所有类别。**\n\n"
         "**逆向用例格式规范**：\n"
         "- title 末尾建议标注「-反向」或「-异常」，便于识别\n"
         "- expected 必须明确写出异常/报错/失败的具体内容，严禁写「操作成功」「返回正常」\n"
         "- is_negative_test 必须设为 true\n"
         "- steps 中必须描述触发异常的具体操作（如省略必填字段、传入错误类型值），禁止内嵌完整 JSON 请求体\n\n"
         "### 断言关键词（预期结果中必须使用）\n"
         "- [eq] 相等断言：验证返回值与预期完全相等\n"
         "- [contains] 包含断言：验证返回值包含预期内容\n"
         "- [ne] 不相等断言：确认删除/变更后旧数据不存在\n"
         "- [db] 数据库断言：**仅当下方「数据库表结构信息」非空时才允许使用**；"
         "表结构为空时禁止 [db] 断言（无法写正确 SQL），改用 [eq]/[contains]/[ne]\n\n"
         "### 共享前置引用规范（硬性要求，违反会整批校验失败）\n\n"
         "- 每个用例的 preconditions 字段**只能**引用 shared_preconditions 数组中实际存在的 id（形如 PRE-001）。\n"
         "- **禁止**在 preconditions 中写自由文本描述（如「已创建电表」）、TC 编号或其他非 PRE 编号内容。\n"
         "- 所有被引用的 PRE 编号必须在该输出 JSON 的 shared_preconditions 中**已定义**，禁止悬空引用（引用未定义的前置）。\n"
         "- 前置无引用时，preconditions 输出空数组 []，不要填任何占位内容。\n"
         "- shared_preconditions 的 id 建议按 PRE-001、PRE-002… 连续编号，name/steps/expected 保持完整。\n\n"
         "### 思维链设计规范（硬性要求：每个用例按 前置→执行→断言 三段式设计）\n\n"
         "- **前置（preconditions）**：先想清楚「要测该操作，必须先准备什么」——通过哪个接口（写 url）"
         "创建/初始化哪个实体。前置必须落在 shared_preconditions：每个 PRE 描述一条具体的准备操作"
         "（调用 {{method}} {{url}} 创建/初始化 {{实体}}），禁止用自由文本（如「已创建电表」）代替。\n"
         "- 写操作（新增/修改/删除/绑定/审批等）必须先有对应的创建/初始化前置；"
         "逆向用例若故意省去前置（如删除不存在的资源），该省去本身要在步骤/预期中体现。\n"
         "- **枚举/取值字段不写死字面量（硬性要求，只约束枚举/取值类字段）**：值来自接口定义 desc 的"
         "枚举/取值字段（如电表类型、接入方式、分类等）在前置（PRE）与步骤中只写字段名与取值来源，"
         "禁止写死具体值——取值来源以「接口映射分析」中的枚举标注为准"
         "（如「电表类型 meterDeviceType 取枚举（单相/双相/三相）」）；"
         "被跨用例引用的标识/编码字段（如 code/name/sceneCode）不受此限，可保留具体值；"
         "仅当需多个用例（>2）以不同取值区分时才允许写出具体枚举值。\n"
         "- **执行（steps）**：有序操作链，每行一步，体现接口依赖顺序（先查询确认 → 再操作 → 再验证）。\n"
         "  · 每行格式：调用 {{method}} {{url}}，做{{业务动作}}。步骤中的 url 必须能溯源到下方"
         "【模块场景与接口分析】或【接口定义】。\n"
         "  · 删除/修改/解绑等操作后，需经查询接口验证结果；跨模块用例体现接口间的调用链。\n"
         "  · **禁止在 steps 中内嵌完整 JSON 请求体**；具体参数值属 Phase C 数据层，"
         "这里只描述操作意图与关键触发条件（如省略必填字段、错误类型值）。\n"
         "- **断言（expected）**：每行对应一步，行数与 steps 一一对应；断言内容与操作语义对齐"
         "（创建成功/查询命中/删除成功/异常拦截等）。\n"
         "- **强令使用上下文**：充分利用下方【模块场景与接口分析】【接口定义】【关联模块】【用户需求】全部信息，"
         "禁止忽略给定分析、禁止凭空编造接口/步骤/接口关系。\n"
         "- **只使用提供的信息分析，禁止瞎编**：所有分析、步骤、前置、断言必须建立在下方提供的信息之上，"
         "禁止引入提供范围之外的知识、接口、字段或数据来凑用例；信息不足时如实说明，宁可少设计也不编造。\n\n"
         "### 用例设计规范\n\n"
         "**模块与功能点识别**：\n"
         "- 必须先识别所有子模块及其功能点（新增、查询、修改、删除、导出、审批等）\n"
         "- 每个功能点至少 3-5 条（正向+逆向合计），依据详细产品文档充分设计\n\n"
         "- **每个接口至少 1 条用例直接调用该接口（硬性要求）**\n"
         "- 导出/导入/模板/开关类**无参数接口**（url 含 export / importTemplate / template / autoOff / download 等，"
         "或 GET 且无任何必填参数）同样必须至少 1 条用例直接调用，"
         "如验证空文件导出、模板下载、开关状态查询等，**禁止仅因无参数而跳过**\n\n"
         "**正向测试**：每个操作至少一个正向用例；审批类操作各至少一个正向\n\n"
         "**字段校验**（作为逆向用例的组成部分）：必填缺失/格式错误/超长输入/特殊字符各至少 1 条\n"
         "**边界值**：数值最小值-1/最小值/最大值/最大值+1，时间临界时刻，空值零值负值\n"
         "**异常场景**：权限不足、数据冲突、并发、依赖不可用、网络超时\n"
         "**跨模块联动**：利用关联模块设计端到端场景\n\n"
         "**步骤/预期严格对齐（硬性要求，违反会整批校验失败）**：\n"
         "- steps 每行一个操作，expected 每行对应该操作的断言，**行数必须一一对应**（steps 2 行 → expected 2 行）\n"
         "- expected 每行以断言标签开头（如 `1.[eq]创建成功`、`2.[db]存在记录`），"
         "禁止把多步断言合并成一行（如 `返回成功；[db] 新增记录` 应拆为两行）\n\n"
         "**用例质量要求**：无冗余无重复无遗漏，逻辑严谨；覆盖等价类划分、边界值分析、场景法、错误推测法"
        ),
        ("human",
         "{gen_warning}"
         "### 模块场景与接口分析（已预分析，权威数据源）\n{module_analysis}\n\n"
         "### 接口定义\n{api_definitions}\n\n"
         "### 关联模块\n{related_docs}\n\n"
         "### 用户需求\n{user_context}\n\n"
         "### 数据库表结构信息（为空时禁止 [db] 断言）\n{db_schema}\n\n"
         "**⚠️ 输出前柔性自检（无需严格逐项核对，但需在思考中确认整体合理性）：**\n"
         "□ 逆向用例总数是否大致达到正向用例的 1/3？若明显不足，请思考是否有遗漏的异常场景。\n"
         "□ 写接口（POST/PUT/DELETE）是否都覆盖了必要的参数校验和业务规则异常？\n"
         "□ 对于纯查询/导出等接口，是否根据其参数复杂度适当补充了异常用例（如参数错误）？\n"
         "□ 每条逆向用例的 `expected` 是否明确了具体的报错/异常信息？\n"
         "□ 每条用例的 `preconditions` 是否只引用了 shared_preconditions 中实际存在的 PRE 编号？"
         "是否混入了自由文本或引用了未定义的前置编号？\n"
         "□ 每条用例的 steps 与 expected 行数是否一一对应？expected 是否每行以断言标签开头、未合并多步断言？\n"
         "□ 每条用例是否按 前置→执行→断言 三段式设计？写操作是否都准备了对应的创建/初始化前置？\n"
         "□ steps 是否每行 = 调用 {{method}} {{url}} 做{{业务动作}}、url 可溯源到接口定义/场景分析？是否误内嵌了 JSON 请求体？\n"
         "□ 前置/步骤中的枚举/取值字段是否未写死具体值、只写了字段名与取值来源（标识/编码字段除外）？\n"
         "□ 删除/修改后是否经查询接口验证？跨接口用例是否体现了接口调用链？\n"
         "□ 是否只使用提供的信息分析、未引入范围外知识/接口/数据瞎编？信息不足时是否如实说明而非凑数？\n\n"
         "如果「模块场景与接口分析」不为空，说明场景和接口映射已预分析完成，"
         "请直接据此生成测试用例，不要重复分析场景。"
         "如果为空，请按接口定义自行分析。"
         "请设计测试用例并输出 JSON。\n\n"
        )
    ])


# ---- Phase B: Excel 计划修复 ----
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
         "6. 枚举/取值字段（值来自接口定义 desc，如电表类型、接入方式、分类等）在前置（PRE-xxx）"
         "与步骤中禁止写死具体值，只写字段名与取值来源（如「电表类型 meterDeviceType 取合法枚举」）；"
         "被跨用例引用的标识/编码字段（如 code/name/sceneCode）不受此限，可保留具体值；"
         "仅当需多个用例（>2）以不同取值区分时才允许写出具体枚举值\n"
         "7. 禁止 Markdown，只输出 JSON"),
        ("human", "请输出修正后的测试用例 JSON：")
    ])


# ---- Phase C: 意图识别与模块匹配 ----
def confirm_user_intent_prompt() -> ChatPromptTemplate:
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


# ---- Phase C: 英文翻译 ----
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


# ======================================================================
# 两段式 prompt 已注释（2026-08-24）。生成路径收敛为单节点
# generate_yaml_data_single_prompt（schema 驱动、无手写示例）。原代码见 git 历史：
#   git show HEAD:prompts/extraction_prompts.py
# ======================================================================
# def analyze_yaml_data_prompt() -> ChatPromptTemplate:
#     """Phase C YAML 数据 — 第一阶段：thinking 自由分析。"""
#     return ChatPromptTemplate.from_messages([
#         ("system",
#          "你是资深测试数据构造专家。根据【接口定义】和【用例逻辑】，深度分析需要生成的测试数据。\n\n"
#          "请分析以下方面（自由文本，不要输出 JSON）：\n"
#          "1. **接口匹配**：每个步骤对应哪个接口（从接口定义中找匹配的 url/method）。"
#          "**分析中描述接口时，url 只写路径**（如 /payConfig/detail），不要写 ${{}} 或完整 URL，"
#          "动态参数通过 params 传递即可。\n"
#          "2. **请求参数**：每个接口需要哪些请求参数，参数值从哪来（用例指定 / 上游提取 / 模拟）\n"
#          "3. **数据传递**：哪些步骤的返回值需要 extract，供下游步骤引用（使用数据工厂清单中的提取函数）\n"
#          "4. **断言设计**：每个步骤应该断言什么字段（从接口 returns 中选择），期望值是什么\n"
#          "5. **工厂方法**：哪些参数值需要用工厂方法随机生成\n\n"
#          "### 可用数据工厂方法\n{data_factory_methods}\n\n"
#          "### 输出字段约束（json_mode 阶段会严格按以下 schema 输出，你的分析要覆盖这些字段）\n"
#          "- baseInfo: 仅含 api_name/url/method/header 四个字段。**header 必须存在**（GET 请求 header 为空字典，POST/PUT/PATCH 写 Content-Type: application/json）\n"
#          "- testCase: case_name/json|params|data/extract|input_extract/validation\n"
#          "- 请求参数位置按接口定义字段的 `location` 决定：`location=query` 的字段 → params（query string），"
#          "`location=body` 的字段 → json（JSON body）；接口定义未标 location 时按 HTTP 方法——"
#          "GET/DELETE → params，POST/PUT/PATCH → json\n"
#          "- **url 禁止动态占位符**——url 在框架中不经 replace_load() 解析，动态参数必须用 params 传递，url 保持静态路径\n"
#          "- **params/json/data 只能放在 testCase 内**，禁止放在 baseInfo 层级\n"
#          "- validation 支持 eq/contains/ne/db 四种断言（不等于是 ne 不是 neq）。**validation 不能为空数组**\n"
#          "- **db 断言禁止**：若「数据库表结构信息」为空，禁止生成 db 断言（无表结构无法写正确 SQL），改用 eq/contains/ne\n"
#          "- **导出/下载/模板接口**（URL 含 export/import/template/download/upload 或接口标注 is_export）：返回二进制流，"
#          "断言必须用 `contains: {{status_code: 200}}`，禁止 eq/ne 检查状态码\n"
#          "- **对 `status_code` 的断言必须用 `contains: {{status_code: X}}`，禁止 eq/ne**（不限于导出接口；导出接口维持 contains: {{status_code: 200}}）\n"
#          "- **`contains` 的值必须是字典对象**（`{{字段: 期望}}` 或 `{{$.JSONPath: 期望}}`），禁止裸字符串/标量\n"
#          "- extract 从接口返回值中提取数据（JSONPath），供下游步骤用 ${{get_extract_data(key)}} 引用。"
#          "input_extract 极少使用，不要把它当数据暂存。禁止填入 PRE 编号或固定字面量\n"
#          "- extract/validation 的 JSONPath 必须以 $. 开头（如 $.data.id）\n"
#          "- 动态占位符只能从上方数据工厂清单中选择并按 syntax 使用，禁止胡编函数或语法；"
#          "清单不支持的能力用合理固定字面量（如远期日期直接写 \"2029-12-31 10:00:00\"）\n"
#          "- 分析阶段就要为每个动态值判定：用哪个工厂函数，还是固定字面量"),
#         ("human",
#          "### 接口定义\n{api_definitions}\n\n"
#          "### 用例逻辑\n{test_case_logic}\n\n"
#          "### 用户意图\n{user_context}\n\n"
#          "### 数据库表结构信息（为空时禁止 db 断言）\n{db_schema}\n\n"
#          "请分析测试数据需求：")
#     ])


# def repair_yaml_data_prompt() -> ChatPromptTemplate:
#     """Phase C YAML 数据 — 修复轮思考：带上一轮错误输出与校验错误自查（thinking on）。
#
#     与 analyze_yaml_data_prompt 相同定位（自由文本分析），额外注入：
#       - 上一轮原始输出（有错）
#       - 本项校验错误明细
#       - 全批次错误模式统计（跨文件模式反馈）
#     输出接 format_yaml_data_prompt 结构化收敛。
#     """
#     return ChatPromptTemplate.from_messages([
#         ("system",
#          "你是资深测试数据构造专家。你上一轮生成的测试数据未通过校验，"
#          "请先分析错误原因，再给出修正后的完整数据方案（自由文本，不要输出 JSON）。\n\n"
#          "### 本轮全批次错误模式统计（其他文件也在犯的错，注意规避）\n"
#          "{error_pattern_summary}\n\n"
#          "### 可用数据工厂方法（动态占位符只能从此清单选择，严格按 syntax 填写）\n"
#          "{data_factory_methods}\n\n"
#          "### 修复要点\n"
#          "- 逐条对照【校验错误明细】定位问题字段，说明错在哪、应改成什么\n"
#          "- 动态值只能用数据工厂清单内的函数（语法见清单），禁止自创函数或语法"
#          "清单不支持的能力写合理固定字面量\n"
#          "- 无需提取时省略 extract/input_extract 字段，禁止 {{}} 占位与 null 值条目\n"
#          "- json/params/data 三选一：优先按接口定义字段的 `location` 确定——`location=query` → params，"
#          "`location=body` → json；未标 location 时按 HTTP 方法——GET/DELETE → params，POST/PUT/PATCH → json\n"
#          "- **若「数据库表结构信息」为空，禁止 db 断言**，改用 eq/contains/ne\n"
#          "- **导出/下载/模板接口**（URL 含 export/import/template/download/upload）：断言用 contains: {{status_code: 200}}，禁止 eq/ne 检查状态码\n"
#          "- **对 `status_code` 的断言必须用 `contains: {{status_code: X}}`，禁止 eq/ne**（不限于导出接口）\n"
#          "- **`contains` 的值必须是字典对象**（`{{字段: 期望}}` 或 `{{$.JSONPath: 期望}}`），禁止裸字符串\n"
#          "- 修正时保持原有正确部分不动，只改错误部分"),
#         ("human",
#          "{post_check_issues}"
#          "### 接口定义\n{api_definitions}\n\n"
#          "### 用例逻辑\n{test_case_logic}\n\n"
#          "### 用户意图\n{user_context}\n\n"
#          "### 数据库表结构信息（为空时禁止 db 断言）\n{db_schema}\n\n"
#          "### 你上一轮的输出（有错）\n{prior_output}\n\n"
#          "### 校验错误明细\n{error_detail}\n\n"
#          "请分析并给出修正方案：")
#     ])


# def format_yaml_data_prompt() -> ChatPromptTemplate:
#     """Phase C YAML 数据 — 第二阶段：json_mode 结构化输出（thinking off）。
#
#     输出 TestData 模型的 JSON，字段与 Pydantic 严格对齐。
#     """
#     return ChatPromptTemplate.from_messages([
#         ("system",
#          "你是数据格式化专家。根据【数据分析】和【接口定义】，输出 TestData 模型结构的 JSON（Pydantic 校验）。\n\n"
#          "### 可用数据工厂方法（动态占位符只能从此清单选择，严格按 syntax 填写）\n"
#          "{data_factory_methods}\n\n"
#          "### ⚠️ 输出 JSON 结构（必须严格遵循，一个字符都不能错）\n\n"
#          "整个输出只有一个顶层 key: **data**（数组），数组中每个元素是一个步骤对象。\n\n"
#          "```json\n"
#          "{{\n"
#          '  "data": [\n'
#          '    {{\n'
#          '      "baseInfo": {{\n'
#          '        "api_name": "新增创建",\n'
#          '        "url": "/meterDevice/add",\n'
#          '        "method": "post",\n'
#          '        "header": {{"Content-Type": "application/json;charset=UTF-8"}}\n'
#          '      }},\n'
#          '      "testCase": [\n'
#          '        {{\n'
#          '          "case_name": "新增单一费率电表",\n'
#          # 注意：下方示例中的 random_plates(1) 车牌生成器被 LLM 误用于电表编号 ——
#          # 这是两段式被注释的原因之一（错误示例诱导字段误用）。
#          '          "json": {{"code": "${{random_plates(1)}}", "name": "测试电表"}},\n'
#          '          "validation": [{{"contains": {{"$.msg": "成功"}}}}],\n'
#          '          "extract": {{"meterCode": "$.data.code"}}\n'
#          '        }}\n'
#          '      ]\n'
#          '    }},\n'
#          '    {{\n'
#          '      "baseInfo": {{\n'
#          '        "api_name": "分页查询",\n'
#          '        "url": "/meterDevice/getPage",\n'
#          '        "method": "post",\n'
#          '        "header": {{"Content-Type": "application/json;charset=UTF-8"}}\n'
#          '      }},\n'
#          '      "testCase": [\n'
#          '        {{\n'
#          '          "case_name": "查询电表列表验证新增",\n'
#          '          "json": {{"pageNum": 1, "pageSize": 10}},\n'
#          '          "validation": [{{"contains": {{"$.data.records[0].meterName": "${{get_extract_data(meterName)}}"}}}}]\n'
#          '        }}\n'
#          '      ]\n'
#          '    }}\n'
#          '  ]\n'
#          '}}\n'
#          "```\n\n"
#          "### 结构铁律（参考上方示例）\n"
#          "1. 顶层必须是 **\"data\": [...]** 数组，禁止用 testCase 或其他名字\n"
#          "2. data 数组的每个元素是步骤对象，必须包含 **baseInfo** 和 **testCase** 两个键\n"
#          "3. **testCase 必须是数组** [...], 禁止写成对象 {{...}}\n"
#          "4. **validation 必须是数组** [...], 禁止写成对象 {{...}}\n"
#          "5. api_name/url/method 与接口定义完全一致\n"
#          "6. method 必须小写（post/get/put/delete）\n"
#          "7. url 必须与接口序列（锚）中的 url **逐字一致**（含 {{param}} 字面量）；"
#          "只写路径、禁域名、禁 query、禁 ${{}}；路径参数禁止替换成具体值"
#          "（具体值/query 走 testCase.params 或 json）\n"
#          "8. **每个 baseInfo 必须有 header 字段**：POST/PUT/PATCH 写 Content-Type: application/json, GET 写空 {{}}\n"
#          "9. 请求参数位置按接口定义 body 字段的 `location` 决定：`location=query` 的字段 → testCase.params，"
#          "`location=body` 的字段 → testCase.json；接口定义未标 location 时按 HTTP 方法——"
#          "GET/DELETE → params, POST/PUT/PATCH → json\n"
#          "10. 动态值使用 ${{函数名(参数)}}，函数必须来自上方清单\n"
#          "11. extract/input_extract 用不到就省略整个字段，禁止输出空 {{}} 或 null\n"
#          "12. validation 数组不能为空，每步至少一条断言；断言的期望值须按规则 19 取自接口返回定义\n"
#          "13. 断言运算符只用 [eq, contains, ne, db] 四种，不等于是 ne 不是 neq；"
#          "每条断言必须且只能是一个单键块（如 {{eq: {{$.msg: 成功}}}} / "
#          "{{contains: {{status_code: 200}}}}），禁止 check/expected/operator、"
#          "jsonpath/operator/value 等多键写法\n"
#          "14. **若「数据库表结构信息」为空，禁止 db 断言**（无表结构无法写正确 SQL），改用 eq/contains/ne\n"
#          "15. **对 `status_code` 的断言必须用 contains: {{status_code: X}}，禁止 eq/ne**（适用于所有接口，"
#          "不限于导出；导出/下载/模板接口返回二进制流，维持 contains: {{status_code: 200}}）\n"
#          "16. JSONPath 必须以 $. 开头（如 $.data.code）\n"
#          "17. 禁止 Markdown，只输出纯净 JSON\n"
#          "18. **`contains` 的值必须是字典对象**（`{{字段: 期望}}` 或 `{{$.JSONPath: 期望}}`），禁止裸字符串/标量\n"
#          "19. **成功/失败断言的期望值取自接口返回定义**：断言的字段与期望值必须取自「接口定义」"
#          "返回定义中真实给出的字段/取值/语义；正向用例断言业务成功对应的返回取值，反向用例"
#          "断言失败返回取值；返回定义未给出明确取值时，退化为 contains 字段存在性或 status_code "
#          "断言，禁止臆造固定取值"),
#         ("human",
#          "### 数据分析\n{data_analysis}\n\n"
#          "### 接口定义\n{api_definitions}\n\n"
#          "### 用例逻辑\n{test_case_logic}\n\n"
#          "### 用户意图\n{user_context}\n\n"
#          "### 数据库表结构信息（为空时禁止 db 断言）\n{db_schema}\n\n"
#          "请严格按照上方 JSON 结构输出：")
#     ])


# ---- Phase C: YAML 数据生成 ----
def generate_yaml_data_single_prompt() -> ChatPromptTemplate:
    """Phase C YAML 数据生成 — 单节点专用（schema 驱动，无示例）。

    2026-08-25 替换为 v3 结构：分节标题（# 角色 / 数据工厂 / schema / 铁律）+ 完整规则措辞。
    A/B 对比（logs/prompt_ab）：v3 修复精简措辞引入的断言块键倒置回归（{$.retCode: {eq: 1}}），
    断言结构 0 非法块，与旧版同质量且更快；value > desc > 用例值 优先级保留在 human 段。
    """

    # ===================== SYSTEM（固定规则） =====================
    system_template = """
    # 角色
    你是数据格式化专家。输出严格遵循下方 yaml 格式 schema 的 TestData JSON（Pydantic 校验，字段与类型一个都不能错）。
    本 prompt 不含任何具体业务示例，禁止编造与输入无关的固定数据。
    
    # 可用数据工厂方法（动态占位符只能从此清单选择，严格按 syntax 填写）
    {data_factory_methods}
    
    # yaml 格式 schema（输出结构唯一来源，禁止自创结构或推断字段）
    {json_schema}
    
    # 铁律（共 15 条，内容唯一来源 = 上方 schema + human 段的 B/A/接口文档）
    1. 顶层只能有一个 data 数组，每元素含 baseInfo 与 testCase 两个键
    2. 请求体三选一（json/params/data 只出现一个）：优先按接口定义 body 字段的 `location` 选——
       `location=query` 的字段 → params，`location=body` 的字段 → json；接口定义未标 location 时
       按 HTTP 方法——GET/DELETE 用 params，POST/PUT/PATCH 用 json
    3. url 与接口定义中的路径完全一致：只写路径、禁 query/域名/占位符表达式；路径参数不得替换成具体值
    4. 每个 baseInfo 必有 header 键：JSON 请求体方法写 Content-Type=application/json，其余写空对象
    5. validation 数组不得为空，每步至少一条断言；运算符只允许 eq/contains/ne/db；
       每条断言必须且只能是一个单键块，即一个运算符键对应一个断言对象，
       禁止 check/expected/operator、jsonpath/operator/value 等多键写法
    6. status_code 只能被 contains 断言，且 contains 的值必须是字典对象（键=字段名、值=期望值）；禁止用 eq/ne 断言 status_code
    7. JSONPath 一律以 $. 开头
    8. extract/input_extract 用不到就整字段省略，禁止输出空对象或 null
    9. 成功/失败断言的期望值取自接口返回定义：断言的字段与期望值必须取自「接口详情文档」返回定义中真实给出的字段/取值/语义；
       正向用例断言业务成功对应的返回取值，反向用例断言失败返回取值；返回定义未给出明确成功/失败取值时，退化为
       contains 字段存在性或 status_code 断言，禁止臆造返回定义之外的固定取值
    10. 唯一键动态化：设备名/编码等唯一键一律用工厂方法生成，可保留用例给出的值作前缀并追加随机后缀，禁止输出纯字面值；覆盖 setup 与用例内所有创建步骤
    11. delete 参数按接口定义：delete 请求参数严格按接口定义填写，禁止输出接口定义之外的任何字段
    12. 禁止 Markdown；只输出纯净 JSON（Pydantic 校验，字段/类型一个都不能错）
    13. 跨步骤/前置资源引用：引用前置（setup）或上游步骤创建的资源标识（code/name 等），必须从创建步骤
       input_extract 提取 key（如 $.json.code），本步骤用 ${{get_extract_data(key)}} 引用；禁止写死或重新构造。
       引用动作禁止随机生成——必须引用创建时提取的同一值
    14. 列表型返回断言：data 为数组时用 contains 断言 $.data（键=$.data、值=目标值；框架对 $.data 拼接列表
       全部元素做子串包含，稳定）；避免用 $..字段 做断言（框架取第一个匹配值，列表顺序变化即不稳定）
    15. ne 断言仅用于简单字段比较（无 JSONPath），禁止对 JSONPath 用 ne（框架 ne 不解析 JSONPath，必败）；
       JSONPath 断言一律用 eq/contains
    """

    # ===================== HUMAN（可变输入） =====================
    human_template = """
    # B 用例内容（本用例执行步骤 + 预期结果）
    {test_case_logic}
    
    # A 数据分析（生成前思考要点引导）
    {data_analysis}
    
    # 接口详情文档（路径/请求字段/返回语义，取值唯一来源）
    {api_definitions}
    
    # 用户意图
    {user_context}
    
    # 数据库表结构信息（为空时禁止 db 断言）
    {db_schema}
    
    请严格按照 system 段的 yaml 格式 schema 输出 TestData JSON：字段名与结构以 schema 为准，
    字段值按 value > desc > 用例值 的优先级决定（异常用例除外），禁止使用示例或占位数据。
    """

    return ChatPromptTemplate.from_messages([
        ("system", system_template),
        ("human", human_template),
    ])


# ============================================================
# YAML 生成 Prompt 常量（2026-09-01 自 yaml_gen.py 迁移，A+B+C 重构）
# ============================================================

# 2026-08-27 v8 根因修复（决策「注入 setup 标记」）：setup 是否捕获键是 LLM 掷骰子——
# prompt 铁律 8 允许省略无用 extract、铁律 13 只约束引用方，且 LLM 无法识别「共享前置」任务。
# 此规则注入 setup 任务 steps（走 test_case_logic），强制 setup 块捕获资源标识供下游引用。
# 规则表述（非示例），符合 prompt 无示例约束。
# ---- Phase C: 常量·前置捕获规则 ----
SETUP_CAPTURE_RULE = (
    "【共享前置 setup 块】本文件为共享前置，创建的资源标识（code 等唯一键）"
    "必须通过 input_extract 捕获（键名 camelCase 语义化，如 pre001MeterCode），"
    "供后续用例与清理引用；即使本文件内无引用也必须捕获，禁止省略 input_extract"
)

# 单节点生成引导：浓缩 analyze 的 5 条分析要点，引导模型在 thinking 里完成分析
# （单节点 thinking 走 reasoning_content，content 直接输出 TestData JSON，无独立分析文本）
# ---- Phase C: 常量·YAML 分析指南 ----
YAML_ANALYSIS_GUIDE = (
    "请先在思考中完成以下分析，再严格按本 prompt 的 JSON 结构输出：\n"
    "1. 接口匹配：每个步骤对应哪个接口（url/method 与接口定义一致）\n"
    "2. 请求参数：来源（用例指定/上游提取/工厂方法）\n"
    "3. 数据传递：哪些返回值需要 extract 供下游引用\n"
    "4. 断言设计：断言字段与期望值\n"
    "5. 动态值：用哪个工厂函数，还是固定字面量"
)


# ---- Phase C: 依赖映射生成 ----
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


# ---- Phase C: 依赖映射修复 ----
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
