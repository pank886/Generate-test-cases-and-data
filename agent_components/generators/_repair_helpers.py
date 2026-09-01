"""Phase C 生成流程后处理辅助函数（YAML 生成后的 setup 键注入、teardown 容错、阶段合并）。

拆分自 yaml_gen.py（2026-09-01 A+B+C 重构）。这些函数是「生成流程内部后处理」关注点，
与 YamlMixin 的生成主流程分离。生成后静态校验函数（_find_missing_yaml_refs /
_scan_missing_key_refs 等）已迁至 agent_components/validation/yaml_validator.py
（2026-09-01 校验包归位重构），本文件只保留 setup 键注入 / teardown 容错 / 阶段合并。
"""
import glob
import json
import os
import re

import yaml

from infrastructure.observability import get_logger

logger = get_logger(__name__)


# ============================================================
# 三阶段 key 状态传递（2026-08-27 三阶段化设计，见 changelog
# 2026-08-27_three_stage_generation_state_discussion.md §5/§10）
# ============================================================

def _match_pre_label(case_name: str) -> str | None:
    """从 setup 块的 case_name 解析 PRE 标签（兼容生成 / 手工两种命名风格）。

    生成器命名: test_PRE001_add_meter_001 / test_PRE001_isolated_TC007_add_meter_001
    对照组命名: PRE-001_创建测试电表B / PRE-001_isolated_TC-007_创建...
    返回归一化标签: PRE-001 / PRE-001_isolated_TC-007；无法识别返回 None（D4 兜底）。
    """
    m = re.search(r"PRE[-_]?(\d+)", case_name)
    if not m:
        return None
    label = f"PRE-{m.group(1)}"
    iso = re.search(r"isolated[-_]?TC[-_]?(\d+)", case_name, re.IGNORECASE)
    if iso:
        label += f"_isolated_TC-{iso.group(1)}"
    return label


