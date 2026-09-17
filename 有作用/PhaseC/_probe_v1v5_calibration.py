"""V1–V5 校验规则标定探针（2026-09-10）。

目的：在把 _scan_extract_value_forms 写进 yaml_validator.py 之前，
先用真实产物（智慧停车 Round 3 的 47 个 setup）跑一遍规则，
确认【命中面】与【误报面】——正在通过的 setup 不得被判失败。

用法：
  python 有作用/PhaseC/_probe_v1v5_calibration.py
"""

import glob
import os
import re
import sys

import yaml

SETUP_DIR = (r"C:\Users\damai\PyCharmMiscProject\testcase\园区基线\智慧停车"
             r"\SmartParking\setup_data")

# ---- 绑定链配置（V1–V5 的适用范围，V6 泛化时再扩展）----
BIND_URL_SUFFIX = "/parkingPark/saveOrUpdate"
RULE_ADD_SUFFIX = "/parkingPaymentConfig/add"
RULE_PAGE_SUFFIX = "/parkingPaymentConfig/getParkingPaymentConfigPage"
BIND_PARENT = "parkingPark"
BIND_FIELD = "bindPayCode"
NEED_PAY_FIELD = "needPay"

# ${get_extract_data('key')} / ${get_extract_data('key', N)}
_GET_RE = re.compile(
    r"\$\{\s*get_extract_data\(\s*['\"]([^'\"]+)['\"]\s*(?:,\s*([^)]*?)\s*)?\)\s*\}")
_SPLIT_RE = re.compile(
    r"\$\{\s*split_extract_data\(\s*['\"]([^'\"]+)['\"]\s*(?:,\s*([^)]*?)\s*)?\)\s*\}")
_EXTRACT_BLOCKS = ("input_extract", "extract", "extract_list")


def _scan(path):
    """返回该 setup 的 V1–V5 命中项。"""
    steps = yaml.safe_load(open(path, encoding="utf-8")) or []
    if not isinstance(steps, list):
        return []

    # 预扫：按文件顺序建「键 -> (声明步序, 声明块名)」
    declared = {}
    url_seq = []
    for i, step in enumerate(steps):
        if not isinstance(step, dict):
            continue
        url = str((step.get("baseInfo") or {}).get("url", ""))
        url_seq.append(url)
        for tc in step.get("testCase") or []:
            if not isinstance(tc, dict):
                continue
            for blk in _EXTRACT_BLOCKS:
                for key in (tc.get(blk) or {}):
                    declared.setdefault(key, (i, blk))

    hits = []
    for i, step in enumerate(steps):
        if not isinstance(step, dict):
            continue
        url = str((step.get("baseInfo") or {}).get("url", ""))
        if not url.endswith(BIND_URL_SUFFIX):
            continue
        for tc in step.get("testCase") or []:
            if not isinstance(tc, dict):
                continue
            pp = ((tc.get("json") or {}).get(BIND_PARENT)) or {}
            if not isinstance(pp, dict):
                continue
            case = tc.get("case_name", "")
            raw = pp.get(BIND_FIELD)

            # ---- V1：主场或开计费时，bindPayCode 必须存在且非空 ----
            # 闸门是 mainPark（服务端：主场类型必须要绑定计费），不止 needPay
            if raw is None or str(raw).strip() == "":
                if pp.get("mainPark") in (True, "true", "True", 1, "1") \
                        or pp.get(NEED_PAY_FIELD) in (True, "true", "True", 1, "1"):
                    hits.append(("V1", i, case, f"mainPark={pp.get('mainPark')} "
                                                f"needPay={pp.get(NEED_PAY_FIELD)} 但 bindPayCode 为空"))
                continue

            text = str(raw)
            gm = _GET_RE.search(text)
            sm = _SPLIT_RE.search(text)

            # ---- V2：非空时必须是带引号的提取引用形态，不得字面量 ----
            if not (gm or sm):
                kind = "裸键引用（键未加引号）" if "get_extract_data(" in text or \
                    "split_extract_data(" in text else "字面量"
                hits.append(("V2", i, case, f"{kind}: {raw}"))
                continue

            m = gm or sm
            key, arg = m.group(1), (m.group(2) or "").strip()

            # ---- V3：键必须由更早步骤声明 ----
            loc = declared.get(key)
            if loc is None:
                hits.append(("V3", i, case, f"key={key} 全文件未声明"))
                continue
            decl_i, decl_blk = loc
            if decl_i >= i:
                hits.append(("V3", i, case, f"key={key} 声明步 {decl_i} 不早于引用步 {i}"))
                continue

            # ---- V4：声明步必须是分页回读步，且其之前有 add 步 ----
            # 修正：不能用 next() 取「第一个」page 步——一个 setup 可有多组 add/page 对
            decl_url = url_seq[decl_i] if decl_i < len(url_seq) else ""
            if not decl_url.endswith(RULE_PAGE_SUFFIX):
                hits.append(("V4", i, case, f"key={key} 由步 {decl_i} 声明，该步非分页回读步"))
                continue
            has_prior_add = any(u.endswith(RULE_ADD_SUFFIX) for u in url_seq[:decl_i])
            if not has_prior_add:
                hits.append(("V4", i, case, f"key={key} 回读步 {decl_i} 之前无规则 add 步（缺读回链）"))
                continue

            # ---- V5：extract_list 键的引用必须带索引 ----
            if decl_blk == "extract_list" and not arg:
                hits.append(("V5", i, case, f"key={key} 源自 extract_list 但引用无索引"))
    return hits


def main():
    files = sorted(glob.glob(os.path.join(SETUP_DIR, "setup_*.yaml")))
    total_hits, by_rule, hit_files = 0, {}, 0
    for fp in files:
        hits = _scan(fp)
        if hits:
            hit_files += 1
            total_hits += len(hits)
            for rule, idx, case, detail in hits:
                by_rule[rule] = by_rule.get(rule, 0) + 1
            print(f"\n{os.path.basename(fp)}  [{len(hits)} hit]")
            for rule, idx, case, detail in hits:
                print(f"   {rule}  step#{idx}  {case[:46]}")
                print(f"        {detail}")
    print("\n" + "=" * 60)
    print(f"setup 文件总数      : {len(files)}")
    print(f"命中文件数          : {hit_files}")
    print(f"命中条目总数        : {total_hits}")
    print(f"分规则              : {by_rule}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
