"""V1–V5 车场绑定值形态校验回归测试（2026-09-10）。

背景：智慧停车 Round 3 —— parkingPark/saveOrUpdate 79 次调用 47 次失败，
44 次同属一条根因：bindPayCode 未以「标量编码」形态送达服务端。
规则表准据见 changelog/2026-09-10_parking_phase_c_rerun_two_rounds.md §16.7。

标定基准：拿 Round 3 真实产物 47 个 setup 跑，命中 20/20 个失败 setup，
0 误报、0 漏报 —— 本文件的用例即该标定结论的最小复现。

运行方式:
  pytest tests/test_bind_value_form_validator.py -v
"""

import os
import sys

import yaml as _yaml

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent_components.validation.yaml_validator import (  # noqa: E402
    _scan_extract_value_forms,
)

PARK_SAVE = "/park-access-parking-web/parkingPark/saveOrUpdate"
RULE_ADD = "/park-access-parking-web/parkingPaymentConfig/add"
RULE_PAGE = "/park-access-parking-web/parkingPaymentConfig/getParkingPaymentConfigPage"


# ============================================================
# 帮助函数
# ============================================================

def _step(url, case_name="tc", json_body=None, input_extract=None,
          extract=None, extract_list=None):
    """构建一个顶层 list 形态的 setup 步骤。"""
    tc = {"case_name": case_name, "validation": [{"eq": {"$.retCode": 1}}]}
    if json_body is not None:
        tc["json"] = json_body
    for blk, val in (("input_extract", input_extract),
                     ("extract", extract),
                     ("extract_list", extract_list)):
        if val is not None:
            tc[blk] = val
    return {"baseInfo": {"api_name": "x", "method": "post", "url": url}, "testCase": [tc]}


def _park_step(bind, main=True, need=1, case_name="绑定车场"):
    """构建一个 parkingPark/saveOrUpdate 绑定步骤。"""
    return _step(PARK_SAVE, case_name=case_name,
                 json_body={"parkingPark": {
                     "bindPayCode": bind, "mainPark": main, "needPay": need}})


def _rule_add(prefix="pre001RuleName"):
    return _step(RULE_ADD, case_name="建规则",
                 input_extract={prefix: "$.json.name"})


def _rule_readback(prefix="pre001RuleCode", block="extract"):
    kw = {block: {prefix: "$.data.list[0].code"}}
    return _step(RULE_PAGE, case_name="回读规则编码", **kw)


def _write_setup(tmp_path, steps, name="setup_x.yaml"):
    """把步骤写成 setup_*.yaml，返回作为 output_base 的目录。"""
    with open(tmp_path / name, "w", encoding="utf-8") as f:
        _yaml.dump(steps, f, allow_unicode=True)
    return str(tmp_path)


def _checks(issues):
    return sorted(i["check"] for i in issues)


# ============================================================
# V1 — mainPark/needPay 开启时绑定值不得为空
# ============================================================

class TestV1EmptyBind:

    def test_main_park_empty_flagged(self, tmp_path):
        """主场 + needPay=0 + 空串 → 命中（实测服务端会拒：主场类型必须要绑定计费）。"""
        out = _write_setup(tmp_path, [_park_step("", main=True, need=0)])
        assert _checks(_scan_extract_value_forms(out)) == ["bind_pay_code_empty"]

    def test_need_pay_empty_flagged(self, tmp_path):
        """子场但已开计费 + 空串 → 命中。"""
        out = _write_setup(tmp_path, [_park_step("", main=False, need=1)])
        assert _checks(_scan_extract_value_forms(out)) == ["bind_pay_code_empty"]

    def test_missing_field_flagged(self, tmp_path):
        """字段整体缺失同样按空处理。"""
        out = _write_setup(tmp_path, [_park_step(None, main=True, need=1)])
        assert _checks(_scan_extract_value_forms(out)) == ["bind_pay_code_empty"]

    def test_sub_park_not_charging_not_flagged(self, tmp_path):
        """子场且未开计费 → 不校验（避免误报）。"""
        out = _write_setup(tmp_path, [_park_step("", main=False, need=0)])
        assert _scan_extract_value_forms(out) == []


