# -*- coding: utf-8 -*-
"""
智慧用电_26 SmartPower YAML 测试数据合规检查脚本
检查依据: skill_YAML_CHECK.md + base/apiutil.py + common/assertions.py + common/debugtilk.py
跳过空文件。
注意：2026-08-05 框架已加 parse_dollar_args 剥引号，带引号 ${} 现已兼容，不再按问题报。
"""
import os
import re
import sys
import glob
import yaml


# ROOT 可用命令行第 1 参数覆盖（按批次复用分析工具）
ROOT = (sys.argv[1] if len(sys.argv) > 1
        else r"C:\Users\damai\PyCharmMiscProject\testcase\园区基线\智慧用电_26\SmartPower")


class _StrSafeLoader(yaml.SafeLoader):
    """与框架一致：日期时间按字符串保留"""
    @staticmethod
    def _str_ts_constructor(loader, node):
        return loader.construct_scalar(node)
    @classmethod
    def _register(cls):
        cls.add_constructor('tag:yaml.org,2002:timestamp', cls._str_ts_constructor)
_StrSafeLoader._register()


# DebugTalk 可用函数
DEBUGTALK_FUNCS = {
    'get_extract_data': (1, 3),
    'get_extract_data_list': (1, 2),
    'random_plates': (1, 1),
    'get_current_time': (1, 1),
    'get_offset_time': (1, 5),
    'split_extract_data': (1, 2),
}

SUPPORTED_ASSERT = {'eq', 'ne', 'contains', 'db'}


def find_yaml_files(root):
    out = []
    for dp, dn, fn in os.walk(root):
        for f in fn:
            if f.endswith('.yaml') or f.endswith('.yml'):
                out.append(os.path.join(dp, f))
    return sorted(out)


def is_empty_file(path):
    return os.path.getsize(path) == 0


def parse_yaml(path):
    with open(path, 'r', encoding='utf-8') as f:
        return yaml.load(f, Loader=_StrSafeLoader)


def collect_dollar_exprs(obj, found):
    """递归收集字符串中的 ${...} 表达式"""
    if isinstance(obj, str):
        for m in re.finditer(r'\$\{([^}]*)\}', obj):
            found.append(m.group(0))
    elif isinstance(obj, dict):
        for v in obj.values():
            collect_dollar_exprs(v, found)
    elif isinstance(obj, list):
        for it in obj:
            collect_dollar_exprs(it, found)


def check_expression(expr, path):
    """检查单个 ${...} 表达式"""
    issues = []
    body = expr[2:-1].strip()
    m = re.match(r'^([A-Za-z_]\w*)\s*\((.*)\)$', body, re.S)
    if not m:
        issues.append((path, '异常表达式', f'`{expr}` 无法解析为 函数名(...)'))
        return issues
    fname = m.group(1)
    argstr = m.group(2).strip()
    # 参数分割（忽略引号内逗号）
    args = []
    if argstr:
        cur, quote, depth = '', None, 0
        for ch in argstr:
            if quote:
                cur += ch
                if ch == quote:
                    quote = None
            elif ch in ("'", '"'):
                quote = ch
                cur += ch
            elif ch == '(':
                depth += 1
                cur += ch
            elif ch == ')':
                depth -= 1
                cur += ch
            elif ch == ',' and depth == 0:
                args.append(cur.strip())
                cur = ''
            else:
                cur += ch
        if cur.strip():
            args.append(cur.strip())
    if fname not in DEBUGTALK_FUNCS:
        issues.append((path, '未知函数', f'`{fname}` 不在 DebugTalk 中'))
        return issues
    min_a, max_a = DEBUGTALK_FUNCS[fname]
    if not (min_a <= len(args) <= max_a):
        issues.append((path, '参数个数', f'`{expr}` 函数 {fname} 需要 {min_a}-{max_a} 个参数，实际 {len(args)}'))
    return issues


def line_of(text, needle):
    """粗略定位：在文本中找 needle 首次出现的行号"""
    idx = text.find(needle)
    if idx == -1:
        return ''
    return text[:idx].count('\n') + 1


