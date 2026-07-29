#!/usr/bin/env python3
"""Check the ngrok prerequisite (the stable public URL for webhook/OAuth connectors).

This is a config check, not a live-tunnel test — a live tunnel needs the server running, which is
out of scope for this standalone pre-req stage. It confirms: the binary is installed, an authtoken
is configured, and EVENTS_NGROK_DOMAIN is set. See NGROK.md.

Keys:  EVENTS_NGROK_DOMAIN
"""
import shutil
import sys
from pathlib import Path

from _common import PASS, FAIL, load_env, title, ok, bad, warn, info


def _authtoken_configured():
    candidates = [
        Path.home() / ".config" / "ngrok" / "ngrok.yml",
        Path.home() / "Library" / "Application Support" / "ngrok" / "ngrok.yml",
    ]
    for p in candidates:
        if p.is_file() and "authtoken" in p.read_text():
            return True
    return False


def main():
    env = load_env()
    title("ngrok (public URL prerequisite)")
    failed = False

    if shutil.which("ngrok"):
        ok("ngrok binary installed")
    else:
        bad("ngrok not installed — brew install ngrok")
        failed = True

    if _authtoken_configured():
        ok("authtoken configured")
    else:
        bad("no authtoken found — run: ngrok config add-authtoken <token>")
        failed = True

    dom = env.get("EVENTS_NGROK_DOMAIN")
    if dom:
        ok(f"EVENTS_NGROK_DOMAIN = {dom}")
    else:
        warn("EVENTS_NGROK_DOMAIN not set — you'll get a flapping quick-tunnel URL (see NGROK.md)")

    info("a live tunnel is verified once the server is running (out of scope for this stage)")
    return FAIL if failed else PASS


if __name__ == "__main__":
    sys.exit(main())
