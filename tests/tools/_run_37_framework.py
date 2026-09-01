# -*- coding: utf-8 -*-
"""在测试框架内执行 智慧用电_37_regenerated，与对照组(14 passed/6 skipped)对比。"""
import io
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

# 脚本已迁移至 tests/tools/，根目录不在默认 sys.path：显式加入项目根
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from tests.execution.test_run_generated_case import run_generated_case

# case_rel 必须相对框架根（FRAMEWORK_ROOT = C:/Users/damai/PycharmMiscProject）。
# 2026-08-27 修复：脚本迁入 tests/tools/ 后 PROJECT_ROOT 变成仓库根，
# 直接 PROJECT_ROOT / 'testcase/...' 会拼出 E:\...\testcase 导致目录不存在。
r = run_generated_case(Path('testcase/园区基线/智慧用电_37_regenerated'), timeout=3600)
print(f'=== EXIT {r["exit_code"]}')
print(f'=== SUMMARY: {r["summary"]}')
rep = open(r['report_file'], encoding='utf-8').read()
print(rep[:6000])
