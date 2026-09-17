# -*- coding: utf-8 -*-
"""智慧停车 ParkingRuleTable 规则表产物：导出 + 部署 + 自检（aux，不入系统运行）。

**为什么要有这个脚本**：规则表产物（39 个 YAML + `test_ParkingRuleTable.py`）由本仓的
`rule_tables/` 源码生成，却落在**框架项目**的模块目录里运行。此前「改了内核 → 忘了重新
导出」是靠人记的：2026-09-11 18:04 改了 `yaml_kernel.py` / `parking_rule_map.yaml`，
部署目录仍停在 17:13，09-14 跑批按旧产物跑出 27 红（MANIFEST H20 根因 A）。
本脚本把「导出 + 部署」收成**唯一一条命令**，并在部署后**当场自检**（部署目录 vs 按当前
源码重生成逐文件比对），从路径上消除「忘导出」。

跑法（**本仓 venv**，不需要框架 venv，不消耗 LLM）：

    .venv/Scripts/python.exe 有作用/PhaseC/_redeploy_parking_rule_table.py --check
    .venv/Scripts/python.exe 有作用/PhaseC/_redeploy_parking_rule_table.py

`--check` 只比对、**不写任何文件**；不带参数则先导出部署、再自检。

部署目标取自 `registry.get_meta().module_dir`（框架实际运行的目录），可用 `--module-dir` 覆盖。
"""
import argparse
import filecmp
import os
import re
import sys
import tempfile

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_ROOT)

from rule_tables import phase_c_export as P          # noqa: E402
from rule_tables import registry as reg              # noqa: E402
from rule_tables import rule_rows as rr              # noqa: E402

FEATURE = "ParkingRuleTable"
SKIP_DIRS = {"__pycache__"}


def _normalize(text: str) -> str:
    """抹掉「与产物内容无关」的差异，只留语义。

    两类：① 输出路径前缀（部署侧 `./testcase/园区基线/智慧停车/ParkingRuleTable/…`
    vs 临时目录 `./../AppData/Local/Temp/tmpXXXX/ParkingRuleTable/…`）；
    ② 日期戳（`keep_data` 注释里带生成日期）。
    """
    text = re.sub(r"[^\s'\"]*" + re.escape(FEATURE) + r"/", "<FEATURE>/", text)
    return re.sub(r"\d{4}-\d{2}-\d{2}", "<DATE>", text)


def _read(path: str) -> str:
    with open(path, encoding="utf-8", errors="replace") as fh:
        return _normalize(fh.read())


def _walk_files(root: str) -> set:
    out = set()
    for base, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for f in files:
            out.add(os.path.relpath(os.path.join(base, f), root))
    return out


def compare(deployed: str, fresh: str) -> dict:
    """部署目录 vs 当场重生成，逐文件比对（`__pycache__` 不算）。"""
    d_files, f_files = _walk_files(deployed), _walk_files(fresh)
    same, diff = [], []
    for rel in sorted(d_files & f_files):
        a, b = os.path.join(deployed, rel), os.path.join(fresh, rel)
        if filecmp.cmp(a, b, shallow=False) or _read(a) == _read(b):
            same.append(rel)
        else:
            diff.append(rel)
    return {
        "same": same,
        "diff": diff,
        "only_deployed": sorted(d_files - f_files),
        "only_fresh": sorted(f_files - d_files),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="智慧停车规则表产物：导出 + 部署 + 自检")
    ap.add_argument("--module-dir", default="", help="覆盖部署目标（默认 registry.get_meta().module_dir）")
    ap.add_argument("--check", action="store_true", help="只比对部署目录与当前源码，不写任何文件")
    ap.add_argument("--keep-data", dest="keep_data", action="store_true", default=True,
                    help="注释掉 fixture 收尾的 teardown（默认开，跑完保留现场）")
    ap.add_argument("--no-keep-data", dest="keep_data", action="store_false",
                    help="恢复正常收尾删数据")
    ap.add_argument("--dry-run", action="store_true", help="只打印将写入的路径，不写文件")
    args = ap.parse_args()

    meta = reg.get_meta()
    xlsx = str(meta.get("source_xlsx", ""))
    module_dir = args.module_dir or str(meta.get("module_dir", ""))
    if not module_dir:
        print("!! 无部署目标：meta.module_dir 为空且未给 --module-dir", file=sys.stderr)
        return 2
    if not os.path.exists(xlsx):
        print("!! 判定表 xlsx 不在本机：%s" % xlsx, file=sys.stderr)
        return 2

    output_base = os.path.dirname(module_dir)
    print("判定表   : %s" % xlsx)
    print("部署目录 : %s" % module_dir)

    plan = rr.build_plan(xlsx)

    if args.check:
        fresh = tempfile.mkdtemp(prefix="prt_check_")
        P.export(plan, fresh, keep_data=args.keep_data)
        P.export_yaml(plan, fresh)
        res = compare(module_dir, os.path.join(fresh, FEATURE))
        print("\n=== 自检：部署目录 vs 按当前源码重生成 ===")
        print("相同 %d | 不同 %d | 仅部署侧有 %d | 仅重生成侧有 %d"
              % (len(res["same"]), len(res["diff"]),
                 len(res["only_deployed"]), len(res["only_fresh"])))
        for rel in res["diff"]:
            print("  不同: %s" % rel)
        for rel in res["only_deployed"]:
            print("  仅部署侧有: %s" % rel)
        for rel in res["only_fresh"]:
            print("  仅重生成侧有: %s" % rel)
        if res["diff"] or res["only_fresh"]:
            print("\n⇒ **不一致**：部署产物落后于源码，需执行本脚本（不带 --check）重新部署。")
            return 1
        print("\n⇒ 一致。")
        return 0

    if args.dry_run:
        print("\n[dry-run] 将写入：")
        print("  %s/  （39 个 YAML + test_ParkingRuleTable.py）" % module_dir)
        print("  %s/%s" % (output_base, P.DEFAULT_EXCEL_NAME))
        print("  %s/%s  （合并写）" % (output_base, P.CACHE_NAME))
        return 0

    out = P.export(plan, output_base, keep_data=args.keep_data)
    P.export_yaml(plan, output_base)
    print("\n=== 已部署 ===")
    print("  Excel  : %s" % out["excel_path"])
    print("  缓存   : %s（合并写）" % out["cache_path"])
    print("  模块   : %s" % out["feature_dir"])
    print("  .py    : %s（%d 用例，teardown 已注释=%s）"
          % (out["py_path"], out["cases"], out["teardown_commented"]))

    res = compare(module_dir, out["feature_dir"])
    print("\n=== 自检：部署目录 vs 按当前源码重生成 ===")
    print("相同 %d | 不同 %d | 仅部署侧有 %d | 仅重生成侧有 %d"
          % (len(res["same"]), len(res["diff"]),
             len(res["only_deployed"]), len(res["only_fresh"])))
    for rel in res["diff"] + res["only_deployed"] + res["only_fresh"]:
        print("  差异: %s" % rel)
    if res["diff"] or res["only_fresh"]:
        print("\n⇒ 自检未通过（不应发生：刚部署完）", file=sys.stderr)
        return 1
    print("\n⇒ 一致，部署完成。")
    return 0


if __name__ == "__main__":
    # 只在直接运行时包 stdout：本模块可被 import（`compare()` 无副作用），
    # 在 import 期换 sys.stdout 会破坏 pytest 的输出捕获。
    if hasattr(sys.stdout, "buffer"):
        import io
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
    raise SystemExit(main())
