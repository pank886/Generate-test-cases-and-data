"""Phase A 产品文档上传支持 Excel (.xlsx) 测试。

覆盖范围:
  1. _extract_text / _extract_xlsx — 全 sheet 行式文本化
     （单/多 sheet、空行跳过、单元格类型、全空工作簿、旧版 .xls 拒绝）
  2. process_product_doc 全链入库 — 真 .xlsx 文件（mock LLM + ChromaDB + 本地 SQLite）
  3. HTTP 上传路由逻辑 — .xlsx 接受并入 uploads/product；.xls 拒绝 400

说明:
  - 全链测试复用 tests/test_ingest_main_flow.py 的 mock 手法（patch ChatTestAgentGraph /
    get_chroma_db），额外 patch _get_llm 使摘要段离线走"补偿空摘要"路径，不触发真实网络。
  - HTTP 用例直接 await 路由协程（不经 lifespan），保持轻量、无真实磁盘/服务副作用。

运行:
  cd <项目根目录>
  pytest tests/test_phase_a_xlsx_upload.py -v
"""

import asyncio
import datetime as _dt
import io
import os
import sys
import tempfile
from unittest.mock import MagicMock, patch

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


def _make_xlsx(path, sheets):
    """生成临时 .xlsx。sheets: [(sheet名, rows)]，至少一个 sheet。"""
    from openpyxl import Workbook
    wb = Workbook()
    first = True
    for name, rows in sheets:
        ws = wb.active if first else wb.create_sheet()
        ws.title = name
        for row in rows:
            ws.append(list(row))
        first = False
    wb.save(path)
    wb.close()


@pytest.fixture
def in_memory_sqlite():
    """把 database 模块指向临时 SQLite，隔离真实 data/app.db。"""
    os.environ.setdefault("EMBEDDING_MODEL", "test-model")
    import database
    database.DB_DIR = tempfile.mkdtemp()
    database.DB_PATH = os.path.join(database.DB_DIR, "test.db")
    database.init_db()
    return database


# ══════════════════════════════════════════════════════════════════════
#  1. _extract_text / _extract_xlsx
# ══════════════════════════════════════════════════════════════════════

