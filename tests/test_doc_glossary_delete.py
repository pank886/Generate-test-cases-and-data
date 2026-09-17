"""文档术语表删除回归测试。

背景: GET /api/docs/{doc_id}/glossary 返回值缺 id → 前端 parseInt(undefined)=NaN
→ DELETE /glossary/NaN → 后端 int('NaN') ValueError → 500。
修复: get_doc_glossary 返回值补 "id": t.id（web/routes/docs.py）。

覆盖:
  1. GET 文档术语返回 id（术语主键），前端可据此删除
  2. 用该 id DELETE 单条术语 → success、术语被删（不再 500）

运行:
  cd <项目根目录>
  pytest tests/test_doc_glossary_delete.py -v
"""

import asyncio
import os
import sys
import tempfile

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


@pytest.fixture(autouse=True)
def _reset_singletons():
    """每测试重置 database 单例，防止跨测试污染（与 test_ingest_main_flow.py 同规约）。

    不重置则 _ENGINE/_SESSION_LOCAL 缓存首个临时 DB 路径，后续测试的
    DB_DIR/DB_PATH 重赋值不生效 → 多测试共享同一引擎、同 doc_id 互相污染。
    """
    import database
    database._ENGINE = None
    database._SESSION_LOCAL = None
    yield


@pytest.fixture
def in_memory_sqlite():
    """把 database 模块指向临时 SQLite，隔离真实 data/app.db。"""
    os.environ.setdefault("EMBEDDING_MODEL", "test-model")
    import database
    database.DB_DIR = tempfile.mkdtemp()
    database.DB_PATH = os.path.join(database.DB_DIR, "test.db")
    database.init_db()
    return database


def _seed_term(session, doc_id: str) -> int:
    """造一条文档记录 + 术语并提交，返回术语主键 id。"""
    from database.models import Document
    from database.operations import GlossaryOps
    if session.get(Document, doc_id) is None:
        session.add(Document(id=doc_id, file_name=f"{doc_id}.pdf",
                             file_type="pdf", doc_type="product"))
    t = GlossaryOps.add_term(session, doc_id, "token", "身份令牌", notes="")
    session.commit()
    return t.id


class TestDocGlossaryDelete:
    def test_get_glossary_returns_id(self, in_memory_sqlite):
        """GET 文档术语必须含 id，前端才能发正确的删除请求。"""
        from database import get_session_ctx
        from web.routes.docs import get_doc_glossary

        doc_id = "prod_规则表.xlsx_模块"
        with get_session_ctx() as session:
            tid = _seed_term(session, doc_id)

        resp = asyncio.run(get_doc_glossary(doc_id))
        assert resp["success"] is True
        assert any(t.get("id") == tid for t in resp["terms"])

    def test_delete_glossary_by_id_succeeds(self, in_memory_sqlite):
        """用 GET 返回的真实 id 删除 → success=True，术语消失。"""
        from database import get_session_ctx
        from database.operations import GlossaryOps
        from web.routes.docs import delete_doc_glossary, get_doc_glossary

        doc_id = "prod_规则表.xlsx_模块"
        with get_session_ctx() as session:
            tid = _seed_term(session, doc_id)

        resp = asyncio.run(delete_doc_glossary(doc_id, str(tid)))
        assert resp["success"] is True
        with get_session_ctx() as session:
            assert GlossaryOps.get_terms(session, doc_id) == []

        # 二次 GET 为空
        resp2 = asyncio.run(get_doc_glossary(doc_id))
        assert resp2["terms"] == []
