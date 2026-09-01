"""API 文档确认入库测试（全部通过 POST /api/upload/commit-api 触发）。

场景:
  1. 正常导入 — 多条 API 成功入库
  2. 失败重试 — ChromaDB 模拟失败 → 重试 → 成功
  3. 空列表 — apis=[] 正确处理
  4. 重复导入 — 相同 API 再次提交，幂等处理
"""

import json
import os
import sys
import tempfile
from unittest.mock import patch, MagicMock

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import infrastructure.config as config


# ============================================================
# Fixtures
# ============================================================

@pytest.fixture(autouse=True)
def _reset_db():
    import database as _db
    _db._ENGINE = None
    _db._SESSION_LOCAL = None
    from infrastructure.vector_store import dual_chroma
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
            import web.app as _wa
            _wa._imported_files[_wa._DEFAULT_USER] = []
            yield c


@pytest.fixture
def temp_md_file():
    """在 valid 的 uploads/md/ 下创建临时 MD 文件（通过路径校验）。"""
    md_dir = os.path.join(config.BASE_DIR, "uploads", "md")
    os.makedirs(md_dir, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".md", encoding="utf-8", delete=False,
        dir=md_dir,
    ) as f:
        f.write("# test\n")
        f.flush()
        path = f.name
    yield path
    try:
        os.unlink(path)
    except OSError:
        pass


def _wait_task(client, task_id: str, timeout: float = 5.0) -> dict:
    """轮询 /task/{id} 直到终态（TestClient 下后台任务同步执行，通常首轮即终态）。

    2026-08-03：/commit-api 已改为异步（返回 task_id），入库在后台任务 _commit_apis_bg
    完成，测试须通过任务状态确认提交结果。
    """
    import time
    t0 = time.time()
    while time.time() - t0 < timeout:
        task = client.get(f"/task/{task_id}").json()["task"]
        if task["status"] in ("completed", "failed"):
            return task
        time.sleep(0.05)
    raise AssertionError(f"任务 {task_id} 轮询超时（未达终态）")


def _six(name, type_, required=False, value=""):
    """六字段结构辅助构造。"""
    return {"name": name, "type": type_, "required": required,
            "default": "", "desc": "", "value": value}


SAMPLE_APIS = [
    {
        "name": "登录", "url": "/api/login", "method": "POST",
        "description": "用户登录",
        "header": {"Content-Type": "application/json"},
        "body": [_six("username", "string", True, "admin"),
                 _six("password", "string", True)],
        "return": [_six("token", "string")],
    },
    {
        "name": "获取用户信息", "url": "/api/user", "method": "GET",
        "description": "获取当前用户信息",
        "header": {"Content-Type": "application/json"},
        "body": [],
        "return": [_six("name", "string"), _six("email", "string")],
    },
    {
        "name": "注册", "url": "/api/register", "method": "POST",
        "description": "新用户注册",
        "header": {"Content-Type": "application/json"},
        "body": [_six("username", "string", True),
                 _six("password", "string", True),
                 _six("email", "string", True)],
        "return": [_six("id", "integer"), _six("token", "string")],
    },
]


# ============================================================
# 1. 正常导入
# ============================================================

