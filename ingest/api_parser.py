"""YAPI / 纯 Markdown 接口文档纯代码解析（无外部业务依赖）。

2026-08-13 重构：输出结构改为紧凑可读格式
  - header: 请求头 名→值 映射（对象）
  - body:   请求体字段数组，每项 {name, type, required, default, desc, value}
  - return: 响应字段数组，同上
  value 只取自文档「请求示例/返回示例」代码块；无示例则为空串。
  支持两种文档形态：
    - YApi 导出 MD（**Path：**/**Method：** + HTML 表格，API 段以 ## 开头）
    - 纯 Markdown（**接口路径**/**接口名称** + Markdown 表格，API 段以 # 开头）
"""

import json
import re


# ============================================================
# 字段与必填判定
# ============================================================

def _is_required(raw: str) -> bool:
    """判定必填列是否为必填（支持 markdown 加粗，如 **必须**）。

    原写法 ``'必须' in s`` 会把「非必须」误判为必填（子串包含 bug）。
    正确语义：
      - 必填：必须 / 必填 / 是 / y / yes / true / t / 1
      - 非必填：非必须 / 非必填 / 不必填 / 否 / n / no / false / f / 0 / 空
    含「必须/必填」但带「非/不」否定前缀的视为非必填；未注明（空）默认非必填。
    """
    s = (raw or "").strip().strip("*").strip().lower()
    if not s:
        return False
    if s in ("非必须", "非必填", "不必填", "否", "n", "no", "false", "f", "0"):
        return False
    if s in ("必须", "必填", "是", "y", "yes", "true", "t", "1"):
        return True
    # 兜底：含「必须/必填」但带否定前缀（非/不）→ 非必填
    return ("必须" in s or "必填" in s) and not s.startswith(("非", "不"))


def _make_field(name: str, type_: str, required: bool, default: str, desc: str,
                value: str = "", children: list = None) -> dict:
    """构造六字段结构：{name, type, required, default, desc, value[, children]}"""
    f = {
        "name": (name or "").strip(),
        "type": (type_ or "").strip() or "string",
        "required": bool(required),
        "default": (default or "").strip(),
        "desc": (desc or "").strip(),
        "value": value if value is not None else "",
    }
    if children:
        f["children"] = children
    return f


# ============================================================
# 表格解析（Markdown / HTML）→ 六字段数组
# ============================================================

def _parse_md_table(md_str: str) -> list[dict]:
    """解析 Markdown 参数表为六字段数组 [{name,type,required,default,desc,value}]。"""
    lines = [ln for ln in (md_str or "").strip().split("\n") if ln.strip().startswith("|")]
    if len(lines) < 2:
        return []
    rows = [[c.strip() for c in ln.strip().strip("|").split("|")] for ln in lines]
    header = rows[0]
    col_map = {"name": -1, "type": -1, "required": -1, "default": -1, "desc": -1}
    for idx, h in enumerate(header):
        hl = h.lower()
        if col_map["name"] == -1 and ("名称" in h or "name" in hl or "参数" in h or "字段" in h):
            col_map["name"] = idx
        elif col_map["type"] == -1 and ("类型" in h or "type" in hl):
            col_map["type"] = idx
        elif col_map["required"] == -1 and ("必须" in h or "required" in hl):
            col_map["required"] = idx
        elif col_map["default"] == -1 and ("默认" in h or "default" in hl):
            col_map["default"] = idx
        elif col_map["desc"] == -1 and ("备注" in h or "说明" in h or "desc" in hl or "描述" in h):
            col_map["desc"] = idx

    result = []
    for row in rows[1:]:
        if all(set(c) <= {"-", ":", " "} for c in row if c):
            continue  # 分隔行
        def _cell(i):
            return row[i] if 0 <= i < len(row) else ""
        name_val = _cell(col_map["name"]).strip()
        if not name_val:
            continue
        result.append(_make_field(
            name_val,
            _cell(col_map["type"]),
            _is_required(_cell(col_map["required"])),
            _cell(col_map["default"]),
            _cell(col_map["desc"]),
        ))
    return result


