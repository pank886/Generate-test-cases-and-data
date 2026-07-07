from typing import Dict, Any, List, Optional
from pydantic import BaseModel, Field, field_validator

class ProperResponse(BaseModel):
    """
    定义了 LLM 输出数据的结构。
    """
    proper_thinking: List[str] = Field(description="针对如何回复这个问题的思考")
    final_response: str = Field(description="整理思考后的最终回复")
    worth_to_remember: bool = Field(description="从测试经验提升角度判断是否值得记忆")

class ApiDefinition(BaseModel):
    name: str = Field(description="接口名称")
    url: str = Field(description="接口路径部分（不含域名和基础地址），如 /api/login")
    method: str = Field(description="HTTP方法: GET, POST, PUT, DELETE")
    description: str = Field(description="接口功能描述")
    parameters: Dict[str, Any] = Field(description="请求参数结构示例")
    returns: Dict[str, Any] = Field(description="返回数据结构示例，包含所有响应字段的名称和类型")

    @field_validator("returns", mode="before")
    @classmethod
    def normalize_returns(cls, v):
        if isinstance(v, list):
            return {"data": v}
        return v

class TestData(BaseModel):
    """测试数据 — 结构化 JSON，保存时自动转为 YAML"""
    data: list = Field(description="YAML 测试数据的结构化表示，每个元素为一个接口调用（含 baseInfo + testCase）")
    file_name: str = Field(default="test_data.yaml", description="输出的 YAML 文件名")

class StepData(BaseModel):
    """单步测试数据 — data 数组中的一个元素"""
    baseInfo: Dict[str, Any] = Field(description="接口基础信息（api_name, url, method, header 等）")
    testCase: List[Dict[str, Any]] = Field(description="测试用例列表（每个元素含 case_name, json, validation 等）")

class ExcelRow(BaseModel):
    """Excel 测试计划中的一行"""
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
    """单个 Python 测试类的代码片段"""
    class_code: str = Field(description="单个测试类的完整 Python 代码（不含 import 和 epic）")


# --- Phase C 测试点分析 ---

class TestPointItem(BaseModel):
    """单个测试点"""
    module: str = Field(description="所属模块")
    scenario: str = Field(description="测试场景标题")
    description: str = Field(description="测试点详细描述，包含前置条件和预期结果")
    priority: str = Field(description="优先级: P0/P1/P2/P3")
    test_type: str = Field(description="测试类型: 功能/边界/异常/兼容")
    related_requirement: Optional[str] = Field(default=None, description="关联的产品需求点说明")
    risk: Optional[bool] = Field(default=None, description="高风险填 true，否则 false")


# --- Phase A 场景级数据规划 ---

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


class TestPointList(BaseModel):
    """测试点分析结果"""
    project_name: str = Field(description="项目名称")
    summary: str = Field(description="总体分析摘要")
    test_points: List[TestPointItem] = Field(description="测试点列表")
    risk_areas: List[str] = Field(default=[], description="风险点/需重点关注区域名称列表")


# --- Phase A 文档提取 ---

class GlossaryExtract(BaseModel):
    """业务术语表提取结果"""
    terms: List[Dict[str, str]] = Field(description="术语列表，每项含 term(术语名)、definition(解释)和 notes(备注)")


class DocModuleExtract(BaseModel):
    """产品文档模块提取结果"""
    module_name: str = Field(description="本文档所属模块名称，如 合同管理")
    related_modules: List[str] = Field(description="关联/依赖的其他模块列表")
    business_summary: str = Field(description="业务功能摘要，200字以内")
    tags: List[str] = Field(default=[], description="功能标签，如 核心流程、配置管理")


class ApiDefItem(BaseModel):
    """单个接口定义"""
    name: str = Field(description="接口名称")
    url: str = Field(description="接口路径")
    method: str = Field(description="HTTP方法: GET/POST/PUT/DELETE/PATCH")
    description: str = Field(description="接口功能描述")
    parameters: Dict[str, Any] = Field(description="请求参数结构")
    returns: Dict[str, Any] = Field(description="返回数据结构")

    @field_validator("returns", mode="before")
    @classmethod
    def normalize_returns(cls, v):
        """LLM 有时会把纯数组返回值输出为 list，自动包装成 {"data": [...]}。"""
        if isinstance(v, list):
            return {"data": v}
        return v


class ApiDefExtract(BaseModel):
    """接口文档提取结果"""
    apis: List[ApiDefItem] = Field(description="提取到的所有接口定义")
    module_name: str = Field(description="接口所属模块")

