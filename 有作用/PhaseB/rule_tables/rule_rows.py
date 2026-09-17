# -*- coding: utf-8 -*-
"""判定表 xlsx → Phase C 行结构（适配器）。

输入：`D:\\0-导出文件\\停车类型和费用计算-规则表.xlsx`
输出（`RuleTablePlan`）：
  * `rows`     —— Phase C Sheet1 行（喂 `py_export._generate_py_file`）
  * `shared`   —— Phase C Sheet2 前置（逐个编号一行：全局 1 + 每组 N + 每例 M）
  * `naming`   —— 翻译缓存（喂 `<excel目录>/translation_cache.json`，**零 LLM**）
  * `cases`    —— 富规格（`CaseSpec`），喂 YAML 确定性生成内核

前置编号由 `yaml_kernel` 命名（`PRE-RT-001` / `PRE-RT-G-<组>` / `PRE-RT-C-<用例>`），
本文件只负责**把每个用例真正需要的那几个编号写进 Sheet1**、把编号清单写进 Sheet2 ——
「某个用例需要哪些前置块」的判据在 `yaml_kernel.case_pre_ids`，不在这里重写一遍。

**不自己发明规则**：条件列→分组走 `registry.match_group`，车辆类型→记录走
`registry.resolve_vehicle_records`，暂留走 `deferred_rule`，命名走 `naming`。
本文件只做「读表 + 按 xlsx 动作列切分步骤 + 组装」。
"""
from dataclasses import dataclass, field

from rule_tables import registry as reg
from rule_tables import yaml_kernel as K

# xlsx 列位（表1 R2 / 表2 R33 表头实测，2026-09-11）
COL_RULE_NO = 1
COL_STRUCT = 3
COL_ACTION_TEXT = 9          # 表1「条件（出入场动作）」
FLAG_COL = {"tempCarIn": 4, "carWhiteOtherIsCasual": 5,
            "otherParkWhiteAccess": 6, "otherParkMonthAccess": 7,
            "specialCarEnable": 8}
T1_HEADER_ROW, T1_FIRST, T1_LAST = 2, 3, 26
T2_HEADER_ROW, T2_FIRST, T2_LAST = 33, 34, 46


@dataclass
class CaseSpec:
    """一条用例的完整规格（YAML 内核据此生成步骤）。"""
    case_id: str                 # 表1-01
    table: str                   # 表1 / 表2
    title_cn: str
    title_en: str
    group_id: str | None         # registry 匹配出的车场分组
    condition_cells: dict        # 已注入表级默认的条件列取值
    vehicles: list[dict]         # [{kind, scope, car}]（空 = 临停车）
    actions: list[dict]          # [{token, car, qualifier}]，按 xlsx 顺序
    asserts: dict                # {动作列名: xlsx 原始取值}
    prepay: str = ""             # 表2 条件列「是否提前缴费」原始取值（表1 恒为空）
    deferred: bool = False
    deferred_reason: str = ""

    @property
    def is_temp_car(self) -> bool:
        return not self.vehicles

    @property
    def trip_note(self) -> str:
        """整趟行程的限定语（如「主场未产生费用」）—— 属于行程而非单步。"""
        return next((a["qualifier"] for a in self.actions if a.get("qualifier")), "")


@dataclass
class RuleTablePlan:
    rows: list[dict] = field(default_factory=list)
    shared: list[dict] = field(default_factory=list)
    naming: dict = field(default_factory=dict)
    cases: list[CaseSpec] = field(default_factory=list)

    @property
    def active_cases(self) -> list[CaseSpec]:
        return [c for c in self.cases if not c.deferred]


# ---------------------------------------------------------------------------
# 读表
# ---------------------------------------------------------------------------

def _sheet(path):
    import openpyxl
    return openpyxl.load_workbook(path, data_only=True).worksheets[0]


