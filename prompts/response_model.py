from typing import Dict, Any, List, Optional
from pydantic import BaseModel, Field

class ProperResponse(BaseModel):
    """
    定义了 LLM 输出数据的结构。
    """
    proper_thinking: List[str] = Field(description="针对如何回复这个问题的思考")
    final_response: str = Field(description="整理思考后的最终回复")
    worth_to_remember: bool = Field(description="从测试经验提升角度判断是否值得记忆")

class ApiDefinition(BaseModel):
    name: str = Field(description="接口名称")
    url: str = Field(description="接口完整路径，如 http://localhost:8000/api/login")
    method: str = Field(description="HTTP方法: GET, POST, PUT, DELETE")
    description: str = Field(description="接口功能描述")
    parameters: Dict[str, Any] = Field(description="请求参数结构示例")
    returns: Dict[str, Any] = Field(description="返回数据结构示例，包含所有响应字段的名称和类型")

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

