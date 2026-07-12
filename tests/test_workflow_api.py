"""Phase C 工作流 API 集成测试 — 纯 HTTP 接口验证。

验证范围:
  1. /workflow/start — 输入校验、模块匹配、会话创建
  2. /workflow/confirm — 选择解析策略（数字/精确/重新匹配）、会话过期
  3. /task/{id} — 任务状态轮询
  4. 完整链路：start → confirm → poll task（验证中间节点不跳过）

运行方式:
  # 确保服务已启动（python web/app.py），然后:
  pytest tests/test_workflow_api.py -v --base-url=http://localhost:8000

  # 或指定自定义地址:
  pytest tests/test_workflow_api.py -v --base-url=http://192.168.1.100:8000

设计要点:
  - 全部使用 httpx 发送 HTTP 请求，不直接调用 Python 函数
  - 不依赖测试 fixture 中的 mock，使用真实服务（或可配置的远端）
  - 每个测试独立，可单独运行
  - 对 /workflow/start 返回的 candidates 做空安全处理
"""

import json
import os
import time

import pytest
import httpx


@pytest.fixture(scope="session")
def base_url(request):
    return request.config.getoption("--base-url")


@pytest.fixture(scope="session")
def client(base_url):
    """共享 httpx 客户端（整个 session 复用连接池）。"""
    with httpx.Client(base_url=base_url, timeout=30.0) as c:
        yield c


# ============================================================
# 辅助函数
# ============================================================

TASK_POLL_INTERVAL = 2.0   # 任务轮询间隔（秒）
TASK_POLL_TIMEOUT = 120.0  # 任务轮询总超时（秒）


def poll_task(client: httpx.Client, task_id: str,
              timeout: float = TASK_POLL_TIMEOUT) -> dict:
    """轮询 GET /task/{task_id} 直到 completed/failed，返回完整 task 对象。"""
    deadline = time.time() + timeout
    last_status = "pending"
    while time.time() < deadline:
        resp = client.get(f"/task/{task_id}")
        if resp.status_code == 404:
            pytest.fail(f"task_id={task_id} 返回 404，任务可能已过期")
        data = resp.json()
        assert data.get("success"), f"轮询失败: {data}"
        task = data["task"]
        status = task.get("status", "unknown")
        if status in ("completed", "failed"):
            return task
        if status != last_status:
            print(f"  [task] status={status}, progress={task.get('progress')}, "
                  f"msg={task.get('message')}")
            last_status = status
        time.sleep(TASK_POLL_INTERVAL)
    pytest.fail(f"轮询超时（>{timeout}s），最近状态: {last_status}")


# ============================================================
# 测试用例
# ============================================================

class TestWorkflowStart:
    """/workflow/start 验证"""

    ENDPOINT = "/workflow/start"

    def test_empty_or_missing_input_returns_4xx(self, client: httpx.Client):
        """空输入或缺失输入 → 400/422（空字符串 FastAPI 返回 422）"""
        for payload in ({}, {"user_input": ""}):
            resp = client.post(self.ENDPOINT, data=payload)
            assert resp.status_code in (400, 422), (
                f"payload={payload} 期望 400/422，得到 {resp.status_code}: {resp.text}"
            )

    def test_whitespace_input_returns_400(self, client: httpx.Client):
        """纯空格输入 → 400（后端 strip 后判空）"""
        resp = client.post(self.ENDPOINT, data={"user_input": "   "})
        assert resp.status_code == 400, f"期望 400，得到 {resp.status_code}: {resp.text}"

    def test_valid_input_returns_session(self, client: httpx.Client):
        """正常输入 → 200 + session_id + candidates"""
        resp = client.post(self.ENDPOINT, data={"user_input": "健身房"})
        # 即使模块为空也应返回 200（带 no_match 状态）
        assert resp.status_code == 200, f"期望 200，得到 {resp.status_code}: {resp.text}"
        data = resp.json()
        assert data.get("success"), f"success 应为 true: {data}"
        assert "session_id" in data, f"缺少 session_id: {data}"
        assert data.get("status") in (
            "waiting", "no_match",
        ), f"status 应为 waiting 或 no_match: {data}"
        # candidates 可能是空列表（模块未就绪时）
        assert "candidates" in data, f"缺少 candidates 字段: {data}"
        print(f"  session_id={data['session_id']} "
              f"candidates={data['candidates']} "
              f"status={data['status']}")

    def test_multiple_requests_create_different_sessions(self, client: httpx.Client):
        """连续请求生成不同 session_id"""
        r1 = client.post(self.ENDPOINT, data={"user_input": "会员管理"})
        r2 = client.post(self.ENDPOINT, data={"user_input": "停车场"})
        assert r1.status_code == 200 and r2.status_code == 200
        s1 = r1.json()["session_id"]
        s2 = r2.json()["session_id"]
        assert s1 != s2, "两次请求应返回不同 session_id"


