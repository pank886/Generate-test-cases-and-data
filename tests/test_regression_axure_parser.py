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
# 4. _extract_required_fields_from_html — 必填字段（红色星号）提取
# ============================================================

class TestExtractRequiredFields:

    def test_returns_empty_for_none(self):
        """html_path=None 返回空列表。"""
        assert AxureParser._extract_required_fields_from_html(None) == []

    def test_empty_file(self, tmp_path):
        """空文件返回空列表。"""
        f = tmp_path / "p.html"
        f.write_text("", encoding="utf-8")
        assert AxureParser._extract_required_fields_from_html(f) == []

    def test_single_field(self, tmp_path):
        """星号 + 后随标签 → 单个字段，去掉结尾冒号。"""
        f = tmp_path / "p.html"
        f.write_text(
            '<p><span style="color:#D9001B;">*</span><span>电表名称：</span></p>',
            encoding="utf-8",
        )
        assert AxureParser._extract_required_fields_from_html(f) == [
            {"field": "电表名称"}
        ]

    def test_multiple_fields_order_and_dedup(self, tmp_path):
        """多字段按出现顺序去重。"""
        f = tmp_path / "p.html"
        f.write_text(
            '<p><span style="color:#D9001B;">*</span><span>电表名称</span></p>'
            '<p><span style="color:#D9001B;">*</span><span>电表名称</span></p>'
            '<p><span style="color:#D9001B;">*</span><span>电表编号</span></p>',
            encoding="utf-8",
        )
        assert AxureParser._extract_required_fields_from_html(f) == [
            {"field": "电表名称"},
            {"field": "电表编号"},
        ]

    def test_fullwidth_star_and_entities(self, tmp_path):
        """全角星号 ＊ 与 &nbsp; 实体兼容。"""
        f = tmp_path / "p.html"
        f.write_text(
            '<p><span style="color:#D9001B;">＊</span><span>收费方式&nbsp;：</span></p>',
            encoding="utf-8",
        )
        assert AxureParser._extract_required_fields_from_html(f) == [
            {"field": "收费方式"}
        ]

    def test_uppercase_color(self, tmp_path):
        """颜色写法大小写 / 冒号后空格兼容。"""
        f = tmp_path / "p.html"
        f.write_text(
            '<p><span style="COLOR: #d9001b">*</span><span>安装位置</span></p>',
            encoding="utf-8",
        )
        assert AxureParser._extract_required_fields_from_html(f) == [
            {"field": "安装位置"}
        ]

    def test_overlong_label_skipped(self, tmp_path):
        """长度 >40 视为误抓跳过。"""
        f = tmp_path / "p.html"
        long_label = "很长的字段名" * 10
        f.write_text(
            '<p><span style="color:#D9001B;">*</span><span>%s</span></p>' % long_label,
            encoding="utf-8",
        )
        assert AxureParser._extract_required_fields_from_html(f) == []

    def test_parse_populates_required_fields(self, tmp_path):
        """parse() 端到端：必填字段写入 page_details[url]['required_fields']。"""
        import zipfile
        zip_path = tmp_path / "mini.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr(
                "data/sitemap.js",
                'var sitemap = {name:"proj", children:[{name:"p1", url:"p1.html"}]};',
            )
            zf.writestr(
                "p1.html",
                '<html><body>'
                '<p><span style="color:#D9001B;">*</span><span>电表名称：</span></p>'
                '<p><span style="color:#D9001B;">*</span><span>电表编号</span></p>'
                "</body></html>",
            )
        parsed = AxureParser(str(zip_path)).parse()
        pd = parsed["page_details"]
        assert "p1.html" in pd
        assert pd["p1.html"]["required_fields"] == [
            {"field": "电表名称"},
            {"field": "电表编号"},
        ]


# ============================================================
# 5. _parse_rp9_sitemap — RP9 document.js sitemap 树（父级路径）
# ============================================================

