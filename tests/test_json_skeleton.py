"""Phase C 骨架生成 + JSON 提取 + 修复诊断包 单元测试。

不依赖 LLM / ChromaDB / 服务端，纯逻辑测试。
覆盖 Phase 1 步骤 5 和 Phase 4 步骤 12。
"""
import json
import os
import sys

import pytest
from pydantic import ValidationError

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from prompts.response_model import TestData, StepData, TestCase
from agent_components.generators import (
    generate_json_skeleton,
    _extract_json_from_thinking,
    prepare_repair_context,
    RepairNeeded,
    _error_mentions_placeholder,
    _error_mentions_api,
)


# ============================================================
# 1. generate_json_skeleton — 骨架生成
# ============================================================

class TestSkeletonGeneration:
    """Phase 1 步骤 2/5: 骨架生成正确性验证。"""

    def test_top_level_structure(self):
        """顶层应为 {"data": [...], "file_name": ""}"""
        skeleton = generate_json_skeleton(TestData)
        parsed = json.loads(skeleton)
        assert set(parsed.keys()) == {"data", "file_name"}
        assert isinstance(parsed["data"], list)
        assert len(parsed["data"]) == 1  # 骨架单元素示例

    def test_baseinfo_keys(self):
        """baseInfo 应有 api_name / url / method / header 四个占位键"""
        skeleton = generate_json_skeleton(TestData)
        parsed = json.loads(skeleton)
        base = parsed["data"][0]["baseInfo"]
        assert set(base.keys()) == {"api_name", "url", "method", "header"}

    def test_testcase_keys(self):
        """testCase 应有 case_name / json / params / validation"""
        skeleton = generate_json_skeleton(TestData)
        parsed = json.loads(skeleton)
        tc = parsed["data"][0]["testCase"][0]
        assert "case_name" in tc
        assert "json" in tc
        assert "params" in tc
        assert "validation" in tc

    def test_optional_fields_skipped(self):
        """Optional[Dict] 无 example_keys 的字段不渲染（§5.10）"""
        skeleton = generate_json_skeleton(TestData)
        parsed = json.loads(skeleton)
        tc = parsed["data"][0]["testCase"][0]
        skipped = ("extract", "input_extract", "extract_list")
        for field in skipped:
            assert field not in tc, f"{field} 不应出现在骨架中"

    def test_optional_with_example_keys_rendered(self):
        """Optional[Dict] 有 example_keys 的字段渲染为 {}（json/params）"""
        skeleton = generate_json_skeleton(TestData)
        parsed = json.loads(skeleton)
        tc = parsed["data"][0]["testCase"][0]
        assert tc["json"] == {}
        assert tc["params"] == {}

    def test_validation_is_array_with_empty_object(self):
        """validation 应为 [{}]（非 Optional，default_factory=list）"""
        skeleton = generate_json_skeleton(TestData)
        parsed = json.loads(skeleton)
        tc = parsed["data"][0]["testCase"][0]
        assert tc["validation"] == [{}]

    def test_skeleton_is_valid_json(self):
        """骨架必须是合法 JSON"""
        skeleton = generate_json_skeleton(TestData)
        json.loads(skeleton)  # 不抛异常

    def test_graceful_fallback(self):
        """非 Pydantic 类型传入不崩溃，返回占位"""
        result = generate_json_skeleton(str)  # str 不是 BaseModel
        assert isinstance(result, str)
        json.loads(result)  # 至少是合法 JSON


# ============================================================
# 2. _extract_json_from_thinking — JSON 提取多层降级
# ============================================================