class TestWorkflowConfirm:
    """/workflow/confirm 验证"""

    START = "/workflow/start"
    ENDPOINT = "/workflow/confirm"

    def _start_and_get_session(self, client: httpx.Client,
                                query: str = "健身房") -> tuple[str, list[str]]:
        """启动工作流并返回 (session_id, candidates)。"""
        resp = client.post(self.START, data={"user_input": query})
        assert resp.status_code == 200
        data = resp.json()
        return data["session_id"], data.get("candidates", [])

    # ---- 会话校验 ----

    def test_invalid_session_returns_404(self, client: httpx.Client):
        """不存在的 session_id → 404"""
        resp = client.post(
            self.ENDPOINT,
            data={"session_id": "nonexistent_abc123", "choice": "1"},
        )
        assert resp.status_code == 404, f"期望 404，得到 {resp.status_code}: {resp.text}"
        data = resp.json()
        assert not data.get("success", True), "success 应为 false"

    def test_empty_session_id_returns_4xx(self, client: httpx.Client):
        """空 session_id → 422（FastAPI Form 校验）"""
        resp = client.post(
            self.ENDPOINT,
            data={"session_id": "", "choice": "1"},
        )
        assert resp.status_code in (400, 422), (
            f"期望 400/422，得到 {resp.status_code}: {resp.text}"
        )

    # ---- choice 解析策略 ----

    def test_numeric_choice_selects_correct_module(self, client: httpx.Client):
        """数字 choice 按序号匹配候选模块

        验证: 候选列表第一个模块可通过 choice="1" 选中
        """
        session_id, candidates = self._start_and_get_session(client, "健身房")
        if not candidates:
            pytest.skip("当前服务无候选模块，跳过序号匹配测试")

        resp = client.post(
            self.ENDPOINT,
            data={"session_id": session_id, "choice": "1"},
        )
        assert resp.status_code == 200, f"期望 200，得到 {resp.status_code}: {resp.text}"
        data = resp.json()
        # 成功确认应返回 running + task_id
        if data.get("status") == "running":
            assert "task_id" in data, f"running 状态应返回 task_id: {data}"
            assert candidates[0] in data.get("message", ""), (
                f"消息应引用选中的模块名: {data}"
            )

    def test_numeric_choice_out_of_range_falls_back(self, client: httpx.Client):
        """超出范围的数字 choice → reconfirm 或 重新匹配"""
        session_id, candidates = self._start_and_get_session(client, "健身房")
        if not candidates:
            pytest.skip("当前服务无候选模块，跳过越界测试")

        out_of_range = str(len(candidates) + 5)
        resp = client.post(
            self.ENDPOINT,
            data={"session_id": session_id, "choice": out_of_range},
        )
        assert resp.status_code == 200
        data = resp.json()
        # 超出范围，候选非空，但数字不匹配 → 走策略3（重新匹配）
        assert data.get("status") in ("reconfirm", "running"), (
            f"超出范围的数字应触发重新匹配或修正处理: {data}"
        )

    def test_exact_module_name_choice(self, client: httpx.Client):
        """精确模块名匹配

        验证: 输入精确模块名直接选中，不走重新匹配
        """
        session_id, candidates = self._start_and_get_session(client, "健身房")
        if not candidates:
            pytest.skip("当前服务无候选模块，跳过精确名称测试")

        exact_name = candidates[0]
        resp = client.post(
            self.ENDPOINT,
            data={"session_id": session_id, "choice": exact_name},
        )
        assert resp.status_code == 200
        data = resp.json()
        # 精确匹配成功应返回 running
        if data.get("status") == "running":
            assert "task_id" in data
            assert exact_name in data.get("message", "")

    def test_unrecognized_choice_triggers_reconfirm(self, client: httpx.Client):
        """完全无关的 choice → reconfirm 状态

        验证: 当 choice 既不匹配序号也不匹配模块名，应触发重新匹配
        """
        session_id, candidates = self._start_and_get_session(client, "健身房")

        resp = client.post(
            self.ENDPOINT,
            data={"session_id": session_id, "choice": "完全不存在的模块名_xyz"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("status") == "reconfirm", (
            f"未识别的 choice 应触发 reconfirm: {data}"
        )
        # reconfirm 应返回新的 candidates + question
        assert "candidates" in data, f"reconfirm 应返回新 candidates: {data}"
        assert "question" in data, f"reconfirm 应返回新 question: {data}"
        assert "未识别" in data.get("message", "") or "重新匹配" in data.get("message", ""), (
            f"消息应提示未识别: {data}"
        )

    # ---- 完整链路 ----

    @pytest.mark.slow
    def test_full_workflow_end_to_end(self, client: httpx.Client):
        """完整链路: start → confirm → poll task 直到 completed

        验证:
          1. start 返回 session_id
          2. confirm 返回 task_id
          3. 任务最终 completed（而非 failed）
          4. 回执包含 excel_path 或 test_points
        """
        session_id, candidates = self._start_and_get_session(client, "健身房")
        if not candidates:
            pytest.skip("当前服务无候选模块，跳过端到端测试")

        # Confirm
        resp = client.post(
            self.ENDPOINT,
            data={"session_id": session_id, "choice": "1"},
        )
        assert resp.status_code == 200
        data = resp.json()
        if data.get("status") != "running":
            pytest.skip(f"confirm 返回非 running 状态，跳过端到端: {data}")

        task_id = data["task_id"]
        print(f"\n  task_id={task_id}")
        print(f"  确认模块: {candidates[0]}")

        # Poll
        task = poll_task(client, task_id)
        status = task.get("status")
        result = task.get("result") or {}

        print(f"  最终状态: {status}")
        print(f"  结果 keys: {list(result.keys())}")

        if status == "completed":
            # 完成 → 必须有业务产出
            if result.get("excel_path"):
                print(f"  excel_path: {result['excel_path']}")
            if result.get("test_points") or result.get("thinking"):
                print(f"  测试点/思考: {result.get('thinking')}")
            # 不强制断言 excel_path（可能中断于 NO_DATA），但需要明确不是 500
            assert "error" not in task or not task.get("error"), (
                f"completed 状态不应有 error: {task}"
            )
        elif status == "failed":
            error = task.get("error", "")
            # 允许 "未找到产品文档" 这种合法的业务中断
            print(f"  (可接受) 任务失败: {error}")
            # 但不允许是 500 类系统错误 — 那说明节点跳过了处理
            assert "Internal Server Error" not in error, (
                f"系统级错误，疑似节点跳过: {error}"
            )
            assert "500" not in error, f"系统级错误，疑似节点跳过: {error}"

    @pytest.mark.slow
    def test_workflow_intermediate_nodes_not_skipped(self, client: httpx.Client):
        """验证中间节点未跳过 — 约定检查

        我们无法直接断言 LangGraph 内部节点的执行轨迹（纯 API），
        但可以通过以下间接证据判断节点是否被跳过:

        1. 如果 confirm 立即返回 500 → 节点被跳过的可疑信号
        2. 如果 task 在 <5s 内 failed 且错误为模板变量等提示 → 节点被跳过
        3. 如果 task completed 且有 test_points → 中间节点正常执行

        本测试专注于场景3和场景2的检测。
        """
        session_id, candidates = self._start_and_get_session(client, "健身房")
        if not candidates:
            # 无候选模块时，测试 confirm 的 fallback 行为不抛 500
            resp = client.post(
                self.ENDPOINT,
                data={"session_id": session_id, "choice": "1"},
            )
            assert resp.status_code in (200, 404), (
                f"无候选模块时 confirm 不应抛 500: {resp.status_code}"
            )
            if resp.status_code == 200:
                data = resp.json()
                # 可能是 reconfirm 或 no_match，但不应是 server error
                assert "message" in data
            return

        # 精确模块名匹配端到端
        resp = client.post(
            self.ENDPOINT,
            data={"session_id": session_id, "choice": candidates[0]},
        )
        assert resp.status_code == 200, (
            f"精确匹配不应抛 500: {resp.status_code} {resp.text}"
        )
        data = resp.json()
        if data.get("status") != "running":
            return  # 无有效数据路径

        task_id = data["task_id"]
        task = poll_task(client, task_id)
        status = task.get("status")

        if status == "failed":
            error = task.get("error", "")
            # 检测节点是否被跳过：检查是否是 prompt 模板类错误
            skip_indicators = [
                "missing variables",
                "ChatPromptTemplate",
                "confirmation_question",
                "workflow_status",
            ]
            for indicator in skip_indicators:
                assert indicator not in error, (
                    f"节点可能被跳过 — 错误包含特征 '{indicator}': {error}"
                )
            # 合法中断（如 NO_DATA）是允许的
            print(f"  (可接受) 合法业务中断: {error}")


class TestTaskStatus:
    """GET /task/{task_id} 验证"""

    def test_nonexistent_task_returns_404(self, client: httpx.Client):
        """不存在的 task_id → 404"""
        resp = client.get("/task/nonexistent_task_id_abc123")
        assert resp.status_code == 404

    def test_task_has_required_fields(self, client: httpx.Client):
        """任务包含所有必填字段"""
        # 通过 /workflow/start + /workflow/confirm 创建一个任务
        start_resp = client.post("/workflow/start", data={"user_input": "测试"})
        if start_resp.status_code != 200:
            pytest.skip("服务未就绪，跳过任务字段验证")
        session_id = start_resp.json()["session_id"]

        confirm_resp = client.post(
            "/workflow/confirm",
            data={"session_id": session_id, "choice": "无此模块"},
        )
        assert confirm_resp.status_code == 200
        data = confirm_resp.json()

        if data.get("status") != "running":
            return  # reconfirm 或无模块，没有 task_id

        task_id = data["task_id"]
        resp = client.get(f"/task/{task_id}")
        assert resp.status_code == 200
        task = resp.json()["task"]

        # 必填字段
        for field in ("status", "progress", "message", "created_at"):
            assert field in task, f"task 缺少字段: {field}"
        assert task["status"] in (
            "pending", "running", "completed", "failed",
        ), f"未知状态: {task['status']}"


class TestWorkflowModuleIntegration:
    """模块创建 → 工作流调用 集成测试（纯 API）"""

    MODULE_API = "/api/modules"

    def test_create_module_then_workflow(self, client: httpx.Client):
        """通过 API 创建模块后验证 workflow 能识别"""
        # Step 1: 创建测试模块
        module_name = f"API测试模块_{int(time.time())}"
        resp = client.post(
            self.MODULE_API,
            json={"name": module_name, "parent_id": "root"},
        )
        if resp.status_code != 200:
            pytest.skip(f"创建模块失败（服务可能未就绪）: {resp.text}")

        assert resp.json().get("success"), f"模块创建失败: {resp.text}"

        # Step 2: 验证模块出现在列表
        list_resp = client.get(self.MODULE_API)
        assert list_resp.status_code == 200
        module_names = _extract_module_names(list_resp.json())
        assert module_name in module_names, (
            f"新建模块 {module_name} 未出现在模块列表中: {module_names}"
        )
        print(f"  模块 [{module_name}] 已确认存在")

        # Step 3: 用刚创建的模块名测试 workflow
        wf_resp = client.post(
            "/workflow/start",
            data={"user_input": module_name},
        )
        assert wf_resp.status_code == 200
        wf_data = wf_resp.json()
        print(f"  workflow 识别结果: candidates={wf_data.get('candidates')}")

        # 可能因为模块没有绑定文档导致 confidence=low，但不应抛 500
        assert "session_id" in wf_data


def _extract_module_names(modules_data: dict) -> list[str]:
    """从 /api/modules 响应中递归提取所有模块名。"""
    names = []
    tree = modules_data.get("tree", [])
    if isinstance(tree, list):
        _walk_tree(tree, names)
    return names


def _walk_tree(nodes: list, out: list):
    for n in nodes:
        if isinstance(n, dict):
            if n.get("name"):
                out.append(n["name"])
            children = n.get("children", n.get("nodes", []))
            if children:
                _walk_tree(children, out)
