"""Phase B 接口检索/去重单元测试

覆盖 2026-08 修复（存储反转后 API 检索塌缩）：
1. _parse_api_prefix：从自然语言检索文本前缀解析 method/url/name
2. _search_api_defs：正文非 JSON → 从 metadata / 前缀解析，不再塌缩成 {"raw": ...}
3. _dedup_api_defs：method/url 缺失时按 doc_id 兜底，不同接口绝不塌缩
"""
import os
import sys
from unittest.mock import Mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from langchain_core.documents import Document

from agent_components.retrievers import (
    RetrievalMixin,
    _parse_api_prefix,
    _dedup_api_defs,
)


# ============================================================
# 1. _parse_api_prefix 前缀解析
# ============================================================

class TestParseApiPrefix:
    def test_standard(self):
        assert _parse_api_prefix(
            "POST /api/login 登录。用户登录。参数: username string 必填 用户名。"
        ) == ("POST", "/api/login", "登录")

    def test_no_name(self):
        assert _parse_api_prefix("GET /api/user") == ("GET", "/api/user", "")

    def test_method_question(self):
        """method 缺失时 _build_api_search_text 写 '?'，应跳过 method 仍解析 url。"""
        assert _parse_api_prefix("? /api/login 登录。") == ("", "/api/login", "登录")

    def test_no_method_no_url(self):
        assert _parse_api_prefix("登录。/api/x") == ("", "", "登录")

    def test_empty(self):
        assert _parse_api_prefix("") == ("", "", "")
        assert _parse_api_prefix("   ") == ("", "", "")

    def test_http_url(self):
        assert _parse_api_prefix("POST http://example.com/api 登录。") == (
            "POST", "http://example.com/api", "登录")


# ============================================================
# 2. _search_api_defs 从 ChromaDB 结果构造 dict（mock dual_chroma）
# ============================================================

class TestSearchApiDefs:
    @staticmethod
    def _make_retriever():
        class _R(RetrievalMixin):
            pass
        r = _R()
        r.dual_chroma = Mock()
        return r

    def test_api_search_text_parses_method_url(self):
        """核心回归：api_search_text 正文（自然语言）→ 解析出 method/url，不再塌缩成 {"raw"}。"""
        r = self._make_retriever()
        r.dual_chroma.search_api_defs.return_value = [
            Document(
                page_content="POST /api/login 登录。用户登录。参数: username",
                metadata={"doc_id": "d1", "api_name": "登录"},
            ),
        ]
        apis = r._search_api_defs("登录", doc_ids=["d1"])
        assert len(apis) == 1
        a = apis[0]
        assert a["method"] == "POST"
        assert a["url"] == "/api/login"
        assert a["name"] == "登录"
        assert a["source"] == "d1"  # doc_id 保留，供 SQLite 回查全量定义

    def test_metadata_takes_priority(self):
        """旧 simple_summary 块：metadata 带 api_method/api_url → 优先，不用正文解析覆盖。"""
        r = self._make_retriever()
        r.dual_chroma.search_api_defs.return_value = [
            Document(
                page_content="POST /api/login 登录。",
                metadata={"doc_id": "d1", "api_name": "改名",
                          "api_method": "PUT", "api_url": "/api/other"},
            ),
        ]
        apis = r._search_api_defs("登录", doc_ids=["d1"])
        a = apis[0]
        assert a["method"] == "PUT"
        assert a["url"] == "/api/other"
        assert a["name"] == "改名"

    def test_json_content_still_parsed(self):
        """旧 JSON 原文格式仍兼容。"""
        r = self._make_retriever()
        r.dual_chroma.search_api_defs.return_value = [
            Document(
                page_content='{"name":"登录","url":"/api/login","method":"POST","parameters":[]}',
                metadata={"doc_id": "d1"},
            ),
        ]
        apis = r._search_api_defs("登录", doc_ids=["d1"])
        assert len(apis) == 1
        a = apis[0]
        assert a["method"] == "POST"
        assert a["url"] == "/api/login"
        assert a["source"] == "d1"

    def test_two_distinct_docs_not_collapsed(self):
        """两个不同 doc_id 的接口 → 去重前都应保留，method/url 各异。"""
        r = self._make_retriever()
        r.dual_chroma.search_api_defs.return_value = [
            Document(page_content="POST /api/login 登录。", metadata={"doc_id": "d1", "api_name": "登录"}),
            Document(page_content="GET /api/user 获取用户。", metadata={"doc_id": "d2", "api_name": "获取用户"}),
        ]
        apis = r._search_api_defs("查询", doc_ids=["d1", "d2"])
        assert len(apis) == 2
        assert {a["source"] for a in apis} == {"d1", "d2"}
        assert {a["method"] for a in apis} == {"POST", "GET"}

    def test_fallback_to_sqlite_when_empty(self, monkeypatch):
        """ChromaDB 空结果 → 走 SQLite 即时补偿。"""
        r = self._make_retriever()
        r.dual_chroma.search_api_defs.return_value = []
        called = {"flag": False}

        def _fake_compensate(doc_ids):
            called["flag"] = True
            return [{"name": "登录", "method": "POST", "url": "/api/login", "source": "d1", "_compensated": True}]

        monkeypatch.setattr(
            RetrievalMixin, "_compensate_api_defs_from_sqlite",
            staticmethod(_fake_compensate))
        apis = r._search_api_defs("登录", doc_ids=["d1"])
        assert called["flag"] is True
        assert apis[0]["url"] == "/api/login"


# ============================================================
# 3. _dedup_api_defs 去重防塌缩
# ============================================================

class TestDedupApiDefs:
    def test_distinct_docs_no_method_url_not_collapsed(self):
        """核心回归：method/url 缺失时不同 doc_id 绝不塌缩（72 个接口 → 保留 72 个）。"""
        out = _dedup_api_defs([{"source": "d1"}, {"source": "d2"}, {"source": "d3"}])
        assert len(out) == 3

    def test_same_method_url_dedup(self):
        """同 method+url（同一接口绑定多模块）→ 只保留一条。"""
        out = _dedup_api_defs([
            {"method": "POST", "url": "/api/login", "source": "d1"},
            {"method": "POST", "url": "/api/login", "source": "d2"},
        ])
        assert len(out) == 1

    def test_mixed(self):
        out = _dedup_api_defs([
            {"method": "POST", "url": "/api/login", "source": "d1"},
            {"source": "d2"},
        ])
        assert len(out) == 2

    def test_latest_wins(self):
        out = _dedup_api_defs([
            {"method": "POST", "url": "/api/login", "source": "d1", "name": "旧"},
            {"method": "POST", "url": "/api/login", "source": "d2", "name": "新"},
        ])
        assert len(out) == 1
        assert out[0]["name"] == "新"


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
