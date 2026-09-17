# -*- coding: utf-8 -*-
"""Phase C 执行结果 → 逐文件权威失败落点（纯函数库，2026-09-09）。

背景：智慧停车 77/111 失败根因已提炼为模块目录约定文件（phase_c_user_context.md）。用户要求
「定向重跑」只跑实际失败 + 导致失败的上游，并给权威失败数据一个**动态落点**：一份保留原位原始日志
（不标逻辑），一份逐 YAML 伴随 `<stem>.last_run.json`（覆盖式，只保留最新一轮）。

本模块是仓库侧本次由 LLM 手动吸收执行结果 / 以及定向重跑入口读取落点的**纯函数库**：
- 不 import nodes / 不入 LangGraph 图；无 DB / 无 LLM。
- 只依赖 os/re/json/glob/xml.etree/yaml。
- 长期由执行框架（PyCharmMiscProject run 层）原生写 `.last_run.json`；届时本模块读取侧不变。

落点 JSON schema（与用户确认一致）：
    { "run_ts": "20260909173746",
      "verdict": "passed"|"failed"|"broken",
      "case_name": ["该文件本轮失败块 case_name ..."],
      "server_reason": ["从原始日志聚合的服务端 data 原因 ..."],
      "junit_msg": "<该文件本轮 junit message 摘要>" }
落点命名：`test_data.yaml → test_data.last_run.json`（同 func 目录）；
          `setup_<slug>.yaml → setup_<slug>.last_run.json`（同 setup_data 目录）。
"""
import ast
import glob
import json
import os
import re
import xml.etree.ElementTree as ET

import yaml

# 目录/文件排除标记（与 Thread F `_list_phase_c_module_dirs` 一致）
_EXCLUDED_TOKENS = (".bak", "_backup", "_regenerated")


# ============================================================
# junit 解析
# ============================================================

def _is_excluded_path(path: str) -> bool:
    return any(tok in path for tok in _EXCLUDED_TOKENS)


def _junit_ts_to_run_ts(ts: str) -> str:
    """'2026-09-09T17:37:46.389723+08:00' → '20260909173746'（取前 19 字符再格式化）。"""
    import datetime
    if not ts:
        return ""
    head = ts[:19]
    try:
        return datetime.datetime.strptime(head, "%Y-%m-%dT%H:%M:%S").strftime("%Y%m%d%H%M%S")
    except ValueError:
        return ""


def _strip_param(func: str) -> str:
    """去 pytest param 尾：'test_x[1]' → 'test_x'。"""
    return re.sub(r"\[\d+\]$", "", func or "")


def parse_junit(junit_path: str) -> list[dict]:
    """按 func 聚合 junit 节点，返回每条 {func,class,verdict,junit_msg,teardown_error}。

    junit 语义（真实文件核验）：setup 破损/teardown 越权都以 <error> 落在依赖它的用例节点上，
    且一个 func 可能同时有 <failure>（body 已跑）与 <error>（teardown）。故**必须按 func 跨节点
    聚合**，verdict 优先级：
      <failure>            → failed（body 真失败）
      else "failed on setup" error → broken（body 未跑，根因在类 setup）
      else 纯 "failed on teardown" error → passed + teardown_error=True（teardown 越权，本批无）
      else                  → passed
    """
    if not junit_path or not os.path.isfile(junit_path):
        raise FileNotFoundError(f"junit.xml 不存在: {junit_path}")
    tree = ET.parse(junit_path)
    root = tree.getroot()
    # 时间戳（供 run_ts 解析）；testsuite 可能在根或包在 testsuites 下
    ts_node = root if root.tag == "testsuite" else root.find("testsuite")
    junit_timestamp = (ts_node.get("timestamp", "") if ts_node is not None else "") or root.get("timestamp", "")

    state: dict[str, dict] = {}
    order: list[str] = []
    for tc in root.iter("testcase"):
        func = _strip_param(tc.get("name", ""))
        cls = (tc.get("classname", "") or "").rsplit(".", 1)[-1]
        if func not in state:
            state[func] = {
                "class": cls, "has_failure": False, "failure_msg": "",
                "setup_err": False, "setup_msg": "", "teardown_err": False,
            }
            order.append(func)
        else:
            state[func]["class"] = cls
        st = state[func]
        for f in tc.findall("failure"):
            st["has_failure"] = True
            m = (f.get("message") or (f.text or "").strip() or "")[:500]
            if m and not st["failure_msg"]:
                st["failure_msg"] = m
        for e in tc.findall("error"):
            m = e.get("message") or (e.text or "").strip() or ""
            if m.startswith("failed on setup"):
                st["setup_err"] = True
                st["setup_msg"] = m[:500] or st["setup_msg"]
            elif m.startswith("failed on teardown"):
                st["teardown_err"] = True
            else:
                # 未知 <error>：保守视作 setup 侧破损（同 setup 前缀路径）
                st["setup_err"] = True
                st["setup_msg"] = m[:500] or st["setup_msg"]

    out = []
    for func in order:
        st = state[func]
        if st["has_failure"]:
            verdict, msg = "failed", st["failure_msg"]
        elif st["setup_err"]:
            verdict, msg = "broken", st["setup_msg"]
        else:
            verdict, msg = "passed", ""
        out.append({
            "func": func,
            "class": st["class"],
            "verdict": verdict,
            "junit_msg": msg,
            "teardown_error": st["teardown_err"],
        })
    return {"records": out, "timestamp": junit_timestamp}


