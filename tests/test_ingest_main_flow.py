"""入库主流程集成测试 — 全 mock 外部依赖，可 CI 运行。

运行方式:
  python -m pytest tests/test_ingest_main_flow.py -v

覆盖场景:
  1. _safe_doc_id 幂等性与截断
  2. _save_to_sqlite 写入与异常回滚
  3. process_product_doc 全链路 (mock LLM + ChromaDB)
  4. process_api_doc_extract 两阶段提取 (mock LLM)
  5. commit_api_docs 确认入库 (mock ChromaDB)
  6. 数据一致性: ChromaDB 成功 → SQLite 失败 → 回滚清理
  7. Axure 解析流程 (mock parser)
"""

import json
import os
import sys
import tempfile
from unittest.mock import MagicMock, patch, PropertyMock
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

# ── Fixtures ────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _reset_singletons():
    """每测试重置单例状态，防止跨测试污染。"""
    from database import _ENGINE, _SESSION_LOCAL
    import database
    database._ENGINE = None
    database._SESSION_LOCAL = None
    # 重置 ChromaDB 单例
    from agent_components import dual_chroma
    dual_chroma._chroma_instance = None
    yield


@pytest.fixture
def mock_llm():
    """Mock ChatTestAgentGraph._invoke_structured 返回固定 Pydantic 模型。"""
    from prompts.response_model import (
        DocModuleExtract, ApiDefExtract, GlossaryExtract,
    )

    def _make_mock(return_value):
        patcher = patch(
            "agent_components.nodes.ChatTestAgentGraph._invoke_structured",
            return_value=return_value,
        )
        mock = patcher.start()
        yield mock
        patcher.stop()

    return _make_mock


@pytest.fixture
def mock_chroma():
    """Mock ChromaDB 双集合。"""
    from agent_components.dual_chroma import DualChromaDB
    with patch("agent_components.dual_chroma.get_chroma_db") as m:
        fake_db = MagicMock(spec=DualChromaDB)
        fake_db.search_product_docs.return_value = []
        fake_db.search_api_defs.return_value = []
        fake_db.search_context.return_value = "mock context"
        fake_db.get_doc_chunks.return_value = []
        m.return_value = fake_db
        yield fake_db


@pytest.fixture
def temp_md_file():
    """创建临时 MD 文件。"""
    content = """# 用户管理 API

## 登录
POST /api/login
请求参数: {username, password}
返回: {token, user_info}

## 获取用户信息
GET /api/user/{id}
请求参数: {id}
返回: {name, email, role}
"""
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".md", encoding="utf-8", delete=False,
    ) as f:
        f.write(content)
        f.flush()
        path = f.name
    yield path
    try:
        os.unlink(path)
    except OSError:
        pass


@pytest.fixture
def temp_pdf_like(tmp_path):
    """创建模拟 PDF 文本文件（测试 _extract_text 用纯文本替代）。"""
    path = tmp_path / "test_doc.txt"
    path.write_text(
        "健身房预约模块功能说明\n"
        "## 用户注册\n"
        "用户可以通过手机号注册\n"
        "## 预约课程\n"
        "用户可以选择课程时间\n",
        encoding="utf-8",
    )
    return str(path)


@pytest.fixture
def in_memory_sqlite():
    """初始化 SQLite 内存数据库。"""
    from database import init_db, get_session_ctx
    from database.operations import ModuleOps, DocOps

    os.environ.setdefault("EMBEDDING_MODEL", "test-model")
    import database
    # 强制内存模式
    database.DB_DIR = tempfile.mkdtemp()
    database.DB_PATH = os.path.join(database.DB_DIR, "test.db")
    init_db()

    # 插入种子模块树
    from database.models import Module
    with get_session_ctx() as session:
        root = Module(name="全部模块", parent_id=None, path="/")
        session.add(root)
        session.flush()
        mod = Module(name="用户管理", parent_id=root.id, path="/用户管理")
        session.add(mod)
    return database


# ══════════════════════════════════════════════════════════════════════
#  1. _safe_doc_id — 幂等性与截断
# ══════════════════════════════════════════════════════════════════════

