# -*- coding: utf-8 -*-
"""Phase C 定向重跑测试（2026-09-09）。

覆盖：
  1. yaml_gen allow_paths/ctx_overrides：
     - allow 子集 → 只 append 命中任务、计数=子集、未选中 func 目录不重建；
     - None → 全量行为不变（向后兼容）；
     - 只选 setup → 仅 Stage1 被调；
     - ctx_overrides 逐文件注入 row._ctx_override；
     - 真实 _run_yaml_rounds 的 ctx 优先级 = override > base。
  2. _selective_phase_c_rerun_bg 装配：
     - allow_paths / ctx_overrides 派生正确；excluded_broken 回填；session 清理；
     - 无落点 + junit → ingest 引导；无落点无 junit → failed；
     - 无失败/破损目标 → completed「无目标」且不调生成。
  3. 路由：/phase-c/selective-rerun 注册 + 引用 bg。

不依赖 LLM / 真实 DB / 向量库。fake agent + mock 断言（参照 test_three_stage_generation /
test_phase_c_standalone_rerun 风格）。
"""
import asyncio
import os
import sys

from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent_components.graph.nodes import ChatTestAgentGraph


def _write(path, data=b"x"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    mode = "wb" if isinstance(data, bytes) else "w"
    with open(path, mode, encoding=None if isinstance(data, bytes) else "utf-8") as f:
        f.write(data)
    return path


# ============================================================
# 1. yaml_gen allow_paths / ctx_overrides（fake agent）
# ============================================================

_EN = {"feature_en": {"模块A": "ModuleA"},
       "story_en": {"故事A": "StoryA"},
       "title_en": {"用例A": "case_a", "用例B": "case_b"}}

_FAKE_KEYS = {"PRE-001": {"keys": {"pre001Code": "$.json.code"},
                          "case_name": "PRE-001_前置A"}}


def _fake_agent(calls: list):
    agent = object.__new__(ChatTestAgentGraph)
    agent._read_excel_rows = lambda path: [
        {"feature": "模块A", "story": "故事A", "title": "用例A",
         "steps": "1.调用A", "expected": "", "preconditions": "PRE-001",
         "case_id": "TC-001"},
        {"feature": "模块A", "story": "故事A", "title": "用例B",
         "steps": "1.调用B", "expected": "", "preconditions": "",
         "case_id": "TC-002"},
    ]
    agent._translate_to_en = lambda path, rows: _EN
    agent._read_shared_preconditions = lambda path: [
        {"id": "PRE-001", "name": "前置一", "steps": "1.创建主数据"}]
    agent._log_node_output = lambda *a, **kw: None

    def fake_rounds(yaml_tasks, api_defs_json, user_ctx, output_base,
                    gen_func=None, repair_rounds=None, post_check_issues=None):
        calls.append(yaml_tasks)
        return {"total": len(yaml_tasks), "success": len(yaml_tasks),
                "failed": 0, "repaired": 0, "rounds": 1, "errors_file": None}

    agent._run_yaml_rounds = fake_rounds
    return agent


def _paths(tmp_path):
    """输出 base = dirname(excel)。给定 allow 相对 → 绝对 yaml 路径集。"""
    base = str(tmp_path)
    return base, {
        "setup": os.path.join(base, "ModuleA", "setup_data", "setup_story_a.yaml"),
        "teardown": os.path.join(base, "ModuleA", "setup_data", "teardown_story_a.yaml"),
        "testA": os.path.join(base, "ModuleA", "test_case_a", "test_data.yaml"),
        "testB": os.path.join(base, "ModuleA", "test_case_b", "test_data.yaml"),
    }


class TestYamlGenAllowPaths:
    def test_allow_subset_counts_only_subset(self, tmp_path):
        base, p = _paths(tmp_path)
        calls = []
        agent = _fake_agent(calls)
        with patch("agent_components.generators.yaml_gen._parse_setup_extract_keys",
                   return_value=_FAKE_KEYS):
            result = agent._generate_all_yamls(
                os.path.join(base, "test_plan.xlsx"), "[]", "STATIC",
                allow_paths={p["setup"], p["testA"]})
        # 2 阶段（setup + test），teardown 未选中 → Stage3 不跑
        assert len(calls) == 2, f"应 setup+test 两阶段，实际 {len(calls)}"
        assert [os.path.basename(x) for _, x in calls[0]] == ["setup_story_a.yaml"]
        assert len(calls[1]) == 1
        assert os.path.basename(os.path.dirname(calls[1][0][1])) == "test_case_a"
        # testB 目录未被创建（未选中 func 不重建）
        assert not os.path.exists(os.path.join(base, "ModuleA", "test_case_b"))
        assert result["total"] == 2 and result["success"] == 2

    def test_none_allow_full_behavior_unchanged(self, tmp_path):
        base, p = _paths(tmp_path)
        calls = []
        agent = _fake_agent(calls)
        with patch("agent_components.generators.yaml_gen._parse_setup_extract_keys",
                   return_value=_FAKE_KEYS):
            result = agent._generate_all_yamls(
                os.path.join(base, "test_plan.xlsx"), "[]", "STATIC")
        assert len(calls) == 3          # setup + test + teardown（全量）
        assert result["total"] == 4 and result["success"] == 4

    def test_only_setup_selected_runs_stage1_only(self, tmp_path):
        base, p = _paths(tmp_path)
        calls = []
        agent = _fake_agent(calls)
        with patch("agent_components.generators.yaml_gen._parse_setup_extract_keys",
                   return_value=_FAKE_KEYS):
            result = agent._generate_all_yamls(
                os.path.join(base, "test_plan.xlsx"), "[]", "STATIC",
                allow_paths={p["setup"]})
        assert len(calls) == 1
        assert all(os.path.basename(x).startswith("setup_") for _, x in calls[0])
        assert result["total"] == 1
        # test 目录不被创建
        assert not os.path.exists(os.path.join(base, "ModuleA", "test_case_a"))

    def test_ctx_overrides_injected_per_file(self, tmp_path):
        base, p = _paths(tmp_path)
        calls = []
        agent = _fake_agent(calls)
        with patch("agent_components.generators.yaml_gen._parse_setup_extract_keys",
                   return_value=_FAKE_KEYS):
            agent._generate_all_yamls(
                os.path.join(base, "test_plan.xlsx"), "[]", "STATIC",
                allow_paths={p["testA"], p["testB"]},
                ctx_overrides={p["testA"]: "DIGEST_A", p["testB"]: "DIGEST_B"})
        # setup 未选中；仅 test 阶段
        test_rows = {os.path.abspath(x): row for row, x in calls[0]}
        assert len(calls) == 1
        assert test_rows[p["testA"]]["_ctx_override"] == "DIGEST_A"
        assert test_rows[p["testB"]]["_ctx_override"] == "DIGEST_B"
        # 无 override 的文件不带该键
        assert "STATIC" not in test_rows[p["testA"]]["_ctx_override"]


class TestRunYamlRoundsCtxPrecedence:
    def test_row_override_wins_over_base(self, tmp_path):
        """真实 _run_yaml_rounds：submit 取 row._ctx_override or base user_ctx。"""
        got = []

        def recorder(row, api_defs_json, user_ctx, output_path, repair_ctx=None):
            got.append((row.get("case_id"), user_ctx))
            return ""

        agent = object.__new__(ChatTestAgentGraph)
        agent._log_node_output = lambda *a, **kw: None
        tasks = [
            ({"case_id": "A", "steps": "s", "_ctx_override": "OVR_A"}, "out_a.yaml"),
            ({"case_id": "B", "steps": "s"}, "out_b.yaml"),   # 无 override → base
        ]
        res = agent._run_yaml_rounds(tasks, "[]", "BASE_CTX",
                                     output_base=str(tmp_path), gen_func=recorder)
        by_case = dict(got)
        assert by_case["A"] == "OVR_A"
        assert by_case["B"] == "BASE_CTX"
        assert res["success"] == 2


# ============================================================
# 2. _selective_phase_c_rerun_bg 装配（mock run_results + fake components）
# ============================================================

class _FakeSelectiveComponents:
    def __init__(self):
        self.call = None
        self.sample = {"total": 0, "success": 0, "failed": 0,
                       "repaired": 0, "rounds": 0, "errors_file": None}

    def _generate_all_yamls(self, excel_path, api_defs_json, user_ctx,
                            allow_paths=None, ctx_overrides=None):
        self.call = (excel_path, api_defs_json, user_ctx,
                     set(allow_paths or []), ctx_overrides)
        return dict(self.sample)

    def _generate_py_file(self, *a, **k):
        raise AssertionError("定向重跑不应调用 _generate_py_file")

    def _generate_dependency_map(self, *a, **k):
        raise AssertionError("定向重跑不应调用 _generate_dependency_map")


def _make_module(base, name="智慧停车", ctx_text="STATIC_RULE_LINE\n规则正文"):
    d = os.path.join(base, name)
    _write(os.path.join(d, "test_plan.xlsx"))
    with open(os.path.join(d, "api_defs.json"), "w", encoding="utf-8") as f:
        f.write('[{"name": "A", "url": "/park-access-parking-web/a"}]')
    with open(os.path.join(d, "phase_c_user_context.md"), "w", encoding="utf-8") as f:
        f.write(ctx_text)
    return d


class _Env:
    """装配 monkeypatch 的 fake 环境。"""

    def __init__(self, monkeypatch):
        import web.state as state
        import agent_components.graph.nodes as nodes
        self.fake = _FakeSelectiveComponents()
        self.updates = []
        self.run_results_calls = []

        async def _fake_update(task_id, **kwargs):
            self.updates.append({"task_id": task_id, **kwargs})

        monkeypatch.setattr(state, "_phase_b_components", self.fake)
        monkeypatch.setattr(state, "_update_task", _fake_update)
        monkeypatch.setattr(nodes, "reload_llm", lambda: None)

    def patch_run(self, monkeypatch, *, records, select, digest, ingest=None):
        import agent_components.run_results as rr_mod

        def _wrap(name, val):
            monkeypatch.setattr(rr_mod, name, val)

        _wrap("read_last_run_records",
              lambda module_dir: dict(records))
        _wrap("select_rerun_targets",
              lambda module_dir, recs: select)
        _wrap("build_failure_digest",
              lambda module_dir, recs, targets: digest)
        _wrap("ingest_run_results",
              lambda module_dir, junit_path, log_path=None, run_ts=None:
                  ingest(module_dir, junit_path) if ingest else {})


async def _run_selective(module_dir, _env, monkeypatch, **kwargs):
    from web.tasks import _selective_phase_c_rerun_bg
    await _selective_phase_c_rerun_bg("tid-sel", module_dir, session_id="sess-sel", **kwargs)


def _completed(_env):
    return [u for u in _env.updates if u.get("status") == "completed"]


class TestSelectiveBg:
    def test_target_wiring_allow_and_ctx(self, tmp_path, monkeypatch):
        module = _make_module(str(tmp_path))
        abs_t = os.path.join(module, "ModuleA", "test_a", "test_data.yaml")
        abs_s = os.path.join(module, "ModuleA", "setup_data", "setup_story.yaml")
        env = _Env(monkeypatch)
        env.patch_run(
            monkeypatch,
            records={"r/a.yaml": {"verdict": "failed"},
                     "r/setup_data/setup_s.yaml": {"verdict": "broken"}},
            select={"test_yamls": [abs_t], "setup_yamls": [abs_s],
                    "excluded_broken": [{"rel": "r/b.yaml",
                                         "junit_msg": "failed on setup"}]},
            digest={abs_t: "DIGEST_T", abs_s: "DIGEST_S"},
        )
        asyncio.run(_run_selective(module, env, monkeypatch))

        # 生成调用 1 次，allow_paths / ctx_overrides 派生正确
        call = env.fake.call
        assert call is not None
        excel, api_defs, user_ctx, allow, ctx_ov = call
        assert allow == {abs_t, abs_s}
        assert ctx_ov[abs_t] == "STATIC_RULE_LINE\n规则正文" + "\n\n" + "DIGEST_T"
        assert ctx_ov[abs_s].endswith("DIGEST_S")

        done = _completed(env)
        assert done and done[-1]["status"] == "completed"
        result = done[-1]["result"]
        assert result["target_counts"] == {"test": 1, "setup": 1}
        assert result["excluded_broken"][0]["rel"] == "r/b.yaml"
        # session 清理
        import web.state as state
        assert "sess-sel" not in state._workflow_sessions

    def test_no_targets_completed_no_generate(self, tmp_path, monkeypatch):
        module = _make_module(str(tmp_path))
        env = _Env(monkeypatch)
        env.patch_run(
            monkeypatch,
            records={"r/a.yaml": {"verdict": "passed"}},
            select={"test_yamls": [], "setup_yamls": [], "excluded_broken": []},
            digest={},
        )
        asyncio.run(_run_selective(module, env, monkeypatch))
        assert env.fake.call is None, "无失败/破损目标不应调生成"
        done = _completed(env)
        assert "无失败/破损目标" in done[-1]["message"]

    def test_no_records_with_junit_ingests(self, tmp_path, monkeypatch):
        module = _make_module(str(tmp_path))
        abs_t = os.path.join(module, "ModuleA", "test_a", "test_data.yaml")
        env = _Env(monkeypatch)
        ingested = [False]

        def _ingest(md, junit):
            ingested.append(True)
            return {"r/a.yaml": {"verdict": "failed"}}

        env.patch_run(
            monkeypatch,
            records={},                       # 初始无落点
            select={"test_yamls": [abs_t], "setup_yamls": [], "excluded_broken": []},
            digest={abs_t: "D"},
            ingest=_ingest,
        )
        asyncio.run(_run_selective(module, env, monkeypatch,
                                   junit_path="/x/junit.xml"))
        assert any(ingested), "无落点 + junit → 应先 ingest 引导"
        assert env.fake.call is not None      # 引导后继续定向

    def test_no_records_no_junit_fails(self, tmp_path, monkeypatch):
        module = _make_module(str(tmp_path))
        env = _Env(monkeypatch)
        env.patch_run(monkeypatch, records={},
                      select={"test_yamls": [], "setup_yamls": [],
                              "excluded_broken": []},
                      digest={})
        asyncio.run(_run_selective(module, env, monkeypatch))
        failed = [u for u in env.updates if u.get("status") == "failed"]
        assert failed and ".last_run.json" in failed[-1]["error"]
        assert env.fake.call is None


# ============================================================
# 3. 路由 + tasks 静态断言
# ============================================================

class TestSelectiveRoutes:
    def test_selective_route_registered(self):
        from web.routes.chat import router
        paths = [r.path for r in router.routes]
        assert "/phase-c/selective-rerun" in paths

    def test_route_imports_and_forwards_bg(self):
        chat_path = os.path.join(os.path.dirname(__file__), "..", "web",
                                 "routes", "chat.py")
        with open(chat_path, "r", encoding="utf-8") as f:
            content = f.read()
        seg = content.split("async def phase_c_selective_rerun")[1]
        assert "_selective_phase_c_rerun_bg" in seg
        assert "background_tasks.add_task" in seg
        # 不改全量分支：本端点内不应触发 _phase_c_rerun_bg
        assert "_phase_c_rerun_bg," not in seg.split("_selective_phase_c_rerun_bg")[0]

    def test_tasks_bg_uses_run_results_and_no_py_depmap(self):
        tasks_path = os.path.join(os.path.dirname(__file__), "..", "web", "tasks.py")
        with open(tasks_path, "r", encoding="utf-8") as f:
            content = f.read()
        seg = content.split("async def _selective_phase_c_rerun_bg")[1]
        seg = seg.split("# ========================================================================")[0]
        assert "read_last_run_records" in seg
        assert "select_rerun_targets" in seg
        assert "build_failure_digest" in seg
        assert "ingest_run_results" in seg
        assert "allow_paths=allow_paths" in seg
        assert "ctx_overrides=ctx_overrides" in seg
        assert "_generate_py_file" not in seg
        assert "_generate_dependency_map" not in seg
        # session 清理兜底
        assert "_workflow_sessions.pop" in seg
