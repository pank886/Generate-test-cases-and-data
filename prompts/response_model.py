"""LLM 结构化输出模型定义。

所有模型按业务链路分组，规则：
  1. 依赖项定义在前，组合体在后（如 ExcelRow → ExcelPlan）
  2. 每个分组顶部有 === 注释说明用途
  3. 与 LLM 配合的模型配置统一放在此处，prompt 中不再重复定义结构
"""

from __future__ import annotations
import logging
import re
from typing import Dict, Any, List, Optional
from pydantic import BaseModel, Field, field_validator, model_validator, ValidationInfo

logger = logging.getLogger(__name__)

# LLM 字段漂移统计（data→json），用于监控 prompt 质量
_drift_total = 0
_drift_count = 0

# 动态占位符解析（框架 replace_load() 只解析 ${func(args)}）。
# 函数白名单与实参规则以 data_factory/methods.yaml 注册表为单一事实源
# （data_factory.registry.get_validation_rules()），此处不维护清单副本。
_PLACEHOLDER_RE = re.compile(r"\$\{([^{}]*)\}")
_PLACEHOLDER_CALL_RE = re.compile(r"^([A-Za-z_]\w*)\(([^()]*)\)$")


# ============================================================
# Phase A 对话响应
# ============================================================

class ProperResponse(BaseModel):
    """LLM 对用户问题的结构化回复"""
    proper_thinking: List[str] = Field(description="针对如何回复这个问题的思考")
    final_response: str = Field(description="整理思考后的最终回复")
    worth_to_remember: bool = Field(description="从测试经验提升角度判断是否值得记忆")


# ============================================================
# 接口定义（文档提取 / 测试计划引用）
# ============================================================

class ApiDefinition(BaseModel):
    """单个接口定义"""
    name: str = Field(description="接口名称")
    url: str = Field(description="接口路径部分（不含域名和基础地址），如 /api/login")
    method: str = Field(description="HTTP方法: GET/POST/PUT/DELETE/PATCH")
    description: str = Field(description="接口功能描述")
    parameters: Dict[str, Any] = Field(description="请求参数结构示例")
    returns: Dict[str, Any] = Field(description="返回数据结构示例，包含所有响应字段的名称和类型")

    @classmethod
    @field_validator("returns", mode="before")
    def normalize_returns(cls, v, info: ValidationInfo):
        """LLM 有时会把纯数组返回值输出为 list，自动包装成 {"data": [...]}。"""
        if isinstance(v, list):
            return {"data": v}
        return v


# ============================================================
# Excel V2 — 双 Sheet（共享前置 + 测试用例）
# ============================================================

class SharedPrecondition(BaseModel):
    """共享前置条件 — Excel Sheet 2 的一行"""
    id: str = Field(description="全局唯一编号，如 PRE-001")
    name: str = Field(description="前置名称，如「已创建测试跑步机」")
    steps: str = Field(description="详细步骤文本，\\n 分隔")
    expected: str = Field(description="预期结果文本")
    cloned_from: Optional[str] = Field(
        default=None,
        description="克隆来源 PRE id。消解器自动填入，非克隆时为 null，不写入 Excel"
    )


class TestCaseRow(BaseModel):
    """测试用例 — Excel Sheet 1 的一行。epic/feature 由代码从模块树填入，不走 LLM。"""
    id: str = Field(description="全局唯一编号，如 TC-001")
    story: str = Field(description="子模块名（对应 @allure.story），从产品文档中提取的模块名称，如「设施管理」，嵌套时用 A-a 格式")
    title: str = Field(description="用例名称（对应 @allure.title），如「设施管理-新增设施-正向」")
    preconditions: list[str] = Field(
        default_factory=list,
        description="引用的前置编号列表，如 ['PRE-001', 'PRE-002']"
    )
    steps: str = Field(description="执行步骤文本，\\n 分隔")
    expected: str = Field(description="预期结果文本，\\n 分隔")
    mutates_data: bool = Field(
        default=False,
        description="内部元数据：是否为写操作（增删改等）。LLM 输出，不写入 Excel"
    )
    is_negative_test: bool = Field(
        default=False,
        description="内部元数据：是否为写操作（增删改等）。LLM 输出，不写入 Excel"
    )


