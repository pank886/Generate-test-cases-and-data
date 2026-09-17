# -*- coding: utf-8 -*-
"""run_results 纯函数单测（2026-09-09）。

覆盖：parse_junit（前缀分类/跨节点聚合/param 去重）；parse_run_log_blocks（切块/失败/soft 标记）；
read_yaml_case_names / extract_file_failures（只归因本文件、无匹配不抛）；build_module_map
（AST 解析 runner .py / 无 .py 目录兜底）；ingest_run_results（写覆盖 + passed 清场 +
broken-by-setup 不写 + run_ts 优先级）；read_last_run_records（只留 max cohort）；
select_rerun_targets；build_failure_digest（事实无示例）。

不依赖 LLM / DB。tmp 目录纯文件。
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from agent_components import run_results as rr


# ============================================================
# 1. parse_junit：按 func 跨节点聚合 + message 前缀分类
# ============================================================

_JUNIT = """<?xml version="1.0" encoding="utf-8"?>
<testsuites>
 <testsuite name="pytest" tests="6" errors="2" failures="2"
            timestamp="2026-09-09T17:37:46.389723+08:00">
  <testcase classname="testcase.园区基线.FeatureA.test_FeatureA.TestAlpha" name="test_body_fail[1]">
    <failure message="相等断言失败: key &quot;$.retCode&quot; 期望值 &quot;1&quot; 实际值 &quot;0&quot;"/>
  </testcase>
  <testcase classname="testcase.园区基线.FeatureA.test_FeatureA.TestBeta" name="test_setup_fail[1]">
    <error message="failed on setup with &quot;AssertionError: $.msg 期望 success 实际 fail&quot;"/>
  </testcase>
  <testcase classname="testcase.园区基线.FeatureA.test_FeatureA.TestGamma" name="test_teardown_and_fail[1]">
    <failure message="TypeError: &apos;NoneType&apos; object is not iterable"/>
  </testcase>
  <testcase classname="testcase.园区基线.FeatureA.test_FeatureA.TestGamma" name="test_teardown_and_fail[1]">
    <error message="failed on teardown with &quot;TypeError&quot;"/>
  </testcase>
  <testcase classname="testcase.园区基线.FeatureA.test_FeatureA.TestDelta" name="test_teardown_only[1]">
    <error message="failed on teardown with &quot;TypeError&quot;"/>
  </testcase>
  <testcase classname="testcase.园区基线.FeatureA.test_FeatureA.TestEps" name="test_ok[1]"/>
 </testsuite>
