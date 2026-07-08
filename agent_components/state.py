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

    # --- Phase C 多跳检索 + 测试点分析 ---
    product_docs: Optional[List[dict]]       # Hop 1: 产品文档检索结果
    related_modules: Optional[List[str]]     # 提取出的关联模块列表
    api_definitions: Optional[List[dict]]    # Hop 2b: 接口定义检索结果
    test_points: Optional[list]              # 分析后的测试点列表

    # --- Phase C 多轮对话 ---
    candidate_modules: Optional[List[str]]    # 节点1 LLM 匹配的候选模块名
    confirmation_question: Optional[str]      # 给用户看的确认提示文本
    workflow_status: str                      # "PENDING" → "WAITING" → "CONFIRMED"
    confirmed_module: Optional[str]           # 用户最终选择的模块名


class ApiDefinitionList(BaseModel):
    """包装类：用于让 LLM 输出接口列表"""
    apis: List[ApiDefinition] = Field(..., description="提取到的所有接口定义列表")