def _make_doc_js():
    """构造最小 RP9 document.js（压缩格式：_() 字典构造器 + 变量表）。"""
    return (
        'var _ = function(){var r={},a=arguments;for(var i=0;i<a.length;i+=2)r[a[i]]=a[i+1];return r;};'
        'var _creator = function(){return _(b,_(c,d,e,f,g),q,_(r,['
        '_(s,t,u,v,w,x,y,z),'
        '_(s,D,u,E,w,F,y,D,G,[_(s,H,u,I,w,x,y,J)]),'
        '_(s,A,u,B,w,x,y,C),'
        '_(s,D,u,K,w,F,y,D,G,[_(s,L,u,M,w,x,y,N),_(s,O,u,P,w,x,y,Q)])'
        ']));};'
        'var b="configuration",c="showPageNotes",d=true,e="showPageNoteNames",'
        'f=false,g="showAnnotations",q="sitemap",r="rootNodes",s="id",t="root1",'
        'u="pageName",v="概览",w="type",x="Wireframe",y="url",z="概览.html",'
        'A="page3",B="企业公摊生成",C="企业公摊生成.html",D="",E="用电配置",'
        'F="Folder",G="children",H="leaf1",I="计费规则",J="计费规则.html",'
        'K="结算配置",L="p1",M="企业结算配置",N="企业结算配置.html",'
        'O="p2",P="公寓结算配置",Q="公寓结算配置.html";'
    )


class TestRp9SitemapTree:

    def test_parses_hierarchy(self):
        """文件夹层级：url → 父级/页面名。"""
        result = AxureParser._parse_rp9_sitemap(_make_doc_js())
        assert result["概览.html"] == "概览"
        assert result["计费规则.html"] == "用电配置/计费规则"
        assert result["企业公摊生成.html"] == "企业公摊生成"
        assert result["企业结算配置.html"] == "结算配置/企业结算配置"
        assert result["公寓结算配置.html"] == "结算配置/公寓结算配置"

    def test_missing_rootnodes_returns_empty(self):
        """无 rootNodes 变量 → 返回空 dict。"""
        assert AxureParser._parse_rp9_sitemap('var x="foo";') == {}

    def test_malformed_returns_empty(self):
        """非 document.js 内容 → 返回空 dict，不抛异常。"""
        assert AxureParser._parse_rp9_sitemap("") == {}
        assert AxureParser._parse_rp9_sitemap("random garbage;") == {}

    def test_page_path_in_parse_output(self, tmp_path):
        """parse() 端到端：RP9 页面带 page_path（父级路径）。"""
        import zipfile
        zip_path = tmp_path / "mini_rp9.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("data/document.js", _make_doc_js())
            zf.writestr("概览.html", "<html><body>概览</body></html>")
            zf.writestr("计费规则.html",
                        '<html><body><p><span style="color:#D9001B;">*</span><span>收费类型：</span></p></body></html>')
            zf.writestr("企业公摊生成.html", "<html><body>公摊</body></html>")
            zf.writestr("企业结算配置.html", "<html><body>配置</body></html>")
            zf.writestr("公寓结算配置.html", "<html><body>配置</body></html>")
        parsed = AxureParser(str(zip_path)).parse()
        pd = parsed["page_details"]
        assert pd["计费规则.html"]["page_path"] == "用电配置/计费规则"
        assert pd["企业公摊生成.html"]["page_path"] == "企业公摊生成"
        assert pd["企业结算配置.html"]["page_path"] == "结算配置/企业结算配置"
        # 必填字段仍正常提取
        assert pd["计费规则.html"]["required_fields"] == [{"field": "收费类型"}]


# ============================================================
# 6. _has_unmatched_quotes — 引号配对（有重复定义需验证一致）
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


# ============================================================
# 7. 页面块四段：导航识别 / 弹窗提取 / 排除集 / 内嵌图片
# ============================================================

class TestFindNavContainer:

    def test_returns_container_id(self):
        """含 <a class=link> 的状态面板 → 返回容器 id。"""
        html = (
            '<div id="u53" class="ax_default ax_default_hidden">'
            '<div id="u53_state0" class="panel_state" data-label="状态 1">'
            '<a class="link" href="p.html">页面</a></div></div>'
            '<div id="u100"><p>正文</p></div>'
        )
        assert AxureParser._find_nav_container_id(html) == "u53"

    def test_no_link_returns_none(self):
        """无 <a class=link> → None。"""
        html = '<div id="u53"><div id="u53_state0" data-label="状态 1"><p>x</p></div></div>'
        assert AxureParser._find_nav_container_id(html) is None


