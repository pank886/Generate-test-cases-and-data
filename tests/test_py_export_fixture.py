"""py_export fixture 调用模式回归测试（2026-08-13 P0 修复）。

覆盖：_generate_py_file 生成的 class fixture 必须用 base.run_blocks(path)，
禁止再用 base.specification_yaml(get_testcase_yaml(path))。

框架契约（base/apiutil.py）：
  - get_testcase_yaml() 返回 YAML 列表（setup/teardown 均多 block）
  - specification_yaml(case_info) 只接受单个 block dict → 传列表必抛
    TypeError: list indices must be integers, not str
  - run_blocks(yaml_path) 接受路径，逐 block 执行 + 汇总断言（正确姿势）

智慧用电_27 回归：_generate_py_file 把 fixture 从 run_blocks 改成
specification_yaml(get_testcase_yaml(...))，5 个真实 fixture 全崩，
102/112（91%）测试方法 class 级 ERROR。本测试锁定该回归。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent_components.nodes import ChatTestAgentGraph


class TestPyExportFixture:
    def _make_agent(self, monkeypatch, tmp_path):
        agent = object.__new__(ChatTestAgentGraph)
        excel_path = os.path.join(str(tmp_path), "test_plan.xlsx")
        rows = [
            {"feature": "智慧用电", "story": "电表管理", "title": "电表查询-正向",
             "preconditions": "PRE-001", "steps": "1.调用", "expected": "1.[eq]成功"},
            {"feature": "智慧用电", "story": "电表管理", "title": "电表查询-不存在-反向",
             "preconditions": "PRE-001", "steps": "1.调用", "expected": "1.[ne]失败"},
        ]
        monkeypatch.setattr(agent, "_read_excel_rows", lambda path: rows)
        monkeypatch.setattr(
            agent, "_translate_to_en",
            lambda path, rows: {
                "feature_en": {"智慧用电": "SmartElectricity"},
                "story_en": {"电表管理": "MeterManagement"},
                "title_en": {
                    "电表查询-正向": "test_meter_query_positive",
                    "电表查询-不存在-反向": "test_meter_query_negative",
                },
            })
        monkeypatch.setattr(agent, "_read_shared_preconditions", lambda path: [])
        monkeypatch.setattr(agent, "_log_node_output", lambda *a, **k: None)
        return agent, excel_path

    def test_fixture_uses_run_blocks(self, monkeypatch, tmp_path):
        """fixture 必须用 run_blocks(path)，禁止 specification_yaml(get_testcase_yaml(...))。"""
        agent, excel_path = self._make_agent(monkeypatch, tmp_path)
        result = agent._generate_py_file(excel_path)
        assert result["py_path"], "未生成 .py 文件"
        src = open(result["py_path"], encoding="utf-8").read()

        # setup + teardown 各 1 处，加 2 个测试函数 = 至少 4 处 run_blocks
        assert src.count("run_blocks(") >= 4
        # 禁止回归到 specification_yaml(get_testcase_yaml(...)) 模式
        assert "specification_yaml(get_testcase_yaml" not in src

    def test_fixture_references_setup_teardown_paths(self, monkeypatch, tmp_path):
        """fixture 引用的 setup/teardown 路径正确。"""
        agent, excel_path = self._make_agent(monkeypatch, tmp_path)
        result = agent._generate_py_file(excel_path)
        src = open(result["py_path"], encoding="utf-8").read()
        assert "setup_data/setup_meter_management.yaml" in src
        assert "setup_data/teardown_meter_management.yaml" in src


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
