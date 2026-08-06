"""LIVE check: both credential-ownership models against real Activepieces connections.

  shared   → one connection for the agent; every user resolves to the SAME externalId.
  per-user → a distinct connection per user; a user who hasn't connected → needs a connect.

Uses a SECRET_TEXT connection (the token path — Telegram bot token from .env) so it can run
headlessly. OAuth apps (Gmail/Box) are authorized in AP's UI instead (documented).

    .venv-events/bin/python tests/events/live_credentials_check.py
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
credentials = importlib.import_module("events.credentials")
Principal = importlib.import_module("events.principal").Principal

PIECE = "@activepieces/piece-telegram-bot"
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")


async def main() -> int:
    if not TOKEN:
        print("SKIP: no TELEGRAM_BOT_TOKEN in .env")
        return 0
    eng = ap_engine.APEngine()
    alice = Principal(tenant_id="acme", user_id="alice")
    bob = Principal(tenant_id="acme", user_id="bob")
    charlie = Principal(tenant_id="acme", user_id="charlie")
    made = []

    # ---- Model A: shared (per-agent) ----
    sa = credentials.connection_external_id("telegram", "shared", alice, agent="mailbot")
    sb = credentials.connection_external_id("telegram", "shared", bob, agent="mailbot")
    print("shared: alice→", sa, "| bob→", sb, "| same?", sa == sb)
    await eng.ensure_secret_connection(sa, PIECE, TOKEN)
    made.append(sa)
    shared_ok = sa == sb and await eng.connection_exists(sa)

    # ---- Model B: per-user ----
    ua = credentials.connection_external_id("telegram", "per-user", alice)
    ub = credentials.connection_external_id("telegram", "per-user", bob)
    print("per-user: alice→", ua, "| bob→", ub, "| distinct?", ua != ub)
    await eng.ensure_secret_connection(ua, PIECE, TOKEN)
    made.append(ua)
    await eng.ensure_secret_connection(ub, PIECE, TOKEN)
    made.append(ub)
    # charlie hasn't connected → just-in-time connect needed
    uc = credentials.connection_external_id("telegram", "per-user", charlie)
    charlie_needs = credentials.is_per_user("per-user") and not await eng.connection_exists(uc)
    per_user_ok = (
        ua != ub and await eng.connection_exists(ua) and await eng.connection_exists(ub) and charlie_needs
    )
    print(f"charlie needs a connect (not yet authorized): {charlie_needs}")

    # cleanup
    for e in made:
        await eng.delete_connection(e)
    print("cleaned up test connections")

    ok = shared_ok and per_user_ok
    print(
        "\nPASS — shared cred reused by all users; per-user creds distinct; unconnected user → connect prompt"
        if ok
        else "\nFAIL"
    )
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
