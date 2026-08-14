#!/usr/bin/env python3
"""gen_actions.py — DRAFT action-registry rows for a piece from the LIVE Activepieces catalog.

This is the "adding a piece = DATA, not code" tool (design notes in the events_channels_docs repo §10). It reads ``GET /api/v1/pieces/<piece>`` — the same endpoint
``ap_engine`` already uses — and prints ready-to-verify ``actions.Action`` rows: each action's
``ap_action`` name, its props with AP types + required flags, and a destructive/kind heuristic so
you can eyeball which actions to keep.

It DRAFTS; it does not write ``actions.py`` — a human verifies (the Gmail piece, for instance, has
no real archive/label action; only ``custom_api_call`` — the generator flags that so you don't ship
a phantom). Paste the rows you want, set ``source`` hints (answer/trigger/static/user), commit.

Usage:
  python3 events/scripts/gen_actions.py @activepieces/piece-gmail
  python3 events/scripts/gen_actions.py gmail            # short name → @activepieces/piece-gmail
  AP_BASE_URL=http://localhost:8081 python3 events/scripts/gen_actions.py github

Env: AP_BASE_URL (default http://localhost:8081).
"""

import json
import os
import re
import sys
import urllib.error
import urllib.request

AP = os.environ.get("AP_BASE_URL", "http://localhost:8081").rstrip("/")

# actions whose verb mutates/destroys — flagged destructive=True (gates approval, design §3.4b).
_DESTRUCTIVE = re.compile(r"\b(delete|remove|trash|archive|purge|drop|destroy|revoke|overwrite)\b", re.I)
_READONLY = re.compile(r"\b(get|list|search|read|fetch|find|lookup|retrieve)\b", re.I)


def _piece_key(name: str) -> str:
    return name if name.startswith("@activepieces/") else f"@activepieces/piece-{name}"


def _fetch(piece: str) -> dict:
    url = f"{AP}/api/v1/pieces/{piece}"
    try:
        with urllib.request.urlopen(url, timeout=10) as r:  # nosec - local AP
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        sys.exit(f"AP said HTTP {e.code} for {url} — is the piece installed? (events/scripts/ap_pieces.py)")
    except Exception as e:  # noqa: BLE001
        sys.exit(f"couldn't reach AP at {url}: {e}\nSet AP_BASE_URL or start AP (podman).")


def _kind(name: str, disp: str) -> str:
    blob = f"{name} {disp}"
    if _DESTRUCTIVE.search(blob):
        return "DESTRUCTIVE"
    if _READONLY.search(blob):
        return "read"
    return "write"


def _guess_source(pname: str) -> str:
    p = pname.lower()
    if p in ("message_id", "messageid", "thread_id", "id"):
        return "trigger"
    if p in ("body", "text", "content", "message", "comment"):
        return "answer"
    if p in ("body_type", "reply_type", "draft", "include_original_message"):
        return "static"
    return "user"


def _typed_empty(t: str):
    return [] if t == "ARRAY" else (False if t == "CHECKBOX" else "")


