"""Hop2b 主模块接口全量直取回归测试（2026-09-08 方案C）。

背景：Hop2b `_retrieve_related_data` 原本对 `confirmed_module` 也走 `_search_api_defs`
（ChromaDB 语义 top-K）→ 智慧停车 39 条绑定接口被相似度截成 22，mock 脚手架接口与部分
业务接口落在 `api_definitions` 之外 → 既压低了 Phase 1 用例量（92→60），又让 URL 校验
白名单（取自 api_definitions）对真实接口整批判「疑似URL拼写错误」。
方案C（用户 2026-09-08 定）：主模块直接关联接口在 SQLite 绑定上全量直取（`_load_api_defs_full`，
权威源），只有关联/公共模块绑定接口才走语义检索。详见
changelog/2026-09-08_url_validation_whitelist_bound_scope.md §五 方案C。

覆盖：
  1. 主模块 doc_ids 传给 `_load_api_defs_full`；`_search_api_defs` 只收到关联模块 doc_ids。
  2. 同 method+url 接口被主模块与关联模块同时绑定时，去重保留主模块直取全量版（主模块最后追加）。
  3. `_load_api_defs_full` 真读 SQLite：按 doc_ids 精确过滤、默认全量返回、与补偿路径字段同构；
     `_compensate_api_defs_from_sqlite` 委托后行为不变（标记 _compensated、RETRIEVAL_K 上限）。
"""
import contextlib
import json as _json
import os
import sys
from unittest.mock import Mock

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import get_session_ctx
from database.models import Document
from agent_components.retrievers import RetrievalMixin


# ============================================================
# Fixtures
# ============================================================

@pytest.fixture(autouse=True)
def _reset_singletons():
    """每测试重置单例，防止跨测试 SQLite 文件锁污染。"""
    import database
    database._ENGINE = None
    database._SESSION_LOCAL = None
    from infrastructure.vector_store import dual_chroma
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


class _BoundDoc:
    """BindingOps.get_bound_docs 返回的绑定文档桩（仅暴露 id/doc_type）。"""

    def __init__(self, doc_id, doc_type):
        self.id = doc_id
        self.doc_type = doc_type


class _R(RetrievalMixin):
    pass


def _make_retriever(monkeypatch, bound_by_module, search_return=None):
    """构造 RetrievalMixin 实例并 patch 图外依赖。

    bound_by_module: {模块名: [_BoundDoc, ...]} —— get_bound_docs 的桩返回。
    search_return: _search_api_defs 的固定返回（默认 []）；记录每次 doc_ids 入参。
    """
    r = _R()
    r.dual_chroma = Mock()

    def _fake_get_bound_docs(session, module_name):
        return bound_by_module.get(module_name, [])

    from database.operations import BindingOps
    monkeypatch.setattr(BindingOps, "get_bound_docs",
                        staticmethod(_fake_get_bound_docs))
    monkeypatch.setattr("database.get_session_ctx", contextlib.nullcontext)
    # 公共基础服务模块视为不存在，避免探测模块树（模块级函数，无需 staticmethod 包装）
    monkeypatch.setattr("agent_components.retrievers._mod_exists_in_tree",
                        lambda module_name, session: False)

    search_calls = []

    def _fake_search_api_defs(query, doc_ids=None):
        search_calls.append(list(doc_ids or []))
        return list(search_return or [])

    r._search_api_defs = _fake_search_api_defs  # 实例属性覆盖绑定方法
    return r, search_calls


# ============================================================
# 1. 主模块全量直取、不走语义
# ============================================================

class TestMainModuleFullFetchBypassesSemantic:
    def test_main_full_fetch_related_semantic_only(self, monkeypatch):
        bound = {
            "智慧停车": [_BoundDoc(1, "api"), _BoundDoc(2, "api"),
                        _BoundDoc(3, "api"), _BoundDoc(4, "api"), _BoundDoc(5, "api")],
            "关联车场模块": [_BoundDoc(101, "api"), _BoundDoc(102, "api")],
        }
        r, search_calls = _make_retriever(monkeypatch, bound)

        full_calls = []
        main_apis = [
            {"name": f"接口{i}", "url": f"/api/main/{i}", "method": "POST",
             "description": "", "header": {}, "body": [], "return": [], "source": i}
            for i in range(1, 6)
        ]

        def _fake_full(doc_ids, limit=None):
            full_calls.append(list(doc_ids or []))
            return list(main_apis)

        r._load_api_defs_full = _fake_full  # 实例属性覆盖静态方法

        result = r._retrieve_related_data({
            "confirmed_module": "智慧停车",
            "related_modules": ["关联车场模块"],
            "product_docs": [],
            "user_input": "生成智慧停车测试用例",
        })

        # 语义检索只收到关联模块 doc_ids；主模块绝不进 _search_api_defs
        assert search_calls == [[101, 102]], f"主模块不应被语义检索, search_calls={search_calls}"
        # 直取函数收到主模块全部绑定 doc_ids
        assert full_calls == [[1, 2, 3, 4, 5]]
        # api_definitions = 主模块直取全量（关联模块语义为空时）
        assert len(result["api_definitions"]) == 5
        assert {a["source"] for a in result["api_definitions"]} == {1, 2, 3, 4, 5}