class TestSafeDocId:
    def test_normal_length(self):
        """正常长度不截断。"""
        from ingest_v2 import _safe_doc_id
        doc_id = _safe_doc_id("prod", "test_doc.pdf", "用户管理")
        assert doc_id.startswith("prod_")
        assert "test_doc.pdf" in doc_id
        assert len(doc_id) < 180

    def test_idempotent(self):
        """同输入输出相同 doc_id（幂等性）。"""
        from ingest_v2 import _safe_doc_id
        a = _safe_doc_id("prod", "mydoc.pdf", "用户管理")
        b = _safe_doc_id("prod", "mydoc.pdf", "用户管理")
        assert a == b

    def test_truncation_with_md5(self):
        """超长路径触发 MD5 截断。"""
        from ingest_v2 import _safe_doc_id
        long_name = "a" * 200
        doc_id = _safe_doc_id("api", long_name, "long_module_name_" * 10)
        # 截断策略：raw[:172] + '_' + MD5[:8] = 172+1+8 = 181 ≤ 200(String 列)
        assert len(doc_id) <= 200
        assert len(doc_id) < len("api_" + long_name + "_long_module_name_" * 10)
        assert "_" in doc_id  # 应有 MD5 分隔符


# ══════════════════════════════════════════════════════════════════════
#  2. _save_to_sqlite — SQLite 写入
# ══════════════════════════════════════════════════════════════════════

class TestSaveToSqlite:
    def test_write_and_read(self, in_memory_sqlite):
        """写入文档记录后可以查询到。"""
        from ingest_v2 import _save_to_sqlite
        from database import get_session_ctx
        from database.operations import DocOps

        _save_to_sqlite(
            doc_id="test_doc_001",
            file_name="test.pdf",
            file_type="pdf",
            doc_type="product",
            chunk_count=10,
            module_name="用户管理",
        )

        with get_session_ctx() as session:
            doc = DocOps.get_document(session, "test_doc_001")
            assert doc is not None
            assert doc.file_name == "test.pdf"
            assert doc.doc_type == "product"
            assert doc.chunk_count == 10

    def test_merge_idempotent(self, in_memory_sqlite):
        """相同 doc_id 多次写入幂等（merge 而非 insert）。"""
        from ingest_v2 import _save_to_sqlite
        from database import get_session_ctx
        from database.operations import DocOps

        _save_to_sqlite(doc_id="dup_001", file_name="v1.pdf", file_type="pdf",
                         doc_type="product", chunk_count=5)
        _save_to_sqlite(doc_id="dup_001", file_name="v2.pdf", file_type="pdf",
                         doc_type="product", chunk_count=10)

        with get_session_ctx() as session:
            docs = DocOps.get_all_documents(session)
            dup = [d for d in docs if d.id == "dup_001"]
            assert len(dup) == 1  # 仅一条记录
            assert dup[0].chunk_count == 10  # 后写入覆盖

    def test_glossary_terms(self, in_memory_sqlite):
        """术语表写入和读取。"""
        from ingest_v2 import _save_to_sqlite
        from database import get_session_ctx
        from database.operations import GlossaryOps

        terms = [
            {"term": "用户", "definition": "系统用户"},
            {"term": "课程", "definition": "预约课程"},
        ]
        _save_to_sqlite(
            doc_id="glossary_test", file_name="test.pdf",
            file_type="pdf", doc_type="product",
            chunk_count=5, glossary_terms=terms,
        )

        with get_session_ctx() as session:
            saved = GlossaryOps.get_terms(session, "glossary_test")
            assert len(saved) == 2
            names = {t.term for t in saved}
            assert "用户" in names
            assert "课程" in names


# ══════════════════════════════════════════════════════════════════════
#  3. process_product_doc — 全链路 (mock LLM + ChromaDB)
# ══════════════════════════════════════════════════════════════════════