def _cell(ws, r, c) -> str:
    v = ws.cell(r, c).value
    return "" if v is None else str(v).strip()


def _table2_header_index(ws) -> dict:
    """表2 表头文本 → 列号（动作列与条件列都靠它定位）。"""
    return {_cell(ws, T2_HEADER_ROW, c): c for c in range(1, 30)
            if _cell(ws, T2_HEADER_ROW, c)}


def _is_deferred(ws, row_idx: int, t2_header: dict) -> bool:
    rule = reg.get_deferred_rule()
    prefix = rule.get("trigger_prefix", "是")
    for col_name in rule.get("columns") or []:
        col = t2_header.get(col_name)
        if col and _cell(ws, row_idx, col).startswith(prefix):
            return True
    return False


# ---------------------------------------------------------------------------
# 动作解析
# ---------------------------------------------------------------------------

def parse_actions(action_text: str) -> list[dict]:
    """xlsx「条件（出入场动作）」→ [{token, car, qualifier}]。

    切分规则来自 `action_parse`：
      * 先剥离括注限定语（如「（主场未产生费用）」）—— 它是标注，不是步骤
      * 按 `step_sep` 切分
      * 剥离 A/B 车辆前缀 → car 槽位
      * 余下文本按 `action_tokens[].aliases` 匹配到 token
    未能匹配的片段原样保留在 `token`，由调用方报错（不静默丢弃）。
    """
    spec = reg.get_action_parse()
    sep = spec.get("step_sep", "-")
    prefixes = spec.get("car_prefixes") or {}
    na = set(spec.get("not_applicable") or [])
    text = (action_text or "").strip()
    if not text or text in na:
        return []

    qualifier = ""
    for q in spec.get("qualifiers") or []:
        if q in text:
            qualifier = q
            text = text.replace(q, "")

    # alias → token（长的优先，避免「变更为临停车」被「出主」类短词误伤）
    alias_map = {}
    for t in reg.get_action_tokens():
        for a in t.get("aliases") or []:
            alias_map[a] = t["token"]

    out = []
    for part in text.split(sep):
        part = part.strip()
        if not part or part in na:
            continue
        car = None
        for p, slot in prefixes.items():
            if part.startswith(p) and len(part) > len(p):
                car = slot
                part = part[len(p):].strip()
                break
        token = alias_map.get(part)
        out.append({"token": token or part, "car": car,
                    "qualifier": qualifier, "raw": part})
    return out


def _unknown_tokens(actions: list[dict]) -> list[str]:
    known = {t["token"] for t in reg.get_action_tokens()}
    return [a["token"] for a in actions if a["token"] not in known]


# ---------------------------------------------------------------------------
# 组装
# ---------------------------------------------------------------------------

def _read_asserts(ws, row_idx: int, table: str) -> dict:
    """读该表的动作列（表1 col10–14 / 表2 靠表头名定位）。"""
    out = {}
    cols = [c for c in reg.get_assert_columns() if c["table"] == table]
    if table == "表1":
        for i, col in enumerate(cols, start=10):
            out[col["column"]] = _cell(ws, row_idx, i)
    else:
        header = _table2_header_index(ws)
        for col in cols:
            key = f"动作（{col['column']}）"
            c = header.get(key)
            if c:
                out[col["column"]] = _cell(ws, row_idx, c)
    return out


