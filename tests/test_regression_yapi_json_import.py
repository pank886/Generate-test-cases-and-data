"""回归测试：YApi JSON 导出（api.json）纯代码导入。

覆盖 extract_apis_from_yapi_json：
  1. 顶层分类数组扁平化 + 模块名 + (method,url) 去重
  2. req_query → location="query"
  3. req_body_other JSON Schema → location="body"（required/children）
  4. res_body JSON Schema → return（无 location）
  5. req_headers → header 名→值映射
  6. req_body_form（文件上传）→ location="body"
  7. path 参数留在 URL（has_path_params 由 ApiAnnotationRegistry 处理）
  8. 异常 JSON / 非法结构明确报错
"""

import json
import os
import sys
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ingest_v2 import extract_apis_from_yapi_json


def _api(**kw):
    """构造 YApi JSON 单个接口元素（缺省字段按导出格式补齐）。"""
    path = kw.pop("path", "/sample/list")
    base = {
        "path": path,
        "title": kw.pop("title", "示例接口"),
        "method": kw.pop("method", "POST"),
        "query_path": {"path": path, "params": []},
        "req_headers": [],
        "req_query": [],
        "req_params": [],
        "req_body_other": None,
        "req_body_form": [],
        "req_body_is_json_schema": True,
        "res_body": None,
        "res_body_is_json_schema": True,
        "desc": "<p></p>",
        "_id": "abc",
    }
    base.update(kw)
    return base


def _export(categories, list_items=None):
    """构造 api.json 顶层结构：分类数组或单分类对象。"""
    if list_items is not None:
        return [{"index": 0, "name": categories, "desc": "", "list": list_items}]
    return [{"index": i, "name": n, "desc": "", "list": items}
            for i, (n, items) in enumerate(categories)]


# ============================================================
# 基础：扁平化 / 模块名 / 去重
# ============================================================

class TestYapiJsonBasics:
    def test_flattens_categories_and_module_name(self):
        doc = _export([
            ("坏账管理", [
                _api(path="/badDebt/getList", title="分页查询"),
                _api(path="/badDebt/add", title="新增申请"),
            ]),
            ("账单管理", [
                _api(path="/bill/getPage", title="账单分页"),
            ]),
        ])
        result = extract_apis_from_yapi_json(json.dumps(doc, ensure_ascii=False))
        assert len(result["apis"]) == 3
        assert result["module_name"] == "", "多分类文件无单一模块名（避免误显示为首个分类）"
        urls = {a["url"] for a in result["apis"]}
        assert urls == {"/badDebt/getList", "/badDebt/add", "/bill/getPage"}
        # 每个接口携带真实分类
        cats = {a["annotations"]["category"] for a in result["apis"]}
        assert cats == {"坏账管理", "账单管理"}

    def test_single_category_sets_module_name(self):
        doc = _export([("坏账管理", [_api(path="/badDebt/a"), _api(path="/badDebt/b")])])
        result = extract_apis_from_yapi_json(json.dumps(doc, ensure_ascii=False))
        assert result["module_name"] == "坏账管理", "单分类文件用分类名作为模块名"

    def test_dedup_same_method_url_across_categories(self):
        doc = _export([
            ("分类A", [_api(path="/dup", title="A 版本")]),
            ("分类B", [_api(path="/dup", title="B 版本")]),
        ])
        result = extract_apis_from_yapi_json(json.dumps(doc, ensure_ascii=False))
        assert len(result["apis"]) == 1, "跨分类重复接口应去重"
        assert result["apis"][0]["name"] == "A 版本"

    def test_single_category_object_accepted(self):
        doc = {"name": "坏账管理", "list": [_api(path="/a")]}
        result = extract_apis_from_yapi_json(json.dumps(doc, ensure_ascii=False))
        assert len(result["apis"]) == 1
        assert result["module_name"] == "坏账管理"

    def test_method_upper_and_desc_strip_html(self):
        doc = _export([("分类", [_api(method="get", desc="<p><b>说明</b>接口</p>")])])
        result = extract_apis_from_yapi_json(json.dumps(doc, ensure_ascii=False))
        api = result["apis"][0]
        assert api["method"] == "GET"
        assert api["description"] == "说明接口"
        assert api["description"] != "<p><b>说明</b>接口</p>"


# ============================================================
# query / body / header 分层
# ============================================================

