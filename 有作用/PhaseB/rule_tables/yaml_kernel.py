# -*- coding: utf-8 -*-
"""判定表 YAML 确定性生成内核（替代 `yaml_gen` 的 LLM 调用）。

三阶段（与 Phase C 布局一致）：
  setup_data/setup_<slug>__<编号>.yaml  前置**按「全局 / 每组 / 每例」各一个文件**
  test_<title_en>/test_data.yaml        每用例：触发动作 + 断言（车辆已在 setup 建好）
  setup_data/teardown_<slug>.yaml       统一清理：车辆记录 → 设备 → 车场 → 收费规则

前置拆成三级（2026-09-15）是为了 Allure 里**每条用例只显示自己的前置**：
框架侧 `allure_pytest` 的 fixture 容器 children = 所有引用它的用例，所以要按用例分
fixture，就得先把块分到「全局 / 每组 / 每例」三级（见 `build_setup_parts`）。
三级并集恒等于 `build_setup` 的全部块（有测试锁定）。

**确定性**：全部步骤由 `parking_rule_map.yaml` + xlsx 推导，不经 LLM。
**不臆造事实**：URL / 字段 / 断言形状全部逐字对实证文件，来源见各函数注释。
"""
import copy
import datetime
import os

import yaml

from rule_tables import registry as reg

SLUG = "parking_rule_table"

# 门类动作 token → 断言列的 scope
_GATE_SCOPES = {"入主": "main_in", "出主": "main_out",
                "入子": "sub_in", "出子": "sub_out"}


# ---------------------------------------------------------------------------
# 前置编号（Sheet2 编号 / fixture 名 / setup 文件名三者同源）
# ---------------------------------------------------------------------------

PRE_ID_GLOBAL = "PRE-RT-001"

# Sheet1「fixture等级」列的取值 = 告诉 `py_export` 按编号逐个挂 fixture。
# 该列此前从未被消费方读取（`py_export` 恒发 class 级单 fixture）；别的产品
# （SmartParking / 智慧用电）那列仍是 "class"/"danyuan"，故其产物逐字不变。
FIXTURE_LEVEL_PER_PRE = "逐前置"


def pre_id_group(group_id: str) -> str:
    """车场组前置编号：`PRE-RT-G-P1`。"""
    return f"PRE-RT-G-{group_id}"


def pre_id_case(case) -> str:
    """用例自有前置编号：`PRE-RT-C-T1_01`（`case_key` 与车牌键同源）。"""
    return f"PRE-RT-C-{case_key(case)}"


def pre_id_slug(pre_id: str) -> str:
    """编号 → 文件名/fixture 名里用的 slug：`PRE-RT-G-P1` → `pre_rt_g_p1`。

    **两侧必须逐字一致**：本模块用它写 `setup_<SLUG>__<slug>.yaml`，
    `py_export` 用它生成 `setup_<class_slug>__<slug>()` fixture 与同名文件引用。
    转换规则要改就两边一起改，回归锁见
    `tests/test_rule_table_fixture_split.py::TestNaming::test_py_export_agrees`。
    """
    return pre_id.lower().replace("-", "_")


def setup_file_name(pre_id: str) -> str:
    """`setup_parking_rule_table__pre_rt_g_p1.yaml`（fixture 名 == 文件名主干）。"""
    return f"setup_{SLUG}__{pre_id_slug(pre_id)}"


# ---------------------------------------------------------------------------
# 车场实体（按依赖顺序：父先于子；PO 先于 P4，因 P4 要引用 PO 的 parkCode）
# ---------------------------------------------------------------------------

def park_entities() -> list[dict]:
    """返回 [{eid, group, role, parent}]，eid 唯一（主+子分组拆成两个实体）。"""
    out = [{"eid": "PO", "group": "PO", "role": "main", "parent": None}]
    for g in reg.get_matchable_groups():
        roles = [p.get("role") for p in (g.get("parks") or [])]
        if roles == ["sub"]:
            parent = (g["parks"][0].get("parent") or "P1")
            out.append({"eid": g["id"], "group": g["id"], "role": "sub",
                        "parent": parent})
        elif "sub" in roles:
            out.append({"eid": f"{g['id']}M", "group": g["id"], "role": "main",
                        "parent": None})
            out.append({"eid": f"{g['id']}S", "group": g["id"], "role": "sub",
                        "parent": f"{g['id']}M"})
        else:
            out.append({"eid": g["id"], "group": g["id"], "role": "main",
                        "parent": None})
    return out


def groups_in_scope(plan) -> set:
    """本批用例实际用到的分组，**并上它们引用的上游车场**。

    用例只匹配得到 ``matchable`` 的分组；但如 P4 的 carInConfig
    `otherParkWhiteAccessParks` 需要 PO 的 parkCode —— PO 本身不匹配任何用例，
    若只按 case.group_id 收集会漏建，导致「其他车场」用例失效（不报错）。
    """
    used = {c.group_id for c in plan.active_cases}
    deps = set()
    for gid in used:
        for p in (reg.get_group(gid) or {}).get("parks") or []:
            for group_ids in (p.get("other_parks") or {}).values():
                deps.update(group_ids)
    return used | deps


def needs_pay(plan) -> bool:
    for c in plan.active_cases:
        g = reg.get_group(c.group_id) or {}
        if any(p.get("need_pay") for p in (g.get("parks") or [])):
            return True
    return False


# ---------------------------------------------------------------------------
# 提取键命名
# ---------------------------------------------------------------------------

def k_park_name(eid): return f"pre{eid}ParkName"
def k_park_code(eid): return f"pre{eid}ParkCode"
def k_dev_in(eid):    return f"pre{eid}InDeviceCode"
def k_dev_out(eid):   return f"pre{eid}OutDeviceCode"


def case_key(case) -> str:
    """表1-01 → T1_01。"""
    return case.case_id.replace("表", "T").replace("-", "_")


def k_car_number(case, car) -> str:
    """car_a → preCarT1_01ANumber。"""
    return f"preCar{case_key(case)}{car[-1].upper()}Number"


def k_car_record(case, car, kind) -> str:
    suffix = "WhiteCode" if kind == "白名单" else "MonthCode"
    return f"preCar{case_key(case)}{car[-1].upper()}{suffix}"


# ---------------------------------------------------------------------------
# 步骤构造原语
# ---------------------------------------------------------------------------

def _g(key):
    return "${get_extract_data('%s')}" % key


def _cfg(tail):
    return reg.get_constants()["url_prefix"]["config"] + tail


def _mock(tail):
    return reg.get_constants()["url_prefix"]["mock"] + tail


def _V(name):
    return copy.deepcopy(reg.get_constants()[name])


def _step(url, cases: list[dict], api_name: str) -> dict:
    return {
        "baseInfo": {
            "api_name": api_name,
            "header": copy.deepcopy(reg.get_constants()["header"]),
            "method": "post",
            "url": url,
        },
        "testCase": cases,
    }