def check(piece: str) -> None:
    """--check: arm a throwaway AP flow for EACH action and report VALID/INVALID — folding the
    validity-probe into the generator so you never discover an unfireable action by hand again.
    Emits ALL props (optionals as typed empties — the rule that made send_email valid), templating a
    message-id field from the trigger when present. Requires a connected credential for the piece."""
    import asyncio

    sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[2] / "src"))
    from cuga.backend.events.ap_engine import APEngine  # noqa: E402

    data = _fetch(piece)
    app = piece.split("piece-")[-1].split("-")[0]
    acts = data.get("actions", {}) or {}
    trigs = list((data.get("triggers", {}) or {}).keys())
    trig = next((t for t in trigs if "new" in t.lower()), trigs[0] if trigs else None)

    async def run():
        import httpx

        eng = APEngine()
        ok, why = await eng.available()
        if not ok:
            sys.exit(f"AP not reachable: {why}")
        async with httpx.AsyncClient(timeout=40) as c:
            hdrs = await eng._auth(c)
            conn = next(
                (
                    x.get("externalId")
                    for x in await eng._connections(c, hdrs, eng.project_id)
                    if str(x.get("externalId", "")).endswith(f"::{app}")
                ),
                None,
            )
            if not conn:
                sys.exit(f"no connected {app} credential in AP — connect it first, then re-run --check")
            gver = await eng._piece_version(c, piece)
            print(f"# validity probe: {piece} ({len(acts)} actions)  conn={conn}\n")
            for name, a in acts.items():
                inp = {}
                for pn, pv in (a.get("props", {}) or {}).items():
                    t = pv.get("type", "SHORT_TEXT")
                    if pn in ("message_id", "messageId", "id"):
                        inp[pn] = "{{trigger.message.id}}"  # templated from the trigger
                    elif not pv.get("required"):
                        inp[pn] = _typed_empty(t)  # optionals → typed empties
                    elif t == "ARRAY":
                        inp[pn] = ["sample@example.com"]
                    elif t in ("STATIC_DROPDOWN", "DROPDOWN"):
                        opts = ((pv.get("options") or {}).get("options")) or []
                        inp[pn] = opts[0]["value"] if opts else "sample"
                    elif t == "CHECKBOX":
                        inp[pn] = False
                    elif t == "NUMBER":
                        inp[pn] = 1
                    else:
                        inp[pn] = "sample"
                fid = (
                    await c.post(
                        f"{eng.base}/api/v1/flows",
                        headers=hdrs,
                        json={"displayName": f"probe-{name}", "projectId": eng.project_id},
                    )
                ).json()["id"]
                try:
                    if trig:
                        await eng._post_op(
                            c,
                            fid,
                            eng._piece_trigger_op(
                                piece, trig, {"auth": f"{{{{connections['{conn}']}}}}"}, gver
                            ),
                            hdrs,
                        )
                    op = eng._action_op(
                        piece=piece,
                        ap_action=name,
                        inp=inp,
                        ver=gver,
                        parent="trigger",
                        name="step_1",
                        connection=conn,
                    )
                    await eng._post_op(c, fid, op, hdrs)
                    d = (await c.get(f"{eng.base}/api/v1/flows/{fid}", headers=hdrs)).json()
                    v = (d.get("version") or {}).get("trigger", {}).get("nextAction", {}).get("valid")
                    print(f"  [{'VALID  ' if v else 'INVALID'}] {name}")
                finally:
                    await c.delete(f"{eng.base}/api/v1/flows/{fid}", headers=hdrs)

    asyncio.run(run())


def main() -> None:
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    if "--check" in sys.argv:
        check(_piece_key(next(a for a in sys.argv[1:] if not a.startswith("-"))))
        return
    piece = _piece_key(sys.argv[1])
    app = piece.split("piece-")[-1].split("-")[0]  # gmail, github, box…
    data = _fetch(piece)
    acts = data.get("actions", {}) or {}
    print(
        f"# piece: {piece}  (app={app})  —  {len(acts)} actions\n"
        f"# verify each row, set source hints, keep only what you'll support.\n"
    )
    for name, a in acts.items():
        kind = _kind(name, a.get("displayName", ""))
        flag = (
            "  # ⚠ DESTRUCTIVE — approval-gated"
            if kind == "DESTRUCTIVE"
            else ("  # read-only (usually a TOOL, not a post-agent action)" if kind == "read" else "")
        )
        print(f'_a(name="{name}", title="{a.get("displayName", name)}", ap_action="{name}",{flag}')
        if kind == "DESTRUCTIVE":
            print("   destructive=True,")
        props = a.get("props", {}) or {}
        if props:
            print("   params=(")
            for pn, pv in props.items():
                req = ", required=True" if pv.get("required") else ""
                src = _guess_source(pn)
                extra = ', array=True' if pv.get("type") == "ARRAY" else ""
                print(f'       Param("{pn}", "{pv.get("type", "SHORT_TEXT")}"{req}, source="{src}"{extra}),')
            print("   ),")
        print("   **_APP),\n")
    # a nudge about the Gmail-style gap
    names = set(acts)
    if "custom_api_call" in names:
        print(
            "# NOTE: this piece exposes custom_api_call — capabilities NOT in the list above "
            "(e.g. archive/label/delete for gmail) are reachable ONLY via a raw custom_api_call "
            "step. Do NOT invent registry rows for them; add custom_api_call deliberately if needed."
        )


if __name__ == "__main__":
    main()
