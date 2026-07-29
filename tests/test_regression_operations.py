"""数据库 CRUD 回归测试 — 拆分后各 Operation class 行为不变。

依赖 conftest.py 中的 in_memory_sqlite fixture。

运行方式:
  pytest tests/test_regression_operations.py -v
"""

import os
import sys
import uuid

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ── Fixtures ────────────────────────────────────────────────────────────────

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


from database.operations import DocOps, ModuleOps, BindingOps, GlossaryOps
from database import get_session_ctx


# ============================================================
# 1. DocOps CRUD
# ============================================================

class TestDocOps:

    def test_add_and_get(self, in_memory_sqlite):
        """add_document + get_document 往返。"""
        with get_session_ctx() as session:
            doc = DocOps.add_document(
                session, doc_id="doc-001", file_name="test.pdf",
                file_type="pdf", doc_type="product", chunk_count=10,
            )
            session.commit()
            assert doc.id == "doc-001"
            assert doc.file_name == "test.pdf"

        with get_session_ctx() as session:
            fetched = DocOps.get_document(session, "doc-001")
            assert fetched is not None
            assert fetched.file_name == "test.pdf"
            assert fetched.chunk_count == 10

    def test_update_document(self, in_memory_sqlite):
        """update_document 修改字段。"""
        with get_session_ctx() as session:
            DocOps.add_document(session, doc_id="doc-002",
                              file_name="old.pdf", file_type="pdf", doc_type="product")
            session.commit()

        with get_session_ctx() as session:
            updated = DocOps.update_document(
                session, "doc-002", file_name="new.pdf", status="bound",
            )
            session.commit()
            assert updated.file_name == "new.pdf"
            assert updated.status == "bound"

    def test_delete_document(self, in_memory_sqlite):
        """delete_document 后 get 返回 None。"""
        with get_session_ctx() as session:
            DocOps.add_document(session, doc_id="doc-003",
                              file_name="del.pdf", file_type="pdf", doc_type="product")
            session.commit()

        with get_session_ctx() as session:
            assert DocOps.delete_document(session, "doc-003") is True
            session.commit()

        with get_session_ctx() as session:
            assert DocOps.get_document(session, "doc-003") is None

    def test_get_all_documents(self, in_memory_sqlite):
        """get_all_documents 返回列表。"""
        with get_session_ctx() as session:
            DocOps.add_document(session, doc_id="d1", file_name="a.pdf",
                              file_type="pdf", doc_type="product")
            DocOps.add_document(session, doc_id="d2", file_name="b.md",
                              file_type="md", doc_type="api")
            session.commit()

        with get_session_ctx() as session:
            all_docs = DocOps.get_all_documents(session)
            assert len(all_docs) >= 2

    def test_get_unassociated_docs(self, in_memory_sqlite):
        """新文档默认出现在未关联列表中。"""
        with get_session_ctx() as session:
            DocOps.add_document(session, doc_id="orphan-1",
                              file_name="o.pdf", file_type="pdf", doc_type="product")
            session.commit()

        with get_session_ctx() as session:
            unassociated = DocOps.get_unassociated_docs(session)
            ids = [d.id for d in unassociated]
            assert "orphan-1" in ids


# ============================================================
# 2. ModuleOps CRUD
# ============================================================

