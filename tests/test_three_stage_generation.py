"""三阶段生成 + key 状态传递单元测试（2026-08-27 设计 §10.5 用例 1-4）。

覆盖：
  1. 三阶段顺序：setup 全部完成 → test 全部完成 → teardown 全部完成（严格 barrier）
  2. key 解析：case_name 前缀关联 PRE → input_extract 键映射
  3. D3 过滤注入：TC 只注入其引用 PRE 的键
  4. D4 兜底：setup 缺 code → __MISSING_KEY__ 占位 + 下游提示 + 后校验 P1 标记

不依赖 LLM，纯逻辑测试。
"""
import json
import os
import sys

from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from agent_components.generators.yaml_gen import (
    _parse_setup_extract_keys,
    _inject_setup_keys_note,
    _scan_missing_key_refs,
    _filter_teardown_missing_pres,
    _relax_teardown_validation,
)
from agent_components.graph.nodes import ChatTestAgentGraph

# 与盘上 setup 产物同构的构造 YAML（顶层 list + case_name 前缀 + input_extract）
_SETUP_YAML = """\
- baseInfo:
    api_name: 添加电表
    method: post
    url: /park-energy-electric-web/electricMeter/add
  testCase:
  - case_name: PRE-001_创建测试电表B
    input_extract:
      ELEC_001: $.json.code
    json: {code: '${random_code(\"METER\")}'}
    validation:
    - eq: {$.retCode: 1}
- baseInfo:
    api_name: 添加电表
    method: post
    url: /park-energy-electric-web/electricMeter/add
  testCase:
  - case_name: PRE-001_isolated_TC-007_创建测试电表B
    input_extract:
      meterCode: $.json.code
      meterName: $.json.name
    json: {code: '${random_code(\"METER\")}'}
    validation:
    - eq: {$.retCode: 1}
- baseInfo:
    api_name: 添加电表
    method: post
    url: /park-energy-electric-web/electricMeter/add
  testCase:
  - case_name: PRE-003_创建绑定计费方案的收费电表
    input_extract:
      ELEC_BIND: $.json.code
    json: {code: '${random_code(\"BINDCODE\", 8)}'}
    validation:
    - eq: {$.retCode: 1}
"""


# ============================================================
# 1. 三阶段顺序（§10.5 用例 1）
# ============================================================