class ExcelPlanV2(BaseModel):
    """Excel 测试计划 V2（双 Sheet）"""
    shared_preconditions: list[SharedPrecondition] = Field(
        description="共享前置列表 — 写入 Sheet 2"
    )
    test_cases: list[TestCaseRow] = Field(
        description="测试用例列表 — 写入 Sheet 1"
    )
    file_name: str = Field(default="test_plan.xlsx", description="输出的 Excel 文件名")


# ============================================================
# Excel 测试计划 V1（保留兼容）
# ============================================================

class ExcelRow(BaseModel):
    """Excel 测试计划中的一行 = 一个测试用例"""
    project_name: str = Field(description="项目名称，如 VehicleAccess")
    allure_epic: str = Field(description="Allure Epic 层级")
    module_name: str = Field(description="模块/类名（相同场景的用例填入相同值），如 TestParkingFree")
    allure_feature: str = Field(description="Allure Feature 层级")
    allure_story: str = Field(description="Allure Story 层级（场景标题）")
    fixture_level: str = Field(description="fixture等级，如 danyuan，多个用逗号分隔")
    case_name: str = Field(description="用例方法名，如 test_CarEntry_001")
    steps: List[str] = Field(description="执行步骤列表，steps[0] 含前置清空信息，如 '前置清空:园区数据已清理'")
    test_data_yaml: str = Field(description="测试数据 YAML 文件名（单文件），如 testCarEntry_001.yaml")
    enabled: str = Field(description="是否启用，Y 或 N")


class ExcelPlan(BaseModel):
    """完整的 Excel 测试计划"""
    rows: List[ExcelRow] = Field(description="测试计划行数据列表")
    file_name: str = Field(default="test_plan.xlsx", description="输出的 Excel 文件名")


class PyFile(BaseModel):
    """生成的 Python 测试文件"""
    file_name: str = Field(description="Python 文件名，如 test_Vehicle_access.py")
    py_content: str = Field(description="完整的 Python 测试文件代码")


class ClassCode(BaseModel):
    """单个 Python 测试类的代码片段（不含 import 和 epic 装饰器）"""
    class_code: str = Field(description="单个测试类的完整 Python 代码")


# ============================================================
# 测试数据（YAML 生成，Phase A 确认后执行）
# ============================================================

