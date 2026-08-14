# -*- coding: utf-8 -*-
"""补充检查: 导出断言模式 + 时间值 sexagesimal 解析"""
import yaml, os, re

class _StrSafeLoader(yaml.SafeLoader):
    @staticmethod
    def _str_ts_constructor(loader, node):
        return loader.construct_scalar(node)
    @classmethod
    def _register(cls):
        cls.add_constructor('tag:yaml.org,2002:timestamp', cls._str_ts_constructor)
_StrSafeLoader._register()

root = r'C:\Users\damai\PyCharmMiscProject\testcase\园区基线\智慧用电_25\SmartPower'

# 1) 导出/下载/模板文件断言模式
exp_files = []
for dp, dn, fn in os.walk(root):
    for f in fn:
        if not f.endswith('.yaml'):
            continue
        p = os.path.join(dp, f)
        txt = open(p, encoding='utf-8').read()
        if re.search(r'url:\s*/.*(export|Export|download|template)', txt):
            exp_files.append((os.path.relpath(p, root), txt))

eq_bad = []
contains_ok = []
other = []
for rel, t in exp_files:
    eq_pt = re.search(r'-\s*eq:\s*\n\s*\$?\.?status(?:_code)?\s*:', t)
    ct_pt = re.search(r'-\s*contains:\s*\n\s*status_code\s*:', t)
    if eq_pt:
        eq_bad.append(rel)
    elif ct_pt:
        contains_ok.append(rel)
    else:
        other.append(rel)

print(f'导出/下载/模板文件总数: {len(exp_files)}')
print(f'  [坏] 用 eq status_code (二进制流会失败): {len(eq_bad)}')
for f in eq_bad:
    print(f'    - {f}')
print(f'  [好] 用 contains status_code: {len(contains_ok)}')
for f in contains_ok:
    print(f'    - {f}')
print(f'  [其他] : {len(other)}')
for f in other:
    print(f'    - {f}')

# 2) 时间值 sexagesimal 解析问题
print('\n=== 未加引号被解析为 int 的时间值 ===')
for dp, dn, fn in os.walk(root):
    for f in fn:
        if not f.endswith('.yaml'):
            continue
        p = os.path.join(dp, f)
        with open(p, encoding='utf-8') as fh:
            try:
                data = yaml.load(fh, Loader=_StrSafeLoader)
            except Exception as e:
                print(f'  parse err {p}: {e}')
                continue
        def walk(o, path):
            if isinstance(o, dict):
                for k, v in o.items():
                    walk(v, f'{path}.{k}')
            elif isinstance(o, list):
                for i, it in enumerate(o):
                    walk(it, f'{path}[{i}]')
            elif isinstance(o, int) and ('Time' in path or path.lower().endswith('time')):
                print(f'  INT时间 {os.path.relpath(p, root)} | {path} = {o}')
        walk(data, '')
