# -*- coding: utf-8 -*-
"""智慧停车数据层事实回归（2026-09-10 Round 0 实证落地的护栏）。

覆盖 `changelog/2026-09-10_parking_phase_c_rerun_two_rounds.md` §十 写入的三处关键事实：
  1. `parkingPark/saveOrUpdate` 新建时四类 parkCode 示例值必须为空串（历史第一大失败根因）；
  2. `parkingDevice/add` 厂家/型号字典值必须非空且成对，且同时文档化 parkCode 与 placeCode；
  3. 删除三端点的请求体形状（裸数组 / 逗号串）描述就位。
外加两条护栏：
  4. 上下文文件只含规则、**不得含具体取值示例**（用户约束：prompt 中不增加示例）；
  5. SQLite `documents.api_parameters` 存档与模块 `api_defs.json` 保持一致（09-09 惯例）。

模块目录在仓库外（PyCharmMiscProject），缺失时整文件 skip。
"""
import json
import sqlite3
from pathlib import Path

import pytest

MODULE = Path(r"C:\Users\damai\PyCharmMiscProject\testcase\园区基线\智慧停车")
API_DEFS = MODULE / "api_defs.json"
CONTEXT = MODULE / "phase_c_user_context.md"
DB = Path(__file__).resolve().parents[1] / "data" / "app.db"

pytestmark = pytest.mark.skipif(
    not API_DEFS.exists(), reason="智慧停车模块目录不在本机，跳过数据层回归"
)


@pytest.fixture(scope="module")
def api_defs():
    return json.loads(API_DEFS.read_text(encoding="utf-8"))


def _entry(api_defs, suffix):
    for a in api_defs:
        if a.get("url", "").endswith(suffix):
            return a
    pytest.fail(f"api_defs.json 缺少端点 {suffix}")


def _field(entry, name):
    for b in (entry.get("body") or []):
        if b.get("name") == name:
            return b
    pytest.fail(f"{entry['url']} 缺少字段 {name}")


# ---------- 1. 新建车场 parkCode 必须空串 ----------

@pytest.mark.parametrize("field", [
    "parkingPark.parkCode",
    "carInConfig.parkCode",
    "paymentConfig.parkCode",
    "specialAccessConfigList.parkCode",
])
def test_create_park_example_code_is_empty(api_defs, field):
    """示例值必须是空串——服务端生成编码；非空会得到「请求失败」。"""
    assert _field(_entry(api_defs, "parkingPark/saveOrUpdate"), field)["value"] == ""


def test_create_park_code_desc_states_empty_rule(api_defs):
    """描述须点明「新建必须传空串」，否则生成器仍会照抄旧习惯。"""
    desc = _field(_entry(api_defs, "parkingPark/saveOrUpdate"), "parkingPark.parkCode")["desc"]
    assert "空串" in desc


# ---------- 2. 设备字典值与车场字段语义 ----------

@pytest.mark.parametrize("field", [
    "parkingDeviceManufacturerCode",
    "parkingDeviceManufacturerName",
    "parkingDeviceModelCode",
    "parkingDeviceModelName",
])
def test_device_dictionary_values_are_populated(api_defs, field):
    """真实字典值必须非空，否则生成器会自造编码（历史 91010/91110 根因）。"""
    assert _field(_entry(api_defs, "parkingDevice/add"), field)["value"] != ""


def test_device_documents_both_park_and_place_fields(api_defs):
    """parkCode=绑定车场、placeCode=设备位置，二者语义不同须都在文档里。"""
    entry = _entry(api_defs, "parkingDevice/add")
    names = {b["name"] for b in entry["body"]}
    assert {"parkCode", "parkName", "placeCode", "placeName"} <= names


def test_device_place_desc_distinguishes_from_park_binding(api_defs):
    """placeCode 描述不得再写成「车场编码」——那正是把绑定写丢的误导源。"""
    desc = _field(_entry(api_defs, "parkingDevice/add"), "placeCode")["desc"]
    assert "设备位置" in desc
    assert "parkCode" in desc


# ---------- 3. 删除形状 ----------

@pytest.mark.parametrize("suffix", ["parkingPark/deleteByCodes", "parkingPaymentConfig/delete"])
def test_delete_bulk_endpoints_declare_bare_array(api_defs, suffix):
    """车场/收费规则删除的请求体是裸数组，描述须点明，否则生成 {"codes":[…]}。"""
    desc = _field(_entry(api_defs, suffix), "codes")["desc"]
    assert "裸数组" in desc


