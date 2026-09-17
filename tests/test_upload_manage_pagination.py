"""上传页/管理页主列表 分页 + 惰性加载 + 跨页批量删除 测试（2026-09-04）。

验证范围（changelog 2026-09-03_upload_manage_page_pagination_lazy_plan.md 第六节）:
  1. uploaded-files 分页：type/page/page_size 切片 + total/counts/has_more
  2. uploaded-files 兼容：不带分页参数 → 全量 + 旧字段（存量调用回归）
  3. uploaded-files page_size=0 → 仅计数（建 Tab/section 头）
  4. unassociated 分页：type=axure 当页行含 pages、counts、末页 has_more=false
  5. unassociated 兼容：不带参数 → 全量（含 axure pages）
  6. 关联数稳定性：分页不改变 total
  7. batch-delete-files:
     a. {filter:{type,q}} → 删除该筛选全部匹配，返回 deleted=N
     b. {filter:{...无匹配}} → deleted=0，幂等，不触发清理
     c. {names:[...]} → 仅删名单内，不存在项进 failed 不阻断
     d. 批量路径与单条 delete_file 同清理函数（本端点内部即调用 delete_file）
  附带：page/page_size 非法 → 400。

设计要点:
  - TestClient（模块级，与 test_files_bindings_api.py 同规约）
  - 用 mock 替代数据源/清理函数，避免操作 SQLite / 磁盘 / ChromaDB：
      * uploaded-files & batch(filter)  → patch web.routes.files._merged_uploaded_state
      * batch 清理                      → patch web.routes.files.delete_file（AsyncMock 记录调用）
      * unassociated                   → patch database.get_session_ctx /
                                         DocOps.get_unassociated_docs /
                                         web.routes.docs._axure_pages_from_glossary
  因全部 mock，无需 autouse reset singletons。

运行方式:
  pytest tests/test_upload_manage_pagination.py -v
"""

import os
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)


@pytest.fixture(scope="module")
def client():
    """创建 TestClient，一次启动复用整个模块。"""
    from web.app import app
    with TestClient(app) as c:
        yield c


# ============================================================
# 数据构造辅助
# ============================================================

def _file_row(name, doc_type, **kw):
    row = {"name": name, "type": doc_type, "chunks": 3, "doc_id": "doc-" + name,
           "status": "done"}
    row.update(kw)
    return row


def _seed_rows():
    """产品 3 / 接口 5 / axure 2 的混合文件集，供 uploaded-files 各用例。"""
    return [
        _file_row("api-alpha-1.xlsx", "api"),
        _file_row("api-alpha-2.xlsx", "api"),
        _file_row("api-beta.xlsx", "api"),
        _file_row("api-gamma.xlsx", "api"),
        _file_row("api-delta.xlsx", "api"),
        _file_row("prod-alpha.docx", "product"),
        _file_row("prod-bravo.docx", "product"),
        _file_row("prod-charlie.docx", "product"),
        _file_row("axure-home.zip", "axure"),
        _file_row("axure-detail.zip", "axure"),
    ]


def _doc_obj(doc_id, file_name, doc_type, chunk_count=2):
    return SimpleNamespace(id=doc_id, doc_type=doc_type, file_name=file_name,
                           chunk_count=chunk_count)


def _unassociated_seed():
    """未关联文档集：product 2 / api 3 / axure 2（其中 1 个绑定模块则不在此）。"""
    return [
        _doc_obj("u-prod-1", "规格说明.docx", "product"),
        _doc_obj("u-prod-2", "需求矩阵.xlsx", "product"),
        _doc_obj("u-api-1", "login.md", "api"),
        _doc_obj("u-api-2", "bill.md", "api"),
        _doc_obj("u-api-3", "meter.md", "api"),
        _doc_obj("u-axure-1", "电表管理.zip", "axure"),
        _doc_obj("u-axure-2", "缴费.zip", "axure"),
    ]