# ============================================================
# 模块布局映射（func/test_data、class/setup）
# ============================================================

def _snake(name: str) -> str:
    """同 yaml_gen:317 class_slug 公式：'TestDeleteWhitelist' → 'test_delete_whitelist'。"""
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name or "").lower()


def _yield_index(fnode: ast.FunctionDef) -> int:
    for i, stmt in enumerate(fnode.body):
        if isinstance(stmt, (ast.Yield, ast.Expr)) and _contains_yield(stmt):
            return i
    return len(fnode.body)


def _contains_yield(node) -> bool:
    for n in ast.walk(node):
        if isinstance(n, ast.Yield):
            return True
    return False


def _setup_slug_from_fixture(fname: str) -> str:
    return fname[len("setup_"):] if fname.startswith("setup_") else fname


def build_module_map(module_dir: str) -> dict:
    """解析模块下生成的 runner .py，给出 func→test_data、class→setup 的**绝对路径**映射。

    优先 AST 解析 `**/test_*.py`（py_export 模板产物）：类级 fixture 名 `setup_<slug>`、pre-yield
    `run_blocks(.../setup_data/setup_<slug>.yaml)` 判真实 setup（pass-only 夹具无 run_blocks，
    不伪造 broken）；方法名 == func_en == test 目录名 → test_data.yaml。
    无 .py → 目录布局兜底（func 目录扫描 + junit 侧类名推导）。

    返回：{"py_files": [...],
           "funcs": {func_en: {"class": cls_name, "test_yaml": abs|None, "feature_dir": abs}},
           "classes": {cls_name: {"slug": slug, "setup_yaml": abs|None, "has_setup": bool}}}
    """
    module_dir = os.path.abspath(module_dir)
    result = {"py_files": [], "funcs": {}, "classes": {}}
    pys = sorted(glob.glob(os.path.join(module_dir, "**", "test_*.py"), recursive=True))
    pys = [p for p in pys if not _is_excluded_path(p)]

    for py in pys:
        result["py_files"].append(py)
        try:
            with open(py, "r", encoding="utf-8") as f:
                src = f.read()
            tree = ast.parse(src)
        except (OSError, SyntaxError):
            continue
        feature_dir = os.path.dirname(py)
        # 模块级 setup_ 夹具：name -> FunctionDef
        fixtures = {}
        for node in tree.body:
            if isinstance(node, ast.FunctionDef) and node.name.startswith("setup_"):
                fixtures[node.name] = node

        # class → fixture slug（经 usefixtures 装饰器）
        def _fixture_of_class(cnode: ast.ClassDef):
            for dec in cnode.decorator_list:
                name = _decorator_str(dec)
                if name and name.startswith("setup_"):
                    return name
            return None

        for cnode in tree.body:
            if not isinstance(cnode, ast.ClassDef) or not cnode.name.startswith("Test"):
                continue
            cls_name = cnode.name
            fx_name = _fixture_of_class(cnode)
            fx = fixtures.get(fx_name) if fx_name else None
            slug = _setup_slug_from_fixture(fx_name) if fx_name else _snake(cls_name[len("Test"):])
            has_setup = False
            setup_yaml = None
            if fx is not None:
                yidx = _yield_index(fx)
                pre = fx.body[:yidx]
                for stmt in pre:
                    for call in _find_run_blocks(stmt):
                        target = call.get("yaml")
                        if target and "/setup_data/setup_" in target:
                            has_setup = True
                if has_setup:
                    setup_yaml = os.path.join(feature_dir, "setup_data", f"setup_{slug}.yaml")
            result["classes"][cls_name] = {
                "slug": slug,
                "setup_yaml": setup_yaml,
                "has_setup": has_setup,
            }
            # class 内 test 方法
            for m in cnode.body:
                if isinstance(m, ast.FunctionDef) and m.name.startswith("test_"):
                    result["funcs"][m.name] = {
                        "class": cls_name,
                        "test_yaml": os.path.join(feature_dir, m.name, "test_data.yaml"),
                        "feature_dir": feature_dir,
                    }

    if result["funcs"]:
        return result

    # ---- 兜底：目录布局扫描（无 runner .py）----
    feature_dirs = []
    for fd in sorted(glob.glob(os.path.join(module_dir, "*"))):
        if not os.path.isdir(fd) or _is_excluded_path(fd):
            continue
        if os.path.isdir(os.path.join(fd, "setup_data")) or \
           glob.glob(os.path.join(fd, "test_*", "test_data.yaml")):
            feature_dirs.append(fd)
    for fd in feature_dirs:
        for d in sorted(glob.glob(os.path.join(fd, "test_*"))):
            if not os.path.isdir(d):
                continue
            test_yaml = os.path.join(d, "test_data.yaml")
            if os.path.isfile(test_yaml):
                result["funcs"][os.path.basename(d)] = {
                    "class": "", "test_yaml": test_yaml, "feature_dir": fd}
    return result