</testsuites>
"""


class TestParseJunit:
    def _parse(self, tmp_path, text=_JUNIT):
        p = os.path.join(str(tmp_path), "junit.xml")
        with open(p, "w", encoding="utf-8") as f:
            f.write(text)
        return rr.parse_junit(p)

    def test_verdict_prefix_classification(self, tmp_path):
        parsed = self._parse(tmp_path)
        got = {r["func"]: r for r in parsed["records"]}
        assert got["test_body_fail"]["verdict"] == "failed"          # <failure>
        assert got["test_setup_fail"]["verdict"] == "broken"         # failed on setup
        # failure + failed-on-teardown → failed 且 teardown_error=True（<failure> 优先）
        assert got["test_teardown_and_fail"]["verdict"] == "failed"
        assert got["test_teardown_and_fail"]["teardown_error"] is True
        # 纯 failed on teardown → passed + 标记
        assert got["test_teardown_only"]["verdict"] == "passed"
        assert got["test_teardown_only"]["teardown_error"] is True
        assert got["test_ok"]["verdict"] == "passed"
        # class 名取 classname 末段
        assert got["test_body_fail"]["class"] == "TestAlpha"

    def test_param_dedup(self, tmp_path):
        text = _JUNIT.replace('name="test_body_fail[1]"', 'name="test_body_fail[1]"', 1)
        # 同名 func 出现两次（param 化重复节点），应聚合为一条
        text = text.replace(
            '</testsuite>',
            '  <testcase classname="x.TestAlpha" name="test_body_fail[2]">'
            '    <failure message="dup"/>\n  </testcase></testsuite>')
        parsed = self._parse(tmp_path, text)
        names = [r["func"] for r in parsed["records"]]
        assert names.count("test_body_fail") == 1
        assert parsed["timestamp"] == "2026-09-09T17:37:46.389723+08:00"

    def test_missing_file_raises(self):
        with pytest.raises(FileNotFoundError):
            rr.parse_junit("Z:/no_such/junit.xml")


# ============================================================
# 2. 日志切块
# ============================================================

_LOG = """\
接口名称:删除某园区 全量 进出相关数据
接口请求地址:https://dev/mock/delAllForMock
测试用例名称:PRE_001_CLEAR_ALL
请求参数:{'parkCode': 'PARK-FIXTURE'}
接口实际返回信息{"success":true}
相等断言成功: $.success = True
接口名称:车场保存
接口请求地址:https://dev/parkPark/saveOrUpdate
测试用例名称:CASE_save_001
接口实际返回信息{"retCode":0,"msg":"fail","data":"绑定的临停收费规则不存在或类型不正确"}
相等断言失败: key "$.retCode" 期望值 "1"，实际值 "0"
接口名称:软标记块
测试用例名称:CASE_soft_001
接口实际返回信息{"retCode":0,"msg":"fail","data":"无断言覆盖"}
"""


class TestParseRunLogBlocks:
    def test_block_split_and_flags(self, tmp_path):
        p = os.path.join(str(tmp_path), "test.20260909173746.log")
        with open(p, "w", encoding="utf-8") as f:
            f.write(_LOG)
        blocks = rr.parse_run_log_blocks(p)
        assert len(blocks) == 3
        b0, b1, b2 = blocks
        assert b0["case_name"] == "PRE_001_CLEAR_ALL" and b0["ok"] is True
        assert b0["resp_json"] == {"success": True}
        assert b0["business_suspect"] is False
        assert b1["case_name"] == "CASE_save_001" and b1["ok"] is False
        assert b1["failures"] and "相等断言失败" in b1["failures"][0]
        assert b1["resp_json"]["data"] == "绑定的临停收费规则不存在或类型不正确"
        # 无断言失败标记但响应语义失败 → 软标记，不翻转 ok
        assert b2["ok"] is True and b2["business_suspect"] is True

    def test_missing_log_raises(self):
        with pytest.raises(FileNotFoundError):
            rr.parse_run_log_blocks("Z:/no_such/test.log")


# ============================================================
# 3. YAML case_name 读取 + 文件级归因
# ============================================================

_YAML_FILE = """\
- baseInfo:
    api_name: 车场保存
    method: post
    url: /a
  testCase:
  - case_name: CASE_save_001
    json: {}
    validation:
    - eq:
        $.retCode: 1
- baseInfo:
    api_name: 查询
    method: post
    url: /b
  testCase:
  - case_name: CASE_query_001
    json: {}
    validation: []
