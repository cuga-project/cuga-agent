"""Clear Activepieces artifacts created by the events layer — for a clean slate.

Default: deletes EA-tagged flows + connections (externalId/displayName starting with 'ea')
across all projects, then removes empty 'ea::' projects. Use --all to delete EVERY flow +
connection (nuclear). Use --dry to preview.

    .venv-events/bin/python tests/events/ap_nuke.py          # EA artifacts only
    .venv-events/bin/python tests/events/ap_nuke.py --dry    # preview
    .venv-events/bin/python tests/events/ap_nuke.py --all    # everything (careful!)
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
_spec = importlib.util.spec_from_file_location("events", os.path.join(EV, "__init__.py"),
                                               submodule_search_locations=[EV])
_pkg = importlib.util.module_from_spec(_spec); sys.modules["events"] = _pkg
_spec.loader.exec_module(_pkg)
ap_engine = importlib.import_module("events.ap_engine")
import httpx  # noqa: E402

ALL = "--all" in sys.argv
DRY = "--dry" in sys.argv


def _is_ea_flow(name: str) -> bool:
    return bool(name) and (name.startswith("ea") or "::ea:" in name or "::ea-" in name)


async def main() -> int:
    eng = ap_engine.APEngine()
    df = dc = dp = 0
    async with httpx.AsyncClient(timeout=20) as c:
        hdrs = await eng._auth(c)
        pr = await c.get(f"{eng.base}/api/v1/projects", headers=hdrs, params={"limit": 100})
        data = pr.json(); projects = data.get("data", data) if isinstance(data, dict) else data
        for p in projects or []:
            pid, pname = p["id"], p.get("displayName")
            # flows
            fr = await c.get(f"{eng.base}/api/v1/flows", headers=hdrs,
                             params={"projectId": pid, "limit": 100})
            for f in fr.json().get("data", []):
                nm = (f.get("version") or {}).get("displayName", "")
                if ALL or _is_ea_flow(nm):
                    print(f"{'[dry] ' if DRY else ''}flow  del {pname}/{nm}")
                    if not DRY:
                        await c.delete(f"{eng.base}/api/v1/flows/{f['id']}", headers=hdrs)
                    df += 1
            # connections
            cr = await c.get(f"{eng.base}/api/v1/app-connections", headers=hdrs,
                             params={"projectId": pid, "limit": 100})
            for cn in cr.json().get("data", []):
                ext = cn.get("externalId", "")
                if ALL or ext.startswith("ea"):
                    print(f"{'[dry] ' if DRY else ''}conn  del {pname}/{ext}")
                    if not DRY:
                        await c.delete(f"{eng.base}/api/v1/app-connections/{cn['id']}", headers=hdrs)
                    dc += 1
        # remove ea:: projects (now empty) — never the default
        for p in projects or []:
            if (p.get("displayName") or "").startswith("ea::") and p["id"] != eng.project_id:
                print(f"{'[dry] ' if DRY else ''}proj  del {p['displayName']}")
                if not DRY:
                    await c.delete(f"{eng.base}/api/v1/projects/{p['id']}", headers=hdrs)
                dp += 1
    print(f"\n{'[dry] would remove' if DRY else 'removed'}: {df} flows, {dc} connections, {dp} projects")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
