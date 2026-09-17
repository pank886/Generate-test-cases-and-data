# -*- coding: utf-8 -*-
"""Round 1/2 驱动器：智慧停车 Phase C 全量重跑（2026-09-10，aux）。

在进程内复刻生产链路 `/phase-c/start → /workflow/confirm(flow=phase_c) → _phase_c_rerun_bg`：
设置 `web.state._phase_b_components` 为真实 `ChatTestAgentGraph()`，再 await 真实的
`_phase_c_rerun_bg`，最后把 task 结果落 JSON。

沙箱 TLS 变通（仅本驱动，不影响真实服务）：langchain-openai 用 certifi 包校验 TLS，本机缺
api.deepseek.com 出示的 TrustAsia-2025 根证书；改用 Windows 证书库的默认 context，
并把 `nodes._get_llm`/`reload_llm` 钉住，使所有取用路径都走该 client。

消耗真实 LLM credit。不入系统运行（有作用/ 惯例，aux）。
"""
import argparse
import asyncio
import json
import ssl
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, r"E:\Generate-test-cases-and-data")

DEFAULT_MODULE = r"C:\Users\damai\PyCharmMiscProject\testcase\园区基线\智慧停车"


def _build_llm_with_working_tls():
    import httpx
    import infrastructure.config as cfg
    from infrastructure.llm.deepseek import DeepSeekChatOpenAI

    ctx = ssl.create_default_context()          # Windows store -> trusts TrustAsia 2025
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
    ap = argparse.ArgumentParser(description="智慧停车 Phase C 全量重跑驱动")
    ap.add_argument("--module-dir", default=DEFAULT_MODULE)
    ap.add_argument("--out", required=True, help="task 结果 JSON 输出路径")
    ap.add_argument("--skip-preflight", action="store_true")
    args = ap.parse_args()

    import web.state as state
    import agent_components.graph.nodes as nodes
    from web.tasks import _phase_c_rerun_bg

    llm = _build_llm_with_working_tls()
    state._phase_b_components = nodes.ChatTestAgentGraph()
    state._phase_b_components.llm = llm
    nodes._llm_instance = llm
    nodes._get_llm = lambda: llm            # reload_llm() 会重置单例，这里钉住
    nodes.reload_llm = lambda: None
    print("[driver] _phase_b_components + TLS-safe llm ready", flush=True)

    if not args.skip_preflight:
        try:
            r = llm.invoke([{"role": "user", "content": "reply with exactly: pong"}])
            print(f"[driver] preflight OK: {repr(r.content)[:40]}", flush=True)
        except Exception as e:  # noqa: BLE001
            print(f"[driver] preflight FAILED, aborting: {type(e).__name__} {e}", flush=True)
            return 1

    task_id = await state._create_task()
    print(f"[driver] task_id={task_id}", flush=True)

    await _phase_c_rerun_bg(task_id, args.module_dir, session_id="")

    async with state._task_store_lock:
        rec = dict(state._task_store.get(task_id, {}))
    payload = {k: rec.get(k) for k in ("status", "progress", "message", "error", "result")}
    payload["_run_at"] = datetime.now().isoformat(timespec="seconds")
    payload["_task_id"] = task_id

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print("[driver] FINAL_STATUS=" + json.dumps(payload, ensure_ascii=False, indent=2)[:2000], flush=True)
    print("[driver] written:", out, flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