"""


class TestFileFailureExtraction:
    def test_read_yaml_case_names(self, tmp_path):
        y = os.path.join(str(tmp_path), "test_data.yaml")
        with open(y, "w", encoding="utf-8") as f:
            f.write(_YAML_FILE)
        assert rr.read_yaml_case_names(y) == ["CASE_save_001", "CASE_query_001"]
        assert rr.read_yaml_case_names(os.path.join(str(tmp_path), "nope.yaml")) == []

    def test_extract_only_this_file_failures(self, tmp_path):
        y = os.path.join(str(tmp_path), "test_data.yaml")
        with open(y, "w", encoding="utf-8") as f:
            f.write(_YAML_FILE)
        log_path = os.path.join(str(tmp_path), "test.log")
        with open(log_path, "w", encoding="utf-8") as f:
            f.write("接口名称:a\n测试用例名称:CASE_save_001\n"
                    "接口实际返回信息{\"retCode\":0,\"msg\":\"fail\"}\n相等断言失败: x\n"
                    "接口名称:c\n测试用例名称:CASE_OTHER\n"
                    "接口实际返回信息{\"retCode\":0,\"msg\":\"fail\"}\n相等断言失败: x\n")
        blocks = rr.parse_run_log_blocks(log_path)
        out = rr.extract_file_failures(blocks, y)
        assert out["case_name"] == ["CASE_save_001"]          # CASE_OTHER 不属于本文件 → 滤掉
        assert any("retCode=0" in s for s in out["server_reason"])

    def test_no_match_returns_empty_not_raise(self, tmp_path):
        y = os.path.join(str(tmp_path), "test_data.yaml")
        with open(y, "w", encoding="utf-8") as f:
            f.write(_YAML_FILE)
        # 日志里没有任何本文件 case_name 的失败块
        log_path = os.path.join(str(tmp_path), "test.log")
        with open(log_path, "w", encoding="utf-8") as f:
            f.write("接口名称:a\n测试用例名称:UNKNOWN\n"
                    "接口实际返回信息{\"retCode\":0}\n相等断言失败: x\n")
        out = rr.extract_file_failures(rr.parse_run_log_blocks(log_path), y)
        assert out == {"case_name": [], "server_reason": []}


# ============================================================
# 4. build_module_map：AST 解析 runner .py（+ 目录兜底）
# ============================================================

_RUNNER_PY = '''\
import pytest

@pytest.fixture(scope="class")
def setup_alpha():
    read = ReadYamlData()
    base = RequestsBase()
    base.run_blocks('./testcase/F/FeatureA/setup_data/setup_alpha.yaml')
    yield
    base.run_blocks('./testcase/F/FeatureA/setup_data/teardown_alpha.yaml')

@pytest.mark.usefixtures("setup_alpha")
class TestAlpha:
    def test_alpha_one(self):
        RequestsBase().run_blocks('./testcase/F/FeatureA/test_alpha_one/test_data.yaml')
    def test_alpha_pass(self):
        RequestsBase().run_blocks('./testcase/F/FeatureA/test_alpha_pass/test_data.yaml')

class TestNopre:
    def test_nopre_one(self):
        RequestsBase().run_blocks('./testcase/F/FeatureA/test_nopre_one/test_data.yaml')
'''


def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


class TestBuildModuleMap:
    def test_ast_parses_runner_py(self, tmp_path):
        module = str(tmp_path)
        py = _write(os.path.join(module, "FeatureA", "test_FeatureA.py"), _RUNNER_PY)
        m = rr.build_module_map(module)
        assert m["py_files"] == [py]
        # func → test_data（绝对，feature_dir 下）
        fe = m["funcs"]["test_alpha_one"]
        assert fe["class"] == "TestAlpha"
        assert fe["test_yaml"] == os.path.join(module, "FeatureA", "test_alpha_one", "test_data.yaml")
        # class → setup（真实 run_blocks）
        ca = m["classes"]["TestAlpha"]
        assert ca["has_setup"] is True
        assert ca["setup_yaml"] == os.path.join(module, "FeatureA", "setup_data", "setup_alpha.yaml")
        # 无前置 class：无 usefixtures → 不伪造 setup
        cn = m["classes"]["TestNopre"]
        assert cn["has_setup"] is False
        assert cn["setup_yaml"] is None

    def test_no_py_directory_fallback(self, tmp_path):
        module = str(tmp_path)
        _write(os.path.join(module, "FeatureA", "test_alpha_one", "test_data.yaml"), _YAML_FILE)
        m = rr.build_module_map(module)
        assert m["py_files"] == []
        assert "test_alpha_one" in m["funcs"]
        assert m["funcs"]["test_alpha_one"]["test_yaml"].endswith(
            os.path.join("FeatureA", "test_alpha_one", "test_data.yaml"))

    def test_class_setup_abs_resolves(self, tmp_path):
        module = str(tmp_path)
        _write(os.path.join(module, "FeatureA", "test_FeatureA.py"), _RUNNER_PY)
        setup = os.path.join(module, "FeatureA", "setup_data", "setup_alpha.yaml")
        _write(setup, "- baseInfo:\n    url: /x\n  testCase:\n  - case_name: PRE_a\n")
        m = rr.build_module_map(module)
        assert rr.class_setup_abs(m, module, "TestAlpha") == setup
        # 未知 class → slug 探测
        assert rr.class_setup_abs(m, module, "TestAlpha") is not None
        assert rr.class_setup_abs(m, module, "TestNoSuch") is None


# ============================================================
# 5. ingest_run_results：写/覆盖 sidecar + 归因 + run_ts 优先级
# ============================================================

_MODULE_LAYOUT = {
    "FeatureA/test_alpha_one/test_data.yaml":
        "- baseInfo:\n    url: /a\n  testCase:\n  - case_name: CASE_alpha_1\n",
    "FeatureA/test_alpha_pass/test_data.yaml":
        "- baseInfo:\n    url: /a\n  testCase:\n  - case_name: CASE_alpha_pass_1\n",
    "FeatureA/test_beta_one/test_data.yaml":
        "- baseInfo:\n    url: /a\n  testCase:\n  - case_name: CASE_beta_1\n",
    "FeatureA/setup_data/setup_alpha.yaml":
        "- baseInfo:\n    url: /s\n  testCase:\n  - case_name: PRE_alpha_1\n",
    "FeatureA/setup_data/setup_beta.yaml":
        "- baseInfo:\n    url: /s\n  testCase:\n  - case_name: PRE_BETA\n",
}

_JUNIT_INGEST = """<?xml version="1.0" encoding="utf-8"?>
<testsuite name="pytest" timestamp="2026-09-09T17:37:46.389723+08:00">
 <testcase classname="...TestAlpha" name="test_alpha_one[1]">
   <failure message="相等断言失败: $.retCode 期望 1 实际 0"/>
 </testcase>
 <testcase classname="...TestAlpha" name="test_alpha_pass[1]"/>
 <testcase classname="...TestBeta" name="test_beta_one[1]">
   <error message="failed on setup with &quot;AssertionError: PRE_BETA&quot;"/>
 </testcase>
