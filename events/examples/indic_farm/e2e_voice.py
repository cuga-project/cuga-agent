"""End-to-end proof for the Indic Farm Assistant — a WhatsApp voice note, with NO real WhatsApp traffic.

A FAKE Meta Graph API stands in for Meta: it serves the inbound voice note by media id and captures
what the events layer sends back (media uploads + messages). The webhook is signed the way Meta signs
it, so the real signature check, the real media download, and the real upload-then-send path all run.

    # 1. speech service(s), handbook MCP, CUGA (farm roster), events service — see README.md
    # 2. then:
    python e2e_voice.py --audio /tmp/q_te.ogg
    python e2e_voice.py --text "बीज की गुणवत्ता की पहचान कैसे करें?"

The events service must run with WHATSAPP_GRAPH_BASE=http://127.0.0.1:8399, a WHATSAPP_APP_SECRET
matching --secret, and EVENTS_TURN_PIPELINES pointing at a config whose providers are reachable.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import threading
import time
from pathlib import Path

import httpx
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

STATE: dict = {"inbound_audio": b"", "inbound_mime": "", "uploads": {}, "messages": [], "n": 0}
graph = FastAPI(docs_url=None, redoc_url=None)


@graph.get("/_media/{media_id}")
def media_bytes(media_id: str, request: Request):
    """Meta's step 2: the bytes, same bearer token."""
    if not request.headers.get("authorization"):
        return JSONResponse({"error": {"message": "missing token"}}, 401)
    data, mime = STATE["uploads"].get(media_id, (STATE["inbound_audio"], STATE["inbound_mime"]))
    return Response(content=data, media_type=mime or "application/octet-stream")


@graph.get("/{ver}/{media_id}")
def media_url(ver: str, media_id: str, request: Request):
    """Meta's step 1: a media id → a short-lived URL (ours points back here).

    Registered AFTER /_media/{id} on purpose: this path is a catch-all, and when it came first it also
    swallowed the download route — so the events layer dutifully downloaded a JSON body as "audio"."""
    if not request.headers.get("authorization"):
        return JSONResponse({"error": {"message": "missing token"}}, 401)
    if media_id in STATE["uploads"]:
        data, mime = STATE["uploads"][media_id]
    else:
        data, mime = STATE["inbound_audio"], STATE["inbound_mime"]
    return {"url": f"http://127.0.0.1:8399/_media/{media_id}", "mime_type": mime, "file_size": len(data)}


@graph.post("/{ver}/{pnid}/media")
async def upload(ver: str, pnid: str, request: Request):
    form = await request.form()
    f = form["file"]
    data = await f.read()
    STATE["n"] += 1
    mid = f"UP{STATE['n']}"
    STATE["uploads"][mid] = (data, str(form.get("type") or f.content_type or ""))
    return {"id": mid}


@graph.post("/{ver}/{pnid}/messages")
async def messages(ver: str, pnid: str, request: Request):
    body = await request.json()
    STATE["messages"].append(body)
    return {"messaging_product": "whatsapp", "messages": [{"id": f"wamid.OUT{len(STATE['messages'])}"}]}


def _serve(port: int) -> threading.Thread:
    cfg = uvicorn.Config(graph, host="127.0.0.1", port=port, log_level="error")
    server = uvicorn.Server(cfg)
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    for _ in range(100):
        if getattr(server, "started", False):
            return t
        time.sleep(0.1)
    raise SystemExit("fake Graph API did not start")


def _payload(wa_id: str, *, text: str = "", media_id: str = "", mime: str = "") -> dict:
    msg = {"from": wa_id, "id": "wamid.IN1", "timestamp": str(int(time.time()))}
    msg.update(
        {"type": "text", "text": {"body": text}}
        if text
        else {"type": "audio", "audio": {"id": media_id, "mime_type": mime, "voice": True}}
    )
    return {
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "contacts": [{"wa_id": wa_id, "profile": {"name": "E2E farmer"}}],
                            "messages": [msg],
                        }
                    }
                ]
            }
        ]
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--audio", help="voice note to send (ogg/opus, as WhatsApp sends)")
    ap.add_argument("--text", help="send a typed message instead")
    ap.add_argument("--events", default="http://127.0.0.1:8100")
    ap.add_argument("--wa-id", default="919000000001")
    ap.add_argument("--secret", default="e2e-app-secret")
    ap.add_argument("--graph-port", type=int, default=8399)
    ap.add_argument("--timeout", type=float, default=900)
    ap.add_argument("--out", default="e2e_out")
    args = ap.parse_args()
    if not (args.audio or args.text):
        ap.error("pass --audio or --text")

    if args.audio:
        STATE["inbound_audio"] = Path(args.audio).read_bytes()
        STATE["inbound_mime"] = "audio/ogg; codecs=opus"
    _serve(args.graph_port)

    body = _payload(args.wa_id, text=args.text or "", media_id="INMEDIA1", mime=STATE["inbound_mime"])
    raw = json.dumps(body, separators=(",", ":")).encode()
    sig = "sha256=" + hmac.new(args.secret.encode(), raw, hashlib.sha256).hexdigest()
    t0 = time.time()
    r = httpx.post(
        f"{args.events}/api/events/whatsapp/events",
        content=raw,
        headers={"Content-Type": "application/json", "X-Hub-Signature-256": sig},
        timeout=30,
    )
    print(f"webhook → {r.status_code} {r.text.strip()}   ({time.time() - t0:.2f}s to ack)")
    if r.status_code != 200:
        return 1

    want_audio = bool(args.audio)
    seen = 0
    deadline = time.time() + args.timeout
    while time.time() < deadline:
        if len(STATE["messages"]) > seen:
            m = STATE["messages"][seen]
            seen += 1
            kind = m.get("type")
            at = time.time() - t0
            if kind == "text":
                print(f"\n← TEXT after {at:.1f}s:\n{m['text']['body']}\n")
            elif kind == "audio":
                data, mime = STATE["uploads"].get(m["audio"]["id"], (b"", ""))
                out = Path(args.out)
                out.mkdir(parents=True, exist_ok=True)
                dest = out / f"reply.{'ogg' if 'ogg' in mime else 'bin'}"
                dest.write_bytes(data)
                print(f"← VOICE after {at:.1f}s: {len(data)} bytes {mime} → {dest}")
                want_audio = False
            else:
                print(f"← {kind}: {json.dumps(m)[:200]}")
        elif seen and not want_audio:
            break
        else:
            time.sleep(0.5)
    if want_audio:
        print(f"\n⚠ no voice reply within {args.timeout:.0f}s")
    print(f"delivered: {[m.get('type') for m in STATE['messages']]}")
    return 0 if STATE["messages"] and not want_audio else 1


if __name__ == "__main__":
    raise SystemExit(main())
