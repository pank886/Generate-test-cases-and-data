"""ApiOps.get_by_url 单元测试（Step 3：精确 + L3 段级模板回退）。

依赖 in_memory_sqlite fixture（与 test_regression_operations 同模式）。
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture(autouse=True)
def _reset_singletons():
    """每测试重置单例，防止跨测试 SQLite 文件锁污染。"""
    import database
    database._ENGINE = None
    database._SESSION_LOCAL = None
    from agent_components import dual_chroma
    dual_chroma._chroma_instance = None
    yield


@pytest.fixture
def in_memory_sqlite(tmp_path):
    """SQLite 文件数据库（非内存），自动建表。"""
    import database
    import database.models  # noqa: F401

    db_path = str(tmp_path / "test.db")
    database._ENGINE = database.create_engine(f"sqlite:///{db_path}", echo=False)
    database.Base.metadata.create_all(bind=database._ENGINE)

    from sqlalchemy.orm import sessionmaker
    database._SESSION_LOCAL = sessionmaker(
        autocommit=False, autoflush=False, bind=database._ENGINE,
    )
    yield
    database._ENGINE.dispose()
    database._ENGINE = None
    database._SESSION_LOCAL = None


from database import get_session_ctx
from database.models import Document
from database.operations import ApiOps


def _add_api(session, doc_id, url, method="POST", name="api",
             params=None, returns=None, annotations=None):
    """测试用：插入一条 api 文档（doc_type='api' + api_* 结构化列）。"""
    import json as _json
    session.add(Document(
        id=doc_id,
        file_name=f"{method} {url}",
        file_type="md",
        doc_type="api",
        chunk_count=1,
        status="pending",
        api_name=name,
        api_url=url,
        api_method=method.upper(),
        api_parameters=_json.dumps(params or [], ensure_ascii=False),
        api_returns=_json.dumps(returns or [], ensure_ascii=False),
        api_annotations=_json.dumps(annotations or {}, ensure_ascii=False),
    ))


class TestApiOpsGetByUrl:

    def test_exact_match(self, in_memory_sqlite):
        with get_session_ctx() as session:
            _add_api(session, "a1", "/order/create", method="POST", name="创建订单",
                     params=[{"name": "id", "type": "int"}])
            session.commit()
        with get_session_ctx() as session:
            r = ApiOps.get_by_url(session, "post", "/order/create")
        assert r is not None
        assert r["api_name"] == "创建订单"
        assert r["api_parameters"] == [{"name": "id", "type": "int"}]

    def test_method_case_insensitive(self, in_memory_sqlite):
        with get_session_ctx() as session:
            _add_api(session, "a1", "/order/create", method="POST")
            session.commit()
        with get_session_ctx() as session:
            assert ApiOps.get_by_url(session, "POST", "/order/create") is not None
            assert ApiOps.get_by_url(session, "post", "/order/create") is not None

    def test_lookup_url_normalized(self, in_memory_sqlite):
        """查询 url 带域名/query/尾斜杠 → 规范化后命中库中纯路径。"""
        with get_session_ctx() as session:
            _add_api(session, "a1", "/order/list", method="GET")
            session.commit()
        with get_session_ctx() as session:
            assert ApiOps.get_by_url(
                session, "GET", "http://host/order/list/") is not None

    def test_template_fallback_single_match(self, in_memory_sqlite):
        with get_session_ctx() as session:
            _add_api(session, "a1", "/order/{id}", method="GET", name="查详情")
            session.commit()
        with get_session_ctx() as session:
            r = ApiOps.get_by_url(session, "GET", "/order/123")
        assert r is not None and r["api_name"] == "查详情"

    def test_template_fallback_ambiguous_returns_none(self, in_memory_sqlite):
        with get_session_ctx() as session:
            _add_api(session, "a1", "/order/{id}", method="GET")
            _add_api(session, "a2", "/order/{sn}", method="GET")
            session.commit()
        with get_session_ctx() as session:
            assert ApiOps.get_by_url(session, "GET", "/order/123") is None

    def test_not_found_returns_none(self, in_memory_sqlite):
        with get_session_ctx() as session:
            _add_api(session, "a1", "/order/create", method="POST")
            session.commit()
        with get_session_ctx() as session:
            assert ApiOps.get_by_url(session, "POST", "/order/nonexistent") is None

    def test_exact_wins_over_template(self, in_memory_sqlite):
        """字面路径存在时精确命中，即使同 method 有模板能通配。"""
        with get_session_ctx() as session:
            _add_api(session, "a1", "/order/export", method="GET", name="导出")
            _add_api(session, "a2", "/order/{id}", method="GET", name="详情")
            session.commit()
        with get_session_ctx() as session:
            r = ApiOps.get_by_url(session, "GET", "/order/export")
        assert r is not None and r["api_name"] == "导出"
