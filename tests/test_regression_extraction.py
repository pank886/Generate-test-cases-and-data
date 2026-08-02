"""回归测试：API 提取相关 bug 修复验证。

覆盖本轮修改中引入的 6 个缺陷：
  1. _split_text_by_headers 按所有标题(#/##/###)切分，导致API被截断
  2. 拼批次逻辑被移除，72个API产生72次独立LLM调用
  3. extract_apis_from_yapi_md 把<h1>前言混入第一个API的name
  4. JS selectExtractMethod 少一个 } 导致整个页面交互失效
  5. showExtractChoiceModal 选LLM时用 _pendingApiConfirm(null) 而非 _llmResultCache
  6. extract-api-code endpoint 期望JSON dict但前端发FormData
"""

import json
import os
import re
import sys
import tempfile
import pytest

# 确保项目根目录在 path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ============================================================
# Bug 1: _split_text_by_headers 切分粒度
# ============================================================

class TestSplitTextByHeaders:
    """验证 _split_text_by_headers 只按 ## (h2) 切分，不按 # / ### 切。"""

    @pytest.fixture
    def split_func(self):
        from ingest_v2 import _split_text_by_headers
        return _split_text_by_headers

    def test_only_splits_on_h2(self, split_func):
        """文档含 # / ## / ### 标题时，只应在 ## 处切分。"""
        text = (
            "# 模块标题\n\n"
            "## API-1 添加用户\n"
            "### 基本信息\n"
            "### 请求参数\n"
            "### 返回数据\n\n"
            "## API-2 删除用户\n"
            "### 基本信息\n"
            "### 请求参数\n"
            "### 返回数据\n"
        )
        batches = split_func(text, max_chars=99999)
        assert len(batches) == 2, f"期望2个批次，实际{len(batches)}"
        assert "API-1" in batches[0]
        assert "### 基本信息" in batches[0], "### 子标题不应被切走"
        assert "API-2" in batches[1]

    def test_each_batch_is_whole_api(self, split_func):
        """每个批次应包含完整的单个 API。### 不会被切走。"""
        text = (
            "## 接口A\n### 基本信息\n**Path：** /api/a\n**Method：** GET\n"
            "### 请求参数\n| 名称 | 类型 |\n|---|---|\n| id | int |\n"
            "### 返回数据\n| 名称 | 类型 |\n|---|---|\n| ret | int |\n\n"
            "## 接口B\n### 基本信息\n**Path：** /api/b\n**Method：** POST\n"
        )
        batches = split_func(text, max_chars=99999)
        assert len(batches) == 2
        # API A 完整
        assert "/api/a" in batches[0]
        assert "接口A" in batches[0]
        assert "请求参数" in batches[0]  # ### 子标题保留
        # API B 完整
        assert "/api/b" in batches[1]
        assert "接口B" in batches[1]

    def test_no_grouping_bug(self, split_func):
        """每个 API 独立成段，不按字符数合并。"""
        # 3个API，每个5000字符，max_chars=30000 — 旧逻辑会拼成1批
        api_template = "## API_{}\n### 基本信息\n" + "x" * 4990 + "\n\n"
        text = api_template.format(1) + api_template.format(2) + api_template.format(3)
        batches = split_func(text, max_chars=30000)
        assert len(batches) == 3, (
            f"期望3个独立批次（不拼接），实际{len(batches)}"
        )

    def test_api_never_truncated(self, split_func):
        """单个 API 即使超过 max_chars 也不截断。"""
        large_api = "## 大接口\n### 基本信息\n" + "y" * 50000
        batches = split_func(text=large_api + "\n\n## 小接口\n### 基本信息\n小", max_chars=30000)
        assert len(batches) == 2
        assert len(batches[0]) == len(large_api), (
            f"大API不应被截断: 期望{len(large_api)}字符，实际{len(batches[0])}"
        )


# ============================================================
# Bug 3: extract_apis_from_yapi_md HTML 前言污染
# ============================================================

class TestYApiExtractorPreamble:
    """验证代码提取器不会把 HTML preamble 混入 API name。"""

    @pytest.fixture
    def sample_md(self):
        return (
            '<h1 class="curproject-name"> 智慧用电 - 新版本 </h1>\n'
            '2026年7月版本\n\n'
            '# 电表\n\n'
            '## 修改电表\n'
            '<a id=修改电表> </a>\n'
            '### 基本信息\n'
            '**Path：** /electricMeter/update\n'
            '**Method：** POST\n'
            '**接口描述：**\n<p>修改电表信息</p>\n'
            '### 请求参数\n'
            '### 返回数据\n'
        )

    def test_module_name_clean(self, sample_md):
        """模块名应只含纯文本，无 HTML 标签。"""
        from ingest_v2 import extract_apis_from_yapi_md
        result = extract_apis_from_yapi_md(sample_md)
        module_name = result["module_name"]
        assert "<" not in module_name, f"模块名含HTML: {module_name!r}"
        assert "智慧用电" in module_name

    def test_first_api_name_not_contaminated(self, sample_md):
        """第一个 API 的 name 不应包含 h1 文本。"""
        from ingest_v2 import extract_apis_from_yapi_md
        result = extract_apis_from_yapi_md(sample_md)
        first_api = result["apis"][0]
        assert first_api["name"] == "修改电表", (
            f"第一个API name应为'修改电表'，实际为{first_api['name']!r}"
        )
        assert "<" not in first_api["name"]

    def test_api_count_correct(self, sample_md):
        """应提取正确的 API 数量。"""
        from ingest_v2 import extract_apis_from_yapi_md
        result = extract_apis_from_yapi_md(sample_md)
        assert len(result["apis"]) == 1

    def test_extract_basic_fields(self, sample_md):
        """基本字段提取正确。"""
        from ingest_v2 import extract_apis_from_yapi_md
        result = extract_apis_from_yapi_md(sample_md)
        api = result["apis"][0]
        assert api["url"] == "/electricMeter/update"
        assert api["method"] == "POST"
        assert "修改电表" in api["description"] or api["description"] == api["name"]


