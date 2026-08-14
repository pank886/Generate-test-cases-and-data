# -*- coding: utf-8 -*-
"""解析 test_SmartPower.py 引用的所有 yaml 路径，与实际文件对比"""
import os, re

PY = r'C:\Users\damai\PyCharmMiscProject\testcase\园区基线\智慧用电_26\SmartPower\test_SmartPower.py'
PROJ = r'C:\Users\damai\PyCharmMiscProject'
ROOT = r'C:\Users\damai\PyCharmMiscProject\testcase\园区基线\智慧用电_26\SmartPower'

src = open(PY, encoding='utf-8').read()
refs = re.findall(r"'\./([^']+\.yaml)'", src)
refs = sorted(set(refs))
print(f'Python 引用的 yaml 路径数: {len(refs)}')

missing = []
ok = 0
for r in refs:
    full = os.path.join(PROJ, r)
    if not os.path.exists(full):
        missing.append(r)
    else:
        ok += 1
print(f'存在: {ok}')
print(f'缺失: {len(missing)}')
for m in missing:
    print(f'  MISSING: {m}')

# 统计每个目录下是否有 test_data.yaml
print('\n=== 所有 test_ 目录是否有 test_data.yaml ===')
for d in sorted(os.listdir(ROOT)):
    dp = os.path.join(ROOT, d)
    if os.path.isdir(dp) and d.startswith('test_'):
        has = os.path.exists(os.path.join(dp, 'test_data.yaml'))
        print(f'  {"OK " if has else "EMPTY"} {d}')

# 反向: 存在但未被引用的 yaml
print('\n=== 存在但未被 Python 引用的文件 ===')
all_yaml = set()
for dp, dn, fn in os.walk(ROOT):
    for f in fn:
        if f.endswith('.yaml'):
            rel = os.path.relpath(os.path.join(dp, f), ROOT).replace('\\', '/')
            all_yaml.add(rel)
# refs 中的路径含 testcase/ 前缀，转成相对 SmartPower 的路径
refs_rel = set()
for r in refs:
    if 'SmartPower/' in r:
        refs_rel.add(r.split('SmartPower/', 1)[1])
unused = sorted(all_yaml - refs_rel)
for u in unused:
    print(f'  UNUSED: {u}')
