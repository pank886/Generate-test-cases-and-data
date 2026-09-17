# -*- coding: utf-8 -*-
"""Throwaway driver (task #24): in-process real 智慧停车 Phase C rerun.

Mirrors production at the function level:
  /phase-c/start -> /workflow/confirm (flow=phase_c) -> _phase_c_rerun_bg
by setting web.state._phase_b_components to a real ChatTestAgentGraph(), then awaiting the
real _phase_c_rerun_bg and dumping the final task.

Sandbox TLS workaround (driver-local only): langchain-openai verifies TLS with the certifi
bundle, which here lacks the TrustAsia-2025 root that api.deepseek.com presents. urllib's
ssl.create_default_context() (Windows store) does trust it, so we hand the LLM a custom
httpx.Client/AsyncClient using that context, and pin nodes._get_llm/reload_llm so every call
path uses it. Real service (user's terminal) is unaffected.

Consumes real LLM credit. Not tracked (logs/ gitignored).
"""
import asyncio
import json
import os
import ssl
import sys

sys.path.insert(0, r"E:\Generate-test-cases-and-data")

MODULE_DIR = r"C:\Users\damai\PycharmMiscProject\testcase\园区基线\智慧停车"


def _build_llm_with_working_tls():
    import httpx
    import infrastructure.config as cfg
    from infrastructure.llm.deepseek import DeepSeekChatOpenAI

    ctx = ssl.create_default_context()          # Windows store -> trusts TrustAsia 2025
    llm = DeepSeekChatOpenAI(
        model=cfg.LLM_MODEL,
        base_url=cfg.LLM_BASE_URL,
        api_key=cfg.LLM_API_KEY(),
        temperature=cfg.LLM_TEMPERATURE,
        max_tokens=cfg.LLM_MAX_TOKENS,
        http_client=httpx.Client(verify=ctx, trust_env=True),
        http_async_client=httpx.AsyncClient(verify=ctx, trust_env=True),
    )
    return llm


async def main():
    import web.state as state
    import agent_components.graph.nodes as nodes
    from web.tasks import _phase_c_rerun_bg

    llm = _build_llm_with_working_tls()

    # Pin every LLM acquisition path (components.llm + module-global singleton).
    state._phase_b_components = nodes.ChatTestAgentGraph()
    state._phase_b_components.llm = llm
    nodes._llm_instance = llm
    nodes._get_llm = lambda: llm            # after reload_llm() resets the singleton
    nodes.reload_llm = lambda: None
    print("[driver] _phase_b_components + TLS-safe llm ready", flush=True)

    # Pre-flight: one real completion; abort before the long run if it still fails.
    try:
        r = llm.invoke([{"role": "user", "content": "reply with exactly: pong"}])
        print(f"[driver] preflight OK: {repr(r.content)[:40]}", flush=True)
    except Exception as e:
        print(f"[driver] preflight FAILED, aborting: {type(e).__name__} {e}", flush=True)
        return 1

    task_id = await state._create_task()
    print(f"[driver] task_id={task_id}", flush=True)

    await _phase_c_rerun_bg(task_id, MODULE_DIR, session_id="")

    async with state._task_store_lock:
        rec = dict(state._task_store.get(task_id, {}))
    print("[driver] FINAL_STATUS=" + json.dumps(
        {k: rec.get(k) for k in ("status", "progress", "message", "error", "result")},
        ensure_ascii=False, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
