"""Integration preflight — tests EVERY integration independently, using only ``.env``.

A "doctor" that hits each external service with its real credential and reports ✅/❌ + why, so
you know what's actually working before wiring flows. No CUGA server needed. Stdlib only.

    python3 tests/events/preflight.py
    python3 tests/events/preflight.py telegram box        # only these

Checks: watsonx (LLM) · Activepieces · Telegram · Discord · Slack · Box (dev token) · cuga-* MCP.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request


def _load_dotenv(path: str) -> None:
    if not os.path.exists(path):
        return
    for line in open(path):
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        v = v.split(" #", 1)[0].strip().strip('"').strip("'")
        os.environ[k.strip()] = v


def _http(url, *, method="GET", headers=None, data=None, timeout=25):
    body = None
    if data is not None:
        body = data if isinstance(data, bytes) else json.dumps(data).encode()
    # a real User-Agent — Discord/Cloudflare 403 ("error code 1010") the default python-urllib UA.
    h = {"User-Agent": "Mozilla/5.0 (CUGA-events preflight)", **(headers or {})}
    req = urllib.request.Request(url, data=body, method=method, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode(errors="replace")
            try:
                return r.status, json.loads(raw)
            except Exception:  # noqa: BLE001
                return r.status, raw
    except urllib.error.HTTPError as e:
        raw = e.read().decode(errors="replace")
        try:
            return e.code, json.loads(raw)
        except Exception:  # noqa: BLE001
            return e.code, raw
    except Exception as e:  # noqa: BLE001
        return 0, str(e)


def _need(*keys):
    missing = [k for k in keys if not os.environ.get(k)]
    return missing


# ---- one function per integration; returns (ok: bool, detail: str) --------
def check_watsonx():
    m = _need("WATSONX_APIKEY")
    if m:
        return None, f"skip — no {m[0]}"
    code, body = _http("https://iam.cloud.ibm.com/identity/token", method="POST",
                       headers={"Content-Type": "application/x-www-form-urlencoded"},
                       data=("grant_type=urn:ibm:params:oauth:grant-type:apikey&apikey="
                             + os.environ["WATSONX_APIKEY"]).encode())
    if code == 200 and isinstance(body, dict) and body.get("access_token"):
        return True, "IAM token minted (watsonx apikey valid)"
    return False, f"IAM HTTP {code}: {str(body)[:120]}"


def check_activepieces():
    base = os.environ.get("AP_BASE_URL", "").rstrip("/")
    if not base:
        return None, "skip — no AP_BASE_URL"
    code, _ = _http(f"{base}/api/v1/flags", timeout=10)
    if code != 200:
        return False, f"{base} unreachable (HTTP {code})"
    email, pw = os.environ.get("AP_EMAIL"), os.environ.get("AP_PASSWORD")
    if not (email and pw):
        return True, f"reachable at {base} (no AP_EMAIL/PASSWORD to test auth)"
    code, body = _http(f"{base}/api/v1/authentication/sign-in", method="POST",
                       headers={"Content-Type": "application/json"},
                       data={"email": email, "password": pw})
    if code < 300 and isinstance(body, dict) and body.get("token"):
        return True, f"reachable + authenticated (project {body.get('projectId', '?')[:12]})"
    return False, f"sign-in HTTP {code}: {str(body)[:120]}"


def check_telegram():
    tok = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not tok:
        return None, "skip — no TELEGRAM_BOT_TOKEN"
    code, body = _http(f"https://api.telegram.org/bot{tok}/getMe", timeout=15)
    if code == 200 and isinstance(body, dict) and body.get("ok"):
        u = body["result"]
        return True, f"bot @{u.get('username')} (id {u.get('id')})"
    return False, f"getMe HTTP {code}: {str(body)[:120]}"


def check_discord():
    tok = os.environ.get("DISCORD_BOT_TOKEN")
    if not tok:
        return None, "skip — no DISCORD_BOT_TOKEN"
    code, body = _http("https://discord.com/api/v10/users/@me",
                       headers={"Authorization": f"Bot {tok}"}, timeout=15)
    if code == 200 and isinstance(body, dict) and body.get("id"):
        return True, f"bot {body.get('username')}#{body.get('discriminator', '')} (id {body.get('id')})"
    return False, f"users/@me HTTP {code}: {str(body)[:120]}"


def check_slack():
    tok = os.environ.get("SLACK_BOT_TOKEN")
    if not tok:
        return None, "skip — no SLACK_BOT_TOKEN"
    code, body = _http("https://slack.com/api/auth.test", method="POST",
                       headers={"Authorization": f"Bearer {tok}",
                                "Content-Type": "application/x-www-form-urlencoded"},
                       data=b"")
    if code == 200 and isinstance(body, dict) and body.get("ok"):
        return True, f"team '{body.get('team')}' as {body.get('user')} (bot id {body.get('bot_id', '?')})"
    err = body.get("error") if isinstance(body, dict) else body
    return False, f"auth.test → {err}"


def check_box():
    tok = os.environ.get("BOX_DEV_TOKEN")
    if not tok:
        return None, "skip — no BOX_DEV_TOKEN (dev token expires ~60 min; regenerate)"
    code, body = _http("https://api.box.com/2.0/users/me",
                       headers={"Authorization": f"Bearer {tok}"}, timeout=15)
    if code == 200 and isinstance(body, dict) and body.get("id"):
        return True, f"{body.get('name')} <{body.get('login')}> — dev token OK (⏳ ~60 min)"
    if code == 401:
        return False, "401 — dev token expired/invalid (regenerate it in the Box console)"
    return False, f"users/me HTTP {code}: {str(body)[:120]}"


def check_mcp():
    # the cuga-* servers scale to zero; a reachable HTTP (even 4xx from a GET) = the host is up.
    apps = ("finance", "geo", "web", "knowledge", "code", "local", "text")
    up = []
    for a in apps:
        code, _ = _http(f"https://cuga-apps-mcp-{a}.1gxwxi8kos9y.us-east.codeengine.appdomain.cloud/mcp",
                        timeout=20)
        if code and code < 500:
            up.append(a)
    if not up:
        return False, "no cuga-* MCP servers reachable (Code Engine cold? try again to warm)"
    return True, f"{len(up)}/{len(apps)} reachable: {', '.join('cuga-' + a for a in up)}"


CHECKS = {
    "watsonx": check_watsonx, "activepieces": check_activepieces, "telegram": check_telegram,
    "discord": check_discord, "slack": check_slack, "box": check_box, "mcp": check_mcp,
}


def main(argv) -> int:
    repo = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    _load_dotenv(os.path.join(repo, ".env"))
    want = [a.lower() for a in argv] or list(CHECKS)
    print("Integration preflight (from .env)\n" + "=" * 40)
    fails = 0
    for name in want:
        fn = CHECKS.get(name)
        if not fn:
            print(f"  ?  {name}: unknown check"); continue
        try:
            ok, detail = fn()
        except Exception as e:  # noqa: BLE001
            ok, detail = False, f"error: {e}"
        icon = "✅" if ok else ("➖" if ok is None else "❌")
        if ok is False:
            fails += 1
        print(f"  {icon} {name:12s} {detail}")
    print("=" * 40)
    print("all green" if fails == 0 else f"{fails} integration(s) need attention")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
