# -*- coding: utf-8 -*-
"""静态检查：v4 重生成的 setup/引用是否正确动态化唯一键（校验器降级替代）。

依据 changelog/2026-08-26_prompt_unique_key_conflict.md：
- setup 创建块唯一键（desc 含「唯一」的字段）必须含 ${（动态化，可保留原值前缀）
- 引用动作（duplicate/delete）的 code 必须为变量（get_extract_data / ${变量}），非字面值

不进运行流程，仅作为验证手段。
"""
import io
import sys
import json
import glob
import sqlite3

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

import yaml

BASE = r'C:/Users/damai/PycharmMiscProject/testcase/园区基线/智慧用电_37_regenerated/SmartPower'
DB = 'data/app.db'

# 1. 读 DB：add 接口参数，找 desc 含「唯一」的字段
conn = sqlite3.connect(DB)
row = conn.execute(
    "SELECT api_parameters FROM documents WHERE api_url='/park-energy-electric-web/electricMeter/add'"
).fetchone()
params = json.loads(row[0])
unique_fields = [p['name'] for p in params
                 if p.get('desc') and '唯一' in p['desc']]
conn.close()
print(f'唯一键字段(来自 add desc): {unique_fields}')

issues = []

# 2. 遍历 setup YAML，检查唯一键字段是否含 ${（创建动作必须动态化）
for fp in glob.glob(BASE + '/setup_data/*.yaml'):
    data = yaml.safe_load(open(fp, encoding='utf-8'))
    for i, block in enumerate(data or []):
        for tc in block.get('testCase', []):
            body = tc.get('json') or {}
            cn = tc.get('case_name', '未知')
            bodies = body if isinstance(body, list) else [body]
            for b in bodies:
                if not isinstance(b, dict):
                    continue
                for field in unique_fields:
                    val = b.get(field)
                    if val is None:
                        continue
                    vs = str(val)
                    if '${' not in vs:
                        issues.append(f'SETUP {cn} 块{i} 字段 {field} = {vs!r} 未动态化（缺 ${{{""}}}）')

# 3. 检查下游用例块引用（duplicate/delete 的 code 应为变量或动态，非字面 ELEC_xxx）
for fp in glob.glob(BASE + '/test_*/*.yaml'):
    name = fp.replace('\\', '/').split('/')[-2]
    data = yaml.safe_load(open(fp, encoding='utf-8'))
    for i, block in enumerate(data or []):
        for tc in block.get('testCase', []):
            body = tc.get('json') or {}
            cn = tc.get('case_name', '未知')
            bodies = body if isinstance(body, list) else [body]
            for b in bodies:
                if not isinstance(b, dict):
                    continue
                for field in unique_fields:
                    val = b.get(field)
                    if val is None:
                        continue
                    vs = str(val)
                    if vs.startswith('ELEC_') and '${' not in vs and 'get_extract_data' not in vs:
                        issues.append(f'用例 {name} {cn} 字段 {field} = {vs!r} 疑似写死引用')

# 4. 引用键一致性：用例 get_extract_data('key') 必须存在于 setup 或本文件内已提取的 input_extract key 集中
#    （防 KeyError：setup 提取 key 与用例引用 key 名不匹配，2026-08-26 delete_bound KeyError 'ELEC_BIND' 复盘）
import re
_GET = re.compile(r"get_extract_data\(\s*['\"]([^'\"]+)['\"]\s*\)")

def collect_extract_keys(data):
    """收集 YAML 内所有 input_extract 的 key 集合"""
    keys = set()
    for block in data or []:
        for tc in block.get('testCase', []):
            for k in (tc.get('input_extract') or {}):
                keys.add(k)
    return keys

# setup/teardown 全量提取 key（作为全局前置资源）
setup_keys = set()
for fp in glob.glob(BASE + '/setup_data/*.yaml'):
    setup_keys |= collect_extract_keys(yaml.safe_load(open(fp, encoding='utf-8')))

for fp in glob.glob(BASE + '/test_*/*.yaml'):
    name = fp.replace('\\', '/').split('/')[-2]
    data = yaml.safe_load(open(fp, encoding='utf-8'))
    seen = collect_extract_keys(data)  # 本文件内已提取（含前块）
    for i, block in enumerate(data or []):
        for tc in block.get('testCase', []):
            cn = tc.get('case_name', '未知')
            text = str(tc.get('json') or '') + str(tc.get('validation') or '')
            for key in _GET.findall(text):
                if key not in seen and key not in setup_keys:
                    issues.append(f'用例 {name} {cn} 引用键 {key!r} 未定义（setup 提取: {sorted(setup_keys) or "无"} / 本文件提取: {sorted(seen) or "无"}）')

if issues:
    print('\n'.join(f'❌ {x}' for x in issues))
    print(f'\n发现 {len(issues)} 个问题')
else:
    print('✅ 唯一键动态化 + 引用变量化 + 引用键一致性 全部通过')