class TestCase(BaseModel):
    """单个测试用例 — testCase 数组中的一个元素"""
    model_config = {"populate_by_name": True}

    case_name: str = Field(description="用例名，如 test_CarEntry_001")
    request_body: Optional[Dict[str, Any]] = Field(
        default=None,
        description="请求体 JSON 数据",
        serialization_alias="json",  # YAML 输出时仍用 json 字段名
        validation_alias="json",     # LLM 输入时接受 json 字段名
    )
    params: Optional[Dict[str, Any]] = Field(default=None, description="URL query 参数")
    form_data: Optional[Dict[str, Any]] = Field(
        default=None,
        description="表单编码请求体（仅 Content-Type 为 x-www-form-urlencoded 时合法），"
                    "由 StepData 依据 header 判定后填入，YAML 输出为 data",
        serialization_alias="data",
    )
    validation: List[Dict[str, Any]] = Field(default=[], description="断言规则列表")
    # B5/B10（回炉类）: extract 系字段值必须是 str —— 依赖 Dict[str, str] 严格类型校验，
    # int/float/bool/None 一律 ValidationError（不做静默强转/丢弃，失败进重生成循环）。
    # 无需提取时应省略字段（prompt 铁律），空 {} 由 strip_empty_optional_dicts 剔除。
    extract_list: Optional[Dict[str, str]] = Field(default=None, description="从响应提取字段")
    extract: Optional[Dict[str, str]] = Field(default=None, description="(兼容旧字段) 从响应提取")
    input_extract: Optional[Dict[str, str]] = Field(default=None, description="从请求提取")

    @model_validator(mode="before")
    @classmethod
    def migrate_data_to_json(cls, data: Any) -> Any:
        """字段级兜底：LLM 误输出 data 而非 json 时，在校验前自动迁移。

        统计漂移频率用于监控 prompt 质量：
          - <=5%: WARNING 级别
          - >5%:  ERROR 级别（提示需优化 prompt）
        """
        global _drift_total, _drift_count
        if isinstance(data, dict):
            # validation_alias 解析后字段名已是 request_body
            _drift_total += 1
            if "data" in data and "request_body" not in data:
                _drift_count += 1
                data["request_body"] = data.pop("data")
                rate = _drift_count / _drift_total * 100
                log_msg = f"LLM 字段漂移 data→json 累计 {_drift_count}/{_drift_total} ({rate:.1f}%)"
                if rate > 5:
                    logger.error(log_msg)
                else:
                    logger.warning(log_msg)
        return data

    @model_validator(mode="after")
    def validate_body_exclusivity(self) -> "TestCase":
        """B9（回炉类）: json/params/data 必须且只能出现一个。

        空 params 占位已由 strip_empty_optional_dicts 剔除，此处检出的是真实并存 —
        代码删哪个都是猜测，一律校验失败进重生成循环，由 LLM 自查决定正确的请求方式。
        """
        present = [label for label, val in (
            ("json", self.request_body),
            ("params", self.params),
            ("data", self.form_data),
        ) if val is not None]
        if len(present) > 1:
            raise ValueError(
                f"json/params/data 三选一，检测到并存: {' + '.join(present)}，"
                "请依据接口定义只保留正确的一种请求参数")
        return self

    @model_validator(mode="before")
    @classmethod
    def strip_empty_optional_dicts(cls, data: Any) -> Any:
        """输出卫生兜底（json/params/data 必须且只能出现一个）。

        - extract/input_extract/extract_list 为空对象 {} → None（可选字段，省略）
        - params 为空 {} 且已有 json/data 请求体 → None（消除"有 json 还带空 params"）
        - params 为空 {} 但无其他请求体（如无条件 GET）→ 保留，满足三选一必填
        None 会被 model_dump(exclude_none=True) 剔除；json 请求体不做剔除（{} 有语义）。
        """
        if isinstance(data, dict):
            for field in ("extract", "input_extract", "extract_list"):
                if data.get(field) == {}:
                    data[field] = None
            if data.get("params") == {}:
                has_body = any(
                    data.get(k) is not None
                    for k in ("json", "request_body", "data", "form_data")
                )
                if has_body:
                    data["params"] = None
        return data

    @model_validator(mode="before")
    @classmethod
    def merge_same_type_validations(cls, data: Any) -> Any:
        """输出卫生兜底：合并同类型断言为一个条目。

        LLM 输出 [{eq: {code: 0}}, {eq: {msg: ok}}] → [{eq: {code: 0, msg: ok}}]。
        同类型断言中出现同名字段但期望值不同（真实冲突）时保持独立条目，不丢断言。
        """
        if not isinstance(data, dict):
            return data
        validation = data.get("validation")
        if not isinstance(validation, list) or len(validation) <= 1:
            return data

        merged_by_type: Dict[str, Dict[str, Any]] = {}
        result = []
        for item in validation:
            if not (isinstance(item, dict) and len(item) == 1):
                result.append(item)
                continue
            vtype, payload = next(iter(item.items()))
            if not isinstance(payload, dict):
                result.append(item)
                continue
            bucket = merged_by_type.get(vtype)
            if bucket is None:
                bucket = dict(payload)
                merged_by_type[vtype] = bucket
                result.append({vtype: bucket})
            elif any(k in bucket and bucket[k] != v for k, v in payload.items()):
                result.append({vtype: payload})  # 同字段不同期望值 → 保持独立
            else:
                bucket.update(payload)
        if len(result) != len(validation):
            data["validation"] = result
        return data


