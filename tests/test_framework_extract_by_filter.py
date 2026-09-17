# -*- coding: utf-8 -*-
"""框架侧加固回归：按名筛选回读（2026-09-11）。

背景见 `changelog/_phase_c_selective_2026-09-09/parking_rule_table_round2_attribution.md` §五。
生成器所有 `preXXXParkCode` 靠 `getAllWithDevices{parkName}` 回读 `$.data[0]`，
而该 `parkName` 过滤**服务端不生效**（传任意名都返回全量）→ 编码取成别人的 →
车场拓扑整片挂错。修复靠两件事，本文件逐一锁死：

  1. `base/apiutil.py` 的 JSONPath 解析器升级为 `jsonpath_ng.ext`
     （基础语法超集，多支持 `[?(@.field=='v')]`；旧的 `jsonpath_ng.parse` 遇到
     过滤器直接抛 JsonPathLexerError，而调用点都吞异常 → 静默「提取不到数据」）。
  2. `specification_yaml` 里 `extract` / `extract_list` 也走 `replace_load`，
     使 `$.data[?(@.parkingPark.parkName=='${get_extract_data(...)}')]` 能把
     上一步回读到的名字注进去。

**必须在框架 venv 里跑**（仓库 venv 没有 jsonpath_ng / allure）：
    C:/Users/damai/PyCharmMiscProject/.venv/Scripts/python.exe -m pytest tests/test_framework_extract_by_filter.py
"""
import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

MISC = Path(r"C:\Users\damai\PyCharmMiscProject")

pytestmark = pytest.mark.skipif(
    not MISC.exists(), reason="框架目录不在本机")


@pytest.fixture(scope="module")
def apiutil():
    """加载框架 `base/apiutil.py`（顺带把框架根加进 sys.path）。"""
    if str(MISC) not in sys.path:
        sys.path.insert(0, str(MISC))
    try:
        from base import apiutil as mod
    except ImportError as e:  # 多半是跑错 venv（缺 allure/jsonpath_ng）
        pytest.skip(f"框架依赖不可用，请在框架 venv 运行: {e}")
    return mod


def _run_fixture(fixture_fn):
    """直接驱动一个（被 pytest 包装过的）fixture 生成器。"""
    gen = getattr(fixture_fn, "__wrapped__", fixture_fn)()
    next(gen)
    return gen


