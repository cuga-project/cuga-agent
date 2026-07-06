"""Debug/run entrypoint for the event-driven CUGA server (behind EVENTS_ENABLED).

Use this as the VS Code launch target ("Events: CUGA server :8100 (debug)") to set breakpoints in
src/cuga/backend/events/*.py, or run it directly:

    .venv/bin/python scripts/run_events_server.py

It loads .env, applies the events defaults, and runs uvicorn WITHOUT reload (so breakpoints bind).
Everything uses ``setdefault`` so a real env var / launch.json envFile wins over these defaults.
Requires the tool registry to be up first (scripts/events_up.sh, or the "API Registry" launch config).
"""
import os

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# load .env (setdefault → does not clobber values already in the environment)
_env = os.path.join(REPO, ".env")
if os.path.exists(_env):
    for line in open(_env):
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.split(" #", 1)[0].strip().strip('"').strip("'"))

os.environ.setdefault("EVENTS_ENABLED", "1")
os.environ.setdefault("EVENTS_WORKER_BACKEND", "cuga")               # CUGA does the reasoning
os.environ.setdefault("EVENTS_SEED_AGENTS", "1")                     # seed the demo fleet
os.environ.setdefault("EVENTS_USER_ID", "admin")                    # web Studio == telegram identity
os.environ.setdefault("EVENTS_DB", os.path.join(REPO, ".events.db"))  # persist subs/identity
os.environ.setdefault("DYNACONF_ADVANCED_FEATURES__SANDBOX_EXECUTION_TIMEOUT", "120")  # slow external APIs
os.environ.setdefault("DYNACONF_SERVER_PORTS__REGISTRY_HOST", "http://localhost:8001")  # tool registry

PORT = int(os.environ.get("EVENTS_CUGA_PORT", "8100"))

if __name__ == "__main__":
    import uvicorn
    print(f"[events] EVENTS_ENABLED={os.environ['EVENTS_ENABLED']} worker={os.environ['EVENTS_WORKER_BACKEND']} "
          f"db={os.environ['EVENTS_DB']} registry={os.environ['DYNACONF_SERVER_PORTS__REGISTRY_HOST']} port={PORT}")
    uvicorn.run("cuga.backend.server.main:app", host="127.0.0.1", port=PORT, log_level="info")