class StepData(BaseModel):
    """单步测试数据 — data 数组中的一个元素"""
    baseInfo: Dict[str, Any] = Field(description="接口基础信息（api_name, url, method, header 等）")
    testCase: List[TestCase] = Field(
        min_length=1,
        description="测试用例列表（每个元素含 case_name, request_body, validation 等），至少 1 条",
    )

    @model_validator(mode="before")
    @classmethod
    def normalize_base_info(cls, data: Any) -> Any:
        """规范化 baseInfo（代码兜底 LLM 漂移）。

        1. method 必须小写
        2. url 不含域名，LLM 输出完整 URL 时截取 path
        3. header 缺失时注入 Content-Type（token/公共头由框架层常量注入，此处不生成）：
           - json/迁移后为 json 的请求体: application/json;charset=UTF-8
           - 文件上传（请求体含 file）/ 仅 params: 不注入（multipart 边界由客户端生成、
             GET 无需请求体头）
        4. 表单体判定（data 仅在 x-www-form-urlencoded 下合法）：
           header 明确为表单 Content-Type 时，case 的 data 是合法表单体 → 存入
           form_data（输出仍为 data）；否则 data 视为字段漂移，由 TestCase 迁移为 json。
        """
        if not isinstance(data, dict):
            return data
        base = data.get("baseInfo")
        if not isinstance(base, dict):
            return data
        cases = [c for c in (data.get("testCase") or []) if isinstance(c, dict)]

        # 1. method 小写
        method = base.get("method")
        if isinstance(method, str) and method != method.lower():
            base["method"] = method.lower()

        # 2. url 去域名
        url = base.get("url")
        if isinstance(url, str) and url.startswith(("http://", "https://")):
            from urllib.parse import urlparse
            parsed = urlparse(url)
            base["url"] = parsed.path + (f"?{parsed.query}" if parsed.query else "")
            logger.warning("LLM url 含域名已截取 path: %s -> %s", url, base["url"])

        # 3. header 注入（仅在有 json 类请求体且非上传时）
        if "header" not in base:
            has_body, has_file = False, False
            for c in cases:
                body = c.get("json")
                if body is None:
                    body = c.get("request_body")
                if body is None:
                    body = c.get("data")  # 无表单 CT，后续会迁移为 json
                if body is not None:
                    has_body = True
                    if isinstance(body, dict) and "file" in body:
                        has_file = True
            if has_body and not has_file:
                base["header"] = {"Content-Type": "application/json;charset=UTF-8"}

        # 4. 表单 Content-Type 明确时，data 为合法表单体
        header = base.get("header")
        if isinstance(header, dict):
            ct = next((v for k, v in header.items()
                       if str(k).lower() == "content-type"), "")
            if "x-www-form-urlencoded" in str(ct).lower():
                for c in cases:
                    if "data" in c and "form_data" not in c:
                        c["form_data"] = c.pop("data")
        return data


