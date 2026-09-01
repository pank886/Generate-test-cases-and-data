"""拆分后导入烟雾测试 — 验证所有关键 import 路径可用。

运行方式:
  pytest tests/test_regression_import_smoke.py -v
"""

import os
import sys
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ============================================================
# 1. prompts.response_model — 拆分后可能变 package
# ============================================================

class TestResponseModelImports:
    """P1-4: response_model.py 拆分后的导入验证。"""

    def test_import_testdata_models(self):
        """TestData / StepData / TestCase 可导入。"""
        from prompts.response_model import TestData, StepData, TestCase
        assert TestData is not None
        assert StepData is not None
        assert TestCase is not None

    def test_import_plan_models(self):
        """ExcelPlanV2 / DependencyMap 可导入（DataPlanStep 已注释：真死代码）。"""
        from prompts.response_model import (
            ExcelPlanV2, DependencyMap,  # DataPlanStep 已注释（真死代码）
        )
        assert ExcelPlanV2 is not None
        assert DependencyMap is not None
        # assert DataPlanStep is not None  # 已注释（真死代码）

    def test_import_api_models(self):
        """ApiDefinition / ApiDefExtract 可导入。"""
        from prompts.response_model import ApiDefinition, ApiDefExtract
        assert ApiDefinition is not None
        assert ApiDefExtract is not None

    def test_import_translation_models(self):
        """TranslationResult 可导入。"""
        from prompts.response_model import TranslationResult
        assert TranslationResult is not None

    def test_import_support_models(self):
        """TestCaseRow / SharedPrecondition / StoryDependencyMap 可导入。"""
        from prompts.response_model import (
            TestCaseRow, SharedPrecondition, StoryDependencyMap,
            InternalDependency, CrossModuleDep,
        )
        assert TestCaseRow is not None
        assert SharedPrecondition is not None
        assert StoryDependencyMap is not None

    def test_import_validation_interceptor(self):
        """ValidationInterceptor 可导入。"""
        from prompts.response_model import ValidationInterceptor
        assert ValidationInterceptor is not None


# ============================================================
# 2. agent_components.generators — 拆分后可能变 package
# ============================================================

class TestGeneratorsImports:
    """P2-9: generators.py 拆分后的导入验证。"""

    def test_import_generation_mixin(self):
        """GenerationMixin 可导入。"""
        from agent_components.generators import GenerationMixin
        assert GenerationMixin is not None

    def test_import_helper_functions(self):
        """模块级辅助函数可导入。"""
        from agent_components.generators import (
            _summarize_error_patterns,
            _extract_completion_snippet,
            _format_post_issues_for_prompt,
        )
        assert _summarize_error_patterns is not None
        assert _extract_completion_snippet is not None
        assert _format_post_issues_for_prompt is not None


# ============================================================
# 3. agent_components.graph.nodes — LangGraph 子包
# ============================================================

class TestNodesImports:
    """P2-8: nodes.py 拆分后的导入验证。"""

    def test_import_chat_test_agent_graph(self):
        """ChatTestAgentGraph 可导入。"""
        from agent_components.graph.nodes import ChatTestAgentGraph
        assert ChatTestAgentGraph is not None

    def test_import_reload_llm(self):
        """reload_llm 可导入。"""
        from agent_components.graph.nodes import reload_llm
        assert reload_llm is not None


# ============================================================
# 4. database.operations — 拆分后可能变 package
# ============================================================

class TestOperationsImports:
    """P1-5: operations.py 拆分后的导入验证。"""

    def test_import_all_ops_classes(self):
        """DocOps / ModuleOps / BindingOps / GlossaryOps 可导入。"""
        from database.operations import DocOps, ModuleOps, BindingOps, GlossaryOps
        assert DocOps is not None
        assert ModuleOps is not None
        assert BindingOps is not None
        assert GlossaryOps is not None


# ============================================================
# 5. ingest_v2 — 拆分后可能变 package
# ============================================================

class TestIngestImports:
    """P1-6: ingest_v2.py 拆分后的导入验证。"""

    def test_import_top_level_functions(self):
        """顶层处理函数可导入。"""
        from ingest_v2 import (
            process_product_doc, process_api_doc_extract,
            process_axure_zip, commit_api_docs,
        )
        assert process_product_doc is not None
        assert process_api_doc_extract is not None
        assert process_axure_zip is not None
        assert commit_api_docs is not None

    def test_import_internal_helpers(self):
        """内部辅助函数可导入。"""
        from ingest_v2 import _safe_doc_id, _save_to_sqlite, _extract_text
        assert _safe_doc_id is not None
        assert _save_to_sqlite is not None
        assert _extract_text is not None


# ============================================================
# 6. agent_components.axure — 拆分后可能变 package
# ============================================================

class TestAxureImports:
    """P1-?: axure_parser.py 拆分后的导入验证。"""

    def test_import_axure_parser(self):
        """AxureParser 可导入。"""
        from agent_components.axure_parser import AxureParser
        assert AxureParser is not None


# ============================================================
# 7. 其余关键模块导入
# ============================================================

class TestOtherKeyImports:
    """其他模块的导入验证。"""

    def test_import_post_validator(self):
        """YamlPostValidator 可导入。"""
        from agent_components.validation.yaml_validator import YamlPostValidator
        assert YamlPostValidator is not None

    def test_import_dual_chroma(self):
        """DualChromaDB 可导入。"""
        from infrastructure.vector_store.dual_chroma import DualChromaDB, get_chroma_db
        assert DualChromaDB is not None
        assert get_chroma_db is not None

    def test_import_data_factory(self):
        """数据工厂注册表可导入。"""
        from data_factory.registry import get_validation_rules
        assert get_validation_rules is not None

    def test_import_relocated_prompt_functions(self):
        """迁移后模块级 prompt 函数可导入（PromptFactory 已并入 extraction_prompts.py）。"""
        from prompts.extraction_prompts import (
            generate_excel_plan_thinking_prompt,
            confirm_user_intent_prompt,
        )
        assert generate_excel_plan_thinking_prompt is not None
        assert confirm_user_intent_prompt is not None

    def test_import_config(self):
        """config 模块可导入。"""
        import infrastructure.config as config
        assert config.BASE_DIR is not None
        assert hasattr(config, "PYCHARM_MISC")

    def test_import_observability(self):
        """observability 模块可导入。"""
        from infrastructure.observability import get_logger
        assert get_logger is not None

    def test_import_settings(self):
        """settings 模块可导入。"""
        from infrastructure.settings import settings
        assert settings is not None
