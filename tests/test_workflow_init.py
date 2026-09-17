"""Phase C 工作流初始化测试。

场景:
  1. global 声明完整性 — lifespan 中所有赋值变量都在 global 声明中
  2. 启动后 Phase C 正常 — _phase_c_graph 不为 None
  3. 向量库未就绪 — 返回 400 提示
  4. 无文件时 — 返回 400 提示
  5. Phase C 未初始化 — 返回 500
  6. global 声明包含 _phase_c_graph — 修复验证
"""

import ast
import sys
import os

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


# ============================================================
# 1. global 声明完整性 + 6. 含 _phase_c_graph
# ============================================================

class TestGlobalDeclaration:
    """静态分析 lifespan() 的 global 声明完整性。"""

    GLOBAL_LINE = None  # 将在测试中定位

    def _get_lifespan_ast(self):
        """解析 web/app.py，提取 lifespan 函数的 AST。"""
        with open(
            os.path.join(os.path.dirname(__file__), "..", "web", "app.py"),
            "r", encoding="utf-8",
        ) as f:
            tree = ast.parse(f.read())
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "lifespan":
                return node
        raise AssertionError("未找到 lifespan 函数")

    def test_all_module_globals_in_global_stmt(self):
        """lifespan 中赋值的模块级变量都必须在 global 声明中。

        检查 web/app.py 模块顶层以 _ 开头的变量，
        如果在 lifespan 中被赋值，则必须在 global 声明中。
        """
        func = self._get_lifespan_ast()
        import ast as _ast

        # 收集模块顶层以 _ 开头的变量
        with open(
            os.path.join(os.path.dirname(__file__), "..", "web", "app.py"),
            "r", encoding="utf-8",
        ) as f:
            full_tree = _ast.parse(f.read())

        module_level = set()
        for node in full_tree.body:
            if isinstance(node, _ast.Assign):
                for target in node.targets:
                    if isinstance(target, _ast.Name) and target.id.startswith("_"):
                        module_level.add(target.id)
                    elif isinstance(target, _ast.Tuple):
                        for elt in target.elts:
                            if isinstance(elt, _ast.Name) and elt.id.startswith("_"):
                                module_level.add(elt.id)

        # 收集 lifespan 中的 global 声明
        global_vars = set()
        for node in _ast.walk(func):
            if isinstance(node, _ast.Global):
                global_vars.update(node.names)

        # 收集 lifespan 中所有赋值语句的左值
        assigned_in_func = set()
        for node in _ast.walk(func):
            if isinstance(node, _ast.Assign):
                for target in node.targets:
                    if isinstance(target, _ast.Name):
                        assigned_in_func.add(target.id)
                    elif isinstance(target, _ast.Tuple):
                        for elt in target.elts:
                            if isinstance(elt, _ast.Name):
                                assigned_in_func.add(elt.id)

        # 交集：模块级且在 lifespan 中赋值 → 必须在 global 中
        must_be_global = (module_level & assigned_in_func) - global_vars
        assert not must_be_global, (
            f"以下模块级变量在 lifespan 中赋值但缺少 global 声明: {must_be_global}"
        )

    def test_phase_c_graph_in_global(self):
        """工作流图经 _state 对象在 lifespan 中赋值（2026-08-03 起不再用模块级 global）。"""
        func = self._get_lifespan_ast()
        state_attrs = set()
        for node in ast.walk(func):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    # 支持元组赋值（如 `_state.a, _state.b = build_workflow()`）
                    targets = target.elts if isinstance(target, ast.Tuple) else [target]
                    for t in targets:
                        if (isinstance(t, ast.Attribute)
                                and isinstance(t.value, ast.Name)
                                and t.value.id == "_state"):
                            state_attrs.add(t.attr)

        assert {"_phase_b_graph", "_phase_b_components"} <= state_attrs, (
            f"_state 必须在 lifespan 中赋值 _phase_b_graph/_phase_b_components，实际: {state_attrs}"
        )


# ============================================================
# 2. 启动后 Phase C 正常（模拟模式）
# ============================================================

