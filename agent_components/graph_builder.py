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


def build_new_workflow(db_path: str = None):
    """
    构建新的多跳检索测试工作流（Phase C）

    Args:
        db_path: 向量数据库路径

    Returns:
        chat(user_input: str) -> response_obj
    """
    components = ChatTestAgentGraph(db_path=db_path or config.CHROMA_DB_DIR)

    builder = StateGraph(State)

    builder.add_node("retrieve_product_docs", lambda state: components._retrieve_product_docs(state))
    builder.add_node("extract_related_modules", lambda state: components._extract_related_modules(state))
    builder.add_node("retrieve_related_data", lambda state: components._retrieve_related_data(state))
    builder.add_node("analyze_test_points", lambda state: components._analyze_test_points(state))
    builder.add_node("bridge_api_defs", lambda state: components._prepare_excel_plan_data(state))
    builder.add_node("generate_excel_plan", lambda state: components._generate_excel_plan_node(state))

    builder.add_edge(START, "retrieve_product_docs")
    builder.add_edge("retrieve_product_docs", "extract_related_modules")
    builder.add_edge("extract_related_modules", "retrieve_related_data")
    builder.add_edge("retrieve_related_data", "analyze_test_points")
    builder.add_edge("analyze_test_points", "bridge_api_defs")
    builder.add_edge("bridge_api_defs", "generate_excel_plan")
    builder.add_edge("generate_excel_plan", END)

    graph = builder.compile()

    def chat(user_input: str):
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
            # Phase C fields
            "product_docs": None,
            "related_modules": None,
            "api_definitions": None,
            "test_points": None,
        }
        result = graph.invoke(initial_state)
        resp = result.get("response_obj")
        if not resp:
            resp = SimpleNamespace()
            plan = result.get("excel_plan")
            case_count = len(plan.rows) if plan and hasattr(plan, "rows") else 0
            tps = (result.get("test_points") or {}).get("test_points", [])
            tp_count = len(tps) if isinstance(tps, list) else 0
            resp.proper_thinking = [f"分析出 {tp_count} 个测试点"]

            # 检查是否需要人工审查
            if result.get("requires_review"):
                errs = result.get("error_info", [])
                resp.final_response = f"测试分析完成：{tp_count} 个测试点，但 Excel 计划校验失败（需人工审查）。\\n错误: {chr(10).join('- ' + e for e in errs[:5])}"
                if len(errs) > 5:
                    resp.final_response += f"\\n... 共 {len(errs)} 个错误"
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

    chat.components = components
    return chat
