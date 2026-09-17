"""离线取证：Phase 2 的 8192 输出钳制到底跟哪个开关走（2026-09-08）。

对照同一份大负载（今天真实 Phase 1 分析文本 = 92 用例），4 种请求形态各调一次：
  A1 json_object + thinking=disabled
  A2 json_object + thinking=enabled
  B1 无 response_format + thinking=disabled
  B2 无 response_format + thinking=enabled

max_tokens 统一绑 20000（>8192，让服务端钳制显形；<393216 以控成本）。
目的：确认 8192 是跟 response_format 还是跟 thinking 开关走。
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import infrastructure.config as config
from infrastructure.llm.client import _get_llm
from prompts.extraction_prompts import generate_excel_plan_format_prompt
from prompts.response_model import ExcelPlanV2

TRACE = "logs/thinking_trace.log"


def latest_phase1_body() -> str:
    """取 thinking_trace.log 最近一次 analyze_test_points_raw 的模型输出正文。"""
    lines = open(TRACE, encoding="utf-8", errors="replace").read().splitlines()
    # 找最后一个 analyze 头与其后第一个 END
    start = None
    for i, l in enumerate(lines):
        if "*** analyze_test_points_raw ***" in l:
            start = i
    if start is None:
        raise SystemExit("未找到 analyze 头")
    end = None
    for j in range(start + 1, len(lines)):
        if lines[j].strip() == "=== END analyze_test_points_raw ===":
            end = j
            break
    if end is None:
        end = len(lines)
    body = lines[start + 1:end]
    # 去掉「用户输入: ...」与「--- LLM 输出 (N 字符) ---」两行元信息
    if body and body[0].strip().startswith("用户输入"):
        body = body[1:]
    if body and body[0].strip().startswith("--- LLM 输出"):
        body = body[1:]
    return "\n".join(body).strip()


def build_messages(analysis: str) -> list:
    return generate_excel_plan_format_prompt().format_messages(
        json_schema=json.dumps(ExcelPlanV2.model_json_schema(), ensure_ascii=False, indent=2),
        analysis_text=analysis,
        api_definitions="（22 个接口定义已并入上方分析：车场隔离改造，详见分析内各接口 URL 与场景。）",
        db_schema=config.DB_SCHEMA,
        gen_warning="",
    )


def summarize(tag: str, fn):
    print(f"\n===== {tag} =====")
    try:
        msg = fn()
        meta = getattr(msg, "response_metadata", {}) or {}
        usage = getattr(msg, "usage_metadata", None) or meta.get("token_usage") or {}
        content = msg.content if hasattr(msg, "content") else str(msg)
        finish = meta.get("finish_reason", meta.get("finish_reasons", "?"))
        print("finish_reason:", finish)
        print("usage:", json.dumps(usage, ensure_ascii=False))
        print("content chars:", len(content or ""))
        if content:
            print("head:", repr((content or "")[:150]))
            print("tail:", repr((content or "")[-150:]))
        return content
    except Exception as e:
        print("EXC:", type(e).__name__, str(e)[:300])


def main():
    analysis = latest_phase1_body()
    print(f"model={config.LLM_MODEL} base_url={config.LLM_BASE_URL}")
    print(f"LLM_MAX_TOKENS(cfg)={config.LLM_MAX_TOKENS}")
    print(f"Phase1 analysis chars={len(analysis)} approx_tokens={len(analysis)//2}")
    msgs = build_messages(analysis)
    print(f"prompt chars={sum(len(m.content or '') for m in msgs) if hasattr(msgs[0],'content') else '?'}")
    base = _get_llm()

    def variant(**extra):
        def call():
            return base.bind(temperature=0.4, max_tokens=20000, **extra).invoke(msgs)
        return call

    json_obj = {"response_format": {"type": "json_object"}}
    think_off = {"extra_body": {"thinking": {"type": "disabled"}}}
    think_on = {"extra_body": {"thinking": {"type": "enabled"}}}

    # 先摸一下 prompt 长度（token 大致）
    summarize("A1 json_object + thinking=disabled", variant(**json_obj, **think_off))
    summarize("A2 json_object + thinking=enabled", variant(**json_obj, **think_on))
    summarize("B1 no-format + thinking=disabled", variant(**think_off))
    summarize("B2 no-format + thinking=enabled", variant(**think_on))


if __name__ == "__main__":
    main()
