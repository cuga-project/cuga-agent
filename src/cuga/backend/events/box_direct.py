"""Direct Box integration — talks to Box's own API with a token, bypassing Activepieces.

Same move as ``slack_direct``: Box's REST API accepts a bearer token directly, so we don't need AP's
OAuth2 connection (the wall that blocked the AP Box path — AP insists on doing the code-exchange
itself and refuses a pre-obtained dev/access token) NOR AP's Box ``new_file`` webhook (which needs a
paid Box app with a saved redirect URI + ``manage_webhook``). Combined with direct-channel delivery
(``delivery.send_direct``), this gives a fully AP-free resume watcher:

    Box folder poll (this module) ▸ /invoke(resume_judge) ▸ Slack/Gmail delivery.

Token: ``BOX_DEV_TOKEN`` (a 60-min Box developer token — grab a fresh one from the Box dev console;
no OAuth app/redirect-URI needed) or, later, a stored OAuth access token via ``EVENTS_BOX_TOKEN``.

This is a POLLING trigger (Box has no free push): CUGA lists a folder's items and fires on files
whose ``created_at`` is newer than the last poll. Emit-on-change is caller-tracked (``since``).
"""
from __future__ import annotations

import os

import httpx

API = "https://api.box.com/2.0"


def token() -> str:
    """The Box bearer token — dev token (fast, OAuth-free) or a configured access token."""
    for key in ("EVENTS_BOX_TOKEN", "BOX_DEV_TOKEN"):
        v = (os.environ.get(key, "") or "").split(" #", 1)[0].strip().strip('"').strip("'")
        if v:
            return v
    return ""


def should_process(item: dict) -> bool:
    """A real file we should judge — not a subfolder or a web link."""
    return bool(item) and item.get("type") == "file" and bool(item.get("id"))


async def whoami(tok: str | None = None) -> dict:
    """GET /users/me — proves the token is valid (used by the live harness / setup guide)."""
    tok = tok or token()
    if not tok:
        return {"ok": False, "error": "no BOX_DEV_TOKEN / EVENTS_BOX_TOKEN"}
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.get(f"{API}/users/me", headers={"Authorization": f"Bearer {tok}"})
        if r.status_code == 200:
            j = r.json()
            return {"ok": True, "login": j.get("login"), "name": j.get("name")}
        return {"ok": False, "error": f"HTTP {r.status_code}", "detail": r.text[:200]}


async def list_folder_items(folder_id: str, tok: str | None = None) -> list[dict]:
    """List a folder's items (id, name, type, created_at). Raises on a non-200 so callers see
    an expired token loudly rather than treating it as 'no new files'."""
    tok = tok or token()
    if not tok:
        raise RuntimeError("no Box token (set BOX_DEV_TOKEN or EVENTS_BOX_TOKEN)")
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.get(f"{API}/folders/{folder_id}/items",
                        params={"fields": "id,name,type,created_at", "limit": 200},
                        headers={"Authorization": f"Bearer {tok}"})
        if r.status_code != 200:
            raise RuntimeError(f"Box list folder {folder_id} failed: HTTP {r.status_code} {r.text[:200]}")
        return (r.json() or {}).get("entries", []) or []


async def new_files_since(folder_id: str, since_iso: str | None, tok: str | None = None) -> list[dict]:
    """Files in ``folder_id`` created strictly after ``since_iso`` (ISO-8601; None → all files).
    Box's created_at is RFC-3339 and lexically sortable, so a string compare is correct for the
    same offset — good enough for the poll baseline (the caller stores the max created_at seen)."""
    items = [i for i in await list_folder_items(folder_id, tok) if should_process(i)]
    if since_iso:
        items = [i for i in items if (i.get("created_at") or "") > since_iso]
    return items