def _decorator_str(dec) -> str:
    """从 usefixtures('setup_x') 这类装饰器表达式取首参字符串字面量（setup_x）。"""
    if isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute) \
            and dec.func.attr == "usefixtures":
        for a in dec.args:
            if isinstance(a, ast.Constant) and isinstance(a.value, str):
                return a.value
        return ""
    return ""


def _find_run_blocks(stmt):
    """在语句子树里找 base.run_blocks('....yaml') 调用，产出 {yaml:...}。"""
    for n in ast.walk(stmt):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) \
                and n.func.attr == "run_blocks":
            for a in n.args:
                if isinstance(a, ast.Constant) and isinstance(a.value, str) \
                        and a.value.endswith(".yaml"):
                    yield {"yaml": a.value}
                    break


def class_setup_abs(map_: dict, module_dir: str, cls_name: str) -> str | None:
    """class → setup_*.yaml 绝对路径（存在才返回）。map 缺 class → slug 推导 + feature_dir 探测。"""
    ce = map_["classes"].get(cls_name) if cls_name else None
    if ce and ce.get("has_setup"):
        cand = ce.get("setup_yaml")
        if cand and os.path.isfile(cand):
            return cand
    slug = _snake(cls_name[len("Test"):]) if cls_name.startswith("Test") else ""
    if not slug:
        return None
    feature_dirs = {fe["feature_dir"] for fe in map_["funcs"].values()}
    for fd in feature_dirs:
        cand = os.path.join(fd, "setup_data", f"setup_{slug}.yaml")
        if os.path.isfile(cand):
            return cand
    # 无 func 布局时的补充探测
    setup_dirs = sorted(glob.glob(os.path.join(os.path.abspath(module_dir), "**", "setup_data"),
                                  recursive=True))
    for sd in setup_dirs:
        cand = os.path.join(sd, f"setup_{slug}.yaml")
        if os.path.isfile(cand):
            return cand
    return None


# ============================================================
# 原始执行日志解析（服务端原因）
# ============================================================

_FAILURE_MARKERS = ("相等断言失败", "包含断言失败", "状态码断言失败", "请求处理异常")


