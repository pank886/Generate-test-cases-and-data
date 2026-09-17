"""YAML 数据生成检测（2026-09-01 校验包归位重构）。

合并自 agent_components/post_validator.py（YAML 生成后快速验证）与
_repair_helpers.py 迁移的生成后校验函数（_find_missing_yaml_refs 引用完整性 /
_scan_missing_key_refs D4 缺失键扫描），语义归属「YAML 数据生成检测」。

纯代码，不放 LLM。产出结构化错误信息，可被 _run_yaml_rounds 修复轮直接消费。
"""

import glob
import os
import re

import yaml

from infrastructure.observability import get_logger

logger = get_logger(__name__)

_PLACEHOLDER_RE = re.compile(r'\$\{[^}]+\}')
_VALID_OPS = {"eq", "contains", "ne", "db"}
_GET_EXTRACT_RE = re.compile(r"get_extract_data\(\s*['\"]([^'\"]+)['\"]\s*\)")


class YamlPostValidator:
    """YAML 生成后快速验证器。

    每个检查项返回统一结构:
      {yaml_path, check, severity, line, current, expected, fix_hint}
    """

    # ---- 公共入口 ----

    def validate_all(self, output_dir: str) -> list[dict]:
        """遍历所有 YAML 文件，执行全部注册的检查项。

        形状兼容（2026-09-10 修复）：生成器实际写盘为**顶层 list**，
        而此处原只认 {data: [...]}，导致全部文件被 continue、4 个检查项休眠。
        现两种形状都接受（与 _scan_missing_key_refs 的说明同源）。
        """
        issues: list[dict] = []
        pattern = os.path.join(output_dir, "**", "*.yaml")
        yaml_files = glob.glob(pattern, recursive=True)
        for path in yaml_files:
            try:
                with open(path, encoding="utf-8") as f:
                    data = yaml.safe_load(f) or {}
            except Exception:
                continue
            if isinstance(data, dict):
                steps = data.get("data")
            elif isinstance(data, list):
                steps = data
            else:
                continue
            if not isinstance(steps, list):
                continue
            for step in steps:
                if not isinstance(step, dict):
                    continue
                issues.extend(self._check_delete_body_wrapper(step, path))
                issues.extend(self._check_assertion_dynamic_key(step, path))
                issues.extend(self._check_assertion_op_key(step, path))
                issues.extend(self._check_malformed_assertion(step, path))
        return issues

    # ---- 检查 1：delete body 包裹检测 ----

    def _check_delete_body_wrapper(self, step: dict, yaml_path: str) -> list[dict]:
        """检测 json: {body: [...]} 包裹层误用。

        触发条件（四条同时满足）：
          - method 为 post/put/patch
          - json 中存在且仅有 body 一个 key
          - json.body 为非空数组
          - 数组元素为 dict（有 key，非简单值）
        """
        issues = []
        base_info = step.get("baseInfo")
        if not isinstance(base_info, dict):
            return issues
        method = str(base_info.get("method", "")).lower()
        if method not in ("post", "put", "patch"):
            return issues

        for tc in step.get("testCase") or []:
            body = tc.get("json")
            if not isinstance(body, dict):
                continue
            if set(body.keys()) != {"body"}:
                continue
            inner = body.get("body")
            if not isinstance(inner, list) or not inner:
                continue
            if not all(isinstance(item, dict) and item for item in inner):
                continue
            issues.append({
                "yaml_path": yaml_path,
                "check": "delete_body_wrapper",
                "severity": "P0",
                "line": 0,
                "current": f"json: {{body: [{len(inner)} items]}}",
                "expected": f"json: [{len(inner)} items]",
                "fix_hint": "数组 body 直接用 json: [...]，去掉 body 包裹层",
            })
        return issues

    # ---- 检查 2：断言 key 动态值检测 ----

    def _check_assertion_dynamic_key(self, step: dict, yaml_path: str) -> list[dict]:
        """检测 validation 中 key 位置使用了 ${} 模板变量。

        正则匹配 ${{xxx}} 模板变量，不误伤 $.data.xxx JSONPath。
        """
        issues = []
        for tc in step.get("testCase") or []:
            validation = tc.get("validation") or []
            for item in validation:
                if not isinstance(item, dict):
                    continue
                for key in item.keys():
                    if _PLACEHOLDER_RE.search(key):
                        issues.append({
                            "yaml_path": yaml_path,
                            "check": "assertion_dynamic_key",
                            "severity": "P1",
                            "line": 0,
                            "current": f"{key}: ...",
                            "expected": f"$.data.xxx: ${{{_PLACEHOLDER_RE.search(key).group(0)}}}",
                            "fix_hint": "断言的 key 必须是静态 JSONPath（如 $.data.code），动态值放在 : 右边",
                        })
        return issues

    # ---- 检查 3：断言块键白名单检测 ----

    def _check_assertion_op_key(self, step: dict, yaml_path: str) -> list[dict]:
        """检测 validation 块键不是合法运算符（eq/contains/ne/db）。

        P0 兜底：response_model 层已拦截的畸形块若仍落盘，在此标记，
        供修复轮消费。块键必须是运算符，JSONPath 写在操作数 dict 的键位。
        """
        issues = []
        for tc in step.get("testCase") or []:
            validation = tc.get("validation") or []
            for item in validation:
                if not isinstance(item, dict):
                    continue
                for key in item.keys():
                    if key not in _VALID_OPS:
                        issues.append({
                            "yaml_path": yaml_path,
                            "check": "assertion_op_key",
                            "severity": "P0",
                            "line": 0,
                            "current": f"{key}: ...",
                            "expected": "块键必须是运算符 eq/contains/ne/db 之一",
                            "fix_hint": "断言块键必须是运算符，JSONPath 写在操作数 dict 的键位，"
                                        "如 {eq: {$.code: 1}}，禁止 {$.retCode: {eq: 1}} 等写法",
                        })
        return issues

    # ---- 检查 4：断言格式拼合检测 ----

    def _check_malformed_assertion(self, step: dict, yaml_path: str) -> list[dict]:
        """检测 validation 中 key 或 value 有未配对的引号。

        仅告警不修复——LLM 没有明确的 expected 值，修复轮可能越修越错。
        """
        issues = []
        for tc in step.get("testCase") or []:
            validation = tc.get("validation") or []
            for item in validation:
                if not isinstance(item, dict):
                    continue
                for key, value in item.items():
                    if isinstance(key, str) and self._has_unmatched_quotes(key):
                        issues.append({
                            "yaml_path": yaml_path,
                            "check": "malformed_assertion",
                            "severity": "P2",
                            "line": 0,
                            "current": key,
                            "expected": "修复引号配对",
                            "fix_hint": "检查 assertion key 的引号是否配对（仅告警，不自动修复）",
                        })
                    if isinstance(value, str) and self._has_unmatched_quotes(value):
                        issues.append({
                            "yaml_path": yaml_path,
                            "check": "malformed_assertion",
                            "severity": "P2",
                            "line": 0,
                            "current": value,
                            "expected": "修复引号配对",
                            "fix_hint": "检查 assertion value 的引号是否配对（仅告警，不自动修复）",
                        })
        return issues

    @staticmethod
    def _has_unmatched_quotes(s: str) -> bool:
        """检测字符串中是否有未配对的引号（忽略转义）。"""
        single = s.count("'") - s.count("\\'")
        double = s.count('"') - s.count('\\"')
        return single % 2 == 1 or double % 2 == 1