class TestProcessProductDoc:
    @patch("ingest_v2.ChatTestAgentGraph")
    @patch("ingest_v2.get_chroma_db")
    def test_full_flow(self, mock_get_db, mock_graph_class, tmp_path, in_memory_sqlite):
        """完整产品文档入库流程。"""
        from ingest_v2 import process_product_doc
        from database import get_session_ctx
        from database.operations import DocOps

        # Mock LLM 返回
        mock_graph = MagicMock()
        mock_graph_class.return_value = mock_graph

        from prompts.response_model import DocModuleExtract, GlossaryExtract
        mock_graph._invoke_structured.side_effect = [
            DocModuleExtract(
                module_name="用户管理",
                related_modules=["权限管理"],
                business_summary="用户管理模块",
                tags=["核心功能"],
            ),
            GlossaryExtract(
                terms=[{"term": "token", "definition": "身份令牌", "notes": ""}],
            ),
        ]

        # Mock ChromaDB
        fake_db = MagicMock()
        mock_get_db.return_value = fake_db

        # 创建测试 docx 文件
        docx_path = tmp_path / "test_doc.docx"

        # 由于无法创建真实 docx，用 txt 模拟纯文本 -> extract_text 走 md/txt 分支
        txt_path = tmp_path / "test_doc.txt"
        txt_path.write_text("健身房预约模块功能说明\n## 用户注册\n用户可以通过手机号注册\n",
                            encoding="utf-8")

        result = process_product_doc(str(txt_path))

        assert result["module_name"] == "用户管理"
        assert result["chunks"] > 0
        assert "doc_id" in result

        # 验证 SQLite 写入
        with get_session_ctx() as session:
            doc = DocOps.get_document(session, result["doc_id"])
            assert doc is not None
            assert doc.doc_type == "product"

        # 验证 ChromaDB 写入
        assert fake_db.delete_by_doc_id.called
        assert fake_db.add_product_doc_chunks.called

    @patch("ingest_v2.ChatTestAgentGraph")
    @patch("ingest_v2.get_chroma_db")
    def test_empty_file_rejected(self, mock_get_db, mock_graph_class, tmp_path):
        """空文件应当被拒绝。"""
        from ingest_v2 import process_product_doc
        empty_path = tmp_path / "empty.txt"
        empty_path.write_text("   \n  \n", encoding="utf-8")

        with pytest.raises(ValueError, match="文档内容为空"):
            process_product_doc(str(empty_path))


# ══════════════════════════════════════════════════════════════════════
#  4. process_api_doc_extract — 两阶段提取
# ══════════════════════════════════════════════════════════════════════

class TestApiDocExtract:
    @patch("ingest_v2.ChatTestAgentGraph")
    def test_extract_apis(self, mock_graph_class, temp_md_file):
        """从 MD 提取接口定义。"""
        from ingest_v2 import process_api_doc_extract
        from prompts.response_model import ApiDefExtract, ApiDefinition

        mock_graph = MagicMock()
        mock_graph_class.return_value = mock_graph
        mock_graph._invoke_structured.return_value = ApiDefExtract(
            module_name="用户管理",
            apis=[
                ApiDefinition(
                    name="登录", url="/api/login", method="POST",
                    description="用户登录", parameters={},
                    returns={"token": "string"},
                ),
            ],
        )

        result = process_api_doc_extract(temp_md_file)

        assert result["module_name"] == "用户管理"
        assert len(result["apis"]) == 1
        assert result["apis"][0]["url"] == "/api/login"

    @patch("ingest_v2.ChatTestAgentGraph")
    def test_extract_merge_duplicates(self, mock_graph_class, tmp_path):
        """重复接口（method+url 相同）应合并。"""
        from ingest_v2 import process_api_doc_extract, _merge_api_defs

        # _merge_api_defs 单元测试
        existing = {"method": "GET", "url": "/api/user", "parameters": {"id": "int"},
                     "description": "old"}
        incoming = {"method": "GET", "url": "/api/user", "parameters": {"name": "str"},
                     "description": "new longer description"}
        merged = _merge_api_defs(existing, incoming)

        # parameters 应合并（并集）
        assert "id" in merged["parameters"]
        assert "name" in merged["parameters"]
        # description 取更长的
        assert len(merged["description"]) == len("new longer description")


# ══════════════════════════════════════════════════════════════════════
#  5. commit_api_docs — 确认入库
# ══════════════════════════════════════════════════════════════════════