def test_device_delete_declares_string_shape(api_defs):
    """设备删除的 codes 是逗号分隔**字符串**（与上面两个端点不同的形状）。"""
    field = _field(_entry(api_defs, "parkingDevice/deleteByCodes"), "codes")
    assert field["type"] == "string"
    assert "逗号" in field["desc"] or "字符串" in field["desc"]


# ---------- 4. 上下文文件：只含规则，不含取值示例 ----------

def test_context_has_key_rules():
    text = CONTEXT.read_text(encoding="utf-8")
    for marker in ("空串", "回读", "删除", "不建立车场绑定"):
        assert marker in text, f"上下文缺少规则要点：{marker}"


@pytest.mark.parametrize("leak", ["zhyq", "PARK001", "100061", "DH-IPC", "大华"])
def test_context_contains_no_concrete_examples(leak):
    """用户约束：prompt 中不增加示例，接口事实一律维护在 api_defs.json。"""
    assert leak not in CONTEXT.read_text(encoding="utf-8"), f"上下文泄漏了取值示例：{leak}"


# ---------- 5. 存档同步 ----------

@pytest.mark.skipif(not DB.exists(), reason="data/app.db 不存在")
@pytest.mark.parametrize("suffix,field", [
    ("parkingPark/saveOrUpdate", "parkingPark.parkCode"),
    ("parkingDevice/add", "parkingDeviceManufacturerCode"),
    ("parkingDevice/add", "parkingDeviceModelCode"),
])
def test_db_archive_matches_api_defs(api_defs, suffix, field):
    want = _field(_entry(api_defs, suffix), field)["value"]
    con = sqlite3.connect(DB)
    try:
        rows = con.execute(
            "select api_parameters from documents where doc_type='api' and api_url like ?",
            (f"%{suffix}",)).fetchall()
    finally:
        con.close()
    assert rows, f"存档中无 {suffix}"
    for (params,) in rows:
        got = {b["name"]: b.get("value") for b in json.loads(params or "[]")}
        assert got.get(field) == want, f"{suffix}::{field} 存档与文件不一致"


# ---------- 6. getAllWithDevices 返回嵌套（2026-09-10 Round 1 归因） ----------

def test_get_all_with_devices_return_documents_nesting(api_defs):
    """return 必须表出 data 元素的嵌套层级，否则生成器会写成 $.data[0].parkCode。

    Round 1 实测：data 仅声明为空 array、无嵌套描述 → 62 处回读路径少一层 parkingPark
    → 83 次调用带占位串级联失败。
    """
    ret = _entry(api_defs, "parkingPark/getAllWithDevices")["return"]
    names = [b["name"] for b in ret]
    for required in ("parkingPark", "parkCode", "carInDeviceList", "carOutDeviceList"):
        assert required in names, f"return 缺少嵌套字段 {required}"


def test_get_all_with_devices_park_code_desc_states_full_path(api_defs):
    """parkCode 的 desc 必须写明完整下钻路径，生成器据此产出 jsonpath。"""
    ret = _entry(api_defs, "parkingPark/getAllWithDevices")["return"]
    park_code = next(b for b in ret if b["name"] == "parkCode")
    assert "parkingPark" in park_code["desc"]


def test_get_all_with_devices_data_desc_mentions_element_shape(api_defs):
    """data 的 desc 须点明元素结构，并记录「精确命中在 data[0]」这一实测事实。"""
    ret = _entry(api_defs, "parkingPark/getAllWithDevices")["return"]
    data = next(b for b in ret if b["name"] == "data")
    assert "parkingPark" in data["desc"]
    assert "data[0]" in data["desc"]


@pytest.mark.skipif(not DB.exists(), reason="data/app.db 不存在")
def test_db_archive_return_matches_api_defs(api_defs):
    """Phase C 走 SQL 读 api_returns（_resolve_api_defs），存档必须与文件一致。"""
    want = [b["name"] for b in
            _entry(api_defs, "parkingPark/getAllWithDevices")["return"]]
    con = sqlite3.connect(DB)
    try:
        rows = con.execute(
            "select api_returns from documents where doc_type='api' and api_url like ?",
            ("%parkingPark/getAllWithDevices%",)).fetchall()
    finally:
        con.close()
    assert rows, "存档中无 getAllWithDevices"
    for (ret,) in rows:
        got = [b["name"] for b in json.loads(ret or "[]")]
        assert got == want, "api_returns 存档与 api_defs.json 不一致"

# ---------- 7. Round 3：删除形状 / 编码回读 / 列表结构（2026-09-10 §十五） ----------

def _ret_field(entry, name):
    for b in (entry.get("return") or []):
        if b.get("name") == name:
            return b
    pytest.fail(f"{entry['url']} 的 return 缺少字段 {name}")


