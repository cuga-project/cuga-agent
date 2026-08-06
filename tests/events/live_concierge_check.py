"""LIVE Phase-1 check: the CONCIERGE reuses-or-creates a worker and runs it (NOW).

"what's the bitcoin price right now?" → concierge calls list_capabilities → provision_agent
(pricebot, cuga-finance) → run_now → a real price. Then a reuse check (ethereum → same pricebot).

    .venv-events/bin/python tests/events/live_concierge_check.py
"""

import asyncio
import importlib
import importlib.util
import os
import re
import sys

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
EVENTS_DIR = os.path.join(REPO, "src", "cuga", "backend", "events")

from dotenv import load_dotenv  # noqa: E402

load_dotenv(os.path.join(REPO, ".env"))

_spec = importlib.util.spec_from_file_location(
    "events", os.path.join(EVENTS_DIR, "__init__.py"), submodule_search_locations=[EVENTS_DIR]
)
_pkg = importlib.util.module_from_spec(_spec)
sys.modules["events"] = _pkg
_spec.loader.exec_module(_pkg)

runtime = importlib.import_module("events.runtime")
llm = importlib.import_module("events.llm")
concierge_mod = importlib.import_module("events.concierge")
principal_mod = importlib.import_module("events.principal")


async def main() -> int:
    rt = runtime.ReactRuntime(model_factory=llm.default_model_factory)
    con = concierge_mod.Concierge(rt, model_factory=llm.default_model_factory)
    p = principal_mod.Principal(user_id="demo")  # explicit principal (isolation scope)

    print("→ user: what's the bitcoin price right now?")
    r1 = await con.run("web:1", "what's the bitcoin price right now?", p)
    print("   concierge:", r1)
    agents_after = [a.name for a in rt.list_agents(scope=p.scope)]
    print("   agents now (scope demo):", agents_after)

    print("\n→ user: and ethereum? (should REUSE the same assistant)")
    r2 = await con.run("web:1", "and the price of ethereum right now?", p)
    print("   concierge:", r2)
    agents_after2 = [a.name for a in rt.list_agents(scope=p.scope)]
    print("   agents now (scope demo):", agents_after2)
    # prove isolation: a different principal sees NONE of demo's agents
    other = [a.name for a in rt.list_agents(scope=principal_mod.Principal(user_id="stranger").scope)]
    print("   agents visible to a different tenant:", other)

    created_one = len(agents_after) >= 1
    reused = len(agents_after2) == len(agents_after)  # no new agent on the follow-up
    has_number = bool(re.search(r"\d", r1)) and bool(re.search(r"\d", r2))
    ok = created_one and reused and has_number
    print(f"\n{'PASS' if ok else 'FAIL'}  (created={created_one} reused={reused} numeric={has_number})")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
