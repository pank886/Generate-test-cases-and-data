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
