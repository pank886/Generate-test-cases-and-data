"""YAPI MD 纯代码解析（无外部业务依赖）。"""

import re


def _merge_api_defs(existing: dict, incoming: dict) -> dict:
    """合并同一接口的两个版本（method+url 相同），而非简单覆盖。

    合并策略：
      - parameters/returns: 取两套字段的并集，incoming 的字段优先
      - description: 取更详细（更长）的那一个
      - name/method/url: 保留 incoming（新版本为准）
    """
    merged = dict(incoming)  # 以新版本为基底
    # parameters: 新版为 list，旧版为 dict；list 直接覆盖，dict 做字段级合并
    incoming_params = incoming.get("parameters", []) or []
    if isinstance(incoming_params, list):
        merged["parameters"] = incoming_params
    else:
        existing_params = existing.get("parameters", {}) or {}
        if isinstance(existing_params, dict):
            merged_params = dict(existing_params)
            merged_params.update(incoming_params or {})
            merged["parameters"] = merged_params
        else:
            merged["parameters"] = incoming_params

    # returns 同理
    incoming_returns = incoming.get("returns", []) or []
    if isinstance(incoming_returns, list):
        merged["returns"] = incoming_returns
    else:
        existing_returns = existing.get("returns", {}) or {}
        if isinstance(existing_returns, dict):
            merged_returns = dict(existing_returns)
            merged_returns.update(incoming_returns or {})
            merged["returns"] = merged_returns
        else:
            merged["returns"] = incoming_returns

    # description 保留更详细的那个
    desc_existing = (existing.get("description") or "").strip()
    desc_incoming = (incoming.get("description") or "").strip()
    merged["description"] = desc_incoming if len(desc_incoming) >= len(desc_existing) else desc_existing

    return merged


def _extract_valid_api_paths(full_text: str) -> set[tuple[str, str]]:
    """从 yapi 导出的 MD 文档中提取所有合法的 (METHOD, URL) 白名单。

    以 ``**Path：** `` 为锚点，向后搜索最近 500 字符内的 ``**Method：** ``。
    返回 {(METHOD_UPPER, url), ...} 集合，用于过滤 LLM 幻觉的接口。

    设计意图：LLM 可能因参数字段名（deviceId）或记忆污染（跨项目接口）
    编造不存在的接口。白名单直接扫描原始文档的 Path/Method 行，
    不依赖 LLM 质量，提供一道独立防线。
    """
    path_re = re.compile(r'\*\*Path[：:]\*\*\s+(/\S+)')
    method_re = re.compile(r'\*\*Method[：:]\*\*\s+(\w+)')

    valid: set[tuple[str, str]] = set()
    for path_m in path_re.finditer(full_text):
        url = path_m.group(1).strip().rstrip("/")
        if not url:
            continue
        pos = path_m.end()
        # 向后搜索最近 500 字符内的 Method（yapi 格式 Path 在前 Method 在后）
        search_end = min(len(full_text), pos + 500)
        search_text = full_text[pos:search_end]
        method_m = method_re.search(search_text)
        if method_m:
            method = method_m.group(1).strip().upper()
            valid.add((method, url))
    return valid


