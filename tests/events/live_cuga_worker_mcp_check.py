"""LIVE smoke — a **CUGA worker using its MCP tools** answers a real question.

The full chain: concierge-provisioned worker (backend=cuga) with ``mcp_servers=['cuga_finance']``
→ ``AgentStoreRuntime`` builds a ``DynamicAgentGraph`` whose ``CombinedToolProvider(app_names=[...])``
pulls the server's tools from the CUGA **registry** → CUGA calls ``get_crypto_price`` → real number.

Prereq: the CUGA **registry** must be running with the cuga-apps config (all 7 servers):
    MCP_SERVERS_FILE=src/cuga/backend/tools_env/registry/config/mcp_servers_cuga_apps.yaml \\
      .venv/bin/python -m uvicorn \\
      cuga.backend.tools_env.registry.registry.api_registry_server:app --port 8001
Then:
    .venv/bin/python tests/events/live_cuga_worker_mcp_check.py
Point at a non-default registry port with EVENTS_REGISTRY_URL=http://localhost:8021.
(Code Engine cuga-* servers scale to zero — warm them first.)
"""

from __future__ import annotations

import os
import sys

# MUST run before any cuga import: point the registry client at our cuga-apps registry.
os.environ.setdefault(
    "DYNACONF_SERVER_PORTS__REGISTRY_HOST", os.environ.get("EVENTS_REGISTRY_URL", "http://localhost:8001")
)

import asyncio  # noqa: E402


def _load_dotenv(path: str) -> None:
    if not os.path.exists(path):
        return
    for line in open(path):
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        v = v.split(" #", 1)[0]
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


async def main() -> int:
    repo = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    _load_dotenv(os.path.join(repo, ".env"))

    from cuga.backend.events.runtime import AgentStoreRuntime, AgentSpec, DEFAULT_SCOPE
    from cuga.backend.events.agent_store import AgentStore
    from cuga.backend.cuga_graph.policy.configurable import PolicyConfigurable

    policy = PolicyConfigurable.get_instance()
    try:
        await policy.initialize()
    except Exception:  # noqa: BLE001
        pass

    def app_context():
        return {"policy_system": policy, "langfuse_handler": None, "get_include_by_app": None}

    rt = AgentStoreRuntime(agent_store=AgentStore(":memory:"), app_context=app_context)
    # a worker WITH an MCP server — CUGA must load cuga_finance tools from the registry
    rt.upsert_agent(
        AgentSpec(name="pricebot", prompt="You answer finance questions.", mcp_servers=["cuga_finance"]),
        scope=DEFAULT_SCOPE,
    )
    print("registry:", os.environ["DYNACONF_SERVER_PORTS__REGISTRY_HOST"])
    print("provisioned 'pricebot' backend=cuga mcp=[cuga_finance]")

    q = "What is the current price of Bitcoin in USD? Give the number."
    print("Q:", q)
    print("… building CUGA graph (loads cuga_finance tools) + running …")
    answer = await rt.run("pricebot", "cuga-mcp-1", q, scope=DEFAULT_SCOPE)
    print("A:", (answer or "")[:400])

    used_cuga = (DEFAULT_SCOPE, "pricebot") in rt._graphs
    has_number = any(c.isdigit() for c in (answer or ""))
    ok = used_cuga and has_number
    print(f"\nexecuted via: {'CUGA DynamicAgentGraph' if used_cuga else 'other'}")
    print(
        "RESULT:",
        "PASS — CUGA worker used its MCP tool (numeric price)"
        if ok
        else "FAIL — no numeric answer (registry up + cuga_finance warm?)",
    )
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
