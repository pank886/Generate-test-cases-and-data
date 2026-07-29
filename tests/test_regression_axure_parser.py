"""AxureParser 拆分回归测试 — HTML 解析纯函数验证。

运行方式:
  pytest tests/test_regression_axure_parser.py -v
"""

import os
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent_components.axure_parser import AxureParser


# ============================================================
# 1. _clean_html_to_text — HTML 清洗
# ============================================================

class TestCleanHtmlToText:

    def test_strips_script_tags(self):
        """移除 script 标签内容。"""
        html = "<html><body>Hello<script>alert('xss')</script> World</body></html>"
        result = AxureParser._clean_html_to_text(html)
        assert "Hello" in result
        assert "World" in result
        assert "alert" not in result

    def test_strips_style_tags(self):
        """移除 style 标签内容。"""
        html = "<html><head><style>.red{color:red}</style></head><body>Text</body></html>"
        result = AxureParser._clean_html_to_text(html)
        assert "Text" in result
        assert ".red" not in result

    def test_extracts_data_label(self):
        """提取 data-label 属性。"""
        html = '<html><body><div data-label="用户名">输入框</div></body></html>'
        result = AxureParser._clean_html_to_text(html)
        assert "[元素] 用户名" in result

    def test_extracts_panel_states(self):
        """提取 data-ax-state 动态面板状态。"""
        html = '<div data-ax-state="展开状态">面板内容</div>'
        result = AxureParser._clean_html_to_text(html)
        assert "展开状态" in result

    def test_extracts_repeater_data(self):
        """提取 data-ax-repeater 中继器数据。"""
        html = '<div data-ax-repeater="用户列表">列表</div>'
        result = AxureParser._clean_html_to_text(html)
        assert "用户列表" in result

    def test_br_converted_to_newline(self):
        """<br> 转换为换行。"""
        html = "<body>Line1<br>Line2<br/>Line3</body>"
        result = AxureParser._clean_html_to_text(html)
        assert "\n" in result

    def test_empty_html(self):
        """空 HTML 不报错。"""
        result = AxureParser._clean_html_to_text("")
        assert isinstance(result, str)

    def test_no_body_tag(self):
        """无 body 标签时返回全文。"""
        html = "<p>Simple paragraph</p>"
        result = AxureParser._clean_html_to_text(html)
        assert "Simple paragraph" in result


# ============================================================
# 2. _extract_brace_content — 嵌套括号提取
# ============================================================

class TestExtractBraceContent:

    def test_simple_brace(self):
        """简单单层括号。"""
        result = AxureParser._extract_brace_content(
            "registerCaseInfo({code: '001', name: 'test'})", 0
        )
        assert "code" in result
        assert "name" in result

    def test_nested_brace(self):
        """嵌套括号 — 正确匹配层级。"""
        result = AxureParser._extract_brace_content(
            "outer({inner({deep: 1}), shallow: 2})", 0
        )
        assert "inner" in result
        assert "shallow" in result

    def test_nested_function_calls(self):
        """嵌套函数调用括号 — 不被内层 ) 截断。"""
        result = AxureParser._extract_brace_content(
            "fn({a: getData(1, 2), b: getMore(3, 4)})", 0
        )
        assert "getData(1, 2)" in result
        assert "getMore(3, 4)" in result

    def test_no_opening_brace(self):
        """无左括号时返回空。"""
        result = AxureParser._extract_brace_content("no braces here", 0)
        assert result == ""

    def test_multiple_sibling_braces(self):
        """多个同级括号组 — 只提取从 start_pos 开始的那组。"""
        result = AxureParser._extract_brace_content(
            "first({1,2}) second({3,4})", 0
        )
        assert "1,2" in result
        assert "3,4" not in result

    def test_from_offset_start(self):
        """从非零位置开始搜索括号。"""
        text = "prefix registerCaseInfo({code: '001'}) suffix"
        # 从 "register" 开始
        pos = text.index("register")
        result = AxureParser._extract_brace_content(text, pos)
        assert "code" in result


# ============================================================
# 3. _extract_ui_text_from_html — HTML 文件文本提取
# ============================================================

class TestExtractUiTextFromHtml:

    def test_returns_empty_for_none(self):
        """html_path=None 返回空字符串。"""
        result = AxureParser._extract_ui_text_from_html(None)
        assert result == ""

    def test_extracts_from_valid_file(self, tmp_path):
        """从有效 HTML 文件提取文本。"""
        html_file = tmp_path / "page.html"
        html_file.write_text(
            '<html><body>'
            '<div data-label="保存按钮">保存</div>'
            '</body></html>',
            encoding="utf-8",
        )
        result = AxureParser._extract_ui_text_from_html(html_file)
        assert "[元素] 保存按钮" in result
        assert "保存" in result


# ============================================================
# 4. _has_unmatched_quotes — 引号配对（有重复定义需验证一致）
# ============================================================

class TestAxureImportConsistency:
    """验证拆分后 AxureParser 的公开接口一致。"""

    def test_all_static_methods_present(self):
        """关键静态方法存在。"""
        assert hasattr(AxureParser, "_clean_html_to_text")
        assert hasattr(AxureParser, "_extract_brace_content")
        assert hasattr(AxureParser, "_extract_ui_text_from_html")

    def test_clean_html_to_text_callable(self):
        """方法可调用。"""
        result = AxureParser._clean_html_to_text("<body>test</body>")
        assert "test" in result
