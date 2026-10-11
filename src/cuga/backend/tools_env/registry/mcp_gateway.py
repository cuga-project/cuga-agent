"""MCP gateway instances.

A gateway instance is a workspace-scoped slice of an MCP gateway (Context Forge). The admin
pastes its URL, ``https://<gateway>/?instance=<instanceId>``, and an API key created on the
instance page. The key belongs to a service ID with a role on the instance. CUGA exchanges it
at the platform IAM (``auth.iam_proxy_url``) for a short-lived token that the gateway accepts
on ``<gateway>/mcp/`` and that only reaches the instance's tools.
"""

from __future__ import annotations

import asyncio
import base64
import json
import re
import threading
import time
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, AsyncGenerator, Generator
from urllib.parse import parse_qs, urlsplit, urlunsplit

import httpx
from loguru import logger

AUTH_TYPE = "account_iam_apikey"
RUN_AS_SERVICE = "service"
RUN_AS_USER = "user"

# A user-mode call carries the user's instance token in this header (agent -> registry only).
USER_TOKEN_HEADER = "X-Cuga-Gateway-Token"
# The registry's 401 body when a user-mode call has no token; it names the instance.
ERROR_USER_TOKEN_REQUIRED = "gateway_user_token_required"
# The user's instance token for the tool call being served (registry, task-local).
CALL_USER_TOKEN: ContextVar[str | None] = ContextVar("cuga_gateway_call_user_token", default=None)

# A token is renewed this long before it expires.
RENEW_BEFORE_SECONDS = 300
_INSTANCE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{0,127}$")


class GatewayError(Exception):
    """A gateway or IAM call failed. The message is safe to show to an admin."""


@dataclass(frozen=True)
class GatewayInstance:
    instance_url: str
    instance_id: str
    base_url: str

    @property
    def mcp_url(self) -> str:
        return f"{self.base_url}/mcp/"


def parse_instance_url(url: str) -> GatewayInstance:
    """Parse ``https://<gateway>/?instance=<id>``. Raises ValueError with a message for the admin."""
    raw = (url or "").strip()
    parts = urlsplit(raw)
    if parts.scheme not in ("https", "http") or not parts.netloc:
        raise ValueError("Enter the gateway instance URL, for example https://<gateway>/?instance=<id>")
    ids = parse_qs(parts.query).get("instance") or []
    instance_id = ids[0].strip() if ids else ""
    if not _INSTANCE_ID_RE.match(instance_id):
        raise ValueError(
            "The URL has no valid ?instance=<id>. Copy it from the instance page in your workspace."
        )
    base_url = urlunsplit((parts.scheme, parts.netloc, "", "", "")).rstrip("/")
    return GatewayInstance(instance_url=raw, instance_id=instance_id, base_url=base_url)


def user_mode_instance(config: Any) -> str | None:
    """The instance ID when tool calls of this service config run as the signed-in user."""
    gateway = getattr(config, "gateway", None) or {}
    if gateway.get("run_as") == RUN_AS_USER and gateway.get("instance_id"):
        return str(gateway["instance_id"])
    return None


def user_token_required_body(instance_id: str) -> dict[str, str]:
    return {
        "error": ERROR_USER_TOKEN_REQUIRED,
        "instance_id": instance_id,
        "message": "This gateway tool runs as the signed-in user, and the call had no user token.",
    }


def gateway_settings() -> dict[str, Any]:
    """IAM URL and TLS settings. IAM TLS follows ``auth.iam_proxy_*``; gateway TLS ``mcp_gateway.*``."""
    from cuga.config import settings

    auth = getattr(settings, "auth", None)
    gw = getattr(settings, "mcp_gateway", None)
    iam_skip = bool(getattr(auth, "iam_proxy_skip_verify", False))
    iam_ca = getattr(auth, "iam_proxy_ca_bundle", "") or ""
    gw_skip = bool(getattr(gw, "skip_verify", False))
    gw_ca = getattr(gw, "ca_bundle", "") or ""
    return {
        "iam_url": (getattr(auth, "iam_proxy_url", "") or "").rstrip("/"),
        "iam_verify": False if iam_skip else (iam_ca or True),
        "gateway_verify": False if gw_skip else (gw_ca or True),
    }


def _token_exp(token: str) -> float:
    """``exp`` of a JWT, read without verification (the gateway verifies it). 0 when absent."""
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return float(json.loads(base64.urlsafe_b64decode(payload)).get("exp") or 0)
    except Exception:
        return 0.0


def _iam_error(resp: httpx.Response) -> GatewayError:
    detail = ""
    try:
        body = resp.json()
        detail = str(body.get("message") or body.get("error") or body.get("detail") or "")
    except Exception:
        pass
    if resp.status_code in (400, 401, 403):
        return GatewayError(
            f"The platform IAM refused the instance API key ({resp.status_code}{': ' + detail if detail else ''}). "
            "Check that the key was created on this gateway instance and is still active."
        )
    return GatewayError(f"The platform IAM answered {resp.status_code} for the token exchange.")


