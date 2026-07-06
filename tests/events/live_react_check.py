"""LIVE Phase-1 check: ReactRuntime runs a real worker via watsonx + a real cuga-* MCP.

Proves the NOW path + per-thread memory end to end. Needs .env (watsonx creds) and network
to watsonx + the cuga-geo MCP server. Run with the focused venv:

    .venv-events/bin/python tests/events/live_react_check.py

Imports the events package standalone (as top-level ``events``) so it doesn't trigger the
heavy ``cuga`` package __init__ that the focused venv doesn't fully provide.
"""

import asyncio
import importlib
import importlib.util
import os
import sys

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
EVENTS_DIR = os.path.join(REPO, "src", "cuga", "backend", "events")

from dotenv import load_dotenv  # noqa: E402
load_dotenv(os.path.join(REPO, ".env"))

# Register the events package under its own name so relative imports resolve, WITHOUT
# adding src/cuga/backend to sys.path (which shadows top-level names) or importing the
# heavy cuga package (its __init__ pulls loguru/etc. not in the focused venv).
_spec = importlib.util.spec_from_file_location(
    "events", os.path.join(EVENTS_DIR, "__init__.py"), submodule_search_locations=[EVENTS_DIR])
_pkg = importlib.util.module_from_spec(_spec)
sys.modules["events"] = _pkg
_spec.loader.exec_module(_pkg)

runtime = importlib.import_module("events.runtime")
llm = importlib.import_module("events.llm")


async def main() -> int:
    print(f"provider={os.getenv('LLM_PROVIDER')} model={os.getenv('LLM_MODEL')}")
    r = runtime.ReactRuntime(model_factory=llm.default_model_factory)
    r.upsert_agent(runtime.AgentSpec(
        name="geobot",
        prompt="You are a geography assistant. Use your tools. Answer in one short line.",
        mcp_servers=["cuga-geo"]))

    print("→ run #1: capital of Japan?")
    a1 = await r.run("geobot", "t1", "What is the capital of Japan? Answer with just the city.")
    print("   answer:", a1)

    print("→ run #2 (same thread — memory): and its population?")
    a2 = await r.run("geobot", "t1", "And roughly its population? Just the number.")
    print("   answer:", a2)

    print("→ run #3 (new thread): capital of Peru?")
    a3 = await r.run("geobot", "t2", "What is the capital of Peru? Just the city.")
    print("   answer:", a3)

    ok = ("tokyo" in a1.lower()
          and any(x in a2.lower() for x in ("million", "13", "14", "37", "9,", "9 ", "populat"))
          and "lima" in a3.lower())
    print("\nPASS" if ok else "\nFAIL (see answers above)")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