# ==================== 生成后引用/缺失键校验（迁移自 _repair_helpers.py） ====================


def _find_missing_yaml_refs(output_base: str, project_root: str) -> list:
    """扫描 output_base 下所有 test_*.py 引用的 yaml，返回磁盘缺失清单。

    引用路径形如 './testcase/<batch>/<feature>/<func>/test_data.yaml'
    （pytest 从 project_root 运行），磁盘解析为 project_root + 相对路径。
    2026-08-12 问题 3：_27 曾出现 .py 引用存在但磁盘缺失（空目录）未被拦截，
    此处接入生成收尾，缺失文件禁止静默放行。
    """
    refs = []
    for dp, _dn, fn in os.walk(output_base):
        for f in fn:
            if f.endswith(".py") and f.startswith("test_"):
                _path = os.path.join(dp, f)
                refs.extend(re.findall(r"'\./([^']+\.yaml)'",
                                       open(_path, encoding="utf-8").read()))
    missing = []
    for r in sorted(set(refs)):
        if not os.path.exists(os.path.join(project_root, r)):
            missing.append(r)
    return missing


# ==================== V1–V5：车场绑定值形态校验（停车模块，2026-09-10） ====================
#
# 背景：智慧停车 Round 3 定量 —— parkingPark/saveOrUpdate 79 次调用中 47 次失败，
# 其中 44 次同属一条根因：bindPayCode 最终没有以「标量编码」形态送达服务端。
#   B 类 28 次 字面量 'RULE-HOME'/'RULE-CHILD'（回退成测试计划里的规则名）  → V2
#   D 类 10 次 空串 ''                                                    → V1
#   E 类  4 次 extract_list 产出的列表被 str() 降级成 "['zhyqC…']"        → V5
#   其余        键未经回读声明 / 缺整条读回链                              → V3 / V4
#
# 标定（拿 Round 3 真实产物 47 个 setup 跑）：命中 20/20 个失败 setup，0 误报，0 漏报。
# 注意 V1 闸门是 mainPark 或 needPay（实测 mainPark=1/needPay=0 且 bindPayCode='' 同样被
# 服务端拒："主场类型必须要绑定计费"）；V2 不再加闸门——mainPark=0 时绑字面量照样失败。
#
# V6（把 extract_list 键的索引检查泛化到全模块全字段）本轮只登记不实现。