class TestExtractDialogs:

    _HIDDEN = '<div id="u103" class="ax_default ax_default_hidden" style="display:none">'

    def _html(self, inner):
        return self._HIDDEN + inner + "</div>"

    def test_meaningful_label_included(self):
        """业务标签（历史电量）即使无表单标记也视为弹窗。"""
        html = self._html(
            '<div id="u103_state0" class="panel_state" data-label="历史电量">'
            '<div id="u103_state0_content"><p>查询</p></div></div>'
        )
        dialogs = AxureParser._extract_dialogs_from_html(html, "电表管理")
        assert len(dialogs) == 1
        assert dialogs[0]["state"] == "历史电量"
        assert dialogs[0]["title"] == "电表管理/历史电量"

    def test_state_n_with_marker_uses_heuristic_title(self):
        """占位标签 State N + 表单标记（新增）→ 内容启发式标题。"""
        html = self._html(
            '<div id="u354_state0" class="panel_state" data-label="State 1">'
            '<div id="u354_state0_content"><p>*收费类型： 新增收费配置</p></div></div>'
        )
        dialogs = AxureParser._extract_dialogs_from_html(html, "用电配置/计费规则")
        assert len(dialogs) == 1
        assert dialogs[0]["title"] == "用电配置/计费规则/新增收费配置"

    def test_state_n_without_marker_skipped(self):
        """占位标签 State N 且无表单标记 → 跳过。"""
        html = self._html(
            '<div id="u103_state1" class="panel_state" data-label="State 1">'
            '<div id="u103_state1_content"><p>无表单标记占位</p></div></div>'
        )
        assert AxureParser._extract_dialogs_from_html(html, "电表管理") == []

    def test_nested_panel_state_not_treated_as_dialog(self):
        """嵌套子面板（u137_state0 在 u103_state0 内）不误判为独立弹窗。"""
        html = self._html(
            '<div id="u103_state0" class="panel_state" data-label="添加">'
            '<div id="u103_state0_content"><p>确定</p><p>取消</p>'
            '<div id="u137_state0" class="panel_state" data-label="State1">'
            '<div id="u137_state0_content"><p>嵌套子面板</p></div></div>'
            "</div></div>"
        )
        dialogs = AxureParser._extract_dialogs_from_html(html, "电表管理")
        assert len(dialogs) == 1
        assert dialogs[0]["state"] == "添加"

    def test_dialog_extracts_required_and_filters(self):
        """弹窗块内含 ②必填 + ③筛选。"""
        html = self._html(
            '<div id="u103_state0" class="panel_state" data-label="添加">'
            '<div id="u103_state0_content">'
            '<p><span style="color:#D9001B;">*</span><span>电表名称：</span></p>'
            '<select><option>请选择</option><option>A</option><option>B</option></select>'
            "</div></div>"
        )
        d = AxureParser._extract_dialogs_from_html(html, "电表管理")[0]
        assert [f["field"] for f in d["required_fields"]] == ["电表名称"]
        assert d["filters"][0]["options"] == ["A", "B"]


class TestStripContainer:

    def test_strips_balanced_div(self):
        """配平剥离整个容器（含嵌套）。"""
        html = '<div id="u53"><div><p>内</p></div></div>保留'
        result = AxureParser._strip_container(html, "u53")
        assert "内" not in result
        assert "保留" in result

    def test_unknown_id_unchanged(self):
        """未知 id → 原样返回。"""
        html = '<div id="u53"><p>x</p></div>'
        assert AxureParser._strip_container(html, "u999") == html

    def test_balanced_end_nested(self):
        """_find_div_balanced_end 支持嵌套。"""
        html = '<div id="a"><div><p>x</p></div></div>tail'
        end = AxureParser._find_div_balanced_end(html, 0)
        assert html[end:] == "tail"


class TestExplanationExclusion:

    def test_excludes_containers(self):
        """排除集容器整体从 ④ 中剥离（html 字符串入参）。"""
        html = (
            '<html><body>'
            '<div id="u53"><a class="link" href="p.html">导航</a><p>树结构内容</p></div>'
            '<div id="u103" class="ax_default_hidden"><p>弹窗内容</p></div>'
            '<p>查询表单说明</p><p>需求规格</p>'
            "</body></html>"
        )
        result = AxureParser._extract_page_explanation(html, exclude_ids=["u53", "u103"])
        assert "查询表单说明" in result
        assert "需求规格" in result
        assert "树结构内容" not in result
        assert "弹窗内容" not in result

    def test_no_exclusion_all_copy(self):
        """无排除集 → 全量复制（html 字符串入参）。"""
        html = '<html><body><p>全部内容</p></body></html>'
        result = AxureParser._extract_page_explanation(html)
        assert "全部内容" in result