# ============================================================
# V2 — 非空时必须是提取引用，不得字面量
# ============================================================

class TestV2Literal:

    def test_rule_name_literal_flagged(self, tmp_path):
        """写规则名字面量 → 命中（B 类 28 次的形态）。"""
        out = _write_setup(tmp_path, [_park_step("RULE-HOME")])
        assert _checks(_scan_extract_value_forms(out)) == ["bind_pay_code_literal"]

    def test_literal_flagged_even_when_not_charging(self, tmp_path):
        """未开计费也拦：实测 mainPark=0/needPay=0 绑字面量照样被服务端拒。"""
        out = _write_setup(tmp_path, [_park_step("RULE-CHILD", main=False, need=0)])
        assert _checks(_scan_extract_value_forms(out)) == ["bind_pay_code_literal"]

    def test_bare_key_without_quotes_flagged(self, tmp_path):
        """键未加引号的裸引用 → 命中，且提示指向引号/前缀问题。"""
        out = _write_setup(tmp_path, [_park_step("${get_extract_data(ruleChildCode)}")])
        issues = _scan_extract_value_forms(out)
        assert _checks(issues) == ["bind_pay_code_literal"]
        assert "裸键" in issues[0]["current"]


# ============================================================
# V3 — 键必须由更早步骤声明
# ============================================================

class TestV3Undeclared:

    def test_key_never_declared_flagged(self, tmp_path):
        """引用了一个全文件没人产出的键 → 命中。"""
        out = _write_setup(tmp_path, [
            _rule_add(), _rule_readback(),
            _park_step("${get_extract_data('pre999RuleCode')}"),
        ])
        assert _checks(_scan_extract_value_forms(out)) == ["bind_pay_code_undeclared"]

    def test_key_declared_later_flagged(self, tmp_path):
        """声明步晚于引用步 → 命中。"""
        out = _write_setup(tmp_path, [
            _park_step("${get_extract_data('pre001RuleCode')}"),
            _rule_add(), _rule_readback(),
        ])
        assert _checks(_scan_extract_value_forms(out)) == ["bind_pay_code_undeclared"]


# ============================================================
# V4 — 声明步必须是回读步，且之前有规则添加步
# ============================================================

class TestV4NoReadback:

    def test_declaring_step_not_page_flagged(self, tmp_path):
        """键由非分页步的响应声明 → 命中。"""
        out = _write_setup(tmp_path, [
            _rule_add(),
            _step(RULE_ADD, case_name="另一步", extract={"pre001RuleCode": "$.data.code"}),
            _park_step("${get_extract_data('pre001RuleCode')}"),
        ])
        assert _checks(_scan_extract_value_forms(out)) == ["bind_pay_code_no_readback"]

    def test_no_prior_add_flagged(self, tmp_path):
        """有回读步但之前压根没建过规则 → 命中（断点①：缺半条链）。"""
        out = _write_setup(tmp_path, [
            _rule_readback(),
            _park_step("${get_extract_data('pre001RuleCode')}"),
        ])
        assert _checks(_scan_extract_value_forms(out)) == ["bind_pay_code_no_readback"]

    def test_multi_pairs_key_from_second_pair_ok(self, tmp_path):
        """回归：一个 setup 含多组 add/回读对，键取自第二组 —— 不得误报。

        这是标定阶段真实踩过的坑：用「第一个分页步」比对会把通过的
        setup_charge_rule_parking_type_match / setup_device_list_query 判失败。
        """
        out = _write_setup(tmp_path, [
            _rule_add("pre003RuleName"), _rule_readback("pre003RuleCode"),
            _rule_add("pre004RuleName"), _rule_readback("pre004RuleCode"),
            _park_step("${get_extract_data('pre004RuleCode')}"),
        ])
        assert _scan_extract_value_forms(out) == []


# ============================================================
# V5 — extract_list 键的 get_extract_data 引用必须带索引
# ============================================================