class TestData(BaseModel):
    """测试数据（序列化为 YAML 文件）"""
    data: List[StepData] = Field(
        min_length=1,
        description="接口调用列表，每个元素为一个接口调用（含 baseInfo + testCase），至少 1 个",
    )
    file_name: str = Field(default="test_data.yaml", description="输出的 YAML 文件名")

    @model_validator(mode="after")
    def validate_placeholders(self) -> "TestData":
        """动态占位符校验（B1-B4，回炉类。框架只认 ${...}）。

        函数白名单与实参规则读取 data_factory/methods.yaml 注册表
        （单一事实源，框架新增方法只需更新注册表，此处自动跟随）。
        拦截 LLM 幻觉语法（真实案例: '{{(get_current_time(ymd) + 1day)}} 11:00:00'）：
          - {{}} 双花括号 → replace_load() 不解析，原样发给服务端
          - 占位符内运算/拼接（+ 1day）→ 框架不支持
          - 非注册表函数 / 实参个数越界 / 首参不在枚举内（如 fmt 非 ydm|hms）
        校验失败抛 ValueError → 登记后进入轮末自查重生成循环。
        """
        from data_factory.registry import get_validation_rules
        rules = get_validation_rules()
        issues: List[str] = []

        def _check_call(inner: str, raw: str, path: str) -> None:
            call = _PLACEHOLDER_CALL_RE.match(inner)
            if not call:
                issues.append(
                    path + ": 占位符只能是 ${函数名(参数)}，禁止运算/拼接: " + raw[:60])
                return
            func, args_str = call.group(1), call.group(2)
            rule = rules.get(func)
            if rule is None:
                issues.append(
                    path + f": 未知占位符函数 '{func}'，注册表可用: "
                    + "/".join(sorted(rules)))
                return
            args = ([a.strip() for a in args_str.split(",")]
                    if args_str.strip() else [])
            n = len(args)
            min_a = rule.get("min_args")
            max_a = rule.get("max_args")
            if (min_a is not None and n < min_a) or (max_a is not None and n > max_a):
                issues.append(
                    path + f": {func} 实参个数 {n} 超出范围 [{min_a}, {max_a}]: " + raw[:60])
                return
            enum0 = rule.get("arg0_enum")
            if enum0 and args:
                a0 = args[0].strip("'\"").lower()
                if a0 not in {str(e).lower() for e in enum0}:
                    issues.append(
                        path + f": {func} 第1个参数仅支持 "
                        + "/".join(str(e) for e in enum0) + f"，得到 '{args[0]}'")

        def _walk(node: Any, path: str) -> None:
            if isinstance(node, str):
                if "{{" in node or "}}" in node:
                    issues.append(
                        path + ": 含 '{{}}' 双花括号（框架只解析 ${...}）: " + node[:60])
                matches = list(_PLACEHOLDER_RE.finditer(node))
                if node.count("${") > len(matches):
                    issues.append(path + ": 占位符未闭合或嵌套: " + node[:60])
                for m in matches:
                    _check_call(m.group(1).strip(), m.group(0), path)
            elif isinstance(node, dict):
                for k, v in node.items():
                    _walk(v, f"{path}.{k}")
            elif isinstance(node, list):
                for idx, v in enumerate(node):
                    _walk(v, f"{path}[{idx}]")

        _walk(self.model_dump(exclude_none=True, by_alias=True)["data"], "data")
        if issues:
            raise ValueError(
                "动态占位符校验失败（只能使用数据工厂注册表内的函数；时间偏移用 "
                "get_offset_time；注册表不支持的能力请写合理固定字面量，如 "
                "'2029-12-31 10:00:00'）:\n" + "\n".join(issues[:10]))
        return self


# ============================================================
# 场景数据规划（Phase A 数据依赖分析）
# ============================================================

class DataPlanStep(BaseModel):
    """单步 API 调用的数据规划"""
    api_name: str = Field(description="接口名称")
    data_values: Dict[str, Any] = Field(description="请求数据（json 字段内容）")
    form_data: Optional[Dict[str, Any]] = Field(default=None, description="form-data 参数")
    query_params: Optional[Dict[str, Any]] = Field(default=None, description="URL query 参数")
    extract_rules: Optional[Dict[str, str]] = Field(default=None, description="从响应提取数据规则，key=字段名, value=jsonpath")
    input_extract: Optional[Dict[str, str]] = Field(default=None, description="从请求参数提取规则")
    assertions: List[Dict[str, Any]] = Field(default=[], description="断言规则，如 [{\"eq\": {\"code\": 0}}]")
    data_factory_calls: Optional[List[str]] = Field(default=None, description="需使用的工厂方法")


