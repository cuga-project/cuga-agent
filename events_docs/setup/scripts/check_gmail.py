#!/usr/bin/env python3
"""Verify Gmail works end-to-end — independent of CUGA/AP.

Mirrors the CUGA use case (email as a delivery sink): do the OAuth GRANT headlessly and SEND a real
email. In CUGA you click "Connect" in the Studio and AP does the consent; here the script does the
exact same consent itself over a **loopback redirect** — a one-time browser approval — then caches
the refresh token so every later run is fully non-interactive. That is the "approve/grant, but via
the API" path.

Keys:   EVENTS_OAUTH_GMAIL_CLIENT_ID, EVENTS_OAUTH_GMAIL_CLIENT_SECRET
Extra:  GMAIL_TEST_TO  (optional recipient; defaults to your own address)

ONE-TIME: on the OAuth client (Google Cloud -> APIs & Services -> Credentials), add
          http://localhost:8765/  as an Authorized redirect URI. This is separate from CUGA's
          callback URI and only used by this script. The script prints this if it hits a mismatch.
"""
import base64
import json
import os
import sys
import urllib.parse
import webbrowser
from email.message import EmailMessage
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from _common import PASS, FAIL, SKIP, load_env, request_json, title, ok, bad, warn, info

PORT = 8765
REDIRECT = f"http://localhost:{PORT}/"
SCOPE = "openid email https://www.googleapis.com/auth/gmail.send"
CACHE = Path.home() / ".config" / "cuga-prereq" / "gmail_token.json"


def _capture_code():
    result = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            result["code"] = q.get("code", [None])[0]
            result["error"] = q.get("error", [None])[0]
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"<h3>CUGA pre-req: Gmail consent received. You can close this tab.</h3>")

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", PORT), Handler)
    srv.handle_request()  # serve exactly one redirect
    srv.server_close()
    return result.get("code"), result.get("error")


def _token_request(cid, secret, **extra):
    data = urllib.parse.urlencode({"client_id": cid, "client_secret": secret, **extra})
    return request_json("POST", "https://oauth2.googleapis.com/token",
                        headers={"Content-Type": "application/x-www-form-urlencoded"}, data=data)


def _email_from_id_token(id_token):
    try:
        payload = id_token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload)).get("email")
    except Exception:
        return None


def _access_token(cid, secret):
    """Return (access_token, email) using a cached refresh token, or a one-time browser consent."""
    if CACHE.is_file():
        cached = json.loads(CACHE.read_text())
        rt = cached.get("refresh_token")
        if rt:
            st, _, j, _ = _token_request(cid, secret, grant_type="refresh_token", refresh_token=rt)
            if st == 200 and j and j.get("access_token"):
                return j["access_token"], cached.get("email")
            warn("cached Gmail token no longer valid — re-consenting")

    auth = "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode({
        "client_id": cid, "redirect_uri": REDIRECT, "response_type": "code",
        "scope": SCOPE, "access_type": "offline", "prompt": "consent"})
    info("opening the Google consent screen in your browser…")
    info(f"if it doesn't open, paste this into a browser:\n      {auth}")
    webbrowser.open(auth)

    code, err = _capture_code()
    if err or not code:
        bad(f"consent failed: {err or 'no authorization code returned'}")
        return None, None

    st, _, j, _ = _token_request(cid, secret, grant_type="authorization_code",
                                 code=code, redirect_uri=REDIRECT)
    if st != 200 or not j or not j.get("access_token"):
        bad(f"token exchange failed (HTTP {st}): {(j or {}).get('error')}")
        if j and j.get("error") == "redirect_uri_mismatch":
            bad(f"add exactly  {REDIRECT}  as an Authorized redirect URI on the OAuth client")
        return None, None

    email = _email_from_id_token(j.get("id_token", ""))
    if j.get("refresh_token"):
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        CACHE.write_text(json.dumps({"refresh_token": j["refresh_token"], "email": email}))
        info(f"cached refresh token -> {CACHE} (outside the repo; next run needs no browser)")
    return j["access_token"], email


def main():
    env = load_env()
    title("Gmail")
    cid = env.get("EVENTS_OAUTH_GMAIL_CLIENT_ID")
    secret = env.get("EVENTS_OAUTH_GMAIL_CLIENT_SECRET")
    if not (cid and secret):
        warn("EVENTS_OAUTH_GMAIL_CLIENT_ID/_SECRET not set — skipping")
        return SKIP

    if os.environ.get("CUGA_PREREQ_NONINTERACTIVE") and not CACHE.is_file():
        warn("Gmail needs a one-time browser consent — run it on its own:  python3 check_gmail.py")
        return SKIP

    at, email = _access_token(cid, secret)
    if not at:
        return FAIL
    ok("OAuth grant works — obtained an access token")

    to = env.get("GMAIL_TEST_TO") or email
    if not to:
        bad("no recipient — set GMAIL_TEST_TO=you@example.com (couldn't read your own address)")
        return FAIL

    msg = EmailMessage()
    msg["To"] = to
    msg["Subject"] = "CUGA pre-req check — Gmail OK"
    msg.set_content("Your Gmail OAuth client works — this email was sent by the pre-req check script.")
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    st, _, j, _ = request_json("POST", "https://gmail.googleapis.com/gmail/v1/users/me/messages/send",
                               headers={"Authorization": f"Bearer {at}"}, data={"raw": raw})
    if st == 200:
        ok(f"sent a test email to {to} — check the inbox")
        return PASS
    bad(f"send failed (HTTP {st}): {(j or {}).get('error')}")
    return FAIL


if __name__ == "__main__":
    sys.exit(main())