def _mock_ctx():
    """mock database.get_session_ctx 上下文管理器，返回 MagicMock session。"""
    session = MagicMock()
    ctx = MagicMock()
    ctx.__enter__ = MagicMock(return_value=session)
    ctx.__exit__ = MagicMock(return_value=False)
    return ctx


AXURE_PAGES = [
    {"title": "电表管理/历史电量", "required_fields": ["表号", "日期"]},
    {"title": "电表管理/抄表", "required_fields": ["表号"]},
]


# ============================================================
# 1~3. uploaded-files 分页 / 兼容 / 仅计数
# ============================================================

class TestUploadedFilesPagination:
    """GET /api/files/uploaded-files 分页/过滤/计数。"""

    def test_pagination_slice_and_counts(self, client):
        rows = _seed_rows()
        with patch("web.routes.files._merged_uploaded_state",
                   new=AsyncMock(return_value=(rows, True))):
            resp = client.get("/api/files/uploaded-files",
                              params={"type": "api", "page": 1, "page_size": 2})
            assert resp.status_code == 200
            d = resp.json()
            assert [f["name"] for f in d["files"]] == ["api-alpha-1.xlsx", "api-alpha-2.xlsx"]
            assert d["total"] == 5
            assert d["has_more"] is True
            assert d["page"] == 1 and d["page_size"] == 2
            # counts = 应用 q、不含 type
            assert d["counts"] == {"product": 3, "api": 5, "axure": 2}
            assert d["vector_ready"] is True

    def test_pagination_last_page_has_more_false(self, client):
        rows = _seed_rows()
        with patch("web.routes.files._merged_uploaded_state",
                   new=AsyncMock(return_value=(rows, False))):
            resp = client.get("/api/files/uploaded-files",
                              params={"type": "api", "page": 3, "page_size": 2})
            d = resp.json()
            assert len(d["files"]) == 1
            assert d["files"][0]["name"] == "api-delta.xlsx"
            assert d["total"] == 5
            assert d["has_more"] is False

    def test_pagination_unknown_type_returns_empty(self, client):
        rows = _seed_rows()
        with patch("web.routes.files._merged_uploaded_state",
                   new=AsyncMock(return_value=(rows, False))):
            resp = client.get("/api/files/uploaded-files",
                              params={"type": "axure", "page": 1, "page_size": 50})
            d = resp.json()
            assert len(d["files"]) == 2
            assert d["total"] == 2

    def test_q_filter_case_insensitive(self, client):
        rows = _seed_rows()
        with patch("web.routes.files._merged_uploaded_state",
                   new=AsyncMock(return_value=(rows, False))):
            resp = client.get("/api/files/uploaded-files",
                              params={"type": "api", "q": "ALPHA", "page": 1, "page_size": 50})
            d = resp.json()
            assert d["total"] == 2
            assert {f["name"] for f in d["files"]} == {"api-alpha-1.xlsx", "api-alpha-2.xlsx"}
            # q 应用于 counts 但不含 type → 命中 api 2 + product(prod-alpha) 1，跨类型统计
            assert d["counts"] == {"product": 1, "api": 2, "axure": 0}

    def test_backward_compat_no_params_full_list(self, client):
        """不带分页参数 → 全量 + 旧字段（存量调用零改动）。"""
        rows = _seed_rows()
        with patch("web.routes.files._merged_uploaded_state",
                   new=AsyncMock(return_value=(rows, False))):
            resp = client.get("/api/files/uploaded-files")
            d = resp.json()
            assert len(d["files"]) == len(rows) == 10
            assert d["total"] == 10
            # 旧响应无分页字段
            assert "page" not in d and "page_size" not in d and "has_more" not in d
            assert "counts" in d

    def test_page_size_zero_counts_only(self, client):
        rows = _seed_rows()
        with patch("web.routes.files._merged_uploaded_state",
                   new=AsyncMock(return_value=(rows, True))):
            resp = client.get("/api/files/uploaded-files",
                              params={"page_size": 0})
            d = resp.json()
            assert d["files"] == []
            assert d["total"] == 10
            assert d["counts"] == {"product": 3, "api": 5, "axure": 2}
            assert d["has_more"] is False

    def test_invalid_params_400(self, client):
        rows = _seed_rows()
        with patch("web.routes.files._merged_uploaded_state",
                   new=AsyncMock(return_value=(rows, False))):
            assert client.get("/api/files/uploaded-files",
                              params={"page": 0, "page_size": 10}).status_code == 400
            assert client.get("/api/files/uploaded-files",
                              params={"page": 1, "page_size": -1}).status_code == 400


