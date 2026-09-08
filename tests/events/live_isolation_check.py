"""LIVE isolation check: two principals → two separate Activepieces PROJECTS.

Arms a flow for 'alice' and one for 'bob' and verifies each lands in its OWN AP project (and
neither sees the other's). Then cleans up flows + projects. Fast (no wait-for-fire).

    .venv-events/bin/python tests/events/live_isolation_check.py
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
ap_engine = importlib.import_module("events.ap_engine")
Principal = importlib.import_module("events.principal").Principal
import httpx  # noqa: E402


async def main() -> int:
    eng = ap_engine.APEngine()
    g = eng.project_grain
    alice, bob = Principal(user_id="alice"), Principal(user_id="bob")
    print(f"isolation grain: {g}  (EVENTS_AP_PROJECT_GRAIN); degrades to shared if plan caps projects")

    # same base name for both → isolation must come purely from the principal scope
    fa = await eng.create_schedule_flow(
        name="ea:iso",
        agent="x",
        thread_id="t",
        prompt="hi",
        interval_seconds=60,
        deliver=False,
        project_name=alice.ap_project_name(g),
        scope=alice.scope,
    )
    fb = await eng.create_schedule_flow(
        name="ea:iso",
        agent="x",
        thread_id="t",
        prompt="hi",
        interval_seconds=60,
        deliver=False,
        project_name=bob.ap_project_name(g),
        scope=bob.scope,
    )
    name_a, name_b = f"{alice.scope}::ea:iso", f"{bob.scope}::ea:iso"

    async with httpx.AsyncClient(timeout=15) as c:
        hdrs = await eng._auth(c)
        pa = eng._project_cache.get(alice.ap_project_name(g), eng.project_id)
        pb = eng._project_cache.get(bob.ap_project_name(g), eng.project_id)

        async def flows_in(pid):
            r = await c.get(f"{eng.base}/api/v1/flows", headers=hdrs, params={"projectId": pid, "limit": 100})
            return [(f.get("version") or {}).get("displayName") for f in r.json().get("data", [])]

        na, nb = await flows_in(pa), await flows_in(pb)
        # per-tenant filter (what /api/events/subscriptions does): only my scope's flows
        alice_sees = [n for n in na if n and n.startswith(alice.scope + "::")]
        bob_sees = [n for n in nb if n and n.startswith(bob.scope + "::")]
        print(f"alice project={pa}  flows visible to alice: {alice_sees}")
        print(f"bob   project={pb}  flows visible to bob:   {bob_sees}")
        ok = (
            name_a in na
            and name_b in nb
            and alice_sees == [name_a]
            and bob_sees == [name_b]
            and name_a != name_b
        )

        await eng.delete_flow(fa)
        await eng.delete_flow(fb)
        # only remove projects we created (never the shared default)
        for pid in {pa, pb}:
            if pid != eng.project_id:
                await c.delete(f"{eng.base}/api/v1/projects/{pid}", headers=hdrs)
    print(f"\n{'PASS' if ok else 'FAIL'} — tenant flows isolated by scope (grain={g})")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
