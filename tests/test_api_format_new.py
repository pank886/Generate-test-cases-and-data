"""2026-08-13 API 提取/存储新格式测试。

新结构：header 名→值映射 + body/return 六字段数组 {name,type,required,default,desc,value}。
value 只取自请求/返回示例代码块，不混入 default。
覆盖：纯 Markdown 解析、示例捕获、必填默认、六字段完整性、字段合并、格式归一化。
"""

import json
import os
import re
import sys
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ingest.api_parser import (
    extract_apis_from_yapi_md,
    _coerce_api_format,
    _merge_api_defs,
    _extract_valid_api_paths,
    _is_required,
)
from ingest.chunking import _build_api_search_text
from agent_components.retrievers import _format_params


GYM_FIXTURE = """\
# 新增健身房设施接口文档

## 接口说明
- **接口名称**：新增创建
- **接口路径**：`POST /gymFacility/add`
- **接口状态**：已完成
- **创建人**：smj

---

## 请求信息

### Headers
| 参数名称 | 参数值 | 是否必须 | 示例 | 备注 |
|----------|--------|----------|------|------|
| Content-Type | application/json | 是 | application/json | - |
| yp-app-code | test | 是 | test | 应用编码 |

### Body 参数
| 名称 | 类型 | 是否必须 | 默认值 | 备注 |
|------|------|----------|--------|------|
| id | string | 非必须 | - | 主键 |
| code | string | 非必须 | - | - |
| facName | string | **必须** | - | 设施名称 |

### 请求示例
```json
{
    "id": "faci_001",
    "code": "GYM-2024-001",
    "facName": "智能跑步机"
}
```

---

## 返回数据

### 返回参数说明
| 名称 | 类型 | 是否必须 | 默认值 | 备注 |
|------|------|----------|--------|------|
| retCode | integer | 非必须 | - | - |
| msg | string | 非必须 | - | - |

### 返回示例
```json
{
    "retCode": 0,
    "msg": "操作成功"
}
```

# 修改健身房设施接口文档

## 接口说明
- **接口名称**：修改
- **接口路径**：`POST /gymFacility/update`
- **接口状态**：已完成

## 请求信息

### Body 参数
| 名称 | 类型 | 是否必须 | 默认值 | 备注 |
|------|------|----------|--------|------|
| id | string | 非必须 | - | 主键 |
| facName | string | **必须** | - | 设施名称 |

### 请求示例
```json
{
    "id": "faci_001",
    "facName": "智能跑步机（升级版）"
}
```

## 返回数据

### 返回参数说明
| 名称 | 类型 | 是否必须 | 默认值 | 备注 |
|------|------|----------|--------|------|
| retCode | integer | 非必须 | - | - |
"""


class TestPureMarkdownExtraction:
    """纯 Markdown 接口文档（健身房格式）解析。"""

    def test_api_count_and_names(self):
        r = extract_apis_from_yapi_md(GYM_FIXTURE)
        assert len(r["apis"]) == 2, f"期望2个API，实际{len(r['apis'])}"
        assert r["apis"][0]["name"] == "新增创建"
        assert r["apis"][1]["name"] == "修改"

    def test_method_and_url(self):
        r = extract_apis_from_yapi_md(GYM_FIXTURE)
        a = r["apis"][0]
        assert a["method"] == "POST"
        assert a["url"] == "/gymFacility/add"

    def test_header_is_name_value_map(self):
        r = extract_apis_from_yapi_md(GYM_FIXTURE)
        a = r["apis"][0]
        assert a["header"] == {
            "Content-Type": "application/json",
            "yp-app-code": "test",
        }

    def test_body_fields_none_lost(self):
        """body 字段集合 = 参数表全字段（一个不少）。"""
        r = extract_apis_from_yapi_md(GYM_FIXTURE)
        a = r["apis"][0]
        names = [f["name"] for f in a["body"]]
        assert names == ["id", "code", "facName"], f"字段丢失/乱序: {names}"

    def test_value_from_example(self):
        """value 来自请求示例，示例没有的字段为 ""。"""
        r = extract_apis_from_yapi_md(GYM_FIXTURE)
        a = r["apis"][0]
        by_name = {f["name"]: f for f in a["body"]}
        assert by_name["id"]["value"] == "faci_001"
        assert by_name["code"]["value"] == "GYM-2024-001"
        assert by_name["facName"]["value"] == "智能跑步机"

    def test_required_flag(self):
        """facName 为 **必须** → required=True；非必须 → False。"""
        r = extract_apis_from_yapi_md(GYM_FIXTURE)
        a = r["apis"][0]
        by_name = {f["name"]: f for f in a["body"]}
        assert by_name["facName"]["required"] is True
        assert by_name["id"]["required"] is False
        assert by_name["code"]["required"] is False

    def test_return_values(self):
        """返回示例对齐 return 的 value。"""
        r = extract_apis_from_yapi_md(GYM_FIXTURE)
        a = r["apis"][0]
        by_name = {f["name"]: f for f in a["return"]}
        assert by_name["retCode"]["value"] == "0"
        assert by_name["msg"]["value"] == "操作成功"

    def test_six_field_complete(self):
        """每个 body/return 元素恰好含 6 个字段。"""
        six = {"name", "type", "required", "default", "desc", "value"}
        r = extract_apis_from_yapi_md(GYM_FIXTURE)
        for a in r["apis"]:
            for f in a["body"] + a["return"]:
                assert set(f) == six, f"字段集合异常: {set(f)}"

    def test_second_api_without_headers(self):
        """无 Headers 小节的 API → header 为空映射，body 仍正确。"""
        r = extract_apis_from_yapi_md(GYM_FIXTURE)
        a = r["apis"][1]
        assert a["header"] == {}
        names = [f["name"] for f in a["body"]]
        assert names == ["id", "facName"]
        by_name = {f["name"]: f for f in a["body"]}
        assert by_name["facName"]["value"] == "智能跑步机（升级版）"


