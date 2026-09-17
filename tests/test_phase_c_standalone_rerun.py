"""Phase C 独立重跑工作流回归测试（2026-09-09）。

背景：智慧停车执行日志失败根因 → 用户要求把分析提炼为模块目录约定文件
`phase_c_user_context.md`，作为 `{user_context}` 输入 Phase C 数据生成阶段，并让
Phase C 可作独立工作流单独启动（对话流新增「Phase C 项」）。

本文件验证：
  1. `_list_phase_c_module_dirs`：只列含 test_plan.xlsx + api_defs.json +
     phase_c_user_context.md 的模块目录，排除 .bak/_backup/_regenerated。
  2. `_read_phase_c_user_ctx`：读 context 文件正确；缺失返回 ""。
  3. `_phase_c_rerun_bg` 装配：api_defs_json == api_defs.json 原文、user_ctx == context
     内容、excel_path 正确、completed 字段齐全；**不调用** .py / dependency_map。
  4. 路由：/phase-c/start 存在 + /workflow/confirm 有 flow=="phase_c" 独立分支。

不依赖 LLM / 真实 DB / 向量库。纯文件 + mock 断言（参照 test_phase_c_api 风格）。
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest


# ============================================================
# 1. _list_phase_c_module_dirs 纯函数
# ============================================================

def _make_module_dir(base, name, *, has_plan=True, has_defs=True, has_ctx=True,
                     ctx_text="规则正文"):
    """在 base 下造一个模块目录文件集合，返回目录路径。"""
    d = os.path.join(base, name)
    os.makedirs(d, exist_ok=True)
    if has_plan:
        open(os.path.join(d, "test_plan.xlsx"), "wb").write(b"x")
    if has_defs:
        with open(os.path.join(d, "api_defs.json"), "w", encoding="utf-8") as f:
            f.write('[{"name": "A", "url": "/park-access-parking-web/a"}]')
    if has_ctx:
        with open(os.path.join(d, "phase_c_user_context.md"), "w", encoding="utf-8") as f:
            f.write(ctx_text)
    return d


class TestListPhaseCModuleDirs:
    def test_lists_only_fully_ready_module_dirs(self, tmp_path):
        from web.tasks import _list_phase_c_module_dirs
        base = str(tmp_path)
        a = _make_module_dir(base, "智慧停车")
        _make_module_dir(base, "缺context", has_ctx=False)          # 无 context → 不列
        _make_module_dir(base, "缺defs", has_defs=False)            # 无 api_defs → 不列
        _make_module_dir(base, "模块.bak", ctx_text="历史")          # .bak → 排除
        _make_module_dir(base, "模块_regenerated")                  # 重生成目录 → 排除
        _make_module_dir(base, "模块_备份_backup")

        dirs = _list_phase_c_module_dirs(base)
        assert [os.path.basename(d) for d in dirs] == ["智慧停车"], dirs

    def test_empty_base_returns_empty(self, tmp_path):
        from web.tasks import _list_phase_c_module_dirs
        assert _list_phase_c_module_dirs("") == []
        empty = os.path.join(str(tmp_path), "nope")
        assert _list_phase_c_module_dirs(empty) == []

    def test_finds_nested_domain_grouped_module(self, tmp_path):
        # TESTCASE_BASE 下模块按域分组嵌套：base/园区基线/智慧停车/...
        from web.tasks import _list_phase_c_module_dirs
        base = str(tmp_path)
        domain = _make_module_dir(os.path.join(base, "园区基线"), "智慧停车")
        # 模块子目录（YAML 输出等）不应被当作独立模块收录
        _make_module_dir(domain, "SmartParking", has_plan=False, has_defs=False, has_ctx=False)
        # 分组目录缺 context 的模块不收录
        _make_module_dir(os.path.join(base, "园区基线"), "智慧用电_16", has_ctx=False)

        dirs = _list_phase_c_module_dirs(base)
        assert dirs == [os.path.join(base, "园区基线", "智慧停车")], dirs

    def test_skips_backup_subtree_at_any_depth(self, tmp_path):
        from web.tasks import _list_phase_c_module_dirs
        base = str(tmp_path)
        # 深埋在分组下的备份克隆整树跳过
        _make_module_dir(os.path.join(base, "园区基线", "智慧用电_37_regenerated"), "子模块")
        good = _make_module_dir(os.path.join(base, "园区基线"), "智慧停车")
        dirs = _list_phase_c_module_dirs(base)
        assert dirs == [good], dirs


# ============================================================
# 2. _read_phase_c_user_ctx 纯函数
# ============================================================

class TestReadPhaseCUserCtx:
    def test_reads_file_content(self, tmp_path):
        from web.tasks import _read_phase_c_user_ctx
        d = _make_module_dir(str(tmp_path), "M", ctx_text="约束规则 ABC")
        assert _read_phase_c_user_ctx(d) == "约束规则 ABC"

    def test_missing_file_returns_empty(self, tmp_path):
        from web.tasks import _read_phase_c_user_ctx
        d = _make_module_dir(str(tmp_path), "M", has_ctx=False)
        assert _read_phase_c_user_ctx(d) == ""

    def test_empty_dir_returns_empty(self):
        from web.tasks import _read_phase_c_user_ctx
        assert _read_phase_c_user_ctx("") == ""
        assert _read_phase_c_user_ctx(os.path.join("Z:", "no_such_dir")) == ""


# ============================================================
# 3. _phase_c_rerun_bg 装配（YAML-only，不调 .py / dep_map）
# ============================================================

class _FakeComponents:
    """替身：_generate_all_yamls 记录入参返回样例；.py / dep_map 一调用即失败守卫。"""

    def __init__(self):
        self.calls = []
        self.sample = {
            "total": 3, "success": 3, "failed": 0,
            "repaired": 0, "rounds": 1, "errors_file": None,
        }

    def _generate_all_yamls(self, excel_path, api_defs_json, user_ctx):
        self.calls.append((excel_path, api_defs_json, user_ctx))
        return dict(self.sample)

    def _generate_py_file(self, *a, **k):
        raise AssertionError("Phase C 独立重跑不应调用 _generate_py_file")

    def _generate_dependency_map(self, *a, **k):
        raise AssertionError("Phase C 独立重跑不应调用 _generate_dependency_map")


def _run_rerun_bg(module_dir, monkeypatch):
    """装配环境并同步跑 _phase_c_rerun_bg，返回 (fake, updates)。"""
    import web.state as state
    import agent_components.graph.nodes as nodes

    fake = _FakeComponents()
    updates = []

    async def _fake_update(task_id, **kwargs):
        updates.append({"task_id": task_id, **kwargs})

    monkeypatch.setattr(state, "_phase_b_components", fake)
    monkeypatch.setattr(state, "_update_task", _fake_update)
    monkeypatch.setattr(nodes, "reload_llm", lambda: None)

    asyncio.run(_rerun(task_id="tid123", module_dir=module_dir))
    return fake, updates


async def _rerun(task_id, module_dir):
    from web.tasks import _phase_c_rerun_bg
    await _phase_c_rerun_bg(task_id, module_dir, session_id="sess1")


class TestPhaseCRerunBg:
    def test_yaml_only_wiring(self, tmp_path, monkeypatch):
        import web.state as state
        from web.tasks import _read_phase_c_user_ctx

        base = str(tmp_path)
        module_dir = _make_module_dir(
            base, "智慧停车",
            ctx_text="## 车场绑定规则\n必须先真实创建收费规则再绑定",
        )
        excel_path = os.path.join(module_dir, "test_plan.xlsx")
        # 先跑 env（fake 已记录调用、updates 已捕获）
        fake, updates = _run_rerun_bg(module_dir, monkeypatch)

        # 1) 只调用一次 _generate_all_yamls；.py / dep_map 守卫未触发
        assert len(fake.calls) == 1
        call_excel, call_api_defs, call_ctx = fake.calls[0]
        assert call_excel == excel_path
        # 2) api_defs_json == api_defs.json 原文（JSON 字符串）
        with open(os.path.join(module_dir, "api_defs.json"), "r", encoding="utf-8") as f:
            assert call_api_defs == f.read()
        # 3) user_ctx == context 文件内容
        assert call_ctx == _read_phase_c_user_ctx(module_dir)
        assert "车场绑定规则" in call_ctx

        # 4) 最终 completed + result 字段齐全
        done = [u for u in updates if u.get("status") == "completed"]
        assert done, updates
        result = done[-1]["result"]
        assert result["yaml_success"] == 3
        assert result["yaml_total"] == 3
        assert result["excel_path"] == excel_path
        assert result["output_dir"] == module_dir
        assert result["reply"]
        assert updates[-1]["progress"] == 100

    def test_missing_api_defs_fails(self, tmp_path, monkeypatch):
        import web.state as state
        module_dir = _make_module_dir(str(tmp_path), "缺defs", has_defs=False)
        # context 无意义下也应在缺 defs 时 fail 而非继续
        fake = _FakeComponents()
        updates = []

        async def _fake_update(task_id, **kwargs):
            updates.append({"task_id": task_id, **kwargs})

        monkeypatch.setattr(state, "_phase_b_components", fake)
        monkeypatch.setattr(state, "_update_task", _fake_update)
        import agent_components.graph.nodes as nodes
        monkeypatch.setattr(nodes, "reload_llm", lambda: None)

        async def _go():
            from web.tasks import _phase_c_rerun_bg
            await _phase_c_rerun_bg("t1", module_dir)

        asyncio.run(_go())
        failed = [u for u in updates if u.get("status") == "failed"]
        assert failed and "api_defs.json" in failed[-1]["error"]
        assert fake.calls == [], "缺 api_defs 不应进入生成"

    def test_missing_excel_fails(self, tmp_path, monkeypatch):
        import web.state as state
        module_dir = _make_module_dir(str(tmp_path), "无plan", has_plan=False)
        fake = _FakeComponents()
        updates = []

        async def _fake_update(task_id, **kwargs):
            updates.append({"task_id": task_id, **kwargs})

        monkeypatch.setattr(state, "_phase_b_components", fake)
        monkeypatch.setattr(state, "_update_task", _fake_update)
        import agent_components.graph.nodes as nodes
        monkeypatch.setattr(nodes, "reload_llm", lambda: None)

        async def _go():
            from web.tasks import _phase_c_rerun_bg
            await _phase_c_rerun_bg("t2", module_dir)

        asyncio.run(_go())
        failed = [u for u in updates if u.get("status") == "failed"]
        assert failed and "test_plan.xlsx" in failed[-1]["error"]


# ============================================================
# 4. 路由分支（静态 + 结构断言）
# ============================================================

class TestPhaseCRoutes:
    def test_phase_c_start_route_registered(self):
        from web.routes.chat import router
        paths = [r.path for r in router.routes]
        assert "/phase-c/start" in paths
        assert "/workflow/confirm" in paths

    def test_confirm_has_phase_c_branch(self):
        # /workflow/confirm 里必须有 flow=="phase_c" 独立分支，且转发 _phase_c_rerun_bg
        chat_path = os.path.join(os.path.dirname(__file__), "..", "web", "routes", "chat.py")
        with open(chat_path, "r", encoding="utf-8") as f:
            content = f.read()
        assert '"phase_c"' in content or "'phase_c'" in content
        assert "flow" in content
        assert "_phase_c_rerun_bg" in content
        assert "candidate_paths" in content

    def test_phase_c_start_does_not_require_vector_files(self):
        # Phase C 重跑只读模块目录，不应校验向量库/已导入文件
        chat_path = os.path.join(os.path.dirname(__file__), "..", "web", "routes", "chat.py")
        with open(chat_path, "r", encoding="utf-8") as f:
            content = f.read()
        # /phase-c/start 段与 /workflow/start 段之间：phase_c_start 内不应出现 vector_ready 报错
        seg = content.split("async def phase_c_start")[1].split("async def workflow_start")[0]
        assert "_vector_ready" not in seg, "Phase C 入口不应要求向量库就绪"
        assert "请先上传文档" not in seg

    def test_bg_has_session_cleanup(self):
        tasks_path = os.path.join(os.path.dirname(__file__), "..", "web", "tasks.py")
        with open(tasks_path, "r", encoding="utf-8") as f:
            content = f.read()
        seg = content.split("async def _phase_c_rerun_bg")[1].split("async def _resume_workflow_bg")[0]
        assert "_workflow_sessions.pop" in seg, "Phase C bg 完成后应清理会话"
        # 生成调用只有 _generate_all_yamls；不得 submit .py / dependency_map
        assert "_generate_all_yamls," in seg
        assert "to_thread(\n" not in seg or "_generate_all_yamls,\n" in seg
        assert "_generate_py_file,\n" not in seg
        assert "_generate_dependency_map,\n" not in seg
