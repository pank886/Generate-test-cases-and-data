"""文档-模块关联流程测试（通过 API 端点操作，不直写库表）。

正向测试用例全部通过 FastAPI TestClient 调用前端使用的接口：
  POST   /api/modules          创建模块
  POST   /api/bindings         绑定文档到模块
  DELETE /api/bindings         解绑
  GET    /api/docs/unassociated 未关联文档列表
  GET    /api/modules/{name}/docs 已关联文档列表

验证结果通过 API 响应断言（不直接读库表）。
"""

import os
import sys
import tempfile
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


# ============================================================
# Fixtures
# ============================================================

@pytest.fixture(autouse=True)
def _reset_db():
    """每测试重置 SQLite 单例，创建干净的数据库 + 种子模块。"""
    # 重置单例
    import database as _db
    _db._ENGINE = None
    _db._SESSION_LOCAL = None

    # mock ChromaDB 单例防跨测试污染
    from agent_components import dual_chroma
    dual_chroma._chroma_instance = None

    # 指向临时数据库
    _db.DB_DIR = tempfile.mkdtemp()
    _db.DB_PATH = os.path.join(_db.DB_DIR, "test.db")
    from database import init_db
    init_db()

    # 创建种子模块——这是不可避免的一次性基础数据，无对应 API 端点
    from database import get_session_ctx
    from database.models import Module
    with get_session_ctx() as session:
        root = Module(name="全部模块", parent_id=None, path="/")
        session.add(root)
        session.flush()
        session.add(Module(name="用户管理", parent_id=root.id, path="/用户管理"))
        session.add(Module(name="订单管理", parent_id=root.id, path="/订单管理"))
    yield


@pytest.fixture
def client():
    """FastAPI TestClient — 后续测试均通过此 client 调用 API。"""
    # 构建一个 mock 的 FastAPI 应用（仅注册文档+bindings 路由，绕开 lifespan 重资源）
    # 这里复用主应用但 patch 掉 lifespan 中依赖的外部资源（_chroma_db, LLM 等）
    with patch("web.app._chroma_db", None):
        from web.app import app
        # 使用 TestClient 时 lifespan 自动执行，需确保无外部依赖崩溃
        with TestClient(app) as c:
            yield c


def _create_doc(doc_id, file_name, file_type, doc_type, chunk_count=5):
    """通过 _save_to_sqlite（产品代码写入路径）创建文档。

    前端/用户创建文档的唯一路径是上传文件（POST /upload-file），
    但测试环境无法产生真实 PDF/DOCX/MD，因此用生产代码中的 _save_to_sqlite。
    这与 process_product_doc / commit_api_docs 使用的写入路径完全相同。
    """
    from ingest_v2 import _save_to_sqlite
    _save_to_sqlite(
        doc_id=doc_id,
        file_name=file_name,
        file_type=file_type,
        doc_type=doc_type,
        chunk_count=chunk_count,
    )


# ============================================================
# 1. 未关联文档列表  GET /api/docs/unassociated
# ============================================================

class TestGetUnassociatedDocs:
    """通过 API 验证未关联文档列表。"""

    def test_all_docs_unassociated_by_default(self, client):
        """新建的文档默认全部未关联。"""
        _create_doc("doc_001", "test.pdf", "pdf", "product")
        _create_doc("doc_002", "test.md", "md", "api")

        resp = client.get("/api/docs/unassociated")
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert len(data["docs"]) == 2

    def test_bound_doc_excluded(self, client):
        """已关联到模块的文档不出现在未关联列表中（通过 API 创建绑定）。"""
        _create_doc("doc_001", "test.pdf", "pdf", "product")
        _create_doc("doc_002", "test.md", "md", "api")

        # 通过 API 绑定 product 文档到模块
        bind_resp = client.post("/api/bindings", json={
            "source_type": "product", "source_id": "doc_001",
            "target_type": "module", "target_id": "用户管理",
        })
        assert bind_resp.status_code == 200
        assert bind_resp.json()["success"] is True

        # 验证未关联列表不再包含已绑定的文档
        resp = client.get("/api/docs/unassociated")
        docs = resp.json()["docs"]
        doc_ids = [d["doc_id"] for d in docs]
        assert "doc_001" not in doc_ids
        assert "doc_002" in doc_ids

    def test_unbind_restores_unassociated(self, client):
        """解除绑定后文档回到未关联列表。"""
        _create_doc("doc_001", "test.pdf", "pdf", "product")

        # 绑定
        client.post("/api/bindings", json={
            "source_type": "product", "source_id": "doc_001",
            "target_type": "module", "target_id": "用户管理",
        })

        # 解绑（通过 DELETE API）
        unbind_resp = client.request("DELETE", "/api/bindings", json={
            "a_type": "product", "a_id": "doc_001",
            "b_type": "module", "b_id": "用户管理",
        })
        assert unbind_resp.status_code == 200
        assert unbind_resp.json()["success"] is True

        # 验证文档回到未关联列表
        resp = client.get("/api/docs/unassociated")
        doc_ids = [d["doc_id"] for d in resp.json()["docs"]]
        assert "doc_001" in doc_ids