_BIND_CHAIN = {
    "bind_url_suffix": "/parkingPark/saveOrUpdate",
    "parent": "parkingPark",
    "field": "bindPayCode",
    "main_flag": "mainPark",
    "need_pay_flag": "needPay",
    "rule_add_suffix": "/parkingPaymentConfig/add",
    "rule_page_suffix": "/parkingPaymentConfig/getParkingPaymentConfigPage",
}

_EXTRACT_BLOCKS = ("input_extract", "extract", "extract_list")
_EXTRACT_LIST_BLOCK = "extract_list"
_TRUTHY = (True, "true", "True", 1, "1")

_GET_EXTRACT_QUOTED_RE = re.compile(
    r"\$\{\s*get_extract_data\(\s*['\"]([^'\"]+)['\"]\s*(?:,\s*([^)]*?)\s*)?\)\s*\}")
_SPLIT_EXTRACT_RE = re.compile(
    r"\$\{\s*split_extract_data\(\s*['\"]([^'\"]+)['\"]\s*(?:,\s*([^)]*?)\s*)?\)\s*\}")


def _bind_issue(yaml_path: str, check: str, step_idx: int, case_name: str,
                current: str, expected: str, fix_hint: str) -> dict:
    """V1–V5 统一问题结构（与 validate_all 同构，便于共用修复轮通道）。"""
    return {
        "yaml_path": yaml_path,
        "check": check,
        "severity": "P0",
        "line": 0,
        "current": f"step#{step_idx} {current}",
        "expected": expected,
        "fix_hint": fix_hint,
        "case_name": case_name,
    }


def _scan_extract_value_forms(output_base: str) -> list:
    """V1–V5：车场绑定值 bindPayCode 形态校验（纯代码）。

    直读生成器实际写盘的**顶层 list**，不经 YamlPostValidator.validate_all ——
    后者只认 {data: [...]} 形状，对顶层 list 全部 continue（与 _scan_missing_key_refs
    记录的是同一个格式错位）。且 V4 要看整份 setup 的步骤序列，逐 step 检查看不到。

    返回结构同 validate_all，P0 由 yaml_gen 的追加修复轮直接消费。
    """
    issues: list[dict] = []
    pattern = os.path.join(output_base, "**", "setup_*.yaml")
    for fp in sorted(glob.glob(pattern, recursive=True)):
        try:
            with open(fp, encoding="utf-8") as f:
                raw = yaml.safe_load(f)
        except Exception:
            continue
        steps = raw.get("data") if isinstance(raw, dict) else raw
        if not isinstance(steps, list):
            continue
        issues.extend(_check_bind_forms_in_setup(steps, fp))
    return issues


