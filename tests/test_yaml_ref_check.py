"""yaml_ref_check 接入收尾测试（2026-08-13 P2）。

覆盖：_find_missing_yaml_refs 扫描 .py 引用的 yaml 与磁盘实际文件对比，
缺失文件被报告（禁止静默放行）。补生成不做（用户确认属设计规划）。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent_components.validation.yaml_validator import _find_missing_yaml_refs


class TestFindMissingYamlRefs:
    def test_missing_ref_reported(self, tmp_path):
        """.py 引用 2 个 yaml，其中 1 个磁盘缺失 → 返回缺失清单。"""
        project = str(tmp_path / "proj")
        batch = str(tmp_path / "out")
        os.makedirs(batch, exist_ok=True)
        os.makedirs(os.path.join(project, "testcase", "batch", "F", "test_a"),
                    exist_ok=True)
        open(os.path.join(project, "testcase", "batch", "F", "test_a",
                          "test_data.yaml"), "w").close()
        with open(os.path.join(batch, "test_F.py"), "w", encoding="utf-8") as f:
            f.write(
                "def a():\n"
                "    RequestsBase().run_blocks(\n"
                "        './testcase/batch/F/test_a/test_data.yaml')\n"
                "def b():\n"
                "    RequestsBase().run_blocks(\n"
                "        './testcase/batch/F/test_b/test_data.yaml')\n")
        missing = _find_missing_yaml_refs(batch, project)
        assert missing == ["testcase/batch/F/test_b/test_data.yaml"]

    def test_all_refs_exist_empty(self, tmp_path):
        """全部引用存在 → 空清单。"""
        project = str(tmp_path / "proj")
        batch = str(tmp_path / "out")
        os.makedirs(batch, exist_ok=True)
        os.makedirs(os.path.join(project, "testcase", "batch", "F", "test_a"),
                    exist_ok=True)
        open(os.path.join(project, "testcase", "batch", "F", "test_a",
                          "test_data.yaml"), "w").close()
        with open(os.path.join(batch, "test_F.py"), "w", encoding="utf-8") as f:
            f.write("RequestsBase().run_blocks("
                    "'./testcase/batch/F/test_a/test_data.yaml')\n")
        assert _find_missing_yaml_refs(batch, project) == []

    def test_no_py_files_empty(self, tmp_path):
        """无 .py 文件 → 空清单（不抛异常）。"""
        assert _find_missing_yaml_refs(str(tmp_path / "out"),
                                       str(tmp_path / "proj")) == []


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