</testsuite>
"""


def _build_module(tmp_path, layout):
    module = str(tmp_path)
    _write(os.path.join(module, "FeatureA", "test_FeatureA.py"), _RUNNER_PY)
    for rel, text in layout.items():
        _write(os.path.join(module, rel), text)
    return module


class TestIngestRunResults:
    def test_writes_sidecars_and_attributes(self, tmp_path):
        module = _build_module(tmp_path, _MODULE_LAYOUT)
        junit = _write(os.path.join(str(tmp_path), "junit.xml"), _JUNIT_INGEST)
        log = _write(os.path.join(str(tmp_path), "test.20260909173746.log"), _LOG)
        out = rr.ingest_run_results(module, junit, log)

        # broken-by-setup 的 test_beta_one test_data **不写** sidecar
        assert "FeatureA/test_beta_one/test_data.yaml" not in out
        # body 失败 → test sidecar failed
        assert out["FeatureA/test_alpha_one/test_data.yaml"]["verdict"] == "failed"
        # _LOG 的失败块 case_name 是 CASE_save_001（非本文件 YAML）→ 按漂移降级为空，junit_msg 兜底
        assert out["FeatureA/test_alpha_one/test_data.yaml"]["case_name"] == []
        assert "相等断言失败" in out["FeatureA/test_alpha_one/test_data.yaml"]["junit_msg"]
        # 匹配归因见 test_attribution_with_matching_log
        # passed 清场
        assert out["FeatureA/test_alpha_pass/test_data.yaml"]["verdict"] == "passed"
        assert out["FeatureA/setup_data/setup_alpha.yaml"]["verdict"] == "passed"
        # setup 破损
        assert out["FeatureA/setup_data/setup_beta.yaml"]["verdict"] == "broken"
        # 落点文件真实落盘
        assert os.path.isfile(os.path.join(
            module, "FeatureA", "test_alpha_one", "test_data.last_run.json"))
        assert os.path.isfile(os.path.join(
            module, "FeatureA", "setup_data", "setup_beta.last_run.json"))
        assert os.path.isfile(os.path.join(
            module, "FeatureA", "test_alpha_pass", "test_data.last_run.json"))

    def test_attribution_with_matching_log(self, tmp_path):
        module = _build_module(tmp_path, _MODULE_LAYOUT)
        junit = _write(os.path.join(str(tmp_path), "junit.xml"), _JUNIT_INGEST)
        log = _write(os.path.join(str(tmp_path), "test.20260909173746.log"),
                     "接口名称:车场保存\n测试用例名称:CASE_alpha_1\n"
                     "接口实际返回信息{\"retCode\":0,\"msg\":\"fail\",\"data\":\"绑定的临停收费规则不存在\"}\n"
                     "相等断言失败: x\n"
                     "接口名称:绑定\n测试用例名称:PRE_BETA\n"
                     "接口实际返回信息{\"retCode\":0,\"msg\":\"fail\",\"data\":\"收费规则不存在\"}\n"
                     "请求处理异常: ...\n")
        out = rr.ingest_run_results(module, junit, log)
        rec_a = out["FeatureA/test_alpha_one/test_data.yaml"]
        assert rec_a["case_name"] == ["CASE_alpha_1"]
        assert any("收费规则不存在" in s for s in rec_a["server_reason"])
        rec_b = out["FeatureA/setup_data/setup_beta.yaml"]
        assert rec_b["case_name"] == ["PRE_BETA"]
        assert rec_b["junit_msg"].startswith("failed on setup")

    def test_run_ts_priority(self, tmp_path):
        module = _build_module(tmp_path, _MODULE_LAYOUT)
        junit = _write(os.path.join(str(tmp_path), "junit.xml"), _JUNIT_INGEST)
        out = rr.ingest_run_results(module, junit, None, run_ts="20260910120000")
        assert all(r["run_ts"] == "20260910120000" for r in out.values())
        # junit timestamp 解析
        out2 = rr.ingest_run_results(module, junit)
        assert any(r["run_ts"] == "20260909173746" for r in out2.values())

    def test_resolve_run_ts(self, tmp_path):
        assert rr.resolve_run_ts("20260910120000", "junk", None) == "20260910120000"
        assert rr.resolve_run_ts("", "2026-09-09T17:37:46.389723+08:00", None) == "20260909173746"
        p = _write(os.path.join(str(tmp_path), "test.20260901184506.log"), "x")
        assert rr.resolve_run_ts("", "", p) == "20260901184506"
        with pytest.raises(ValueError):
            rr.resolve_run_ts("", "", None)


# ============================================================
# 6. read_last_run_records：只留 max run_ts cohort
# ============================================================

class TestReadLastRunRecords:
    def _sidecar(self, module, rel_yaml, run_ts, verdict):
        yaml_abs = os.path.join(module, *rel_yaml.split("/"))
        sp = os.path.splitext(yaml_abs)[0] + ".last_run.json"
        _write(sp, json.dumps(
            {"run_ts": run_ts, "verdict": verdict,
             "case_name": [], "server_reason": [], "junit_msg": ""},
            ensure_ascii=False))

    def test_keeps_only_max_cohort(self, tmp_path):
        module = str(tmp_path)
        self._sidecar(module, "A/test_one/test_data.yaml", "20260909173746", "failed")
        self._sidecar(module, "A/test_two/test_data.yaml", "20260909173746", "passed")
        self._sidecar(module, "A/test_three/test_data.yaml", "20260910120000", "failed")
        recs = rr.read_last_run_records(module)
        assert set(recs) == {"A/test_three/test_data.yaml"}
        assert recs["A/test_three/test_data.yaml"]["run_ts"] == "20260910120000"

    def test_no_records_returns_empty(self, tmp_path):
        assert rr.read_last_run_records(str(tmp_path)) == {}


# ============================================================
# 7. select_rerun_targets + build_failure_digest
# ============================================================

class TestSelectAndDigest:
    def _records(self):
        return {
            "F/test_body_fail/test_data.yaml": {
                "verdict": "failed", "case_name": ["CASE_1"],
                "server_reason": ["retCode=0 data=绑定不存在"],
                "junit_msg": "相等断言失败"},
            "F/test_setup_broken/test_data.yaml": {
                "verdict": "broken", "case_name": [], "server_reason": [],
                "junit_msg": "failed on setup ..."},
            "F/setup_data/setup_x.yaml": {
                "verdict": "broken", "case_name": ["PRE_X"],
                "server_reason": ["data=收费规则不存在"], "junit_msg": "failed on setup"},
            "F/setup_data/setup_y.yaml": {
                "verdict": "passed", "case_name": [], "server_reason": [], "junit_msg": ""},
        }

    def test_select_targets(self, tmp_path):
        module = str(tmp_path)
        t = rr.select_rerun_targets(module, self._records())
        assert t["test_yamls"] == [os.path.join(module, "F", "test_body_fail", "test_data.yaml")]
        assert t["setup_yamls"] == [os.path.join(module, "F", "setup_data", "setup_x.yaml")]
        # broken-by-setup 的 test → excluded，不重生成其 test_data
        assert t["excluded_broken"] and t["excluded_broken"][0]["rel"].endswith(
            "test_setup_broken/test_data.yaml")
        assert all(not p.endswith("test_setup_broken/test_data.yaml") for p in t["test_yamls"])

    def test_empty_records_empty_targets(self, tmp_path):
        t = rr.select_rerun_targets(str(tmp_path), {})
        assert t["test_yamls"] == [] and t["setup_yamls"] == [] and t["excluded_broken"] == []

    def test_digest_has_facts_no_examples(self, tmp_path):
        module = str(tmp_path)
        recs = self._records()
        targets = rr.select_rerun_targets(module, recs)
        digests = rr.build_failure_digest(module, recs, targets)
        assert len(digests) == 2
        for d in digests.values():
            assert "本次执行失败反馈" in d
            assert "禁止给出示例" in d            # 收尾规则含禁示例
            assert "禁止编造固定数据" in d
        txt = digests[os.path.join(module, "F", "test_body_fail", "test_data.yaml")]
        assert "CASE_1" in txt and "绑定不存在" in txt and "相等断言失败" in txt
