"""文件管理 & 绑定关系 API 路由测试（纯 HTTP，不操作数据库）。

验证范围:
  1. /api/files/* — upload/delete/open/list/download/save/content 路由可达性
  2. /api/bindings — 创建/解除/查询绑定关系的路由可达性与参数校验

设计要点:
  - 使用 FastAPI TestClient + unittest.mock 模拟数据库层
  - 不写入磁盘、不操作 SQLite、不调用 ChromaDB
  - 仅验证 HTTP 层：路由匹配、状态码、响应格式

运行方式:
  cd <项目根目录>
  pytest tests/test_files_bindings_api.py -v
"""

import io
import os
import sys
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)


# ============================================================
# 辅助函数
# ============================================================

def _mock_db_session():
    """创建 mock 数据库 session 上下文管理器。"""
    mock_session = MagicMock()
    ctx = MagicMock()
    ctx.__enter__ = MagicMock(return_value=mock_session)
    ctx.__exit__ = MagicMock(return_value=False)
    return ctx


def _mock_doc(mock_id="doc-001", file_name="test.md", doc_type="product"):
    """创建 mock 文档对象。"""
    d = MagicMock()
    d.file_name = file_name
    d.doc_type = doc_type
    d.chunk_count = 3
    d.upload_time = MagicMock()
    d.upload_time.strftime.return_value = "2026-07-15 10:00:00"
    d.id = mock_id
    d.status = "done"
    return d


# ============================================================
# TestClient fixture
# ============================================================

@pytest.fixture(scope="module")
def client():
    """创建 TestClient，一次启动复用整个模块。"""
    from web.app import app
    with TestClient(app) as c:
        yield c


# ---- 通用 mock 上下文管理器组合 ----

from contextlib import ExitStack


def _enter_mocks(*patches):
    """将多个 patch 对象合并为一个 ExitStack 上下文管理器。"""
    stack = ExitStack()
    for p in patches:
        stack.enter_context(p)
    return stack


def _mock_uploaded_files():
    """uploaded-files 端点需要的 mock 组合。"""
    return _enter_mocks(
        patch("database.get_session_ctx", return_value=_mock_db_session()),
        patch("database.operations.DocOps.get_all_documents", return_value=[_mock_doc()]),
        patch("web.app._get_imported_files", return_value=[]),
    )


def _mock_bindings_empty():
    """GET bindings 空列表需要的 mock。"""
    return _enter_mocks(
        patch("database.get_session_ctx", return_value=_mock_db_session()),
        patch("database.operations.BindingOps.get_bindings", return_value=[]),
    )


# ============================================================
# /api/files/* 路由测试
# ============================================================

class TestFilesRoutes:
    """文件管理路由：验证路径可达 + 基本响应格式。"""

    def test_upload_route_exists(self, client):
        """POST /api/files/upload-file 无文件 → 422。"""
        resp = client.post("/api/files/upload-file")
        assert resp.status_code == 422

    def test_delete_route_exists(self, client):
        """POST /api/files/delete-file 缺参 → 422。"""
        resp = client.post("/api/files/delete-file")
        assert resp.status_code == 422

    def test_open_route_exists(self, client):
        """POST /api/files/open-file 缺参 → 422。"""
        resp = client.post("/api/files/open-file")
        assert resp.status_code == 422

    def test_file_save_route_exists(self, client):
        """POST /api/files/file-save 空 body → 422。"""
        resp = client.post("/api/files/file-save")
        assert resp.status_code == 422

    def test_file_save_missing_path(self, client):
        """POST /api/files/file-save path 为空 → 400。"""
        resp = client.post("/api/files/file-save", json={"path": "", "content": ""})
        data = resp.json()
        assert resp.status_code == 400
        assert data["success"] is False

    def test_uploaded_files_success(self, client):
        """GET /api/files/uploaded-files → 200。"""
        with _mock_uploaded_files():
            resp = client.get("/api/files/uploaded-files")
            assert resp.status_code == 200
            assert "files" in resp.json()

    def test_download_file_no_path(self, client):
        """GET /api/files/download-file 缺 path → 业务 404。"""
        resp = client.get("/api/files/download-file")
        assert resp.json()["success"] is False

    def test_file_content_no_path(self, client):
        """GET /api/files/file-content 缺 path → 返回 JSON。"""
        resp = client.get("/api/files/file-content")
        assert resp.json()["success"] is False


# ============================================================
# /api/bindings 路由测试
# ============================================================

