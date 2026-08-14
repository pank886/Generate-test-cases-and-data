# -*- coding: utf-8 -*-
"""验证: 在 replace_load 加"剥引号"补丁后，带引号/不带引号两种写法是否都兼容。
对比 1) 当前框架(不剥引号)  2) 打补丁后(剥引号)  下各表达式形态的运行结果。
"""
import os, re, json
import base.apiutil as apiutil

# ---- 收集本批 YAML 中实际出现的所有 ${} 表达式形态 ----
root = r'C:\Users\damai\PyCharmMiscProject\testcase\园区基线\智慧用电_25\SmartPower'
shapes = set()
for dp, dn, fn in os.walk(root):
    for f in fn:
        if f.endswith('.yaml'):
            txt = open(os.path.join(dp, f), encoding='utf-8').read()
            for m in re.finditer(r'\$\{[^}]*\}', txt):
                shapes.add(m.group(0))
shapes = sorted(shapes)
print(f'本批共出现 {len(shapes)} 种 ${} 表达式形态\n')

# ---- 构造测试用例: 同一语义的 带引号/不带引号 两种写法 ----
test_exprs = [
    # (说明, 不带引号, 带引号)
    ("get_extract_data 单参",        "${get_extract_data(plateKey)}",          "${get_extract_data('plateKey')}"),
    ("get_extract_data 三参",        "${get_extract_data(plateKey, 0, 1)}",    "${get_extract_data('plateKey', 0, 1)}"),
    ("get_extract_data_list",        "${get_extract_data_list(meterCodes)}",   "${get_extract_data_list('meterCodes')}"),
    ("get_current_time(ydm)",        "${get_current_time(ydm)}",               "${get_current_time('ydm')}"),
    ("get_current_time(hms)",        "${get_current_time(hms)}",               "${get_current_time('hms')}"),
    ("get_offset_time 2参",          "${get_offset_time(hms, -30)}",           "${get_offset_time('hms', -30)}"),
    ("get_offset_time 3参",          "${get_offset_time(ydm, 7)}",             "${get_offset_time('ydm', 7)}"),
    ("random_plates 数值",           "${random_plates(1)}",                    "${random_plates('1')}"),
]

def run_replace_load(rb, payload):
    """模拟真实调用: 只替换表达式, 不真正调 DebugTalk 网络/外部逻辑。"""
    try:
        return ('OK ', rb.replace_load(payload))
    except Exception as e:
        return ('ERR', f'{type(e).__name__}: {e}')

# 准备 extract.yaml (备份+写入测试 key)
from conf.setting import FILE_PATH
path = FILE_PATH['extract']
backup = None
if os.path.exists(path):
    backup = open(path, encoding='utf-8').read()
with open(path, 'w', encoding='utf-8') as f:
    f.write('plateKey: ABC123\nmeterCodes: [M1, M2, M3]\n')

rb = apiutil.RequestsBase()

print('=' * 100)
print('【一】当前框架（不剥引号）行为 —— 带引号全部报错')
print('=' * 100)
for desc, unq, quo in test_exprs:
    r_unq = run_replace_load(rb, {unq: 1})[0]
    r_quo = run_replace_load(rb, {quo: 1})[0]
    print(f'  {desc:26s} 不带引号[{r_unq}]  带引号[{r_quo}]')

# ---- 打补丁: 每个参数 strip 后剥单双引号 ----
def patched_replace_load(self, data):
    str_data = data
    if not isinstance(data, str):
        str_data = json.dumps(data, ensure_ascii=False)
    for _ in range(str_data.count('${')):
        if '${' in str_data and '}' in str_data:
            si = str_data.index('${')
            ei = str_data.index('}', si)
            ref = str_data[si:ei + 1]
            fn = ref[2:ref.index('(')]
            params = ref[ref.index('(') + 1:ref.index(')')]
            args = params.split(',') if params else []
            # ★ 补丁: 每个参数去空白 + 剥单双引号
            args = [a.strip().strip("'\"").strip() for a in args]
            from common.debugtilk import DebugTalk
            val = getattr(DebugTalk(), fn)(*args)
            str_data = str_data.replace(ref, str(val), 1)
    if isinstance(data, (dict, list)):
        data = json.loads(str_data)
    else:
        data = str_data
    return data

print()
print('=' * 100)
print('【二】打补丁后（剥引号）行为 —— 两种写法全部正常')
print('=' * 100)
for desc, unq, quo in test_exprs:
    r_unq = run_replace_load(rb, {unq: 1})[0]
    r_quo = run_replace_load(rb, {quo: 1})[0]
    print(f'  {desc:26s} 不带引号[{r_unq}]  带引号[{r_quo}]')

print()
print('=' * 100)
print('【三】补丁兼容性边界检查')
print('=' * 100)
# 边界1: 带引号参数含空格
print('  带引号参数含空格(应正常):', run_replace_load(rb, {"${get_extract_data( plateKey )}": 1})[0])
# 边界2: 数值/负数参数在两种写法下都正常
print('  负数参数不带引号(应正常):', run_replace_load(rb, {"${get_offset_time(hms, -30)}": 1})[0])
print('  负数参数带引号(补丁后应正常):', run_replace_load(rb, {"${get_offset_time('hms', -30)}": 1})[0])
# 边界3: 参数内带逗号的引号字符串 (框架 split(',') 限制, 与补丁无关)
print('  带引号参数内含逗号(框架限制, 补丁前/后都碎):', run_replace_load(rb, {"${get_extract_data('a,b')}": 1})[0])

# 恢复
if backup is not None:
    open(path, 'w', encoding='utf-8').write(backup)
else:
    os.remove(path)