def _tc(name, json=None, input_extract=None, extract=None, extract_list=None,
        validation=None):
    """构造 testCase 条目。

    validation 必须始终是 list（`Assertions.assert_result` 要求，见
    common/assertions.py:216）。None = 用配置面通用断言；[] = 不断言（teardown）。
    """
    tc = {"case_name": name}
    if input_extract:
        tc["input_extract"] = input_extract
    if extract:
        tc["extract"] = extract
    if extract_list:
        tc["extract_list"] = extract_list
    if json is not None:
        tc["json"] = json
    tc["validation"] = _V("validation_ok") if validation is None else validation
    return tc


def _ok_mock():
    return _V("validation_mock_ok")


def _ok_mock_read():
    """mock 面无外壳可断言的只读端点（carTypeQuery / monthlyCar/query）。"""
    return _V("validation_mock_read")


# ---------------------------------------------------------------------------
# setup
# ---------------------------------------------------------------------------

def _setup_blocks(plan) -> dict:
    """setup 的**底层切块** —— 只为按不同粒度重排，块内容逐字沿用旧实现。

    Returns:
        {"ents": [车场实体…],
         "global": [清场 / 收费规则],
         "park":   {eid: [建场, 回读 parkCode]},
         "device": {eid: [入口设备, 出口设备]},
         "case":   {case_id: [该例自有块]}}   # 无自有块的用例不在键里（表1-24）
    """
    ents = [e for e in park_entities() if e["group"] in groups_in_scope(plan)]
    pay = needs_pay(plan)

    # 特殊车牌不单独建记录：车牌随车场 payload 的 specialAccessConfigList 写入，
    # 故在同一步用 input_extract 取回（实证 test_enable_special_plate_release_positive）。
    special_keys = _special_plate_keys(plan)

    park, device = {}, {}
    for e in ents:
        park[e["eid"]] = _park_steps(e, pay, _other_codes_for(e),
                                     special_keys.get(e["eid"]))
        device[e["eid"]] = _device_steps(e)

    case = {}
    for c in plan.active_cases:
        blocks = _temp_car_step_for_case(c) + _car_setup_steps_for_case(c)
        if blocks:
            case[c.case_id] = blocks

    return {"ents": ents,
            "global": [_clean_step()] + (_pay_rule_steps() if pay else []),
            "park": park, "device": device, "case": case}


def build_setup(plan) -> list[dict]:
    """统一前置（**旧顺序**）：清场 → 收费规则 → 各场建场 → 各场设备 → 临停牌 → 车辆记录。

    顺序与内容与拆三级之前逐字相同：**块内容**由
    `tests/test_rule_table_fixture_split.py::TestBlockPartition` 逐块比对、
    **顺序**由同文件 `TestLegacyOrder` 钉住（改动前的 golden 逐字比对是开发期
    手工做的，留档见 `changelog/2026-09-15_per_precondition_fixtures.md` §六）。
    「跑起来用哪份」由 `build_setup_parts` 决定；本函数保留旧视图，便于比对与排查。
    """
    b = _setup_blocks(plan)
    steps = list(b["global"])
    for e in b["ents"]:
        steps.extend(b["park"][e["eid"]])
    for e in b["ents"]:
        steps.extend(b["device"][e["eid"]])
    for c in plan.active_cases:                # 临停车牌（无车辆记录的用例）
        if not c.vehicles:
            steps.extend(b["case"].get(c.case_id, []))
    for c in plan.active_cases:                # 车辆记录
        if c.vehicles:
            steps.extend(b["case"].get(c.case_id, []))
    return steps


def build_setup_parts(plan) -> dict:
    """按「全局 / 每组 / 每例」三级给出 setup（= 每个前置编号一个文件）。

    与 `build_setup` 是**同一批块**的两种排布：

      * 组内顺序 = 该组各实体在旧顺序里的相对顺序（建场+回读，再入口/出口设备）；
      * 例内顺序 = 该例的临停车牌（若有）+ 车辆记录 add/回读。

    Returns:
        {"global": [...], "group": {group_id: [...]}, "case": {case_id: [...]}}
    """
    b = _setup_blocks(plan)
    groups = {}
    for e in b["ents"]:
        groups.setdefault(e["group"], []).extend(
            b["park"][e["eid"]] + b["device"][e["eid"]])
    return {"global": list(b["global"]),
            "group": groups,
            "case": {cid: list(blocks) for cid, blocks in b["case"].items()}}


def has_own_blocks(case) -> bool:
    """该用例有没有「每例」级的自有前置块。

    表1-24（特殊车牌）没有：车牌随 P5 车场的 `specialAccessConfigList` 写入，
    由**组**级前置承担，故它不生成「每例」前置、Sheet1 也不引用该编号。
    """
    return bool(_temp_car_step_for_case(case) or _car_setup_steps_for_case(case))


def case_pre_ids(case) -> list[str]:
    """该用例**实际需要**的前置编号（顺序即 fixture 挂载顺序）：全局 → 本组 → 本例。"""
    ids = [PRE_ID_GLOBAL, pre_id_group(case.group_id)]
    if has_own_blocks(case):
        ids.append(pre_id_case(case))
    return ids


def _group_of_eid(eid: str) -> str:
    return next(e["group"] for e in park_entities() if e["eid"] == eid)


def group_deps(group_id: str) -> list[str]:
    """该组前置**依赖的其它组**：父车场（跨组时）与该实体 `other_parks` 引用的车场。

    组内实体的父子（如 PS1S ← PS1M）不是组间依赖 —— 它们在同一份组前置里，
    靠块顺序保证；跨组只有两处：S1 ← P1（子场要主场 parkCode）、
    P4 ← PO（`otherParkWhiteAccessParks` 要 PO 的 parkCode）。
    """
    out = []
    for e in park_entities():
        if e["group"] != group_id:
            continue
        if e["parent"]:
            g = _group_of_eid(e["parent"])
            if g != group_id:
                out.append(g)
        out.extend(_referenced_groups(e))
    return list(dict.fromkeys(out))


def _referenced_groups(ent) -> list[str]:
    """该实体 `other_parks` 里引用的分组 id（按 role 取它自己那份车场定义）。"""
    park = _park_def(ent)
    return [g for group_ids in ((park or {}).get("other_parks") or {}).values()
            for g in group_ids]


def _park_def(ent):
    """实体对应的车场定义（`group["parks"]` 里 role 相同的那份）。"""
    group = reg.get_group(ent["group"]) or {}
    return next((p for p in group.get("parks") or []
                 if p.get("role") == ent["role"]), None)


def _other_codes_for(ent) -> dict:
    """该实体车场 payload 里 `*AccessParks` 需要的他场编码。

    旧实现传的是**边建边累积**的字典（`other_codes[eid] = parkCode`），
    而 `reg.get_park_spec` 只按 `*AccessParks` 里的**分组 id** 取值
    （`codes.get(g, "")`）—— 命中的键必须正好是那个分组的实体 eid。按组拆开后
    累积范围变了，故改成精确取值。全库唯一的 `other_parks` 是 P4 → `['PO']`，
    而 PO 的 eid 就是分组 id，两种传法逐字一致（有测试 + 落盘产物比对兜底）。
    """
    codes = {}
    for gid in _referenced_groups(ent):
        codes[gid] = _g(k_park_code(_main_sub_eids(gid)[0]))
    return codes