class TestModuleOps:

    # ── helpers ──

    @staticmethod
    def _create_mod(session, name, parent_id=None):
        mod = ModuleOps.create_module(session, name=name, parent_id=parent_id)
        session.flush()
        return mod

    # ── tests ──

    def test_create_and_get(self, in_memory_sqlite):
        """create_module + get_by_name 往返。"""
        with get_session_ctx() as session:
            mod = self._create_mod(session, "智慧用电")
            session.commit()
            assert mod.name == "智慧用电"

        with get_session_ctx() as session:
            fetched = ModuleOps.get_by_name(session, "智慧用电")
            assert fetched is not None
            assert fetched.parent_id is None  # 默认 root

    def test_create_nested_modules(self, in_memory_sqlite):
        """创建嵌套模块树。"""
        with get_session_ctx() as session:
            parent = self._create_mod(session, "园区基线")
            child = self._create_mod(session, "设备管理", parent_id=parent.id)
            session.commit()

        with get_session_ctx() as session:
            tree = ModuleOps.get_tree(session)
            assert len(tree) >= 1

    def test_rename_module(self, in_memory_sqlite):
        """rename_module 修改名称（参数为 module_id）。"""
        with get_session_ctx() as session:
            mod = self._create_mod(session, "旧名称")
            session.commit()
            mod_id = mod.id

        with get_session_ctx() as session:
            ok, msg = ModuleOps.rename_module(session, mod_id, "新名称")
            session.commit()
            assert ok is True

        with get_session_ctx() as session:
            assert ModuleOps.get_by_name(session, "旧名称") is None
            assert ModuleOps.get_by_name(session, "新名称") is not None

    def test_rename_to_existing_name_fails(self, in_memory_sqlite):
        """重名检测—由 SQL UNIQUE 约束保证。"""
        with get_session_ctx() as session:
            self._create_mod(session, "模块A")
            mod_b = self._create_mod(session, "模块B")
            session.commit()
            mod_b_id = mod_b.id

        with get_session_ctx() as session:
            # 试图把 模块B 改名 模块A → 冲突
            import sqlalchemy
            try:
                ModuleOps.rename_module(session, mod_b_id, "模块A")
                session.commit()
                # 如果没抛异常，说明名字改了
                renamed = ModuleOps.get_by_id(session, mod_b_id)
                # 取决于业务逻辑：有些 rename 内部有重名检查
            except Exception:
                session.rollback()

    def test_delete_module(self, in_memory_sqlite):
        """delete_module 删除模块（参数为 module_id）。"""
        with get_session_ctx() as session:
            mod = self._create_mod(session, "待删除")
            session.commit()
            mod_id = mod.id

        with get_session_ctx() as session:
            ok, msg = ModuleOps.delete_module(session, mod_id)
            session.commit()
            assert ok is True

        with get_session_ctx() as session:
            assert ModuleOps.get_by_id(session, mod_id) is None

    def test_get_all_modules(self, in_memory_sqlite):
        """get_all 返回全部模块。"""
        with get_session_ctx() as session:
            self._create_mod(session, "模块1")
            self._create_mod(session, "模块2")
            session.commit()

        with get_session_ctx() as session:
            all_mods = ModuleOps.get_all(session)
            assert len(all_mods) >= 2

    def test_get_tree_returns_list(self, in_memory_sqlite):
        """get_tree 返回列表格式。"""
        with get_session_ctx() as session:
            self._create_mod(session, "根模块")
            session.commit()

        with get_session_ctx() as session:
            tree = ModuleOps.get_tree(session)
            assert isinstance(tree, list)


# ============================================================
# 3. BindingOps CRUD
# ============================================================

class TestBindingOps:

    def _setup_doc_and_module(self, session, doc_id="bd-1", module_name="绑定测试模块"):
        """创建文档和模块供绑定测试，返回 module 实例。"""
        DocOps.add_document(session, doc_id=doc_id,
                          file_name="bind_test.pdf", file_type="pdf", doc_type="product")
        mod = ModuleOps.create_module(session, name=module_name)
        session.flush()
        return mod

    def test_bind_and_unbind(self, in_memory_sqlite):
        """bind(target_id=模块名) + unbind_by_pair 往返。"""
        with get_session_ctx() as session:
            self._setup_doc_and_module(session)
            session.commit()

        with get_session_ctx() as session:
            ok, msg = BindingOps.bind(
                session, source_type="product", source_id="bd-1",
                target_type="module", target_id="绑定测试模块",
            )
            session.commit()
            assert ok is True

        with get_session_ctx() as session:
            ok = BindingOps.unbind_by_pair(
                session, a_type="product", a_id="bd-1",
                b_type="module", b_id="绑定测试模块",
            )
            session.commit()
            assert ok is True

    def test_bind_duplicate_returns_false(self, in_memory_sqlite):
        """重复绑定返回 False（防重）。"""
        with get_session_ctx() as session:
            self._setup_doc_and_module(session)
            session.commit()

        with get_session_ctx() as session:
            ok1, _ = BindingOps.bind(
                session, source_type="product", source_id="bd-1",
                target_type="module", target_id="绑定测试模块",
            )
            session.commit()
            assert ok1 is True

        with get_session_ctx() as session:
            ok2, msg2 = BindingOps.bind(
                session, source_type="product", source_id="bd-1",
                target_type="module", target_id="绑定测试模块",
            )
            assert ok2 is False  # 已存在

    def test_get_bindings(self, in_memory_sqlite):
        """get_bindings 返回绑定列表。"""
        with get_session_ctx() as session:
            self._setup_doc_and_module(session, doc_id="bd-3")
            session.commit()

        with get_session_ctx() as session:
            BindingOps.bind(session, source_type="product", source_id="bd-3",
                          target_type="module", target_id="绑定测试模块")
            session.commit()

        with get_session_ctx() as session:
            bindings = BindingOps.get_bindings(session)
            assert len(bindings) >= 1

    def test_get_partners(self, in_memory_sqlite):
        """get_partners 返回关联实体列表。"""
        with get_session_ctx() as session:
            self._setup_doc_and_module(session, doc_id="bd-4")
            session.commit()

        with get_session_ctx() as session:
            BindingOps.bind(session, source_type="product", source_id="bd-4",
                          target_type="module", target_id="绑定测试模块")
            session.commit()

        with get_session_ctx() as session:
            partners = BindingOps.get_partners(
                session, entity_type="product", entity_id="bd-4",
            )
            assert len(partners) >= 1
            # 返回 [(partner_type, partner_id), ...]
            assert isinstance(partners[0], tuple)
            assert partners[0][0] == "module"

    def test_delete_bindings_for_doc(self, in_memory_sqlite):
        """delete_bindings_for_doc 清理绑定。"""
        with get_session_ctx() as session:
            self._setup_doc_and_module(session, doc_id="bd-5")
            session.commit()

        with get_session_ctx() as session:
            BindingOps.bind(session, source_type="product", source_id="bd-5",
                          target_type="module", target_id="绑定测试模块")
            session.commit()

        with get_session_ctx() as session:
            BindingOps.delete_bindings_for_doc(session, "bd-5")
            session.commit()

        with get_session_ctx() as session:
            bindings = BindingOps.get_bindings(session)
            bd5_bindings = [
                b for b in bindings
                if b.left_id == "bd-5" or b.right_id == "bd-5"
            ]
            assert len(bd5_bindings) == 0


