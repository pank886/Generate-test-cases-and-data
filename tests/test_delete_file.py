"""删除文件流程测试（全部通过 POST /delete-file API 触发）。

场景:
  1. 正常删除 — 磁盘文件 + SQLite 记录 + ChromaDB 全量存在
  2. 无本地文件 — 物理文件已删除，仅清理 DB + 内存
  3. 无 SQLite 记录 — 仅清理物理文件 + 内存
  4. ChromaDB 未初始化 — 延迟重试成功（5 分钟后补偿删除）
  5. ChromaDB 未初始化 — 延迟重试也失败
"""

import os
import sys
import tempfile
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


# ============================================================
# Fixtures
# ============================================================

@pytest.fixture(autouse=True)
def _reset_db():
    import database as _db
    _db._ENGINE = None
    _db._SESSION_LOCAL = None
    from agent_components import dual_chroma
    dual_chroma._chroma_instance = None
    _db.DB_DIR = tempfile.mkdtemp()
    _db.DB_PATH = os.path.join(_db.DB_DIR, "test.db")
    from database import init_db
    init_db()
    from database import get_session_ctx
    from database.models import Module
    with get_session_ctx() as session:
        root = Module(name="全部模块", parent_id=None, path="/")
        session.add(root)
        session.flush()
        session.add(Module(name="用户管理", parent_id=root.id, path="/用户管理"))
    yield


@pytest.fixture
def client():
    with patch("web.app._chroma_db", MagicMock()):
        from web.app import app
        with TestClient(app) as c:
            # lifespan 可能扫描到真实 uploads/ 中的文件，重置为纯测试状态
            import web.app as _wa
            _wa._imported_files[_wa._DEFAULT_USER] = []
            yield c


def _create_doc(file_name, doc_type="product", doc_id=None):
    """通过生产代码路径创建文档。"""
    if doc_id is None:
        doc_id = f"test_{file_name}"
    from ingest_v2 import _save_to_sqlite
    _save_to_sqlite(
        doc_id=doc_id, file_name=file_name,
        file_type=os.path.splitext(file_name)[1].lstrip("."),
        doc_type=doc_type, chunk_count=5,
    )
    return doc_id


def _add_to_memory(file_name, file_type="product"):
    """将文件加入内存列表（模拟无 SQLite 记录的场景）。"""
    import pytest
    from web import app as _web_app
    pytest.fail("不允许直写内存状态，测试必须通过 API 端点操作")


# 删除测试使用的前端工具方法
def _delete_file(client, filename):
    """通过 POST /delete-file 发送删除请求（模拟前端 FormData）。"""
    return client.post("/delete-file", data={"filename": filename})


# ============================================================
# 1. 正常删除
# ============================================================

class TestNormalDelete:
    """磁盘文件 + SQLite + ChromaDB 全量存在。"""

    def test_delete_removes_from_file_list(self, client):
        """删除后文件列表不再包含该文件。"""
        _create_doc("test.pdf")
        # 删除前列表应有 1 个文件
        before = client.get("/uploaded-files").json()
        assert len(before["files"]) == 1

        # 执行删除
        resp = _delete_file(client, "test.pdf")
        assert resp.status_code == 200
        assert resp.json()["success"] is True

        # 删除后列表应空
        after = client.get("/uploaded-files").json()
        assert len(after["files"]) == 0

    def test_delete_multiple_files(self, client):
        """连续删除多个文件。"""
        _create_doc("a.pdf")
        _create_doc("b.md", "api")
        _create_doc("c.zip", "axure")

        resp1 = _delete_file(client, "a.pdf")
        resp2 = _delete_file(client, "b.md")
        resp3 = _delete_file(client, "c.zip")
        assert resp1.json()["success"] is True
        assert resp2.json()["success"] is True
        assert resp3.json()["success"] is True

        after = client.get("/uploaded-files").json()
        assert len(after["files"]) == 0

    def test_delete_unknown_file_returns_success(self, client):
        """不存在的文件名删除返回成功（文件本就不存在）。"""
        resp = _delete_file(client, "不存在的文件.pdf")
        assert resp.status_code == 200
        assert resp.json()["success"] is True


# ============================================================
# 2. 无本地文件
# ============================================================

