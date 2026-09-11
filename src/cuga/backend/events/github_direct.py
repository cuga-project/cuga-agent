"""Direct GitHub integration — GitHub's own webhooks and REST API, bypassing Activepieces.

WHY THIS EXISTS. GitHub was the single largest Activepieces dependency in the trigger registry:
14 of the 42 triggers, more than every other AP app combined. Moving it here is the biggest
available reduction in AP surface.

14 TRIGGERS, ONE ROUTE. This is the part that makes it tractable. GitHub does not expose one
endpoint per event type — it POSTs *every* event to a single webhook URL and names the kind in the
``X-GitHub-Event`` header, with a finer ``action`` in the body. So this module is one signature
check plus a dispatch table, not fourteen integrations.

AUTH — a GitHub App, not a PAT. Same shape as ``box_direct``'s client-credentials grant, and for
the same reason: you sign a short-lived JWT with the app's private key and exchange it for an
INSTALLATION TOKEN. The token expires in an hour and is re-minted from static config, so there is
**no refresh token to store, rotate or lose**. A ``GITHUB_TOKEN`` (PAT) is still accepted for a
quick local run and always wins if set.

    GITHUB_APP_ID + GITHUB_APP_PRIVATE_KEY (+ GITHUB_APP_INSTALLATION_ID)   ← production
    GITHUB_TOKEN                                                            ← a PAT, for a demo
    GITHUB_WEBHOOK_SECRET                                                   ← required, fails closed

Reading the API is optional: the webhook payload already carries what the agent needs. The token
exists for enrichment (fetching a PR diff, listing files) and for the setup check.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import time

import httpx

API = "https://api.github.com"
log = logging.getLogger("cuga.events.github")

# ── X-GitHub-Event (+ action) → our trigger event id ────────────────────────────────────────────
# Left side is GitHub's vocabulary, right side is the registry's. Where GitHub reuses one event
# name for several lifecycle actions we key on (event, action); where the event itself is the
# signal, action is None. Everything not listed is deliberately ignored rather than guessed at —
# GitHub sends a lot of traffic nobody armed a watcher for.
_MAP: dict[tuple[str, str | None], str] = {
    ("pull_request", "opened"): "new_pr",
    ("pull_request", "review_requested"): "new_review_request",
    ("issues", "opened"): "new_issue",
    ("push", None): "new_push",
    ("create", None): "new_branch",  # refined by ref_type below
    ("release", "published"): "new_release",
    ("star", "created"): "new_star",
    ("member", "added"): "new_collaborator",
    ("milestone", "created"): "new_milestone",
    ("label", "created"): "new_repo_label",
    ("discussion", "created"): "new_discussion",
    ("discussion_comment", "created"): "new_discussion_comment",
    ("commit_comment", "created"): "new_commit",
}
# Events whose body mentions someone — mapped to new_gh_mention when the payload names the user
# we are watching for. Kept separate because it is a CONTENT match, not an event-type match.
_MENTIONABLE = ("issue_comment", "pull_request_review_comment", "issues", "pull_request")


def webhook_secret() -> str:
    return _secret("GITHUB_WEBHOOK_SECRET")


def _secret(key: str) -> str:
    """Read a credential through the events secret seam (vault://-capable, plaintext unchanged)."""
    try:
        from .secret_seam import secret as _s
    except ImportError:  # flat load (tests put the events dir on sys.path)
        from secret_seam import secret as _s  # type: ignore
    return _s(key)


def _allow_unauthenticated() -> bool:
    return (os.environ.get("EVENTS_ALLOW_UNAUTHENTICATED", "") or "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def verify_signature(headers, raw_body: bytes | str) -> tuple[bool, str]:
    """Verify GitHub's ``X-Hub-Signature-256`` (HMAC-SHA256 of the raw body). Returns (ok, reason).

    FAILS CLOSED, deliberately. An unset secret refuses traffic rather than waving it through: this
    endpoint runs an agent, the URL is public, and "enforce only if configured" means the day you
    forget the secret is the day anyone can fire your flows. The documented local escape hatch is
    the same one the rest of the events layer uses.
    """
    secret = webhook_secret()
    if not secret:
        if _allow_unauthenticated():
            return True, "unverified (EVENTS_ALLOW_UNAUTHENTICATED=1)"
        return False, "GITHUB_WEBHOOK_SECRET not set — refusing unverified GitHub events"
    sent = (headers.get("x-hub-signature-256") or headers.get("X-Hub-Signature-256") or "").strip()
    if not sent:
        return False, "missing X-Hub-Signature-256"
    body = raw_body.encode() if isinstance(raw_body, str) else raw_body
    mine = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    # compare_digest, not ==: a plain comparison leaks the correct prefix through timing.
    if not hmac.compare_digest(mine, sent):
        return False, "signature mismatch"
    return True, "verified"


def event_of(headers, payload: dict) -> str:
    """Map one delivery to a registry trigger event id, or "" when nothing is armed for it.

    Returning "" is the common case and is not an error — GitHub sends every event type the
    installation subscribes to, and most deliveries match no watcher.
    """
    gh = (headers.get("x-github-event") or headers.get("X-GitHub-Event") or "").strip().lower()
    if not gh:
        return ""
    action = (payload or {}).get("action")
    action = action.strip().lower() if isinstance(action, str) else None

    if gh == "create":
        # One GitHub event, two of our triggers — the ref_type says which.
        return "new_branch" if (payload or {}).get("ref_type") == "branch" else ""

    hit = _MAP.get((gh, action)) or _MAP.get((gh, None))
    return hit or ""


def repo_of(payload: dict) -> str:
    """``owner/repo`` from any GitHub payload — the slot every github trigger is keyed on."""
    return str(((payload or {}).get("repository") or {}).get("full_name") or "")


def mentions(payload: dict, login: str) -> bool:
    """Does this payload @-mention ``login``? Used for the new_gh_mention trigger."""
    if not login:
        return False
    needle = "@" + login.lstrip("@").lower()
    for path in (("comment", "body"), ("issue", "body"), ("pull_request", "body")):
        node = payload or {}
        for key in path:
            node = (node or {}).get(key) if isinstance(node, dict) else None
        if isinstance(node, str) and needle in node.lower():
            return True
    return False


def summarize(gh_event: str, payload: dict) -> str:
    """A one-line, human-readable rendering of the delivery — what the agent actually runs on.

    The agent should not have to parse a 4KB GitHub payload to know what happened, and a raw dump
    wastes context on `node_id`s and avatar URLs.
    """
    p = payload or {}
    repo = repo_of(p) or "a repository"
    who = ((p.get("sender") or {}).get("login")) or "someone"
    if gh_event == "pull_request":
        pr = p.get("pull_request") or {}
        return f"PR #{pr.get('number')} in {repo}: {pr.get('title')} — by {who}\n{pr.get('html_url', '')}"
    if gh_event == "issues":
        iss = p.get("issue") or {}
        return (
            f"Issue #{iss.get('number')} in {repo}: {iss.get('title')} — by {who}\n{iss.get('html_url', '')}"
        )
    if gh_event == "push":
        commits = p.get("commits") or []
        head = (commits[-1].get("message", "").splitlines() or [""])[0] if commits else ""
        return f"{len(commits)} commit(s) pushed to {repo} by {who} — latest: {head}"
    if gh_event == "release":
        rel = p.get("release") or {}
        return f"Release {rel.get('tag_name')} published in {repo} by {who}\n{rel.get('html_url', '')}"
    if gh_event == "create":
        return f"New {p.get('ref_type')} '{p.get('ref')}' in {repo} by {who}"
    if gh_event in ("discussion", "discussion_comment"):
        d = p.get("discussion") or {}
        return f"Discussion in {repo}: {d.get('title')} — by {who}\n{d.get('html_url', '')}"
    return f"GitHub {gh_event} in {repo} by {who}"


# ── auth: GitHub App installation token (no refresh token, by design) ───────────────────────────
_inst_cache: dict = {"token": "", "expires_at": 0.0}
_SKEW = 120.0  # re-mint before the stated expiry so a call never starts with a dying token


def _app_config() -> dict:
    app_id, key = _secret("GITHUB_APP_ID"), _secret("GITHUB_APP_PRIVATE_KEY")
    if not (app_id and key):
        return {}
    # A PEM pasted into an env var usually arrives with literal \n — restore them, or the signer
    # rejects a key that is actually correct.
    if "\\n" in key and "-----BEGIN" in key:
        key = key.replace("\\n", "\n")
    return {"app_id": app_id, "private_key": key, "installation_id": _secret("GITHUB_APP_INSTALLATION_ID")}


def configured() -> bool:
    """Is GitHub usable — a PAT, or enough App config to mint an installation token?"""
    return bool(_secret("GITHUB_TOKEN")) or bool(_app_config())


def _app_jwt(cfg: dict) -> str:
    """A short-lived RS256 JWT proving we are the App. Valid 9 minutes (GitHub's max is 10)."""
    import base64

    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding

    def b64(raw: bytes) -> bytes:
        return base64.urlsafe_b64encode(raw).rstrip(b"=")

    now = int(time.time())
    header = b64(json.dumps({"alg": "RS256", "typ": "JWT"}).encode())
    # iat backdated 60s: GitHub rejects a token whose iat is in the future by even a second, and
    # clock skew between us and them is real.
    claims = b64(json.dumps({"iat": now - 60, "exp": now + 540, "iss": cfg["app_id"]}).encode())
    signing_input = header + b"." + claims
    pk = serialization.load_pem_private_key(cfg["private_key"].encode(), password=None)
    sig = pk.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())
    return (signing_input + b"." + b64(sig)).decode()


async def access_token(force: bool = False) -> str:
    """A token to call the GitHub API with: a PAT if set, else a minted installation token.

    Returns "" when GitHub is not configured, or when minting fails — callers treat that as
    "cannot enrich right now", never as a crash.
    """
    pat = _secret("GITHUB_TOKEN")
    if pat:
        return pat
    cfg = _app_config()
    if not cfg:
        return ""
    now = time.time()
    if not force and _inst_cache["token"] and now < _inst_cache["expires_at"]:
        return _inst_cache["token"]
    try:
        jwt = _app_jwt(cfg)
    except Exception as e:  # noqa: BLE001 — a bad PEM must not take the loop down
        log.warning("github: could not sign the App JWT (%s) — check GITHUB_APP_PRIVATE_KEY", e)
        return ""
    inst = cfg.get("installation_id")
    hdrs = {"Authorization": f"Bearer {jwt}", "Accept": "application/vnd.github+json"}
    try:
        async with httpx.AsyncClient(timeout=20) as c:
            if not inst:
                # One App is normally installed once; discover it rather than make the operator
                # hunt for an id in a settings URL.
                r = await c.get(f"{API}/app/installations", headers=hdrs)
                if r.status_code != 200 or not (r.json() or []):
                    log.warning("github: cannot list App installations (HTTP %s)", r.status_code)
                    return ""
                inst = str((r.json() or [])[0].get("id") or "")
            r = await c.post(f"{API}/app/installations/{inst}/access_tokens", headers=hdrs)
    except Exception as e:  # noqa: BLE001
        log.warning("github: installation-token mint failed (%s)", e)
        return ""
    if r.status_code not in (200, 201):
        log.warning("github: installation-token mint returned HTTP %s", r.status_code)
        return ""
    tok = str((r.json() or {}).get("token") or "")
    if not tok:
        return ""
    _inst_cache["token"] = tok
    _inst_cache["expires_at"] = now + (3600 - _SKEW)  # GitHub installation tokens last 1h
    log.info("github: minted an installation token (1h, no refresh token needed)")
    return tok


async def whoami(tok: str | None = None) -> dict:
    """Prove the credential works — used by the setup guide and the live harness."""
    tok = tok or await access_token()
    if not tok:
        return {"ok": False, "error": "no GITHUB_TOKEN and no GitHub App configured"}
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.get(
            f"{API}/user", headers={"Authorization": f"Bearer {tok}", "Accept": "application/vnd.github+json"}
        )
    if r.status_code == 200:
        return {"ok": True, "login": (r.json() or {}).get("login")}
    if r.status_code == 403 and "installation" in r.text.lower():
        return {"ok": True, "login": "(installation token — no user)"}
    return {"ok": False, "error": f"HTTP {r.status_code}"}