def _check_bind_forms_in_setup(steps: list, yaml_path: str) -> list:
    """单份 setup 的 V1–V5 判定。步骤序列与提取声明先预扫，再逐绑定步检查。"""
    declared: dict = {}   # 提取键 -> (声明步序, 声明块名)，取首次声明
    urls: list = []       # 步序 -> url
    for i, step in enumerate(steps):
        if not isinstance(step, dict):
            urls.append("")
            continue
        urls.append(str((step.get("baseInfo") or {}).get("url", "")))
        for tc in step.get("testCase") or []:
            if not isinstance(tc, dict):
                continue
            for blk in _EXTRACT_BLOCKS:
                for key in (tc.get(blk) or {}):
                    declared.setdefault(key, (i, blk))

    cfg = _BIND_CHAIN
    issues: list[dict] = []
    for i, step in enumerate(steps):
        if not isinstance(step, dict) or not urls[i].endswith(cfg["bind_url_suffix"]):
            continue
        for tc in step.get("testCase") or []:
            if not isinstance(tc, dict):
                continue
            parent = (tc.get("json") or {}).get(cfg["parent"])
            if not isinstance(parent, dict):
                continue
            case_name = tc.get("case_name", "")
            value = parent.get(cfg["field"])

            # ---- V1：主场/开计费时绑定值不得缺失或为空串 ----
            if value is None or str(value).strip() == "":
                if (parent.get(cfg["main_flag"]) in _TRUTHY
                        or parent.get(cfg["need_pay_flag"]) in _TRUTHY):
                    issues.append(_bind_issue(
                        yaml_path, "bind_pay_code_empty", i, case_name,
                        f"{cfg['parent']}.{cfg['field']} 为空"
                        f"（{cfg['main_flag']}={parent.get(cfg['main_flag'])!r},"
                        f" {cfg['need_pay_flag']}={parent.get(cfg['need_pay_flag'])!r}）",
                        f"必须是 ${{get_extract_data('键')}} / ${{split_extract_data('键', 索引)}} 形态",
                        "主场或已开计费的车场必须绑经查询回读到的真实收费规则编码，"
                        "不得留空。服务端会拒：主场类型必须要绑定计费"))
                continue

            # ---- V2：非空时必须是带引号的提取引用，不得字面量 ----
            quoted = _GET_EXTRACT_QUOTED_RE.search(str(value))
            m = quoted or _SPLIT_EXTRACT_RE.search(str(value))
            if m is None:
                is_bare = ("get_extract_data(" in str(value)
                           or "split_extract_data(" in str(value))
                kind = "裸键引用（键未加引号）" if is_bare else "字面量"
                issues.append(_bind_issue(
                    yaml_path, "bind_pay_code_literal", i, case_name,
                    f"{cfg['parent']}.{cfg['field']} = {value!r}（{kind}）",
                    f"${{get_extract_data('键')}} / ${{split_extract_data('键', 索引)}}",
                    "绑定值必须引用由查询回读步骤提取的键，不得写规则名等字面量，"
                    "键名必须带用例前缀且加引号" if is_bare else
                    "绑定值必须是提取引用，不得写规则名/编码字面量（服务端会拒："
                    "绑定的临停收费规则不存在或类型不正确）"))
                continue

            key = m.group(1)
            arg = (m.group(2) or "").strip()

            # ---- V3：键必须由更早步骤声明 ----
            loc = declared.get(key)
            if loc is None:
                issues.append(_bind_issue(
                    yaml_path, "bind_pay_code_undeclared", i, case_name,
                    f"引用键 {key!r} 在本 setup 全文件未声明",
                    f"键需由更早步骤的 {'/'.join(_EXTRACT_BLOCKS)} 声明",
                    "引用了一个无人产出的键，运行时必 KeyError。需补回读步骤或改用已声明的键"))
                continue
            decl_i, decl_blk = loc
            if decl_i >= i:
                issues.append(_bind_issue(
                    yaml_path, "bind_pay_code_undeclared", i, case_name,
                    f"引用键 {key!r} 声明于 step#{decl_i}，不早于引用步 step#{i}",
                    "声明步必须早于引用步",
                    "提取键必须先声明后引用，需调整步骤顺序"))
                continue

            # ---- V4：声明步必须是分页回读步，且其之前存在规则 add 步 ----
            # 不能用「第一个分页步」比对——一个 setup 可含多组 add/回读对
            if not urls[decl_i].endswith(cfg["rule_page_suffix"]):
                issues.append(_bind_issue(
                    yaml_path, "bind_pay_code_no_readback", i, case_name,
                    f"引用键 {key!r} 由 step#{decl_i}({decl_blk}) 声明，该步不是分页回读步",
                    f"键需由 {cfg['rule_page_suffix']} 步骤的响应提取声明",
                    "绑定值必须取自收费规则分页查询的回读结果，不能来自其它步骤的响应"))
                continue
            if not any(u.endswith(cfg["rule_add_suffix"]) for u in urls[:decl_i]):
                issues.append(_bind_issue(
                    yaml_path, "bind_pay_code_no_readback", i, case_name,
                    f"引用键 {key!r} 的回读步 step#{decl_i} 之前没有规则添加步",
                    f"回读步之前需有 {cfg['rule_add_suffix']} 步",
                    "缺整条读回链：需先添加收费规则、再分页查询回读其编码，"
                    "否则读回的是历史规则甚至空列表"))
                continue

            # ---- V5：extract_list 键的 get_extract_data 引用必须带索引 ----
            # split_extract_data 默认 index=0，裸用安全，不在此列
            if decl_blk == _EXTRACT_LIST_BLOCK and quoted and not arg:
                issues.append(_bind_issue(
                    yaml_path, "bind_pay_code_missing_index", i, case_name,
                    f"引用键 {key!r} 源自 {_EXTRACT_LIST_BLOCK}，但引用无索引",
                    f"${{get_extract_data('{key}', 1)}} 或 ${{split_extract_data('{key}', 0)}}",
                    "extract_list 产出的是列表，裸引用会被框架替换层 str() 成 \"['…']\" "
                    "字符串静默失真，必须带索引取标量"))
    return issues