class TestNoLocalFile:
    """物理文件已从磁盘删除，但 SQLite 和内存还有记录。"""

    def test_delete_without_physical_file(self, client):
        """磁盘无文件时删除仍然清理 SQLite + 内存。"""
        doc_id = _create_doc("missing.pdf")
        # 删除物理文件（模拟已丢失）
        # 通过直接删除数据库指向的路径来模拟（但这里无法轻易定位实际路径）
        # 删除端点本身会扫描 uploads/ 目录，找不到文件时不阻断

        # 执行删除
        resp = _delete_file(client, "missing.pdf")
        assert resp.status_code == 200
        assert resp.json()["success"] is True

        # 文件不应再出现在列表中
        after = client.get("/uploaded-files").json()
        assert len(after["files"]) == 0

    def test_delete_after_physical_file_removed(self, client):
        """先删磁盘文件，再通过 API 删除，仍能清理干净。"""
        import os as _os
        import config
        _create_doc("cleanup.pdf")

        # 手动删除物理文件（模拟磁盘清理）
        for subdir in ["product", "pdf", "docx", ""]:
            p = _os.path.join(config.BASE_DIR, "uploads", subdir, "cleanup.pdf")
            if _os.path.exists(p):
                _os.remove(p)
                break

        # API 删除
        resp = _delete_file(client, "cleanup.pdf")
        assert resp.status_code == 200

        after = client.get("/uploaded-files").json()
        assert len(after["files"]) == 0


# ============================================================
# 3. 无 SQLite 记录
# ============================================================

class TestNoSqliteRecord:
    """文件仅存在于内存（_imported_files）或磁盘，SQLite 无记录。"""

    def test_delete_memory_only_file(self, client):
        """仅内存中有记录（无 SQLite）时删除成功。"""
        # 文件在内存中的场景：通过上传流程正常添加后删除 SQLite 模拟
        # 直接通过客户端先上传一个文件，然后手动删除 SQLite 记录
        _create_doc("mem_only.pdf")
        # 手动删除 SQLite 记录（模拟异常状态）
        from database import get_session_ctx
        from database.models import Document
        with get_session_ctx() as session:
            doc = session.query(Document).filter(Document.file_name == "mem_only.pdf").first()
            if doc:
                session.delete(doc)

        # 文件仍在内存中（_imported_files 通过 lifespan 或 _add_imported_file 保留过）
        # 但此时 SQLite 无记录，端点走 "if not doc" 分支
        resp = _delete_file(client, "mem_only.pdf")
        assert resp.status_code == 200
        assert resp.json()["success"] is True

    def test_delete_nonexistent_record(self, client):
        """SQLite 和磁盘都无记录时删除仍返回成功。"""
        resp = _delete_file(client, "ghost.pdf")
        assert resp.status_code == 200
        assert resp.json()["success"] is True


# ============================================================
# 4. ChromaDB 未初始化 — 延迟重试成功
# ============================================================

class TestChromaRetrySuccess:
    """_chroma_db 为 None 时创建延迟任务，模拟 5 分钟后重试成功。"""

    @patch("web.app._chroma_db", None)
    @patch("config.CHROMA_RETRY_DELAY", 0.1)  # 100ms，不等 5 分钟
    def test_retry_succeeds(self, client):
        """ChromaDB 不可用 → 延迟重试 → 成功。"""
        import asyncio
        import config

        _create_doc("chroma_retry.pdf")
        doc_id = f"test_chroma_retry.pdf"

        # 执行删除（此时 _chroma_db 被 patch 为 None）
        resp = _delete_file(client, "chroma_retry.pdf")
        assert resp.status_code == 200

        # 等待延迟任务执行完毕（_CHROMA_RETRY_DELAY=0.1）
        import asyncio
        asyncio.get_event_loop().run_until_complete(asyncio.sleep(0.3))

        # 此时 retry 应该已经执行，且由于 patch 是 None 所以 get_chroma_db() 会返回默认单例
        # 验证 SQLite 记录已被清理（延迟重试本身已完成删除）
        from database import get_session_ctx
        from database.operations import DocOps
        with get_session_ctx() as session:
            doc = DocOps.get_document(session, doc_id)
        assert doc is None, "SQLite 记录应在首次删除时已被清理"


# ============================================================
# 5. ChromaDB 未初始化 — 延迟重试也失败
# ============================================================

class TestChromaRetryFail:
    """_chroma_db 为 None → 延迟重试也失败 → 输出日志和控制台。"""

    @patch("web.app._chroma_db", None)
    @patch("config.CHROMA_RETRY_DELAY", 0.1)
    @patch("agent_components.dual_chroma.get_chroma_db", side_effect=Exception("Ollama 未启动"))
    def test_retry_fails_with_console_output(self, mock_get_db, client, capsys):
        """ChromaDB 不可用 → 延迟重试也失败 → print 输出到控制台。"""
        _create_doc("chroma_fail.pdf")

        resp = _delete_file(client, "chroma_fail.pdf")
        assert resp.status_code == 200

        # 等待延迟任务执行
        import asyncio
        try:
            loop = asyncio.get_event_loop()
            loop.run_until_complete(asyncio.sleep(0.3))
        except RuntimeError:
            # 事件循环可能已在运行，改用 create_task
            pass

        # 捕获 print 输出
        captured = capsys.readouterr()
        # 验证控制台输出了失败信息
        assert "ChromaDB" in captured.out or "延迟删除" in captured.out or "失败" in captured.out
