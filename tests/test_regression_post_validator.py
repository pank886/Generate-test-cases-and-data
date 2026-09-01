"""PostValidator 回归测试 — YAML 后校验纯函数。

运行方式:
  pytest tests/test_regression_post_validator.py -v
"""

import os
import sys
import tempfile
import yaml as _yaml

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent_components.validation.yaml_validator import YamlPostValidator


# ============================================================
# 帮助函数
# ============================================================

def _make_step(method="post", url="/test/delete", json_body=None, validation=None):
    """构建一个最小合法 StepData dict。"""
    tc = {"case_name": "test_case_1"}
    if json_body is not None:
        tc["json"] = json_body
    if validation is not None:
        tc["validation"] = validation
    else:
        tc["validation"] = [{"eq": {"$.retCode": 0}}]
    return {
        "baseInfo": {
            "api_name": "测试接口",
            "method": method,
            "url": url,
            "header": {"Content-Type": "application/json;charset=UTF-8"},
        },
        "testCase": [tc],
    }


def _make_testdata(steps: list) -> dict:
    """包装为 TestData dict。"""
    return {"data": steps}


# ============================================================
# 1. _check_delete_body_wrapper
# ============================================================

class TestCheckDeleteBodyWrapper:

    def test_detects_body_wrapping(self):
        """检测 json: {body: [...]}} 包裹层。"""
        validator = YamlPostValidator()
        step = _make_step(
            method="post", url="/meter/delete",
            json_body={"body": [{"id": "001"}, {"id": "002"}]},
        )
        issues = validator._check_delete_body_wrapper(step, "test.yaml")
        assert len(issues) == 1
        assert issues[0]["check"] == "delete_body_wrapper"
        assert issues[0]["severity"] == "P0"
        assert "body" in issues[0]["current"]
        assert "body" not in issues[0]["expected"]

    def test_normal_json_not_flagged(self):
        """普通 json dict（非 body 包裹）不误报。"""
        validator = YamlPostValidator()
        step = _make_step(
            json_body={"meterCode": "M001", "status": 1},
        )
        issues = validator._check_delete_body_wrapper(step, "test.yaml")
        assert len(issues) == 0

    def test_list_json_not_flagged(self):
        """json 直接是 list — 正确用法，不误报。"""
        validator = YamlPostValidator()
        step = _make_step(
            json_body=["id1", "id2"],
        )
        issues = validator._check_delete_body_wrapper(step, "test.yaml")
        assert len(issues) == 0

    def test_empty_body_not_flagged(self):
        """body: [] 空数组不触发。"""
        validator = YamlPostValidator()
        step = _make_step(json_body={"body": []})
        issues = validator._check_delete_body_wrapper(step, "test.yaml")
        assert len(issues) == 0

    def test_body_with_extra_keys_not_flagged(self):
        """body 不是唯一 key 时不触发。"""
        validator = YamlPostValidator()
        step = _make_step(json_body={"body": [{"id": "1"}], "other": "val"})
        issues = validator._check_delete_body_wrapper(step, "test.yaml")
        assert len(issues) == 0

    def test_get_method_not_checked(self):
        """GET 方法不检查 delete body wrapper。"""
        validator = YamlPostValidator()
        step = _make_step(
            method="get", url="/test/query",
            json_body={"body": [{"id": "1"}]},
        )
        issues = validator._check_delete_body_wrapper(step, "test.yaml")
        assert len(issues) == 0

    def test_body_scalar_elements_not_flagged(self):
        """body 元素是简单值而非 dict 时不触发。"""
        validator = YamlPostValidator()
        step = _make_step(json_body={"body": ["id1", "id2"]})
        issues = validator._check_delete_body_wrapper(step, "test.yaml")
        assert len(issues) == 0


# ============================================================
# 2. _check_assertion_dynamic_key
# ============================================================