# ============================================================
# 4~6. unassociated 分页 / 兼容 / total 稳定性
# ============================================================

class TestUnassociatedPagination:
    """GET /api/docs/unassociated 分页/计数/当页聚合。"""

    def _patch_unassociated(self, seed=None):
        """组合 patch：session ctx + 未关联集合 + axure 页面块聚合。"""
        from contextlib import ExitStack
        stack = ExitStack()
        seed = seed if seed is not None else _unassociated_seed()
        stack.enter_context(
            patch("database.get_session_ctx", new=_mock_ctx()))
        stack.enter_context(
            patch("database.operations.DocOps.get_unassociated_docs", return_value=seed))
        # 页面块聚合：仅当被调用时返回固定 pages（当前页 axure 行才会触发）
        stack.enter_context(
            patch("web.routes.docs._axure_pages_from_glossary", return_value=AXURE_PAGES))
        return stack

    def test_pagination_axure_current_page_has_pages(self, client):
        with self._patch_unassociated():
            resp = client.get("/api/docs/unassociated",
                              params={"type": "axure", "page": 1, "page_size": 1})
            d = resp.json()
            assert resp.status_code == 200
            assert len(d["docs"]) == 1
            assert d["docs"][0]["doc_type"] == "axure"
            assert d["docs"][0]["pages"] == AXURE_PAGES   # 当页行聚合页面块
            assert d["total"] == 2
            assert d["counts"] == {"product": 2, "api": 3, "axure": 2}
            assert d["has_more"] is True

    def test_pagination_last_axure_page_has_more_false(self, client):
        with self._patch_unassociated():
            resp = client.get("/api/docs/unassociated",
                              params={"type": "axure", "page": 2, "page_size": 1})
            d = resp.json()
            assert len(d["docs"]) == 1
            assert d["has_more"] is False
            assert d["docs"][0]["doc_id"] == "u-axure-2"

    def test_backward_compat_no_params_full_list(self, client):
        """不带参数 → 全量（含 axure 全 pages），旧字段保留。"""
        with self._patch_unassociated():
            resp = client.get("/api/docs/unassociated")
            d = resp.json()
            assert len(d["docs"]) == 7
            assert d["total"] == 7
            axure = [x for x in d["docs"] if x["doc_type"] == "axure"]
            assert len(axure) == 2
            assert all(x["pages"] == AXURE_PAGES for x in axure)
            assert "page" not in d and "page_size" not in d and "has_more" not in d

    def test_total_stable_across_pages(self, client):
        """关联数稳定性：分页不改变 total。"""
        with self._patch_unassociated():
            page1 = client.get("/api/docs/unassociated",
                               params={"type": "api", "page": 1, "page_size": 2}).json()
            page2 = client.get("/api/docs/unassociated",
                               params={"type": "api", "page": 2, "page_size": 2}).json()
            full = client.get("/api/docs/unassociated",
                              params={"type": "api"}).json()
            assert page1["total"] == page2["total"] == full["total"] == 3

    def test_page_size_zero_counts_only(self, client):
        with self._patch_unassociated():
            resp = client.get("/api/docs/unassociated", params={"page_size": 0})
            d = resp.json()
            assert d["docs"] == []
            assert d["total"] == 7
            assert d["counts"] == {"product": 2, "api": 3, "axure": 2}

    def test_invalid_params_400(self, client):
        with self._patch_unassociated():
            assert client.get("/api/docs/unassociated",
                              params={"page": 0, "page_size": 5}).status_code == 400
            assert client.get("/api/docs/unassociated",
                              params={"page": 1, "page_size": -1}).status_code == 400


