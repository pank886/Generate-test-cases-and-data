# -*- coding: utf-8 -*-
"""最小验证: 单引号 / 双引号 两种写法，在 当前框架 vs 剥引号补丁 下的行为"""
import os, json, sys
sys.path.insert(0, r'C:\Users\damai\PyCharmMiscProject')
import base.apiutil as apiutil
from conf.setting import FILE_PATH

path = FILE_PATH['extract']
backup = None
if os.path.exists(path):
    backup = open(path, encoding='utf-8').read()
with open(path, 'w', encoding='utf-8') as f:
    f.write('testKey: HELLO\n')

rb = apiutil.RequestsBase()

def run(payload):
    try:
        return 'OK ', rb.replace_load(payload)
    except Exception as e:
        return 'ERR', f'{type(e).__name__}: {e}'

print('【当前框架】')
for desc, expr in [
    ("单引号 ${get_extract_data('testKey')}",  "${get_extract_data('testKey')}"),
    ("双引号 ${get_extract_data(\"testKey\")}", "${get_extract_data(\"testKey\")}"),
]:
    r = run({expr: 1})
    print(f'  {desc}: {r[0]} {r[1]}')

# ---- 补丁: 剥引号。注意 json.dumps 会把双引号转义成 \"，需一并处理 ----
from common.debugtilk import DebugTalk

def patched(self, data):
    str_data = json.dumps(data, ensure_ascii=False) if not isinstance(data, str) else data
    for _ in range(str_data.count('${')):
        if '${' in str_data and '}' in str_data:
            si = str_data.index('${'); ei = str_data.index('}', si)
            ref = str_data[si:ei + 1]
            fn = ref[2:ref.index('(')]
            params = ref[ref.index('(') + 1:ref.index(')')]
            # 剥引号(健壮版): 先还原 json.dumps 对双引号的 \" 转义，再统一剥单双引号
            args = [a.strip().replace('\\"', '"').strip("'\"").strip() for a in params.split(',')] if params else []
            val = getattr(DebugTalk(), fn)(*args)
            str_data = str_data.replace(ref, str(val), 1)
    return json.loads(str_data) if isinstance(data, (dict, list)) else str_data

print('\n【剥引号补丁后】')
for desc, expr in [
    ("单引号 ${get_extract_data('testKey')}",  "${get_extract_data('testKey')}"),
    ("双引号 ${get_extract_data(\"testKey\")}", "${get_extract_data(\"testKey\")}"),
    ("不带引号 ${get_extract_data(testKey)}",  "${get_extract_data(testKey)}"),
    ("数值参数 ${get_offset_time(hms, -30)}",  "${get_offset_time(hms, -30)}"),
    ("单引号多参 ${get_offset_time('hms', -30)}", "${get_offset_time('hms', -30)}"),
]:
    try:
        r = patched(rb, {expr: 1})
        print(f'  {desc}: OK  {r}')
    except Exception as e:
        print(f'  {desc}: ERR {type(e).__name__}: {e}')

if backup is not None:
    open(path, 'w', encoding='utf-8').write(backup)
else:
    os.remove(path)