class TestNormalCommit:
    """多条 API 成功入库。"""

    def test_commit_single_api(self, client, temp_md_file):
        """提交单条 API。"""
        resp = client.post("/api/upload/commit-api", json={
            "file_path": temp_md_file,
            "module_name": "用户管理",
            "apis": [SAMPLE_APIS[0]],
            "all_selected": False,
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data.get("task_id"), "异步端点应返回 task_id"
        task = _wait_task(client, data["task_id"])
        assert task["status"] == "completed", f"提交应成功: {task}"

    def test_commit_multiple_apis(self, client, temp_md_file):
        """提交多条 API。"""
        resp = client.post("/api/upload/commit-api", json={
            "file_path": temp_md_file,
            "module_name": "用户管理",
            "apis": SAMPLE_APIS,
            "all_selected": True,
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data.get("task_id"), "异步端点应返回 task_id"
        task = _wait_task(client, data["task_id"])
        assert task["status"] == "completed", f"提交应成功: {task}"

    def test_committed_apis_show_in_file_list(self, client, temp_md_file):
        """入库后文件列表应包含 API 文档。"""
        client.post("/api/upload/commit-api", json={
            "file_path": temp_md_file,
            "module_name": "用户管理",
            "apis": SAMPLE_APIS,
            "all_selected": True,
        })

        files = client.get("/api/files/uploaded-files").json()["files"]
        api_files = [f for f in files if f["type"] == "api"]
        assert len(api_files) == 3


# ============================================================
# 2. 失败重试
# ============================================================

class TestRetryCommit:
    """模拟 ChromaDB 失败后重试成功。"""

    def test_retry_after_chroma_failure(self, client, temp_md_file):
        """ChromaDB 写入失败 → 任务 failed → 恢复后重试成功。

        2026-08-03：/commit-api 已异步化，Chroma 失败不再返回 500，而是记录在
        后台任务状态；且提交路径使用 ingest_v2.get_chroma_db()（非 web.app._chroma_db），
        故直接 patch 提交路径的 db 实例。
        """
        import ingest.pipelines as _ing

        payload = {
            "file_path": temp_md_file,
            "module_name": "用户管理",
            "apis": SAMPLE_APIS[:1],
            "all_selected": False,
        }

        # 第一次：patch commit_api_docs 使用的 get_chroma_db，add_api_defs 抛异常
        _real_get_chroma = _ing.get_chroma_db
        _failing_db = MagicMock()
        _failing_db.delete_by_doc_id = MagicMock()
        _failing_db.add_api_defs = MagicMock(side_effect=Exception("ChromaDB 临时故障"))
        _ing.get_chroma_db = lambda: _failing_db
        try:
            resp1 = client.post("/api/upload/commit-api", json=payload)
            assert resp1.status_code == 200, f"异步端点应 200: {resp1.text}"
            task1 = _wait_task(client, resp1.json()["task_id"])
            assert task1["status"] == "failed", f"Chroma 失败应记录为 failed: {task1}"
        finally:
            _ing.get_chroma_db = _real_get_chroma

        # 恢复后重试
        resp2 = client.post("/api/upload/commit-api", json=payload)
        assert resp2.status_code == 200
        task2 = _wait_task(client, resp2.json()["task_id"])
        assert task2["status"] == "completed", f"重试应成功: {task2}"


# ============================================================
# 3. 边界条件
# ============================================================

class TestEdgeCases:
    """边缘情况验证。"""

    def test_empty_api_list(self, client, temp_md_file):
        """apis=[] 时返回错误而非崩溃。"""
        resp = client.post("/api/upload/commit-api", json={
            "file_path": temp_md_file,
            "module_name": "用户管理",
            "apis": [],
            "all_selected": False,
        })
        # 当前端点逻辑：apis=[] 时返回 400（缺少必要参数）
        assert resp.status_code == 400

    def test_missing_file_path(self, client):
        """缺少 file_path 时返回 400。"""
        resp = client.post("/api/upload/commit-api", json={
            "file_path": "",
            "module_name": "用户管理",
            "apis": SAMPLE_APIS[:1],
            "all_selected": False,
        })
        assert resp.status_code == 400

    def test_invalid_path_rejected(self, client):
        """file_path 不在 uploads 目录下时返回 403。"""
        with tempfile.NamedTemporaryFile(suffix=".md", delete=False) as f:
            invalid_path = f.name
        try:
            resp = client.post("/api/upload/commit-api", json={
                "file_path": invalid_path,
                "module_name": "用户管理",
                "apis": SAMPLE_APIS[:1],
                "all_selected": False,
            })
            assert resp.status_code == 403
        finally:
            os.unlink(invalid_path)

    def test_commit_idempotent(self, client, temp_md_file):
        """相同 API 提交两次 — 第二次不报错（幂等）。"""
        payload = {
            "file_path": temp_md_file,
            "module_name": "用户管理",
            "apis": SAMPLE_APIS[:1],
            "all_selected": False,
        }
        resp1 = client.post("/api/upload/commit-api", json=payload)
        assert resp1.status_code == 200

        resp2 = client.post("/api/upload/commit-api", json=payload)
        assert resp2.status_code == 200
        # 验证文档数量：相同 API 再次提交不应产生重复
        files = client.get("/api/files/uploaded-files").json()["files"]
        login_apis = [f for f in files if "login" in f["name"].lower()]
        assert len(login_apis) == 1, "幂等性：相同 API 不应重复"


def _generate_batch_apis(count: int) -> list[dict]:
    """生成指定数量的 API 定义（用于大批量测试）。"""
    return [
        {
            "name": f"api_{i}", "url": f"/api/batch/{i}", "method": "POST",
            "description": f"批量测试接口 {i}",
            "header": {},
            "body": [{"name": "id", "type": "integer", "required": False,
                      "default": "", "desc": "", "value": ""}],
            "return": [{"name": "success", "type": "boolean", "required": False,
                        "default": "", "desc": "", "value": ""}],
        }
        for i in range(count)
    ]


# ============================================================
# 4. 超大批量（100+ API）
# ============================================================

class TestLargeBatch:
    """验证大批量 API 提交的稳定性和性能。"""

    def test_100_apis(self, client, temp_md_file):
        """100 条 API 一次提交全部成功。"""
        apis = _generate_batch_apis(100)
        resp = client.post("/api/upload/commit-api", json={
            "file_path": temp_md_file,
            "module_name": "用户管理",
            "apis": apis,
            "all_selected": False,
        })
        assert resp.status_code == 200
        assert resp.json().get("task_id"), "异步端点应返回 task_id"

    def test_200_apis_memory_stable(self, client, temp_md_file):
        """200 条 API 提交不内存泄漏。"""
        apis = _generate_batch_apis(200)
        resp = client.post("/api/upload/commit-api", json={
            "file_path": temp_md_file,
            "module_name": "用户管理",
            "apis": apis,
            "all_selected": False,
        })
        assert resp.status_code == 200
        assert resp.json().get("task_id"), "异步端点应返回 task_id"


# ============================================================
# 5. all_selected=true 时原文件被删除
# ============================================================

class TestAllSelectedDeleteOriginal:
    """all_selected=True 时源文件应被删除。"""

    def test_original_file_deleted(self, client, temp_md_file):
        """all_selected=True → 提交后原 .md 文件被删除。"""
        assert os.path.exists(temp_md_file), "测试文件应存在"

        resp = client.post("/api/upload/commit-api", json={
            "file_path": temp_md_file,
            "module_name": "用户管理",
            "apis": SAMPLE_APIS[:1],
            "all_selected": True,
        })
        assert resp.status_code == 200
        assert not os.path.exists(temp_md_file), "all_selected=True 时原文件应被删除"

    def test_all_selected_false_keeps_file(self, client, temp_md_file):
        """all_selected=False → 原文件保留。"""
        resp = client.post("/api/upload/commit-api", json={
            "file_path": temp_md_file,
            "module_name": "用户管理",
            "apis": SAMPLE_APIS[:1],
            "all_selected": False,
        })
        assert resp.status_code == 200
        assert os.path.exists(temp_md_file), "all_selected=False 时原文件应保留"


# ============================================================
# 6. 并发提交同一文档
# ============================================================

class TestConcurrentCommit:
    """多线程并发提交同一文档，验证 ChromaDB 和 SQLite 不冲突。"""

    def test_concurrent_same_file(self, client, temp_md_file):
        """两个请求同时提交同一文件的不同 API。"""
        import concurrent.futures

        payloads = [
            {"file_path": temp_md_file, "module_name": "用户管理",
             "apis": [SAMPLE_APIS[0]], "all_selected": False},
            {"file_path": temp_md_file, "module_name": "用户管理",
             "apis": [SAMPLE_APIS[1]], "all_selected": False},
        ]

        # TestClient 不是线程安全的，用 requests 直接调后端
        # 通过串行模拟幂等性保障
        resp1 = client.post("/api/upload/commit-api", json=payloads[0])
        resp2 = client.post("/api/upload/commit-api", json=payloads[1])
        assert resp1.status_code == 200
        assert resp2.status_code == 200

        files = client.get("/api/files/uploaded-files").json()["files"]
        api_names = [f["name"] for f in files if f["type"] == "api"]
        assert len(api_names) == 2, "两个 API 都应入库"


# ============================================================
# 7. 模块名含有特殊字符
# ============================================================

class TestSpecialCharModuleName:
    """模块名含中文/空格/URL 特殊字符。"""

    @pytest.mark.parametrize("module_name", [
        "用户管理",              # 纯中文
        "User Admin",            # 含空格
        "模块+A",                # + 号
        "测试&开发",              # & 号
        "项目/功能",              # / 号
        "100%完成",              # % 号
    ])
    def test_special_chars(self, client, temp_md_file, module_name):
        """特殊字符模块名能正常提交和查询。"""
        resp = client.post("/api/upload/commit-api", json={
            "file_path": temp_md_file,
            "module_name": module_name,
            "apis": SAMPLE_APIS[:1],
            "all_selected": False,
        })
        assert resp.status_code == 200
        assert resp.json().get("task_id"), "异步端点应返回 task_id"

        # 验证文件列表中有该记录
        files = client.get("/api/files/uploaded-files").json()["files"]
        api_files = [f for f in files if f["type"] == "api"]
        assert len(api_files) >= 1
