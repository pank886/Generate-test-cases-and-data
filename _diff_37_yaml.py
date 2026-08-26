# -*- coding: utf-8 -*-
"""对照组合新生成 YAML 全量语义对比 → logs/yaml_diff_37.md

对照组 = 手工修复后的最终形态；新生成 = 原始自动生成。
差异 = 生成器缺口（尚未人工修补）。
"""
import os
import io
import json
import yaml

BASE_C = r'C:/Users/damai/PycharmMiscProject/testcase/园区基线/智慧用电_37/SmartPower'
BASE_N = r'C:/Users/damai/PycharmMiscProject/testcase/园区基线/智慧用电_37_regenerated/SmartPower'
OUT = r'E:/Generate-test-cases-and-data/logs/yaml_diff_37.md'

lines = []


def norm(v):
    """标量归一化（统一 str，便于比较）。"""
    if v is None:
        return 'null'
    if isinstance(v, bool):
        return 'true' if v else 'false'
    return str(v)


def extract_blocks(y):
    out = []
    for bi, block in enumerate(y or []):
        bi_ = block.get('baseInfo', {})
        tc = block.get('testCase', [])
        rows = []
        for tj, c in enumerate(tc):
            rows.append({
                'case_name': c.get('case_name', ''),
                'input_extract': c.get('input_extract') or {},
                'json': c.get('json'),
                'validation': c.get('validation') or [],
                'assert_list': _assert_list(c.get('validation') or []),
            })
        out.append({
            'idx': bi,
            'url': bi_.get('url', ''),
            'method': bi_.get('method', ''),
            'rows': rows,
        })
    return out


def _assert_list(validations):
    """validation → [ 'eq $.retCode=1', 'contains $.data=xxx', 'ne data=xxx' ]"""
    res = []
    for v in validations:
        if not isinstance(v, dict):
            res.append(f'raw:{v}')
            continue
        for op, payload in v.items():
            if not isinstance(payload, dict):
                res.append(f'{op}: {norm(payload)}')
                continue
            for k, val in payload.items():
                res.append(f'{op} {k}={norm(val)}')
    return res


def json_diff(jc, jn):
    """对比 json（dict 或 list），返回差异字符串列表。"""
    out = []
    if isinstance(jc, dict) and isinstance(jn, dict):
        only_c = sorted(set(jc) - set(jn))
        only_n = sorted(set(jn) - set(jc))
        if only_c:
            out.append(f'  对照组独有键: {only_c}')
        if only_n:
            out.append(f'  新生成独有键: {only_n}')
        for k in sorted(set(jc) & set(jn)):
            vc, vn = norm(jc[k]), norm(jn[k])
            if vc != vn:
                out.append(f'  值不同 [{k}]: 对照={vc!r}  新={vn!r}')
    elif isinstance(jc, list) and isinstance(jn, list):
        if jc != jn:
            out.append(f'  列表不同: 对照={jc}  新={jn}')
    else:
        out.append(f'  结构类型不同: 对照={type(jc).__name__}  新={type(jn).__name__}')
    return out


