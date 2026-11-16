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
import os
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
# (instance ID, user's instance token) for the tool call being served (registry, task-local).
# The instance ID is kept with the token so a token is never sent to another instance's server.
CALL_USER_TOKEN: ContextVar[tuple[str, str] | None] = ContextVar("cuga_gateway_call_user_token", default=None)

# A token is renewed this long before it expires.
RENEW_BEFORE_SECONDS = 300
_INSTANCE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{0,127}$")


class GatewayError(Exception):
    """A gateway or IAM call failed. The message is safe to show to an admin."""


class GatewayHostNotAllowed(ValueError):
    """The gateway host is not in ``mcp_gateway.allowed_hosts``. The message is safe to show to an admin."""


# Plain http is accepted only for a gateway on this machine (tests, local development).
_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


def valid_instance_id(instance_id: str) -> bool:
    return bool(_INSTANCE_ID_RE.match(instance_id or ""))


def allowed_hosts() -> list[str]:
    """``mcp_gateway.allowed_hosts``: host names, ``host:port``, or ``*.domain`` for its subdomains.

    Empty (the default) allows no gateway: tokens are sent only to hosts the operator listed.
    A comma-separated string is accepted too (for an environment variable).
    """
    from cuga.config import settings

    raw = getattr(getattr(settings, "mcp_gateway", None), "allowed_hosts", None) or []
    if isinstance(raw, str):
        raw = raw.split(",")
    return [str(h).strip().lower().rstrip(".") for h in raw if str(h).strip()]


def host_allowed(host: str, port: int, allowed: list[str]) -> bool:
    for entry in allowed:
        entry_host, entry_port = entry, None
        if entry.count(":") == 1:
            name, _, number = entry.partition(":")
            if number.isdigit():
                entry_host, entry_port = name, int(number)
        if entry_port is not None and entry_port != port:
            continue
        if entry_host.startswith("*."):
            suffix = entry_host[1:]
            if host.endswith(suffix) and len(host) > len(suffix):
                return True
        elif host == entry_host:
            return True
    return False


@dataclass(frozen=True)
class GatewayInstance:
    instance_url: str
    instance_id: str
    base_url: str

    @property
    def mcp_url(self) -> str:
        return f"{self.base_url}/mcp/"


def parse_instance_url(url: str) -> GatewayInstance:
    """Parse ``https://<gateway>[/<path>]/?instance=<id>``.

    Raises ValueError with a message for the admin, and GatewayHostNotAllowed when the host is
    not in ``mcp_gateway.allowed_hosts``. Every token CUGA sends to a gateway goes to the
    ``base_url`` returned here, so this is where the destination is checked.
    """
    raw = (url or "").strip()
    parts = urlsplit(raw)
    try:
        host = (parts.hostname or "").rstrip(".")
        port = parts.port or (443 if parts.scheme == "https" else 80)
    except ValueError:
        host, port = "", 0
    if parts.scheme not in ("https", "http") or not host:
        raise ValueError("Enter the gateway instance URL, for example https://<gateway>/?instance=<id>")
    if parts.scheme == "http" and host not in _LOOPBACK_HOSTS:
        raise ValueError("Use an https:// gateway instance URL. Tokens are not sent over plain http.")
    ids = parse_qs(parts.query).get("instance") or []
    instance_id = ids[0].strip() if ids else ""
    if not _INSTANCE_ID_RE.match(instance_id):
        raise ValueError(
            "The URL has no valid ?instance=<id>. Copy it from the instance page in your workspace."
        )
    if not host_allowed(host, port, allowed_hosts()):
        raise GatewayHostNotAllowed(
            f"The gateway host {host} is not in mcp_gateway.allowed_hosts. "
            "Ask the CUGA administrator to add it."
        )
    # Keep a path prefix (a gateway served under /<prefix>); drop any user:password@.
    netloc = parts.netloc.rsplit("@", 1)[-1]
    base_url = urlunsplit((parts.scheme, netloc, parts.path.rstrip("/"), "", ""))
    return GatewayInstance(instance_url=raw, instance_id=instance_id, base_url=base_url)


def user_mode_instance(config: Any) -> str | None:
    """The instance ID when tool calls of this service config run as the signed-in user."""
    gateway = getattr(config, "gateway", None) or {}
    if gateway.get("run_as") == RUN_AS_USER and gateway.get("instance_id"):
        return str(gateway["instance_id"])
    return None


def stored_tools(config: Any) -> list[dict[str, Any]] | None:
    """Tool definitions saved with a user-mode entry (Connect lists them with the admin's token).

    The registry serves these instead of listing the instance, so user mode needs no API key.
    """
    gateway = getattr(config, "gateway", None) or {}
    tools = gateway.get("tools")
    if gateway.get("run_as") != RUN_AS_USER or not isinstance(tools, list):
        return None
    return [
        {
            "name": t["name"],
            "description": t.get("description") or "",
            "inputSchema": t.get("inputSchema") or {"type": "object", "properties": {}},
            "outputSchema": {},
        }
        for t in tools
        if isinstance(t, dict) and t.get("name")
    ]