class TestBindingsRoutes:
    """绑定关系路由：创建/解除/查询。"""

    # ---- POST (create) ----

    def test_create_route_exists(self, client):
        """POST /api/bindings 空 body → 422。"""
        resp = client.post("/api/bindings")
        assert resp.status_code == 422

    def test_create_missing_params(self, client):
        """POST /api/bindings 缺参 → 400 + '缺少参数'。"""
        resp = client.post("/api/bindings", json={})
        data = resp.json()
        assert resp.status_code == 400
        assert data["success"] is False
        assert "缺少参数" in data["message"]

    def test_create_success(self, client):
        """POST /api/bindings 正常参数 → 200。"""
        with patch("database.get_session_ctx", return_value=_mock_db_session()), \
             patch("database.operations.BindingOps.bind", return_value=(True, "ok")), \
             patch("ingest_v2._cascade_bind_to_module_docs"):
            resp = client.post("/api/bindings", json={
                "source_type": "product", "source_id": "doc-001",
                "target_type": "module", "target_id": "mod-001",
            })
            assert resp.status_code == 200
            assert resp.json()["success"] is True

    # ---- DELETE (unbind) ----

    def test_delete_route_exists(self, client):
        """DELETE /api/bindings 空 body → 422。"""
        resp = client.request("DELETE", "/api/bindings")
        assert resp.status_code == 422

    def test_delete_missing_params(self, client):
        """DELETE /api/bindings 缺参 → 400。"""
        resp = client.request("DELETE", "/api/bindings", json={})
        data = resp.json()
        assert resp.status_code == 400
        assert data["success"] is False
        assert "缺少参数" in data["message"]

    def test_delete_success(self, client):
        """DELETE /api/bindings 正常参数 → 200。"""
        with patch("database.get_session_ctx", return_value=_mock_db_session()), \
             patch("database.operations.BindingOps.unbind_by_pair", return_value=True):
            resp = client.request("DELETE", "/api/bindings", json={
                "a_type": "product", "a_id": "doc-001",
                "b_type": "module", "b_id": "mod-001",
            })
            assert resp.status_code == 200
            assert resp.json()["success"] is True

    def test_delete_not_found(self, client):
        """DELETE /api/bindings 不存在 → 200 + success=False。"""
        with patch("database.get_session_ctx", return_value=_mock_db_session()), \
             patch("database.operations.BindingOps.unbind_by_pair", return_value=False):
            resp = client.request("DELETE", "/api/bindings", json={
                "a_type": "product", "a_id": "doc-999",
                "b_type": "module", "b_id": "mod-999",
            })
            assert resp.status_code == 200
            assert resp.json()["success"] is False

    # ---- GET (list) ----

    def test_get_empty(self, client):
        """GET /api/bindings 空列表 → 200。"""
        with _mock_bindings_empty():
            resp = client.get("/api/bindings")
            assert resp.status_code == 200
            assert resp.json()["bindings"] == []

    def test_get_with_results(self, client):
        """GET /api/bindings?entity_type=product&entity_id=doc-001 → 返回列表。"""
        mock_b = MagicMock()
        mock_b.left_type, mock_b.left_id = "product", "doc-001"
        mock_b.right_type, mock_b.right_id = "module", "mod-001"
        with patch("database.get_session_ctx", return_value=_mock_db_session()), \
             patch("database.operations.BindingOps.get_bindings", return_value=[mock_b]):
            resp = client.get("/api/bindings?entity_type=product&entity_id=doc-001")
            assert resp.status_code == 200
            data = resp.json()
            assert len(data["bindings"]) == 1


# ============================================================
# 路由注册完整性 — 确认所有必需路由存在
# ============================================================

class TestRouteRegistration:
    """确认 route prefix 生效，所有路径不返回 FastAPI 原生 404。"""

    def test_files_crud_routes(self, client):
        """files 增删查路由不可返回 FastAPI 404。"""
        cases = [
            ("POST", "/api/files/upload-file", 422),
            ("POST", "/api/files/delete-file", 422),
            ("POST", "/api/files/open-file", 422),
            ("POST", "/api/files/file-save", 422),
        ]
        for method, path, expected in cases:
            resp = client.request(method, path)
            assert resp.status_code != 404, \
                f"{method} {path} → 404（路由未注册）"

    def test_files_read_routes(self, client):
        """files GET 路由不可返回 FastAPI 404。"""
        with _mock_uploaded_files():
            assert client.get("/api/files/uploaded-files").status_code == 200

        # download / content 缺参返回业务 404（JSON），非路由 404
        for path in ("/api/files/download-file", "/api/files/file-content"):
            resp = client.get(path)
            assert resp.status_code != 404 or "success" in resp.text, \
                f"{path} → FastAPI 404（路由未注册）"

    def test_bindings_crud_routes(self, client):
        """bindings 全部路由不可返回 FastAPI 404。"""
        # POST 空 body → 422
        assert client.post("/api/bindings").status_code != 404
        # DELETE 空 body → 422
        assert client.request("DELETE", "/api/bindings").status_code != 404
        # GET → 200
        with _mock_bindings_empty():
            assert client.get("/api/bindings").status_code == 200
