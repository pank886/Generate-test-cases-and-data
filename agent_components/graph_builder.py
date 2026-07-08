"""LangGraph 图构建"""
from types import SimpleNamespace
from langgraph.graph import StateGraph, START, END

import config
from agent_components.state import State
from agent_components.nodes import ChatTestAgentGraph


def build_and_run_agent(db_path: str = None):
    """
    构建并返回智能测试助手的 LangGraph 应用

    Args:
        db_path: 向量数据库路径，默认使用 config.CHROMA_DB_DIR

    Returns:
        chat(user_input: str) -> response_obj
    """
    components = ChatTestAgentGraph(db_path=db_path or config.CHROMA_DB_DIR)

    # 构建图
    builder = StateGraph(State)

    builder.add_node("retrieve", lambda state: components._retrieve_node(state))
    builder.add_node("parse_api", lambda state: components._parse_api_node(state))
    builder.add_node(
        "generate_excel_plan",
        lambda state: components._generate_excel_plan_node(state),
    )
    # 连接节点（PY/YAML 生成在用户确认后执行）
    builder.add_edge(START, "retrieve")
    builder.add_edge("retrieve", "parse_api")
    builder.add_edge("parse_api", "generate_excel_plan")
    builder.add_edge("generate_excel_plan", END)

    graph = builder.compile()

    def chat(user_input: str):
        """执行一次完整的测试流程"""
        initial_state = {
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
        }
        result = graph.invoke(initial_state)
        resp = result.get("response_obj")
        # 把状态中的额外数据附加到响应对象上
        if not resp:
            resp = SimpleNamespace()
            plan = result.get("excel_plan")
            case_count = len(plan.rows) if plan and hasattr(plan, "rows") else 0
            resp.proper_thinking = [f"已提取 {len(result.get('api_definition_list', []))} 个接口"]
            resp.final_response = f"Excel 测试计划已生成：共 {case_count} 条用例"
        resp.excel_path = result.get("excel_path")
        resp.excel_plan = result.get("excel_plan")
        resp.api_definition_list = result.get("api_definition_list")
        resp.original_input = result.get("original_input")
        resp.output_dir = result.get("output_dir")
        return resp

    # 挂载 components 实例供外部调用（如 /confirm-plan 生成 .py 文件）
    chat.components = components
    return chat


def _make_initial_state(user_input: str) -> dict:
    """构建 Phase C 工作流初始状态。"""
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
        # Phase C 多跳检索
        "product_docs": None,
        "related_modules": None,
        "api_definitions": None,
        "test_points": None,
        # Phase C 多轮对话
        "candidate_modules": None,
        "confirmation_question": None,
        "workflow_status": "PENDING",
        "confirmed_module": None,
    }


def _build_result_response(result: dict):
    """从 graph invoke 结果构建响应对象。"""
    resp = SimpleNamespace()
    plan = result.get("excel_plan")
    case_count = len(plan.rows) if plan and hasattr(plan, "rows") else 0
    tps = (result.get("test_points") or {}).get("test_points", [])
    tp_count = len(tps) if isinstance(tps, list) else 0
    resp.proper_thinking = [f"分析出 {tp_count} 个测试点"]

    if result.get("requires_review"):
        errs = result.get("error_info", [])
        resp.final_response = (
            f"测试分析完成：{tp_count} 个测试点，但 Excel 计划校验失败（需人工审查）。\n"
            f"错误: {'; '.join('- ' + e for e in errs[:5])}"
        )
        if len(errs) > 5:
            resp.final_response += f"\n... 共 {len(errs)} 个错误"
    else:
        resp.final_response = f"测试分析完成：{tp_count} 个测试点, Excel 计划 {case_count} 条用例"

    resp.excel_path = result.get("excel_path")
    resp.excel_plan = result.get("excel_plan")
    resp.api_definition_list = result.get("api_definition_list")
    resp.original_input = result.get("original_input")
    resp.output_dir = result.get("output_dir")
    resp.test_points = result.get("test_points")
    resp.requires_review = result.get("requires_review", False)
    resp.error_info = result.get("error_info", [])
    return resp


def build_new_workflow(db_path: str = None):
    """构建三段式多跳检索工作流（Phase C），支持 LangGraph 条件中断。

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
      analyze_test_points     (节点5)
           ▼
      bridge_api_defs         (节点6)
           ▼
      generate_excel_plan
           ▼
          END

    Args:
        db_path: 向量数据库路径

    Returns:
        (graph, components) 元组，供 API 层管理多轮会话
    """
    from agent_components.state import State
    components = ChatTestAgentGraph(db_path=db_path or config.CHROMA_DB_DIR)

    builder = StateGraph(State)

    builder.add_node("confirm_intent", lambda state: components._confirm_user_intent(state))
    builder.add_node("retrieve_product_docs", lambda state: components._retrieve_product_docs(state))
    builder.add_node("extract_related_modules", lambda state: components._extract_related_modules(state))
    builder.add_node("retrieve_related_data", lambda state: components._retrieve_related_data(state))
    builder.add_node("analyze_test_points", lambda state: components._analyze_test_points(state))
    builder.add_node("bridge_api_defs", lambda state: components._prepare_excel_plan_data(state))
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
    builder.add_edge("retrieve_related_data", "analyze_test_points")
    builder.add_edge("analyze_test_points", "bridge_api_defs")
    builder.add_edge("bridge_api_defs", "generate_excel_plan")
    builder.add_edge("generate_excel_plan", END)

    graph = builder.compile()
    return graph, components