def parse_run_log_blocks(log_path: str) -> list[dict]:
    """把原始执行日志切成请求块。每块 {url, case_name, resp_raw, resp_json, ok, failures, business_suspect}。

    - 块边界：`接口名称:` 行。框架逐请求刷：接口名称/接口请求地址/接口请求方法/测试用例名称/请求头/
      请求参数/接口实际返回信息<json>（**无空格**）/断言行。
    - ok=False = 块内出现失败标记（断言失败或请求处理异常）。
    - business_suspect：无断言失败标记但响应体语义失败（retCode∉(1,None) / msg=="fail" /
      success is False）——软标记，**不**参与 junit verdict，仅提示。
    """
    if not log_path or not os.path.isfile(log_path):
        raise FileNotFoundError(f"执行日志不存在: {log_path}")
    with open(log_path, "r", encoding="utf-8", errors="replace") as f:
        lines = f.read().splitlines()
    blocks: list[dict] = []
    cur = None
    for line in lines:
        if "接口名称:" in line:
            cur = {"url": "", "case_name": "", "resp_raw": "", "resp_json": None,
                   "ok": True, "failures": [], "business_suspect": False}
            blocks.append(cur)
        if cur is None:
            continue
        if "接口请求地址:" in line:
            cur["url"] = line.split("接口请求地址:", 1)[1].strip()
        elif "测试用例名称:" in line:
            cur["case_name"] = line.split("测试用例名称:", 1)[1].strip()
        elif "接口实际返回信息" in line:
            raw = line.split("接口实际返回信息", 1)[1].strip()
            cur["resp_raw"] = raw[:2000]
            cur["resp_json"] = _try_json(raw)
        if any(m in line for m in _FAILURE_MARKERS):
            cur["ok"] = False
            cur["failures"].append(line.strip()[:300])
        elif cur["resp_json"] and cur["ok"]:
            rj = cur["resp_json"]
            rc = rj.get("retCode") if isinstance(rj, dict) else None
            if rc not in (None, 1) or (isinstance(rj, dict) and
                                       (rj.get("msg") == "fail" or rj.get("success") is False)):
                cur["business_suspect"] = True
    return blocks


def _try_json(raw: str):
    if not raw:
        return None
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        return json.loads(raw[start:end + 1])
    except (ValueError, TypeError):
        return None