# ============================================================
# 2. 已关联文档列表  GET /api/modules/{name}/docs
# ============================================================

class TestGetBoundDocs:
    """通过 API 验证已关联文档列表。"""

    def test_empty_when_no_bindings(self, client):
        """模块无绑定文档时返回空列表。"""
        _create_doc("doc_001", "test.pdf", "pdf", "product")

        resp = client.get("/api/modules/用户管理/docs")
        assert resp.status_code == 200
        assert resp.json()["docs"] == []

    def test_returns_bound_docs(self, client):
        """绑定后文档出现在模块的已关联列表中。"""
        _create_doc("doc_001", "test.pdf", "pdf", "product")
        _create_doc("doc_002", "test.md", "md", "api")

        # 绑定两个文档
        client.post("/api/bindings", json={
            "source_type": "product", "source_id": "doc_001",
            "target_type": "module", "target_id": "用户管理",
        })
        client.post("/api/bindings", json={
            "source_type": "api", "source_id": "doc_002",
            "target_type": "module", "target_id": "用户管理",
        })

        # 验证已关联列表
        resp = client.get("/api/modules/用户管理/docs")
        doc_ids = [d["doc_id"] for d in resp.json()["docs"]]
        assert "doc_001" in doc_ids
        assert "doc_002" in doc_ids
        assert len(doc_ids) == 2

    def test_different_modules_isolation(self, client):
        """不同模块的绑定文档互相隔离。"""
        _create_doc("doc_001", "test.pdf", "pdf", "product")
        _create_doc("doc_002", "test.md", "md", "api")

        # 分别绑定到不同模块
        client.post("/api/bindings", json={
            "source_type": "product", "source_id": "doc_001",
            "target_type": "module", "target_id": "用户管理",
        })
        client.post("/api/bindings", json={
            "source_type": "api", "source_id": "doc_002",
            "target_type": "module", "target_id": "订单管理",
        })

        # 每个模块只看到自己的文档
        user_docs = client.get("/api/modules/用户管理/docs").json()["docs"]
        order_docs = client.get("/api/modules/订单管理/docs").json()["docs"]
        assert len(user_docs) == 1
        assert user_docs[0]["doc_id"] == "doc_001"
        assert len(order_docs) == 1
        assert order_docs[0]["doc_id"] == "doc_002"


# ============================================================
# 3. 绑定/解绑完整生命周期  POST/DELETE /api/bindings
# ============================================================

class TestBindUnbindLifecycle:
    """通过 API 测试创建 → 绑定 → 解绑 全链路。"""

    def test_full_lifecycle(self, client):
        """用户故事：上传文档 → 关联到模块 → 确认 → 解绑。"""
        _create_doc("doc_001", "test.pdf", "pdf", "product")

        # Step 1: 刚上传，在未关联列表
        docs_before = client.get("/api/docs/unassociated").json()["docs"]
        assert "doc_001" in [d["doc_id"] for d in docs_before]

        # Step 2: 通过 API 绑定
        bind_resp = client.post("/api/bindings", json={
            "source_type": "product", "source_id": "doc_001",
            "target_type": "module", "target_id": "用户管理",
        })
        assert bind_resp.json()["success"] is True

        # Step 3: 已关联列表出现，未关联列表消失
        bound = client.get("/api/modules/用户管理/docs").json()["docs"]
        unassociated = client.get("/api/docs/unassociated").json()["docs"]
        assert "doc_001" in [d["doc_id"] for d in bound]
        assert "doc_001" not in [d["doc_id"] for d in unassociated]

        # Step 4: 解绑
        unbind_resp = client.request("DELETE", "/api/bindings", json={
            "a_type": "product", "a_id": "doc_001",
            "b_type": "module", "b_id": "用户管理",
        })
        assert unbind_resp.json()["success"] is True

        # Step 5: 文档回到未关联，模块绑定列表为空
        bound_after = client.get("/api/modules/用户管理/docs").json()["docs"]
        unassociated_after = client.get("/api/docs/unassociated").json()["docs"]
        assert len(bound_after) == 0
        assert "doc_001" in [d["doc_id"] for d in unassociated_after]

    def test_bind_idempotent(self, client):
        """通过 API 重复绑定同一文档到同一模块——第二次返回失败。"""
        _create_doc("doc_001", "test.pdf", "pdf", "product")

        # 第一次绑定
        resp1 = client.post("/api/bindings", json={
            "source_type": "product", "source_id": "doc_001",
            "target_type": "module", "target_id": "用户管理",
        })
        assert resp1.json()["success"] is True

        # 第二次绑定同一对
        resp2 = client.post("/api/bindings", json={
            "source_type": "product", "source_id": "doc_001",
            "target_type": "module", "target_id": "用户管理",
        })
        assert resp2.json()["success"] is False  # 防重