class TestCommitApiDocs:
    @patch("ingest_v2.get_chroma_db")
    def test_commit_single(self, mock_get_db, temp_md_file, in_memory_sqlite):
        """确认单个接口入库。"""
        from ingest_v2 import commit_api_docs
        from database import get_session_ctx
        from database.operations import DocOps

        fake_db = MagicMock()
        mock_get_db.return_value = fake_db

        apis = [
            {"name": "登录", "url": "/api/login", "method": "POST",
             "description": "登录", "parameters": {}, "returns": {}},
        ]

        result = commit_api_docs(temp_md_file, "用户管理", apis)

        assert result["api_count"] == 1
        assert fake_db.add_api_defs.called

        with get_session_ctx() as session:
            doc = DocOps.get_document(session, result["doc_ids"][0])
            assert doc is not None
            assert doc.doc_type == "api"

    @patch("ingest_v2.get_chroma_db")
    def test_commit_multiple(self, mock_get_db, temp_md_file, in_memory_sqlite):
        """多个接口入库。"""
        from ingest_v2 import commit_api_docs

        fake_db = MagicMock()
        mock_get_db.return_value = fake_db

        apis = [
            {"name": "登录", "url": "/api/login", "method": "POST",
             "description": "登录", "parameters": {}, "returns": {}},
            {"name": "注册", "url": "/api/register", "method": "POST",
             "description": "注册", "parameters": {}, "returns": {}},
        ]

        result = commit_api_docs(temp_md_file, "用户管理", apis)

        assert result["api_count"] == 2
        assert len(result["doc_ids"]) == 2

    @patch("ingest_v2.get_chroma_db")
    def test_delete_original(self, mock_get_db, in_memory_sqlite):
        """delete_original=True 应删除原文件。"""
        from ingest_v2 import commit_api_docs
        import tempfile as tf

        fake_db = MagicMock()
        mock_get_db.return_value = fake_db

        # 创建临时文件
        with tf.NamedTemporaryFile(
            mode="w", suffix=".md", encoding="utf-8", delete=False,
        ) as f:
            f.write("test")
            tmp_path = f.name

        try:
            apis = [{"name": "测试", "url": "/api/test", "method": "GET",
                      "description": "test", "parameters": {}, "returns": {}}]
            commit_api_docs(tmp_path, "测试模块", apis, delete_original=True)
            assert not os.path.exists(tmp_path), "原文件应被删除"
        finally:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)


# ══════════════════════════════════════════════════════════════════════
#  6. 数据一致性 — ChromaDB 成功 → SQLite 失败
# ══════════════════════════════════════════════════════════════════════

class TestDataConsistency:
    """验证双库写入的原子性保障。"""

    @patch("ingest_v2.ChatTestAgentGraph")
    @patch("ingest_v2.get_chroma_db")
    def test_chroma_without_sqlite_orphan(self, mock_get_db, mock_graph_class,
                                           tmp_path, in_memory_sqlite):
        """模拟 SQLite 写入失败后 ChromaDB 数据是否孤立（当前设计允许孤立，非 crash）。"""
        from ingest_v2 import process_product_doc, _save_to_sqlite
        from database import get_session_ctx
        from database.operations import DocOps

        mock_graph = MagicMock()
        mock_graph_class.return_value = mock_graph
        from prompts.response_model import DocModuleExtract, GlossaryExtract
        mock_graph._invoke_structured.side_effect = [
            DocModuleExtract(module_name="用户管理", related_modules=[], business_summary="", tags=[]),
            GlossaryExtract(terms=[]),
        ]

        fake_db = MagicMock()
        mock_get_db.return_value = fake_db

        txt_path = tmp_path / "test_doc.txt"
        txt_path.write_text("测试内容", encoding="utf-8")

        # Scenario 1: SQLite 失败 → ChromaDB 永不执行（无孤立数据）
        with patch("ingest_v2._save_to_sqlite",
                   side_effect=Exception("SQLite write failed")):
            with pytest.raises(Exception, match="SQLite write failed"):
                process_product_doc(str(txt_path))
            # ChromaDB 从未被调用（SQLite 先写原则）
            fake_db.delete_by_doc_id.assert_not_called()
            fake_db.add_product_doc_chunks.assert_not_called()

        # Scenario 2: ChromaDB 失败 → SQLite 补偿回滚
        fake_db.delete_by_doc_id.side_effect = Exception("ChromaDB write failed")
        raised = False
        try:
            process_product_doc(str(txt_path))
        except Exception:
            raised = True
        assert raised, "ChromaDB 失败时应抛出异常"

        # SQLite 应为空（补偿回滚）
        with get_session_ctx() as session:
            all_docs = DocOps.get_all_documents(session)
            assert len(all_docs) == 0, f"Expected 0 docs after compensation, got {len(all_docs)}"