def _temp_car_step_for_case(case) -> list[dict]:
    """该用例的临停车牌块（有车辆记录的用例返回 []）。

    临停车无记录，但断言仍需要一个合法车牌 → **每例各取一块**。

    `mock/access/enter|exit` 与 `mock/carTypeQuery` 都要传车牌；临停车用例
    （表2 全部）没有车辆记录，故各取一个随机车牌存进 extract。

    **一例一牌**：mock 的在场状态挂在**车牌**上 —— 全量跑实测，共用一块牌时
    本批第一条用例留下在场/账单状态，其后每例 `bill/unpaid` 都塌成 `fee:0`
    （无券用例同样中招），隔离实验见 `changelog/_phase_c_selective_2026-09-09/MANIFEST.md`
    H19 根因 A。键与 `_primary_car_key` 同源（`k_car_number(case, 'car_a')`）。
    """
    if case.vehicles:
        return []
    park_eid = _main_sub_eids(case.group_id)[0]
    return [_step(_mock("/carTypeQuery"), [
        _tc(f"PRE-004: 生成临停车牌（{case.case_id}）",
            json={"carNumber": "${random_plates(1)}",
                  "parkCode": _g(k_park_code(park_eid)),
                  "startTime": _fmt(_base_dt())},
            input_extract={k_car_number(case, "car_a"): "$.json.carNumber"},
            validation=_ok_mock_read()),
    ], "查询在场类型")]


def _temp_car_step(plan) -> list[dict]:
    """全量临停车牌块（旧视图，顺序 = active_cases）。"""
    steps = []
    for c in plan.active_cases:
        steps.extend(_temp_car_step_for_case(c))
    return steps


def _clean_step() -> dict:
    """清场。`parkCode` 语义未验证（MANIFEST H9）；与现存两个调用方一致写 PARK-FIXTURE。"""
    return _step(_mock("/delAllForMock"), [
        _tc("PRE-001: 清空测试园区全量进出数据", json={"parkCode": "PARK-FIXTURE"},
            validation=[{"contains": {"status_code": 200}},
                        {"eq": {"$.success": True}}]),
    ], "删除某园区 全量 进出相关数据")


def _pay_rule_steps() -> list[dict]:
    """收费规则：主场 / 子场各一条，逐一 add + 分页回读 code。

    实证 setup_change_device_parking.yaml：子场挂的是 `mainPark: false` 的
    独立规则（主场规则 mainPark: true 不能给子场用）。
    `getParkingPaymentConfigPage` 按 mainPark 过滤，故回读各查一次。
    """
    out = []
    for slot, main_park in (("prePayCode", True), ("preChildPayCode", False)):
        tpl = copy.deepcopy(reg.get_pay_rule_template())
        tpl["name"] = "${random_code('RULE-%s', 6)}" % ("M" if main_park else "C")
        tpl["mainPark"] = main_park
        tag = "主场" if main_park else "子场"
        out.append(_step(_cfg("/parkingPaymentConfig/add"), [
            _tc(f"PRE-002{'' if main_park else 'B'}: 新增{tag}临时停车费规则",
                json=tpl),
        ], "收费规则-临时停车费-临停车费-添加"))
        out.append(_step(_cfg("/parkingPaymentConfig/getParkingPaymentConfigPage"), [
            _tc(f"PRE-003{'' if main_park else 'B'}: 回读{tag}收费规则编码",
                json={"configType": "3", "mainPark": main_park,
                      "pageNum": 1, "pageSize": 10},
                extract={slot: "$.data.list[0].code"}),
        ], "收费规则-查询分页"))
    return out


def _pay_key(role: str) -> str:
    """子场用子场收费规则，主场用主场收费规则。"""
    return "preChildPayCode" if role == "sub" else "prePayCode"


def _special_plate_keys(plan) -> dict:
    """eid → [车牌提取键]，取自该车场特殊车牌用例的车牌槽位。

    特殊车牌没有独立 add 步，车牌在车场 payload 里，故提取点挂在车场创建步上。
    """
    out = {}
    for c in plan.active_cases:
        for v in c.vehicles:
            if v["kind"] != "特殊车牌":
                continue
            for eid in _park_eids(v["scope"], _main_sub_eids(c.group_id)):
                out.setdefault(eid, []).append(k_car_number(c, v["car"]))
    return out


def _park_steps(e, pay, other_codes, special_keys=None) -> list[dict]:
    """建车场 + 回读 parkCode。

    parkName 走 random_code、parkCode 回读后填 parentCode —— 唯一化要求见
    Round 4「此车场名称已存在」根因（禁止字面量 parkName）。

    回读**必须按名筛选**，不能写 `$.data[0]`：`getAllWithDevices` 的 `parkName`
    参数服务端不生效（传任意名都返回全量），`data[0]` 于是退化成撞排序运气 ——
    实测把 PS3M 的码取成了 PS2S，整片车场拓扑挂错（见
    `changelog/_phase_c_selective_2026-09-09/parking_rule_table_round2_attribution.md` §五）。
    车场名是我们自己生成的、服务端强制唯一，用它做过滤是无歧义的。
    """
    eid, role = e["eid"], e["role"]
    parent_code = _g(k_park_code(e["parent"])) if e["parent"] else ""
    spec = reg.get_park_spec(
        e["group"], "", "${random_code('PK-%s', 6)}" % eid,
        pay_code=_g(_pay_key(role)) if pay else None,
        other_park_codes=other_codes, role=role, parent_code=parent_code)
    ix = {k_park_name(eid): "$.json.parkingPark.parkName"}
    for key in special_keys or []:
        ix[key] = "$.json.specialAccessConfigList[0].carNumber"
    return [
        _step(_cfg("/parkingPark/saveOrUpdate"), [
            _tc(f"PRE-{eid}: 新建车场", json=spec, input_extract=ix),
        ], "车场维护聚合-添加/修改"),
        _step(_cfg("/parkingPark/getAllWithDevices"), [
            _tc(f"PRE-{eid}: 回读车场编码",
                json={"parkName": _g(k_park_name(eid))},
                extract={k_park_code(eid):
                         "$.data[?(@.parkingPark.parkName=='%s')]"
                         ".parkingPark.parkCode" % _g(k_park_name(eid))}),
        ], "查询所有车场及设备列表"),
    ]


