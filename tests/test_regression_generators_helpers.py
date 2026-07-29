"""生成器辅助函数回归测试。

覆盖:
  - _summarize_error_patterns — 错误分类聚合
  - _extract_completion_snippet — LLM 原始输出提取
  - _format_post_issues_for_prompt — 后校验问题格式化
  - _sanitize_en — 英文标识符清洗
  - _parse_assertion — 断言关键词解析
  - _has_unmatched_quotes — 引号配对检测

运行方式:
  pytest tests/test_regression_generators_helpers.py -v
"""

import os
import sys
import json
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ============================================================
# 1. _summarize_error_patterns
# ============================================================

class TestSummarizeErrorPatterns:
    """generators.py:32 _summarize_error_patterns"""

    def test_empty_list_returns_none_stat(self):
        """空列表返回'（无统计）'。"""
        from agent_components.generators import _summarize_error_patterns
        result = _summarize_error_patterns([])
        assert "无统计" in result

    def test_b1_double_braces(self):
        """B1 双花括号错误识别。"""
        from agent_components.generators import _summarize_error_patterns
        failures = [{"error": "双花括号 {{}} 未解析"}]
        result = _summarize_error_patterns(failures)
        assert "B1" in result
        assert "1 处" in result

    def test_b2_placeholder_unclosed(self):
        """B2 占位符未闭合错误识别。"""
        from agent_components.generators import _summarize_error_patterns
        failures = [{"error": "占位符未闭合或嵌套"}]
        result = _summarize_error_patterns(failures)
        assert "B2" in result

    def test_b3_unknown_function(self):
        """B3 非注册表函数错误识别。"""
        from agent_components.generators import _summarize_error_patterns
        failures = [{"error": "未知占位符函数 'foo'"}]
        result = _summarize_error_patterns(failures)
        assert "B3" in result

    def test_b4_wrong_arg_count(self):
        """B4 实参不合规错误识别。"""
        from agent_components.generators import _summarize_error_patterns
        failures = [{"error": "实参个数 5 超出范围"}]
        result = _summarize_error_patterns(failures)
        assert "B4" in result

    def test_b5_string_validation_error(self):
        """B5/B10 字符串校验错误识别。"""
        from agent_components.generators import _summarize_error_patterns
        failures = [{"error": "Input should be a valid string"}]
        result = _summarize_error_patterns(failures)
        assert "B5" in result or "B10" in result

    def test_b6_empty_list(self):
        """B6/B7 空列表错误识别。"""
        from agent_components.generators import _summarize_error_patterns
        failures = [{"error": "at least 1 item required"}]
        result = _summarize_error_patterns(failures)
        assert "B6" in result or "B7" in result

    def test_b8_unknown_fallback(self):
        """未匹配任何关键词 → B8 兜底。"""
        from agent_components.generators import _summarize_error_patterns
        failures = [{"error": "完全未知的错误信息 XYZ"}]
        result = _summarize_error_patterns(failures)
        assert "B8" in result

    def test_b9_three_way_conflict(self):
        """B9 json/params/data 并存错误识别。"""
        from agent_components.generators import _summarize_error_patterns
        failures = [{"error": "json/params/data 三选一"}]
        result = _summarize_error_patterns(failures)
        assert "B9" in result

    def test_mixed_errors_aggregated(self):
        """混合错误 — 多种模式同时出现时各自计数。"""
        from agent_components.generators import _summarize_error_patterns
        failures = [
            {"error": "双花括号 {{}} 未解析"},
            {"error": "双花括号 {{}} 再次出现"},
            {"error": "未知占位符函数 'bar'"},
        ]
        result = _summarize_error_patterns(failures)
        assert "B1" in result
        assert "B3" in result


# ============================================================
# 2. _extract_completion_snippet
# ============================================================

class TestExtractCompletionSnippet:
    """generators.py:50 _extract_completion_snippet"""

    def test_extract_from_langchain_error(self):
        """从 LangChain 异常中提取 completion 片段。"""
        from agent_components.generators import _extract_completion_snippet
        err = (
            'Failed to parse JSON from completion {"json": {"data": [1,2,3]}}. '
            'Got: Extra data: line 1 column 5'
        )
        result = _extract_completion_snippet(err)
        assert '{"json"' in result

    def test_no_completion_in_error(self):
        """无 completion 关键字时返回原文本前 500 字符。"""
        from agent_components.generators import _extract_completion_snippet
        err = "Something went wrong with no completion block"
        result = _extract_completion_snippet(err)
        assert "Something went wrong" in result

    def test_truncation_default_limit(self):
        """默认 500 字符截断。"""
        from agent_components.generators import _extract_completion_snippet
        long_text = "A" * 1000
        result = _extract_completion_snippet(long_text)
        assert len(result) <= 500

    def test_custom_limit(self):
        """自定义截断长度。"""
        from agent_components.generators import _extract_completion_snippet
        long_text = "B" * 500
        result = _extract_completion_snippet(long_text, limit=100)
        assert len(result) <= 100


# ============================================================
# 3. _format_post_issues_for_prompt
# ============================================================

