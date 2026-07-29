"""LIVE Phase-2 check: a CRON watcher fires via Activepieces → /invoke → worker → delivery.

Stands up a real /invoke receiver on :8000 (+ a capture sink), arms an arXiv watcher flow on
the live AP (every 1 min), and waits for a real delivery. Proves the full AP round-trip.

    .venv-events/bin/python tests/events/live_phase2_watchers.py

Needs: AP up on AP_BASE_URL, AP able to reach HOST_CALLBACK_URL (host.docker.internal:8000),
watsonx + network. Runs ~2-3 min (AP minute-granularity schedule).
"""

import asyncio
import importlib
import importlib.util
import os
import sys
import threading
import time

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
EVENTS_DIR = os.path.join(REPO, "src", "cuga", "backend", "events")

from dotenv import load_dotenv  # noqa: E402
load_dotenv(os.path.join(REPO, ".env"))

# :8000 is often already taken (a running CUGA/daemon) — use a free port and point AP there.
PORT = int(os.environ.get("EVENTS_TEST_PORT", "8009"))
os.environ["EA_CAPTURE_URL"] = f"http://localhost:{PORT}/capture"          # where /invoke delivers
os.environ["HOST_CALLBACK_URL"] = f"http://host.docker.internal:{PORT}/invoke"  # AP → receiver

_spec = importlib.util.spec_from_file_location(
    "events", os.path.join(EVENTS_DIR, "__init__.py"), submodule_search_locations=[EVENTS_DIR])
_pkg = importlib.util.module_from_spec(_spec)
sys.modules["events"] = _pkg
_spec.loader.exec_module(_pkg)

runtime = importlib.import_module("events.runtime")
llm = importlib.import_module("events.llm")
ap_engine = importlib.import_module("events.ap_engine")
ev_app = importlib.import_module("events.app")

from fastapi import FastAPI, Request  # noqa: E402
import uvicorn  # noqa: E402

# ---- build the receiver -------------------------------------------------
rt = runtime.ReactRuntime(model_factory=llm.default_model_factory)
rt.upsert_agent(runtime.AgentSpec(
    name="papers",
    prompt="You find recent arXiv papers. Use the arxiv_search tool. Reply with 2-3 titles, short.",
    builtin_tools=["arxiv_search"]))

fa = FastAPI()
ev_app.register_events_routes(fa, runtime=rt)   # /invoke + /api/concierge
CAPTURED: list = []


@fa.post("/capture")
async def capture(req: Request):
    CAPTURED.append(await req.json())
    return {"ok": True}


@fa.get("/captured")
async def captured():
    return CAPTURED


def _serve():
    uvicorn.Server(uvicorn.Config(fa, host="0.0.0.0", port=PORT, log_level="error")).run()


async def main() -> int:
    th = threading.Thread(target=_serve, daemon=True)
    th.start()
    time.sleep(2.0)

    # 0) sanity: hit our own /invoke locally → proves worker + capture (no AP yet)
    import httpx
    async with httpx.AsyncClient(timeout=60) as c:
        r = await c.post(f"http://localhost:{PORT}/invoke",
                         headers={"X-Gateway-Token": os.environ.get("GATEWAY_TOKEN", "")},
                         json={"agent": "papers", "text": "Find 2 recent arXiv papers on LLMs.",
                               "deliver": True,
                               "source": {"type": "time", "name": "cron", "thread_id": "sanity"},
                               "event": {"kind": "tick"}})
        print("local /invoke:", r.status_code, "captured so far:", len(CAPTURED))
    if not CAPTURED:
        print("FAIL: local /invoke did not deliver to capture (worker/capture broken)")
        return 1
    CAPTURED.clear()

    # 1) arm the arXiv watcher on the LIVE AP (every 1 minute)
    eng = ap_engine.APEngine()
    ok, detail = await eng.available()
    print("AP:", detail)
    if not ok:
        print("FAIL: AP not available")
        return 1
    flow_id = await eng.create_schedule_flow(
        name="ea-arxiv-watcher", agent="papers", thread_id="sub:arxiv",
        prompt="Find 3 recent arXiv papers on mixture-of-experts.",
        interval_seconds=60, deliver=True)
    print("armed arXiv watcher → AP flow:", flow_id)

    # 2) wait for AP to fire → /invoke → capture
    print("waiting up to 190s for the watcher to fire…")
    deadline = time.time() + 190
    while time.time() < deadline and not CAPTURED:
        await asyncio.sleep(5)
    fired = bool(CAPTURED)
    await eng.delete_flow(flow_id)
    print("cleaned up AP flow")

    if fired:
        print("\nCAPTURED delivery from AP:", (CAPTURED[0].get("answer") or "")[:220])
        print("PASS — CRON watcher fired via AP → /invoke → worker → delivery")
        return 0
    print("\nFAIL: no delivery within timeout (is AP able to reach HOST_CALLBACK_URL?)")
    return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