class TestCheckAssertionDynamicKey:

    def test_detects_dynamic_key(self):
        """断言 key 使用 ${} 模板 — 检测到（validation item 第一层 key）。"""
        validator = YamlPostValidator()
        # 注意：_check_assertion_dynamic_key 检查 validation item 的第一层 key
        # 真实场景：{"${get_extract_data('firstCode')}": "meterCode02"} — 陷阱 10
        step = _make_step(
            validation=[{"${get_extract_data('code')}": "expected_value"}],
        )
        issues = validator._check_assertion_dynamic_key(step, "test.yaml")
        assert len(issues) == 1
        assert issues[0]["check"] == "assertion_dynamic_key"
        assert issues[0]["severity"] == "P1"

    def test_jsonpath_key_not_flagged(self):
        """$.data.code 是合法 JSONPath key — 不误报。"""
        validator = YamlPostValidator()
        step = _make_step(
            validation=[{"eq": {"$.data.code": "expected"}}],
        )
        issues = validator._check_assertion_dynamic_key(step, "test.yaml")
        assert len(issues) == 0

    def test_static_string_key_not_flagged(self):
        """普通字符串 key — 不误报。"""
        validator = YamlPostValidator()
        step = _make_step(
            validation=[{"eq": {"retCode": 0}}],
        )
        issues = validator._check_assertion_dynamic_key(step, "test.yaml")
        assert len(issues) == 0

    def test_dynamic_value_not_flagged(self):
        """value 位置使用 ${} 是合规的 — 不误报。"""
        validator = YamlPostValidator()
        step = _make_step(
            validation=[{"eq": {"$.meterName": "${get_extract_data('meterName')}"}}],
        )
        issues = validator._check_assertion_dynamic_key(step, "test.yaml")
        assert len(issues) == 0


# ============================================================
# 3. _check_malformed_assertion
# ============================================================

class TestCheckMalformedAssertion:

    def test_catches_unmatched_quote_in_key(self):
        """Key 中未配对引号 — 告警 P2（validation item 第一层 key）。"""
        validator = YamlPostValidator()
        # 注意：检查的是 validation item 的第一层 key（非嵌套 dict 的 key）
        step = _make_step(
            validation=[{"eq unmatched'": "value"}],
        )
        issues = validator._check_malformed_assertion(step, "test.yaml")
        found = [i for i in issues if i["check"] == "malformed_assertion"]
        assert len(found) >= 1
        assert found[0]["severity"] == "P2"

    def test_matched_quotes_not_flagged(self):
        """配对引号 — 不告警。"""
        validator = YamlPostValidator()
        step = _make_step(
            validation=[{"eq": {"$.data.name": "value"}}],
        )
        issues = validator._check_malformed_assertion(step, "test.yaml")
        assert len(issues) == 0


# ============================================================
# 4. validate_all — 集成验证
# ============================================================

class TestValidateAll:

    def test_empty_dir_no_issues(self, tmp_path):
        """空目录返回空列表。"""
        validator = YamlPostValidator()
        issues = validator.validate_all(str(tmp_path))
        assert issues == []

    def test_valid_yaml_no_issues(self, tmp_path):
        """合法 YAML 无校验问题。"""
        validator = YamlPostValidator()
        data = _make_testdata([
            _make_step(json_body={"code": "001"}, validation=[{"eq": {"$.retCode": 0}}]),
        ])
        yaml_path = tmp_path / "test_data.yaml"
        with open(yaml_path, "w", encoding="utf-8") as f:
            _yaml.dump(data, f, allow_unicode=True)
        issues = validator.validate_all(str(tmp_path))
        assert len(issues) == 0

    def test_yaml_with_body_wrapper_detected(self, tmp_path):
        """包含 body 包裹的 YAML 被检出。"""
        validator = YamlPostValidator()
        data = _make_testdata([
            _make_step(
                method="post", url="/meter/delete",
                json_body={"body": [{"id": "001"}, {"id": "002"}]},
            ),
        ])
        yaml_path = tmp_path / "test_data.yaml"
        with open(yaml_path, "w", encoding="utf-8") as f:
            _yaml.dump(data, f, allow_unicode=True)
        issues = validator.validate_all(str(tmp_path))
        assert len(issues) >= 1
        assert any(i["check"] == "delete_body_wrapper" for i in issues)

    def test_nested_directories_scanned(self, tmp_path):
        """递归扫描子目录。"""
        validator = YamlPostValidator()
        sub_dir = tmp_path / "sub"
        sub_dir.mkdir()
        data = _make_testdata([_make_step(json_body={"body": [{"id": "1"}]})])
        yaml_path = sub_dir / "deep.yaml"
        with open(yaml_path, "w", encoding="utf-8") as f:
            _yaml.dump(data, f, allow_unicode=True)
        issues = validator.validate_all(str(tmp_path))
        assert len(issues) >= 1

    def test_malformed_yaml_skipped(self, tmp_path):
        """损坏的 YAML 文件不中断扫描。"""
        validator = YamlPostValidator()
        bad_path = tmp_path / "bad.yaml"
        bad_path.write_text("::: not valid yaml :::", encoding="utf-8")
        issues = validator.validate_all(str(tmp_path))
        assert isinstance(issues, list)