def _device_steps(e) -> list[dict]:
    """每个车场建 入(passType 1) / 出(passType 2) 两个设备，code 用 input_extract 取回。"""
    eid = e["eid"]
    out = []
    for pass_type, kdev, tag in (("1", k_dev_in(eid), "入口"),
                                 ("2", k_dev_out(eid), "出口")):
        body = {
            "code": "${random_code('DEV-%s-%s', 6)}" % (eid, pass_type),
            "deviceStatus": "1",
            "deviceType": "1",
            "inner": False,
            "location": "${random_code('LOC', 6)}",
            "multi": False,
            "name": "${random_code('CAM', 6)}",
            "needToShow": "1",
            "parkCode": _g(k_park_code(eid)),
            "parkName": _g(k_park_name(eid)),
            "parkingDeviceManufacturerCode": "100061",
            "parkingDeviceManufacturerName": "大华",
            "parkingDeviceModelCode": "100121",
            "parkingDeviceModelName": "DH-IPC-HFW5234K-ZFD-LED",
            "passType": pass_type,
        }
        out.append(_step(_cfg("/parkingDevice/add"), [
            _tc(f"PRE-{eid}: 新增{tag}相机设备", json=body,
                input_extract={kdev: "$.json.code"}),
        ], "设备-添加"))
    return out


# ---------------------------------------------------------------------------
# 用例车辆记录（统一前置建，teardown 兜底清理）
# ---------------------------------------------------------------------------

def _main_sub_eids(group_id: str):
    ent = park_entities()
    mains = [e for e in ent if e["group"] == group_id and e["role"] == "main"]
    subs = [e for e in ent if e["group"] == group_id and e["role"] == "sub"]
    return (mains[0]["eid"] if mains else "PO", subs[0]["eid"] if subs else None)


def _park_eids(scope, eids) -> list[str]:
    """记录建在哪个（些）车场；both → 主+子两场。"""
    main_eid, sub_eid = eids
    if scope == "other":
        return ["PO"]
    if scope == "sub":
        return [sub_eid]
    if scope == "both":
        return [main_eid, sub_eid]
    return [main_eid]


def _refs(eid_list, key_fn) -> str:
    """多个车场编码/名称渲染成逗号分隔的取值（**不是**把一个复合串当键）。

    每场各自一个 `${get_extract_data(...)}`，再用 `,` 连接 —— 车场侧字段
    `otherParkWhiteAccessParks` / 白名单 `parkCode` 多场时就是这个形状。
    """
    return ",".join(_g(key_fn(e)) for e in eid_list)


def _car_setup_steps_for_case(case) -> list[dict]:
    """该用例的车辆记录块（add + 回读；无车辆记录 / 只有特殊车牌 → []）。"""
    kinds = reg.get_vehicle_kinds()
    roots = [v for v in case.vehicles if not v.get("sub_of")]
    subs = [v for v in case.vehicles if v.get("sub_of")]
    plates = {}                            # 本用例已抽过的车牌槽位：键 → 已写过 input_extract
    steps = []
    for v in roots:
        kind = v["kind"]
        if kind == "特殊车牌":
            continue                       # 随车场配置写入（specialAccessConfigList）
        park_eids = _park_eids(v["scope"], _main_sub_eids(case.group_id))
        steps.extend(_car_add_step(case, v, kind, park_eids, subs, kinds, plates))
        steps.extend(_car_readback_step(case, v, kind, park_eids, kinds))
    return steps


def _car_setup_steps(plan) -> list[dict]:
    """全量车辆记录块（旧视图，顺序 = active_cases）。"""
    steps = []
    for c in plan.active_cases:
        steps.extend(_car_setup_steps_for_case(c))
    return steps


def _car_add_step(case, v, kind, park_eids, subs, kinds, plates) -> list[dict]:
    """`plates` 是本用例内**已抽过**的车牌槽位，用于同槽位复用同一块牌。

    提取键 `k_car_number(case, car)` 只按「用例 + 槽位」命名，而 add 体里的
    `carNumber: ${random_plates(1)}` 是**每条记录各抽一次** ⇒ 同一槽位出现两次时
    两块牌共用一个键，后写覆盖先写，用例后续步骤全用错牌（仅
    `vehicle_recipes.混合_主场白名单_子场包月` = 表1-23 命中：主场白名单 +
    子场包月同取 `car_a`，实测两块牌 湘BHFL9H / 云UH73VG，键最终被后者覆盖，
    主车场查不到白名单记录 → `carTypeQuery` 取不到 `parkingType=4`）。
    故槽位**首次**出现才抽牌并写 `input_extract`，**再次**出现一律复用首块牌。
    """
    spec = kinds[kind]
    car = v["car"]
    num_key = k_car_number(case, car)
    body = copy.deepcopy(spec["add"]["body"])
    body["parkCode"] = _refs(park_eids, k_park_code)
    if "parkName" in body:
        body["parkName"] = _refs(park_eids, k_park_name)

    ix = {}
    label = f"新建{kind}"
    if num_key in plates:
        body["carNumber"] = _g(num_key)
    else:
        plates[num_key] = True
        ix[num_key] = spec["add"]["input_extract"]["<C>CarNumber"]
    if subs:
        # 一人多车：同一记录带 subCarNumbers，副车牌随同一次请求写入（同槽位同规则）
        body.update(copy.deepcopy(spec["add"].get("multi") or {}))
        sub_slots = body.get("subCarNumbers") or []
        for i, s in enumerate(subs):
            skey = k_car_number(case, s["car"])
            if skey in plates:
                if i < len(sub_slots):
                    sub_slots[i] = _g(skey)
            else:
                plates[skey] = True
                ix[skey] = spec["add"]["input_extract_multi"]["<C>SubCarNumber"]
        label += "（一人多车）"

    if kind == "白名单":
        url, api_name, val = _cfg("/carWhiteBlack/add"), "黑白名单-添加", None
    else:
        url, api_name, val = _mock("/monthlyCar/add"), "包月车添加", _ok_mock()
    return [_step(url, [_tc(f"{case.case_id}: {label}", json=body,
                            input_extract=ix, validation=val)], api_name)]


def _car_readback_step(case, v, kind, park_eids, kinds) -> list[dict]:
    """回读记录主键：删除必须传真实 code（传车牌会假成功，2026-09-11 实测）。

    多车场记录（主+子）走 `readback_multi`：服务端把 `parkCode` 存成逗号复合串，
    而查询侧的 `parkCode` 是等值匹配 → 传复合串、传单码、甚至传它自己那个串都命中
    不了（`''` ≡ 不传）。故只能"不传 parkCode + searchKey 捞"，再用 filter 精确到车。
    实证见 `parking_rule_map.yaml::vehicle_kinds.白名单.readback_multi` 注释。
    """
    spec = kinds[kind]
    car = v["car"]
    num_key = k_car_number(case, car)
    multi = len(park_eids) > 1 and "readback_multi" in spec
    rb = (spec.get("readback_multi") or {}) if multi else {}
    body = copy.deepcopy(spec["readback"]["body"])
    if multi:
        for omit in rb.get("omit_body") or []:
            body.pop(omit, None)
    else:
        body["parkCode"] = _refs(park_eids, k_park_code)
    key_field = "searchKey" if "searchKey" in body else "carNumber"
    body[key_field] = _g(num_key)
    expr = next(iter((rb.get("extract") or spec["readback"]["extract"]).values()))
    # `<C>CarNumber` 在 extract 里展开成**裸键名**（供 get_extract_data 引用），
    # 与请求体里展开成 `${...}` 的写法不同源，故单独替换。
    expr = expr.replace("<C>CarNumber", num_key)
    if kind == "白名单":
        url, api_name = _cfg("/carWhiteBlack/getPage"), "黑白名单-查询分页"
        val = None                    # 配置面：validation_ok（有 retCode/msg 外壳）
    else:
        url, api_name = _mock("/monthlyCar/query"), "包月车辆查询"
        val = _ok_mock_read()         # mock 面：裸数组，无外壳可断言
    return [_step(url, [_tc(f"{case.case_id}: 回读{kind}编码", json=body,
                            extract={k_car_record(case, car, kind): expr},
                            validation=val)], api_name)]


