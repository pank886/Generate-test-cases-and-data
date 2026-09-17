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

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent_components.graph.nodes import ChatTestAgentGraph


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


class TestLegacyModeUnchanged:
    """反向锁（2026-09-15）：非「逐前置」的 plan 必须走原分支，产物形状逐字不变。

    `_generate_py_file` 是 Phase C 的格式权威，共用它的还有 SmartParking 等产品
    （那列对它们是 class/danyuan，或压根没这列）。前置三级拆分只准在
    Sheet1 `fixture等级 = 逐前置` 时生效 —— 这条就是那个开关的守门测试。
    """

    @staticmethod
    def _src(monkeypatch, tmp_path):
        agent, excel_path = TestPyExportFixture()._make_agent(monkeypatch, tmp_path)
        return open(agent._generate_py_file(excel_path)["py_path"], encoding="utf-8").read()

    def test_class_level_fixture_shape_is_untouched(self, monkeypatch, tmp_path):
        src = self._src(monkeypatch, tmp_path)
        assert '@pytest.mark.usefixtures("setup_meter_management")' in src
        assert src.count("@pytest.fixture(scope=\"class\")") == 1
        # 逐前置模式的两样东西一个都不许出现
        assert "_fixture_with_display_name" not in src
        assert "__allure_display_name__" not in src
        assert "pre_rt" not in src

    def test_rows_without_the_column_keep_the_old_branch(self, monkeypatch, tmp_path):
        """老 plan 没有 `fixture_level` 列 → 读成 None → 走原分支（不是报错、不是新模式）。"""
        agent, excel_path = TestPyExportFixture()._make_agent(monkeypatch, tmp_path)
        src = open(agent._generate_py_file(excel_path)["py_path"], encoding="utf-8").read()
        assert 'setup_data/setup_meter_management.yaml' in src
        assert src.count("run_blocks(") == 4          # 2 个方法 + fixture 里 setup/teardown 各 1

    def test_per_pre_without_sheet2_rows_fails_loud(self, monkeypatch, tmp_path):
        """「逐前置」却拿不到 Sheet2 → 必须报错，不能猜一个文件名继续生成。"""
        agent, excel_path = TestPyExportFixture()._make_agent(monkeypatch, tmp_path)
        rows = agent._read_excel_rows(excel_path)
        for r in rows:
            r["fixture_level"] = "逐前置"
        monkeypatch.setattr(agent, "_read_excel_rows", lambda path: rows)
        with pytest.raises(ValueError, match="Sheet2"):
            agent._generate_py_file(excel_path)


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
