"""A2 形态压测 n=5（2026-09-08）：json_object + thinking=enabled，同一份 92 用例负载。

目的：确认 09-02「双开 83% 空 content」的坑在当前 deepseek-v4-flash 是否还在。
判据（每轮）：
  - 空 content ？
  - finish_reason == stop / length（length=被顶，含 8192 与服务端上限）
  - json.loads 可解析？
  - 顶层含 shared_preconditions / test_cases？条数？
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import infrastructure.config as config
from infrastructure.llm.client import _get_llm
from prompts.extraction_prompts import generate_excel_plan_format_prompt
from prompts.response_model import ExcelPlanV2

TRACE = "logs/thinking_trace.log"
N = 5


def latest_phase1_body() -> str:
    lines = open(TRACE, encoding="utf-8", errors="replace").read().splitlines()
    start = None
    for i, l in enumerate(lines):
        if "*** analyze_test_points_raw ***" in l:
            start = i
    if start is None:
        raise SystemExit("未找到 analyze 头")
    end = next((j for j in range(start + 1, len(lines))
                if lines[j].strip() == "=== END analyze_test_points_raw ==="), len(lines))
    body = lines[start + 1:end]
    if body and body[0].strip().startswith("用户输入"):
        body = body[1:]
    if body and body[0].strip().startswith("--- LLM 输出"):
        body = body[1:]
    return "\n".join(body).strip()


def tally(log, ok="", fail="", extra=""):
    prefix = "  ✓" if not fail else "  ✗"
    print(f"{prefix} {log:34} {ok}{fail}{extra}")


def main():
    analysis = latest_phase1_body()
    msgs = generate_excel_plan_format_prompt().format_messages(
        json_schema=json.dumps(ExcelPlanV2.model_json_schema(), ensure_ascii=False, indent=2),
        analysis_text=analysis,
        api_definitions="（22 个接口定义已并入上方分析：车场隔离改造，详见分析内各接口 URL 与场景。）",
        db_schema=config.DB_SCHEMA,
        gen_warning="",
    )
    base = _get_llm()
    print(f"model={config.LLM_MODEL}  分析 chars={len(analysis)}  prompt_est_tokens~{sum(len(m.content or '') for m in msgs)//2}")

    results = {"empty": 0, "length": 0, "ok_json": 0, "valid": 0}
    for i in range(1, N + 1):
        print(f"\n===== round {i}/{N} =====")
        try:
            llm = base.bind(
                temperature=0.4,
                max_tokens=20000,
                response_format={"type": "json_object"},
                extra_body={"thinking": {"type": "enabled"}},
            )
            msg = llm.invoke(msgs)
            meta = getattr(msg, "response_metadata", {}) or {}
            finish = meta.get("finish_reason", meta.get("finish_reasons", "?"))
            content = msg.content if hasattr(msg, "content") else str(msg)
            reasoning = None
            try:
                g = msg.generations[0].message if hasattr(msg, "generations") else msg
                reasoning = g.additional_kwargs.get("reasoning_content")
            except Exception:
                pass
            usage = getattr(msg, "usage_metadata", None) or meta.get("token_usage") or {}
            out_tok = usage.get("output_tokens") or usage.get("completion_tokens") or "?"
            is_empty = not (content and content.strip())
            print(f"finish={finish}  output_tokens={out_tok}  content_chars={len(content or '')}"
                  f"  reasoning_present={bool(reasoning)}  empty={is_empty}")

            if is_empty:
                results["empty"] += 1
                tally(f"round{i} 空 content", fail="空 content（坑仍在？）")
                continue
            if finish == "length":
                results["length"] += 1
                tally(f"round{i} 被顶", fail=f"finish=length {out_tok}")
            try:
                obj = json.loads(content)
                results["ok_json"] += 1
                sp = obj.get("shared_preconditions")
                tc = obj.get("test_cases")
                n_tc = len(tc) if isinstance(tc, list) else "?"
                n_sp = len(sp) if isinstance(sp, list) else "?"
                print(f"  json.loads OK   shared_preconditions={n_sp}  test_cases={n_tc}")
                try:
                    plan = ExcelPlanV2.model_validate(obj)
                    results["valid"] += 1
                    tally(f"round{i} 完整可用",
                          ok=f"ExcelPlanV2 通过: {len(plan.shared_preconditions)} 前置 / {len(plan.test_cases)} 用例")
                except Exception as ve:
                    tally(f"round{i} schema 不符", fail=f"{type(ve).__name__}: {str(ve)[:140]}")
            except Exception as je:
                tally(f"round{i} json 解析失败", fail=f"{type(je).__name__}: {str(je)[:120]}")
        except Exception as e:
            results["empty"] += 1 if "空" in str(e) else 0
            print(f"  EXC: {type(e).__name__}: {str(e)[:200]}")
            tally(f"round{i} 调用异常", fail=f"{type(e).__name__}")
        time.sleep(1)

    print("\n===== 汇总 =====")
    print(f"空 content     : {results['empty']} / {N}")
    print(f"finish=length  : {results['length']} / {N}")
    print(f"json 可解析    : {results['ok_json']} / {N}")
    print(f"ExcelPlanV2 过 : {results['valid']} / {N}")
    print("结论:", "空 content 坑仍在 → 不能靠 A2 松绑" if results["empty"] else
          ("截断普遍 → A2 也不行" if results["length"] >= N else "A2 干净可走一行改（双开空坑在当前模型已消失）"))


if __name__ == "__main__":
    main()