class AccountIAMApiKeyAuth(httpx.Auth):
    """Sends a token exchanged from an instance API key; renews it before expiry and once after a 401."""

    def __init__(self, iam_url: str, instance_id: str, api_key: str, verify: bool | str = True):
        if not iam_url:
            raise GatewayError("auth.iam_proxy_url is not set, so the instance API key cannot be exchanged.")
        self._url = f"{iam_url.rstrip('/')}/api/2.0/services/{instance_id}/apikeys/token"
        self._api_key = api_key
        self._verify = verify
        self._token = ""
        self._exp = 0.0
        self._async_lock = asyncio.Lock()
        self._sync_lock = threading.Lock()

    def __repr__(self) -> str:  # never print the key
        return f"AccountIAMApiKeyAuth(url={self._url!r})"

    def _valid(self) -> bool:
        return bool(self._token) and (self._exp == 0 or time.time() < self._exp - RENEW_BEFORE_SECONDS)

    def _store(self, resp: httpx.Response) -> str:
        if resp.status_code != 200:
            raise _iam_error(resp)
        body = resp.json()
        token = body.get("token") or body.get("access_token") or ""
        if not token:
            raise GatewayError("The platform IAM answered without a token.")
        self._token, self._exp = token, _token_exp(token)
        return token

    async def token(self, force: bool = False) -> str:
        async with self._async_lock:
            if force or not self._valid():
                async with httpx.AsyncClient(verify=self._verify, timeout=15.0) as client:
                    self._store(await client.post(self._url, json={"apikey": self._api_key}))
            return self._token

    def sync_token(self, force: bool = False) -> str:
        with self._sync_lock:
            if force or not self._valid():
                with httpx.Client(verify=self._verify, timeout=15.0) as client:
                    self._store(client.post(self._url, json={"apikey": self._api_key}))
            return self._token

    async def async_auth_flow(self, request: httpx.Request) -> AsyncGenerator[httpx.Request, httpx.Response]:
        request.headers["Authorization"] = f"Bearer {await self.token()}"
        response = yield request
        if response.status_code == 401:
            request.headers["Authorization"] = f"Bearer {await self.token(force=True)}"
            yield request

    def sync_auth_flow(self, request: httpx.Request) -> Generator[httpx.Request, httpx.Response, None]:
        request.headers["Authorization"] = f"Bearer {self.sync_token()}"
        response = yield request
        if response.status_code == 401:
            request.headers["Authorization"] = f"Bearer {self.sync_token(force=True)}"
            yield request


def auth_for(instance: GatewayInstance, api_key: str) -> AccountIAMApiKeyAuth:
    s = gateway_settings()
    return AccountIAMApiKeyAuth(s["iam_url"], instance.instance_id, api_key, verify=s["iam_verify"])


def _field(item: dict[str, Any], *names: str) -> Any:
    for n in names:
        if item.get(n) not in (None, ""):
            return item[n]
    return None


async def discover(instance_url: str, api_key: str) -> dict[str, Any]:
    """The instance's MCP servers and their tools, as the instance API key sees them.

    Tool names are the names the gateway exposes on ``/mcp`` (the registry ``include`` list).
    """
    instance = parse_instance_url(instance_url)
    if not api_key:
        raise GatewayError("Enter the instance API key.")
    auth = auth_for(instance, api_key)
    s = gateway_settings()
    async with httpx.AsyncClient(verify=s["gateway_verify"], timeout=20.0, auth=auth) as client:
        try:
            resp = await client.get(f"{instance.base_url}/tools", params={"limit": 0})
        except httpx.HTTPError as e:
            raise GatewayError(
                f"The gateway at {instance.base_url} cannot be reached: {type(e).__name__}"
            ) from e
    if resp.status_code in (401, 403):
        raise GatewayError(
            f"The gateway refused the token ({resp.status_code}). The key's service ID needs a role on this instance."
        )
    if resp.status_code != 200:
        raise GatewayError(f"The gateway answered {resp.status_code} when listing tools.")
    body = resp.json()
    items = body if isinstance(body, list) else (body.get("tools") or body.get("items") or [])
    servers: dict[str, list[dict[str, str]]] = {}
    for item in items:
        if not isinstance(item, dict) or item.get("enabled") is False:
            continue
        name = _field(item, "name")
        if not name:
            continue
        server = _field(item, "gatewaySlug", "gateway_slug") or "other"
        servers.setdefault(server, []).append(
            {"name": name, "description": _field(item, "description") or ""}
        )
    logger.info(
        "Gateway instance {} lists {} tools on {} servers", instance.instance_id, len(items), len(servers)
    )
    return {
        "instance_id": instance.instance_id,
        "mcp_url": instance.mcp_url,
        "servers": [
            {"name": k, "tools": sorted(v, key=lambda t: t["name"])} for k, v in sorted(servers.items())
        ],
    }