class TestJsonExtraction:
    """Phase 1 步骤 3: 各层降级路径覆盖。"""

    # ---- L1: 直接解析 ----

    def test_l1_pure_json(self):
        """纯 JSON → L1 json.loads 直接命中"""
        result = _extract_json_from_thinking('{"data": []}')
        assert result == {"data": []}

    def test_l1_nested_json(self):
        """嵌套 JSON → L1 直接命中"""
        raw = '{"data": [{"baseInfo": {"api_name": "test"}}]}'
        result = _extract_json_from_thinking(raw)
        assert result["data"][0]["baseInfo"]["api_name"] == "test"

    # ---- L2: json 代码块 ----

    def test_l2_json_fence(self):
        """```json ... ``` → L2 代码块提取"""
        raw = '好的，以下是生成的 JSON：\n```json\n{"data": []}\n```'
        result = _extract_json_from_thinking(raw)
        assert result == {"data": []}

    def test_l2_json_fence_multiline(self):
        """多行 ```json 代码块"""
        raw = '```json\n{\n  "data": [\n    {"case_name": "test"}\n  ]\n}\n```'
        result = _extract_json_from_thinking(raw)
        assert result["data"][0]["case_name"] == "test"

    # ---- L2b: 无语言标记代码块 ----

    def test_l2b_no_lang_fence(self):
        """``` ... ``` 无语言标记 → L2b"""
        raw = '```\n{"data": []}\n```'
        result = _extract_json_from_thinking(raw)
        assert result == {"data": []}

    # ---- L3: find + rfind ----

    def test_l3_find_rfind(self):
        """JSON 混在文本中（无代码块标记）→ L3"""
        raw = '一些前置说明文字 {"data": [{"case_name": "x"}]} 一些后置文字'
        result = _extract_json_from_thinking(raw)
        assert result["data"][0]["case_name"] == "x"

    # ---- 异常 ----

    def test_invalid_fallback(self):
        """完全无法提取 → JSONDecodeError"""
        with pytest.raises(json.JSONDecodeError):
            _extract_json_from_thinking("这不是 JSON")

    def test_empty_string(self):
        """空字符串 → JSONDecodeError"""
        with pytest.raises(json.JSONDecodeError):
            _extract_json_from_thinking("")


# ============================================================
# 3. prepare_repair_context + RepairNeeded — 诊断包构造
# ============================================================

class TestRepairContext:
    """Phase 1 步骤 4: 三种异常类型各测一遍。"""

    def test_validation_error_constructs_yaml(self):
        """ValidationError → failed_yaml 为 YAML 格式 + error_roadmap 精简路径"""
        raw = '{"data": [{"case_name": "test"}]}'
        # 构造一个缺少必填字段的 Pydantic 校验错误
        try:
            TestData.model_validate({"data": [{"case_name": "test"}]})
            pytest.fail("应该抛出 ValidationError")
        except ValidationError as e:
            ctx = prepare_repair_context(raw, e)
        assert "failed_yaml" in ctx
        assert "error_roadmap" in ctx
        assert "raw_text" in ctx
        assert ctx["raw_text"] == raw
        # error_roadmap 应该有路径信息
        assert len(ctx["error_roadmap"]) > 0
        # failed_yaml 应该是 YAML 而非原始 raw_text
        assert "data:" in ctx["failed_yaml"]

    def test_json_decode_error(self):
        """JSONDecodeError → failed_yaml = raw_text + 错误位置"""
        raw = "这不是 JSON { broken"
        try:
            json.loads(raw)
            pytest.fail("应该抛出 JSONDecodeError")
        except json.JSONDecodeError as e:
            ctx = prepare_repair_context(raw, e)
        assert ctx["failed_yaml"] == raw  # JSON 解析失败时直接用原文
        assert "JSON 解析失败" in ctx["error_roadmap"]
        assert "第 1 行" in ctx["error_roadmap"] or "line" in ctx["error_roadmap"].lower()

    def test_generic_exception(self):
        """其他 Exception → failed_yaml = raw_text, error_roadmap = str(e)"""
        raw = "valid json content"
        error = RuntimeError("LLM 调用超时")
        ctx = prepare_repair_context(raw, error)
        assert ctx["failed_yaml"] == raw
        assert ctx["error_roadmap"] == "LLM 调用超时"
        assert ctx["raw_text"] == raw

    def test_repair_needed_exception(self):
        """RepairNeeded 异常携带 repair_ctx"""
        ctx = {"failed_yaml": "yaml", "error_roadmap": "err", "raw_text": "raw"}
        exc = RepairNeeded(ctx)
        assert exc.repair_ctx is ctx
        assert str(exc) == "err"


# ============================================================
# 4. _error_mentions_placeholder / _error_mentions_api — 条件注入判断
# ============================================================