# ---------------------------------------------------------------------------
# teardown（validation 一律 [] —— 框架 `_relax_teardown_validation` 亦会兜底置空）
# ---------------------------------------------------------------------------

def build_teardown(plan) -> list[dict]:
    steps = []
    ents = [e for e in park_entities() if e["group"] in groups_in_scope(plan)]
    kinds = reg.get_vehicle_kinds()

    for c in plan.active_cases:
        for v in c.vehicles:
            if v["kind"] == "特殊车牌" or v.get("sub_of"):
                continue                       # 副车牌随主记录一并删除
            rec_key = k_car_record(c, v["car"], v["kind"])
            if v["kind"] == "白名单":
                steps.append(_step(_cfg("/carWhiteBlack/delete"), [
                    _tc(f"TEARDOWN-{c.case_id}: 删白名单", json=[_g(rec_key)],
                        validation=[]),
                ], "黑白名单-删除"))
            else:
                body = copy.deepcopy(kinds["包月车"]["delete"]["body"])
                body["codes"] = [_g(rec_key)]
                steps.append(_step(_mock("/monthlyCar/delete"), [
                    _tc(f"TEARDOWN-{c.case_id}: 删包月车", json=body, validation=[]),
                ], "包月车删除"))

    for e in ents:
        eid = e["eid"]
        steps.append(_step(_cfg("/parkingDevice/deleteByCodes"), [
            _tc(f"TEARDOWN-{eid}: 删设备",
                json={"codes": "%s,%s" % (_g(k_dev_in(eid)), _g(k_dev_out(eid)))},
                validation=[]),
        ], "设备-删除"))
    for e in reversed(ents):                   # 子场先于父场
        steps.append(_step(_cfg("/parkingPark/deleteByCodes"), [
            _tc(f"TEARDOWN-{e['eid']}: 删车场", json=[_g(k_park_code(e['eid']))],
                validation=[]),
        ], "车场-删除"))

    if needs_pay(plan):
        for slot, tag in (("prePayCode", "主场"), ("preChildPayCode", "子场")):
            steps.append(_step(_cfg("/parkingPaymentConfig/delete"), [
                _tc(f"TEARDOWN-PAY: 删{tag}收费规则", json=[_g(slot)],
                    validation=[]),
            ], "收费规则-删除"))
    return steps


# ---------------------------------------------------------------------------
# 单用例
# ---------------------------------------------------------------------------

def _base_dt() -> datetime.datetime:
    tf = reg.get_time_format()
    return datetime.datetime.strptime(
        "%s %s" % (tf["base_date"], tf["base_hms"]), "%Y-%m-%d %H:%M:%S")


def _fmt(dt) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _times_for(case) -> list[str]:
    """本用例**逐个动作**的时刻字面量：第 i 个 = `case.actions` 第 i 个动作。

    判定表 c9「条件（出入场动作）」本身就是**有序**的动作序列 ——
    表1-08/09/19/20 写 `A入主-B入主-A出主-B出主`、表1-10/21 写 `A入主-B入主` ——
    所以时刻按同一顺序列出、**位置即归属**，不需要任何键。

    一人多车时 A/B 两车**共用同一个 token**（都是「入主」/「出主」）：按键取时刻
    会把两车压成同一秒（2026-09-15 之前的产物就是这样，「后入场」根本不存在），
    按位置取则天然分得开 —— 这正是改用位置对应的理由。

    个数必须等于动作数：不等就报错，**不补齐、不截断、不兜底**。
    2026-09-15 之前「没写时间线就把所有动作落在基准时刻」那条兜底，
    正是 17 条用例入场==出场（停车时长 0）长期不被发现的原因。

    字面量的日期必须与 `time_format.base_date` 同一天：车辆记录的有效期窗口
    （MANIFEST H19-B1）是以基准日为准配的，日期写死后若只改 `base_date`
    而时刻不跟随，动作会落到有效期窗口外（2026-09-11 首轮 `openGate=false` 的坑）。
    确有跨天用例（如过夜停车）时，改的是这条约束本身，而不是绕过它。
    """
    tl = reg.get_timeline().get(case.case_id)
    if not tl:
        if not case.actions:
            return []
        raise ValueError(
            f"{case.case_id}: 有 {len(case.actions)} 个动作却没有 timeline"
            f"（每个有动作的用例都必须逐例给出时刻，见 map 的 timeline 说明）")
    if len(tl) != len(case.actions):
        raise ValueError(
            f"{case.case_id}: timeline 有 {len(tl)} 个时刻、动作有 "
            f"{len(case.actions)} 个（{tl} vs "
            f"{[a['token'] for a in case.actions]}）—— 按位置对应，个数必须相等")
    base_date = reg.get_time_format()["base_date"]
    for i, t in enumerate(tl):
        if not isinstance(t, str):
            raise ValueError(
                f"{case.case_id}: timeline 第 {i + 1} 项必须是字面量时刻字符串"
                f"（如 \"{base_date} 10:00:00\"），实际是 {t!r}")
        if not t.startswith(base_date):
            raise ValueError(
                f"{case.case_id}: timeline 第 {i + 1} 项 {t!r} 的日期不是 "
                f"base_date {base_date}（理由见本函数 docstring 的 H19-B1 一段）")
    return list(tl)


def _primary_car_slot(case) -> str:
    """该用例参与**缴费/券动作**的车槽位。

    临停车无记录（车牌由 `_temp_car_step` 一例一牌），只有一块牌（`car_a` 槽位）；
    表2 的断言列不含 A/B 形态（「按临停计费」是表1 专属列），故缴费/券只认第一辆。
    """
    return case.vehicles[0]["car"] if case.vehicles else "car_a"


def _primary_car_key(case, car=None) -> str:
    """该用例参与动作的车牌**提取键**（车槽位取法见 `_primary_car_slot`）。"""
    return k_car_number(case, car or _primary_car_slot(case))


def _primary_car(case, car=None) -> str:
    """同上的 `${get_extract_data(...)}` 引用形态（请求体里用）。"""
    return _g(_primary_car_key(case, car))


