"""LangGraph 工作流状态定义"""
from typing import Optional, TypedDict, List

from prompts.response_model import ProperResponse, ExcelPlan


class State(TypedDict):
    """LangGraph 工作流的全局状态，在各个节点间传递"""

    user_input: str
    original_input: str
    context: str
    response_obj: "ProperResponse"
    excel_plan: Optional["ExcelPlan"]
    excel_path: Optional[str]
    output_dir: Optional[str]  # 本次生成的输出目录
    requires_review: Optional[bool]  # generate_excel_plan 重试耗尽时标记需人工审查
    error_info: Optional[list]      # 审查相关的错误信息列表

    # --- Phase B 多跳检索 + 测试点分析 ---
    product_docs: Optional[List[dict]]       # Hop 1: 产品文档检索结果
    related_modules: Optional[List[str]]     # 提取出的关联模块列表
    api_definitions: Optional[List[dict]]    # Hop 2b: 接口定义检索结果
    test_point_analysis: Optional[str]       # analyze_test_points_raw 输出的自由文本分析报告

    # --- Phase B 多轮对话 ---
    candidate_modules: Optional[List[str]]    # 节点1 LLM 匹配的候选模块名
    confirmation_question: Optional[str]      # 给用户看的确认提示文本
    workflow_status: str                      # "PENDING" → "WAITING" → "CONFIRMED"
    confirmed_module: Optional[str]           # 用户最终选择的模块名
