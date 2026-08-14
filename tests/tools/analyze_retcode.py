# -*- coding: utf-8 -*-
"""精确分类 _26 所有 retCode 断言：文件类型 × 运算符 × 值"""
import os, re, yaml, sys
sys.path.insert(0, r'C:\Users\damai\PyCharmMiscProject')
ROOT = r'C:\Users\damai\PyCharmMiscProject\testcase\园区基线\智慧用电_26\SmartPower'

from collections import Counter, defaultdict
result = defaultdict(Counter)   # (kind, op) -> {value: count}

class L(yaml.SafeLoader): pass

for dp, dn, fn in os.walk(ROOT):
    for f in fn:
        if not f.endswith('.yaml'):
            continue
        p = os.path.join(dp, f)
        rel = os.path.relpath(p, ROOT)
        if rel.startswith('setup_data'):
            kind = 'SETUP'
        elif rel.rsplit('/',1)[-1].startswith('test_') and '_negative' in rel:
            kind = 'NEG'
        else:
            kind = 'POS'
        try:
            data = yaml.safe_load(open(p, encoding='utf-8'))
        except Exception:
            continue
        if not isinstance(data, list):
            continue
        for block in data:
            for tc in (block.get('testCase') or []):
                for yq in (tc.get('validation') or []):
                    if not isinstance(yq, dict):
                        continue
                    for op, operand in yq.items():
                        if op not in ('eq', 'ne', 'contains'):
                            continue
                        if isinstance(operand, dict):
                            for k, v in operand.items():
                                if re.search(r'retCode', str(k)):
                                    result[(kind, op)][v] += 1

print(f'{"类型":6s} {"运算符":10s} {"值分布":30s}')
for (kind, op) in sorted(result):
    dist = dict(result[(kind, op)])
    print(f'{kind:6s} {op:10s} {str(dist)}')
