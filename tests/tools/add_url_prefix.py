# -*- coding: utf-8 -*-
"""给 _26 SmartPower 所有 YAML 的 url 增加 /park-energy-electric-web 前缀"""
import os, re

ROOT = r'C:\Users\damai\PyCharmMiscProject\testcase\园区基线\智慧用电_26\SmartPower'
PREFIX = '/park-energy-electric-web'

# 匹配行首缩进 + url: + 空白 + /
pat = re.compile(r'^(\s*url:\s*)(/.*)$')

changed_files = 0
changed_urls = 0
total_urls = 0
skipped_already = 0
non_slash = 0

for dp, dn, fn in os.walk(ROOT):
    for f in fn:
        if not f.endswith('.yaml'):
            continue
        p = os.path.join(dp, f)
        with open(p, encoding='utf-8') as fh:
            lines = fh.readlines()
        new_lines = []
        file_changed = False
        for line in lines:
            m = pat.match(line)
            if m:
                total_urls += 1
                indent, rest = m.group(1), m.group(2)
                if rest.startswith(PREFIX):
                    skipped_already += 1
                    new_lines.append(line)
                    continue
                new_lines.append(f'{indent}{PREFIX}{rest}')
                changed_urls += 1
                file_changed = True
            else:
                # 检查是否有 url: 但值不是 / 开头
                if re.match(r'^\s*url:\s*\S', line) and not re.match(r'^\s*url:\s*/', line):
                    non_slash += 1
                new_lines.append(line)
        if file_changed:
            with open(p, 'w', encoding='utf-8') as fh:
                fh.writelines(new_lines)
            changed_files += 1

print(f'扫描 url 总行数: {total_urls}')
print(f'已加前缀: {changed_urls}')
print(f'跳过(已有前缀): {skipped_already}')
print(f'非 / 开头的 url: {non_slash}')
print(f'修改文件数: {changed_files}')