class TestThreeStageOrder:

    @staticmethod
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
        agent._translate_to_en = lambda path, rows: {
            "feature_en": {}, "story_en": {}, "title_en": {}}
        agent._read_shared_preconditions = lambda path: [
            {"id": "PRE-001", "name": "前置一", "steps": "1.创建电表"}]
        agent._log_node_output = lambda *a, **kw: None

        def fake_rounds(yaml_tasks, api_defs_json, user_ctx, output_base,
                        gen_func=None, repair_rounds=None, post_check_issues=None):
            calls.append(yaml_tasks)
            return {"total": len(yaml_tasks), "success": len(yaml_tasks),
                    "failed": 0, "repaired": 0, "rounds": 1, "errors_file": None}

        agent._run_yaml_rounds = fake_rounds
        return agent

    def test_setup_then_test_then_teardown(self, tmp_path):
        """三阶段严格顺序 + 结果合并（counts 求和 / rounds 取 max）+ D3 注入时机。"""
        calls = []
        agent = self._fake_agent(calls)
        excel_path = os.path.join(str(tmp_path), "test_plan.xlsx")
        # 假 agent 无 setup 产物 → 需 mock parser 返回真实键，否则 PRE-001 被判缺失、
        # teardown 被 task#10 过滤跳过（新行为），三阶段顺序用例失去意义
        fake_keys = {"PRE-001": {"keys": {"pre001ElectricMeterCode": "$.json.code"},
                                 "case_name": "PRE-001_创建测试电表B"}}
        with patch("agent_components.generators.yaml_gen._parse_setup_extract_keys",
                   return_value=fake_keys) as _m:
            result = agent._generate_all_yamls(excel_path, "[]", "ctx")
        _m.assert_called_once()

        assert len(calls) == 3, f"应恰好 3 次 _run_yaml_rounds，实际 {len(calls)}"
        # Stage 1 = setup（仅 setup_*.yaml）
        assert all(os.path.basename(p).startswith("setup_") for _r, p in calls[0])
        # Stage 2 = test（test_data.yaml）
        assert all(os.path.basename(p) == "test_data.yaml" for _r, p in calls[1])
        # Stage 3 = teardown（teardown_*.yaml）
        assert all(os.path.basename(p).startswith("teardown_") for _r, p in calls[2])

        # setup/teardown row 带 _pre_ids 元数据
        setup_row = calls[0][0][0]
        assert setup_row["_pre_ids"] == ["PRE-001"]
        teardown_row = calls[2][0][0]
        assert teardown_row["_pre_ids"] == ["PRE-001"]
        # teardown B 内容只含「清理」，不含创建步骤逆向拼接
        assert "逆向操作" not in teardown_row["steps"]
        assert "清理 PRE-001" in teardown_row["steps"]

        # 三阶段结果合并：total/success 求和，rounds 取 max
        assert result["total"] == 4  # 1 setup + 2 test + 1 teardown
        assert result["success"] == 4
        assert result["failed"] == 0
        assert result["rounds"] == 1

    def test_setup_task_carries_capture_rule(self, tmp_path):
        """v8 根因修复：setup 任务 steps 前置捕获规则，test 任务不带（防污染普通用例）。"""
        calls = []
        agent = self._fake_agent(calls)
        fake_keys = {"PRE-001": {"keys": {"pre001ElectricMeterCode": "$.json.code"},
                                 "case_name": "PRE-001_创建测试电表B"}}
        with patch("agent_components.generators.yaml_gen._parse_setup_extract_keys",
                   return_value=fake_keys):
            agent._generate_all_yamls(os.path.join(str(tmp_path), "test_plan.xlsx"), "[]", "ctx")

        setup_row = calls[0][0][0]
        assert "input_extract" in setup_row["steps"], "setup 任务必须携带捕获规则"
        assert "共享前置" in setup_row["steps"]
        # 普通 test 任务不得携带（规则只针对 setup）
        for row, _ in calls[1]:
            assert "共享前置 setup 块" not in row["steps"]

    def test_d3_note_injected_before_test_stage(self, tmp_path):
        """D3：test 阶段收到的 row 已注入 _setup_keys_note（按 preconditions 过滤）。"""
        calls = []
        agent = self._fake_agent(calls)
        agent._generate_all_yamls(os.path.join(str(tmp_path), "test_plan.xlsx"), "[]", "ctx")

        test_rows = [row for row, _ in calls[1]]
        by_case = {r["case_id"]: r for r in test_rows}
        assert "_setup_keys_note" in by_case["TC-001"]  # 引用 PRE-001 → 注入
        assert "_setup_keys_note" not in by_case["TC-002"]  # 无 PRE 引用 → 不注入


# ============================================================
# 2. key 解析（§10.5 用例 2）
# ============================================================

class TestParseSetupKeys:

    def test_associates_blocks_to_pre_and_isolated_separate(self, tmp_path):
        """key 解析：base 块归到 PRE-001，isolated 块独立成 PRE-001_isolated_TC-007。"""
        setup_dir = tmp_path / "SmartPower" / "setup_data"
        setup_dir.mkdir(parents=True)
        (setup_dir / "setup_meter_management.yaml").write_text(_SETUP_YAML, encoding="utf-8")

        result = _parse_setup_extract_keys(
            str(tmp_path), ["PRE-001", "PRE-001_isolated_TC-007", "PRE-003"])
        # PRE-001 base 只归自己（isolated 变体独立成条目，供 TC-007 单独引用）
        assert result["PRE-001"]["keys"] == {"ELEC_001": "$.json.code"}
        assert result["PRE-001_isolated_TC-007"]["keys"] == {
            "meterCode": "$.json.code",
            "meterName": "$.json.name",
        }
        assert result["PRE-003"]["keys"] == {"ELEC_BIND": "$.json.code"}
        assert result["PRE-003"]["case_name"].startswith("PRE-003_")

        # 落盘产物
        j = json.loads((tmp_path / "_setup_extract_keys.json").read_text(encoding="utf-8"))
        assert j["PRE-003"]["keys"]["ELEC_BIND"] == "$.json.code"

    def test_generated_format_case_names(self, tmp_path):
        """生成式命名（test_PRE001_...，无连字符）：归一化为 PRE-xxx 标签，无 D4 误判。"""
        gen_yaml = """\
- baseInfo:
    api_name: electricMeter_add
    method: post
    url: /park-energy-electric-web/electricMeter/add
  testCase:
  - case_name: test_PRE001_add_meter_001
    input_extract: {pre001MeterCode: '$.json.code'}
  - case_name: test_PRE001_isolated_TC007_add_meter_001
    input_extract: {tc007MeterCode: '$.json.code'}
  - case_name: test_PRE003_add_bind_meter_001
    input_extract: {bindMeterCode: '$.json.code'}
  - case_name: test_PRE004_add_parent_meter_001
    input_extract: {parentMeterCode: '$.json.code'}
"""
        setup_dir = tmp_path / "SmartPower" / "setup_data"
        setup_dir.mkdir(parents=True)
        (setup_dir / "setup_meter_management.yaml").write_text(gen_yaml, encoding="utf-8")

        result = _parse_setup_extract_keys(
            str(tmp_path), ["PRE-001", "PRE-001_isolated_TC-007", "PRE-003", "PRE-004"])
        assert result["PRE-001"]["keys"] == {"pre001MeterCode": "$.json.code"}
        assert result["PRE-001_isolated_TC-007"]["keys"] == {"tc007MeterCode": "$.json.code"}
        assert result["PRE-003"]["keys"] == {"bindMeterCode": "$.json.code"}
        assert result["PRE-004"]["keys"] == {"parentMeterCode": "$.json.code"}
        assert len(result) == 4  # 4 组全部关联到块，无 D4 占位注入
        assert "__MISSING_KEY__" not in str(result)