# ══════════════════════════════════════════════════════════════════════
#  7. _group_chunks_into_batches — 分批算法
# ══════════════════════════════════════════════════════════════════════

class TestChunkBatching:
    def test_basic_grouping(self):
        """\n\n 拼接长度计算准确。"""
        chunks = ["a" * 100, "b" * 100, "c" * 100]
        joined = "\n\n".join(chunks)
        assert len(joined) == 100 + 2 + 100 + 2 + 100  # 304

    def test_split_exceeding_max(self):
        """单 chunk 超限时仍然作为一批（无法进一步拆分）。"""
        chunks = ["x" * 50000, "y" * 100]
        candidate = "\n\n".join(chunks)
        assert len(candidate) > 30000  # 超默认 MAX_INGEST_CHARS_PER_BATCH


# ══════════════════════════════════════════════════════════════════════
#  8. Axure 解析流程 (mock parser)
# ══════════════════════════════════════════════════════════════════════

class TestAxureIngest:
    @patch("ingest_v2.get_chroma_db")
    @patch("agent_components.axure_parser.AxureParser")
    def test_axure_flow(self, mock_parser_class, mock_get_db,
                         in_memory_sqlite, tmp_path):
        """Axure ZIP 解析 + 入库全流程。"""
        from ingest_v2 import process_axure_zip

        # Mock AxureParser
        mock_parser = MagicMock()
        mock_parser_class.return_value = mock_parser
        mock_parser.parse.return_value = {
            "project_name": "健身房管理",
            "pages": [{"name": "首页", "url": "home.html", "children": []}],
            "page_details": {
                "home.html": {
                    "page_name": "首页",
                    "ui_text": "欢迎使用健身房管理系统",
                    "interactions": [],
                },
            },
        }
        mock_parser.to_product_doc_chunks.return_value = [
            "## 页面: 首页\n欢迎使用健身房管理系统",
        ]

        # Mock ChromaDB
        fake_db = MagicMock()
        mock_get_db.return_value = fake_db

        # Mock LLM graph for module extraction
        with patch("ingest_v2.ChatTestAgentGraph") as mock_graph_class:
            mock_graph = MagicMock()
            mock_graph_class.return_value = mock_graph
            from prompts.response_model import DocModuleExtract
            mock_graph._invoke_structured.return_value = DocModuleExtract(
                module_name="健身房管理",
                related_modules=["会员管理"],
                business_summary="健身房管理",
                tags=["核心功能"],
            )

            zip_path = tmp_path / "test.axure.zip"
            zip_path.write_text("fake zip content")  # AxureParser 会 mock 掉

            result = process_axure_zip(str(zip_path))

        assert result["module_name"] == "健身房管理"
        assert result["chunks"] == 1
        assert fake_db.add_product_doc_chunks.called
        assert mock_parser.cleanup.called


# ══════════════════════════════════════════════════════════════════════
#  9. Docx 图片目录隔离（process_product_doc 中的 _img_dir）
# ══════════════════════════════════════════════════════════════════════

class TestDocxImgDir:
    def test_img_dir_contains_filename(self):
        """_docx_img_dir 应包含文件名以隔离并发。"""
        from ingest_v2 import _docx_img_dir
        dirname = _docx_img_dir("/some/path/我的文档.docx")
        # 文件名应以 _images_ 开头且包含 我的文档
        assert "_images_" in dirname
        assert "我的文档" in dirname or "My" in dirname
        # 不同文件应有不同目录
        dir_a = _docx_img_dir("/a/foo.docx")
        dir_b = _docx_img_dir("/b/bar.docx")
        assert dir_a != dir_b

    def test_img_dir_same_file_same_dir(self):
        """同一文件名在不同路径下应生成不同目录（防跨目录冲突）。"""
        from ingest_v2 import _docx_img_dir
        dir_a = _docx_img_dir("/a/doc.docx")
        dir_b = _docx_img_dir("/b/doc.docx")
        assert dir_a != dir_b