class TestV5MissingIndex:

    def test_bare_ref_to_extract_list_key_flagged(self, tmp_path):
        """E 类活样本：setup_delete_device 的形态 —— extract_list 键裸引用。"""
        out = _write_setup(tmp_path, [
            _rule_add(), _rule_readback(block="extract_list"),
            _park_step("${get_extract_data('pre001RuleCode')}"),
        ])
        assert _checks(_scan_extract_value_forms(out)) == ["bind_pay_code_missing_index"]

    def test_indexed_ref_ok(self, tmp_path):
        """带索引取标量 → 不命中。"""
        out = _write_setup(tmp_path, [
            _rule_add(), _rule_readback(block="extract_list"),
            _park_step("${get_extract_data('pre001RuleCode', 1)}"),
        ])
        assert _scan_extract_value_forms(out) == []

    def test_split_extract_bare_ok(self, tmp_path):
        """split_extract_data 默认 index=0，裸用安全 → 不命中。"""
        out = _write_setup(tmp_path, [
            _rule_add(), _rule_readback(block="extract_list"),
            _park_step("${split_extract_data('pre001RuleCode')}"),
        ])
        assert _scan_extract_value_forms(out) == []

    def test_scalar_extract_bare_ok(self, tmp_path):
        """键源自 extract（标量）时裸引用安全 → 不命中。"""
        out = _write_setup(tmp_path, [
            _rule_add(), _rule_readback(block="extract"),
            _park_step("${get_extract_data('pre001RuleCode')}"),
        ])
        assert _scan_extract_value_forms(out) == []


# ============================================================
# 集成：形状、扫描范围、端到端
# ============================================================

class TestScanIntegration:

    def test_full_correct_chain_no_issue(self, tmp_path):
        """add → 回读 → 绑定 的完整正确链 → 零问题。"""
        out = _write_setup(tmp_path, [
            _rule_add(), _rule_readback(),
            _park_step("${get_extract_data('pre001RuleCode')}"),
        ])
        assert _scan_extract_value_forms(out) == []

    def test_top_level_list_shape_scanned(self, tmp_path):
        """回归：生成器写盘是顶层 list，不是 {data: [...]} —— 必须能扫到。"""
        out = _write_setup(tmp_path, [_park_step("RULE-HOME")])
        assert len(_scan_extract_value_forms(out)) == 1

    def test_non_setup_files_ignored(self, tmp_path):
        """只扫 setup_*.yaml，test_data.yaml 不参与。"""
        out = _write_setup(tmp_path, [_park_step("RULE-HOME")], name="test_data.yaml")
        assert _scan_extract_value_forms(out) == []

    def test_nested_setup_dir_scanned(self, tmp_path):
        """setup_data/ 子目录下的 setup 也要扫到。"""
        sub = tmp_path / "setup_data"
        sub.mkdir()
        out = _write_setup(sub, [_park_step("RULE-HOME")])
        assert len(_scan_extract_value_forms(out)) == 1

    def test_issue_fields_complete(self, tmp_path):
        """问题结构必须与 validate_all 同构，供修复轮直接消费。"""
        out = _write_setup(tmp_path, [_park_step("RULE-HOME")])
        issue = _scan_extract_value_forms(out)[0]
        for k in ("yaml_path", "check", "severity", "line", "current",
                  "expected", "fix_hint", "case_name"):
            assert k in issue, f"缺字段 {k}"
        assert issue["severity"] == "P0"
        assert issue["check"].startswith("bind_pay_code_")

    def test_broken_yaml_skipped(self, tmp_path):
        """损坏 YAML 不中断扫描。"""
        (tmp_path / "setup_bad.yaml").write_text("::: nope :::", encoding="utf-8")
        assert _scan_extract_value_forms(str(tmp_path)) == []

    def test_other_endpoints_ignored(self, tmp_path):
        """非绑定接口的步骤不参与校验。"""
        out = _write_setup(tmp_path, [
            _step("/park-access-parking-web/parkingPark/getDetail",
                  json_body={"parkingPark": {"bindPayCode": "RULE-HOME"}}),
        ])
        assert _scan_extract_value_forms(out) == []