class TestValidApiPaths:
    """白名单提取兼容两种路径写法。"""

    def test_gym_md_paths(self):
        paths = _extract_valid_api_paths(GYM_FIXTURE)
        assert ("POST", "/gymFacility/add") in paths
        assert ("POST", "/gymFacility/update") in paths
        # 不应带反引号残留
        assert all("`" not in u for _, u in paths)

    def test_yapi_paths(self):
        text = ("## 修改电表\n**Path：** /electricMeter/update\n"
                "**Method：** POST\n\n## 删除电表\n**Path：** /electricMeter/delete\n"
                "**Method：** DELETE\n")
        paths = _extract_valid_api_paths(text)
        assert ("POST", "/electricMeter/update") in paths
        assert ("DELETE", "/electricMeter/delete") in paths


class TestCoerceApiFormat:
    """旧格式 → 新结构归一化（幂等）。"""

    def test_old_keys_renamed(self):
        old = {
            "name": "登录", "url": "/api/login", "method": "POST",
            "description": "登录",
            "headers": [{"name": "Content-Type", "value": "application/json"}],
            "parameters": [{"name": "username", "type": "string", "required": True,
                            "description": "用户名", "default": ""}],
            "returns": [{"name": "token", "type": "string", "required": False}],
        }
        out = _coerce_api_format(old)
        assert out["header"] == {"Content-Type": "application/json"}
        assert out["body"][0]["name"] == "username"
        assert out["body"][0]["desc"] == "用户名"
        assert out["return"][0]["name"] == "token"
        assert "parameters" not in out and "returns" not in out and "headers" not in out

    def test_old_dict_values(self):
        """旧格式 {name: type} 字典 → 六字段数组。"""
        old = {"method": "GET", "url": "/x", "name": "x", "description": "x",
               "parameters": {"id": "integer"}, "returns": {}}
        out = _coerce_api_format(old)
        assert out["body"] == [{"name": "id", "type": "integer", "required": False,
                                "default": "", "desc": "", "value": ""}]
        assert out["return"] == []

    def test_new_format_unchanged(self):
        """已是新结构的 dict 原样通过（幂等）。"""
        new = {"name": "x", "url": "/x", "method": "GET", "description": "x",
               "header": {"A": "1"},
               "body": [{"name": "id", "type": "string", "required": False,
                         "default": "", "desc": "", "value": "1"}],
               "return": []}
        out = _coerce_api_format(new)
        assert out == new


class TestMergeApiDefsNew:
    """_merge_api_defs 新结构：body/return 并集合并。"""

    def test_body_union(self):
        existing = {"method": "GET", "url": "/api/user",
                    "body": [{"name": "id", "type": "int"}],
                    "header": {"A": "1"},
                    "description": "old"}
        incoming = {"method": "GET", "url": "/api/user",
                    "body": [{"name": "name", "type": "str"}],
                    "header": {"B": "2"},
                    "description": "new longer description"}
        merged = _merge_api_defs(existing, incoming)
        names = {f["name"] for f in merged["body"]}
        assert {"id", "name"} <= names
        assert merged["header"] == {"A": "1", "B": "2"}
        assert merged["description"] == "new longer description"


class TestSearchTextAndRender:
    """检索文本与喂 LLM 渲染用新 key。"""

    def test_build_api_search_text_new_keys(self):
        api = {
            "name": "登录", "url": "/api/login", "method": "POST",
            "description": "用户登录",
            "header": {"Content-Type": "application/json"},
            "body": [{"name": "username", "type": "string", "required": True,
                      "default": "", "desc": "用户名", "value": "admin"}],
            "return": [{"name": "token", "type": "string", "required": False,
                        "default": "", "desc": "", "value": ""}],
            "annotations": {},
        }
        text = _build_api_search_text(api)
        assert "POST /api/login 登录。用户登录。" in text
        assert "username" in text and "必填" in text
        assert "token" in text
        assert "Content-Type" not in text  # header 不进检索文本（保持简洁）

    def test_format_params_value_and_desc(self):
        fields = [{"name": "id", "type": "string", "required": False,
                   "default": "", "desc": "主键", "value": "faci_001"}]
        out = _format_params(fields, indent=0)
        assert "id(string, 可选): faci_001 (主键)" in out

    def test_format_params_no_value_no_desc(self):
        fields = [{"name": "id", "type": "string", "required": False,
                   "default": "", "desc": "", "value": ""}]
        out = _format_params(fields, indent=0)
        assert out == "id(string, 可选)"


class TestIsRequiredDefaults:
    """必填判定：未注明默认非必填；否定形式全部非必填。"""

    def test_unmarked_is_optional(self):
        assert _is_required("") is False
        assert _is_required(None) is False
        assert _is_required("-") is False

    def test_markdown_bold_stripped(self):
        assert _is_required("**必须**") is True
        assert _is_required("**非必须**") is False


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
