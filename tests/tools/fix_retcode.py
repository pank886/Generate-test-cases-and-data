# -*- coding: utf-8 -*-
"""统一修正 _26 的 retCode 断言约定：真实 API 成功=1，失败=0。
按 文件类型×运算符 精确处理，避免误改反向用例。"""
import os, re, sys
sys.path.insert(0, r'C:\Users\damai\PyCharmMiscProject')
ROOT = r'C:\Users\damai\PyCharmMiscProject\testcase\园区基线\智慧用电_26\SmartPower'

def classify(rel):
    if rel.startswith('setup_data'):
        return 'SETUP'
    if '_negative' in rel:
        return 'NEG'
    return 'POS'

# 匹配断言运算符行：    - eq:
op_re = re.compile(r'^(\s*)-\s*(eq|ne|contains):\s*$')
# 匹配 retCode 键行：        $.retCode: 0
rc_re = re.compile(r'^(\s*)(\$?\.?retCode):\s*(\S+)\s*$')

changed_files = 0
changed_total = 0
log = []

for dp, dn, fn in os.walk(ROOT):
    for f in fn:
        if not f.endswith('.yaml'):
            continue
        p = os.path.join(dp, f)
        rel = os.path.relpath(p, ROOT)
        kind = classify(rel)
        with open(p, 'r', encoding='utf-8') as fh:
            lines = fh.readlines()
        new_lines = []
        op = None
        file_changed = 0
        for line in lines:
            m = op_re.match(line)
            if m:
                op = m.group(2)
                new_lines.append(line)
                continue
            m = rc_re.match(line)
            if m and op:
                indent, key, val = m.group(1), m.group(2), m.group(3)
                new_val = None
                reason = ''
                if kind in ('POS', 'SETUP') and op == 'eq' and val in ('0', '200'):
                    new_val, reason = '1', '成功断言 0/200→1'
                elif kind == 'NEG' and op == 'ne' and val == '0':
                    new_val, reason = '1', '反向"非成功" ne0→ne1'
                elif kind == 'NEG' and op == 'eq' and val == '200':
                    new_val, reason = '0', '反向失败断言 eq200→eq0'
                if new_val:
                    line = f'{indent}{key}: {new_val}\n'
                    file_changed += 1
                    log.append(f'{kind:5s} {rel}: {key}: {val} -> {new_val} ({reason})')
            new_lines.append(line)
        if file_changed:
            with open(p, 'w', encoding='utf-8') as fh:
                fh.writelines(new_lines)
            changed_files += 1
            changed_total += file_changed

print(f'修改文件数: {changed_files}')
print(f'修改断言数: {changed_total}')
print()
for l in log:
    print(' ', l)
