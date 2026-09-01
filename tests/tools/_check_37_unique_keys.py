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
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

import yaml

# 脚本已迁移至 tests/tools/，仓库相对路径改为基于项目根推导
PROJECT_ROOT = Path(__file__).resolve().parents[2]
BASE = r'C:/Users/damai/PycharmMiscProject/testcase/园区基线/智慧用电_37_regenerated/SmartPower'
DB = str(PROJECT_ROOT / 'data/app.db')

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

# 5. input_extract 方向与路径校验（2026-08-27 决策 task #11）
#    - 键不得以 $ 开头（防反置 `$.json.code: $.meterCode`，v7 bind_billing 实测空存）
#    - 提取表达式须能命中请求参数结构：
#      JSONPath 须以 $.json./$.data./$.params. 开头（防 `$.code` 顶层路径，v7 single_rate 实测 KeyError）
#      点路径须 json./data./params. 开头；单 token 走框架「直接查找」合法
#    依据 base/apiutil.py extract_input_data 三条解析路径（framework 2026-08-27 核实）
import re as _re
_JSONPATH_OK = _re.compile(r"^\$\.(json|data|params)\.[A-Za-z0-9_\.\[\]\*]*$")
_DOT_OK = _re.compile(r"^(json|data|params)\.[A-Za-z0-9_\.]+$")
_TOKEN_OK = _re.compile(r"^[A-Za-z0-9_]+$")

for fp in sorted(glob.glob(BASE + '/**/*.yaml', recursive=True)):
    name = fp.replace('\\', '/').split('/SmartPower/')[-1]
    data = yaml.safe_load(open(fp, encoding='utf-8'))
    for i, block in enumerate(data or []):
        for tc in block.get('testCase', []):
            cn = tc.get('case_name', '未知')
            ie = tc.get('input_extract') or {}
            for k, expr in ie.items():
                if str(k).startswith('$'):
                    issues.append(f'{name} {cn} input_extract 方向反置：键 {k!r} 以 $ 开头（应为 存储key: 提取表达式）')
                    continue
                es = str(expr).strip()
                if es.startswith('$'):
                    if not _JSONPATH_OK.match(es):
                        issues.append(f'{name} {cn} input_extract 路径不可达：{k!r} -> {es!r}（JSONPath 须命中 $.json./$.data./$.params.）')
                elif '(' in es or '[' in es or ']' in es:
                    issues.append(f'{name} {cn} input_extract 表达式含括号（应为纯路径）：{k!r} -> {es!r}')
                elif '.' in es:
                    if not _DOT_OK.match(es):
                        issues.append(f'{name} {cn} input_extract 点路径须从 json./data./params. 开始：{k!r} -> {es!r}')
                elif not _TOKEN_OK.match(es):
                    issues.append(f'{name} {cn} input_extract 表达式非常规：{k!r} -> {es!r}')

# 6. 断言字段校验（2026-08-27 决策 task#12）：contains $.data 列表断言的值若形如 camelCase 字段名，
#    必须出现在「接口返回定义」字段集合中（防 LLM 臆造返回定义外的字段名，v9 tou_positive sharpElectricity 实测）
#    依据铁律 14（列表返回用 contains $.data 断言）+ 铁律 9（断言字段取自接口返回定义）
#    基准仅取 api_returns（返回定义），不含 api_parameters：sharpElectricity 只在请求参数中出现
#    （update/getPage/getList/getParentList 的 api_parameters），返回定义无此字段 → 判定臆造。
#    动态引用 ${get_extract_data(...)} / 返回字段 / 本文件已提取键名 → 放行；其余 camelCase 疑似臆造。
_FIELD_RE = _re.compile(r"^[a-z][a-zA-Z0-9]*[A-Z][a-zA-Z0-9]*$")

# 接口「返回定义」字段名全集（api_returns 的 name，全量 documents）
api_field_names = set()
_conn = sqlite3.connect(DB)
for _r in _conn.execute("SELECT api_returns FROM documents"):
    if not _r[0]:
        continue
    try:
        _arr = json.loads(_r[0])
    except Exception:
        continue
    if isinstance(_arr, list):
        for _f in _arr:
            if isinstance(_f, dict) and _f.get('name'):
                api_field_names.add(str(_f['name']))
_conn.close()

for fp in sorted(glob.glob(BASE + '/**/*.yaml', recursive=True)):
    name = fp.replace('\\', '/').split('/SmartPower/')[-1]
    data = yaml.safe_load(open(fp, encoding='utf-8'))
    local_keys = set()
    for block in data or []:
        for tc in block.get('testCase', []):
            local_keys.update((tc.get('input_extract') or {}).keys())
    for block in data or []:
        for tc in block.get('testCase', []):
            cn = tc.get('case_name', '未知')
            for ass in (tc.get('validation') or []):
                if not isinstance(ass, dict):
                    continue
                for _op, _obj in ass.items():
                    if _op != 'contains' or not isinstance(_obj, dict):
                        continue
                    for jp, exp in _obj.items():
                        if jp != '$.data':
                            continue
                        es = str(exp).strip()
                        if not _FIELD_RE.match(es):
                            continue
                        if es in api_field_names or es in local_keys:
                            continue
                        issues.append(
                            f'{name} {cn} 断言字段疑似臆造：contains $.data 值 {es!r} 不在接口返回定义中'
                            f'（返回字段: {sorted(api_field_names) or "无"}）')

# 7. setup 提取键名全局唯一（2026-08-27 决策 task#13）：跨 setup 文件键名重名会串键
#    （v9 PRE-002 复用 pre001MeterCode 与 PRE-001 冲突；同文件内多块引用 setup 键是常态，
#     但「存储键」必须全局唯一，两个 PRE 各建各的资源不得共用一个键名）
from collections import defaultdict
setup_key_owners = defaultdict(set)
for fp in sorted(glob.glob(BASE + '/setup_data/setup_*.yaml')):
    name = fp.replace('\\', '/').split('/SmartPower/')[-1]
    data = yaml.safe_load(open(fp, encoding='utf-8'))
    for block in data or []:
        for tc in block.get('testCase', []):
            cn = tc.get('case_name', '未知')
            for k in (tc.get('input_extract') or {}):
                setup_key_owners[k].add(f'{name}::{cn}')
for k in sorted(setup_key_owners):
    owners = setup_key_owners[k]
    if len(owners) > 1:
        issues.append(
            f'setup 提取键名冲突：{k!r} 在 {len(owners)} 个块定义（{sorted(owners)}），跨文件引用会串键')

if issues:
    print('\n'.join(f'❌ {x}' for x in issues))
    print(f'\n发现 {len(issues)} 个问题')
else:
    print('✅ 唯一键动态化 + 引用变量化 + 引用键一致性 + input_extract 方向/路径 + 断言字段 + 提取键名唯一 全部通过')
