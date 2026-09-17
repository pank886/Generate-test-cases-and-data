"""判定表映射注册表加载层（照 data_factory/registry.py 模式）。

单一事实源: `rule_tables/parking_rule_map.yaml`
消费方:
  1. 判定表适配器 — `rule_rows.py`（xlsx → Phase C 行结构）
  2. YAML 生成内核 — 车场分组 / 车辆类型配方 / 动作 → 断言
  3. 单元测试 — 遍历注册表生成合法用例，防止注册表与生成器脱节

缓存策略与 data_factory 一致：模块级缓存 + 双检锁 + reset_cache()。
"""
import os
import threading

import yaml

_lock = threading.Lock()
_map_cache: dict | None = None

# 条件列在 xlsx 中的位置（表1）——表2 只有「是否收费 / 子场主场情况」重合
_CONDITION_COLUMNS = (
    "条件（车辆类型）",
    "条件（子场\\主场情况）",
    "条件（车场临停准入开关）",
    "条件（车场是否支持一人多车白名单）",
    "条件（其他车场白名单准入）",
    "条件（其他车场包月车准入）",
    "条件（是否特殊车牌放行）",
)


def _registry_path() -> str:
    return os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "parking_rule_map.yaml")


def reset_cache() -> None:
    """清空缓存（测试/热更新用）。"""
    global _map_cache
    with _lock:
        _map_cache = None


def load_map() -> dict:
    """读盘返回整份映射（带缓存+双检锁）。文件缺失返回空 dict。"""
    global _map_cache
    if _map_cache is not None:
        return _map_cache
    with _lock:
        if _map_cache is not None:
            return _map_cache
        path = _registry_path()
        if not os.path.exists(path):
            _map_cache = {}
            return _map_cache
        with open(path, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f)
        _map_cache = raw if isinstance(raw, dict) else {}
        return _map_cache


# ---------------------------------------------------------------------------
# 分节读取
# ---------------------------------------------------------------------------

def get_meta() -> dict:
    return load_map().get("meta") or {}


def get_constants() -> dict:
    return load_map().get("constants") or {}


def get_park_template() -> dict:
    return load_map().get("park_template") or {}


def get_pay_rule_template() -> dict:
    return load_map().get("pay_rule_template") or {}


def get_billing() -> dict:
    return load_map().get("billing") or {}


def get_park_groups() -> list[dict]:
    return [g for g in (load_map().get("park_groups") or []) if isinstance(g, dict)]


def get_matchable_groups() -> list[dict]:
    """参与规则匹配的分组（排除 matchable: false 的基础设施车场，如 PO）。"""
    return [g for g in get_park_groups() if g.get("matchable", True)]


def get_group(group_id: str) -> dict | None:
    return next((g for g in get_park_groups() if g.get("id") == group_id), None)


def get_park_flags() -> list[dict]:
    return [f for f in (load_map().get("park_flags") or []) if isinstance(f, dict)]


def get_vehicle_kinds() -> dict:
    """车辆记录配方：kind → {add, readback, delete}。"""
    return load_map().get("vehicle_kinds") or {}


def get_vehicle_recipes() -> list[dict]:
    """xlsx 车辆类型 → 记录列表 的推导规则（有序，先匹配先得）。"""
    return [r for r in (load_map().get("vehicle_recipes") or []) if isinstance(r, dict)]


def get_action_parse() -> dict:
    return load_map().get("action_parse") or {}


def get_deferred_rule() -> dict:
    return load_map().get("deferred_rule") or {}


def get_naming() -> dict:
    """Phase C 命名：feature_en / story_en / title_en（写进 translation_cache.json）。"""
    return load_map().get("naming") or {}


def get_action_tokens() -> list[dict]:
    return [t for t in (load_map().get("action_tokens") or []) if isinstance(t, dict)]


def get_assert_columns() -> list[dict]:
    return [c for c in (load_map().get("assert_columns") or []) if isinstance(c, dict)]


def get_assert_value_map() -> dict:
    return load_map().get("assert_value_map") or {}


def get_pay_actions() -> dict:
    """缴费动作配方：prepay / when_leave → {api, name, body}。"""
    return load_map().get("pay_actions") or {}


def get_coupon_actions() -> dict:
    """券核销配方：{api, name, body}（`mock/addCouponAndUse`，填写即创建+核销）。"""
    return load_map().get("coupon_actions") or {}


def get_coupon_expectation() -> dict:
    """逐例券配置：case_id → [{type, data}, …]（金额券在前、时长券在后）。"""
    return load_map().get("coupon_expectation") or {}