def read_yaml_case_names(yaml_path: str) -> list[str]:
    """读生成 YAML 各 testCase 块的 case_name（顶层 list：{baseInfo, testCase:[...]}）。"""
    if not yaml_path or not os.path.isfile(yaml_path):
        return []
    try:
        with open(yaml_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except (OSError, yaml.YAMLError):
        return []
    names: list[str] = []
    items = data if isinstance(data, list) else ([data] if isinstance(data, dict) else [])
    for it in items:
        if not isinstance(it, dict):
            continue
        tc = it.get("testCase")
        if isinstance(tc, list):
            for block in tc:
                if isinstance(block, dict) and block.get("case_name"):
                    names.append(str(block["case_name"]))
    return names


def extract_file_failures(blocks: list[dict], yaml_path: str) -> dict:
    """从日志块归因**本文件**的失败块。无匹配/无失败 → 空列表，**不抛**（YAML 漂移优雅降级）。"""
    names = set(read_yaml_case_names(yaml_path))
    if not names:
        return {"case_name": [], "server_reason": []}
    case_names: list[str] = []
    reasons: list[str] = []
    for blk in blocks:
        if blk["ok"] or blk["case_name"] not in names:
            continue
        case_names.append(blk["case_name"])
        reasons.append(_reason_text(blk))
    return {"case_name": case_names, "server_reason": reasons}


def _reason_text(blk: dict) -> str:
    rj = blk.get("resp_json")
    if isinstance(rj, dict):
        bits = []
        if "retCode" in rj:
            bits.append(f"retCode={rj.get('retCode')!r}")
        if "msg" in rj:
            bits.append(f"msg={rj.get('msg')!r}")
        data = rj.get("data")
        if isinstance(data, str) and data:
            bits.append(f"data={data}")
        if bits:
            return " ".join(bits)
    if blk["failures"]:
        return blk["failures"][0]
    raw = (blk.get("resp_raw") or "").strip()
    return raw[:200] if raw else blk.get("url", "")


# ============================================================
# 摄取：junit + 日志 → 逐文件 `.last_run.json`（覆盖式，动态落点）
# ============================================================

def _sidecar_path(yaml_abs: str) -> str:
    return os.path.splitext(yaml_abs)[0] + ".last_run.json"


def resolve_run_ts(explicit: str, junit_timestamp: str, log_path: str | None) -> str:
    """run_ts 优先级：显式 > junit timestamp > 日志文件名 14 位。全无 → ValueError。"""
    if explicit:
        return str(explicit)
    ts = _junit_ts_to_run_ts(junit_timestamp)
    if ts:
        return ts
    if log_path:
        m = re.search(r"test\.(\d{14})\.log", os.path.basename(log_path))
        if m:
            return m.group(1)
    raise ValueError("无法解析 run_ts：请显式传 run_ts，或给 junit_path / log_path")


def ingest_run_results(module_dir: str, junit_path: str,
                       log_path: str | None = None, run_ts: str | None = None) -> dict:
    """摄取一次执行的 junit(+原始日志) → 覆盖写各被执行 YAML 的 `.last_run.json`。

    写盘范围（决策 #2/#4）：
      - executed 的 test_data（junit verdict != broken，body 跑过）→ passed/failed；
      - 被执行 class 的 setup 存在 → passed/broken；
      - broken-by-setup 的 test_data **不写**（body 未执行；旧落点由 read_last_run_records 的
        max-run_ts cohort 过滤）。
    返回 {module-相对 POSIX yaml 路径: record}（record 即 sidecar 内容）。
    """
    module_dir = os.path.abspath(module_dir)
    parsed = parse_junit(junit_path)
    records = parsed["records"]
    run_ts_val = resolve_run_ts(run_ts, parsed["timestamp"], log_path)
    blocks = parse_run_log_blocks(log_path) if log_path else []
    map_ = build_module_map(module_dir)

    def _rel(abs_path: str) -> str:
        return os.path.relpath(abs_path, module_dir).replace(os.sep, "/")

    def _write(yaml_abs: str, verdict: str, case_names, reasons, junit_msg):
        if not os.path.isfile(yaml_abs):
            return
        rec = {
            "run_ts": run_ts_val,
            "verdict": verdict,
            "case_name": case_names or [],
            "server_reason": reasons or [],
            "junit_msg": junit_msg or "",
        }
        sp = _sidecar_path(yaml_abs)
        sp_dir = os.path.dirname(sp)
        tmp = os.path.join(sp_dir, os.path.basename(sp) + ".tmp")
        os.makedirs(sp_dir, exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(rec, f, ensure_ascii=False, indent=2)
        os.replace(tmp, sp)

    # 1) 组 class → broken 集合 + junit_msg（setup 侧破损）
    broken_classes: dict[str, str] = {}          # class -> representative junit_msg
    failed_entries = []                          # (func, class, msg)
    for r in records:
        if r["verdict"] == "broken":
            if r["class"] and r["class"] not in broken_classes:
                broken_classes[r["class"]] = r["junit_msg"]
        elif r["verdict"] == "failed":
            failed_entries.append((r["func"], r["class"], r["junit_msg"]))
    # 2) test_data sidecar（仅 body 跑过的 func）
    out: dict[str, dict] = {}
    for func, cls, msg in failed_entries:
        fe = map_["funcs"].get(func)
        yaml_abs = fe["test_yaml"] if fe else None
        if not yaml_abs:
            continue
        fails = extract_file_failures(blocks, yaml_abs)
        _write(yaml_abs, "failed", fails["case_name"], fails["server_reason"], msg)
        out[_rel(yaml_abs)] = _last_record(yaml_abs, "failed", fails, msg)
    passed_funcs = [r for r in records if r["verdict"] == "passed"]
    for r in passed_funcs:
        fe = map_["funcs"].get(r["func"])
        yaml_abs = fe["test_yaml"] if fe else None
        if not yaml_abs:
            continue
        _write(yaml_abs, "passed", [], [], "")
        out[_rel(yaml_abs)] = _last_record(yaml_abs, "passed", None, "")
    # 3) setup sidecar（被执行 class：broken 集合 ∪ 出现过的 class）
    seen_classes = {r["class"] for r in records if r["class"]}
    for cls in seen_classes:
        setup_abs = class_setup_abs(map_, module_dir, cls)
        if not setup_abs:
            continue
        if cls in broken_classes:
            fails = extract_file_failures(blocks, setup_abs)
            _write(setup_abs, "broken", fails["case_name"], fails["server_reason"],
                   broken_classes[cls])
            out[_rel(setup_abs)] = _last_record(setup_abs, "broken", fails, broken_classes[cls])
        else:
            _write(setup_abs, "passed", [], [], "")
            out[_rel(setup_abs)] = _last_record(setup_abs, "passed", None, "")
    return out


def _last_record(yaml_abs: str, verdict: str, fails, msg: str) -> dict:
    # 重建 record（供 out 返回；fails 为 None → passed 空）
    sp = _sidecar_path(yaml_abs)
    try:
        with open(sp, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        cn = (fails or {}).get("case_name") or [] if isinstance(fails, dict) else []
        sr = (fails or {}).get("server_reason") or [] if isinstance(fails, dict) else []
        return {"run_ts": "", "verdict": verdict, "case_name": cn,
                "server_reason": sr, "junit_msg": msg or ""}


# ============================================================
# 读取落点（定向重跑消费）
# ============================================================

def read_last_run_records(module_dir: str) -> dict:
    """读全部 `.last_run.json`，**只保留 run_ts == max** 的一批（最新一轮 cohort）。

    key = 伴随 YAML 的 module-相对 POSIX 路径。无落点 → {}。
    """
    module_dir = os.path.abspath(module_dir)
    sidecars = sorted(glob.glob(os.path.join(module_dir, "**", "*.last_run.json"),
                                recursive=True))
    loaded: list[tuple[str, str, dict]] = []   # (rel, run_ts, record)
    max_ts = ""
    for sp in sidecars:
        if _is_excluded_path(sp):
            continue
        try:
            with open(sp, "r", encoding="utf-8") as f:
                rec = json.load(f)
        except (OSError, ValueError):
            continue
        if not isinstance(rec, dict):
            continue
        ts = str(rec.get("run_ts") or "")
        # 反推伴随 YAML：'x/test_data.last_run.json' → 'x/test_data.yaml'
        yaml_abs = sp[: -len(".last_run.json")] + ".yaml"
        rel = os.path.relpath(yaml_abs, module_dir).replace(os.sep, "/")
        loaded.append((rel, ts, rec))
        if ts > max_ts:
            max_ts = ts
    if not max_ts:
        return {}
    return {rel: rec for rel, ts, rec in loaded if ts == max_ts}


# ============================================================
# 定向选点 + 失败摘要
# ============================================================

def _is_setup(rel: str) -> bool:
    return "/setup_data/" in rel or rel.startswith("setup_data/")


def select_rerun_targets(module_dir: str, records: dict) -> dict:
    """决策 #6：test 仅 verdict==failed 重生成；setup 仅 verdict∈(broken,failed) 重生成；
    test&verdict==broken（body 未跑）→ excluded_broken 信息性输出（不重生成其 test_data）。

    records 为 {rel: record}（read_last_run_records / ingest 返回，rel = module-相对 POSIX）。
    返回 {"test_yamls": [abs], "setup_yamls": [abs], "excluded_broken": [{rel,class,junit_msg}]}。
    """
    module_dir = os.path.abspath(module_dir)
    test_yamls, setup_yamls, excluded = [], [], []
    for rel, rec in records.items():
        abs_p = os.path.abspath(os.path.join(module_dir, *rel.split("/")))
        verdict = rec.get("verdict")
        if _is_setup(rel):
            if verdict in ("broken", "failed"):
                setup_yamls.append(abs_p)
        else:
            if verdict == "failed":
                test_yamls.append(abs_p)
            elif verdict == "broken":
                excluded.append({"rel": rel,
                                 "junit_msg": rec.get("junit_msg", "")})
    return {"test_yamls": sorted(set(test_yamls)),
            "setup_yamls": sorted(set(setup_yamls)),
            "excluded_broken": excluded}


def build_failure_digest(module_dir: str, records: dict, targets: dict) -> dict:
    """对每个选中文件生成 markdown 失败摘要（文件级；事实无示例）。

    返回 {abs_yaml: digest_str}。摘要内容全来自落点字段（verdict/junit_msg/case_name/server_reason）。
    """
    module_dir = os.path.abspath(module_dir)
    wanted = list(targets.get("test_yamls", [])) + list(targets.get("setup_yamls", []))
    by_abs = {}
    for rel, rec in records.items():
        abs_p = os.path.abspath(os.path.join(module_dir, *rel.split("/")))
        by_abs[abs_p] = rec
    out = {}
    for abs_p in sorted(set(wanted)):
        rec = by_abs.get(abs_p)
        if not rec:
            continue
        rel = os.path.relpath(abs_p, module_dir).replace(os.sep, "/")
        lines = [f"### 本次执行失败反馈（文件级，{rel}）",
                 f"- verdict: {rec.get('verdict')}"]
        if rec.get("junit_msg"):
            lines.append(f"- junit: {rec['junit_msg']}")
        cn = rec.get("case_name") or []
        sr = rec.get("server_reason") or []
        if cn:
            lines.append("- 失败块 case_name: " + ", ".join(cn))
        if sr:
            lines.append("- 服务端返回原因:")
            lines.extend(f"  - {s}" for s in sr)
        lines.append("- 生成该文件数据时请按上述服务端约束修正字段取值；只允许按接口定义与真实主数据取值，"
                     "禁止编造固定数据，禁止给出示例请求/响应。")
        out[abs_p] = "\n".join(lines)
    return out