def main():
    all_files = find_yaml_files(ROOT)
    print(f'总 YAML 文件数: {len(all_files)}')
    empty_files = [f for f in all_files if is_empty_file(f)]
    print(f'空文件数: {len(empty_files)}')
    for f in empty_files:
        print(f'  [空] {os.path.relpath(f, ROOT)}')

    non_empty = [f for f in all_files if not is_empty_file(f)]
    print(f'非空文件数: {len(non_empty)}')

    issues = []      # (file, block_idx, severity, category, detail)
    stats = {
        'files_ok': 0, 'files_with_issues': 0,
        'blocks': 0, 'cases': 0,
        'missing_header': 0, 'missing_apiname': 0, 'missing_url': 0, 'missing_method': 0,
        'no_baseinfo_testcase': 0,
        'bad_assert_op': 0,
        'url_brace': 0, 'url_dollar': 0,
        'body_wrap': 0,
        'params_in_baseinfo': 0,
        'extract_no_dollar': 0,
        'expr_unknown_func': 0, 'expr_unquoted': 0, 'expr_argcount': 0, 'expr_bad': 0,
        'get_json': 0, 'post_params': 0,
        'eq_dynamic_key': 0,
        'db_structure': 0,
        'db_string_sql': 0,
        'db_forbidden': 0,          # db_schema 为空 → 全部 db 断言禁止（2026-08-04 问题 2）
        'export_eq_assert': 0,      # 导出接口用 eq/ne 检查状态码（2026-08-04 问题 3）
        'eq_status_code': 0,        # eq/ne 对 status_code 断言（2026-08-12 问题 2，通用规则）
        'contains_bare_string': 0,  # contains 操作数非 dict（2026-08-12 问题 4）
        'validation_not_list': 0,
        'no_case_name': 0,
        'empty_validation': 0,
        'expr_quoted_ok': 0,              # 带引号 ${}（2026-08-05 框架已兼容）
        'expr_unquoted_ok': 0,            # 不带引号 ${}
        'files_using_quoted': 0,          # 使用带引号 ${} 的文件数
    }

    for fpath in non_empty:
        text = open(fpath, 'r', encoding='utf-8').read()
        rel = os.path.relpath(fpath, ROOT)
        try:
            data = parse_yaml(fpath)
        except Exception as e:
            issues.append((rel, 0, 'FATAL', 'YAML解析失败', f'{e}'))
            stats['files_with_issues'] += 1
            continue

        if data is None:
            # 空内容（全注释或空白）视为空文件
            empty_files.append(fpath)
            continue
        if not isinstance(data, list):
            issues.append((rel, 0, 'FATAL', '顶层非列表', f'顶层类型: {type(data).__name__}'))
            stats['files_with_issues'] += 1
            continue

        file_has_issue = False
        expr_file_flag = [False]
        for bi, block in enumerate(data):
            if not isinstance(block, dict):
                issues.append((rel, bi, 'FATAL', 'block非字典', f'{type(block).__name__}'))
                file_has_issue = True
                continue
            stats['blocks'] += 1

            if 'baseInfo' not in block or 'testCase' not in block:
                issues.append((rel, bi, 'FATAL', '缺baseInfo/testCase', f"keys={list(block.keys())}"))
                stats['no_baseinfo_testcase'] += 1
                file_has_issue = True
                continue

            bi_info = block['baseInfo']
            tc_list = block['testCase']

            if not isinstance(bi_info, dict):
                issues.append((rel, bi, 'FATAL', 'baseInfo非字典', ''))
                file_has_issue = True
                continue

            # ---- baseInfo 必需字段 ----
            for k, name in [('api_name', 'missing_apiname'), ('url', 'missing_url'), ('method', 'missing_method'), ('header', 'missing_header')]:
                if k not in bi_info:
                    issues.append((rel, bi, 'FATAL', f'baseInfo缺{k}', ''))
                    stats[name] += 1
                    file_has_issue = True

            # ---- params/json/data 出现在 baseInfo 层级 ----
            for pk in ('params', 'json', 'data'):
                if pk in bi_info:
                    issues.append((rel, bi, 'FATAL', '参数错放baseInfo', f'`{pk}` 应放在 testCase 内'))
                    stats['params_in_baseinfo'] += 1
                    file_has_issue = True

            # ---- URL 检查 ----
            url = bi_info.get('url', '')
            if isinstance(url, str):
                if re.search(r'\{[^}]*\}', url):
                    issues.append((rel, bi, 'FATAL', 'URL含占位符', f'`{url}`'))
                    stats['url_brace'] += 1
                    file_has_issue = True
                if '${' in url:
                    issues.append((rel, bi, 'FATAL', 'URL含${}', f'`{url}`'))
                    stats['url_dollar'] += 1
                    file_has_issue = True
            elif url:
                issues.append((rel, bi, 'FATAL', 'URL非字符串', f'{type(url).__name__}'))
                file_has_issue = True

            method = str(bi_info.get('method', '')).lower() if bi_info.get('method') is not None else ''

            # ---- testCase 检查 ----
            if not isinstance(tc_list, list) or len(tc_list) == 0:
                issues.append((rel, bi, 'FATAL', 'testCase为空', ''))
                file_has_issue = True
                continue

            for tci, tc in enumerate(tc_list):
                stats['cases'] += 1
                if not isinstance(tc, dict):
                    issues.append((rel, bi, 'FATAL', 'testCase项非字典', f'{type(tc).__name__}'))
                    file_has_issue = True
                    continue
                if 'case_name' not in tc:
                    issues.append((rel, bi, 'WARN', '缺case_name', ''))
                    stats['no_case_name'] += 1
                    file_has_issue = True

                # ---- 请求参数类型 ----
                has_json = 'json' in tc
                has_params = 'params' in tc
                has_data = 'data' in tc

                # json: {body: [...]} 包裹
                if has_json and isinstance(tc['json'], dict) and set(tc['json'].keys()) == {'body'} and isinstance(tc['json']['body'], list):
                    issues.append((rel, bi, 'FATAL', 'json包裹body', '`json: {body: [...]}` 应改为 `json: [...]`'))
                    stats['body_wrap'] += 1
                    file_has_issue = True

                # GET 用 json
                if method == 'get' and has_json and not has_params:
                    issues.append((rel, bi, 'WARN', 'GET用json', 'GET 建议用 params'))
                    stats['get_json'] += 1
                    file_has_issue = True
                # POST/PUT 用 params 而非 json/data
                if method in ('post', 'put') and has_params and not has_json and not has_data:
                    issues.append((rel, bi, 'FATAL', 'POST用params', 'POST/PUT 应用 json/data'))
                    stats['post_params'] += 1
                    file_has_issue = True

                # ---- ${} 表达式检查 ----
                found_exprs = []
                collect_dollar_exprs(tc, found_exprs)
                seen = set()
                for expr in found_exprs:
                    if expr in seen:
                        continue
                    seen.add(expr)
                    for (p, cat, det) in check_expression(expr, rel):
                        issues.append((rel, bi, 'FATAL' if cat in ('未知函数', '参数个数') else 'WARN', cat, det))
                        if cat == '未知函数':
                            stats['expr_unknown_func'] += 1
                        elif cat == '参数个数':
                            stats['expr_argcount'] += 1
                        elif cat == '参数未加引号':
                            stats['expr_unquoted'] += 1
                        else:
                            stats['expr_bad'] += 1
                        file_has_issue = True
                    # 引号兼容：2026-08-05 框架已加 parse_dollar_args 剥引号，
                    # 带引号/不带引号参数均兼容，不再按问题报，仅统计。
                    body = expr[2:-1].strip()
                    has_quote_arg = bool(re.search(r"(['\"][^'\"]*['\"])", body))
                    if has_quote_arg:
                        stats['expr_quoted_ok'] += 1
                        stats['files_using_quoted'] += 1 if not expr_file_flag[0] else 0
                        expr_file_flag[0] = True
                    else:
                        stats['expr_unquoted_ok'] += 1

                # ---- extract 检查 ----
                ext = tc.get('extract') or {}
                if ext:
                    for k, v in ext.items():
                        if isinstance(v, str) and v.strip() and not v.strip().startswith('$'):
                            issues.append((rel, bi, 'WARN', 'extract缺$', f'`{k}: {v}`'))
                            stats['extract_no_dollar'] += 1
                            file_has_issue = True
                ext_list = tc.get('extract_list') or {}
                if ext_list:
                    for k, v in ext_list.items():
                        if isinstance(v, str) and v.strip() and '$' not in v and '(.+?)' not in v and '(.*?)' not in v:
                            issues.append((rel, bi, 'WARN', 'extract_list异常', f'`{k}: {v}`'))
                            file_has_issue = True

                # ---- validation 检查 ----
                val = tc.get('validation')
                if val is not None and not isinstance(val, list):
                    issues.append((rel, bi, 'FATAL', 'validation非列表', f'{type(val).__name__}'))
                    stats['validation_not_list'] += 1
                    file_has_issue = True
                    val = None
                if val == []:
                    stats['empty_validation'] += 1
                    # 空 validation 不算问题（框架允许）
                if isinstance(val, list) and len(val) > 0:
                    for vi, yq in enumerate(val):
                        if not isinstance(yq, dict):
                            issues.append((rel, bi, 'FATAL', '断言非字典', f'第{vi+1}个断言: {yq}'))
                            file_has_issue = True
                            continue
                        if len(yq) != 1:
                            issues.append((rel, bi, 'FATAL', '断言块多项', f'{list(yq.keys())}'))
                            file_has_issue = True
                            continue
                        op, operand = next(iter(yq.items()))
                        if op not in SUPPORTED_ASSERT:
                            issues.append((rel, bi, 'FATAL', '不支持的断言', f'`{op}`'))
                            stats['bad_assert_op'] += 1
                            file_has_issue = True
                            continue
                        # 2026-08-12 问题 2（通用规则）：eq/ne 对 status_code 断言必败
                        # （status_code 特殊处理只在 contains_assert；eq/ne 按 JSONPath 解析
                        #  响应体无 status_code 字段）
                        if op in ('eq', 'ne') and isinstance(operand, dict) \
                                and any(str(k).lstrip('$.').lower() == 'status_code'
                                        for k in operand.keys()):
                            issues.append((rel, bi, 'FATAL', 'eq_status_code',
                                           f'断言用 `{op}` 检查 status_code，应改为 '
                                           f'contains: {{status_code: ...}}'))
                            stats['eq_status_code'] += 1
                            file_has_issue = True
                        # 2026-08-12 问题 4：contains 操作数非 dict → 框架 AttributeError 崩溃
                        if op == 'contains' and not isinstance(operand, dict):
                            issues.append((rel, bi, 'FATAL', 'contains裸字符串',
                                           f'`contains` 操作数应为 dict（{{字段: 期望}}），'
                                           f'当前: {str(operand)[:40]}'))
                            stats['contains_bare_string'] += 1
                            file_has_issue = True
                        if op == 'db':
                            # db_schema 为空 → 全部 db 断言禁止（2026-08-04 问题 2）
                            issues.append((rel, bi, 'FATAL', 'db断言被禁止',
                                           'db_schema 为空，db 断言全部禁止（无表结构无法写正确 SQL），应改用 eq/contains/ne'))
                            stats['db_forbidden'] += 1
                            file_has_issue = True
                            if isinstance(operand, str):
                                # db: <sql字符串> → _handle_db_assert 要求 dict（结构问题一并提示）
                                issues.append((rel, bi, 'FATAL', 'db断言结构', f'`db` 后接裸SQL字符串，应改为 dict(sql/data): {operand[:80]}...'))
                                stats['db_structure'] += 1
                                stats['db_string_sql'] += 1
                            elif not isinstance(operand, dict) or 'sql' not in operand:
                                issues.append((rel, bi, 'FATAL', 'db断言结构', f'{operand}'))
                                stats['db_structure'] += 1
                        else:
                            if isinstance(operand, dict):
                                for akey in operand.keys():
                                    if '${' in str(akey):
                                        issues.append((rel, bi, 'WARN', '断言动态key', f'`{akey}`'))
                                        stats['eq_dynamic_key'] += 1
                                        file_has_issue = True

        # ---- 导出接口断言扫描（2026-08-04 问题 3：导出返回二进制，eq/ne 检查状态码必败）
        # 注意：只匹配 export/download/template，不匹配 import（import 是 POST 返回 JSON，eq/ne 断言正确）
        if isinstance(url, str) and re.search(r'(export|download|template)', url, re.IGNORECASE):
            for _tc in tc_list:
                _val = _tc.get('validation') if isinstance(_tc, dict) else None
                if not isinstance(_val, list):
                    continue
                for _vi, _yq in enumerate(_val):
                    if not isinstance(_yq, dict):
                        continue
                    for _op, _operand in _yq.items():
                        if _op in ('eq', 'ne') and isinstance(_operand, dict):
                            if any(str(k).lstrip('$.').lower() in ('status_code', 'status', 'retcode', 'code')
                                   for k in _operand.keys()):
                                issues.append((rel, bi, 'FATAL', '导出eq断言',
                                               f'`{url}` 断言用 `{_op}` 检查状态码，应改为 contains: {{status_code: 200}}'))
                                stats['export_eq_assert'] += 1
                                file_has_issue = True

        if file_has_issue:
            stats['files_with_issues'] += 1
        else:
            stats['files_ok'] += 1

    # ---------- 汇总 ----------
    print('\n' + '=' * 80)
    print('统计汇总')
    print('=' * 80)
    for k, v in stats.items():
        print(f'  {k}: {v}')

    # 按严重程度分组的详细问题
    order = {'FATAL': 0, 'WARN': 1}
    issues.sort(key=lambda x: (order.get(x[2], 9), x[0], x[1]))
    print('\n' + '=' * 80)
    print('详细问题清单')
    print('=' * 80)
    for f, bi, sev, cat, det in issues:
        print(f'[{sev}] {f} (block {bi+1}) {cat} | {det}')

    # 输出 JSON 便于后续处理
    import json
    with open(r'E:\Generate-test-cases-and-data\tests\tools\smartpower_check_result.json', 'w', encoding='utf-8') as f:
        json.dump({'stats': stats, 'issues': issues, 'empty_files': [os.path.relpath(x, ROOT) for x in empty_files]}, f, ensure_ascii=False, indent=2)
    print(f'\n结果已写入 E:\\Generate-test-cases-and-data\\tests\\tools\\smartpower_check_result.json')
    print(f'空文件: {len(empty_files)}')


if __name__ == '__main__':
    main()