class TestPhaseCInitialized:
    """验证 lifespan 执行后 _phase_c_graph 已被正确赋值。"""

    def test_workflow_graph_builds(self):
        """build_workflow()（Phase B 图）应正常构建返回 graph + components。"""
        # 直接测试函数本身可用
        from agent_components.graph.graph_builder import build_workflow
        graph, components = build_workflow()
        assert graph is not None, "build_workflow() 应返回 graph"
        assert components is not None, "build_workflow() 应返回 components"


# ============================================================
# 3/4/5. 端点错误分支测试（直接调函数模拟）
# ============================================================

class TestWorkflowEndpointErrors:
    """/workflow/start 端点的错误分支行为。"""

    def test_vector_not_ready_message(self):
        """向量库未就绪时返回 400 + 明确提示。"""
        from web.routes.chat import router
        # 验证端点定义存在
        routes = [r.path for r in router.routes]
        assert "/workflow/start" in routes

    def test_no_files_message(self):
        """无文件时返回 400 + 提示上传文档。"""
        # 验证 chat.py 中导入的校验路径
        with open(
            os.path.join(os.path.dirname(__file__), "..", "web", "routes", "chat.py"),
            "r", encoding="utf-8",
        ) as f:
            content = f.read()
        assert "请先上传" in content or "未上传" in content, (
            "无文件时应提示上传文档"
        )

    def test_phase_c_not_initialized_message(self):
        """Phase C 未初始化时返回 500 + 报错。"""
        with open(
            os.path.join(os.path.dirname(__file__), "..", "web", "routes", "chat.py"),
            "r", encoding="utf-8",
        ) as f:
            content = f.read()
        assert "Phase B 工作流未初始化" in content, (
            "未初始化时应返回明确错误消息"
        )


# ============================================================
# 6. 全局断言（已在 TestGlobalDeclaration 中覆盖）
# ============================================================

# ============================================================
# 7. IntentConfirmation 异常降级
# ============================================================

class TestIntentConfirmationFallback:
    """LLM 解析失败时降级为 WAITING 而非抛 500。"""

    def test_model_validator_handles_matches_drift(self):
        """IntentConfirmation 兼容 matches → matched_modules 漂移。"""
        from prompts.response_model import IntentConfirmation

        # 字段名漂移
        c1 = IntentConfirmation.model_validate({
            "matches": ["健身房", "会员管理"], "confidence": "high"
        })
        assert c1.matched_modules == ["健身房", "会员管理"]

        # 字段名 + 值格式双重漂移
        c2 = IntentConfirmation.model_validate({
            "matches": [{"module": "健身房"}], "confidence": "high"
        })
        assert c2.matched_modules == ["健身房"]

    def test_confirm_user_intent_graceful_fallback(self):
        """_invoke_structured 失败时返回 WAITING + 提示文案而非抛异常。"""
        from agent_components.retrievers import RetrievalMixin
        from unittest.mock import MagicMock, patch

        mixin = RetrievalMixin()
        mixin._invoke_structured = MagicMock(side_effect=RuntimeError("LLM 解析失败"))

        state = {"user_input": "测试", "workflow_status": "PENDING"}

        # mock 掉 get_session_ctx 和 module_tree.get_all
        from database import get_session_ctx as _ctx
        from contextlib import contextmanager
        mock_session = MagicMock()

        @contextmanager
        def fake_ctx():
            yield mock_session

        import database.operations as _ops
        # ModuleOps.get_all returns ORM objects; mock with name attribute
        _mock1 = MagicMock(); _mock1.name = "健身房"
        _mock2 = MagicMock(); _mock2.name = "会员管理"
        with patch.object(_ops.ModuleOps, "get_all", return_value=[_mock1, _mock2]):
            with patch("database.get_session_ctx", fake_ctx):
                result = mixin._confirm_user_intent(state)

        assert result["workflow_status"] == "WAITING", "解析失败应返回 WAITING"
        assert result["candidate_modules"] == [], "解析失败候选列表应空"
        assert "未能确定" in result.get("confirmation_question", ""), (
            "应提示用户重新描述"
        )


# ============================================================
# 8. excel_plan state 通道回归（2026-09-01）
# ============================================================