class TestTopBarTrim:

    #: 含导航的页面：导航面板 + 顶栏散件 + post-nav 簇 + 弹窗 + 内容
    _NAV_HTML = (
        '<div id="base">'
        '<div id="u0" class="ax_default _二级标题"><div id="u0_text">运营管理平台</div></div>'
        '<div id="u53" class="ax_default">'
        '<div id="u53_state0" class="panel_state"><a class="link" href="p.html">页面</a></div>'
        "</div>"
        '<div id="u81" class="ax_default box_2"><div id="u81_text">资源中心</div></div>'
        '<div id="u85" class="ax_default _文本段落">'
        '<div id="u85_text">智慧用电 / 电表管理</div></div>'
        '<div id="u88" class="ax_default _三级标题"><div id="u88_text">工作台</div></div>'
        '<div id="u103" class="ax_default ax_default_hidden">'
        '<div id="u103_state0" class="panel_state" data-label="State 1">'
        '<div id="u103_state0_content"><p>弹窗内容</p></div></div></div>'
        '<div id="u288" class="ax_default _段落"><div id="u288_text">需求规格正文</div></div>'
        "</div>"
    )

    #: 各 widget 的 top 坐标（模板不变式：导航 nav_top=107，面包屑 117，post-nav 簇 ≤144）
    _TOPS = {"u0": 24, "u53": 107, "u81": 82, "u85": 117, "u88": 24,
             "u103": 107, "u288": 107}

    def test_pre_nav_and_post_nav_cluster_trimmed(self):
        """顶栏（导航前 + 导航后簇）剔除，弹窗排除，内容保留。"""
        result = AxureParser._extract_page_explanation(
            self._NAV_HTML, exclude_ids=["u53", "u103"], nav_panel_id="u53",
            tops=self._TOPS, nav_top=107,
        )
        assert "需求规格正文" in result
        assert "弹窗内容" not in result
        # 顶栏散件全部剔除（导航前 + 导航后簇）
        for word in ("运营管理平台", "资源中心", "工作台", "智慧用电 / 电表管理"):
            assert word not in result, f"顶栏文字残留: {word}"

    def test_no_nav_keeps_all(self):
        """无导航 → 不做任何截断，全量保留。"""
        html = '<html><body><p>全部内容</p></body></html>'
        result = AxureParser._extract_page_explanation(html)
        assert "全部内容" in result

    def test_anomalous_nav_top_degrades_to_trim_only(self):
        """nav_top 异常（≥500）→ 跳过 post-nav 簇剔除，仅导航前截断。"""
        result = AxureParser._extract_page_explanation(
            self._NAV_HTML, exclude_ids=["u53"], nav_panel_id="u53",
            tops=self._TOPS, nav_top=999,
        )
        # 导航前顶栏仍被截断，post-nav 簇保留（位置不可信时保守不删）
        assert "运营管理平台" not in result
        assert "需求规格正文" in result


class TestPanelMerge:

    def test_panel_states_merged_into_one_block(self):
        """每面板 1 块：面板内多状态合并（②③④），标题取默认状态内容标题。"""
        html = (
            '<div id="u1935" class="ax_default ax_default_hidden" style="display:none">'
            '<div id="u1935_state0" class="panel_state" data-label="账户记录">'
            '<div id="u1935_state0_content">'
            '<div id="u2000" class="ax_default _文本段落"><div id="u2000_text">账户明细</div></div>'
            '<p><span style="color:#D9001B;">*</span><span>充值类型：</span></p>'
            "</div></div>"
            '<div id="u1935_state1" class="panel_state" data-label="扣款记录">'
            '<div id="u1935_state1_content">'
            '<p><span style="color:#D9001B;">*</span><span>扣款类型：</span></p>'
            '<select><option>请选择</option><option>自用电</option><option>公摊用电</option></select>'
            "</div></div>"
            "</div>"
        )
        dialogs = AxureParser._extract_dialogs_from_html(html, "企业余额")
        assert len(dialogs) == 1
        d = dialogs[0]
        assert d["title"] == "企业余额/账户明细"
        # 两状态必填合并
        fields = {f["field"] for f in d["required_fields"]}
        assert fields == {"充值类型", "扣款类型"}
        # 筛选合并（来自 state1）
        assert {"自用电", "公摊用电"} <= set(d["filters"][0]["options"])