# ============================================================
# 3. D3 过滤注入（§10.5 用例 3）
# ============================================================

class TestInjectSetupKeysNote:

    @staticmethod
    def _keys():
        return {
            "PRE-001": {"keys": {"ELEC_001": "$.json.code"},
                        "case_name": "PRE-001_创建测试电表B"},
            "PRE-003": {"keys": {"ELEC_BIND": "$.json.code"},
                        "case_name": "PRE-003_创建绑定计费方案的收费电表"},
        }

    def test_filters_by_pre(self):
        tasks = [
            ({"steps": "s", "case_id": "TC-017", "preconditions": "PRE-003"},
             "test_017/test_data.yaml"),
            ({"steps": "s", "case_id": "TC-018", "preconditions": "PRE-001,PRE-003"},
             "test_018/test_data.yaml"),
            ({"steps": "s", "case_id": "TC-019", "preconditions": ""},
             "test_019/test_data.yaml"),
        ]
        _inject_setup_keys_note(tasks, self._keys(), key_field="preconditions")

        n017 = tasks[0][0]["_setup_keys_note"]
        assert "ELEC_BIND" in n017 and "ELEC_001" not in n017
        n018 = tasks[1][0]["_setup_keys_note"]
        assert "ELEC_BIND" in n018 and "ELEC_001" in n018
        assert "_setup_keys_note" not in tasks[2][0]

    def test_teardown_uses_pre_ids(self):
        tasks = [({"steps": "# 清理 PRE-003: x", "case_id": "teardown_x",
                   "_pre_ids": ["PRE-003"]}, "teardown_x.yaml")]
        _inject_setup_keys_note(tasks, self._keys(), key_field="_pre_ids")
        note = tasks[0][0]["_setup_keys_note"]
        assert "ELEC_BIND" in note and "ELEC_001" not in note


# ============================================================
# 4. D4 兜底（§10.5 用例 4）
# ============================================================

class TestD4Fallback:

    def test_missing_pre_gets_placeholder(self, tmp_path):
        """setup 缺失 → __MISSING_KEY__ 占位落盘。"""
        result = _parse_setup_extract_keys(str(tmp_path), ["PRE-003"])
        assert result["PRE-003"]["keys"] == {"pre003_code": "__MISSING_KEY__"}
        j = json.loads((tmp_path / "_setup_extract_keys.json").read_text(encoding="utf-8"))
        assert j["PRE-003"]["keys"]["pre003_code"] == "__MISSING_KEY__"

    def test_downstream_note_mentions_missing_marker(self):
        """下游注入：注解含 __MISSING_KEY__ 提示。"""
        keys = {"PRE-003": {"keys": {"pre003_code": "__MISSING_KEY__"},
                            "case_name": "(PRE-003 setup 生成缺失)"}}
        tasks = [({"steps": "# 清理 PRE-003: x", "case_id": "teardown_x",
                   "_pre_ids": ["PRE-003"]}, "teardown_x.yaml")]
        _inject_setup_keys_note(tasks, keys, key_field="_pre_ids")
        assert "__MISSING_KEY__" in tasks[0][0]["_setup_keys_note"]

    def test_post_scan_marks_p1(self, tmp_path):
        """后校验：test YAML 引用缺失键 → P1（missing_extract_key）。"""
        keys = {"PRE-003": {"keys": {"pre003_code": "__MISSING_KEY__"},
                            "case_name": "(PRE-003 setup 生成缺失)"}}
        yaml_path = tmp_path / "SmartPower" / "test_del" / "test_data.yaml"
        yaml_path.parent.mkdir(parents=True)
        yaml_path.write_text(
            "- baseInfo:\n"
            "    method: post\n"
            "    url: /park-energy-electric-web/electricMeter/delete\n"
            "  testCase:\n"
            "  - case_name: test_delete_bound\n"
            "    json:\n"
            "      code: ${get_extract_data('pre003_code')}\n"
            "    validation:\n"
            "    - eq: {$.retCode: 1}\n",
            encoding="utf-8")
        issues = _scan_missing_key_refs(str(tmp_path), keys)
        assert any(i["check"] == "missing_extract_key" and i["severity"] == "P1"
                   and i["case_name"] == "test_delete_bound" for i in issues)

    def test_post_scan_ignores_valid_keys(self, tmp_path):
        """后校验：引用已提取键（非缺失）不标 P1。"""
        keys = {"PRE-003": {"keys": {"ELEC_BIND": "$.json.code"},
                            "case_name": "PRE-003_xxx"}}
        yaml_path = tmp_path / "SmartPower" / "test_del" / "test_data.yaml"
        yaml_path.parent.mkdir(parents=True)
        yaml_path.write_text(
            "- baseInfo:\n"
            "    method: post\n"
            "    url: /park-energy-electric-web/electricMeter/delete\n"
            "  testCase:\n"
            "  - case_name: test_delete_ok\n"
            "    json:\n"
            "      code: ${get_extract_data('ELEC_BIND')}\n",
            encoding="utf-8")
        issues = _scan_missing_key_refs(str(tmp_path), keys)
        assert not [i for i in issues if i["check"] == "missing_extract_key"]


