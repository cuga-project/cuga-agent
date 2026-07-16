#!/usr/bin/env python3
"""CUGA server launcher for the event-driven platform — the SAME server `cuga start … --events`
boots, wrapped for the `make up` dev stack.

Unification (events_docs/SETUP.md · plans/SUPERVISOR_REFACTOR.md): there is ONE entry point,
`cuga start demo --events`. `make up` exists only to PROVISION infra the CLI can't (the MCP
registry, the Activepieces container, the tunnels) and then boot this identical app. This launcher
sets the same `EVENTS_ENABLED=1` the `--events` flag sets, plus dev-stack defaults (ports, DB path,
seeded demo users), and hands off to uvicorn. Editing the events env in ONE place keeps the CLI and
`make up` from drifting.

Overridable from the environment:
  EVENTS_CUGA_PORT (8100) · EVENTS_REGISTRY_URL (http://localhost:8001) · CUGA_REPO (repo root) ·
  EVENTS_SUPERVISOR (0/1) · EVENTS_SUPERVISOR_ROSTER (./supervisor_agents.yaml)
"""
import os
import pathlib

REPO = os.environ.get("CUGA_REPO") or str(pathlib.Path(__file__).resolve().parent.parent)

# Load .env into the process environment. setdefault so anything already exported wins (e.g. the
# wrapper exporting a fresh EVENTS_PUBLIC_URL that matches the live tunnel).
env_path = pathlib.Path(REPO) / ".env"
if env_path.exists():
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        v = v.split(" #", 1)[0].strip().strip('"').strip("'")
        os.environ.setdefault(k.strip(), v)

# The events layer is additive and off by default; turn it on for this process.
os.environ["EVENTS_ENABLED"] = "1"
os.environ.setdefault("EVENTS_WORKER_BACKEND", "cuga")
os.environ.setdefault("EVENTS_SEED_AGENTS", "1")
os.environ.setdefault("EVENTS_USER_ID", "admin")           # the web Studio browses as admin
# same default as `cuga start --events` (events.db, as .env.events.example documents) — the two
# entry points must never write different databases. .env's explicit EVENTS_DB wins over both.
os.environ.setdefault("EVENTS_DB", str(pathlib.Path(REPO) / "events.db"))
# arXiv/Semantic Scholar are ~5.5s/call from here; the papers agent does several calls + retries,
# which blows the 30s default sandbox timeout. 120s gives slow external APIs room to complete.
os.environ.setdefault("DYNACONF_ADVANCED_FEATURES__SANDBOX_EXECUTION_TIMEOUT", "120")
os.environ["DYNACONF_SERVER_PORTS__REGISTRY_HOST"] = os.environ.get(
    "EVENTS_REGISTRY_URL", "http://localhost:8001")

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("EVENTS_CUGA_PORT", "8100"))
    uvicorn.run("cuga.backend.server.main:app", host="127.0.0.1", port=port, log_level="warning")