class TestErrorDetection:
    """Phase 4 步骤 12: 关键词匹配覆盖。"""

    # ---- _error_mentions_placeholder ----

    def test_placeholder_detects_get_extract_data(self):
        assert _error_mentions_placeholder(
            "  - [data -> 0 -> testCase -> 0 -> json -> code] ${get_extract_data(code)}"
        )

    def test_placeholder_detects_dollar_brace(self):
        assert _error_mentions_placeholder(
            "  - [data -> 0 -> testCase -> 0 -> params] ${random_plate(1)}"
        )

    def test_placeholder_detects_unknown_function(self):
        assert _error_mentions_placeholder(
            "  - [data -> 0] unknown function 'random_plates'"
        )

    def test_placeholder_detects_chinese_keyword(self):
        assert _error_mentions_placeholder(
            "  - [data -> 0 -> json -> plate] 占位符语法错误"
        )

    def test_placeholder_detects_factory_keyword(self):
        assert _error_mentions_placeholder(
            "  - [data -> 0] factory method not found"
        )

    def test_placeholder_negative(self):
        """纯结构错误不应触发占位符检测"""
        assert not _error_mentions_placeholder(
            "  - [data -> 0] Field required"
        )
        assert not _error_mentions_placeholder(
            "  - [data -> 0 -> testCase] 缺少 baseInfo 字段"
        )

    # ---- _error_mentions_api ----

    def test_api_detects_url(self):
        assert _error_mentions_api(
            "  - [data -> 0 -> baseInfo -> url] Field required"
        )

    def test_api_detects_method(self):
        assert _error_mentions_api(
            "  - [data -> 0 -> baseInfo -> method] Input should be 'get', 'post'"
        )

    def test_api_detects_api_name(self):
        assert _error_mentions_api(
            "  - [data -> 0 -> baseInfo -> api_name] Field required"
        )

    def test_api_detects_baseInfo(self):
        assert _error_mentions_api(
            "  - [data -> 0 -> baseInfo] missing baseInfo"
        )

    def test_api_detects_header(self):
        assert _error_mentions_api(
            "  - [data -> 0 -> baseInfo -> header] KeyError: 'header'"
        )

    def test_api_detects_params_chinese(self):
        assert _error_mentions_api(
            "  - [data -> 0 -> json] 参数缺失"
        )

    def test_api_negative(self):
        """纯占位符错误不应触发 API 检测"""
        assert not _error_mentions_api(
            "  - [data -> 0] ${get_extract_data(code)} 未提取"
        )
        assert not _error_mentions_api(
            "  - [data -> 0] validation 断言字段不存在"
        )

    # ---- 边界：空字符串 ----

    def test_empty_roadmap(self):
        assert not _error_mentions_placeholder("")
        assert not _error_mentions_api("")


# ============================================================
# 5. 集成：RepairNeeded → 条件注入流转
# ============================================================

class TestRepairRoundIntegration:
    """模拟 _run_yaml_rounds 中的修复轮构造逻辑。"""

    def test_structure_only_error_no_injection(self):
        """纯结构错误 → 不注入工厂方法和 API 定义"""
        road = "  - [data -> 0] Field required\n  - [data -> 0 -> testCase] missing"
        assert not _error_mentions_placeholder(road)
        assert not _error_mentions_api(road)

    def test_placeholder_error_injects_factory(self):
        """占位符错误 → 注入工厂方法"""
        road = "  - [data -> 0 -> json -> plate] ${random_plates(1)} 未知函数"
        assert _error_mentions_placeholder(road)
        assert not _error_mentions_api(road)

    def test_api_error_injects_definitions(self):
        """API 匹配错误 → 注入接口定义"""
        road = "  - [data -> 0 -> baseInfo -> url] 接口定义中未找到 /gym/add"
        assert _error_mentions_api(road)

    def test_mixed_error_injects_both(self):
        """占位符+API 混合错误 → 两者都注入"""
        road = (
            "  - [data -> 0 -> baseInfo -> api_name] Field required\n"
            "  - [data -> 0 -> json -> code] ${get_extract_data(xxx)} 未提取\n"
            "  - [data -> 0 -> baseInfo -> url] 接口 /payConfig/update 不存在"
        )
        assert _error_mentions_placeholder(road)
        assert _error_mentions_api(road)