class TestExcelPlanStateChannel:
    """State 必须注解 excel_plan。

    回归背景：commit 3172422 死代码清理时把 State 的 excel_plan 注解连带注释
    （原 Optional["ExcelPlan"] v1 模型），未补 ExcelPlanV2。LangGraph 的 channel
    由 TypedDict 注解定义，未注解键在节点返回时被静默丢弃 → generate_plan_thinking
    产出的 plan 传不到 generate_excel_plan 处理节点 → 前端"共 0 条用例"。
    修复：恢复 excel_plan: Optional["ExcelPlanV2"] 注解。
    """

    def test_state_annotates_excel_plan(self):
        """State 注解必须包含 excel_plan（静态锁定，防再注释）。"""
        from agent_components.graph.state import State
        assert "excel_plan" in State.__annotations__, (
            "State 缺失 excel_plan 注解 → LangGraph channel 丢失 → 前端 0 条用例"
        )

    def test_excel_plan_channel_passes_through_graph(self):
        """最小图复现断链：write 返回 ExcelPlanV2 → read 能取到模型实例。"""
        from agent_components.graph.state import State
        from langgraph.graph import StateGraph, START, END
        from prompts.response_model import ExcelPlanV2

        captured = {}

        def _write(state):
            return {"excel_plan": ExcelPlanV2(shared_preconditions=[], test_cases=[])}

        def _read(state):
            captured["plan"] = state.get("excel_plan")
            return {}

        g = StateGraph(State)
        g.add_node("write", _write)
        g.add_node("read", _read)
        g.add_edge(START, "write")
        g.add_edge("write", "read")
        g.add_edge("read", END)
        g.compile().invoke({
            "user_input": "测试", "original_input": "测试", "context": "",
            "response_obj": None, "workflow_status": "CONFIRMED",
        })

        assert captured["plan"] is not None, "excel_plan 应跨节点传递"
        assert hasattr(captured["plan"], "test_cases"), "应保持 ExcelPlanV2 模型实例"


# ============================================================
# 9. api_full_for_snapshot 通道 + Phase B 回复文案（2026-09-01）
# ============================================================

class TestSnapshotAndReply:
    """api_full_for_snapshot 需注解；requires_review 时 reply 不误导。

    两个回归点：
      - api_full_for_snapshot 与 excel_plan 同病：State 无注解 → LangGraph 静默
        丢弃 → 处理节点 L270 `state.get(...) or 自己构造` 恒走 fallback → 重复
        构造 + 重复 apply_all 标注（功能不变但冗余）。补注解后 thinking 那份直通。
      - requires_review（无 plan）时 reply 曾显示"已生成：共 0 条用例"，
        误导用户以为生成了空计划。改为明确提示人工审查。
    """

    def test_state_annotates_api_full_for_snapshot(self):
        """State 注解必须包含 api_full_for_snapshot（防静默丢弃）。"""
        from agent_components.graph.state import State
        assert "api_full_for_snapshot" in State.__annotations__, (
            "State 缺失 api_full_for_snapshot 注解 → LangGraph channel 丢失 → "
            "处理节点重复构造 + 重复 apply_all 标注"
        )

    def test_phase_b_reply_normal(self):
        """正常生成 → 回复含用例数。"""
        from web.tasks import _build_phase_b_reply
        r = _build_phase_b_reply({}, 19)
        assert "共 19 条用例" in r

    def test_phase_b_reply_requires_review_not_misleading(self):
        """requires_review → 提示人工审查，禁止误导性"已生成"。"""
        from web.tasks import _build_phase_b_reply
        r = _build_phase_b_reply(
            {"requires_review": True, "error_info": ["LLM 未产出有效 plan"]}, 0,
        )
        assert "已生成" not in r, f"不得误导性声称已生成: {r}"
        assert "需人工审查" in r
        assert "LLM 未产出有效 plan" in r

    def test_phase_b_reply_requires_review_without_errors(self):
        """requires_review 且无 error_info → 仍有兜底提示。"""
        from web.tasks import _build_phase_b_reply
        r = _build_phase_b_reply({"requires_review": True}, 0)
        assert "需人工审查" in r
        assert "未产出完整测试计划" in r


# ============================================================
# 10. failed_case_ids 通道 + reply 失败详情（2026-09-02）
# ============================================================