def _load_framework_conftest():
    """按文件路径加载框架 conftest，避免与本仓库同名模块撞车。"""
    spec = importlib.util.spec_from_file_location(
        "_framework_conftest", MISC / "conftest.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# 一份缩微的 `getAllWithDevices` 响应：两条记录，目标车场**不在** data[0]。
# 真实环境就是这个形态（PS3M 排第 2），$[0] 才取错了码。
RESPONSE = {
    "retCode": 1,
    "data": [
        {"parkingPark": {"parkName": "PK-PS2S_mvd05o",
                         "parkCode": "zhyq846724013278630"}},
        {"parkingPark": {"parkName": "PK-PS3M_hfx8hc",
                         "parkCode": "zhyq457861985152284"}},
    ],
}


class TestJsonpathParse:

    def test_plain_syntax_still_works(self, apiutil):
        """老写法（无过滤器）行为不变 —— 全框架在用，不能回归。"""
        m = apiutil.jsonpath_parse("$.data[0].parkingPark.parkCode").find(RESPONSE)
        assert [x.value for x in m] == ["zhyq846724013278630"]

    def test_filter_selects_by_name_not_by_position(self, apiutil):
        """核心修复：按名精确取码，与返回排序无关。"""
        expr = ("$.data[?(@.parkingPark.parkName=='PK-PS3M_hfx8hc')]"
                ".parkingPark.parkCode")
        m = apiutil.jsonpath_parse(expr).find(RESPONSE)
        assert [x.value for x in m] == ["zhyq457861985152284"]

    def test_filter_no_match_returns_empty_not_raise(self, apiutil):
        """名字对不上时应是「匹配为空」（走未提取到数据分支），而不是抛异常。"""
        expr = ("$.data[?(@.parkingPark.parkName=='不存在的名字ZZZ')]"
                ".parkingPark.parkCode")
        assert apiutil.jsonpath_parse(expr).find(RESPONSE) == []

    def test_bare_parser_would_have_failed(self, apiutil):
        """反证：旧解析器对同一表达式是抛异常的 —— 这就是「静默取不到」。"""
        import jsonpath_ng
        expr = "$.data[?(@.parkingPark.parkName=='PK-PS3M_hfx8hc')]"
        with pytest.raises(Exception):
            jsonpath_ng.parse(expr)


class TestExtractReplaceLoad:
    """`extract` 里的 `${}` 必须像请求参数一样被解析。"""

    def test_replace_load_injects_var_into_extract_expression(self, apiutil, monkeypatch):
        from common.debugtilk import DebugTalk

        seen = {}

        def fake_get_extract_data(node_name, *a, **k):
            seen["node_name"] = node_name
            return "PK-PS3M_hfx8hc"

        # staticmethod：替换的是「类上的方法」，加 staticmethod 才不会被当成本例的 self
        monkeypatch.setattr(DebugTalk, "get_extract_data",
                            staticmethod(fake_get_extract_data))

        extract = {"prePS3MParkCode":
                   "$.data[?(@.parkingPark.parkName=='${get_extract_data('prePS3MParkName')}')]"
                   ".parkingPark.parkCode"}
        resolved = apiutil.RequestsBase().replace_load(extract)

        # 1) 变量被取用，且取的是正确的 key
        assert seen["node_name"] == "prePS3MParkName"
        # 2) 表达式里已无 ${}，名字已实打实落在过滤条件里
        assert "${" not in resolved["prePS3MParkCode"]
        assert "PK-PS3M_hfx8hc" in resolved["prePS3MParkCode"]
        # 3) 替换后仍是 dict（原有 jsonpath 提取链依赖 dict 形态）
        assert isinstance(resolved, dict)
        # 4) 端到端：替换后的表达式能取到**正确**那条的码
        m = apiutil.jsonpath_parse(resolved["prePS3MParkCode"]).find(RESPONSE)
        assert [x.value for x in m] == ["zhyq457861985152284"]

    def test_replace_load_is_identity_without_placeholder(self, apiutil):
        """无 ${} 时 round-trip 不改内容 —— 存量 extract 不受影响。"""
        extract = {"k": "$.data[0].parkingPark.parkCode",
                   "re": "token=(\\w+)"}
        assert apiutil.RequestsBase().replace_load(extract) == extract


class TestConftestSnapshot:
    """跑完留存现场：清空前先 copy 一份 extract.last_run.yaml。"""

    def test_snapshot_written_before_clear(self, monkeypatch, tmp_path):
        cf = _load_framework_conftest()
        src = tmp_path / "extract.yaml"
        src.write_text("preP1ParkName: PK-P1_abc123\n", encoding="utf-8")

        cleared = {"done": False}

        def fake_clear():
            cleared["done"] = True
            src.write_text("")

        monkeypatch.setattr(cf, "FILE_PATH", {"extract": str(src)})
        monkeypatch.setattr(cf.read, "clear_yaml_data", fake_clear)

        gen = _run_fixture(cf.clear_extract_data)    # 跑到 yield
        with pytest.raises(StopIteration):
            next(gen)                                # 收尾段

        assert cleared["done"] is True
        snap = tmp_path / "extract.last_run.yaml"
        assert snap.exists()
        assert snap.read_text("utf-8") == "preP1ParkName: PK-P1_abc123\n"

    def test_no_snapshot_when_extract_empty(self, monkeypatch, tmp_path):
        """空文件不留快照，免得下一轮看到的是上一轮的残留。"""
        cf = _load_framework_conftest()
        src = tmp_path / "extract.yaml"
        src.write_text("", encoding="utf-8")

        monkeypatch.setattr(cf, "FILE_PATH", {"extract": str(src)})
        monkeypatch.setattr(cf.read, "clear_yaml_data", lambda: None)

        gen = _run_fixture(cf.clear_extract_data)
        with pytest.raises(StopIteration):
            next(gen)

        assert not (tmp_path / "extract.last_run.yaml").exists()


if __name__ == "__main__":
    os.system(f'"{MISC}/.venv/Scripts/python.exe" -m pytest {__file__} -v')