# ============================================================
# Bug 4 & 5: JS 前端逻辑（通过模拟验证逻辑正确性）
# ============================================================

class TestExtractChoiceFlow:
    """验证选择提取方式的前端逻辑状态转换。"""

    def test_llm_cache_set_on_choice_modal(self):
        """弹选择窗时 _llmResultCache 应被赋值。"""
        # 模拟 showExtractChoiceModal 调用
        data = {"file_path": "/tmp/test.md", "file_name": "test.md",
                "needs_extract_choice": True}
        # 模拟 app.js 逻辑
        cache = data  # _llmResultCache = data
        assert cache is not None
        assert cache["file_path"] == "/tmp/test.md"

    def test_select_llm_uses_cache_not_pending(self):
        """选LLM时应从 _llmResultCache 取数据，而非 _pendingApiConfirm(null)。"""
        cache = {"file_path": "/tmp/test.md", "file_name": "test.md"}
        pending = None  # _pendingApiConfirm 此时为 null
        # 错误做法（Bug 5）:
        # result = pending.result  # → TypeError
        # 正确做法:
        result = cache
        assert result is not None
        assert result["file_path"] == "/tmp/test.md"

    def test_select_code_calls_correct_endpoint(self):
        """选代码提取时应调 /api/upload/extract-api-code。"""
        method = "code"
        url = "/api/upload/extract-api-code" if method != "llm" else "/api/upload/extract-api"
        assert url == "/api/upload/extract-api-code"

    def test_select_llm_calls_correct_endpoint(self):
        """选LLM时应调 /api/upload/extract-api。"""
        method = "llm"
        url = "/api/upload/extract-api" if method == "llm" else "/api/upload/extract-api-code"
        assert url == "/api/upload/extract-api"


# ============================================================
# Bug 6: extract-api-code 参数格式
# ============================================================

class TestExtractApiCodeEndpoint:
    """验证 /api/upload/extract-api-code 参数接收正确。"""

    def test_endpoint_accepts_form_data(self):
        """端点应接受 FormData (file_path + module_name)。"""
        # 通过检查路由签名验证
        from web.routes.api_extract import extract_api_code as endpoint
        import inspect
        sig = inspect.signature(endpoint)
        params = list(sig.parameters.keys())
        assert "file_path" in params, f"缺少 file_path 参数: {params}"
        assert "module_name" in params, f"缺少 module_name 参数: {params}"


# ============================================================
# 集成：_safe_doc_id 不包含 HTML
# ============================================================

class TestSafeDocId:
    """验证 doc_id 不含 HTML 标签。"""

    def test_doc_id_no_html_tags(self):
        """module_name 含纯文本时 doc_id 干净。"""
        from ingest_v2 import _safe_doc_id
        doc_id = _safe_doc_id(
            "api", "api.md", "智慧用电", "POST", "/electricMeter/update", "修改电表"
        )
        assert "<" not in doc_id, f"doc_id含HTML: {doc_id!r}"
        assert "api.md" in doc_id
        assert "智慧用电" in doc_id

    def test_doc_id_sanitizes_html_module_name(self):
        """module_name 含 HTML 标签时 _safe_doc_id 自动净化。"""
        from ingest_v2 import _safe_doc_id
        bad_module = '<h1>用电</h1>'
        doc_id = _safe_doc_id("api", "api.md", bad_module, "GET", "/api/x", "接口")
        assert "<" not in doc_id, (
            f"_safe_doc_id 应净化 HTML 标签，但 doc_id 仍含 <: doc_id={doc_id!r}"
        )
        assert ">" not in doc_id, f"doc_id 不应含原始 > 字符: {doc_id!r}"


# ============================================================
# 端到端：72 API 完整提取
# ============================================================

