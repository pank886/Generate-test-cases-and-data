# -*- coding: utf-8 -*-
"""修复 add_url_prefix.py 的 CRLF bug：url 行丢失 \\n 导致 testCase 被并入 url 行。
恢复方法：把孤立的 \\r（后跟非 \\n）补成 \\r\\n，恢复行结构，保留已加的前缀。"""
import os, re, sys
sys.path.insert(0, r'C:\Users\damai\PyCharmMiscProject')

ROOT = r'C:\Users\damai\PyCharmMiscProject\testcase\园区基线\智慧用电_26\SmartPower'

fixed_files = 0
fixed_orphans = 0
ok_parse = 0
fail_parse = []

for dp, dn, fn in os.walk(ROOT):
    for f in fn:
        if not f.endswith('.yaml'):
            continue
        p = os.path.join(dp, f)
        with open(p, 'rb') as fh:
            content = fh.read().decode('utf-8')
        # 孤立 \r（后跟非 \n）→ 补 \n
        new_content, n = re.subn(r'\r(?!\n)', '\r\n', content)
        if n:
            with open(p, 'wb') as fh:
                fh.write(new_content.encode('utf-8'))
            fixed_files += 1
            fixed_orphans += n
        # 校验可解析
        try:
            import yaml
            with open(p, 'r', encoding='utf-8') as fh:
                yaml.safe_load(fh)
            ok_parse += 1
        except Exception as e:
            fail_parse.append((os.path.relpath(p, ROOT), str(e)[:100]))

print(f'修复文件数: {fixed_files}')
print(f'修复孤立 \\r 数: {fixed_orphans}')
print(f'修复后可解析文件数: {ok_parse}')
if fail_parse:
    print('仍解析失败:')
    for f, e in fail_parse[:10]:
        print(f'  {f}: {e}')