def build_case(case, plan) -> list[dict]:
    steps = []
    times = _times_for(case)
    eids = _main_sub_eids(case.group_id)
    gate_values = _gate_values(case)
    pending = _parking_type_plan(case)
    fee_inserted = False

    for i, a in enumerate(case.actions):
        token = a["token"]
        now = times[i]          # 本动作的时刻：位置对应（见 `_times_for`）
        if token == "变更临停":
            # 这步只删记录、不产生请求时刻，但仍占时间线一个位置 ——
            # 时间线逐位对齐 c9 的动作序列，读起来就是一串动作。
            steps.extend(_switch_to_temp_steps(case))
            continue
        if token not in _GATE_SCOPES:
            raise ValueError(f"{case.case_id} 动作 token 无映射: {token}")
        scope = _GATE_SCOPES[token]
        eid = eids[0] if token in ("入主", "出主") else eids[1]
        if eid is None:
            raise ValueError(f"{case.case_id} 用到子场动作但分组 {case.group_id} 无子场")
        dev = k_dev_in(eid) if token.startswith("入") else k_dev_out(eid)

        # 缴费：判定表口径「先查询缴费金额，再看能不能出场」→ 整块插在
        # **第一次出场之前**（出场后再查/再缴没有意义，且账单尚未成立）。
        # 券核销必须**更早**：抵扣要先进账单，`fee` 断言才是抵扣后的值。
        # 两者都取**本次出场自己的时刻** —— 就是当前这一步的 `now`。
        if token.startswith("出") and not fee_inserted:
            fee_inserted = True
            steps.extend(_coupon_steps(case, now))
            steps.extend(_fee_steps(case, now, eid))

        validation = [{"contains": {"status_code": 200}}]
        expect = _pick_gate(gate_values, scope, case, a)
        if expect is not None:
            validation.append({"eq": {"$.openGate": expect}})
        steps.append(_step(
            _mock("/access/" + ("enter" if token.startswith("入") else "exit")), [
                _tc(f"{case.case_id}: {token}", json={
                    "carNumber": _primary_car(case, a.get("car")),
                    "deviceCode": _g(dev),
                    "time": now,
                }, validation=validation),
            ], "入场" if token.startswith("入") else "出场"))

        # 在场类型必须**紧跟对应入场**查询：`mock/carTypeQuery` 只报在场车，
        # 出过场再查返回 `parkingType: null`（2026-09-11 首轮 29/29 全灭的原因）。
        if token.startswith("入"):
            for p in [p for p in pending if _ptype_matches(p, a, token)]:
                # 必须带上本次入场的 token：`scope` 为 None 的断言指的是
                # 「**本次入场所在的那片场**」，而入子时车在子场、不在主/园区码那场。
                steps.append(_parking_type_call(case, p, token, now))
                pending.remove(p)

    if pending:
        raise ValueError(
            f"{case.case_id}: 按临停计费断言找不到对应的入场动作 → {pending}"
            f"（动作 {[a['token'] for a in case.actions]}），不静默丢弃")
    steps.extend(_assert_steps(case, plan))
    return steps


def _ptype_matches(p, action, token) -> bool:
    """该在场类型断言是否挂在这次入场上。scope 未指定时取本次入场所在场。"""
    if p["car"] != (action.get("car") or "car_a"):
        return False
    if p["scope"] is None:
        return True
    return p["scope"] == ("main" if token == "入主" else "sub")


def _gate_values(case) -> dict:
    """scope → 该动作列的期望取值（bool / [bool, bool] / None）。"""
    out = {}
    for col in reg.get_assert_columns():
        if col["kind"] != "gate":
            continue
        raw = case.asserts.get(col["column"])
        out[col["scope"]] = reg.get_assert_value_map().get(raw)
    return out


def _pick_gate(gate_values, scope, case, action):
    """从动作列取值中取出该动作的期望；A/B 列表按同 scope 出现次序对应。

    xlsx 的 gate 取值域：是 / 否 / A、B是 / A是、B否 / A、B否 / A否、B是。
    返回 None 表示该格不适用（`\\` 或未登记取值）—— 由单测 `_assert_coverage` 兜底报警。
    """
    val = gate_values.get(scope)
    if isinstance(val, list):
        seq = [a for a in case.actions if _GATE_SCOPES.get(a["token"]) == scope]
        idx = seq.index(action) if action in seq else 0
        return val[idx] if idx < len(val) else None
    return val


def _switch_to_temp_steps(case) -> list[dict]:
    """「主场内，变更为临停车」= 删除该车类型记录 → 降级为临停。"""
    steps = []
    for v in case.vehicles:
        if v["kind"] == "特殊车牌" or v.get("sub_of"):
            continue
        rec = _g(k_car_record(case, v["car"], v["kind"]))
        if v["kind"] == "白名单":
            steps.append(_step(_cfg("/carWhiteBlack/delete"), [
                _tc(f"{case.case_id}: 转临停-删白名单", json=[rec]),
            ], "黑白名单-删除"))
        else:
            body = copy.deepcopy(reg.get_vehicle_kinds()["包月车"]["delete"]["body"])
            body["codes"] = [rec]
            steps.append(_step(_mock("/monthlyCar/delete"), [
                _tc(f"{case.case_id}: 转临停-删包月车", json=body,
                    validation=_ok_mock()),
            ], "包月车删除"))
    return steps


# ---------------------------------------------------------------------------
# 断言步骤
# ---------------------------------------------------------------------------

def _assert_steps(case, plan) -> list[dict]:
    steps = []
    for col in reg.get_assert_columns():
        if col["table"] != case.table:
            continue
        raw = case.asserts.get(col["column"])
        if raw in (None, "", "\\", "不适用"):
            continue
        if col["kind"] == "parking_type":
            # 已由 build_case 挂在**入场动作之后**（出场后查不到在场记录），
            # 见 _parking_type_plan 的注释。
            continue
        if col["kind"] == "paid":
            steps.append(_paid_step(case))
    # 「不产生费用」由 fee_expectation 驱动、**出场后**断 payableAmount；数字金额已由
    # build_case 连同缴费动作插到**第一次出场之前**（见 _fee_steps）。
    # 表1-24 无「缴费金额」列，但用户口径「出场不收费」需要在费用侧断言为 0。
    step = _no_fee_step(case)
    if step:
        steps.append(step)
    return steps


def _parking_type_plan(case) -> list[dict]:
    """xlsx「按临停计费」→ 待断言的 `[{car, scope, expect}]`（顺序即查询顺序）。

    取值域与语义（逐字对 xlsx 表1 第 14 列）：
      `否`            → 该车按**自身类型**（白名单 4 / 包月车 1 / 特殊车牌 2）
      `是`            → 该车按**临停**（2）
      `主场否、子场是`  → 主场按自身类型、子场按临停（表1-05/16）
      `A、B否` / `A否、B是` / `A、B是` / `A是、B否` → 两辆车各自的期望

    `scope` 为 None 表示「本次入场所在的那片场」（主/子按动作 token 定）。
    真正的**入场类型**在入场时确定，故断言点由 `build_case` 挂在入场之后 ——
    「变更为临停车」那几条（表1-06/07/17/18）xlsx 也写 `否`，正说明该列
    评的是**入场时的类型**，不是变更后的。
    """
    col = next((c for c in reg.get_assert_columns()
                if c["kind"] == "parking_type" and c["table"] == case.table), None)
    if col is None:
        return []
    raw = case.asserts.get(col["column"])
    if raw in (None, "", "\\", "不适用"):
        return []
    specs = col["value_map"].get(raw)
    if specs is None:
        raise ValueError(f"{case.case_id}: 「按临停计费」取值未登记: {raw!r}")
    out = []
    for s in specs:
        expect = ("临停车" if s["expect"] == "temp" else None)
        out.append({"car": s["car"],
                    "scope": s.get("scope"),
                    "expect": (reg.get_parking_type(expect) if expect
                               else _kind_type(case))})
    return out