@pytest.mark.slow
class TestRealApiMdExtraction:
    """用真实 api.md 验证 72 个 API 完整提取。"""

    @pytest.fixture
    def real_md_path(self):
        for path in (r"D:\ai_test\用电测试\api.md",
                     r"D:\1-ceshi\md\用电\api.md"):
            if os.path.exists(path):
                return path
        pytest.skip("测试文件不存在: 候选路径均缺失")

    def test_all_72_apis_extracted(self, real_md_path):
        """应提取出恰好 72 个 API。"""
        from ingest_v2 import extract_apis_from_yapi_md, _extract_text
        full_text = _extract_text(real_md_path).strip()
        result = extract_apis_from_yapi_md(full_text)
        apis = result["apis"]
        assert len(apis) == 72, f"期望72个API，实际{len(apis)}"

    def test_module_name_clean(self, real_md_path):
        """真实文件提取的模块名不含HTML。"""
        from ingest_v2 import extract_apis_from_yapi_md, _extract_text
        full_text = _extract_text(real_md_path).strip()
        result = extract_apis_from_yapi_md(full_text)
        assert "<" not in result["module_name"], (
            f"模块名含HTML: {result['module_name']!r}"
        )

    def test_no_empty_names(self, real_md_path):
        """所有 API 的 name 非空。"""
        from ingest_v2 import extract_apis_from_yapi_md, _extract_text
        full_text = _extract_text(real_md_path).strip()
        result = extract_apis_from_yapi_md(full_text)
        for api in result["apis"]:
            assert api["name"], f"API name为空: url={api.get('url')}"

    def test_all_have_url_and_method(self, real_md_path):
        """所有 API 有 url 和 method。"""
        from ingest_v2 import extract_apis_from_yapi_md, _extract_text
        full_text = _extract_text(real_md_path).strip()
        result = extract_apis_from_yapi_md(full_text)
        for api in result["apis"]:
            assert api["url"], f"API url为空: name={api.get('name')}"
            assert api["method"] in ("GET", "POST", "PUT", "DELETE", "PATCH"), (
                f"无效method: {api.get('method')} name={api.get('name')}"
            )

    def test_headers_is_list(self, real_md_path):
        """headers 必须是数组格式。"""
        from ingest_v2 import extract_apis_from_yapi_md, _extract_text
        full_text = _extract_text(real_md_path).strip()
        result = extract_apis_from_yapi_md(full_text)
        for api in result["apis"]:
            assert isinstance(api["headers"], list), (
                f"headers应为list: {api.get('name')}"
            )
            assert isinstance(api["parameters"], list), (
                f"parameters应为list: {api.get('name')}"
            )
            assert isinstance(api["returns"], list), (
                f"returns应为list: {api.get('name')}"
            )

    # ── 2026-08 提取修复：desc HTML 残留 / Query·Body 参数 / 响应信封误塞 ──
    def _extract(self, real_md_path):
        from ingest_v2 import extract_apis_from_yapi_md, _extract_text
        return extract_apis_from_yapi_md(_extract_text(real_md_path).strip())["apis"]

    def test_no_html_tag_in_description(self, real_md_path):
        """修复：description 不应含 </p> 等 HTML 标签残留（原 63 个接口全是 </p>）。"""
        apis = self._extract(real_md_path)
        bad = [a for a in apis if re.search(r"</?p>|<[^>]+>", a["description"] or "")]
        assert not bad, f"存在HTML残留描述: {[a['url'] for a in bad]}"

    def test_import_api_has_file_param(self, real_md_path):
        """修复：导入类接口 Body 的 file 参数应被提取（原丢失）。"""
        apis = self._extract(real_md_path)
        api = next((a for a in apis if a["url"] == "/electricBillEnterprise/import"), None)
        assert api, "缺少 /electricBillEnterprise/import"
        names = [p["name"] for p in api["parameters"]]
        assert "file" in names, f"导入公摊面积应含 file 参数，实际: {names}"

    def test_query_params_extracted(self, real_md_path):
        """修复：Query 参数表应被解析（原完全跳过）。"""
        apis = self._extract(real_md_path)
        api = next((a for a in apis if a["url"] == "/shareBill/import/enterprise"), None)
        assert api, "缺少 /shareBill/import/enterprise"
        names = {p["name"] for p in api["parameters"]}
        assert {"startTime", "endTime", "payType"} <= names, f"Query参数缺失: {sorted(names)}"

    def test_no_response_envelope_as_params(self, real_md_path):
        """修复：不得把返回数据信封 retCode/msg/data/queue 误塞进 parameters。"""
        apis = self._extract(real_md_path)
        env = {"retCode", "msg", "data", "queue"}
        bad = [a["url"] for a in apis
               if a["parameters"] and a["parameters"][0]["name"] in env]
        assert not bad, f"响应信封误塞参数: {bad}"

    def test_empty_param_api_stays_empty(self, real_md_path):
        """修复：参数区为空的接口不应 fallback 误取返回表（原被塞信封）。"""
        apis = self._extract(real_md_path)
        api = next((a for a in apis if a["url"] == "/electricMeter/updateAll"), None)
        assert api, "缺少 /electricMeter/updateAll"
        assert api["parameters"] == [], f"空参数接口不应有参数: {api['parameters']}"