def _make_case(ws, row_idx, table, case_id, title_cn, title_en, asserts, t2_header):
    if table == "表1":
        cells = {"struct": _cell(ws, row_idx, COL_STRUCT), "needPay": "否"}
        for k, c in FLAG_COL.items():
            cells[k] = _cell(ws, row_idx, c)
        action_text = _cell(ws, row_idx, COL_ACTION_TEXT)
    else:
        cells = {"struct": _cell(ws, row_idx, COL_STRUCT),
                 "needPay": _cell(ws, row_idx, 2)}
        action_text = _cell(ws, row_idx, t2_header.get("条件（出入场动作）", 6))
    cells = reg.apply_table_defaults(table, cells)

    deferred = table == "表2" and _is_deferred(ws, row_idx, t2_header)
    return CaseSpec(
        case_id=case_id,
        table=table,
        title_cn=title_cn,
        title_en=title_en,
        group_id=reg.match_group(cells),
        condition_cells=cells,
        vehicles=reg.resolve_vehicle_records(_cell(ws, row_idx, 2)
                                             if table == "表1" else "", cells),
        actions=parse_actions(action_text),
        asserts=asserts,
        prepay=(_cell(ws, row_idx, t2_header["条件（是否提前缴费）"])
                if table == "表2" and t2_header.get("条件（是否提前缴费）") else ""),
        deferred=deferred,
        deferred_reason=(reg.get_deferred_rule().get("reason", "") if deferred else ""),
    )


def build_plan(xlsx_path: str, include_deferred: bool = False) -> RuleTablePlan:
    """读判定表 xlsx，产出完整生成计划。

    include_deferred=False（缺省）时按 `deferred_rule` 过滤暂留行；该规则当前触发列
    为空（2026-09-11 券用例解禁后），因此**默认即产出全部 37 条**。
    """
    ws = _sheet(xlsx_path)
    t2_header = _table2_header_index(ws)
    naming = reg.get_naming()
    titles = naming.get("titles") or {}

    cases: list[CaseSpec] = []
    for table, first, last in (("表1", T1_FIRST, T1_LAST),
                               ("表2", T2_FIRST, T2_LAST)):
        for r in range(first, last + 1):
            rid = int(_cell(ws, r, COL_RULE_NO))
            cid = f"{table}-{rid:02d}"
            meta = titles.get(cid)
            if meta is None:
                raise KeyError(f"映射文件 naming.titles 缺 {cid}")
            cases.append(_make_case(
                ws, r, table, cid, meta["cn"], meta["en"],
                _read_asserts(ws, r, table), t2_header))

    # 一致性自检：不静默兜底
    bad_group = [c.case_id for c in cases if c.group_id is None]
    if bad_group:
        raise ValueError(f"以下用例未匹配到车场分组: {bad_group}")
    for c in cases:
        unknown = _unknown_tokens(c.actions)
        if unknown:
            raise ValueError(f"{c.case_id} 存在未登记的动作 token: {unknown}")

    active = [c for c in cases if not c.deferred]
    if include_deferred:
        active = cases

    plan = RuleTablePlan(naming=_to_translation_cache(naming), cases=cases)
    plan.rows = _to_phase_c_rows(active, naming)
    plan.shared = _to_shared_preconditions(plan, active)
    return plan


# ---------------------------------------------------------------------------
# Phase C 结构
# ---------------------------------------------------------------------------

def _to_phase_c_rows(cases: list[CaseSpec], naming: dict) -> list[dict]:
    """Phase C Sheet1 行：epic/feature/story/title/fixture等级/用例编号/前置/步骤/预期。

    `fixture等级` 写 `逐前置` —— `py_export` 据此把每个前置编号挂成独立 fixture
    （别的产品那列是 `class`/`danyuan`，走原分支，产物逐字不变）；
    `preconditions` 逐条列出**该用例真正需要**的编号（见 `yaml_kernel.case_pre_ids`）。
    """
    return [{
        "epic": naming["epic_cn"],
        "feature": naming["feature_cn"],
        "story": naming["story_cn"],
        "title": c.title_cn,
        "fixture_level": K.FIXTURE_LEVEL_PER_PRE,
        "case_id": c.case_id,
        "preconditions": ",".join(K.case_pre_ids(c)),
        "steps": _steps_text(c),
        "expected": _expected_text(c),
    } for c in cases]


