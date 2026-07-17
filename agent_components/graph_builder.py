"""LangGraph 图构建"""
from langgraph.graph import StateGraph, START, END

from agent_components.state import State
from agent_components.nodes import ChatTestAgentGraph


def _make_initial_state(user_input: str) -> dict:
    """构建 Phase B 工作流初始状态。"""
    return {
        "user_input": user_input,
        "original_input": user_input,
        "context": "",
        "chat_history": [],
        "response_obj": None,
        "api_definition_list": None,
        "test_data": None,
        "excel_plan": None,
        "excel_path": None,
        "output_dir": None,
        # Phase B 多跳检索
        "product_docs": None,
        "related_modules": None,
        "api_definitions": None,
        "test_point_analysis": None,
        # Phase B 多轮对话
        "candidate_modules": None,
        "confirmation_question": None,
        "workflow_status": "PENDING",
        "confirmed_module": None,
    }


def build_workflow():
    """构建多跳检索工作流（Phase B），支持 LangGraph 条件中断。

    工作流结构:
      confirm_intent (节点1)
           │
      ┌────▼────┐
      │ WAITING │ → END (挂起，等待用户确认)
      │CONFIRMED│ → 继续执行
      └────┬────┘
           ▼
      retrieve_product_docs  (节点2)
           │
      ┌────▼────┐
      │ NO_DATA │ → END (无数据，提示用户导入)
      │ 有数据  │ → 继续执行
      └────┬────┘
           ▼
      extract_related_modules (节点3)
           ▼
      retrieve_related_data   (节点4)
           ▼
      analyze_test_points_raw (节点5)
           ▼
      generate_excel_plan
           ▼
          END

    Returns:
        (graph, components) 元组，供 API 层管理多轮会话
    """
    from agent_components.state import State
    components = ChatTestAgentGraph()

    builder = StateGraph(State)

    builder.add_node("confirm_intent", lambda state: components._confirm_user_intent(state))
    builder.add_node("retrieve_product_docs", lambda state: components._retrieve_product_docs(state))
    builder.add_node("extract_related_modules", lambda state: components._extract_related_modules(state))
    builder.add_node("retrieve_related_data", lambda state: components._retrieve_related_data(state))
    builder.add_node("analyze_test_points_raw", lambda state: components._analyze_test_points_raw(state))
    builder.add_node("generate_excel_plan", lambda state: components._generate_excel_plan_node(state))

    def _route_after_intent(state: dict) -> str:
        if state.get("workflow_status") == "WAITING":
            return "wait"
        return "continue"

    def _route_after_product_docs(state: dict) -> str:
        if state.get("workflow_status") == "NO_DATA":
            return "no_data"
        return "continue"

    builder.add_edge(START, "confirm_intent")
    builder.add_conditional_edges(
        "confirm_intent",
        _route_after_intent,
        {"wait": END, "continue": "retrieve_product_docs"},
    )
    builder.add_conditional_edges(
        "retrieve_product_docs",
        _route_after_product_docs,
        {"no_data": END, "continue": "extract_related_modules"},
    )
    builder.add_edge("extract_related_modules", "retrieve_related_data")
    builder.add_edge("retrieve_related_data", "analyze_test_points_raw")
    builder.add_edge("analyze_test_points_raw", "generate_excel_plan")
    builder.add_edge("generate_excel_plan", END)

    graph = builder.compile()
    return graph, components