def _parse_setup_extract_keys(output_base: str, expected_pres: list) -> dict:
    """扫描 setup_data/setup_*.yaml，按 case_name 中的 PRE 标识关联块，收集 input_extract 键。

    返回 {"PRE-003": {"keys": {"ELEC_BIND": "$.json.code"}, "case_name": "..."}}。
    base/isolated 变体按 _match_pre_label 归并/区分（isolated 独立成条目）。
    D4 兜底：expected_pres 中无任何关联块、或块无 input_extract 的 PRE，
    注入占位键 {pre_norm}_code = "__MISSING_KEY__" 并抛 Warning（下游提示用）。
    结果同时写盘 `output_base/_setup_extract_keys.json`（跨阶段产物，M8 规则）。
    """
    result = {}
    for fp in glob.glob(os.path.join(output_base, "**", "setup_data", "setup_*.yaml"),
                        recursive=True):
        try:
            data = yaml.safe_load(open(fp, encoding="utf-8"))
        except Exception:
            continue
        for block in data or []:
            for tc in block.get("testCase", []):
                cn = tc.get("case_name") or ""
                pre_label = _match_pre_label(cn)
                if not pre_label:
                    continue
                entry = result.setdefault(pre_label, {"keys": {}, "case_name": cn})
                if not entry.get("case_name"):
                    entry["case_name"] = cn
                for k, v in (tc.get("input_extract") or {}).items():
                    entry["keys"][k] = v
    # D4：期望存在的 PRE 无任何提取键 → 占位 + 告警
    for pid in expected_pres or []:
        if pid not in result:
            placeholder_key = _d4_placeholder_key(pid)
            result[pid] = {"keys": {placeholder_key: "__MISSING_KEY__"},
                           "case_name": f"({pid} setup 生成缺失)"}
            logger.warning("⚠️ D4: %s 无 setup 块（code 提取缺失），注入占位键 %s = __MISSING_KEY__",
                           pid, placeholder_key)
        elif not result[pid]["keys"]:
            placeholder_key = _d4_placeholder_key(pid)
            result[pid]["keys"][placeholder_key] = "__MISSING_KEY__"
            logger.warning("⚠️ D4: %s 的 setup 块无 input_extract，注入占位键 %s = __MISSING_KEY__",
                           pid, placeholder_key)
    _out = os.path.join(output_base, "_setup_extract_keys.json")
    with open(_out, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    logger.info("   🔑 setup 提取键 %d 组 → %s", len(result), _out)
    return result


def _d4_placeholder_key(pre_id: str) -> str:
    """D4 占位键名：PRE-003 → pre003_code（确定性，便于下游引用与后校验识别）。"""
    return re.sub(r"[^0-9A-Za-z]", "", pre_id).lower() + "_code"


def _inject_setup_keys_note(tasks: list, setup_keys: dict, key_field: str) -> None:
    """按任务的引用 PRE 过滤注入键名注解（D3），写入 row['_setup_keys_note']。

    test 任务按 preconditions、teardown 任务按 _pre_ids 取相关 PRE，
    只注入该 PRE 的提取键名，避免无关键混入（防止跨故事串用）。
    注解含 __MISSING_KEY__ 标记 → 下游 prompt 感知缺失并给出兜底策略（D4）。
    未引用任何 PRE 的任务不注入（无 setup 依赖）。
    """
    for row, _path in tasks:
        ref_pres = row.get(key_field) or []
        if isinstance(ref_pres, str):
            ref_pres = [p.strip() for p in ref_pres.split(",")
                        if p.strip().startswith("PRE-")]
        ref_pres = {str(p).strip() for p in ref_pres}
        if not ref_pres:
            continue
        included = [pid for pid in setup_keys if pid in ref_pres]
        if not included:
            continue
        lines = ["### setup 已提取键（引用必须用下列键名）"]
        for pid in included:
            entry = setup_keys[pid]
            for k, v in entry["keys"].items():
                if v == "__MISSING_KEY__":
                    marker = "（⚠️ __MISSING_KEY__：setup 提取失败，请改用硬编码占位值，或在预期结果标注预期失败）"
                else:
                    marker = ""
                lines.append(f"- `{k}` = {v}（{pid} {entry['case_name']}）{marker}")
        row["_setup_keys_note"] = "\n".join(lines)


def _merge_stage_results(stage_results: list, error_entries: list,
                         output_base: str, total: int) -> dict:
    """三阶段 _run_yaml_rounds 结果合并：counts 求和、rounds 取 max。

    rounds 用 max 而非求和：后校验轮触发条件 `result['rounds'] < YAML_REPAIR_ROUNDS`
    与单次调用等价，不会因三阶段求和提前耗尽修复预算。
    方案 A（2026-08-27 决策）：error_entries 已按阶段即时采集（防后阶段覆盖），
    收尾拼接重写一次 _generation_errors.json，placeholder_id 重新编号防跨阶段冲突。
    """
    merged = {"total": total, "success": 0, "failed": 0,
              "repaired": 0, "rounds": 0, "errors_file": None}
    for r in stage_results:
        merged["success"] += r.get("success", 0)
        merged["failed"] += r.get("failed", 0)
        merged["repaired"] += r.get("repaired", 0)
        merged["rounds"] = max(merged["rounds"], r.get("rounds", 0))
    if error_entries:
        for n, e in enumerate(error_entries, 1):
            e["placeholder_id"] = f"GEN-FAIL-{n:03d}"
        errors_file = os.path.join(output_base, "_generation_errors.json")
        with open(errors_file, "w", encoding="utf-8") as f:
            json.dump(error_entries, f, ensure_ascii=False, indent=2)
        merged["errors_file"] = errors_file
    return merged


def _collect_stage_errors(entries: list, r: dict) -> None:
    """即时采集一轮 _run_yaml_rounds 的终态错误清单（转瞬即逝：下一阶段会覆盖同名文件）。"""
    ef = r.get("errors_file")
    if ef and os.path.exists(ef):
        try:
            entries.extend(json.loads(open(ef, encoding="utf-8").read()))
        except Exception as e:
            logger.warning("   ⚠️ 读取 %s 失败: %s", ef, e)


def _filter_teardown_missing_pres(teardown_tasks: list, setup_keys: dict) -> None:
    """teardown 对 __MISSING_KEY__ 的 PRE 跳过清理块（2026-08-27 用户决策，task #10）。

    无提取键的 PRE 未创建可清理资源，占位删除必失败（v7 实测 PRE002_PLACEHOLDER_CODE）。
    从任务源过滤：steps 去掉 `# 清理 {pid}` 行、_pre_ids 剔除缺失 PRE；
    某任务全部 PRE 缺失 → 整任务移除（无清理可做）。在 setup_keys 解析后、
    _inject_setup_keys_note 前调用，teardown 阶段只生成真实键 PRE 的删除块。
    """
    missing_pres = {pid for pid, entry in setup_keys.items()
                    if any(v == "__MISSING_KEY__" for v in entry["keys"].values())}
    if not missing_pres:
        return
    kept = []
    for row, path in teardown_tasks:
        steps = row.get("steps", "") or ""
        lines = [ln for ln in steps.split("\n")
                 if not any(ln.startswith(f"# 清理 {pid}:") for pid in missing_pres)]
        steps_new = "\n".join(lines).strip("\n")
        pre_ids = [pid for pid in (row.get("_pre_ids") or []) if pid not in missing_pres]
        if not steps_new and not pre_ids:
            logger.info("   🧹 teardown %s 全部 PRE 缺失提取键，跳过清理任务", path)
            continue
        row["steps"] = steps_new
        row["_pre_ids"] = pre_ids
        kept.append((row, path))
    teardown_tasks[:] = kept


def _relax_teardown_validation(output_base: str) -> None:
    """teardown 删除块剥离严格断言（2026-08-27 用户决策「teardown 断言容错」，task #9）。

    delete 类用例会消费 setup 资源（v7: delete_positive 删 isolated、delete_bound 删绑定电表），
    teardown 收尾二次删除返回 retCode=0 → 严格 eq 断言必失败（v7 2 ERROR 之一）。
    框架断言仅 eq/ne/contains/db，无集合/或语义可表达 retCode∈{0,1}；
    teardown 语义是清理清扫（幂等）——delete 照发、不校验结果，剥断言为最优解。
    在 teardown 阶段完成后调用（覆盖所有已写盘的 teardown_*.yaml）。

    注意：框架 assert_result 要求 validation 必须是 list（base/apiutil.py
    `tc.pop('validation', '未配置断言')` → None/缺键报 `'expected' 必须是一个列表`，
    2026-08-27 v9 框架实测）。故剥断言 = 置空列表 `[]`（零断言），非删键。
    """
    for fp in glob.glob(os.path.join(output_base, "**", "teardown_*.yaml"), recursive=True):
        try:
            data = yaml.safe_load(open(fp, encoding="utf-8"))
        except Exception:
            continue
        if not data:
            continue
        changed = False
        for block in data:
            for tc in block.get("testCase", []):
                # 缺失或非空都置空列表：缺键时框架读默认字符串会报错（见上），
                # 空列表 = 零断言，框架 isinstance(list) 通过。
                if "validation" not in tc or tc.get("validation"):
                    tc["validation"] = []
                    changed = True
        if changed:
            # 与 _write_yaml_result 相同的序列化参数，保持产物格式一致
            yaml_text = yaml.dump(
                data, allow_unicode=True, indent=2, default_flow_style=False)
            tmp_path = fp + ".tmp"
            try:
                os.remove(tmp_path)
            except OSError:
                pass
            with open(tmp_path, "w", encoding="utf-8") as f:
                f.write(yaml_text)
            os.replace(tmp_path, fp)