# ============================================================
# 5. teardown 健壮性（2026-08-27 用户决策 task #9/#10）
# ============================================================

class TestTeardownRobustness:

    def test_filter_teardown_missing_pres_skips_block(self):
        """teardown 对缺失键 PRE 剔除 steps 行与 _pre_ids（task #10）。"""
        keys = {
            "PRE-001": {"keys": {"pre001ElectricMeterCode": "$.json.code"},
                        "case_name": "PRE-001_xxx"},
            "PRE-002": {"keys": {"pre002_code": "__MISSING_KEY__"},
                        "case_name": "(PRE-002 setup 生成缺失)"},
        }
        tasks = [({"steps": "# 清理 PRE-001: 已存在测试电表B\n# 清理 PRE-002: 已存在分时电表",
                   "case_id": "teardown_x", "_pre_ids": ["PRE-001", "PRE-002"]}, "teardown_x.yaml")]
        _filter_teardown_missing_pres(tasks, keys)

        assert len(tasks) == 1  # 任务保留（PRE-001 仍有清理）
        row = tasks[0][0]
        assert "# 清理 PRE-002:" not in row["steps"]
        assert "# 清理 PRE-001:" in row["steps"]
        assert row["_pre_ids"] == ["PRE-001"]

    def test_filter_teardown_all_missing_drops_task(self):
        """全部 PRE 缺失提取键 → 整任务移除（无清理可做）。"""
        keys = {"PRE-002": {"keys": {"pre002_code": "__MISSING_KEY__"},
                            "case_name": "(PRE-002 setup 生成缺失)"}}
        tasks = [({"steps": "# 清理 PRE-002: 已存在分时电表",
                   "case_id": "teardown_y", "_pre_ids": ["PRE-002"]}, "teardown_y.yaml")]
        _filter_teardown_missing_pres(tasks, keys)
        assert tasks == []

    def test_filter_teardown_no_missing_noop(self):
        """无缺失键 → 不动任务。"""
        keys = {"PRE-001": {"keys": {"pre001ElectricMeterCode": "$.json.code"},
                            "case_name": "PRE-001_xxx"}}
        tasks = [({"steps": "# 清理 PRE-001: x", "case_id": "t", "_pre_ids": ["PRE-001"]}, "t.yaml")]
        _filter_teardown_missing_pres(tasks, keys)
        assert tasks[0][0]["_pre_ids"] == ["PRE-001"]
        assert tasks[0][0]["steps"] == "# 清理 PRE-001: x"

    def test_relax_teardown_validation_strips_assertions(self, tmp_path):
        """teardown 删除块剥断言（task #9）。"""
        td = tmp_path / "SmartPower" / "setup_data"
        td.mkdir(parents=True)
        f = td / "teardown_meter.yaml"
        f.write_text(
            "- baseInfo:\n"
            "    method: post\n"
            "    url: /park-energy-electric-web/electricMeter/delete\n"
            "  testCase:\n"
            "  - case_name: test_Cleanup\n"
            "    json: ['${get_extract_data(\"code\")}']\n"
            "    validation:\n"
            "    - eq: {$.retCode: 1}\n"
            "    - eq: {$.msg: success}\n",
            encoding="utf-8")
        _relax_teardown_validation(str(tmp_path))
        out = (td / "teardown_meter.yaml").read_text(encoding="utf-8")
        assert "retCode" not in out
        # 框架 assert_result 要求 validation 必须是 list（缺键 → 默认字符串 → 报错）。
        # 剥断言 = 置空列表 []（零断言），而非删键。
        assert "validation: []" in out

    def test_relax_teardown_validation_fills_missing_key(self, tmp_path):
        """缺 validation 键的块也补 []（防框架读默认字符串报错，v9 框架实测）。"""
        td = tmp_path / "SmartPower" / "setup_data"
        td.mkdir(parents=True)
        f = td / "teardown_meter.yaml"
        f.write_text(
            "- baseInfo:\n"
            "    method: post\n"
            "    url: /park-energy-electric-web/electricMeter/delete\n"
            "  testCase:\n"
            "  - case_name: test_Cleanup\n"
            "    json: ['${get_extract_data(\"code\")}']\n",
            encoding="utf-8")
        _relax_teardown_validation(str(tmp_path))
        assert "validation: []" in f.read_text(encoding="utf-8")

    def test_relax_teardown_validation_leaves_other_files(self, tmp_path):
        """非 teardown 文件不被剥断言。"""
        tdir = tmp_path / "SmartPower" / "test_del"
        tdir.mkdir(parents=True)
        f = tdir / "test_data.yaml"
        f.write_text(
            "- baseInfo:\n"
            "    method: post\n"
            "    url: /park-energy-electric-web/electricMeter/delete\n"
            "  testCase:\n"
            "  - case_name: test_del\n"
            "    validation:\n"
            "    - eq: {$.retCode: 1}\n",
            encoding="utf-8")
        _relax_teardown_validation(str(tmp_path))
        assert "retCode" in f.read_text(encoding="utf-8")

