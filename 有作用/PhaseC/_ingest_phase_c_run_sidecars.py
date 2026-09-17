# -*- coding: utf-8 -*-
"""手动摄取驱动（2026-09-09）：框架执行一轮后，把 junit + 原始执行日志 → 各 YAML 旁 `.last_run.json`。

动态落点（决策 #3/#4）：长期由执行框架（PyCharmMiscProject run 层）执行后原生写；本次不改框架，
由 LLM 在每次框架执行后手动跑本驱动。有作用/ 惯例 aux，不入系统运行时 / pytest 收集。

用法（从仓库根运行）:
    python 有作用/PhaseC/_ingest_phase_c_run_sidecars.py <模块目录> [--junit X] [--log Y] [--run_ts Z]

缺省 --junit/--log：自动定位 config.PYCHARM_MISC 下 report/junit.xml（覆盖式，最新一次）与
最新 log/test.<ts>.log。run_ts 优先级：显式 > junit timestamp > log 文件名。
"""
import argparse
import glob
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


def _default_paths() -> tuple:
    import infrastructure.config as config
    base = config.PYCHARM_MISC
    if not base:
        return None, None
    junit = os.path.join(base, "report", "junit.xml")
    if not os.path.isfile(junit):
        junit = None
    log = None
    hits = glob.glob(os.path.join(base, "log", "test.*.log"))
    if hits:
        log = max(hits, key=lambda p: os.path.getmtime(p))
    return junit, log


def main() -> int:
    ap = argparse.ArgumentParser(description="摄取一次执行 → 各 YAML 旁 .last_run.json（覆盖式）")
    ap.add_argument("module_dir", help="模块目录（test_plan.xlsx 旁；含 setup_data 与 test_*/test_data.yaml）")
    ap.add_argument("--junit", default="", help="junit.xml 路径（缺省自动定位框架 report/junit.xml）")
    ap.add_argument("--log", default="", help="原始执行日志路径（缺省自动定位最新 test.<ts>.log）")
    ap.add_argument("--run_ts", default="", help="显式 run_ts（YYYYMMDDHHMMSS）；缺省按 junit/log 推断")
    args = ap.parse_args()

    from agent_components.run_results import ingest_run_results

    module_dir = os.path.abspath(args.module_dir)
    if not os.path.isdir(module_dir):
        print(f"[ingest] 模块目录不存在: {module_dir}")
        return 1

    junit = args.junit or None
    log = args.log or None
    if not junit or not log:
        d_junit, d_log = _default_paths()
        junit = junit or d_junit
        log = log or d_log
    if not junit:
        print("[ingest] 未定位到 junit.xml，请用 --junit 显式传入")
        return 1

    print(f"[ingest] module  = {module_dir}")
    print(f"[ingest] junit   = {junit}")
    print(f"[ingest] log     = {log or '(无)'}")
    if args.run_ts:
        print(f"[ingest] run_ts  = {args.run_ts}")

    out = ingest_run_results(module_dir, junit, log, run_ts=(args.run_ts or None))

    counts = {"passed": 0, "failed": 0, "broken": 0}
    fail_list = []
    for rel, rec in sorted(out.items()):
        v = rec.get("verdict")
        counts[v] = counts.get(v, 0) + 1
        if v in ("failed", "broken"):
            fail_list.append((rel, v, rec.get("case_name") or [], rec.get("junit_msg", "")))

    print(f"[ingest] 写 {len(out)} 个 .last_run.json："
          f"passed {counts['passed']} / failed {counts['failed']} / broken {counts['broken']}")
    for rel, v, cn, msg in fail_list[:30]:
        print(f"  - [{v}] {rel}  case={cn}  junit={msg[:120]}")
    if len(fail_list) > 30:
        print(f"  ... 共 {len(fail_list)} 条失败/破损")
    return 0


if __name__ == "__main__":
    sys.exit(main())
