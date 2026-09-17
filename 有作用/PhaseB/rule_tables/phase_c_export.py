# -*- coding: utf-8 -*-
"""判定表 → Phase C 落盘接线（原样复用 `py_export._generate_py_file`）。

职责边界
--------
本模块**不重新实现** Phase C 的骨架/命名/路径/断言接管逻辑，只做两件事：

  1. 把 `rule_rows.RuleTablePlan` 写成 `_generate_py_file` 认得的 9 列双 Sheet；
  2. 把 `plan.naming` 写成它读的 `<excel同级>/translation_cache.json`。

之后调用 `_generate_py_file(excel_path)` 原样产出 `test_<feature_en>.py`。
YAML 侧由 `yaml_kernel.generate` 独立产出（同一 `output_base`）。

为什么 Excel 必须落在 `智慧停车/` 而不是别处
-------------------------------------------
`_generate_py_file` 里 `output_base = os.path.dirname(excel_path)`，随后
`os.makedirs(output_base/feature_en)`。要让产物落进 `智慧停车/ParkingRuleTable/`，
Excel 就只能在 `智慧停车/` 下。该目录已有 SmartParking 的 `test_plan.xlsx` +
`translation_cache.json`，所以：

  - Excel 用**独立文件名**，不覆盖 `test_plan.xlsx`；
  - 翻译缓存**合并写**（见 `write_translation_cache` 的注释）——缓存按中文键存，
    合并后 SmartParking 的条目原样保留；且 `_translate_to_en` 只在有未命中项时
    才回写缓存，全命中时根本不动盘上文件。
"""
import json
import os
import re

from rule_tables import registry as reg


DEFAULT_EXCEL_NAME = "test_plan_parking_rule_table.xlsx"
CACHE_NAME = "translation_cache.json"

HEADERS_SHEET1 = ["@allure.epic", "@allure.feature", "@allure.story", "@allure.title",
                  "fixture等级", "用例编号", "前置步骤", "执行步骤", "预期结果"]
HEADERS_SHEET2 = ["前置编号", "前置名称", "详细步骤", "预期结果", "关联用例",
                  "依赖前置"]

SHEET1_KEYS = ["epic", "feature", "story", "title", "fixture_level",
               "case_id", "preconditions", "steps", "expected"]
SHEET2_KEYS = ["id", "name", "steps", "expected", "linked_cases", "depends_on"]


# ---------------------------------------------------------------------------
# 写盘
# ---------------------------------------------------------------------------

def write_plan_excel(plan, excel_path: str) -> str:
    """把 plan 写成 Phase C 的 9 列 Sheet1 + 6 列前置 Sheet2。

    列序/列名必须与 `excel._read_excel_rows` / `_read_shared_preconditions`
    完全一致（它们按**位置**取值，不认列名）。表头样式沿用
    `graph/nodes.py` 的既有写法，便于人工核对。

    Sheet2 第 6 列「依赖前置」是 2026-09-15 拆分前置时新增的（最后一行，
    旧 plan 读回来是空串 → 不影响别的产品）。
    """
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

    wb = Workbook()
    hf = Font(bold=True, color="FFFFFF", size=11)
    hfill = PatternFill(start_color="1A73E8", end_color="1A73E8", fill_type="solid")
    tb = Border(left=Side(style="thin"), right=Side(style="thin"),
                top=Side(style="thin"), bottom=Side(style="thin"))
    wa = Alignment(wrap_text=True, vertical="center")

    ws1 = wb.active
    ws1.title = "测试计划"
    for col, h in enumerate(HEADERS_SHEET1, 1):
        c = ws1.cell(row=1, column=col, value=h)
        c.font, c.fill, c.border = hf, hfill, tb
        c.alignment = Alignment(horizontal="center", vertical="center")
    for i, row in enumerate(plan.rows, 2):
        for col, key in enumerate(SHEET1_KEYS, 1):
            c = ws1.cell(row=i, column=col, value=row.get(key, ""))
            c.border, c.alignment = tb, wa

    ws2 = wb.create_sheet("共享前置")
    for col, h in enumerate(HEADERS_SHEET2, 1):
        c = ws2.cell(row=1, column=col, value=h)
        c.font, c.fill, c.border = hf, hfill, tb
        c.alignment = Alignment(horizontal="center", vertical="center")
    for i, pre in enumerate(plan.shared, 2):
        for col, key in enumerate(SHEET2_KEYS, 1):
            c = ws2.cell(row=i, column=col, value=pre.get(key, ""))
            c.border, c.alignment = tb, wa

    os.makedirs(os.path.dirname(excel_path) or ".", exist_ok=True)
    tmp = excel_path + ".tmp"
    try:
        os.remove(tmp)
    except OSError:
        pass
    wb.save(tmp)
    os.replace(tmp, excel_path)
    return excel_path


