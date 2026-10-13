"""Knowledge capabilities issued by the trusted graph runtime.

The signing key stays in the backend process. It is deliberately independent
of the internal token shared with MCP subprocesses. A registry or sandbox can
forward a capability, but cannot change its agent, thread or permitted scopes.
Backend restarts invalidate outstanding capabilities.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from contextvars import ContextVar
from dataclasses import dataclass

RUN_TOKEN_HEADER = "X-Knowledge-Run-Token"
RUN_TOKEN_META = "cuga/knowledge-run-token"
current_run_token: ContextVar[str | None] = ContextVar("knowledge_run_token", default=None)
_signing_key = secrets.token_bytes(32)
# Cuga-lite issues a fresh capability per tool call and E2B per code block
# (blocks time out after 30 s), so a short lifetime costs nothing and bounds
# how long a leaked capability stays usable.
_TOKEN_LIFETIME = 600


@dataclass(frozen=True)
class KnowledgeRunContext:
    agent_id: str
    thread_id: str | None
    scopes: tuple[str, ...]
    # Authenticated user the run acts for; None when auth is disabled. Drives
    # the session-ownership check for internal callers.
    user_id: str | None = None


def issue_run_token(context: KnowledgeRunContext) -> str:
    """Called only by host-side runtime code, never by an HTTP/MCP endpoint."""
    if not context.agent_id:
        raise ValueError("Knowledge run requires an agent identity")
    payload = json.dumps(
        {
            "agent_id": context.agent_id,
            "thread_id": context.thread_id,
            "scopes": context.scopes,
            "user_id": context.user_id,
            "expires": time.time() + _TOKEN_LIFETIME,
        },
        separators=(",", ":"),
    ).encode()
    encoded = base64.urlsafe_b64encode(payload).decode()
    signature = hmac.new(_signing_key, encoded.encode(), hashlib.sha256).hexdigest()
    return f"{encoded}.{signature}"


def verify_run_token(token: str) -> KnowledgeRunContext:
    """Resolve authenticated identity; untrusted identity headers are irrelevant."""
    try:
        if not isinstance(token, str) or len(token) > 8192:
            raise ValueError
        encoded, signature = token.split(".", 1)
        expected = hmac.new(_signing_key, encoded.encode("ascii"), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            raise ValueError
        payload = json.loads(base64.b64decode(encoded, altchars=b"-_", validate=True))
        if payload["expires"] <= time.time():
            raise ValueError
        return KnowledgeRunContext(
            payload["agent_id"],
            payload["thread_id"],
            tuple(payload["scopes"]),
            payload.get("user_id"),
        )
    except (ValueError, TypeError, KeyError, UnicodeError) as exc:
        raise ValueError("Invalid or expired knowledge run capability") from exc