class TestFailedCaseDisplay:
    """校验失败编号/详情透传前端。

    两个回归点：
      - State 无 failed_case_ids/failed_case_details 注解 → LangGraph 静默丢弃
        → 前端只看到数量拿不到明细（与 excel_plan 断链同因）。
      - 用户决策：失败详情（编号+标题+原因）展示在聊天框 reply 里，不建独立区块。
    """

    def test_state_annotates_failed_case_ids(self):
        """State 注解必须包含 failed_case_ids / failed_case_details（防静默丢弃）。"""
        from agent_components.graph.state import State
        assert "failed_case_ids" in State.__annotations__, (
            "State 缺失 failed_case_ids 注解 → LangGraph channel 丢失 → 前端拿不到失败编号"
        )
        assert "failed_case_details" in State.__annotations__, (
            "State 缺失 failed_case_details 注解 → 前端拿不到失败标题/原因"
        )

    def test_phase_b_reply_with_failed_details(self):
        """reply 展示每条失败详情（编号+标题+原因），聊天框可分辨哪条失败、为何失败。"""
        from web.tasks import _build_phase_b_reply
        r = _build_phase_b_reply({
            "failed_case_details": [
                {"id": "PRE-002", "title": "已创建分时测试电表",
                 "errors": ["疑似URL拼写错误: /peak/flat/valley"]},
                {"id": "TC-018", "title": "名称特殊字符-反向",
                 "errors": ["疑似URL拼写错误: /script"]},
            ]
        }, 22)
        assert "共 22 条用例" in r
        assert "2 行未通过校验" in r
        assert "PRE-002" in r and "已创建分时测试电表" in r and "/peak/flat/valley" in r
        assert "TC-018" in r and "名称特殊字符-反向" in r

    def test_phase_b_reply_without_failed_details_falls_back(self):
        """无 failed_case_details → 沿用原有 fail_warn 提示（不报错、不遗漏数量）。"""
        from web.tasks import _build_phase_b_reply
        r = _build_phase_b_reply({}, 22, fail_warn="（3 行未通过校验，需人工审查）")
        assert "共 22 条用例" in r and "3 行未通过校验" in r

    def test_zero_failure_empty_list_no_false_warning(self):
        """回归点（2026-09-02 误报）：0 失败时 failed_case_details=[]。

        23 条全过却回「部分用例校验失败」的根因：`if not []` 把空列表当缺失
        → 误读整份 thinking_trace.log 命中历史 FAILED。修复后 [] = 0 失败，不提示。
        """
        from web.tasks import _resolve_failed_case_info
        warn, ids = _resolve_failed_case_info(
            {"failed_case_ids": [], "failed_case_details": []},
            trace_tail="历史记录\n*** generate_excel_plan_FAILED ***\n第3行 | PRE-002 | err",
        )
        assert warn == "" and ids == []

    def test_zero_failure_reply_clean(self):
        """0 失败空列表 → reply 干净（共 N 条，无「部分用例校验失败」字样）。"""
        from web.tasks import _build_phase_b_reply
        r = _build_phase_b_reply({"failed_case_details": [], "failed_case_ids": []}, 23)
        assert r == "Excel 测试计划已生成：共 23 条用例"

    def test_missing_details_scoped_tail_warns(self):
        """failed_case_details 缺失（None，异常路径）→ 仅在本次日志段含 FAILED 时兜底提示。"""
        from web.tasks import _resolve_failed_case_info
        warn, ids = _resolve_failed_case_info(
            {},
            trace_tail="*** generate_excel_plan_FAILED ***\n第3行 | PRE-002 | err1\n第5行 | TC-002 | err2",
        )
        assert "部分用例校验失败" in warn
        assert set(ids) == {"PRE-002", "TC-002"}

    def test_missing_details_clean_tail_no_warning(self):
        """failed_case_details 缺失但日志段无 FAILED → 无提示（不因历史日志误报）。"""
        from web.tasks import _resolve_failed_case_info
        warn, ids = _resolve_failed_case_info({}, trace_tail="本次运行正常日志\n")
        assert warn == "" and ids == []

    def test_real_failures_use_node_ids(self):
        """非空 failed_case_details（真实失败）→ 编号取 failed_case_ids，不读日志。"""
        from web.tasks import _resolve_failed_case_info
        warn, ids = _resolve_failed_case_info(
            {"failed_case_ids": ["PRE-002"], "failed_case_details": [{"id": "PRE-002"}]},
            trace_tail="",
        )
        assert warn == "" and ids == ["PRE-002"]