def write_translation_cache(plan, excel_path: str) -> str:
    """把 `plan.naming` 合并写进 `<excel同级>/translation_cache.json`。

    **合并**而非覆盖：缓存是「中文 → 英文」的字典，同目录（`智慧停车/`）下
    SmartParking 的既有条目必须保留，否则那 82 条用例下次重跑会掉进
    LLM/拼音降级、目录名漂移。键冲突时以本批为准（本批是确定值）。
    """
    path = os.path.join(os.path.dirname(excel_path), CACHE_NAME)
    merged = {}
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                merged = json.load(f)
        except (OSError, ValueError):
            merged = {}
    for bucket, mapping in plan.naming.items():
        merged.setdefault(bucket, {})
        merged[bucket].update(mapping)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(merged, f, ensure_ascii=False, indent=2)
    return path


# ---------------------------------------------------------------------------
# 接线
# ---------------------------------------------------------------------------

def _comment_out_teardown(py_path: str) -> bool:
    """把生成 `.py` 里 fixture 收尾的 teardown 调用整段注释掉。

    为什么在接线层做而不是改 `py_export`：`py_export._generate_py_file` 是 Phase C 的
    **格式权威**，生产管线（SmartParking 等）共用，动它会影响其他 feature 目录。

    为什么需要：teardown 会把本轮建的车场/设备/白名单/包月车/收费规则全删掉
    （14 车场 + 28 设备 + 24 条记录），跑完现场什么都不剩，**无法人工排查失败原因**。
    仅注释（不删文件），恢复时把注释符去掉、或 `export(..., keep_data=False)` 重新生成。

    Returns: 命中原样注释掉了返回 True；没找到（格式变了）返回 False。
    """
    with open(py_path, encoding="utf-8") as f:
        text = f.read()
    # 只认**文件基名**为 teardown_*.yaml 的调用：写成 `[^']*teardown_[^']*` 会连
    # setup 那行一起注释掉 —— 只要路径里任何一段含 "teardown_" 就会误命中
    # （pytest 的 tmp_path 目录名带用例名时必现，例如 …/test_teardown_commented_by_def0/…）。
    pattern = re.compile(
        r"(?P<indent>[ \t]*)base\.run_blocks\(\n"
        r"(?P<line>[ \t]*'[^']*/teardown_[\w\-]+\.yaml'\))\n")
    m = pattern.search(text)
    if not m:
        return False
    block = m.group(0)
    commented = (
        f"{m.group('indent')}# ⚠️ 2026-09-11 临时注释：跑完需保留数据人工排查（用户要求）。\n"
        f"{m.group('indent')}#    恢复：去掉下面两行注释符，或 export(..., keep_data=False) 重新生成。\n"
        + "".join(f"# {ln}\n" for ln in block.rstrip("\n").split("\n"))
    )
    with open(py_path, "w", encoding="utf-8") as f:
        f.write(text.replace(block, commented, 1))
    return True


def export(plan, output_base: str, excel_name: str = DEFAULT_EXCEL_NAME,
           dry_run: bool = False, keep_data: bool = True) -> dict:
    """写 Excel + 翻译缓存，然后调用 Phase C 的 `_generate_py_file`。

    Args:
        plan: `rule_rows.RuleTablePlan`
        output_base: feature 目录的**父目录**（`meta.module_dir` 的上一级）
        excel_name: 落在 output_base 下的计划文件名（勿用 `test_plan.xlsx`）
        dry_run: True 时只写 Excel+缓存并返回将产出的路径，不调生成器
        keep_data: True（**当前默认**）则把 fixture 收尾的 teardown 调用注释掉，
            跑完保留现场供排查；False 恢复为正常收尾删数据。

    Returns:
        `_generate_py_file` 的返回 dict，另加 excel_path / cache_path /
        feature_dir / yaml_dirs 供调用方核对。
    """
    excel_path = os.path.join(output_base, excel_name)
    write_plan_excel(plan, excel_path)
    cache_path = write_translation_cache(plan, excel_path)

    feature_en = reg.get_naming()["feature_en"]
    feature_dir = os.path.join(output_base, feature_en)

    out = {
        "excel_path": excel_path,
        "cache_path": cache_path,
        "feature_dir": feature_dir,
        "py_path": "",
        "py_file_name": "",
        "modules": 0,
        "cases": len(plan.rows),
        "teardown_commented": False,
    }
    if dry_run:
        return out

    from agent_components.graph.nodes import ChatTestAgentGraph
    # object.__new__ 绕过 __init__：本路径不需要 LLM（命名全走缓存），
    # 也不该因为缺 API key 而失败。同 tests/test_py_export_fixture.py 的用法。
    agent = object.__new__(ChatTestAgentGraph)
    # `_log_node_output` 读这两个运行态字段（graph_logging.py:81），
    # 绕过 __init__ 就得自己补齐，否则落盘最后一步抛 AttributeError。
    agent._run_timestamp = None
    agent._run_data = {}
    out.update(agent._generate_py_file(excel_path))

    if keep_data and out.get("py_path"):
        out["teardown_commented"] = _comment_out_teardown(out["py_path"])
    return out


def export_yaml(plan, output_base: str) -> str:
    """同一 output_base 下产出 setup/teardown/逐用例 YAML。"""
    from rule_tables import yaml_kernel as K
    return K.generate(plan, output_base)