def _kind_type(case) -> str:
    """该用例车辆在车场的预期 parkingType（按首个车辆类型查表）。

    未登记 → 报错不兜底（见 MANIFEST H10：特殊车牌的 parkingType 未验证）。
    """
    if not case.vehicles:
        return reg.get_parking_type("临停车")
    kind = case.vehicles[0]["kind"]
    got = reg.get_parking_type(kind)
    if got is None:
        raise ValueError(
            f"{case.case_id}: 车辆类型「{kind}」未登记 parking_type，"
            f"无法断言「按临停计费=否」（见 MANIFEST H10）")
    return got


def _parking_type_call(case, p, entry_token, entry_time) -> dict:
    """`mock/carTypeQuery` → `contains: {parkingType: <int>}`。

    查询的**车场**取「本次入场所在的那片场」：`p["scope"]` 已指定时按它，
    未指定（`None`）时按入场 token（入主→主场 / 入子→子场）。
    早期实现用 `eids[0] if p["scope"] != "sub"` 兜底，把「入子」的查询打到
    主/园区码那场上（表1-02/1-13 实测返 `inPark:false`），是**我方生成器 Bug**。

    `startTime` 取**该车自己这次入场的时刻**（不是基准时刻）：一人多车用例里
    A/B 入场时刻不同（见 `_times_for`），写死基准时刻会让子车的查询时刻与它
    实际的入场时刻不一致。时刻由 `build_case` 按位置取好传进来。

    用 contains 而非 eq：contains 把两侧都 str 化后做子串比较
    （common/assertions.py:52-64 先按 JSONPath 取值再比），接口返字符串还是
    数字都成立；eq 走 operator.eq 要求类型严格相等，脆弱。
    实证 test_main_whitelist_entry_exit_pass_positive（eq '4'）与
    VehicleAccesstest_002/queryCarBPresent_002（contains 1）写法不一致，
    取两者都能过的 contains。
    """
    eids = _main_sub_eids(case.group_id)
    scope = p["scope"] or ("sub" if entry_token == "入子" else "main")
    eid = eids[0] if scope != "sub" else eids[1]
    if eid is None:
        raise ValueError(f"{case.case_id}: 断言子场类型但分组 {case.group_id} 无子场")
    label = "" if p["scope"] is None else f"/{p['scope']}"
    return _step(_mock("/carTypeQuery"), [
        _tc(f"{case.case_id}: 按临停计费校验({eid}{label}, {p['car']})",
            json={"carNumber": _primary_car(case, p["car"]),
                  "parkCode": _g(k_park_code(eid)),
                  "startTime": entry_time},
            validation=[{"contains": {"status_code": 200}},
                        {"contains": {"parkingType": int(p["expect"])}}]),
    ], "查询在场类型")


def _paid_step(case) -> dict:
    """是否缴费 → `mock/bill/latest` + `eq: {$.paid: <bool>}`。

    期望值 = **是否真的执行了缴费动作**（与 `_pay_step` 同源，见 `_pay_action_key`）。

    实证 test_temp_car_prepaid_no_double_charge_positive/test_data.yaml
    （`- contains: {$.paid: true}`）。

    ⚠️ **不照判定表「动作（是否缴费）」列原文断**：表2-06/07/08/09 该列写「否」，
    而同一行的条件列「是否提前缴费」写「是」—— 两列打架，照动作列断 `false`
    这 4 例必错。用户 2026-09-15 口径：「提前缴费只要有费用产生是必成功的，
    后续你实际支付多少都成功，这只是一个动作」⇒ 缴了费 `paid` 必为 `true`。
    11:08 跑批实测：这 4 例 `pay/simulate` 均 `{success:true}`、`bill/latest`
    均 `paid:true`（`payableAmount:0.0` 是券抵扣后的余额，不影响已缴事实）。

    ⚠️ 用户口径的另一半「未产生费用应失败，且返回提示」**当前无用例覆盖**：
    表2-01（不产生费用）根本没有缴费动作。待有用例时再实现「缴费动作应失败」
    这一分支，现在不写无测试覆盖的死代码。
    """
    expect = _pay_action_key(case) is not None
    return _step(_mock("/bill/latest"), [
        _tc(f"{case.case_id}: 是否缴费校验",
            json={"carNumber": _primary_car(case)},
            validation=[{"contains": {"status_code": 200}},
                        {"eq": {"$.paid": expect}}]),
    ], "获取车牌最新账单")


def _coupon_steps(case, now) -> list[dict]:
    """`mock/addCouponAndUse` —— 券**创建 + 核销**一步到位（无发行接口前置）。

    插在「缴费金额查询」**之前**：抵扣要先落到账单上，否则 `fee` 断言拿到的是
    抵扣前的金额（**顺序不可调换**）。

    `now` 由 `build_case` 传入 = **本次出场自己的时刻**（与 `bill/unpaid` 的
    `currentTime` 同值）：文档口径「使用时间：车辆在场的时间内」，
    出场时刻是在场内的最后一个时刻。

    ⚠️ 主+子用例（表2-12/13）落在**第一次出场（出子）之前**、用子场出场时刻 ——
       用户 2026-09-11 明确「在子场提前核销亦可，只要出场时金额是正确的」。
    ⚠️ 双券（表2-04/09）发**两次**请求，金额券在前、时长券在后（同 xlsx 列序）；
       mock 是否支持一程双券**未经实证**（见 changelog/2026-09-11_coupon_cases_unblock.md §五）。
    """
    specs = reg.get_coupon_expectation().get(case.case_id) or []
    if not specs:
        return []
    cfg = reg.get_coupon_actions()
    if not cfg:
        raise ValueError(f"{case.case_id}: 映射缺 coupon_actions")
    out = []
    for s in specs:
        body = copy.deepcopy(cfg["body"])
        for ph, val in (("<K>CouponType", s["type"]),
                        ("<D>Data", str(s["data"])),
                        ("<C>CarNumber", _primary_car_key(case)),
                        ("<T>Time", now)):
            body = {k: (v.replace(ph, val) if isinstance(v, str) else v)
                    for k, v in body.items()}
        out.append(_step(_mock(cfg["api"][len("mock"):]), [
            _tc(f"{case.case_id}: {cfg['name']}（{s['type']}券 data={s['data']}）",
                json=body, validation=_ok_mock()),
        ], cfg["name"]))
    return out