def user_token_required_body(instance_id: str) -> dict[str, str]:
    return {
        "error": ERROR_USER_TOKEN_REQUIRED,
        "instance_id": instance_id,
        "message": "This gateway tool runs as the signed-in user, and the call had no user token.",
    }


def _platform_ca_bundle(iam_ca: str) -> str:
    """The platform trust bundle for gateway routes when ``mcp_gateway.ca_bundle`` is empty.

    On Sovereign Core the gateway route has the spoke's default ingress certificate, signed by
    the platform CA, not a public one. The IAM proxy bundle is that platform bundle; without it,
    ``SSL_CERT_FILE`` or ``REQUESTS_CA_BUNDLE`` when set to a file. Empty: httpx's default store.
    """
    if iam_ca:
        return iam_ca
    for name in ("SSL_CERT_FILE", "REQUESTS_CA_BUNDLE"):
        path = os.environ.get(name, "")
        if path and os.path.isfile(path):
            return path
    return ""


def gateway_settings() -> dict[str, Any]:
    """IAM URL and TLS settings. IAM TLS follows ``auth.iam_proxy_*``; gateway TLS ``mcp_gateway.*``,
    falling back to the platform trust bundle (see ``_platform_ca_bundle``)."""
    from cuga.config import settings

    auth = getattr(settings, "auth", None)
    gw = getattr(settings, "mcp_gateway", None)
    iam_skip = bool(getattr(auth, "iam_proxy_skip_verify", False))
    iam_ca = getattr(auth, "iam_proxy_ca_bundle", "") or ""
    gw_skip = bool(getattr(gw, "skip_verify", False))
    gw_ca = getattr(gw, "ca_bundle", "") or _platform_ca_bundle(iam_ca)
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


def _iam_unreachable(e: Exception) -> GatewayError:
    return GatewayError(f"The platform IAM cannot be reached for the token exchange: {type(e).__name__}")


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
        try:
            body = resp.json()
        except ValueError as e:
            raise GatewayError("The platform IAM did not answer with JSON for the token exchange.") from e
        token = (body.get("token") or body.get("access_token") or "") if isinstance(body, dict) else ""
        if not token:
            raise GatewayError("The platform IAM answered without a token.")
        self._token, self._exp = token, _token_exp(token)
        return token

    async def token(self, force: bool = False) -> str:
        async with self._async_lock:
            if force or not self._valid():
                try:
                    async with httpx.AsyncClient(verify=self._verify, timeout=15.0) as client:
                        resp = await client.post(self._url, json={"apikey": self._api_key})
                except httpx.HTTPError as e:
                    raise _iam_unreachable(e) from e
                self._store(resp)
            return self._token

    def sync_token(self, force: bool = False) -> str:
        with self._sync_lock:
            if force or not self._valid():
                try:
                    with httpx.Client(verify=self._verify, timeout=15.0) as client:
                        resp = client.post(self._url, json={"apikey": self._api_key})
                except httpx.HTTPError as e:
                    raise _iam_unreachable(e) from e
                self._store(resp)
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


class _Bearer(httpx.Auth):
    def __init__(self, token: str):
        self._token = token

    def auth_flow(self, request: httpx.Request):
        request.headers["Authorization"] = f"Bearer {self._token}"
        yield request


async def discover(instance_url: str, api_key: str = "", bearer_token: str = "") -> dict[str, Any]:
    """The instance's MCP servers and their tools, with their input schemas.

    Listed with the instance API key (service ID mode) or with a token the caller already has
    for the instance (the signed-in admin, user mode). Tool names are the names the gateway
    exposes on ``/mcp`` (the registry ``include`` list).
    """
    instance = parse_instance_url(instance_url)
    if bearer_token:
        auth: httpx.Auth = _Bearer(bearer_token)
    elif api_key:
        auth = auth_for(instance, api_key)
    else:
        raise GatewayError("Enter the instance API key.")
    s = gateway_settings()
    async with httpx.AsyncClient(verify=s["gateway_verify"], timeout=20.0, auth=auth) as client:
        try:
            resp = await client.get(f"{instance.base_url}/tools", params={"limit": 0})
        except httpx.HTTPError as e:
            raise GatewayError(
                f"The gateway at {instance.base_url} cannot be reached: {type(e).__name__}"
            ) from e
    if resp.status_code in (401, 403):
        who = "You need" if bearer_token else "The key's service ID needs"
        raise GatewayError(
            f"The gateway refused the token ({resp.status_code}). {who} a role on this instance."
        )
    if resp.status_code != 200:
        raise GatewayError(f"The gateway answered {resp.status_code} when listing tools.")
    try:
        body = resp.json()
    except ValueError as e:
        raise GatewayError(
            "The gateway did not answer with JSON when listing tools (for example a sign-in page). "
            "Check the gateway instance URL."
        ) from e
    if not isinstance(body, (list, dict)):
        raise GatewayError("The gateway answered with an unexpected tool list.")
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
            {
                "name": name,
                "description": _field(item, "description") or "",
                "inputSchema": _field(item, "inputSchema", "input_schema")
                or {"type": "object", "properties": {}},
            }
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