class TestFormatPostIssuesForPrompt:
    """generators.py:76 _format_post_issues_for_prompt"""

    def test_empty_issues_returns_empty(self):
        """空列表返回空字符串。"""
        from agent_components.generators import _format_post_issues_for_prompt
        result = _format_post_issues_for_prompt([])
        assert result == ""

    def test_none_returns_empty(self):
        """None 返回空字符串。"""
        from agent_components.generators import _format_post_issues_for_prompt
        result = _format_post_issues_for_prompt(None)
        assert result == ""

    def test_single_issue_formatted(self):
        """单个问题含 check/current/expected/fix_hint。"""
        from agent_components.generators import _format_post_issues_for_prompt
        issues = [{
            "check": "delete_body_wrapper",
            "yaml_path": "test_data.yaml",
            "current": "json: {body: [3 items]}",
            "expected": "json: [3 items]",
            "fix_hint": "去掉 body 包裹层",
        }]
        result = _format_post_issues_for_prompt(issues)
        assert "delete_body_wrapper" in result
        assert "test_data.yaml" in result
        assert "去掉 body 包裹层" in result

    def test_multiple_issues_enumerated(self):
        """多个问题按序号排列。"""
        from agent_components.generators import _format_post_issues_for_prompt
        issues = [
            {"check": "A", "yaml_path": "a.yaml",
             "current": "x", "expected": "x'", "fix_hint": "fix A"},
            {"check": "B", "yaml_path": "b.yaml",
             "current": "y", "expected": "y'", "fix_hint": "fix B"},
        ]
        result = _format_post_issues_for_prompt(issues)
        assert "1." in result
        assert "2." in result


# ============================================================
# 4. _sanitize_en（静态方法 → 抽取后可能变模块函数）
# ============================================================

class TestSanitizeEn:
    """generators.py:307 _sanitize_en — 已由 test_phase_bc_unit.py 覆盖，
    此处仅补充回归验证。"""

    def test_alphanumeric_preserved(self):
        """字母数字下划线保留。"""
        from agent_components.generators import GenerationMixin
        result = GenerationMixin._sanitize_en("DeviceManagement")
        assert result == "DeviceManagement"

    def test_special_chars_removed(self):
        """特殊字符去除。"""
        from agent_components.generators import GenerationMixin
        result = GenerationMixin._sanitize_en("设备-管理 (v2)")
        assert "-" not in result
        assert "(" not in result

    def test_spaces_replaced(self):
        """空格替换为下划线。"""
        from agent_components.generators import GenerationMixin
        result = GenerationMixin._sanitize_en("Device Management")
        assert "_" in result
        assert " " not in result

    def test_digit_start_prepended(self):
        """数字开头补下划线。"""
        from agent_components.generators import GenerationMixin
        result = GenerationMixin._sanitize_en("2FA Login")
        assert result.startswith("_")


# ============================================================
# 5. _parse_assertion
# ============================================================

class TestParseAssertion:
    """generators.py:431 _parse_assertion — 已部分覆盖，补充回归。"""

    def test_eq_keyword_returns_eq(self):
        """[eq] 返回 ('eq', rest)。"""
        from agent_components.generators import GenerationMixin
        keyword, rest = GenerationMixin._parse_assertion("[eq] 返回码=0 成功")
        assert keyword == "eq"
        assert "返回码=0" in rest

    def test_contains_keyword(self):
        """[contains] 返回 ('contains', rest)。"""
        from agent_components.generators import GenerationMixin
        keyword, rest = GenerationMixin._parse_assertion("[contains] 包含'成功'")
        assert keyword == "contains"
        assert "成功" in rest

    def test_missing_keyword_raises(self):
        """无关键词抛 AssertionParseError。"""
        from agent_components.generators import GenerationMixin
        with pytest.raises(GenerationMixin.AssertionParseError):
            GenerationMixin._parse_assertion("没有方括号关键词")

    def test_case_insensitive_keyword(self):
        """大小写不敏感。"""
        from agent_components.generators import GenerationMixin
        keyword, rest = GenerationMixin._parse_assertion("[EQ] 返回码=0")
        assert keyword == "eq"

    def test_ne_keyword(self):
        """[ne] 不等于。"""
        from agent_components.generators import GenerationMixin
        keyword, rest = GenerationMixin._parse_assertion("[ne] 返回码!=0")
        assert keyword == "ne"


# ============================================================
# 6. _has_unmatched_quotes（YamlPostValidator）
# ============================================================

class TestHasUnmatchedQuotes:
    """post_validator.py:153 _has_unmatched_quotes"""

    def test_matched_quotes(self):
        """配对引号返回 False。"""
        from agent_components.post_validator import YamlPostValidator
        assert YamlPostValidator._has_unmatched_quotes("'hello'") is False
        assert YamlPostValidator._has_unmatched_quotes('"world"') is False

    def test_unmatched_single_quote(self):
        """不配对单引号返回 True。"""
        from agent_components.post_validator import YamlPostValidator
        assert YamlPostValidator._has_unmatched_quotes("it's a test") is True

    def test_unmatched_double_quote(self):
        """不配对双引号返回 True。"""
        from agent_components.post_validator import YamlPostValidator
        assert YamlPostValidator._has_unmatched_quotes('"broken') is True

    def test_no_quotes(self):
        """无引号返回 False。"""
        from agent_components.post_validator import YamlPostValidator
        assert YamlPostValidator._has_unmatched_quotes("no quotes here") is False

    def test_escaped_quotes_not_counted(self):
        """转义引号不计入。"""
        from agent_components.post_validator import YamlPostValidator
        assert YamlPostValidator._has_unmatched_quotes(
            "it\\'s a test"  # 无配对的 '
        ) is False