def diff_case(case):
    pc = os.path.join(BASE_C, case, 'test_data.yaml')
    pn = os.path.join(BASE_N, case, 'test_data.yaml')
    try:
        dc = yaml.safe_load(open(pc, encoding='utf-8'))
    except Exception as e:
        return [f'## {case}\n- 对照组读取失败: {e}']
    try:
        dn = yaml.safe_load(open(pn, encoding='utf-8'))
    except Exception as e:
        return [f'## {case}\n- 新生成读取失败: {e}']
    bc, bn = extract_blocks(dc), extract_blocks(dn)
    sec = [f'## {case}', f'- 对照 block={len(bc)}, 新 block={len(bn)}']
    if len(bc) != len(bn):
        sec.append(f'- ⚠️ block 数量不同')
    for i in range(max(len(bc), len(bn))):
        c = bc[i] if i < len(bc) else None
        n = bn[i] if i < len(bn) else None
        if c is None or n is None:
            sec.append(f'- block[{i}]: {"对照独有" if c else "新独有"}')
            continue
        sec.append(f'- block[{i}] {n["url"]}  ({n["method"]})')
        if c['url'] != n['url'] or c['method'] != n['method']:
            sec.append(f'  ⚠️ url/method 不同: 对照={c["url"]} {c["method"]}  新={n["url"]} {n["method"]}')
        for ri in range(max(len(c['rows']), len(n['rows']))):
            rc = c['rows'][ri] if ri < len(c['rows']) else None
            rn = n['rows'][ri] if ri < len(n['rows']) else None
            if rc is None or rn is None:
                sec.append(f'  testCase[{ri}]: {"对照独有" if rc else "新独有"}')
                continue
            if rc['case_name'] != rn['case_name']:
                sec.append(f'  testCase[{ri}] case_name: 对照={rc["case_name"]}  新={rn["case_name"]}')
            jd = json_diff(rc['json'], rn['json'])
            if jd:
                sec.append(f'  testCase[{ri}] json 差异:')
                sec.extend(jd)
            icc, icn = rc['input_extract'], rn['input_extract']
            if icc != icn:
                sec.append(f'  testCase[{ri}] input_extract: 对照={icc}  新={icn}')
            ac, an = rc['assert_list'], rn['assert_list']
            if ac != an:
                sec.append(f'  testCase[{ri}] validation:')
                sec.append(f'    对照: {json.dumps(ac, ensure_ascii=False)}')
                sec.append(f'    新  : {json.dumps(an, ensure_ascii=False)}')
    return sec


def diff_setup(name):
    pc = os.path.join(BASE_C, 'setup_data', name)
    pn = os.path.join(BASE_N, 'setup_data', name)
    try:
        dc = yaml.safe_load(open(pc, encoding='utf-8'))
    except Exception as e:
        return [f'## setup/{name}\n- 对照组读取失败: {e}']
    try:
        dn = yaml.safe_load(open(pn, encoding='utf-8'))
    except Exception as e:
        return [f'## setup/{name}\n- 新生成读取失败: {e}']
    bc, bn = extract_blocks(dc), extract_blocks(dn)
    sec = [f'## setup/{name}', f'- 对照 block={len(bc)}, 新 block={len(bn)}']
    for i in range(max(len(bc), len(bn))):
        c = bc[i] if i < len(bc) else None
        n = bn[i] if i < len(bn) else None
        if c is None or n is None:
            sec.append(f'- block[{i}]: {"对照独有" if c else "新独有"}')
            continue
        sec.append(f'- block[{i}] {n["url"]}  case_name={n["rows"][0]["case_name"] if n["rows"] else "?"}')
        rc = c['rows'][0] if c['rows'] else None
        rn = n['rows'][0] if n['rows'] else None
        if rc and rn:
            jd = json_diff(rc['json'], rn['json'])
            if jd:
                sec.append(f'  json 差异:')
                sec.extend(jd)
            icc, icn = rc['input_extract'], rn['input_extract']
            if icc != icn:
                sec.append(f'  input_extract: 对照={icc}  新={icn}')
    return sec


cases = sorted(d for d in os.listdir(BASE_C)
               if d.startswith('test_') and os.path.isdir(os.path.join(BASE_C, d)))

all_sec = []
for case in cases:
    all_sec.extend(diff_case(case))
for s in sorted(os.listdir(os.path.join(BASE_C, 'setup_data'))):
    all_sec.extend(diff_setup(s))

header = [
    '# 智慧用电_37 对照组 vs 新生成 YAML 全量对比',
    f'> 对照组 = 手工修复最终形态；新生成 = 原始自动生成（prompt 15 铁律 + O2 校验器 + D5）。',
    f'> 差异 = 生成器缺口（尚未人工修补）。',
    f'> 用例数: {len(cases)}',
    '',
]
with io.open(OUT, 'w', encoding='utf-8') as f:
    f.write('\n'.join(header + all_sec))
print(f'written {OUT}  ({len(all_sec)} sections)')