# ============================================================
# 4. _chroma_db 为 None 时不崩溃
# ============================================================

class TestChromaDbNoneSafety:
    """_chroma_db=None 时 API 端点不抛 500。"""

    def test_get_module_docs_with_api_doc(self, client):
        """_chroma_db=None 时查询已关联 API 文档不 500。"""
        _create_doc("doc_001", "test.md", "md", "api")

        client.post("/api/bindings", json={
            "source_type": "api", "source_id": "doc_001",
            "target_type": "module", "target_id": "用户管理",
        })

        # _chroma_db 已被 fixture 中的 patch 设为 None
        resp = client.get("/api/modules/用户管理/docs")
        assert resp.status_code == 200
        docs = resp.json()["docs"]
        assert len(docs) == 1
        assert docs[0]["doc_type"] == "api"

    def test_get_module_docs_mixed_types(self, client):
        """_chroma_db=None 时，product+api 文档都正常返回。"""
        _create_doc("doc_prod", "test.pdf", "pdf", "product")
        _create_doc("doc_api", "test.md", "md", "api")

        client.post("/api/bindings", json={
            "source_type": "product", "source_id": "doc_prod",
            "target_type": "module", "target_id": "用户管理",
        })
        client.post("/api/bindings", json={
            "source_type": "api", "source_id": "doc_api",
            "target_type": "module", "target_id": "用户管理",
        })

        resp = client.get("/api/modules/用户管理/docs")
        assert resp.status_code == 200
        types = {d["doc_type"] for d in resp.json()["docs"]}
        assert "product" in types
        assert "api" in types
        assert len(resp.json()["docs"]) == 2

    def test_unassociated_list_shows_all_types(self, client):
        """未关联文档列表包含所有类型。"""
        _create_doc("d1", "a.pdf", "pdf", "product")
        _create_doc("d2", "b.md", "md", "api")
        _create_doc("d3", "c.zip", "zip", "axure")

        resp = client.get("/api/docs/unassociated")
        types = {d["type"] for d in resp.json()["docs"]}
        assert "product" in types
        assert "api" in types
        assert "axure" in types
        assert len(resp.json()["docs"]) == 3


# ============================================================
# 5. 边界条件
# ============================================================

class TestEdgeCases:
    """边界情况验证。"""

    def test_no_documents(self, client):
        """没有任何文档时，未关联列表为空。"""
        resp = client.get("/api/docs/unassociated")
        assert resp.json()["docs"] == []

    def test_unknown_module_has_no_docs(self, client):
        """不存在的模块返回空绑定列表。"""
        _create_doc("doc_001", "test.pdf", "pdf", "product")
        resp = client.get("/api/modules/不存在模块/docs")
        assert resp.json()["docs"] == []

    def test_delete_document_cleans_bindings(self, client):
        """删除文档时自动清理关联的绑定记录（通过删除 API 间接验证）。"""
        _create_doc("doc_001", "test.pdf", "pdf", "product")

        # 绑定
        client.post("/api/bindings", json={
            "source_type": "product", "source_id": "doc_001",
            "target_type": "module", "target_id": "用户管理",
        })

        # 删除文档后，模块的绑定列表应为空
        # 模拟 DocOps.delete_document——后端无删除文档的公开 API 端点
        from database import get_session_ctx
        from database.operations import DocOps
        with get_session_ctx() as session:
            DocOps.delete_document(session, "doc_001")

        # 验证
        resp = client.get("/api/modules/用户管理/docs")
        assert len(resp.json()["docs"]) == 0