class TestExtractXlsx:
    def test_single_sheet_rows_joined_with_sheet_header(self, tmp_path):
        """单 sheet：含工作表头，每行非空单元格以 ' | ' 连接，行序保持。"""
        from ingest.extractors import _extract_text
        p = tmp_path / "需求.xlsx"
        _make_xlsx(p, [("Sheet1", [
            ["模块", "功能点", "说明"],
            ["用户注册", "手机号+密码", "支持验证码"],
        ])])
        text = _extract_text(str(p))
        assert "[工作表: Sheet1]" in text
        assert "模块 | 功能点 | 说明" in text
        assert "用户注册 | 手机号+密码 | 支持验证码" in text

    def test_multi_sheets_empty_row_and_empty_sheet_skipped(self, tmp_path):
        """多 sheet 全在；中间全空行被跳过；全空 sheet 不输出。"""
        from ingest.extractors import _extract_text
        p = tmp_path / "multi.xlsx"
        _make_xlsx(p, [
            ("SheetA", [["A", "1"], [], ["B", "2"], []]),
            ("SheetB", [["X", "Y"]]),
            ("SheetEmpty", []),
        ])
        text = _extract_text(str(p))
        assert "[工作表: SheetA]" in text
        assert "[工作表: SheetB]" in text
        assert "[工作表: SheetEmpty]" not in text
        # 只有 3 行真实内容（SheetA 2 行 + SheetB 1 行）
        assert sum(" | " in line for line in text.splitlines()) == 3

    def test_cell_types_textified(self, tmp_path):
        """数字/浮点/布尔/日期时间/date/字符串均入文本；None 跳过。"""
        from ingest.extractors import _extract_text
        p = tmp_path / "types.xlsx"
        dt = _dt.datetime(2026, 9, 3, 10, 30, 0)
        d = _dt.date(2026, 9, 3)
        _make_xlsx(p, [("S", [[123, 4.5, True, None, dt, d, "abc"]])])
        text = _extract_text(str(p))
        # openpyxl 把纯 date 读回为午夜 datetime → "2026-09-03 00:00:00"
        assert "123 | 4.5 | True | 2026-09-03 10:30:00 | 2026-09-03 00:00:00 | abc" in text

    def test_all_blank_workbook_returns_empty(self, tmp_path):
        """全空工作簿 → 空串（process_product_doc 将报「文档内容为空」）。"""
        from ingest.extractors import _extract_text
        p = tmp_path / "blank.xlsx"
        _make_xlsx(p, [("S", [["", ""]])])
        assert _extract_text(str(p)) == ""

    def test_legacy_xls_still_unsupported(self, tmp_path):
        """旧版 .xls 不被 openpyxl 读取 → 维持「不支持的文件类型」。"""
        from ingest.extractors import _extract_text
        p = tmp_path / "old.xls"
        p.write_bytes(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1 legacy-biff")
        with pytest.raises(ValueError, match="不支持的文件类型"):
            _extract_text(str(p))


# ══════════════════════════════════════════════════════════════════════
#  2. process_product_doc 全链（真 .xlsx）
# ══════════════════════════════════════════════════════════════════════

class TestProcessProductDocXlsx:
    @patch("agent_components.graph.nodes._get_llm")
    @patch("ingest.pipelines.get_chroma_db")
    @patch("ingest.pipelines.ChatTestAgentGraph")
    def test_full_ingest_flow(self, mock_graph_class, mock_get_db,
                              mock_llm_getter, tmp_path, in_memory_sqlite):
        """真 .xlsx 走完整产品文档入库：模块提取→SQLite→ChromaDB，file_type=xlsx。"""
        from ingest_v2 import process_product_doc
        from database import get_session_ctx
        from database.operations import DocOps
        from prompts.response_model import DocModuleExtract, GlossaryExtract

        # Mock LLM（模块/术语两段）
        mock_graph = MagicMock()
        mock_graph_class.return_value = mock_graph
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
        # 摘要段离线：llm.invoke 抛错 → 走"整批失败→补偿空摘要"路径，无真实网络
        fake_llm = MagicMock()
        fake_llm.invoke.side_effect = RuntimeError("offline-test")
        mock_llm_getter.return_value = fake_llm

        # 真 .xlsx 产品文档
        xlsx = tmp_path / "req_fields.xlsx"
        _make_xlsx(xlsx, [("需求", [
            ["功能模块", "用例点", "说明"],
            ["用户注册", "手机号校验", "11 位数字"],
            ["用户注册", "密码复杂度", "8-16 位含字母数字"],
        ])])

        result = process_product_doc(str(xlsx))

        assert result["module_name"] == "用户管理"
        assert result["chunks"] > 0
        assert "doc_id" in result

        # SQLite 落库：真实扩展名 file_type=xlsx、doc_type=product（session 内读取防 detached）
        with get_session_ctx() as session:
            doc = DocOps.get_document(session, result["doc_id"])
            assert doc is not None
            assert doc.file_type == "xlsx"
            assert doc.doc_type == "product"

        # ChromaDB 写入
        assert fake_db.delete_by_doc_id.called
        assert fake_db.add_product_doc_chunks.called


# ══════════════════════════════════════════════════════════════════════
#  3. 上传路由逻辑（直接 await 协程，无 lifespan）
# ══════════════════════════════════════════════════════════════════════

class TestUploadRouteXlsx:
    def test_xlsx_accepted_and_saved_to_product(self, tmp_path, monkeypatch):
        """.xlsx 走白名单 → 200/成功，落 uploads/product，后台收到 file_type=product。"""
        import infrastructure.config as config
        import web.state as wstate
        import web.tasks as wtasks
        from starlette.background import BackgroundTasks
        from starlette.datastructures import UploadFile

        monkeypatch.setattr(config, "BASE_DIR", str(tmp_path))

        async def _fake_create():
            return "task_xlsx_1"

        monkeypatch.setattr(wstate, "_create_task", _fake_create)

        async def _fake_bg(*args, **kwargs):
            return None

        monkeypatch.setattr(wtasks, "_process_file_bg", _fake_bg)

        # 真实 .xlsx 字节
        from openpyxl import Workbook
        bio = io.BytesIO()
        wb = Workbook()
        ws = wb.active
        ws.title = "S1"
        ws.append(["a", "b"])
        wb.save(bio)
        wb.close()
        bio.seek(0)

        from web.routes.files import upload_file
        up = UploadFile(filename="req_fields.xlsx", file=bio)
        resp = asyncio.run(upload_file(file=up, background_tasks=BackgroundTasks()))

        assert isinstance(resp, dict)
        assert resp.get("success") is True
        assert resp.get("task_id") == "task_xlsx_1"
        saved = os.path.join(str(tmp_path), "uploads", "product", "req_fields.xlsx")
        assert os.path.exists(saved)

    def test_xls_rejected_with_400(self, monkeypatch):
        """旧版 .xls 不在白名单 → 400「不支持的文件类型」。"""
        from starlette.background import BackgroundTasks
        from starlette.datastructures import UploadFile

        import infrastructure.config as config
        monkeypatch.setattr(config, "BASE_DIR", tempfile.mkdtemp())

        from web.routes.files import upload_file
        up = UploadFile(filename="legacy.xls", file=io.BytesIO(b"dummy"))
        resp = asyncio.run(upload_file(file=up, background_tasks=BackgroundTasks()))

        assert resp.status_code == 400
        assert "不支持的文件类型" in resp.body.decode("utf-8")