def _scan_missing_key_refs(output_base: str, setup_keys: dict) -> list:
    """D4 后校验（就地实现，2026-08-27 决策选项 1，不走 validate_all）。

    扫描 test/teardown YAML（跳过 setup_*.yaml）中引用 __MISSING_KEY__ 的用例
    → P1（需人工复核，不进修复轮——根因是 setup 生成失败，重生成无用）。
    避开 2026-08-27 发现的后校验格式错位：validate_all 只处理 {data:[...]} dict，
    而生成器写盘为顶层 list，故此处直接遍历 list 格式产物。
    """
    missing = {k for entry in setup_keys.values()
               for k, v in entry["keys"].items() if v == "__MISSING_KEY__"}
    if not missing:
        return []
    issues = []
    for fp in glob.glob(os.path.join(output_base, "**", "*.yaml"), recursive=True):
        rel = os.path.relpath(fp, output_base).replace("\\", "/")
        if "/setup_data/setup_" in rel:
            continue  # setup 自身不引用提取键
        try:
            data = yaml.safe_load(open(fp, encoding="utf-8"))
        except Exception:
            continue
        for block in data or []:
            for tc in block.get("testCase", []):
                cn = tc.get("case_name", "")
                text = str(tc.get("json") or "") + str(tc.get("validation") or "")
                for key in _GET_EXTRACT_RE.findall(text):
                    if key in missing:
                        issues.append({
                            "yaml_path": fp,  # 绝对/glob 路径，与 validate_all 一致
                            "check": "missing_extract_key",
                            "severity": "P1",
                            "line": 0,
                            "current": f"get_extract_data('{key}')",
                            "expected": "setup 提取失败，需人工复核（改硬编码占位值或改断言）",
                            "fix_hint": "D4: setup 未提取到该键（__MISSING_KEY__），该用例需人工复核修正",
                            "case_name": cn,
                        })
    return issues
