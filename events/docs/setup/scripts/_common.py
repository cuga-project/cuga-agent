"""Shared helpers for the pre-req check scripts.

Pure Python 3 stdlib — NO CUGA or Activepieces imports. These scripts only read the keys you put in
`.env` and talk directly to each vendor's public API, so they verify your credentials in isolation,
before any of the CUGA/AP machinery is involved.
"""

import json
import os
import ssl
import sys
import urllib.error
import urllib.request
from pathlib import Path

# scripts/ -> setup/ -> events/docs/ -> <repo root>
REPO_ROOT = Path(__file__).resolve().parents[3]

PASS, FAIL, SKIP = 0, 1, 3
_TTY = sys.stdout.isatty()


def _c(code, s):
    return f"\033[{code}m{s}\033[0m" if _TTY else s


def green(s):
    return _c("32", s)


def red(s):
    return _c("31", s)


def yellow(s):
    return _c("33", s)


def dim(s):
    return _c("2", s)


def bold(s):
    return _c("1", s)


def title(name):
    print(bold(f"\n{name}"))


def ok(msg):
    print(f"  {green('OK')}   {msg}")


def bad(msg):
    print(f"  {red('FAIL')} {msg}")


def warn(msg):
    print(f"  {yellow('!')}    {msg}")


def info(msg):
    print(f"  {dim('.')}    {dim(msg)}")


def load_env(path=None):
    """Merge os.environ with the repo `.env` (the file wins — it's the source of truth here).

    Placeholder values from the template (`<...>`, `replace-...`) are treated as unset.
    """
    env = dict(os.environ)
    p = Path(path) if path else Path(os.environ.get("ENV_FILE", REPO_ROOT / ".env"))
    if p.is_file():
        for line in p.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k = k.strip()
            v = v.split(" #", 1)[0].strip().strip('"').strip("'")
            if v and not v.startswith("<") and not v.lower().startswith("replace"):
                env[k] = v
    return env


def request(method, url, headers=None, data=None, timeout=30):
    """HTTP request that never raises on a non-2xx status.

    Returns (status_code, response_headers_dict, body_bytes). A dict/list `data` is JSON-encoded;
    a str is sent as-is (caller sets Content-Type).
    """
    hdrs = dict(headers or {})
    if isinstance(data, (dict, list)):
        data = json.dumps(data).encode()
        hdrs.setdefault("Content-Type", "application/json")
    elif isinstance(data, str):
        data = data.encode()
    req = urllib.request.Request(url, data=data, method=method, headers=hdrs)
    ctx = ssl.create_default_context()
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
            return r.status, _lower_headers(r.headers), r.read()
    except urllib.error.HTTPError as e:
        return e.code, _lower_headers(e.headers), e.read()


def _lower_headers(headers):
    """Response headers keyed by lower-case name, so callers don't guess the server's casing."""
    return {k.lower(): v for k, v in headers.items()}


def request_json(method, url, headers=None, data=None, timeout=30):
    """Like request() but parses the body as JSON. Returns (status, headers, parsed_or_None, raw)."""
    st, hd, body = request(method, url, headers, data, timeout)
    try:
        parsed = json.loads(body) if body else None
    except Exception:
        parsed = None
    return st, hd, parsed, body
