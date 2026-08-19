"""独立重放修复轮：验证 schema 注入后真实 LLM 不再漏 PRE.name。

背景：2026-08-18 修复节点 `_invoke_structured(repair_prompt, ExcelPlanV2, method="json_mode")`
在 langchain json_mode 下 schema 不进请求体 → 修复 LLM 看不到 SharedPrecondition 结构，
输出 PRE 时漏必填 name → 3 次重试全失败 → 整个 Phase B 工作流失败。
修复：repair prompt 注入 ExcelPlanV2.model_json_schema() + PRE 对象示例 + 字段硬约束。

本脚本不跑完整工作流，直接调修复轮同款调用路径，用真实 LLM 验证修复有效性：
  1. 从 logs/thinking_trace.log 提取上次失败运行的真实 PRE（缺失时用内置样例）
  2. 构造触发「PRE 需修正」的场景（PRE-002 步骤含错误 URL → 命中修复规则3）
  3. 调 invoke_structured（同 nodes.py 修复调用点，含 json_schema 注入）
  4. 验收：ExcelPlanV2 解析成功；若 shared_preconditions 非空，每个 PRE 都含 name

用法：python scripts/verify_repair_prompt_schema.py
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
from prompts.extraction_prompts import repair_excel_plan_prompt
from prompts.response_model import ExcelPlanV2
from agent_components.llm_client import _get_llm, invoke_structured
from agent_components.nodes import METHOD_FEATURES


# ---- 内置样例（trace 缺失时的兜底，与线上真实 PRE 同构） ----
_FALLBACK_PRES = [
    {"id": "PRE-001", "name": "创建单一费率电表（未绑定、无数据）",
     "steps": "1.调用 POST /electricMeter/add，填写电表名称\"测试电表A\"、电表编号\"METER001\"。\n"
              "2.确认返回创建成功。",
     "expected": "1.[eq]返回200，创建成功。\n2.[eq]创建成功。"},
    {"id": "PRE-002", "name": "创建分时电表（未绑定、无数据）",
     "steps": "1.调用 POST /electricMeter/addXxx，填写电表名称\"测试电表B\"、电表编号\"METER002\"。\n"
              "2.确认返回创建成功。",  # 故意错误 URL，触发修复规则3
     "expected": "1.[eq]返回200，创建成功。\n2.[eq]创建成功。"},
    {"id": "PRE-003", "name": "创建固定定价计费方案（未绑定）",
     "steps": "1.调用 POST /payConfig/insert，填写计费方案名称\"固定方案A\"。\n2.确认返回创建成功。",
     "expected": "1.[eq]返回200，创建成功。\n2.[eq]创建成功。"},
]


def _load_real_pres() -> list[dict]:
    """从 thinking_trace.log 提取上次失败运行的 14 条真实 PRE。"""
    path = "logs/thinking_trace.log"
    if not os.path.exists(path):
        return _FALLBACK_PRES
    try:
        lines = open(path, encoding="utf-8").read().splitlines()
        start = None
        for i, l in enumerate(lines):
            if ("generate_plan_thinking" in l and "思考内容" not in l
                    and any(ts in l for ts in ("11:14", "2026-08-18"))):
                start = i
        if start is None:
            return _FALLBACK_PRES
        buf = []
        for l in lines[start + 1:]:
            if "=== END" in l:
                break
            buf.append(l)
        text = "\n".join(buf)
        s = text.find("{")
        obj = json.loads(text[s:])
        pres = obj.get("shared_preconditions", [])
        if not pres:
            return _FALLBACK_PRES
        # 只保留 id/name/steps/expected，用于构造共享前置参考段
        return [{"id": p["id"], "name": p["name"],
                 "steps": p["steps"], "expected": p["expected"]} for p in pres]
    except Exception as e:  # noqa: BLE001 — 诊断脚本，解析失败回退样例
        print(f"[warn] trace 解析失败，使用内置样例: {e}")
        return _FALLBACK_PRES


def _render_shared_pre_section(pres: list[dict]) -> str:
    return "\n\n".join(
        f"{p['id']}（{p['name']}）\n{p['steps']}\n预期：{p['expected']}"
        for p in pres)


def main() -> int:
    pres = _load_real_pres()
    print(f"使用 {len(pres)} 条共享前置（来自真实 trace 或内置样例）")

    # PRE-002 故意保留错误 URL（若 trace 中为正确 URL，此处覆盖触发修复）
    for p in pres:
        if p["id"] == "PRE-002":
            p["steps"] = ("1.调用 POST /electricMeter/addXxx，填写电表名称\"测试电表B\"、"
                          "电表编号\"METER002\"。\n2.确认返回创建成功。")

    shared_pre_section = _render_shared_pre_section(pres)
    failed_test_cases = (
        "TC ID: TC-070\n"
        "  子模块: 电表管理\n"
        "  标题: 电表管理-新增分时电表-正向\n"
        "  步骤: 1. 调用 POST /electricMeter/add，填写分时电表参数。\n2. 确认返回创建成功。\n"
        "  预期: 1.[eq] 返回200，创建成功。\n"
        "  错误: preconditions 引用的 PRE-002 步骤中 URL '/electricMeter/addXxx' 无法匹配接口列表，需修正\n"
        "---\n"
        "TC ID: TC-071\n"
        "  子模块: 电表管理\n"
        "  标题: 电表管理-新增分时电表-逆向-必填缺失\n"
        "  步骤: 1. 调用 POST /electricMeter/add，省略必填字段电表名称。\n"
        "  预期: 1.[eq] 返回400，提示电表名称必填。\n"
        "  错误: steps 与 expected 行数不一致（1 vs 1 已对齐，但需复核）"
    )
    block_reasons = (
        "- PRE-002 共享前置步骤引用了错误接口路径 /electricMeter/addXxx，"
        "请修正为 /electricMeter/add（在 shared_preconditions 中按原 id 输出修正版）"
    )
    all_apis_info = json.dumps(
        [{"name": "新增电表", "method": "POST", "url": "/electricMeter/add",
          "description": "新增电表"},
         {"name": "新增计费方案", "method": "POST", "url": "/payConfig/insert",
          "description": "新增计费方案"}],
        indent=2, ensure_ascii=False)
    db_schema = ""  # 为空，禁止 [db] 断言

    print("调用真实 LLM 重放修复轮（method=json_mode + json_schema 注入）...")
    try:
        plan = invoke_structured(
            _get_llm(), repair_excel_plan_prompt(), ExcelPlanV2, METHOD_FEATURES,
            method="json_mode",
            json_schema=json.dumps(
                ExcelPlanV2.model_json_schema(), ensure_ascii=False, indent=2),
            failed_test_cases=failed_test_cases,
            block_reasons=block_reasons,
            module_tree="[]",
            analysis_section="（无）",
            shared_pre_section=shared_pre_section,
            cases_section="（无）",
            all_apis_info=all_apis_info,
            db_schema=db_schema,
        )
    except RuntimeError as e:
        print("❌ 修复轮仍失败：")
        print(e)
        return 1

    pres_out = plan.shared_preconditions
    tcs_out = plan.test_cases
    print("✅ ExcelPlanV2 解析成功")
    print(f"   shared_preconditions: {len(pres_out)} 条, test_cases: {len(tcs_out)} 条")
    for p in pres_out:
        ok = bool(p.name)
        print(f"   PRE {p.id}: name={'✓' if ok else '✗ 缺失!'} -> {p.name!r}")
        if not ok:
            print("❌ 仍有 PRE 缺失 name")
            return 1
    if pres_out:
        print("✅ 修复输出中的 PRE 全部含 name（schema 注入生效）")
    else:
        print("（LLM 判定无需修正 PRE，输出空 shared_preconditions——合法结果）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
