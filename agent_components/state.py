"""LangGraph 工作流状态定义"""
from typing import Optional, TypedDict, List
from pydantic import BaseModel, Field

from prompts.response_model import (
    ProperResponse,
    ApiDefinition,
    TestData,
    ExcelPlan,
)


class State(TypedDict):
    """LangGraph 工作流的全局状态，在各个节点间传递"""

    user_input: str
    original_input: str
    context: str
    chat_history: list
    response_obj: "ProperResponse"
    api_definition_list: Optional[List[ApiDefinition]]
    test_data: Optional["TestData"]
    excel_plan: Optional["ExcelPlan"]
    excel_path: Optional[str]
    output_dir: Optional[str]  # 本次生成的输出目录


class ApiDefinitionList(BaseModel):
    """包装类：用于让 LLM 输出接口列表"""
    apis: List[ApiDefinition] = Field(..., description="提取到的所有接口定义列表")
