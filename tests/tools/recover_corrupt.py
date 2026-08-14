# -*- coding: utf-8 -*-
"""精确反推 add_url_prefix.py 的 CRLF bug：
原 url 行 <indent>url: /path\\n 被改成 <indent>url: /park-energy-electric-web/path（丢 \\n），
随后一行的内容（testCase:）被拼接到同一行。
反推：把 `<indent>url: /park-energy-electric-web/<path><下一行内容>` 在路径后断行。
保留前缀。"""
import os, re, sys, io

ROOT = r'C:\Users\damai\PyCharmMiscProject\testcase\园区基线\智慧用电_26\SmartPower'

# 匹配：行首缩进 + "url: /park-energy-electric-web/路径(无空格)" + 后续被拼的内容(缩进+testCase等)
# 注意必须 re.MULTILINE，让 ^ $ 匹配每行（之前漏了导致 0 匹配）
pat = re.compile(r'^(\s*url: /park-energy-electric-web/\S+)(\s+\S.*)$', re.MULTILINE)

fixed = 0
merged_lines_found = 0
ok = 0
fail = []

for dp, dn, fn in os.walk(ROOT):
    for f in fn:
        if not f.endswith('.yaml'):
            continue
        p = os.path.join(dp, f)
        with io.open(p, 'r', encoding='utf-8') as fh:
            content = fh.read()
        new_content, n = pat.subn(r'\1\n\2', content)
        if n:
            with io.open(p, 'w', encoding='utf-8') as fh:
                fh.write(new_content)
            fixed += 1
        merged_lines_found += n
        # 校验
        try:
            import yaml
            yaml.safe_load(new_content)
            ok += 1
        except Exception as e:
            fail.append((os.path.relpath(p, ROOT), str(e).split('\n')[0][:90]))

print(f'修复文件数: {fixed}')
print(f'断行修复的 url 行数: {merged_lines_found}')
print(f'修复后 YAML 可解析文件数: {ok}')
if fail:
    print('仍解析失败:')
    for f, e in fail[:10]:
        print(f'  {f}: {e}')
