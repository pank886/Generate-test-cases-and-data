"""LLM 结构化输出模型定义。

所有模型按业务链路分组，规则：
  1. 依赖项定义在前，组合体在后（如 ExcelRow → ExcelPlan）
  2. 每个分组顶部有 === 注释说明用途
  3. 与 LLM 配合的模型配置统一放在此处，prompt 中不再重复定义结构
"""

from __future__ import annotations
import logging
from typing import Dict, Any, List, Optional
from pydantic import BaseModel, Field, field_validator, model_validator, ValidationInfo

logger = logging.getLogger(__name__)

# LLM 字段漂移统计（data→json），用于监控 prompt 质量
_drift_total = 0
_drift_count = 0


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
    validation: List[Dict[str, Any]] = Field(default=[], description="断言规则列表")
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


class StepData(BaseModel):
    """单步测试数据 — data 数组中的一个元素"""
    baseInfo: Dict[str, Any] = Field(description="接口基础信息（api_name, url, method, header 等）")
    testCase: List[TestCase] = Field(description="测试用例列表（每个元素含 case_name, request_body, validation 等）")


class TestData(BaseModel):
    """测试数据（序列化为 YAML 文件）"""
    data: List[StepData] = Field(description="接口调用列表，每个元素为一个接口调用（含 baseInfo + testCase）")
    file_name: str = Field(default="test_data.yaml", description="输出的 YAML 文件名")


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