class TestEmbeddedImages:

    def test_extracts_local_imgs_excludes_remote(self):
        """提取内嵌图片，排除 data: 与 http(s)。"""
        html = (
            '<img src="images/a.png">'
            '<img src="data:image/png;base64,xxx">'
            '<img src="http://x.com/b.png">'
            '<img src="images/c.svg">'
        )
        assert AxureParser._extract_embedded_images(html) == ["images/a.png", "images/c.svg"]

    def test_empty(self):
        """无图片 → 空列表。"""
        assert AxureParser._extract_embedded_images("<p>x</p>") == []


class TestStringExtractionCores:

    def test_required_fields_string(self):
        """字符串级必填提取。"""
        html = '<p><span style="color:#D9001B;">*</span><span>电表名称：</span></p>'
        assert AxureParser._extract_required_fields(html) == [{"field": "电表名称"}]

    def test_filters_string(self):
        """字符串级筛选提取。"""
        html = ('<p><span>计费方案：</span></p>'
                '<select><option>请选择</option><option>分时</option><option>固定</option></select>')
        res = AxureParser._extract_filters(html)
        assert res == [{"field": "计费方案", "options": ["分时", "固定"]}]


class TestChunksMainPlusDialogs:

    def test_to_product_doc_chunks_emits_dialog_blocks(self, tmp_path):
        """主块 + 弹窗子块均输出，弹窗块标题含弹窗名。"""
        import zipfile
        zip_path = tmp_path / "mini_dlg.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr(
                "data/sitemap.js",
                'var sitemap = {name:"proj", children:[{name:"p1", url:"p1.html"}]};',
            )
            zf.writestr(
                "p1.html",
                '<html><body>'
                '<p><span style="color:#D9001B;">*</span><span>主字段：</span></p>'
                '<div id="u103" class="ax_default ax_default_hidden" style="display:none">'
                '<div id="u103_state0" class="panel_state" data-label="添加">'
                '<div id="u103_state0_content">'
                '<p><span style="color:#D9001B;">*</span><span>弹窗字段：</span></p>'
                "</div></div></div>"
                "</body></html>",
            )
        parsed = AxureParser(str(zip_path)).parse()
        dialogs = parsed["page_details"]["p1.html"]["dialogs"]
        assert len(dialogs) == 1
        assert dialogs[0]["title"] == "p1/添加"
        chunks = AxureParser(str(zip_path)).to_product_doc_chunks(parsed)
        names = {c["page_name"] for c in chunks}
        assert "p1" in names
        assert "p1/添加" in names

    def test_image_only_page_chunk_omits_path_line(self, tmp_path):
        """仅有图片页面：chunk 无「路径:」无用文本，仅保留页面标签（前端据此渲染原图）。"""
        import zipfile
        zip_path = tmp_path / "mini_img.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr(
                "data/sitemap.js",
                'var sitemap = {name:"proj", children:[{name:"图页", url:"img.html"}, {name:"文页", url:"text.html"}]};',
            )
            # 图页：无可提取内容，仅内嵌 <img>
            zf.writestr(
                "img.html",
                '<html><body><img src="images/a.png"></body></html>',
            )
            # 文页：有必填字段（对照：保留完整头 + 路径 + body 段）
            zf.writestr(
                "text.html",
                '<html><body><p><span style="color:#D9001B;">*</span><span>名称：</span></p></body></html>',
            )
        parsed = AxureParser(str(zip_path)).parse()
        chunks = AxureParser(str(zip_path)).to_product_doc_chunks(parsed)
        by_name = {c["page_name"]: c["content"] for c in chunks}
        img_chunk = by_name["图页"]
        assert "路径" not in img_chunk
        assert "###" not in img_chunk
        assert img_chunk.startswith("## 页面: 图页")
        text_chunk = by_name["文页"]
        assert "路径" in text_chunk
        assert "### 必填字段" in text_chunk
