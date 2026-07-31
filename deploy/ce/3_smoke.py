#!/usr/bin/env python3
"""Smoke-test the deployed CUGA-events app on Code Engine.

Reads the URL from deploy/ce/.ce_urls.env (CUGA_CE_URL) or $CUGA_CE_URL, then:
  1. GET /api/events/status         — the capability report (what's live vs off)
  2. GET /api/events/channels       — inbound channel state (web/telegram/discord/slack)
  3. POST a tiny web-chat turn       — proves the agent answers end to end

No creds needed; it only talks to the public route.
    python deploy/ce/3_smoke.py
"""
import json
import os
import sys
import urllib.request
import urllib.error

CE_DIR = os.path.dirname(os.path.abspath(__file__))


def _url() -> str:
    u = os.environ.get("CUGA_CE_URL", "").strip()
    if u:
        return u.rstrip("/")
    envf = os.path.join(CE_DIR, ".ce_urls.env")
    if os.path.isfile(envf):
        for line in open(envf):
            if "CUGA_CE_URL" in line and "=" in line:
                return line.split("=", 1)[1].strip().strip('"').rstrip("/")
    sys.exit("No CUGA_CE_URL — run deploy/ce/2_deploy_app.sh first, or set CUGA_CE_URL.")


def _get(url: str, timeout: int = 30):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return r.status, json.loads(r.read().decode() or "{}")


def _post(url: str, body: dict, timeout: int = 180):
    data = json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, r.read().decode()


def main() -> int:
    base = _url()
    print(f"CUGA CE app: {base}\n")

    ok = True
    # 1. capability report
    try:
        st, data = _get(f"{base}/api/events/status")
        print(f"[1] /api/events/status -> {st}")
        cap = data.get("capability") or []
        if isinstance(cap, str):
            cap = cap.splitlines()
        for line in cap:
            print("    " + line)
        print(f"    enabled={data.get('enabled')}")
    except Exception as e:  # noqa: BLE001
        ok = False
        print(f"[1] status FAILED: {e}")

    # 2. channels
    try:
        st, data = _get(f"{base}/api/events/channels")
        print(f"\n[2] /api/events/channels -> {st}")
        for c in data.get("channels", []):
            print(f"    {c.get('name','?'):<9} {c.get('status','?'):<14} {c.get('backend','?')}")
    except Exception as e:  # noqa: BLE001
        print(f"\n[2] channels: {e} (non-fatal)")

    # 3. one real web-chat turn through the agent — needs GATEWAY_TOKEN (the /invoke seam is
    #    X-Gateway-Token protected). Reads it from env or deploy/ce/.env.ce; skips if absent.
    print("\n[3] web-chat turn (POST /invoke, agent=cuga) ...")
    gw = os.environ.get("GATEWAY_TOKEN", "").strip()
    if not gw:
        envf = os.path.join(CE_DIR, ".env.ce")
        if os.path.isfile(envf):
            for line in open(envf):
                if line.startswith("GATEWAY_TOKEN="):
                    gw = line.split("=", 1)[1].strip()
    if not gw:
        print("    -> skipped (no GATEWAY_TOKEN in env or .env.ce)")
    else:
        env = {"agent": "cuga",
               "source": {"type": "channel", "name": "web", "thread_id": "ce-smoke"},
               "event": {"kind": "message"},
               "text": "What is the capital of France? Answer in one word."}
        data = json.dumps(env).encode()
        req = urllib.request.Request(f"{base}/invoke", data=data,
                                     headers={"Content-Type": "application/json",
                                              "X-Gateway-Token": gw})
        try:
            with urllib.request.urlopen(req, timeout=200) as r:
                d = json.loads(r.read().decode() or "{}")
            print(f"    -> ok={d.get('ok')}  answer={(d.get('answer') or d.get('error') or '')[:200]!r}")
        except Exception as e:  # noqa: BLE001
            ok = False
            print(f"    -> FAILED: {e}")

    print("\nDONE." if ok else "\nDONE (with issues above).")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
