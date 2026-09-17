"""对比 logs/prompt_ab/<tag> 各轮生成结果（OLD/NEW/V3...，按 _summary.json 自动发现）。

输出：每轮生成汇总 + 内容指标 + 非法断言块统计 + 文件集两两差异。
"""
import os
import sys
import json

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import yaml

SCRATCH = os.path.join(ROOT, "logs", "prompt_ab")
VALID_OPS = {"eq", "contains", "ne", "db"}


def walk(dirpath):
    files = {}
    for root, _dirs, names in os.walk(dirpath):
        for n in names:
            if n.startswith("_") or not n.endswith(".yaml"):
                continue
            full = os.path.join(root, n)
            files[os.path.relpath(full, dirpath)] = full
    return files


def summarize(dirpath):
    out = {"files": 0, "random_plates_files": 0, "random_code_files": 0,
           "bad_meter_type_cases": [], "ops": {}, "url_ok": 0, "url_bad": [],
           "assert_items": 0, "invalid_blocks": []}
    for rel, full in walk(dirpath).items():
        out["files"] += 1
        try:
            data = yaml.safe_load(open(full, encoding="utf-8"))
        except Exception as e:
            out.setdefault("parse_error", []).append((rel, str(e)))
            continue
        if not isinstance(data, list):
            continue
        for blk in data:
            for tc in blk.get("testCase", []) or []:
                raw = json.dumps(tc, ensure_ascii=False)
                if "random_plates" in raw:
                    out["random_plates_files"] += 1
                if "random_code" in raw:
                    out["random_code_files"] += 1
                md = None
                for bt in ("json", "params", "data"):
                    body = tc.get(bt)
                    if isinstance(body, dict):
                        md = body.get("meterDeviceType")
                        if md is not None and md not in ("单相", "双相", "三相"):
                            out["bad_meter_type_cases"].append((rel, tc.get("case_name"), md))
                        break
                for item in tc.get("validation", []) or []:
                    if not isinstance(item, dict):
                        out["assert_items"] += 1
                        out["invalid_blocks"].append((rel, tc.get("case_name"), [type(item).__name__]))
                        continue
                    out["assert_items"] += 1
                    keys = list(item.keys())
                    for k in keys:
                        out["ops"][k] = out["ops"].get(k, 0) + 1
                    bad = [k for k in keys if k not in VALID_OPS]
                    if bad:
                        out["invalid_blocks"].append((rel, tc.get("case_name"), bad))
            url = blk.get("baseInfo", {}).get("url") or ""
            if url.startswith("/park-energy-electric-web/"):
                out["url_ok"] += 1
            elif url:
                out["url_bad"].append((rel, url))
    return out


def main():
    tags = [d for d in sorted(os.listdir(SCRATCH))
            if os.path.isdir(os.path.join(SCRATCH, d)) and
            os.path.exists(os.path.join(SCRATCH, d, "_summary.json"))]
    print("发现轮次:", tags)
    print("=" * 78)
    for tag in tags:
        d = os.path.join(SCRATCH, tag)
        sp = os.path.join(d, "_summary.json")
        with open(sp, encoding="utf-8") as f:
            s = json.load(f)
        r = s["result"]
        print(f"[{tag}] elapsed={s['elapsed_s']}s "
              f"total={r['total']} success={r['success']} failed={r['failed']} "
              f"repaired={r['repaired']} rounds={r['rounds']} err_file={r.get('errors_file')}")
    print("=" * 78)
    for tag in tags:
        print(f"--- {tag} 内容指标 ---")
        s = summarize(os.path.join(SCRATCH, tag))
        skip = {"bad_meter_type_cases", "url_bad", "invalid_blocks", "parse_error"}
        for k, v in s.items():
            if k in skip:
                continue
            print(f"  {k}: {v}")
        if s.get("bad_meter_type_cases"):
            print("  bad_meter_type:", len(s["bad_meter_type_cases"]), "处")
            for x in s["bad_meter_type_cases"][:8]:
                print("    ", x)
        if s.get("url_bad"):
            print("  url_bad:", s["url_bad"][:5])
        if s.get("invalid_blocks"):
            print(f"  非法断言块: {len(s['invalid_blocks'])} 条")
            by_file = {}
            for rel, cn, bk in s["invalid_blocks"]:
                by_file.setdefault(rel, []).append((cn, bk))
            for rel, lst in sorted(by_file.items()):
                print(f"    {rel}: {len(lst)}条 {lst[:2]}")
        if s.get("parse_error"):
            print("  parse_error:", s["parse_error"][:3])
        print()
    # 文件集两两差异
    trees = {tag: set(walk(os.path.join(SCRATCH, tag))) for tag in tags}
    print("=" * 78)
    for i in range(len(tags)):
        for j in range(i + 1, len(tags)):
            a, b = tags[i], tags[j]
            only_a = sorted(trees[a] - trees[b])
            print(f"[{a} vs {b}] 仅{a}:{len(only_a)} 仅{b}:{len(trees[b]-trees[a])} 共有:{len(trees[a]&trees[b])}")


if __name__ == "__main__":
    main()