class TestNormalizeBaseUrls:
    """URL 前缀代码层接管（2026-09-01 task#15）：LLM 丢业务前缀 → 后缀匹配补全。

    v9 实测 getParentList 丢 /park-energy-electric-web/ 前缀 → 请求打到前端返回 HTML。
    """

    class _Step:
        def __init__(self, url: str):
            self.baseInfo = {"url": url}

    class _Result:
        def __init__(self, urls: list):
            self.data = [TestNormalizeBaseUrls._Step(u) for u in urls]

    def _call(self, urls, api_defs):
        from agent_components.generators.yaml_gen import YamlMixin
        r = self._Result(urls)
        YamlMixin._normalize_base_urls(object(), r, json.dumps(api_defs))
        return [s.baseInfo["url"] for s in r.data]

    def test_restores_dropped_business_prefix(self):
        """v9 实证：LLM 丢 /park-energy-electric-web/ 前缀 → 后缀匹配补回。"""
        out = self._call(
            ["/electricMeter/getParentList"],
            [{"url": "/park-energy-electric-web/electricMeter/getParentList"},
             {"url": "/park-energy-electric-web/electricMeter/add"}],
        )
        assert out == ["/park-energy-electric-web/electricMeter/getParentList"]

    def test_idempotent_when_already_full_url(self):
        """产物 url 已是 DB 完整 url → 不改（幂等）。"""
        out = self._call(
            ["/park-energy-electric-web/electricMeter/getList"],
            [{"url": "/park-energy-electric-web/electricMeter/getList"}],
        )
        assert out == ["/park-energy-electric-web/electricMeter/getList"]

    def test_ambiguous_short_suffix_skips(self):
        """超短路径后缀命中多个不同接口 → 歧义跳过，宁缺毋滥。"""
        out = self._call(
            ["/getList"],
            [{"url": "/park-energy-electric-web/electricMeter/getList"},
             {"url": "/collectionNotice/getList"},
             {"url": "/collectionLetter/getList"}],
        )
        assert out == ["/getList"]

    def test_no_suffix_match_keeps_original(self):
        """产物路径本就是 DB 里无前缀的接口（如 getPage）→ 不动。"""
        out = self._call(
            ["/electricMeter/getPage"],
            [{"url": "/electricMeter/getPage"},
             {"url": "/park-energy-electric-web/electricMeter/getList"}],
        )
        assert out == ["/electricMeter/getPage"]

    def test_empty_api_defs_noop(self):
        """api_defs 为空/为 [] → 不处理（兼容无接口定义调用方）。"""
        assert self._call(["/electricMeter/getParentList"], []) == \
            ["/electricMeter/getParentList"]
        assert self._call(["/electricMeter/getParentList"], "[]") == \
            ["/electricMeter/getParentList"]
