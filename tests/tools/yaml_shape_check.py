# -*- coding: utf-8 -*-
import re, os, sys
sys.path.insert(0, r'C:\Users\damai\PyCharmMiscProject')
from base.apiutil import parse_dollar_args

root = r'C:\Users\damai\PyCharmMiscProject\testcase\园区基线\智慧用电_26\SmartPower'
exprs = set()
for dp, dn, fn in os.walk(root):
    for f in fn:
        if f.endswith('.yaml'):
            txt = open(os.path.join(dp, f), encoding='utf-8').read()
            for m in re.finditer(r'\$\{([^}]*)\}', txt):
                exprs.add(m.group(1))

print(f'本批共 {len(exprs)} 种表达式形态:')
ok = 0
for e in sorted(exprs):
    args = parse_dollar_args(e[e.index('(') + 1:e.index(')')])
    print(f'  {e:48s} -> args={args}')
    ok += 1
print(f'解析成功 {ok}/{len(exprs)}')
