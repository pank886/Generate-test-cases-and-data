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
        "response_obj": None,
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
    # 新节点：thinking+json_mode 一步生成（只生成不落盘；失败时无 plan → 处理节点 requires_review）
    def _generate_plan_thinking_safe(state: dict) -> dict:
        try:
            return components._generate_excel_plan_thinking(state)
        except Exception:
            import logging
            logging.getLogger(__name__).warning(
                "一步生成失败，generate_excel_plan 将因缺少 plan 走 requires_review", exc_info=True)
            return state  # 保留 state；无 plan 时由处理节点标记人工审查
    builder.add_node("generate_plan_thinking", _generate_plan_thinking_safe)

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
    # 生成/处理解耦（2026-08 方案3）：
    #   generate_plan_thinking 只生成 plan 入 state → generate_excel_plan 纯处理（校验/修复/落盘）→ END
    #   thinking 失败（无 plan）→ 处理节点直接 requires_review，不降级自生成
    builder.add_edge("retrieve_related_data", "generate_plan_thinking")
    builder.add_edge("generate_plan_thinking", "generate_excel_plan")
    builder.add_edge("generate_excel_plan", END)
    # analyze_test_points_raw 旧链路兜底：见 changelog/2026-08-02_old_generation_fallback.md，暂未启用
    # （节点保留定义不连边；未来旧链路 = analyze 生成 plan → 处理节点）

    graph = builder.compile()
    return graph, components