def get_assert_endpoints() -> dict:
    """表2 断言接口（bill_latest / bill_unpaid / car_type_query）。"""
    return load_map().get("assert_endpoints") or {}


def get_parking_type(kind: str) -> str | None:
    """车辆类型 → `mock/carTypeQuery` 期望的 parkingType（api 文档口径）。

    1包月车 2临停车 3无牌 4白名单 5黑名单
    未登记的 kind 返回 None（调用方必须报错，不得静默兜底）。
    """
    return (get_vehicle_kinds().get(kind) or {}).get("parking_type")


def get_fee_expectation() -> dict:
    return load_map().get("fee_expectation") or {}


def get_time_format() -> dict:
    """时间构造格式（code = 框架格式码；base_date/base_hms = 字面量基准）。"""
    return load_map().get("time_format") or {}


def get_timeline() -> dict:
    """逐例时间线：case_id → [字面量时刻]。

    **按位置对应**：第 i 个时刻 = 该用例第 i 个动作（`case.actions` 顺序即判定表
    c9「条件（出入场动作）」的顺序）。个数必须等于动作数，日期必须是
    `time_format.base_date` —— 两条都由 `yaml_kernel._times_for` 强制。
    """
    return load_map().get("timeline") or {}


def get_deferred_table2_rules() -> list:
    return list(get_meta().get("deferred_table2_rules") or [])


def get_table_defaults() -> dict:
    """表级条件列默认值，如 {"表2": {"tempCarIn": "是"}}。"""
    return load_map().get("table_defaults") or {}


def apply_table_defaults(table: str, condition_cells: dict) -> dict:
    """把某表的默认条件值补进 condition_cells（不覆盖显式给出的值）。

    表2 的 D–H 列全为 `\\`，但语义是「默认临停车」→ 需 tempCarIn=是 才能进场。
    这条规则维护在 `table_defaults`，不写死在适配器里。
    """
    merged = dict(get_table_defaults().get(table) or {})
    merged.update({k: v for k, v in condition_cells.items() if v not in (None, "")})
    return merged


def get_flag(key: str) -> dict | None:
    """按 key 取条件列定义（carInConfig 字段 + 取值映射）。"""
    return next((f for f in get_park_flags() if f.get("key") == key), None)


def condition_columns() -> tuple:
    return _CONDITION_COLUMNS


# ---------------------------------------------------------------------------
# 分组匹配
# ---------------------------------------------------------------------------

def _normalize_struct(cell: str) -> str:
    """xlsx「子场\\主场情况」取值归一：主场 / 子场 / 主+子。"""
    s = (cell or "").strip()
    if "子场" in s and "主场" in s:
        return "主+子"
    if s.startswith("子场"):
        return "子场"
    return "主场"


def match_group(condition_cells: dict) -> str | None:
    """把一条规则的条件列取值匹配到车场分组 id。

    condition_cells: {flag_key 或 'struct'/'needPay': 取值}，取值来自 xlsx 原始文本
                     （是/否/主场/子场/主场下有子场），未列出的 flag 视为「否」。

    匹配规则：结构 + 各个 flag 的「是否」+ 是否收费，全部一致才命中。
    返回分组 id；无命中返回 None（由调用方报错，不静默兜底）。
    """
    struct = _normalize_struct(condition_cells.get("struct", "主场"))
    want = {}
    for f in get_park_flags():
        key = f.get("key")
        if key == "needPay":
            continue
        raw = condition_cells.get(key)
        want[key] = bool((f.get("values") or {}).get(raw, False))
    need_pay = bool((get_flag("needPay") or {}).get("values", {})
                    .get(condition_cells.get("needPay"), False) == 1
                    or condition_cells.get("needPay") == "是")

    for group in get_matchable_groups():
        parks = group.get("parks") or []
        roles = [p.get("role") for p in parks]
        g_struct = ("主+子" if "sub" in roles and "main" in roles
                    else ("子场" if roles == ["sub"] else "主场"))
        if g_struct != struct:
            continue
        main = next((p for p in parks if p.get("role") == "main"), None) or parks[0]
        if bool(main.get("need_pay")) != need_pay:
            continue
        gmatch = {k: bool(v) for k, v in (main.get("match") or {}).items()}
        # 未在 match 中列出的 flag 视为 false
        if all(want.get(k, False) == gmatch.get(k, False) for k in want):
            return group.get("id")
    return None


