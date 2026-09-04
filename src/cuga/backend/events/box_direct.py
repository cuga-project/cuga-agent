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

import json
import os
import time

import httpx

API = "https://api.box.com/2.0"

# Server-tracked per-folder "last created_at seen" — a STANDING scheduled poll must fire only on
# files added since the previous run, so this watermark IS the feature. Lose it and every existing
# file in the folder re-fires as if it were new.
#
# It therefore lives in the EVENTS DATABASE, not a file. It used to be `.box_since.json`, a relative
# path, and nothing in the Code Engine deploy set EVENTS_BOX_SINCE_FILE or mounted a volume — with
# Postgres configured the deploy runs "no mount, no snapshots" — so the watermark sat on the
# container's ephemeral disk and an instance replace silently re-fired the whole folder. Exactly the
# durability gap the Postgres move was meant to close, in the one place that was still a file.
#
# The file remains the fallback for a dev box with no EVENTS_DB, which keeps local behaviour and the
# offline tests unchanged.
_SINCE_FILE = os.environ.get("EVENTS_BOX_SINCE_FILE", "") or ".box_since.json"


def _cursor_db():
    """The events DB, or None when unconfigured (→ file fallback). Never raises: a poll must still
    run if the store is unreachable — it just loses delta tracking for that tick."""
    dsn = (os.environ.get("EVENTS_DB", "") or "").strip()
    if not dsn or dsn == ":memory:":
        return None
    try:
        try:
            from . import db as _db
        except ImportError:  # flat load (tests put the events dir on sys.path)
            import db as _db
        conn = _db.connect(dsn)
        conn.execute(
            """CREATE TABLE IF NOT EXISTS poll_cursor (
                 app TEXT NOT NULL, key TEXT NOT NULL, value TEXT NOT NULL,
                 updated_at REAL NOT NULL DEFAULT 0, PRIMARY KEY (app, key)
               )"""
        )
        conn.commit()
        return conn
    except Exception:  # noqa: BLE001
        return None


def load_since(folder_id: str) -> str | None:
    conn = _cursor_db()
    if conn is not None:
        try:
            r = conn.execute(
                "SELECT value FROM poll_cursor WHERE app=? AND key=?", ("box", str(folder_id))
            ).fetchone()
            if r:
                return r["value"]
            return None
        except Exception:  # noqa: BLE001
            pass  # fall through to the file
    try:
        return json.load(open(_SINCE_FILE)).get(str(folder_id))
    except Exception:  # noqa: BLE001
        return None


def save_since(folder_id: str, created_at: str) -> None:
    if not created_at:
        return
    conn = _cursor_db()
    if conn is not None:
        try:
            conn.execute(
                """INSERT INTO poll_cursor (app,key,value,updated_at) VALUES (?,?,?,?)
                   ON CONFLICT(app,key) DO UPDATE SET value=excluded.value,
                                                     updated_at=excluded.updated_at""",
                ("box", str(folder_id), str(created_at), time.time()),
            )
            conn.commit()
            return
        except Exception:  # noqa: BLE001
            pass  # fall through to the file
    try:
        d = json.load(open(_SINCE_FILE))
    except Exception:  # noqa: BLE001
        d = {}
    d[str(folder_id)] = created_at
    try:
        with open(_SINCE_FILE, "w") as f:
            json.dump(d, f)
    except Exception:  # noqa: BLE001
        pass


def token() -> str:
    """The Box bearer token — dev token (fast, OAuth-free) or a configured access token.
    Reads through the events secret seam (vault://-capable; plaintext unchanged)."""
    try:
        from .secret_seam import secret as _secret
    except ImportError:  # flat load (tests put the events dir on sys.path)
        from secret_seam import secret as _secret
    for key in ("EVENTS_BOX_TOKEN", "BOX_DEV_TOKEN"):
        v = _secret(key)
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
        r = await c.get(
            f"{API}/folders/{folder_id}/items",
            params={"fields": "id,name,type,created_at", "limit": 200},
            headers={"Authorization": f"Bearer {tok}"},
        )
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


async def new_folders_since(folder_id: str, since_iso: str | None, tok: str | None = None) -> list[dict]:
    """Subfolders of ``folder_id`` created strictly after ``since_iso`` — the box/new_folder
    trigger. Same watermark mechanics as files (the poller stores max created_at seen)."""
    items = [i for i in await list_folder_items(folder_id, tok) if i.get("type") == "folder" and i.get("id")]
    if since_iso:
        items = [i for i in items if (i.get("created_at") or "") > since_iso]
    return items


async def file_comments(file_id: str, tok: str | None = None) -> list[dict]:
    """GET /files/{id}/comments — every comment on one file (box/new_box_comment support)."""
    tok = tok or token()
    if not tok:
        raise RuntimeError("no Box token (set BOX_DEV_TOKEN or EVENTS_BOX_TOKEN)")
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.get(
            f"{API}/files/{file_id}/comments",
            params={"fields": "id,message,created_by,created_at,item", "limit": 100},
            headers={"Authorization": f"Bearer {tok}"},
        )
        if r.status_code != 200:
            raise RuntimeError(f"Box comments {file_id} failed: HTTP {r.status_code} {r.text[:200]}")
        return (r.json() or {}).get("entries", []) or []