def _fee_steps(case, now, eid) -> list[dict]:
    """「先查询缴费金额，再看能不能出场」——插在**第一次出场之前**。

    判定表口径（用户 2026-09-11 决策 ③）：金额断言严格照判定表原文
    （`mock/bill/unpaid` + `eq: {fee: <期望值>}`），并回读 `unpaidFee` 供缴费动作使用；
    随后按「是否提前缴费 / 是否缴费」决定缴不缴，最后才是出场。

    `now` 由 `build_case` 传入 = **本次出场自己的时刻**。必须晚于入场时间，
    否则算不出时长
    （第 2 轮实测把入场时刻丢进去 → 服务端 `{"retCode":0,"msg":"fail","data":"请求失败"}`）。
    断言形状逐字取自 VehicleAccesstest_002/carBPrepayExit_002.yaml（`eq` 用数字）。

    `不产生费用` 与未登记**不在此处**插入：前者由 `_no_fee_step` 在出场后断
    `bill/latest.payableAmount`（实测 `bill/latest` 出场前返空串，出场后才有账单）。

    ⚠️ 金额数值由 billing 单价×时长推得、未经实证（见 MANIFEST H10）；
    ⚠️ `bill/unpaid` 在该环境**恒返失败**（与时机无关，2026-09-11 实测），
       已按三层判障 §2 登记为接口缺陷挂起 —— 金额断言**不改口径**，等接口方确认。
    """
    exp = reg.get_fee_expectation().get(case.case_id)
    if exp is None or exp == "不产生费用":
        return []
    car = _primary_car(case)
    steps = [_step(_mock("/bill/unpaid"), [
        _tc(f"{case.case_id}: 缴费金额查询",
            json={"carNumber": car, "currentTime": now},
            validation=[{"eq": {"fee": exp}}],
            extract={"unpaidFee": "$.fee"}),
    ], "获取待支付费用")]
    pay = _pay_step(case, now, eid)
    if pay:
        steps.append(pay)
    return steps


def _pay_action_key(case) -> str | None:
    """本用例**实际执行**的缴费动作 → `pay_actions` 的键；不缴费返回 `None`。

    判定表把缴费信息分在两列：条件列「是否提前缴费」与动作列「是否缴费」。
    取值以**条件列优先**（照 map 的 `pay_actions` 先后）：提前缴费=是 → `prepay`；
    否则动作列「是否缴费」=是 → `when_leave`；两者都不是 → 不缴费。

    这是「缴不缴费」的**唯一判据** —— 缴费步骤（`_pay_step`）与缴费状态断言
    （`_paid_step`）都从这里取，避免两处各判一次而分叉。
    """
    if (case.prepay or "").strip() == "是":
        return "prepay"
    if (case.asserts.get("是否缴费") or "").strip() == "是":
        return "when_leave"
    return None


def _pay_step(case, now, eid) -> dict | None:
    """判定表缴费动作 → `mock/pay/simulate`（提前缴费）/ `mock/payWhenLeave/simulate`。

    缴不缴费由 `_pay_action_key` 判定（条件列优先，见其注释）；返回 None = 不缴费。
    实测（`有作用/PhaseA/_probe_pay_semantics.py`）：两接口均返 `{success:true}`，
    之后出场转 `openGate:true`、`bill/latest.paid` 转 `true`。
    """
    key = _pay_action_key(case)
    if key is None:
        return None
    cfg = reg.get_pay_actions().get(key)
    if not cfg:
        raise ValueError(f"{case.case_id}: 映射缺 pay_actions.{key}")
    body = copy.deepcopy(cfg["body"])
    for ph, val in (("<C>CarNumber", _primary_car_key(case)),
                    ("<D>DeviceCode", k_dev_out(eid)),
                    ("<F>UnpaidFee", "unpaidFee"),
                    ("<T>Time", now)):
        body = {k: (v.replace(ph, val) if isinstance(v, str) else v)
                for k, v in body.items()}
    return _step(_mock(cfg["api"][len("mock"):]), [
        _tc(f"{case.case_id}: {cfg['name']}", json=body, validation=_ok_mock()),
    ], cfg["name"])


def _no_fee_step(case) -> dict | None:
    """`不产生费用` → 出场后 `mock/bill/latest` + `eq: {$.payableAmount: 0}`。

    逐字取自 test_non_charge_parking_exit_no_fee_positive/test_data.yaml。
    实测 `bill/latest` **出场前返空串**、出场后才有账单 → 只能挂在出场之后。
    """
    if reg.get_fee_expectation().get(case.case_id) != "不产生费用":
        return None
    return _step(_mock("/bill/latest"), [
        _tc(f"{case.case_id}: 不产生费用校验",
            json={"carNumber": _primary_car(case)},
            validation=[{"contains": {"status_code": 200}},
                        {"eq": {"$.payableAmount": 0}}]),
    ], "获取车牌最新账单")


# ---------------------------------------------------------------------------
# 落盘
# ---------------------------------------------------------------------------

def _dump(steps) -> str:
    return yaml.dump(steps, allow_unicode=True, indent=2,
                     default_flow_style=False, sort_keys=False)


def generate(plan, output_base: str, dry_run: bool = False) -> dict:
    """产出前置（每个编号一个）/ teardown / 每用例 test_data.yaml 到 output_base/<feature_en>/。

    前置文件与 `py_export` 生成的 fixture **同名同源**（`setup_<slug>__<编号>`）：
    文件名主干就是 fixture 名，`py_export` 据此拼 `run_blocks` 的路径。
    """
    feature_en = plan.naming["feature_en"][reg.get_naming()["feature_cn"]]
    feature_dir = os.path.join(output_base, feature_en)
    setup_dir = os.path.join(feature_dir, "setup_data")

    parts = build_setup_parts(plan)
    by_id = {c.case_id: c for c in plan.active_cases}
    ent_groups = list(dict.fromkeys(
        e["group"] for e in park_entities() if e["group"] in groups_in_scope(plan)))

    files = [(os.path.join(setup_dir, f"teardown_{SLUG}.yaml"),
              _dump(build_teardown(plan)))]
    files.append((os.path.join(setup_dir, f"{setup_file_name(PRE_ID_GLOBAL)}.yaml"),
                  _dump(parts["global"])))
    for gid in ent_groups:
        files.append((os.path.join(setup_dir, f"{setup_file_name(pre_id_group(gid))}.yaml"),
                      _dump(parts["group"][gid])))
    for cid, blocks in parts["case"].items():
        files.append((os.path.join(setup_dir,
                                   f"{setup_file_name(pre_id_case(by_id[cid]))}.yaml"),
                      _dump(blocks)))
    for c in plan.active_cases:
        files.append((os.path.join(feature_dir, f"test_{c.title_en}", "test_data.yaml"),
                      _dump(build_case(c, plan))))

    if not dry_run:
        for path, text in files:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8", newline="\r\n") as f:
                f.write(text)
    return {"feature_dir": feature_dir,
            "files": [f for f, _ in files], "count": len(files)}
