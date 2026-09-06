"""LIVE smoke — the **cuga worker backend** actually answers (not the react fallback).

Runs inside the FULL CUGA venv (`.venv`, after `uv sync`). Proves the piece CUGA lacked:
a per-agent `DynamicAgentGraph` built on demand from an AgentSpec and run to an answer, through
`AgentStoreRuntime` (the default worker backend). If the CUGA graph can't build, `AgentStoreRuntime` falls
back to react — this test FORCES the cuga path (real app_context) and asserts the answer came
from the CUGA graph.

Run:
    .venv/bin/python tests/events/live_cuga_worker_check.py
(reads .env at repo root for the watsonx LLM keys CUGA's agents use)
"""

from __future__ import annotations

import asyncio
import os
import sys


def _load_dotenv(path: str) -> None:
    if not os.path.exists(path):
        return
    for line in open(path):
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        v = v.split(" #", 1)[0]  # strip trailing inline comment
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


async def main() -> int:
    repo = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    _load_dotenv(os.path.join(repo, ".env"))

    from cuga.backend.events.runtime import AgentStoreRuntime, AgentSpec, DEFAULT_SCOPE
    from cuga.backend.events.agent_store import AgentStore

    # real app_context → forces the CUGA path (policy_system present). langfuse off; no tool
    # includes (a plain worker). This mirrors main.py's _cuga_app_context.
    from cuga.backend.cuga_graph.policy.configurable import PolicyConfigurable

    policy = PolicyConfigurable.get_instance()
    try:
        await policy.initialize()
    except Exception:  # noqa: BLE001
        pass

    def app_context():
        return {"policy_system": policy, "langfuse_handler": None, "get_include_by_app": None}

    # cuga executes the work; no react fallback here — a failure must surface, not be masked.
    rt = AgentStoreRuntime(agent_store=AgentStore(":memory:"), app_context=app_context)

    # provision a plain worker (no MCP) — the point is to prove the CUGA graph builds + answers
    rt.upsert_agent(
        AgentSpec(name="helper", prompt="You are a concise, helpful assistant."), scope=DEFAULT_SCOPE
    )
    print("provisioned worker 'helper' · backend =", rt.get_agent("helper", scope=DEFAULT_SCOPE).backend)

    q = "Briefly, what is your role?"  # a no-tool worker answers from reasoning
    print("Q:", q)
    print("… building DynamicAgentGraph on demand + running (first build is slow) …")
    answer = await rt.run("helper", "cuga-smoke-1", q, scope=DEFAULT_SCOPE)
    print("A:", (answer or "")[:400])

    # was it the cuga graph (not the react fallback)?  the graph is cached under (scope, name)
    used_cuga = (DEFAULT_SCOPE, "helper") in rt._graphs
    print(f"\nexecuted via: {'CUGA DynamicAgentGraph' if used_cuga else 'REACT fallback'}")
    # This test proves the PLUMBING: a per-agent CUGA graph builds on demand and runs through
    # the real AgentLoop to a final answer. Tool-backed answering needs the worker's MCP servers
    # attached to the CUGA graph (CombinedToolProvider) — the next wiring step (see TODO.md).
    ok = used_cuga and bool((answer or "").strip())
    print(
        "RESULT:",
        "PASS — CUGA DynamicAgentGraph executed + answered via the real AgentLoop"
        if ok
        else ("ANSWERED but via REACT fallback (no CUGA stack?)" if answer else "FAIL — no answer"),
    )
    if ok:
        print(
            "NOTE: worker had no MCP tools; attaching spec.mcp_servers to the CUGA graph "
            "(CombinedToolProvider) is the next step for tool-backed answers."
        )
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
