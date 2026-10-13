"""Tool calls to MCP gateway instances as the signed-in user.

At login CUGA keeps the user's IdP access token in an encrypted cookie (``cuga_idp``, Fernet with
``CUGA_SECRET_KEY``, bound to the session cookie it was issued with). During a chat request a provider exchanges it
at the platform IAM for a short-lived token of one gateway instance
(``POST {iam}/api/2.0/services/<instanceId>/token``), so the gateway sees the user and checks the
user's own role. The IdP token never leaves this process; the registry only receives the
instance token, and only for a call to that instance.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from contextvars import ContextVar
from typing import Any, Optional

import httpx
from loguru import logger

from cuga.backend.tools_env.registry import mcp_gateway

IDP_COOKIE_NAME = "cuga_idp"
# Instance token lifetime asked from the IAM; a removed role stops working within about this.
TOKEN_LIFETIME_SECONDS = 900
RENEW_BEFORE_SECONDS = 60


class GatewayAccessError(Exception):
    """The user cannot get a token for the instance. The message is meant for the user."""


# --- cookie ------------------------------------------------------------------------------


def _fernet():
    from cuga.backend.storage.secrets_store import _fernet as secrets_fernet

    return secrets_fernet()


def user_tokens_available() -> bool:
    """True when the IdP token can be kept (an encryption key is configured)."""
    return _fernet() is not None


def _session_binding(session_token: str) -> str:
    return hashlib.sha256(session_token.encode()).hexdigest()


def encode_idp_cookie(idp_token: str, session_token: str) -> Optional[str]:
    f = _fernet()
    if f is None or not idp_token or not session_token:
        return None
    return f.encrypt(json.dumps({"s": _session_binding(session_token), "t": idp_token}).encode()).decode()


def decode_idp_cookie(
    value: Optional[str], session_token: Optional[str], max_age: Optional[int] = None
) -> Optional[str]:
    """The IdP token of the cookie, only with the session it was issued with, within ``max_age``."""
    f = _fernet()
    if f is None or not value or not session_token:
        return None
    try:
        data = json.loads(f.decrypt(value.encode(), ttl=max_age))
    except Exception:
        return None
    if data.get("s") != _session_binding(session_token):
        logger.warning("gateway tokens: the IdP cookie belongs to another session; ignored")
        return None
    return data.get("t") or None


# --- exchange and cache ------------------------------------------------------------------

_cache: dict[tuple[str, str], tuple[str, float]] = {}
_locks: dict[tuple[str, str], asyncio.Lock] = {}


def _access_error(status: int, body: str, instance_id: str) -> GatewayAccessError:
    text = body.lower()
    if "role" in text:
        return GatewayAccessError(
            f"You have no role on MCP gateway instance {instance_id}. "
            "Ask the workspace admin for a role on it (for example ServiceUser)."
        )
    if "audience" in text:
        return GatewayAccessError(
            f"The gateway instance {instance_id} does not accept this agent's sign-in. "
            "The instance must be in the same workspace as this agent."
        )
    if status == 401 or "expired" in text:
        return GatewayAccessError("Your sign-in has expired. Sign in again to use the gateway tools.")
    return GatewayAccessError(
        f"The platform IAM answered {status} for the gateway token of instance {instance_id}."
    )


async def _exchange(idp_token: str, instance_id: str) -> tuple[str, float]:
    s = mcp_gateway.gateway_settings()
    if not s["iam_url"]:
        raise GatewayAccessError("auth.iam_proxy_url is not set, so gateway tools cannot run as the user.")
    url = f"{s['iam_url']}/api/2.0/services/{instance_id}/token"
    async with httpx.AsyncClient(verify=s["iam_verify"], timeout=15.0) as client:
        resp = await client.post(
            url,
            params={"tokenExpiration": TOKEN_LIFETIME_SECONDS},
            headers={"Authorization": f"Bearer {idp_token}"},
            json={"jwt": idp_token},
        )
    if resp.status_code != 200:
        raise _access_error(resp.status_code, resp.text[:500], instance_id)
    body = resp.json()
    token = body.get("token") or body.get("access_token") or ""
    if not token:
        raise GatewayAccessError("The platform IAM answered without a gateway token.")
    exp = mcp_gateway._token_exp(token) or (time.time() + TOKEN_LIFETIME_SECONDS)
    return token, exp


class GatewayTokenProvider:
    """Instance tokens for one signed-in user, shared across that user's requests."""

    def __init__(self, sub: str, idp_token: str):
        self.sub = sub
        self._idp_token = idp_token

    def __repr__(self) -> str:  # never print the token
        return f"GatewayTokenProvider(sub={self.sub!r})"

    async def token(self, instance_id: str) -> str:
        key = (self.sub, instance_id)
        lock = _locks.setdefault(key, asyncio.Lock())
        async with lock:
            cached = _cache.get(key)
            if cached and time.time() < cached[1] - RENEW_BEFORE_SECONDS:
                return cached[0]
            token, exp = await _exchange(self._idp_token, instance_id)
            now = time.time()
            for k in [k for k, (_, e) in _cache.items() if e <= now]:
                _cache.pop(k, None)
            _cache[key] = (token, exp)
            return token


_provider: ContextVar[Optional[GatewayTokenProvider]] = ContextVar(
    "cuga_gateway_token_provider", default=None
)


def set_provider(provider: Optional[GatewayTokenProvider]) -> None:
    """Make the user's provider available to tool calls of this request (task-local)."""
    if provider is not None:
        _provider.set(provider)


def provider_from_request(request: Any, user: Any) -> Optional[GatewayTokenProvider]:
    """The signed-in user's provider, or None (no user, no cookie, or no encryption key)."""
    sub = getattr(user, "sub", None)
    if not sub:
        return None
    from cuga.config import settings

    auth = getattr(settings, "auth", None)
    session_token = request.cookies.get(
        getattr(auth, "session_cookie_name", "cuga_session") or "cuga_session"
    )
    max_age = getattr(auth, "session_max_age", None)
    idp_token = decode_idp_cookie(request.cookies.get(IDP_COOKIE_NAME), session_token, max_age=max_age)
    return GatewayTokenProvider(sub, idp_token) if idp_token else None


async def retry_headers(status: int, body_text: str) -> Optional[dict[str, str]]:
    """Headers for one retry when the registry asked for a user token, else None.

    Raises GatewayAccessError when the call needs a user and there is none, or when the IAM
    refuses the user for the instance.
    """
    if status != 401:
        return None
    try:
        body = json.loads(body_text)
    except Exception:
        return None
    if not isinstance(body, dict) or body.get("error") != mcp_gateway.ERROR_USER_TOKEN_REQUIRED:
        return None
    instance_id = str(body.get("instance_id") or "")
    provider = _provider.get()
    if provider is None:
        raise GatewayAccessError(
            "This gateway tool runs as the signed-in user, and this run has no signed-in user "
            "(for example a scheduled or background run)."
        )
    return {mcp_gateway.USER_TOKEN_HEADER: await provider.token(instance_id)}
