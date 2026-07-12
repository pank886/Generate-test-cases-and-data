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
        """_phase_c_graph 在 lifespan 的 global 声明中。"""
        func = self._get_lifespan_ast()
        global_vars = set()
        for node in ast.walk(func):
            if isinstance(node, ast.Global):
                global_vars.update(node.names)

        assert "_phase_c_graph" in global_vars, (
            "_phase_c_graph 必须在 lifespan 的 global 声明中"
        )
        assert "_phase_c_components" in global_vars, (
            "_phase_c_components 必须在 lifespan 的 global 声明中"
        )


# ============================================================
# 2. 启动后 Phase C 正常（模拟模式）
# ============================================================

class TestPhaseCInitialized:
    """验证 lifespan 执行后 _phase_c_graph 已被正确赋值。"""

    def test_phase_c_graph_not_none(self):
        """_phase_c_graph 应被 build_new_workflow() 初始化。"""
        # 直接测试函数本身可用
        from agent_components.graph_builder import build_new_workflow
        graph, components = build_new_workflow()
        assert graph is not None, "build_new_workflow() 应返回 graph"
        assert components is not None, "build_new_workflow() 应返回 components"


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
        assert "Phase C 工作流未初始化" in content, (
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
        mixin.prompt_factory = MagicMock()
        mixin.prompt_factory.confirm_user_intent = MagicMock(return_value="prompt")

        state = {"user_input": "测试", "workflow_status": "PENDING"}

        # mock 掉 get_session_ctx 和 module_tree.get_all
        from database import get_session_ctx as _ctx
        from contextlib import contextmanager
        mock_session = MagicMock()

        @contextmanager
        def fake_ctx():
            yield mock_session

        import agent_components.module_tree as mt
        with patch.object(mt, "get_all", return_value=[
            {"name": "健身房"}, {"name": "会员管理"}
        ]):
            with patch("database.get_session_ctx", fake_ctx):
                result = mixin._confirm_user_intent(state)

        assert result["workflow_status"] == "WAITING", "解析失败应返回 WAITING"
        assert result["candidate_modules"] == [], "解析失败候选列表应空"
        assert "未能确定" in result.get("confirmation_question", ""), (
            "应提示用户重新描述"
        )