def get_park_spec(group_id: str, park_code: str, park_name: str,
                  pay_code: str | None = None,
                  other_park_codes: dict | None = None,
                  role: str = "main",
                  parent_code: str = "") -> dict:
    """把分组定义 + 模板 + 具体编码渲染成 `parkingPark/saveOrUpdate` 的 json。

    park_code/park_name 需由调用方先回读/生成；pay_code 为回读的收费规则编码。
    other_park_codes：{其他车场 id: 已回读的 parkCode}，用于填 `*AccessParks`
    （如 P4 的 otherParkWhiteAccessParks 需要 PO 的 parkCode）。
    role/parent_code：主+子分组要渲染两次，子场需 `mainPark=false` + `parentCode`。
    占位替换（<G>/<C>/<n>）由生成内核负责，这里只做组装。
    """
    import copy
    group = get_group(group_id)
    if group is None:
        raise KeyError(f"未知车场分组: {group_id}")
    park = next((p for p in group["parks"] if p.get("role") == role), None)
    if park is None:
        raise KeyError(f"分组 {group_id} 无 role={role} 的车场定义")

    body = copy.deepcopy(get_park_template())
    for f in get_park_flags():
        key = f.get("key")
        if key == "needPay":
            # needPay 语义为「仅主场」→ 子场恒 0
            body["parkingPark"]["needPay"] = (
                1 if (park.get("need_pay") and role != "sub") else 0)
            continue
        field = f.get("field", "")
        _, _, leaf = field.partition(".")
        body["carInConfig"][leaf] = bool((park.get("match") or {}).get(key, False))

    # 他场准入：把上游车场编码写进对应的 *AccessParks（逗号分隔）
    codes = other_park_codes or {}
    for field, group_ids in (park.get("other_parks") or {}).items():
        rendered = [codes.get(g, "") for g in group_ids]
        body["carInConfig"][field] = ",".join(c for c in rendered if c)

    body["parkingPark"]["parkCode"] = park_code
    body["parkingPark"]["parkName"] = park_name
    body["parkingPark"]["mainPark"] = role != "sub"
    body["parkingPark"]["parentCode"] = parent_code
    body["parkingPark"]["bindPayCode"] = pay_code or ""
    body["carInConfig"]["parkCode"] = park_code

    # 特殊车牌：随车场配置写入顶层 specialAccessConfigList（仅在开启放行的车场）
    if body["carInConfig"]["specialCarEnable"]:
        entry = copy.deepcopy((get_vehicle_kinds().get("特殊车牌") or {})
                              .get("add", {}).get("entry") or {})
        if entry:
            body["specialAccessConfigList"] = [entry]
    return body


# ---------------------------------------------------------------------------
# 车辆记录推导（xlsx 车辆类型 → [{kind, scope, car}]）
# ---------------------------------------------------------------------------

def _scope_from_struct(struct_cell: str) -> str:
    """未带括注时，作用域由「子场\\主场情况」决定。"""
    s = (struct_cell or "").strip()
    if s.startswith("子场"):
        return "sub"
    return "main"


def _flag_is_yes(flag_key: str, condition_cells: dict) -> bool:
    flag = get_flag(flag_key) or {}
    return bool((flag.get("values") or {}).get(condition_cells.get(flag_key), False))


def _recipe_hits(recipe: dict, text: str) -> bool:
    all_of = recipe.get("all_of")
    if all_of and not all(k in text for k in all_of):
        return False
    any_of = recipe.get("any_of")
    if any_of and not any(k in text for k in any_of):
        return False
    return True


def _pick_kind(recipe: dict, text: str) -> str | None:
    for k in recipe.get("kind_from") or []:
        if k in text:
            return k
    return None


def resolve_vehicle_records(vehicle_cell: str, condition_cells: dict) -> list[dict]:
    """把 xlsx「条件（车辆类型）」取值解析成车辆记录列表。

    返回 [{kind, scope, car, sub_of}]，按 vehicle_recipes 顺序取**首个命中**的规则：
      kind  : 车辆类型（白名单 / 包月车 / 特殊车牌）；空列表 = 临停车（无记录）
      scope : main | sub | both | other
      car   : car_a | car_b
      sub_of: 非空 → 该 car 是 car_a 那条记录的**副车牌**（随同一次 add 写入），
              不单独建记录。见 map 的 vehicle_recipes 注释与实证说明。
    """
    text = (vehicle_cell or "").strip()
    for recipe in get_vehicle_recipes():
        if not _recipe_hits(recipe, text):
            continue
        kind = _pick_kind(recipe, text)
        out = []
        for rec in recipe.get("records") or []:
            scope = rec.get("scope")
            if scope == "from_struct":
                scope = _scope_from_struct(condition_cells.get("struct"))
            out.append({"kind": rec.get("kind") or kind,
                        "scope": scope,
                        "car": rec.get("car"),
                        "sub_of": rec.get("sub_of")})
        return out
    return []