class DataPlan(BaseModel):
    """场景级数据规划结果"""
    scenario_name: str = Field(description="场景名称")
    steps: List[DataPlanStep] = Field(description="步骤列表")
    shared_context: Optional[str] = Field(default=None, description="步骤间的数据传递说明")
    note: Optional[str] = Field(default=None, description="补充说明")


# ============================================================
# 测试点分析（Phase C 产出）
# ============================================================

class TestPointItem(BaseModel):
    """单个测试点"""
    module: str = Field(description="所属模块")
    scenario: str = Field(description="测试场景标题")
    description: str = Field(description="测试点详细描述，包含前置条件和预期结果")
    priority: str = Field(description="优先级: P0/P1/P2/P3")
    test_type: str = Field(description="测试类型: 功能/边界/异常/兼容")
    related_requirement: Optional[str] = Field(default=None, description="关联的产品需求点说明")
    risk: Optional[bool] = Field(default=None, description="高风险填 true，否则 false")


class TestPointList(BaseModel):
    """测试点分析结果"""
    project_name: str = Field(description="项目名称")
    summary: str = Field(description="总体分析摘要")
    test_points: List[TestPointItem] = Field(description="测试点列表")
    risk_areas: List[str] = Field(default=[], description="风险点/需重点关注区域名称列表")


# ============================================================
# 文档提取（在线 LLM 解析文档阶段）
# ============================================================

class IntentConfirmation(BaseModel):
    """LLM 模块匹配结果（Phase C 节点1 意图识别）"""
    matched_modules: List[str] = Field(description="匹配到的模块名列表，最多3个，按相关性降序")
    confidence: str = Field(description="置信度: high / medium / low")

    @model_validator(mode="before")
    @classmethod
    def normalize_matches(cls, data: Any) -> Any:
        """兼容 LLM 输出 matches 而非 matched_modules 的字段漂移。"""
        if isinstance(data, dict):
            # 字段名漂移：matches → matched_modules
            if "matches" in data and "matched_modules" not in data:
                raw = data.pop("matches")
                # 值格式漂移：[{"module": "xxx"}] → ["xxx"]
                if isinstance(raw, list):
                    data["matched_modules"] = [
                        m["module"] if isinstance(m, dict) else m for m in raw
                    ]
            # matches 和 matched_modules 同时存在时以 matched_modules 为准
        return data


class GlossaryExtract(BaseModel):
    """业务术语表提取结果"""
    terms: List[Dict[str, str]] = Field(description="术语列表，每项含 term(术语名)、definition(解释)和 notes(备注)")


class DocModuleExtract(BaseModel):
    """产品文档模块提取结果"""
    module_name: str = Field(description="本文档所属模块名称，如 合同管理")
    related_modules: List[str] = Field(description="关联/依赖的其他模块列表")
    business_summary: str = Field(description="业务功能摘要，200字以内")
    tags: List[str] = Field(default=[], description="功能标签，如 核心流程、配置管理")


class ApiDefExtract(BaseModel):
    """接口文档提取结果"""
    apis: List[ApiDefinition] = Field(description="提取到的所有接口定义")
    module_name: str = Field(description="接口所属模块")


class TranslationResult(BaseModel):
    """Phase C 翻译结果：中文 → 英文标识符映射。"""
    feature_en: Dict[str, str] = Field(default_factory=dict, description="feature 中文→英文")
    story_en: Dict[str, str] = Field(default_factory=dict, description="story 中文→英文")
    title_en: Dict[str, str] = Field(default_factory=dict, description="title 中文→英文")


# ============================================================
# Phase B-2 依赖映射表
# ============================================================

class DecisionStep(BaseModel):
    """decision_map 中单个步骤的赋值指令"""
    api: str = Field(description="接口标识: METHOD /url，如 'POST /order/create'")
    params: Dict[str, Any] = Field(
        default_factory=dict,
        description="请求参数赋值指令。静态值直接写，动态值用 ${} 字符串",
    )
    assertions: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="断言列表，YAML 原生格式 [{'eq': {...}}, {'contains': {...}}]",
    )