# ============================================================
# 2. 同 URL 冲突 → 主模块直取全量版胜出
# ============================================================

class TestMainModuleVersionWinsOnDedup:
    def test_same_method_url_main_last_wins(self, monkeypatch):
        bound = {
            "智慧停车": [_BoundDoc(1, "api"), _BoundDoc(2, "api")],
            "关联车场模块": [_BoundDoc(101, "api")],
        }
        # 关联模块语义检索到「同 method+url」接口（截断版/关联版）
        search_return = [{"name": "删除规则-关联版", "url": "/parking/rule/del",
                          "method": "POST", "source": 101}]
        r, _search_calls = _make_retriever(monkeypatch, bound, search_return)

        main_apis = [
            {"name": "删除规则-主模块全量版", "url": "/parking/rule/del",
             "method": "POST", "header": {}, "body": [], "return": [], "source": 1},
            {"name": "新增规则", "url": "/parking/rule/add",
             "method": "POST", "header": {}, "body": [], "return": [], "source": 2},
        ]

        def _fake_full(doc_ids, limit=None):
            return list(main_apis)

        r._load_api_defs_full = _fake_full

        result = r._retrieve_related_data({
            "confirmed_module": "智慧停车",
            "related_modules": ["关联车场模块"],
            "product_docs": [],
            "user_input": "生成智慧停车测试用例",
        })

        urls = {a["url"]: a for a in result["api_definitions"]}
        # 同 method+url 只留一条，且是主模块直取全量版（追加在后 → dedup 覆盖关联版）
        assert urls["/parking/rule/del"]["name"] == "删除规则-主模块全量版"
        assert urls["/parking/rule/del"]["source"] == 1
        assert len(result["api_definitions"]) == 2


# ============================================================
# 3. _load_api_defs_full 真读 SQLite 全量
# ============================================================

def _add_api(session, doc_id, url, method="POST", name="api",
             params=None, returns=None, annotations=None):
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


class TestLoadApiDefsFull:
    def test_full_fetch_respects_doc_ids_and_shape(self, in_memory_sqlite):
        with get_session_ctx() as session:
            _add_api(session, 1, "/parking/rule/del", method="POST",
                     name="删除规则", params=[{"name": "id", "type": "int"}])
            _add_api(session, 2, "/park-access-parking-rule-new/mock/delAllForMock",
                     method="POST", name="清理Mock数据")
            _add_api(session, 3, "/parking/park/add", method="POST", name="新增车场")
            # 不在 doc_ids 内的 api + 非 api doc_type：都应被过滤
            _add_api(session, 99, "/other/out-of-scope", method="GET", name="范围外")
            session.add(Document(
                id=500, file_name="GET /not/api", file_type="md", doc_type="product",
                chunk_count=1, status="pending", api_name="非接口", api_url="/not/api",
                api_method="GET"))
            session.commit()

        apis = RetrievalMixin._load_api_defs_full([1, 2, 3])
        # 默认全量返回、精确按 doc_ids 过滤、排除非 api 行
        # Document.id 为字符串主键（与 ChromaDB doc_id 语义一致），source 即 d.id
        assert {a["source"] for a in apis} == {"1", "2", "3"}
        by_id = {a["source"]: a for a in apis}
        assert by_id["1"]["name"] == "删除规则"
        assert by_id["2"]["url"] == "/park-access-parking-rule-new/mock/delAllForMock"
        assert by_id["1"]["body"] == [{"name": "id", "type": "int"}]
        # 字段与既有 ChromaDB 补偿/检索路径同构（下游只读这些键）
        for a in apis:
            for k in ("name", "url", "method", "description",
                      "header", "body", "return", "annotations", "source"):
                assert k in a, f"缺失直取字段: {k}"

    def test_compensate_delegates_and_marks(self, in_memory_sqlite):
        """ChromaDB 兜底委托直取函数：同字段 + _compensated 标记 + RETRIEVAL_K 上限。"""
        with get_session_ctx() as session:
            for i in range(1, 4):
                _add_api(session, i, f"/api/{i}", method="POST", name=f"接口{i}")
            session.commit()

        apis = RetrievalMixin._compensate_api_defs_from_sqlite([1, 2, 3])
        assert len(apis) == 3
        assert all(a.get("_compensated") is True for a in apis)
        assert {a["source"] for a in apis} == {"1", "2", "3"}


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