def extract_apis_from_yapi_md(text: str) -> list[dict]:
    """纯代码提取：解析 YApi 导出的 MD 文档，返回接口定义列表。

    适用于格式规整的 YApi MD，不需要 LLM。
    提取字段：name, url, method, description, headers, parameters, returns
    """
    import re as _re
    from bs4 import BeautifulSoup

    # ── 提取模块名（从 h1 标签取纯文本）──
    module_name = ""
    h1_match = _re.search(r'<h1[^>]*>(.+?)</h1>', text)
    if h1_match:
        module_name = _re.sub(r'<[^>]+>', '', h1_match.group(1)).strip()

    # ── 切分 API 段（跳过 h1/# 前言，不要混入第一个 API）──
    parts = _re.split(r'(?=\n## )', text)
    parts = [p for p in parts if p.strip() and p.strip().startswith('## ')]

    apis = []
    for part in parts:
        part = part.strip()
        if not part:
            continue

        # ── 基本信息 ──
        first_line = part.split('\n')[0].strip()
        name = _re.sub(r'^##\s+', '', first_line).strip()

        url_match = _re.search(r'\*\*Path：\*\*\s*(.+)', part)
        url = url_match.group(1).strip() if url_match else ""

        method_match = _re.search(r'\*\*Method：\*\*\s*(.+)', part)
        method = method_match.group(1).strip().upper() if method_match else "?"

        # 接口描述：YApi 空描述导出为 <p></p>，正则捕获后再剥掉 HTML 标签残留（不能直接留 </p>）
        desc_match = _re.search(r'\*\*接口描述：\*\*\s*\n?\s*(?:<p[^>]*>)?(.*?)(?:</p>)?\s*\n', part)
        description = ""
        if desc_match:
            description = _re.sub(r'<[^>]+>', '', desc_match.group(1)).strip()

        # ── 辅助：解析 HTML 表格为参数数组 ──
        def _parse_html_table(html_str: str) -> list[dict]:
            """解析 HTML <table> 为 [{name, type, required, description, default, children}]"""
            soup = BeautifulSoup(html_str, 'html.parser')
            table = soup.find('table')
            if not table:
                return []
            # 找表头确定列映射
            headers = []
            for th in table.find_all('th'):
                key = th.get('key', th.get_text(strip=True))
                headers.append(key)
            if not headers:
                return []
            # 列名映射（YApi 格式：名称/类型/是否必须/默认值/备注）
            col_map = {'name': -1, 'type': -1, 'required': -1, 'default': -1, 'desc': -1}
            for idx, h in enumerate(headers):
                hl = h.lower()
                if '名称' in h or 'name' in hl or '字段' in h or '参数' in h:
                    col_map['name'] = idx
                elif '类型' in h or 'type' in hl:
                    col_map['type'] = idx
                elif '必须' in h or 'required' in hl:
                    col_map['required'] = idx
                elif '默认' in h or 'default' in hl:
                    col_map['default'] = idx
                elif '备注' in h or 'desc' in hl or '说明' in h:
                    col_map['desc'] = idx

            # 解析行，通过缩进判断层级
            rows = table.find_all('tr')
            result = []
            stack = [(result, -1)]  # (parent_list, indent_level)

            for tr in rows:
                cells = tr.find_all('td')
                if len(cells) < 2:
                    continue
                # 计算缩进层级
                first_cell = cells[0]
                spans = first_cell.find_all('span')
                indent = 0
                for sp in spans:
                    style = sp.get('style', '')
                    if 'padding-left' in style:
                        try:
                            px = int(_re.search(r'padding-left:\s*(\d+)px', style).group(1))
                            indent = max(indent, px // 20)  # 每 20px 一级
                        except (ValueError, AttributeError):
                            pass

                # 提取字段值
                def _cell_text(idx):
                    if idx < 0 or idx >= len(cells):
                        return ''
                    # 去掉嵌套的 span 样式文本，只取直接文本或 API 名称
                    t = cells[idx].get_text(separator=' ', strip=True)
                    # 清理树形连接符
                    t = _re.sub(r'^\s*[├└]─?\s*', '', t)
                    return t.strip()

                name_val = _cell_text(col_map['name'])
                type_val = _cell_text(col_map['type'])
                required_str = _cell_text(col_map['required'])
                default_val = _cell_text(col_map['default'])
                desc_val = _cell_text(col_map['desc'])

                if not name_val:
                    continue

                required = '必须' in required_str or required_str.lower() == '是' or required_str.lower() == 'true'

                item = {
                    'name': name_val,
                    'type': type_val or 'string',
                    'required': required,
                    'description': desc_val,
                    'default': default_val or None,
                }

                # 处理层级：弹出比当前缩进更深的栈
                while len(stack) > 1 and stack[-1][1] >= indent:
                    stack.pop()
                parent_list = stack[-1][0]
                parent_list.append(item)
                # 如果有嵌套类型，准备 children
                if type_val and ('object' in type_val.lower() or '[]' in type_val or 'array' in type_val.lower()):
                    item['children'] = []
                    stack.append((item['children'], indent))

            return result

        # ── 解析请求参数（支持 Headers/Query/Body；Markdown 表格或 HTML 表格）──
        headers_list = []
        params_list = []

        # 辅助：解析 YApi 导出的 Markdown 参数表（按表头自动映射列名）
        def _parse_md_table(md_str: str) -> list[dict]:
            lines = [ln for ln in md_str.strip().split('\n') if ln.strip().startswith('|')]
            if len(lines) < 2:
                return []
            rows = [[c.strip() for c in ln.strip().strip('|').split('|')] for ln in lines]
            header = rows[0]
            # 列映射：只映射第一个命中，避免「参数名称/参数值」都含"参数"时冲突
            col_map = {'name': -1, 'type': -1, 'required': -1, 'default': -1, 'desc': -1}
            for idx, h in enumerate(header):
                hl = h.lower()
                if col_map['name'] == -1 and ('名称' in h or 'name' in hl or '参数' in h or '字段' in h):
                    col_map['name'] = idx
                elif col_map['type'] == -1 and ('类型' in h or 'type' in hl):
                    col_map['type'] = idx
                elif col_map['required'] == -1 and ('必须' in h or 'required' in hl):
                    col_map['required'] = idx
                elif col_map['default'] == -1 and ('默认' in h or 'default' in hl):
                    col_map['default'] = idx
                elif col_map['desc'] == -1 and ('备注' in h or '说明' in h or 'desc' in hl or '描述' in h):
                    col_map['desc'] = idx
            result = []
            for row in rows[1:]:
                if all(set(c) <= {'-', ':', ' '} for c in row if c):
                    continue  # 分隔行
                def _cell(idx):
                    return row[idx] if 0 <= idx < len(row) else ''
                name_val = _cell(col_map['name'])
                if not name_val:
                    continue
                req_str = _cell(col_map['required'])
                result.append({
                    'name': name_val,
                    'type': _cell(col_map['type']) or 'string',
                    'required': '必须' in req_str or req_str.lower() == '是' or req_str.lower() == 'true',
                    'description': _cell(col_map['desc']),
                    'default': _cell(col_map['default']) or None,
                })
            return result

        # 请求参数区（### 请求参数 → ### 返回数据），只在区内解析，避免误取返回数据表
        req_section = ''
        req_match = _re.search(r'### 请求参数\s*\n(.*?)(?=\n### 返回数据|\Z)', part, _re.DOTALL)
        if req_match:
            req_section = req_match.group(1)

        def _subsection(sec: str, title: str) -> str:
            """取 **title** 标题后的子段内容（到下一个 **标题 或 ### 或结尾）。"""
            m = _re.search(r'\*\*' + _re.escape(title) + r'\*\*\s*\n(.*?)(?=\n\*\*|\n###\s|\Z)',
                           sec, _re.DOTALL)
            return m.group(1) if m else ''

        hdr_section = _subsection(req_section, 'Headers')
        query_section = _subsection(req_section, 'Query')
        body_section = _subsection(req_section, 'Body')

        headers_list = _parse_md_table(hdr_section) if hdr_section else []
        if query_section:
            params_list.extend(_parse_md_table(query_section))
        if body_section:
            body_params = _parse_md_table(body_section)
            params_list.extend(body_params or _parse_html_table(body_section))

        # ── 解析返回数据 ──
        returns_list = []
        ret_match = _re.search(r'### 返回数据\s*\n(.*?)(?=\n##|\Z)', part, _re.DOTALL)
        if ret_match:
            returns_html = ret_match.group(1)
            returns_list = _parse_html_table(returns_html)

        api = {
            'name': name,
            'url': url,
            'method': method,
            'description': description or name,
            'headers': headers_list,
            'parameters': params_list,
            'returns': returns_list,
            'annotations': {},
        }
        apis.append(api)

    return {"apis": apis, "module_name": module_name}


def _split_text_by_headers(text: str, max_chars: int) -> list:
    """按 ## 标题切分文本，每个 API 独立成段。

    只按 ## (h2) 切——保证每个 API 完整不被截断。
    不拼批次：每个 API 就是一段，不再按字符数合并。
    max_chars 仅用于单个 API 超出限制时的截断保护。
    """
    import re
    parts = re.split(r'(?=\n## )', text)
    # 第一段可能是 # 标题行，不含 API；合并到第一个 ## 段
    if len(parts) >= 2:
        pre = parts[0].strip()
        if pre and not pre.startswith('## '):
            parts[1] = pre + "\n\n" + parts[1].lstrip()
        parts = parts[1:] if not parts[0].strip().startswith('## ') else parts

    batches = []
    for part in parts:
        part = part.strip()
        if not part:
            continue
        batches.append(part)
    return batches
