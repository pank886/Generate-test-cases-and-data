# -*- coding: utf-8 -*-
"""在测试框架内执行 智慧用电_37_regenerated，与对照组(14 passed/6 skipped)对比。"""
import io
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')
sys.path.insert(0, r'E:/Generate-test-cases-and-data')

from pathlib import Path
from tests.execution.test_run_generated_case import run_generated_case

r = run_generated_case(Path('testcase/园区基线/智慧用电_37_regenerated'), timeout=3600)
print(f'=== EXIT {r["exit_code"]}')
print(f'=== SUMMARY: {r["summary"]}')
rep = open(r['report_file'], encoding='utf-8').read()
print(rep[:6000])
