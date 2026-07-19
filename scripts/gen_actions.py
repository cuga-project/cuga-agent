#!/usr/bin/env python3
"""gen_actions.py — DRAFT action-registry rows for a piece from the LIVE Activepieces catalog.

This is the "adding a piece = DATA, not code" tool (design: events_docs/plans/
TRIGGERS_ACTIONS_DESIGN.md §10). It reads ``GET /api/v1/pieces/<piece>`` — the same endpoint
``ap_engine`` already uses — and prints ready-to-verify ``actions.Action`` rows: each action's
``ap_action`` name, its props with AP types + required flags, and a destructive/kind heuristic so
you can eyeball which actions to keep.

It DRAFTS; it does not write ``actions.py`` — a human verifies (the Gmail piece, for instance, has
no real archive/label action; only ``custom_api_call`` — the generator flags that so you don't ship
a phantom). Paste the rows you want, set ``source`` hints (answer/trigger/static/user), commit.

Usage:
  python3 scripts/gen_actions.py @activepieces/piece-gmail
  python3 scripts/gen_actions.py gmail            # short name → @activepieces/piece-gmail
  AP_BASE_URL=http://localhost:8081 python3 scripts/gen_actions.py github

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
_DESTRUCTIVE = re.compile(r"\b(delete|remove|trash|archive|purge|drop|destroy|revoke|overwrite)\b",
                          re.I)
_READONLY = re.compile(r"\b(get|list|search|read|fetch|find|lookup|retrieve)\b", re.I)


def _piece_key(name: str) -> str:
    return name if name.startswith("@activepieces/") else f"@activepieces/piece-{name}"


def _fetch(piece: str) -> dict:
    url = f"{AP}/api/v1/pieces/{piece}"
    try:
        with urllib.request.urlopen(url, timeout=10) as r:      # nosec - local AP
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        sys.exit(f"AP said HTTP {e.code} for {url} — is the piece installed? (scripts/ap_pieces.py)")
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


def main() -> None:
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    piece = _piece_key(sys.argv[1])
    app = piece.split("piece-")[-1].split("-")[0]           # gmail, github, box…
    data = _fetch(piece)
    acts = data.get("actions", {}) or {}
    print(f"# piece: {piece}  (app={app})  —  {len(acts)} actions\n"
          f"# verify each row, set source hints, keep only what you'll support.\n")
    for name, a in acts.items():
        kind = _kind(name, a.get("displayName", ""))
        flag = "  # ⚠ DESTRUCTIVE — approval-gated" if kind == "DESTRUCTIVE" else (
            "  # read-only (usually a TOOL, not a post-agent action)" if kind == "read" else "")
        print(f'_a(name="{name}", title="{a.get("displayName", name)}", ap_action="{name}",'
              f'{flag}')
        if kind == "DESTRUCTIVE":
            print("   destructive=True,")
        props = a.get("props", {}) or {}
        if props:
            print("   params=(")
            for pn, pv in props.items():
                req = ", required=True" if pv.get("required") else ""
                src = _guess_source(pn)
                extra = ', array=True' if pv.get("type") == "ARRAY" else ""
                print(f'       Param("{pn}", "{pv.get("type", "SHORT_TEXT")}"{req}, '
                      f'source="{src}"{extra}),')
            print("   ),")
        print("   **_APP),\n")
    # a nudge about the Gmail-style gap
    names = set(acts)
    if "custom_api_call" in names:
        print("# NOTE: this piece exposes custom_api_call — capabilities NOT in the list above "
              "(e.g. archive/label/delete for gmail) are reachable ONLY via a raw custom_api_call "
              "step. Do NOT invent registry rows for them; add custom_api_call deliberately if needed.")


if __name__ == "__main__":
    main()