# ============================================================
# 7. batch-delete-files
# ============================================================

class TestBatchDeleteFiles:
    """POST /api/files/batch-delete-files 跨页全选(filter) / 名单(names)。"""

    def test_filter_type_q_deletes_all_matches(self, client):
        """{filter:{type,q}} → 服务端解析全部匹配并逐条走 delete_file。"""
        rows = _seed_rows()
        calls = []
        fake_del = AsyncMock(side_effect=lambda filename: calls.append(filename) or {"success": True})
        with patch("web.routes.files._merged_uploaded_state",
                   new=AsyncMock(return_value=(rows, True))), \
             patch("web.routes.files.delete_file", new=fake_del):
            resp = client.post("/api/files/batch-delete-files",
                               json={"filter": {"type": "api", "q": "alpha"}})
            d = resp.json()
            assert resp.status_code == 200
            assert d["success"] is True
            assert d["deleted"] == 2
            assert d["failed"] == 0
            assert sorted(calls) == ["api-alpha-1.xlsx", "api-alpha-2.xlsx"]

    def test_filter_no_match_idempotent(self, client):
        """{filter:{...无匹配}} → deleted=0 幂等，不触发任何清理。"""
        rows = _seed_rows()
        fake_del = AsyncMock(return_value={"success": True})
        with patch("web.routes.files._merged_uploaded_state",
                   new=AsyncMock(return_value=(rows, True))), \
             patch("web.routes.files.delete_file", new=fake_del):
            resp = client.post("/api/files/batch-delete-files",
                               json={"filter": {"type": "axure", "q": "不存在"}})
            d = resp.json()
            assert d["deleted"] == 0 and d["failed"] == 0
            fake_del.assert_not_awaited()

    def test_names_subset_skips_missing_without_abort(self, client):
        """{names:[子集]} → 仅删名单内；不存在项进 failed 不阻断。"""
        known = {"api-alpha-1.xlsx", "api-beta.xlsx"}
        fake_del = AsyncMock(side_effect=lambda filename: {"success": filename in known})
        with patch("web.routes.files.delete_file", new=fake_del):
            resp = client.post("/api/files/batch-delete-files",
                               json={"names": ["api-alpha-1.xlsx", "missing.xlsx", "api-beta.xlsx"]})
            d = resp.json()
            assert d["deleted"] == 2
            assert d["failed"] == 1
            assert d["errors"] == ["missing.xlsx"]

    def test_names_deduplicated(self, client):
        """名单重复 → 只清一次。"""
        fake_del = AsyncMock(return_value={"success": True})
        with patch("web.routes.files.delete_file", new=fake_del):
            resp = client.post("/api/files/batch-delete-files",
                               json={"names": ["a.pdf", "a.pdf", "a.pdf"]})
            d = resp.json()
            assert d["deleted"] == 1
            assert fake_del.await_count == 1

    def test_single_delete_and_batch_share_cleanup(self, client):
        """批量内部即调用单条 delete_file（清理同源）；单条删除既有测试回归保留。"""
        fake_del = AsyncMock(return_value={"success": True})
        with patch("web.routes.files.delete_file", new=fake_del):
            resp = client.post("/api/files/batch-delete-files",
                               json={"names": ["only-one.xlsx"]})
            d = resp.json()
            assert d["deleted"] == 1
            fake_del.assert_awaited_once_with(filename="only-one.xlsx")

    def test_missing_body_400(self, client):
        resp = client.post("/api/files/batch-delete-files", json={})
        assert resp.status_code == 400