class InternalDependency(BaseModel):
    """单条用例的变量提取与消费关系"""
    output_var: Optional[str] = Field(
        default=None,
        description="本用例产出的变量名（如 'order_id'），不产出时为 null",
    )
    extract_path: Optional[str] = Field(
        default=None,
        description="从响应提取的 JSONPath，必须对齐 api_defs 的 returns 字段",
    )
    used_by: List[str] = Field(
        default_factory=list,
        description="消费此变量的 case_id 列表，如 ['TC-002', 'TC-003']",
    )


class CrossModuleDep(BaseModel):
    """一条跨模块依赖。支持中英文 alias 访问。"""
    model_config = {"populate_by_name": True}

    module: str = Field(
        alias="依赖模块",
        description="依赖的外部模块名",
    )
    var: str = Field(
        alias="需获取变量",
        description="需要获取的变量名",
    )
    api: str = Field(
        alias="获取接口",
        description="获取接口: METHOD /url",
    )


class StoryDependencyMap(BaseModel):
    """单个 story 的依赖映射"""
    story_name: str = Field(description="中文 story 名，与 Excel @allure.story 列一致")
    story_pre_api_sequence: List[str] = Field(
        default_factory=list,
        description="共享前置 API 序列，格式 ['步骤名:METHOD /url', ...]",
    )
    case_api_sequences: Dict[str, List[str]] = Field(
        default_factory=dict,
        description="case_id → api_sequence 映射",
    )
    decision_map: Dict[str, Dict[str, Any]] = Field(
        default_factory=dict,
        description="case_id → {'steps': [DecisionStep, ...]}",
    )
    internal_dependency: Dict[str, InternalDependency] = Field(
        default_factory=dict,
        description="case_id → 变量依赖关系",
    )
    cross_module_dependency: Dict[str, CrossModuleDep] = Field(
        default_factory=dict,
        description="前置步骤名 → 跨模块依赖",
    )
    teardown_api_sequence: List[str] = Field(
        default_factory=list,
        description="清理 API 序列。LLM 判断无需清理时为空数组 []",
    )

    @model_validator(mode="after")
    def validate_key_consistency(self) -> "StoryDependencyMap":
        """校验 case_api_sequences / internal_dependency / decision_map 的 key 集合一致。"""
        keys_api = set(self.case_api_sequences.keys())
        keys_dep = set(self.internal_dependency.keys())
        keys_dec = set(self.decision_map.keys())

        if keys_api != keys_dep or keys_api != keys_dec:
            missing_api = keys_dec - keys_api
            missing_dep = keys_dec - keys_dep
            extra = keys_api - keys_dec
            parts = []
            if missing_api:
                parts.append(f"case_api_sequences 缺少: {sorted(missing_api)}")
            if missing_dep:
                parts.append(f"internal_dependency 缺少: {sorted(missing_dep)}")
            if extra:
                parts.append(f"多余的 key: {sorted(extra)}")
            raise ValueError("三个 map 的 case_id key 集合不一致: " + "; ".join(parts))

        # case_api_sequences value 非空
        empty_cases = [k for k, v in self.case_api_sequences.items() if not v]
        if empty_cases:
            raise ValueError(
                f"case_api_sequences 中以下 case_id 的值为空数组（必须至少有一个 API）: {empty_cases}"
            )

        # used_by 引用的 case_id 必须存在
        for case_id, dep in self.internal_dependency.items():
            for used in dep.used_by:
                if used not in keys_api:
                    raise ValueError(
                        f"internal_dependency['{case_id}'].used_by 引用了不存在的 case_id '{used}'"
                    )

        return self


class DependencyMap(BaseModel):
    """完整的依赖映射表（一个 feature 的所有 story）"""
    stories: List[StoryDependencyMap] = Field(
        min_length=1,
        description="该 feature 下所有 story 的依赖映射",
    )
    file_name: str = Field(default="dependency_map.json")