@pytest.mark.parametrize("suffix", ["carWhiteBlack/delete", "parkingCouponIssue/deleteByCodes"])
def test_round3_delete_endpoints_declare_bare_array(api_defs, suffix):
    """探针实证：这两个删除端点也只接受裸数组，对象壳一律「参数类型错误」。

    证据 `changelog/_phase_c_rerun_2026-09-10/round3_probe_delete_shapes.json`：
    obj_str / obj_list / obj_csv 三种壳全被拒，仅 bare_list 通过。
    """
    desc = _field(_entry(api_defs, suffix), "codes")["desc"]
    assert "裸数组" in desc


@pytest.mark.parametrize("suffix,field", [
    ("parkingPark/saveOrUpdate", "parkingPark.bindPayCode"),
    ("parkingPaymentConfig/update", "code"),
    ("carWhiteBlack/update", "code"),
    ("parkingCouponIssue/update", "code"),
])
def test_round3_code_fields_require_readback(api_defs, suffix, field):
    """编码来源必须回读，不能填名称或自造值（Round 2 的 R4 主因）。"""
    desc = _field(_entry(api_defs, suffix), field)["desc"]
    assert "回读" in desc


def test_round3_device_add_declares_no_code_in_response(api_defs):
    """设备添加的响应不回传编码，必须声明，否则生成器会去 extract 一个不存在的字段。"""
    desc = _ret_field(_entry(api_defs, "parkingDevice/add"), "data")["desc"]
    assert "不回传" in desc
    assert "回读" in desc


def test_round3_device_page_declares_list_element_shape(api_defs):
    """设备分页须表出 data.list[] 元素结构（Round 2 的 R5：集合被当标量断言）。"""
    names = [b["name"] for b in _entry(api_defs, "parkingDevice/getPage")["return"]]
    for required in ("data.list", "data.list[].code", "data.list[].name", "data.total"):
        assert required in names, f"return 缺少 {required}"


def test_round3_coupon_list_data_desc_states_element_access(api_defs):
    """优惠券列表 data 是数组，desc 须点明须带下标访问元素。"""
    desc = _ret_field(_entry(api_defs, "parkingCouponIssue/getList"), "data")["desc"]
    assert "下标" in desc


@pytest.mark.skipif(not DB.exists(), reason="data/app.db 不存在")
@pytest.mark.parametrize("suffix,field", [
    ("carWhiteBlack/delete", "codes"),
    ("parkingCouponIssue/deleteByCodes", "codes"),
    ("parkingPaymentConfig/update", "code"),
])
def test_round3_db_archive_matches_api_defs(api_defs, suffix, field):
    """Phase C 走 SQL 读，双写必须一致（Round 2 踩过单边写入的坑）。"""
    want = _field(_entry(api_defs, suffix), field)["desc"]
    con = sqlite3.connect(DB)
    try:
        rows = con.execute(
            "select api_parameters from documents where doc_type='api' and api_url like ?",
            (f"%{suffix}",)).fetchall()
    finally:
        con.close()
    assert rows, f"存档中无 {suffix}"
    for (params,) in rows:
        got = {b["name"]: b.get("desc") for b in json.loads(params or "[]")}
        assert got.get(field) == want, f"{suffix}::{field} 存档与文件不一致"


@pytest.mark.skipif(not DB.exists(), reason="data/app.db 不存在")
def test_round3_db_archive_device_page_return_matches(api_defs):
    want = [b["name"] for b in _entry(api_defs, "parkingDevice/getPage")["return"]]
    con = sqlite3.connect(DB)
    try:
        rows = con.execute(
            "select api_returns from documents where doc_type='api' and api_url like ?",
            ("%parkingDevice/getPage%",)).fetchall()
    finally:
        con.close()
    assert rows, "存档中无 parkingDevice/getPage"
    for (ret,) in rows:
        assert [b["name"] for b in json.loads(ret or "[]")] == want

# ---------- 8. Round 3：规则层（phase_c_user_context.md R8–R12） ----------

@pytest.mark.parametrize("marker,rule", [
    ("HTTP 400", "R8 业务失败状态码口径"),
    ("`data` 字段", "R9 失败原因位置"),
    ("禁止", "R10 提取键命名"),
    ("下标", "R11 断言集合元素"),
    ("臆造", "R12 负向用例期望来源"),
])
def test_context_has_round3_rules(marker, rule):
    assert marker in CONTEXT.read_text(encoding="utf-8"), f"上下文缺少 {rule}（找 {marker!r}）"


def test_context_still_forbids_improvising_error_text():
    """R12 必须明确「不得臆造」，且不得反过来给出具体文案（否则自相矛盾）。"""
    text = CONTEXT.read_text(encoding="utf-8")
    assert "臆造" in text
