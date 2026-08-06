"""LIVE statefulness check: agents + conversation memory survive a 'restart' (new replica).

Runtime #1 creates an agent and chats; Runtime #2 (fresh objects, same sqlite files —
simulating a second FastAPI replica or a restart) must SEE the agent and CONTINUE the memory.
Proves the fleet model: any replica can serve any user's /invoke from shared storage.

    .venv-events/bin/python tests/events/live_statefulness_check.py
"""

import asyncio
import importlib
import importlib.util
import os
import sys

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
EV = os.path.join(REPO, "src", "cuga", "backend", "events")
from dotenv import load_dotenv  # noqa: E402

load_dotenv(os.path.join(REPO, ".env"))
_spec = importlib.util.spec_from_file_location(
    "events", os.path.join(EV, "__init__.py"), submodule_search_locations=[EV]
)
_pkg = importlib.util.module_from_spec(_spec)
sys.modules["events"] = _pkg
_spec.loader.exec_module(_pkg)
runtime = importlib.import_module("events.runtime")
llm = importlib.import_module("events.llm")
agent_store = importlib.import_module("events.agent_store")

AGENT_DB = "/tmp/ea_state_agents.db"
MEM_DB = "/tmp/ea_state_mem.db"


async def new_replica():
    """A fresh ReactRuntime on the SAME shared stores — like a new FastAPI replica."""
    store = agent_store.AgentStore(AGENT_DB)
    ckpt = await runtime.make_sqlite_checkpointer(MEM_DB)
    return runtime.ReactRuntime(model_factory=llm.default_model_factory, agent_store=store, checkpointer=ckpt)


async def main() -> int:
    for f in (AGENT_DB, MEM_DB):
        if os.path.exists(f):
            os.remove(f)

    # --- replica #1: create agent + first turn ---
    r1 = await new_replica()
    r1.upsert_agent(
        runtime.AgentSpec(
            name="geobot",
            prompt="You are a geography assistant. Use your tools. Answer in one short line.",
            mcp_servers=["cuga-geo"],
        ),
        scope="acme/default/alice",
    )
    a1 = await r1.run(
        "geobot", "t1", "What is the capital of Japan? Just the city.", scope="acme/default/alice"
    )
    print("replica#1:", a1)

    # --- replica #2: fresh runtime, same sqlite files (simulates restart / 2nd server) ---
    r2 = await new_replica()
    spec = r2.get_agent("geobot", scope="acme/default/alice")
    print("replica#2 sees agent from shared store:", bool(spec), "| mcp:", spec.mcp_servers if spec else None)
    # isolation still holds on the shared store:
    other = r2.get_agent("geobot", scope="acme/default/bob")
    print("replica#2, different user sees geobot:", other)
    # follow-up on the SAME thread via replica#2 → must recall Japan from persisted memory
    a2 = await r2.run("geobot", "t1", "And its population? Just the number.", scope="acme/default/alice")
    print("replica#2 follow-up:", a2)

    ok = (
        bool(spec)
        and other is None
        and "tokyo" in a1.lower()
        and any(x in a2.lower() for x in ("million", "13", "14", "37", "9", "populat"))
    )
    print("\nPASS — agents + memory survive across replicas/restart, still isolated" if ok else "\nFAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