def _to_shared_preconditions(plan, cases: list[CaseSpec]) -> list[dict]:
    """Phase C Sheet2：**每个前置编号一行**（全局 1 + 每组 11 + 每例 36 = 48）。

    每行的「详细步骤」由该编号实际那批块的 `api_name` 串成（不手写散文，避免漂移）；
    「依赖前置」是该编号 fixture 要挂的**其它编号**（生成侧落成 fixture 参数）——
    拆成多 fixture 后，原来靠块顺序保证的父子/他场关系只能这样表达：

      * 全部组 + 全部例 → 全局（清场与收费规则必须先有）；
      * P4 → PO（`otherParkWhiteAccessParks` 要 PO 的 parkCode）；
      * S1 → P1（子场要主场 parkCode）。

    「关联用例」= **直接**引用该编号的用例（与 Sheet1 的 `preconditions` 同源）。
    PO 组只被 P4 依赖、没有用例直接引用它，故它的这格是空的。
    """
    parts = K.build_setup_parts(plan)
    by_id = {c.case_id: c for c in cases}

    def linked(pre_id: str) -> str:
        return ",".join(c.case_id for c in cases if pre_id in K.case_pre_ids(c))

    rows = [{
        "id": K.PRE_ID_GLOBAL,
        "name": "全局前置：清场与收费规则",
        "steps": _chain(parts["global"]),
        "expected": "清场成功；主场/子场临时停车费规则创建成功并回读编码",
        "linked_cases": linked(K.PRE_ID_GLOBAL),
        "depends_on": "",
    }]
    for gid, blocks in parts["group"].items():
        pid = K.pre_id_group(gid)
        rows.append({
            "id": pid,
            "name": f"车场组 {gid}：建场与出入口设备",
            "steps": _chain(blocks),
            "expected": "车场创建成功并回读 parkCode；入口/出口设备创建成功",
            "linked_cases": linked(pid),
            "depends_on": ",".join([K.PRE_ID_GLOBAL]
                                   + [K.pre_id_group(g) for g in K.group_deps(gid)]),
        })
    for cid, blocks in parts["case"].items():
        case = by_id[cid]
        pid = K.pre_id_case(case)
        rows.append({
            "id": pid,
            "name": f"用例前置：{cid} " + ("车辆记录" if case.vehicles else "临停车牌"),
            "steps": _chain(blocks),
            "expected": ("车辆记录创建成功并回读编码" if case.vehicles
                         else "取到本次用例专用的随机车牌"),
            "linked_cases": linked(pid),
            "depends_on": ",".join([K.PRE_ID_GLOBAL,
                                    K.pre_id_group(case.group_id)]),
        })
    return rows


def _chain(blocks: list[dict]) -> str:
    """该批块的接口名链（相邻重复合并；不合并非相邻的，保真步骤数）。"""
    names = []
    for st in blocks:
        n = st["baseInfo"]["api_name"]
        if not names or names[-1] != n:
            names.append(n)
    return " -> ".join(names)


def _steps_text(c: CaseSpec) -> str:
    parts = [f"前置车场分组 {c.group_id}"]
    for v in c.vehicles:
        parts.append(f"建{v['kind']}记录({v['scope']}/{v['car']})")
    for a in c.actions:
        parts.append(a["token"] + (f"[{a['car']}]" if a["car"] else ""))
    return " -> ".join(parts)


def _expected_text(c: CaseSpec) -> str:
    items = [f"{k}={v}" for k, v in c.asserts.items() if v and v not in ("\\",)]
    return "; ".join(items) if items else "\\"


def _to_translation_cache(naming: dict) -> dict:
    """翻译缓存形状（`_load_translation_cache` 读 feature_en/story_en/title_en）。"""
    return {
        "feature_en": {naming["feature_cn"]: naming["feature_en"]},
        "story_en": {naming["story_cn"]: naming["story_en"]},
        "title_en": {t["cn"]: t["en"] for t in (naming.get("titles") or {}).values()},
    }