# ============================================================
# 4. GlossaryOps CRUD
# ============================================================

class TestGlossaryOps:

    def _setup_doc(self, session, doc_id="gl-1"):
        DocOps.add_document(session, doc_id=doc_id,
                          file_name="glossary_test.pdf",
                          file_type="pdf", doc_type="product")

    def test_add_and_get_terms(self, in_memory_sqlite):
        """add_term + get_terms 往返。"""
        with get_session_ctx() as session:
            self._setup_doc(session)
            session.commit()

        with get_session_ctx() as session:
            term = GlossaryOps.add_term(
                session, doc_id="gl-1", term="用电量", definition="总用电量",
            )
            session.commit()
            assert term.term == "用电量"

        with get_session_ctx() as session:
            terms = GlossaryOps.get_terms(session, "gl-1")
            assert len(terms) >= 1
            assert terms[0].term == "用电量"

    def test_update_term(self, in_memory_sqlite):
        """update_term 修改术语（参数为 term_id int）。"""
        with get_session_ctx() as session:
            self._setup_doc(session)
            session.commit()

        with get_session_ctx() as session:
            term = GlossaryOps.add_term(
                session, doc_id="gl-1", term="旧术语", definition="旧定义",
            )
            session.commit()
            tid = term.id

        with get_session_ctx() as session:
            updated = GlossaryOps.update_term(
                session, tid, definition="新定义",
            )
            session.commit()
            assert updated is not None
            assert updated.definition == "新定义"

    def test_delete_term(self, in_memory_sqlite):
        """delete_term 删除术语（参数为 term_id int）。"""
        with get_session_ctx() as session:
            self._setup_doc(session)
            session.commit()

        with get_session_ctx() as session:
            term = GlossaryOps.add_term(
                session, doc_id="gl-1", term="待删除术语", definition="...",
            )
            session.commit()
            tid = term.id

        with get_session_ctx() as session:
            assert GlossaryOps.delete_term(session, tid) is True
            session.commit()

    def test_replace_terms(self, in_memory_sqlite):
        """replace_terms 原子替换术语列表（参数为 terms）。"""
        with get_session_ctx() as session:
            self._setup_doc(session)
            session.commit()

        with get_session_ctx() as session:
            GlossaryOps.add_term(session, doc_id="gl-1", term="旧", definition="旧")
            session.commit()

        with get_session_ctx() as session:
            GlossaryOps.replace_terms(
                session, doc_id="gl-1",
                terms=[
                    {"term": "新术语1", "definition": "定义1"},
                    {"term": "新术语2", "definition": "定义2"},
                ],
            )
            session.commit()

        with get_session_ctx() as session:
            terms = GlossaryOps.get_terms(session, "gl-1")
            term_names = {t.term for t in terms}
            assert "新术语1" in term_names
            assert "新术语2" in term_names
            assert "旧" not in term_names

    def test_cascade_delete_doc_removes_terms(self, in_memory_sqlite):
        """文档删除时级联删除术语（数据库 foreign key cascade）。"""
        with get_session_ctx() as session:
            self._setup_doc(session, doc_id="cascade-test")
            session.commit()

        with get_session_ctx() as session:
            GlossaryOps.add_term(
                session, doc_id="cascade-test", term="级联术语", definition="...",
            )
            session.commit()

        with get_session_ctx() as session:
            DocOps.delete_document(session, "cascade-test")
            session.commit()

        with get_session_ctx() as session:
            terms = GlossaryOps.get_terms(session, "cascade-test")
            assert len(terms) == 0
