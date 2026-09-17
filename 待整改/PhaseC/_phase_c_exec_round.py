# -*- coding: utf-8 -*-
"""#31 包装器执行驱动（智慧停车）：跑一轮框架 pytest → junit + 仓库 logs 报告。

用法: python logs/_phase_c_exec_round.py [timeout秒]
"""
import io
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from tests.execution.test_run_generated_case import run_generated_case

timeout = int(sys.argv[1]) if len(sys.argv) > 1 else 3600
r = run_generated_case(Path("testcase/园区基线/智慧停车"), timeout=timeout)
print(f"=== EXIT {r['exit_code']}", flush=True)
print(f"=== SUMMARY: {r['summary']}", flush=True)
print(f"=== REPORT: {r['report_file']}", flush=True)
