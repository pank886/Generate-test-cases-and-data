# -*- coding: utf-8 -*-
"""#31 第二轮：定向重跑（数据自查修正）——按 .last_run.json 选点 27 test + 36 setup，digest 逐文件注入。

镜像生产 /phase-c/selective-rerun → _selective_phase_c_rerun_bg，真实组件 + TLS-safe llm（同基线驱动）。
"""
import asyncio
import json
import os
import ssl
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, r"E:\Generate-test-cases-and-data")

MODULE_DIR = r"C:\Users\damai\PycharmMiscProject\testcase\园区基线\智慧停车"


def _build_llm_with_working_tls():
    import httpx
    import infrastructure.config as cfg
    from infrastructure.llm.deepseek import DeepSeekChatOpenAI
    ctx = ssl.create_default_context()          # Windows store -> TrustAsia 2025
    return DeepSeekChatOpenAI(
        model=cfg.LLM_MODEL,
        base_url=cfg.LLM_BASE_URL,
        api_key=cfg.LLM_API_KEY(),
        temperature=cfg.LLM_TEMPERATURE,
        max_tokens=cfg.LLM_MAX_TOKENS,
        http_client=httpx.Client(verify=ctx, trust_env=True),
        http_async_client=httpx.AsyncClient(verify=ctx, trust_env=True),
    )


async def main():
    import web.state as state
    import agent_components.graph.nodes as nodes
    from web.tasks import _selective_phase_c_rerun_bg

    llm = _build_llm_with_working_tls()
    state._phase_b_components = nodes.ChatTestAgentGraph()
    state._phase_b_components.llm = llm
    nodes._llm_instance = llm
    nodes._get_llm = lambda: llm
    nodes.reload_llm = lambda: None
    print("[driver] components ready", flush=True)

    try:
        r = llm.invoke([{"role": "user", "content": "reply exactly: pong"}])
        print(f"[driver] preflight OK: {repr(r.content)[:40]}", flush=True)
    except Exception as e:
        print(f"[driver] preflight FAILED: {type(e).__name__} {e}", flush=True)
        return 1

    task_id = await state._create_task()
    await _selective_phase_c_rerun_bg(task_id, MODULE_DIR, session_id="")
    async with state._task_store_lock:
        rec = dict(state._task_store.get(task_id, {}))
    print("[driver] FINAL_STATUS=" + json.dumps(
        {k: rec.get(k) for k in ("status", "progress", "message", "error", "result")},
        ensure_ascii=False, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
