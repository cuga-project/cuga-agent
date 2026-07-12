#!/usr/bin/env python3
"""CUGA server launcher for the event-driven platform.

This is the **regular CUGA server** (`cuga.backend.server.main:app`) started with the events layer
switched on. It does three things and then hands off to uvicorn:

  1. load `.env` into the environment,
  2. set `EVENTS_ENABLED=1` + the events config (worker backend, seeded agents, DB path, …),
  3. run `cuga.backend.server.main:app`.

`scripts/events_up.sh` runs this after it has started the MCP registry and the CUGA tunnel. It used to
`cat` an identical file into `/tmp/events_up/` at boot; keeping it here in the repo means it is
version-controlled, reviewable, and shows up in `ps` as a recognisable path instead of a temp file.

Everything is overridable from the environment, so the shell wrapper (or you) can set ports:
  EVENTS_CUGA_PORT (8100) · EVENTS_REGISTRY_URL (http://localhost:8001) · CUGA_REPO (repo root)
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
os.environ.setdefault("EVENTS_DB", str(pathlib.Path(REPO) / ".events.db"))   # persist subs/identity
# arXiv/Semantic Scholar are ~5.5s/call from here; the papers agent does several calls + retries,
# which blows the 30s default sandbox timeout. 120s gives slow external APIs room to complete.
os.environ.setdefault("DYNACONF_ADVANCED_FEATURES__SANDBOX_EXECUTION_TIMEOUT", "120")
os.environ["DYNACONF_SERVER_PORTS__REGISTRY_HOST"] = os.environ.get(
    "EVENTS_REGISTRY_URL", "http://localhost:8001")

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("EVENTS_CUGA_PORT", "8100"))
    uvicorn.run("cuga.backend.server.main:app", host="127.0.0.1", port=port, log_level="warning")