async def new_comments_since(folder_id: str, since_iso: str | None, tok: str | None = None) -> list[dict]:
    """Comments (across the folder's files) created strictly after ``since_iso``.

    Box has no folder-level comments feed, so this walks the folder's files and collects each
    file's comments — fine at watched-folder scale (≤200 items, resume-drop folders), NOT a
    general-purpose Box crawler. Each returned comment carries ``file`` (name) + ``file_id``."""
    out: list[dict] = []
    for f in await list_folder_items(folder_id, tok):
        if f.get("type") != "file":
            continue
        try:
            comments = await file_comments(f["id"], tok)
        except RuntimeError:
            continue  # one file 403ing must not kill the sweep
        for cm in comments:
            created = cm.get("created_at") or ""
            if since_iso and created <= since_iso:
                continue
            out.append(
                {
                    "id": cm.get("id"),
                    "message": cm.get("message"),
                    "by": (
                        (cm.get("created_by") or {}).get("name")
                        or (cm.get("created_by") or {}).get("login")
                        or ""
                    ),
                    "created_at": created,
                    "file": f.get("name"),
                    "file_id": f.get("id"),
                }
            )
    out.sort(key=lambda x: x.get("created_at") or "")
    return out


# ── the download step ─────────────────────────────────────────────────────────
# A watcher that only learns a file's NAME cannot judge a resume. But the agent holds no Box
# credential — that is the whole point of the design — so it cannot fetch the file either.
#
# The server does it. CUGA downloads the bytes with the token it already has, and hands the agent
# CONTENT rather than a pointer. The credential never leaves this process, and nothing about the
# agent's tool surface has to change.
#
# Two shapes come back, because two shapes are useful:
#   * decodable text  → inlined into the prompt, so any agent can read it with no tools at all
#   * anything else   → base64 in the event payload, for an agent with `extract_text_from_bytes`
#
# Capped hard. A 300MB video in a watched folder must not become a 300MB prompt, an OOM, or a
# surprise bill — it becomes a skipped download with a reason the agent can read.
MAX_DOWNLOAD_BYTES = int(os.environ.get("EVENTS_BOX_MAX_DOWNLOAD_BYTES", str(2 * 1024 * 1024)))
MAX_INLINE_CHARS = int(os.environ.get("EVENTS_BOX_MAX_INLINE_CHARS", "20000"))


def download_enabled() -> bool:
    """Off with EVENTS_BOX_DOWNLOAD=0 — then the agent sees only the filename, as before."""
    return os.environ.get("EVENTS_BOX_DOWNLOAD", "1") != "0"


async def download_file(file_id: str, tok: str | None = None, max_bytes: int = MAX_DOWNLOAD_BYTES) -> bytes:
    """Fetch a file's bytes. Raises on a non-200 so an expired token is loud, and refuses anything
    over ``max_bytes`` rather than streaming it into memory."""
    tok = tok or token()
    if not tok:
        raise RuntimeError("no Box token (set BOX_DEV_TOKEN or EVENTS_BOX_TOKEN)")
    async with httpx.AsyncClient(timeout=60, follow_redirects=True) as c:
        async with c.stream(
            "GET", f"{API}/files/{file_id}/content", headers={"Authorization": f"Bearer {tok}"}
        ) as r:
            if r.status_code != 200:
                body = (await r.aread())[:200].decode(errors="replace")
                raise RuntimeError(f"Box download {file_id} failed: HTTP {r.status_code} {body}")
            declared = int(r.headers.get("content-length") or 0)
            if declared > max_bytes:
                raise ValueError(f"file is {declared} bytes, over the {max_bytes}-byte cap")
            buf = bytearray()
            async for chunk in r.aiter_bytes():
                buf.extend(chunk)
                # content-length can lie, or be absent on a chunked response. Check as we go.
                if len(buf) > max_bytes:
                    raise ValueError(f"file exceeds the {max_bytes}-byte cap")
            return bytes(buf)


async def fetch_content(file_id: str, name: str = "", tok: str | None = None) -> dict:
    """Download a file and shape it for an agent prompt. Never raises: a failed download must not
    lose the event, so the reason travels with it and the agent decides what to do.

    Returns one of:
        {"kind": "text",    "text": "...", "truncated": bool, "bytes": n}
        {"kind": "binary",  "base64": "...", "bytes": n}
        {"kind": "skipped", "reason": "..."}
    """
    import base64

    if not download_enabled():
        return {"kind": "skipped", "reason": "downloads disabled (EVENTS_BOX_DOWNLOAD=0)"}
    try:
        raw = await download_file(file_id, tok)
    except Exception as e:  # noqa: BLE001
        return {"kind": "skipped", "reason": str(e)[:200]}
    if not _looks_like_text(raw):
        return {"kind": "binary", "base64": base64.b64encode(raw).decode(), "bytes": len(raw)}
    text = raw.decode("utf-8", errors="replace")
    if len(text) > MAX_INLINE_CHARS:
        return {"kind": "text", "text": text[:MAX_INLINE_CHARS], "truncated": True, "bytes": len(raw)}
    return {"kind": "text", "text": text, "truncated": False, "bytes": len(raw)}


def _looks_like_text(raw: bytes, sniff: int = 4096) -> bool:
    r"""Decodability is not enough. ``b"%PDF-1.4\x00\x01\x02"`` is valid UTF-8 — every byte is below
    0x80 — so a naive ``decode()`` check inlines a PDF into the prompt as mojibake. A NUL byte, or a
    scattering of other control characters, is the reliable tell that this is not prose."""
    if not raw:
        return True
    head = raw[:sniff]
    if b"\x00" in head:
        return False
    try:
        head.decode("utf-8")
    except UnicodeDecodeError:
        return False
    # tab / newline / carriage-return are the only control chars that belong in a text file
    ctrl = sum(1 for b in head if b < 0x20 and b not in (0x09, 0x0A, 0x0D))
    return ctrl / len(head) < 0.01