def _parse_html_table(html_str: str) -> list[dict]:
    """解析 HTML 表格为六字段数组（支持 YApi 缩进树形嵌套 → children）。"""
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html_str, "html.parser")
    table = soup.find("table")
    if not table:
        return []
    headers = []
    for th in table.find_all("th"):
        key = th.get("key", th.get_text(strip=True))
        headers.append(key)
    if not headers:
        return []
    # 列名映射（YApi 格式：名称/类型/是否必须/默认值/备注）
    col_map = {"name": -1, "type": -1, "required": -1, "default": -1, "desc": -1}
    for idx, h in enumerate(headers):
        hl = h.lower()
        if "名称" in h or "name" in hl or "字段" in h or "参数" in h:
            col_map["name"] = idx
        elif "类型" in h or "type" in hl:
            col_map["type"] = idx
        elif "必须" in h or "required" in hl:
            col_map["required"] = idx
        elif "默认" in h or "default" in hl:
            col_map["default"] = idx
        elif "备注" in h or "desc" in hl or "说明" in h:
            col_map["desc"] = idx

    rows = table.find_all("tr")
    result = []
    stack = [(result, -1)]  # (parent_list, indent_level)

    for tr in rows:
        cells = tr.find_all("td")
        if len(cells) < 2:
            continue
        # 缩进层级（YApi 用 span padding-left 表示嵌套）
        first_cell = cells[0]
        spans = first_cell.find_all("span")
        indent = 0
        for sp in spans:
            style = sp.get("style", "")
            if "padding-left" in style:
                try:
                    px = int(re.search(r"padding-left:\s*(\d+)px", style).group(1))
                    indent = max(indent, px // 20)  # 每 20px 一级
                except (ValueError, AttributeError):
                    pass

        def _cell_text(idx):
            if idx < 0 or idx >= len(cells):
                return ""
            t = cells[idx].get_text(separator=" ", strip=True)
            t = re.sub(r"^\s*[├└]─?\s*", "", t)
            return t.strip()

        name_val = _cell_text(col_map["name"])
        type_val = _cell_text(col_map["type"])
        if not name_val:
            continue

        item = _make_field(
            name_val, type_val,
            _is_required(_cell_text(col_map["required"])),
            _cell_text(col_map["default"]),
            _cell_text(col_map["desc"]),
        )
        # 处理层级：弹出比当前缩进更深的栈
        while len(stack) > 1 and stack[-1][1] >= indent:
            stack.pop()
        parent_list = stack[-1][0]
        parent_list.append(item)
        # 嵌套类型准备 children
        if type_val and ("object" in type_val.lower() or "[]" in type_val or "array" in type_val.lower()):
            item["children"] = []
            stack.append((item["children"], indent))
    return result


def _parse_header_table(table_text: str) -> dict:
    """解析 Headers 表为 {名称: 示例值} 映射。

    纯 Markdown 表列：参数名称/参数值/是否必须/示例/备注（值取「示例」列，缺失用「参数值」）。
    HTML 表（YApi）：复用 _parse_html_table，value 无示例为空串。
    """
    if "<table" in table_text:
        fields = _parse_html_table(table_text)
        return {f["name"]: f["value"] for f in fields}
    lines = [ln for ln in (table_text or "").strip().split("\n") if ln.strip().startswith("|")]
    if len(lines) < 2:
        return {}
    rows = [[c.strip() for c in ln.strip().strip("|").split("|")] for ln in lines]
    header = rows[0]
    name_i = value_i = example_i = -1
    for idx, h in enumerate(header):
        hl = h.lower()
        if name_i == -1 and ("名称" in h or "name" in hl):
            name_i = idx
        elif example_i == -1 and ("示例" in h or "example" in hl):
            example_i = idx
        elif value_i == -1 and ("参数值" in h or "值" in h or "value" in hl):
            value_i = idx

    result = {}
    for row in rows[1:]:
        if all(set(c) <= {"-", ":", " "} for c in row if c):
            continue
        def _cell(i):
            return row[i] if 0 <= i < len(row) else ""
        if name_i < 0 or name_i >= len(row):
            continue
        name_val = _cell(name_i).strip()
        if not name_val:
            continue
        val = ""
        if 0 <= example_i < len(row):
            candidate = _cell(example_i).strip()
            if candidate and candidate != "-":
                val = candidate
        if not val and 0 <= value_i < len(row):
            val = _cell(value_i).strip()
        result[name_val] = val
    return result


# ============================================================
# 示例捕获
# ============================================================

def _norm_example(v):
    """示例值规范化为字符串：None → ""，bool → true/false，其余 str()。"""
    if v is None:
        return ""
    if isinstance(v, bool):
        return "true" if v else "false"
    return str(v)


def _extract_json_example(section_text: str) -> dict:
    """从代码块提取 JSON 示例为 {字段名: 字符串值} 扁平映射。

    数组示例取首个元素（若为对象）。
    """
    m = re.search(r"```[a-zA-Z]*\n(.*?)```", section_text or "", re.DOTALL)
    if not m:
        return {}
    try:
        data = json.loads(m.group(1).strip())
    except (json.JSONDecodeError, TypeError):
        return {}
    if isinstance(data, dict):
        return {k: _norm_example(v) for k, v in data.items()}
    if isinstance(data, list) and data and isinstance(data[0], dict):
        return {k: _norm_example(v) for k, v in data[0].items()}
    return {}


def _apply_examples(fields: list[dict], example_map: dict) -> None:
    """按字段名把示例值写入 value（含 children 递归）；示例有但字段表缺的补入。"""
    if not example_map:
        return
    known: set[str] = set()

    def _walk(items):
        names = set()
        for f in items:
            names.add(f["name"])
            if f["name"] in example_map:
                f["value"] = example_map[f["name"]]
            if f.get("children"):
                names |= _walk(f["children"])
        return names

    known = _walk(fields)
    for k, v in example_map.items():
        if k not in known:
            fields.append(_make_field(k, "string", False, "", "", v))


def _example_block(part: str, title: str) -> str:
    """取「### title」小节内容（到下一个 # 标题或结尾）。"""
    m = re.search(r"###\s*" + re.escape(title) + r"\s*\n(.*?)(?=\n#{1,3}\s|\Z)",
                  part, re.DOTALL)
    return m.group(1) if m else ""


# ============================================================
# 切分 API 段
# ============================================================

def _detect_api_level(text: str) -> int:
    """识别文档 API 标题层级：YApi → 2（##），纯 Markdown → 1（#）。"""
    has_yapi = "**Path：" in text or "**Method：" in text
    has_clean = "**接口路径" in text or "**接口名称" in text
    return 1 if (has_clean and not has_yapi) else 2


def _split_text_by_headers(text: str, max_chars: int) -> list:
    """按文档 API 标题层级切分，每个 API 独立成段（含标题前缀）。

    用于 LLM 提取分批：前缀（模块标题/文档标题）合并进第一个 API 段，
    供 LLM 理解上下文。max_chars 仅保留接口签名，当前不截断。
    """
    level = _detect_api_level(text)
    pat = re.compile(r"(?m)^#{" + str(level) + r"}\s+")
    starts = [m.start() for m in pat.finditer(text)]
    if not starts:
        return [text.strip()] if text.strip() else []
    parts = []
    for i, s in enumerate(starts):
        e = starts[i + 1] if i + 1 < len(starts) else len(text)
        part = text[s:e].strip()
        if part:
            parts.append(part)
    # 前缀（第一个 API 标题之前的标题行等）并入第一个 API 段
    prefix = text[:starts[0]].strip()
    if prefix and parts:
        parts[0] = prefix + "\n\n" + parts[0]
    return parts


def _split_clean_api_sections(text: str) -> list[str]:
    """切分 API 段并丢弃非 API 前缀（代码提取用，避免前缀污染接口名）。"""
    level = _detect_api_level(text)
    pat = re.compile(r"(?m)^#{" + str(level) + r"}\s+")
    starts = [m.start() for m in pat.finditer(text)]
    if not starts:
        return []
    sections = []
    for i, s in enumerate(starts):
        e = starts[i + 1] if i + 1 < len(starts) else len(text)
        part = text[s:e].strip()
        if part:
            sections.append(part)
    return sections


# ============================================================
# 单段解析：YApi HTML / 纯 Markdown 分发
# ============================================================

def _is_yapi_section(part: str) -> bool:
    return "**Path：" in part or "**Method：" in part or "<table" in part


def _parse_yapi_section(part: str) -> dict:
    """解析 YApi 导出格式的单 API 段。"""
    first_line = part.split("\n")[0].strip()
    name_m = re.search(r"(?m)^##\s+(.+?)\s*$", part)
    name = name_m.group(1).strip() if name_m else re.sub(r"^#+\s*", "", first_line)

    url_match = re.search(r"\*\*Path[：:]\*\*\s*(.+)", part)
    url = url_match.group(1).strip() if url_match else ""
    method_match = re.search(r"\*\*Method[：:]\*\*\s*(.+)", part)
    method = method_match.group(1).strip().upper() if method_match else "?"

    # 接口描述：YApi 空描述导出为 <p></p>，捕获后剥掉 HTML 残留
    desc_match = re.search(r"\*\*接口描述[：:]\*\*\s*\n?\s*(?:<p[^>]*>)?(.*?)(?:</p>)?\s*\n", part)
    description = ""
    if desc_match:
        description = re.sub(r"<[^>]+>", "", desc_match.group(1)).strip()

    # ── 请求参数区（### 请求参数 → ### 返回数据）──
    # 注意：标题后只吃一个换行（[ \t\r]*\n），若用 \s*\n 会吃掉空行，
    # 导致「### 请求参数\n\n### 返回数据」把返回区吞进请求区（回信封 bug）。
    req_section = ""
    req_match = re.search(r"### 请求参数[ \t\r]*\n(.*?)(?=\n### 返回数据|\Z)", part, re.DOTALL)
    if req_match:
        req_section = req_match.group(1)

    def _subsection(sec: str, title: str) -> str:
        """取 **title** 标题后的子段内容（到下一个 **标题 或 ### 或结尾）。"""
        m = re.search(r"\*\*" + re.escape(title) + r"\*\*[ \t\r]*\n(.*?)(?=\n\*\*|\n###\s|\Z)",
                      sec, re.DOTALL)
        return m.group(1) if m else ""

    hdr_section = _subsection(req_section, "Headers")
    query_section = _subsection(req_section, "Query")
    body_section = _subsection(req_section, "Body")

    header = _parse_header_table(hdr_section) if hdr_section else {}
    body = []
    if query_section:
        body.extend(_parse_md_table(query_section) or _parse_html_table(query_section))
    if body_section:
        body.extend(_parse_md_table(body_section) or _parse_html_table(body_section))
    # 请求区无 **Headers/Query/Body 子段时，整区当作 body 表
    if not body and req_section.strip():
        body = _parse_md_table(req_section) or _parse_html_table(req_section)

    # ── 返回数据区 ──
    ret = []
    ret_match = re.search(r"### 返回数据[ \t\r]*\n(.*?)(?=\n##|\Z)", part, re.DOTALL)
    if ret_match:
        ret = _parse_md_table(ret_match.group(1)) or _parse_html_table(ret_match.group(1))

    # ── 示例捕获（请求示例/返回示例 代码块）──
    req_ex = _extract_json_example(_example_block(part, "请求示例"))
    ret_ex = _extract_json_example(_example_block(part, "返回示例"))
    if req_ex:
        _apply_examples(body, req_ex)
    if ret_ex:
        _apply_examples(ret, ret_ex)

    return {
        "name": name,
        "url": url,
        "method": method,
        "description": description or name,
        "header": header,
        "body": body,
        "return": ret,
        "annotations": {},
    }


def _parse_clean_md_section(part: str) -> dict:
    """解析纯 Markdown 格式的单 API 段（健身房接口文档）。"""
    # 接口名称（markdown 加粗闭合形式：**接口名称**：xxx）
    name = ""
    name_m = re.search(r"\*\*接口名称\*\*[：:]\s*(.+)", part)
    if name_m:
        name = name_m.group(1).strip()
    if not name:
        h1_m = re.search(r"(?m)^#\s+(.+?)\s*$", part)
        name = h1_m.group(1).strip() if h1_m else ""

    # 接口路径：**接口路径**：`POST /gymFacility/add`
    url, method = "", "?"
    path_m = re.search(r"\*\*接口路径\*\*[：:]\s*(.+)", part)
    if path_m:
        path_line = path_m.group(1).strip().strip("`")
        pm = re.match(r"([A-Za-z]+)\s+(\S+)", path_line)
        if pm and pm.group(1).upper() in ("GET", "POST", "PUT", "DELETE", "PATCH"):
            method = pm.group(1).upper()
            url = pm.group(2).strip().rstrip("/")
        else:
            url = path_line.rstrip("/")

    header = {}
    body = []
    ret = []
    req_ex = {}
    ret_ex = {}

    # 按 ### 子标题切分并分类（标题只取首行，内容为余下整段）
    for seg in re.split(r"(?=\n### )", part):
        seg = seg.strip()
        if not seg:
            continue
        seg_m = re.match(r"^###\s+(.+?)\s*(?=\n|\Z)", seg)
        if not seg_m:
            continue
        title = seg_m.group(1).strip()
        content = seg[seg_m.end():].strip()
        t = title.lower()
        if "示例" in t:
            if "返回" in t:
                ret_ex = _extract_json_example(content)
            else:
                req_ex = _extract_json_example(content)
        elif "header" in t or "请求头" in t:
            header = _parse_header_table(content)
        elif "返回" in t or "return" in t or "response" in t:
            ret = _parse_md_table(content) or _parse_html_table(content)
        elif "body" in t or "参数" in t or "请求" in t or "query" in t:
            body = _parse_md_table(content) or _parse_html_table(content)

    if req_ex:
        _apply_examples(body, req_ex)
    if ret_ex:
        _apply_examples(ret, ret_ex)

    return {
        "name": name,
        "url": url,
        "method": method,
        "description": name,
        "header": header,
        "body": body,
        "return": ret,
        "annotations": {},
    }


# ============================================================
# 入口
# ============================================================

def extract_apis_from_yapi_md(text: str) -> dict:
    """纯代码提取：解析 YApi 导出 MD 或纯 Markdown 接口文档。

    Returns: {"apis": [...], "module_name": str}
      每个 api: {name, url, method, description, header, body, return, annotations}
        header: 请求头 名→值 映射
        body/return: 六字段数组 [{name,type,required,default,desc,value}]
    """
    # 模块名：优先 YApi <h1>，其次文档首个 # 标题
    module_name = ""
    h1_match = re.search(r"<h1[^>]*>(.+?)</h1>", text)
    if h1_match:
        module_name = re.sub(r"<[^>]+>", "", h1_match.group(1)).strip()
    if not module_name:
        m = re.search(r"(?m)^#\s+(.+?)\s*$", text)
        module_name = m.group(1).strip() if m else ""

    sections = _split_clean_api_sections(text)
    apis = []
    for part in sections:
        api = _parse_yapi_section(part) if _is_yapi_section(part) else _parse_clean_md_section(part)
        if api and (api.get("name") or api.get("url")):
            apis.append(api)

    return {"apis": apis, "module_name": module_name}


def _coerce_api_format(api: dict) -> dict:
    """把任意来源的 API dict 归一化为新结构（header 映射 + body/return 六字段数组）。

    幂等：已是新结构的 dict 原样通过。兼容旧格式：
      headers(list)/parameters(list)/returns(list) → header(dict)/body(list)/return(list)
    """
    out = dict(api)
    # 旧 key 迁移
    if "header" not in out and "headers" in out:
        out["header"] = out.pop("headers")
    if "body" not in out and "parameters" in out:
        out["body"] = out.pop("parameters")
    if "return" not in out and "returns" in out:
        out["return"] = out.pop("returns")
    out.pop("headers", None)
    out.pop("parameters", None)
    out.pop("returns", None)

    # header 必须是名→值映射
    h = out.get("header")
    if isinstance(h, list):
        d = {}
        for it in h:
            if isinstance(it, dict) and it.get("name"):
                d[it["name"]] = it.get("value", it.get("default", ""))
        out["header"] = d
    elif h is None:
        out["header"] = {}

    # body/return 必须是数组，元素归一为六字段
    for k in ("body", "return"):
        v = out.get(k)
        if isinstance(v, dict):
            out[k] = ([{"name": n, "type": t, "required": False, "default": "",
                        "desc": "", "value": ""} for n, t in v.items()]
                      if v else [])
        elif v is None:
            out[k] = []
        elif isinstance(v, list):
            out[k] = [_normalize_field_item(f) for f in v if isinstance(f, dict)]
    return out


def _normalize_field_item(f: dict) -> dict:
    """旧字段元素 {name,type,required,description,default,children} → 六字段。"""
    out = {
        "name": (f.get("name") or "").strip(),
        "type": (f.get("type") or "").strip() or "string",
        "required": bool(f.get("required")),
        "default": (f.get("default") or "").strip(),
        "desc": (f.get("desc") or f.get("description") or "").strip(),
        "value": f.get("value", ""),
    }
    children = f.get("children")
    if children:
        out["children"] = [_normalize_field_item(c) for c in children if isinstance(c, dict)]
    return out


def _merge_api_defs(existing: dict, incoming: dict) -> dict:
    """合并同一接口的两个版本（method+url 相同），而非简单覆盖。

    合并策略：
      - body/return: 取两套字段的并集，incoming 的字段优先
      - header: 字典字段级合并，incoming 优先
      - description: 取更详细（更长）的那一个
      - name/method/url: 保留 incoming（新版本为准）
    """
    merged = dict(incoming)  # 以新版本为基底

    def _merge_list(key: str):
        incoming_val = incoming.get(key, []) or []
        existing_val = existing.get(key, []) or []
        if isinstance(incoming_val, list) and isinstance(existing_val, list):
            # 并集：按字段名对齐，incoming 的字段定义优先
            by_name: dict = {}
            for f in existing_val:
                if isinstance(f, dict) and f.get("name"):
                    by_name.setdefault(f["name"], f)
            for f in incoming_val:
                if isinstance(f, dict) and f.get("name"):
                    by_name[f["name"]] = f
            merged[key] = list(by_name.values())
        elif isinstance(incoming_val, dict) and isinstance(existing_val, dict):
            mv = dict(existing_val)
            mv.update(incoming_val)
            merged[key] = mv
        else:
            merged[key] = incoming_val

    for key in ("body", "return"):
        _merge_list(key)

    # header 字典合并
    existing_header = existing.get("header", {}) or {}
    incoming_header = incoming.get("header", {}) or {}
    if isinstance(existing_header, dict) and isinstance(incoming_header, dict):
        merged["header"] = {**existing_header, **incoming_header}
    else:
        merged["header"] = incoming_header

    # description 保留更详细的那个
    desc_existing = (existing.get("description") or "").strip()
    desc_incoming = (incoming.get("description") or "").strip()
    merged["description"] = desc_incoming if len(desc_incoming) >= len(desc_existing) else desc_existing

    return merged


def _extract_valid_api_paths(full_text: str) -> set[tuple[str, str]]:
    """从接口文档中提取所有合法的 (METHOD, URL) 白名单。

    兼容两种路径写法：
      - YApi:  **Path：** /xxx + **Method：** POST
      - 纯MD:  **接口路径**：`POST /xxx`
    返回 {(METHOD_UPPER, url), ...} 集合，用于过滤 LLM 幻觉的接口。
    """
    valid: set[tuple[str, str]] = set()

    # YApi 写法（Path 在前 Method 在后）
    path_re = re.compile(r"\*\*Path[：:]\*\*\s+(/\S+)")
    method_re = re.compile(r"\*\*Method[：:]\*\*\s+(\w+)")
    for path_m in path_re.finditer(full_text):
        url = path_m.group(1).strip().rstrip("/")
        if not url:
            continue
        search_end = min(len(full_text), path_m.end() + 500)
        method_m = method_re.search(full_text[path_m.end():search_end])
        if method_m:
            valid.add((method_m.group(1).strip().upper(), url))

    # 纯 MD 写法：**接口路径**：`POST /xxx`
    gym_re = re.compile(r"\*\*接口路径\*\*[：:]\s*`?\s*([A-Za-z]+)\s+(\S+)\s*`?")
    for m in gym_re.finditer(full_text):
        method = m.group(1).upper()
        url = m.group(2).strip().strip("`").rstrip("/")
        if url.startswith("/"):
            valid.add((method, url))

    return valid