class TestYapiJsonQueryBodyHeader:
    def test_req_query_marked_query(self):
        doc = _export([("分类", [
            _api(
                req_query=[{"name": "billCode", "desc": "账单编号", "required": "1", "value": ""}],
            )
        ])])
        api = extract_apis_from_yapi_json(json.dumps(doc, ensure_ascii=False))["apis"][0]
        assert len(api["body"]) == 1
        f = api["body"][0]
        assert f["name"] == "billCode"
        assert f["location"] == "query"
        assert f["required"] is True  # YApi "1" → True

    def test_req_body_other_marked_body_with_required(self):
        schema = json.dumps({
            "type": "object",
            "properties": {
                "mainBodyCode": {"type": "string", "description": "主体编码"},
                "billCodes": {"type": "array", "items": {"type": "string"}, "description": "账单编号列表"},
            },
            "required": ["billCodes"],
            "description": "新增参数",
        }, ensure_ascii=False)
        doc = _export([("分类", [_api(req_body_other=schema)])])
        api = extract_apis_from_yapi_json(json.dumps(doc, ensure_ascii=False))["apis"][0]
        names = [f["name"] for f in api["body"]]
        assert names == ["mainBodyCode", "billCodes"]
        assert all(f["location"] == "body" for f in api["body"])
        by_name = {f["name"]: f for f in api["body"]}
        assert by_name["billCodes"]["required"] is True
        assert by_name["mainBodyCode"]["required"] is False
        assert by_name["mainBodyCode"]["desc"] == "主体编码"
        assert by_name["billCodes"]["type"] == "array"
        assert "children" not in by_name["billCodes"], "数组元素为原始类型时不生成 children"

    def test_res_body_return_no_location(self):
        schema = json.dumps({
            "type": "object",
            "properties": {
                "retCode": {"type": "integer", "description": "返回码"},
                "data": {"type": "object", "properties": {
                    "total": {"type": "integer", "description": "总数"},
                }},
            },
            "required": ["retCode"],
        }, ensure_ascii=False)
        doc = _export([("分类", [_api(res_body=schema)])])
        api = extract_apis_from_yapi_json(json.dumps(doc, ensure_ascii=False))["apis"][0]
        assert len(api["return"]) == 2
        data = {f["name"]: f for f in api["return"]}["data"]
        assert data["type"] == "object"
        assert data["children"][0]["name"] == "total"
        # return 无 location（与契约 response 语义一致）
        assert all("location" not in f for f in api["return"])

    def test_array_of_objects_children(self):
        schema = json.dumps({
            "type": "object",
            "properties": {
                "data": {"type": "array", "items": {"type": "object", "properties": {
                    "enterpriseCode": {"type": "string", "description": "主体编码"},
                    "enterpriseName": {"type": "string", "description": "主体名称"},
                }}},
            },
        }, ensure_ascii=False)
        doc = _export([("分类", [_api(res_body=schema)])])
        api = extract_apis_from_yapi_json(json.dumps(doc, ensure_ascii=False))["apis"][0]
        data = api["return"][0]
        assert data["type"] == "array"
        assert [c["name"] for c in data["children"]] == ["enterpriseCode", "enterpriseName"]
        assert data["children"][0]["desc"] == "主体编码"

    def test_req_headers_to_mapping(self):
        doc = _export([("分类", [
            _api(req_headers=[
                {"name": "Content-Type", "value": "application/json", "example": "application/json", "required": "1"},
                {"name": "Authorization", "value": "Bearer x", "example": ""},
            ])
        ])])
        api = extract_apis_from_yapi_json(json.dumps(doc, ensure_ascii=False))["apis"][0]
        assert api["header"] == {"Content-Type": "application/json", "Authorization": "Bearer x"}

    def test_req_body_form_file_marked_body(self):
        doc = _export([("分类", [
            _api(req_body_form=[{"name": "file", "type": "file", "desc": "excel文件", "required": "0"}])
        ])])
        api = extract_apis_from_yapi_json(json.dumps(doc, ensure_ascii=False))["apis"][0]
        assert len(api["body"]) == 1
        f = api["body"][0]
        assert f["name"] == "file"
        assert f["type"] == "file"
        assert f["location"] == "body"
        assert f["required"] is False


# ============================================================
# 路径参数 / 异常
# ============================================================

class TestYapiJsonEdgeCases:
    def test_path_param_stays_in_url(self):
        doc = _export([("分类", [
            _api(path="/badDebt/getByCode/{code}", method="GET", title="详情",
                 req_params=[{"name": "code", "desc": "申请单号"}])
        ])])
        api = extract_apis_from_yapi_json(json.dumps(doc, ensure_ascii=False))["apis"][0]
        assert api["url"] == "/badDebt/getByCode/{code}"
        assert api["body"] == [], "path 参数不写入 body（由 has_path_params 标注处理）"

    def test_invalid_json_raises(self):
        with pytest.raises(ValueError, match="JSON 解析失败"):
            extract_apis_from_yapi_json("this is not json {{{")

    def test_invalid_structure_raises(self):
        with pytest.raises(ValueError, match="不支持的 JSON 结构"):
            extract_apis_from_yapi_json('{"a": 1}')

    def test_empty_file_returns_no_apis(self):
        result = extract_apis_from_yapi_json("[]")
        assert result["apis"] == []
        assert result["module_name"] == ""

    def test_category_annotation(self):
        doc = _export([("坏账管理", [_api(path="/badDebt/a")])])
        api = extract_apis_from_yapi_json(json.dumps(doc, ensure_ascii=False))["apis"][0]
        assert api["annotations"].get("category") == "坏账管理"

    def test_location_survives_commit_normalization(self):
        """commit_api_docs 入库前跑 _coerce_api_format，location 不能丢。"""
        from ingest.api_parser import _coerce_api_format
        doc = _export([("分类", [
            _api(
                req_query=[{"name": "billCode", "required": "1", "desc": "账单编号"}],
                req_body_other=json.dumps({
                    "type": "object",
                    "properties": {"reason": {"type": "string", "description": "原因"}},
                    "required": ["reason"],
                }, ensure_ascii=False),
            )
        ])])
        api = extract_apis_from_yapi_json(json.dumps(doc, ensure_ascii=False))["apis"][0]
        normalized = _coerce_api_format(api)
        locs = {f["name"]: f.get("location") for f in normalized["body"]}
        assert locs == {"billCode": "query", "reason": "body"}, \
            f"location 应在归一化后保留，实际: {locs}"
