import hashlib
from typing import Optional

import httpx
from fastapi import HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from loguru import logger

from cuga.backend.server.auth.issuer_allowlist import normalize_issuer_for_discovery
from cuga.backend.server.auth.models import UserInfo
from cuga.backend.server.auth.jwt_validator import JWTValidator

security = HTTPBearer(auto_error=False)

_validator_cache: dict[str, JWTValidator] = {}
_discovery_cache: dict[str, dict] = {}


def _auth_enabled() -> bool:
    try:
        from cuga.config import settings

        auth = getattr(settings, "auth", None)
        return bool(auth and getattr(auth, "enabled", False))
    except Exception as e:
        logger.warning(
            "auth config check failed (fail-closed): {}",
            e,
            exc_info=True,
        )
        return True


def _authorization_enabled() -> bool:
    """Authorization tracks authentication: role checks are enforced exactly when
    authentication is enabled.

    There is no separate authorization switch. The old ``auth.authorization_enabled``
    setting allowed the unsafe "authentication on, authorization off" state — every
    logged-in user, including chat-only and no-role users, reaching the manage and
    secrets APIs (GHSA-4cj6/GHSA-r2pq, D2). A stale ``authorization_enabled`` left in a
    config or environment from an older release is deliberately ignored, not an error.
    """
    return _auth_enabled()


def _get_manage_roles() -> list[str]:
    try:
        from cuga.config import settings

        auth = getattr(settings, "auth", None)
        return (
            list(getattr(auth, "manage_roles", ["ServiceOwner", "ServiceAdmin"]))
            if auth
            else ["ServiceOwner", "ServiceAdmin"]
        )
    except Exception:
        return ["ServiceOwner", "ServiceAdmin"]


def _get_chat_roles() -> list[str]:
    try:
        from cuga.config import settings

        auth = getattr(settings, "auth", None)
        return (
            list(getattr(auth, "chat_roles", ["ServiceOwner", "ServiceAdmin", "ServiceUser"]))
            if auth
            else ["ServiceOwner", "ServiceAdmin", "ServiceUser"]
        )
    except Exception:
        return ["ServiceOwner", "ServiceAdmin", "ServiceUser"]


def has_manage_access(user: Optional[UserInfo]) -> bool:
    """Return the same effective management permission enforced by the API."""
    if not _authorization_enabled() or user is None:
        return True
    return any(role in _get_manage_roles() for role in (user.roles or []))


def _session_cookie_name() -> str:
    try:
        from cuga.config import settings

        auth = getattr(settings, "auth", None)
        return getattr(auth, "session_cookie_name", "cuga_session") if auth else "cuga_session"
    except Exception:
        return "cuga_session"


def _get_tls_settings() -> tuple[bool, Optional[str]]:
    try:
        from cuga.config import settings

        auth = getattr(settings, "auth", None)
        if auth is not None:
            return (
                bool(getattr(auth, "oidc_skip_verify", False)),
                getattr(auth, "oidc_ca_bundle", None) or None,
            )
    except Exception:
        pass
    return False, None


async def _discover_jwks_for_issuer(issuer: str) -> Optional[str]:
    """Fetch JWKS URI from the issuer's standard OIDC discovery endpoint."""
    normalized = normalize_issuer_for_discovery(issuer)
    if not normalized:
        return None

    if normalized in _discovery_cache:
        return _discovery_cache[normalized].get("jwks_uri")

    skip_verify, ca_bundle = _get_tls_settings()
    ssl_arg: bool | str = False if skip_verify else (ca_bundle or True)

    discovery_url = normalized.rstrip("/") + "/.well-known/openid-configuration"
    try:
        async with httpx.AsyncClient(verify=ssl_arg) as client:
            resp = await client.get(discovery_url, follow_redirects=True)
            resp.raise_for_status()
            data = resp.json()
        _discovery_cache[normalized] = data
        return data.get("jwks_uri")
    except Exception as e:
        logger.debug("Auto-discovery failed for issuer {}: {}", normalized, e)
        return None


async def _get_validator_for_token(token: str) -> Optional[JWTValidator]:
    """Resolve JWKS from the token's own `iss` claim via OIDC discovery."""
    import jwt as pyjwt

    try:
        unverified = pyjwt.decode(token, options={"verify_signature": False})
        issuer = unverified.get("iss")
    except Exception as e:
        logger.debug("_get_validator_for_token: failed to decode token header: {}", e)
        return None

    if not issuer:
        logger.debug("_get_validator_for_token: token has no 'iss' claim")
        return None

    logger.debug("_get_validator_for_token: resolving JWKS for issuer={}", issuer)
    jwks_uri = await _discover_jwks_for_issuer(issuer)
    if not jwks_uri:
        logger.warning(
            "_get_validator_for_token: OIDC discovery failed for issuer={} — cannot validate token", issuer
        )
        return None

    jwks_cache_ttl = 3600
    skip_verify, ca_bundle = _get_tls_settings()
    try:
        from cuga.config import settings

        auth = getattr(settings, "auth", None)
        if auth is not None:
            jwks_cache_ttl = getattr(auth, "jwks_cache_ttl", 3600) or 3600
    except Exception:
        pass

    cache_key = f"{jwks_uri}|{issuer}|{skip_verify}|{ca_bundle or ''}"
    if cache_key not in _validator_cache:
        _validator_cache[cache_key] = JWTValidator(
            jwks_uri=jwks_uri,
            cache_ttl=jwks_cache_ttl,
            issuer=issuer,
            skip_verify=skip_verify,
            ca_bundle=ca_bundle,
        )
    return _validator_cache[cache_key]


async def _get_validator() -> Optional[JWTValidator]:
    from cuga.backend.server.auth.oidc_client import get_oidc_client

    client = get_oidc_client()
    if not client:
        return None
    discovery = await client.get_discovery()
    jwks_uri = discovery.get("jwks_uri", "")
    issuer = discovery.get("issuer")
    if not jwks_uri:
        return None
    jwks_cache_ttl = 3600
    skip_verify = False
    ca_bundle: Optional[str] = None
    try:
        from cuga.config import settings

        auth = getattr(settings, "auth", None)
        if auth is not None:
            jwks_cache_ttl = getattr(auth, "jwks_cache_ttl", 3600) or 3600
            skip_verify = bool(getattr(auth, "oidc_skip_verify", False))
            ca_bundle = getattr(auth, "oidc_ca_bundle", None) or None
    except Exception:
        pass
    cache_key = f"{jwks_uri}|{issuer or ''}|{skip_verify}|{ca_bundle or ''}"
    if cache_key not in _validator_cache:
        if skip_verify:
            logger.warning(
                "JWKS SSL verification is disabled (DYNACONF_AUTH__OIDC_SKIP_VERIFY=true) — do not use in production"
            )
        _validator_cache[cache_key] = JWTValidator(
            jwks_uri=jwks_uri,
            cache_ttl=jwks_cache_ttl,
            issuer=issuer,
            skip_verify=skip_verify,
            ca_bundle=ca_bundle,
        )
    return _validator_cache[cache_key]


async def get_current_user(request: Request) -> Optional[UserInfo]:
    if not _auth_enabled():
        return None

    token: Optional[str] = None
    creds: Optional[HTTPAuthorizationCredentials] = await security(request)
    if creds:
        token = creds.credentials
    if not token:
        cookie_name = _session_cookie_name()
        token = request.cookies.get(cookie_name)
    if not token:
        return None

    # Try to resolve validator from the token's own issuer first (supports IAM/non-OIDC tokens).
    # Fall back to the configured OIDC validator.
    validator = await _get_validator_for_token(token)
    if validator is None:
        logger.debug("get_current_user: no issuer-based validator found, falling back to OIDC validator")
        validator = await _get_validator()
    else:
        logger.debug("get_current_user: using issuer-resolved validator (jwks_uri={})", validator.jwks_uri)
    if not validator:
        raise HTTPException(status_code=503, detail="Auth not configured")

    try:
        payload = validator.validate_and_decode(token)
        user = validator.to_user_info(payload)
        # Bind downstream gateway credentials to the token actually validated here.
        request.state.auth_token_hash = hashlib.sha256(token.encode()).hexdigest()
        logger.debug(
            "Authenticated user sub={} roles={} issuer={} all_claims={}",
            user.sub,
            user.roles,
            payload.get("iss"),
            list(payload.keys()),
        )
        return user
    except Exception as e:
        logger.warning(
            "get_current_user: token validation failed (validator jwks_uri={}): {}", validator.jwks_uri, e
        )
        raise HTTPException(status_code=401, detail="Invalid or expired token")


async def require_auth(request: Request) -> Optional[UserInfo]:
    user = await get_current_user(request)
    if _auth_enabled() and user is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    return user


async def require_manage_access(request: Request) -> Optional[UserInfo]:
    """Require authentication and manage role authorization."""
    user = await require_auth(request)

    # If authorization is disabled, pass through
    if not _authorization_enabled():
        return user

    # If auth is disabled entirely, user will be None - pass through
    if user is None:
        return user

    if not has_manage_access(user):
        manage_roles = _get_manage_roles()
        raise HTTPException(
            status_code=403, detail=f"Access denied. Required roles: {', '.join(manage_roles)}"
        )

    return user


async def require_chat_access(request: Request) -> Optional[UserInfo]:
    """Require authentication and chat role authorization."""
    user = await require_auth(request)

    # If authorization is disabled, pass through
    if not _authorization_enabled():
        return user

    # If auth is disabled entirely, user will be None - pass through
    if user is None:
        return user

    # Check if user has any of the required chat roles
    chat_roles = _get_chat_roles()
    user_roles = user.roles or []

    if not any(role in chat_roles for role in user_roles):
        raise HTTPException(status_code=403, detail=f"Access denied. Required roles: {', '.join(chat_roles)}")

    return user


# Loopback client hosts that count as "same machine" for the local exemption.
# The peer address comes from the ASGI scope (request.client), not from a header.
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})

# Uvicorn's ProxyHeadersMiddleware (on by default under `cuga start`) rewrites
# request.client from X-Forwarded-For when the peer is in FORWARDED_ALLOW_IPS. With
# FORWARDED_ALLOW_IPS=*, a remote caller that sends "X-Forwarded-For: 127.0.0.1"
# becomes a loopback client. A request with any of these headers came through a proxy,
# so it does not get the local exemption. A local proxy that sends none of them still
# looks local; the gate cannot detect that case.
_FORWARDING_HEADERS = ("x-forwarded-for", "x-real-ip", "forwarded")

# The webpack dev server, which proxies /api to the backend.
_DEV_UI_PORT = 3002


def _host_name(host_header: str) -> str:
    """Return the host name of a Host header value, without the port."""
    if host_header.startswith("["):
        return host_header[1 : host_header.find("]")] if "]" in host_header else ""
    return host_header.rsplit(":", 1)[0]


def _trusted_local_origins() -> frozenset[str]:
    """This app's own local UI origins, on the configured UI port and the dev port.

    A TCP connection from the loopback address is not enough on its own: a browser tab
    open on an unrelated, untrusted site can also reach a loopback server. Only a
    browser sends an Origin header, so a present-but-unlisted Origin is refused; a
    request with no Origin header (curl, scripts, the CLI) is unaffected by this check.
    Both schemes are listed because start_demo serves TLS when SSL_KEYFILE/SSL_CERTFILE
    are set (see deployment/certs/README.md).
    """
    try:
        from cuga.config import settings

        ui_port = int(settings.server_ports.demo)
    except Exception:
        ui_port = 7860
    return frozenset(
        f"{scheme}://{host}:{port}"
        for scheme in ("http", "https")
        for host in ("localhost", "127.0.0.1", "[::1]")
        for port in (ui_port, _DEV_UI_PORT)
    )


def _allow_local_admin() -> bool:
    """Decision 1 / Option A: with auth off, same-machine callers may reach the
    secrets and management APIs; remote callers may not. Defaults to True so local
    development and demos keep working. With auth.allow_local_admin=false and auth
    off, no caller can reach these APIs; turn on auth to use them (Decision 1 /
    Option B)."""
    try:
        from cuga.config import settings

        auth = getattr(settings, "auth", None)
        return bool(getattr(auth, "allow_local_admin", True)) if auth else True
    except Exception:
        # Fail closed: if we cannot read the setting, do not grant the local exemption.
        return False


def _is_local_ui_request(request: Request) -> bool:
    """True when the request comes straight from this machine, and, from a browser,
    only from this app's own UI.

    * The peer address is loopback, and no forwarding header is present.
    * The Host header names a loopback host. After a DNS-rebinding attack, the attack
      page has the same origin as this server, so the browser sends no Origin header;
      the Host header still carries the attacker's domain.
    * An Origin header, if present, names one of this app's own UI origins.
    """
    client_host = request.client.host if request.client else None
    if client_host not in _LOOPBACK_HOSTS:
        return False
    if any(name in request.headers for name in _FORWARDING_HEADERS):
        return False
    if _host_name(request.headers.get("host", "")) not in _LOOPBACK_HOSTS:
        return False
    origin = request.headers.get("origin")
    return origin is None or origin in _trusted_local_origins()


async def require_secrets_access(request: Request) -> Optional[UserInfo]:
    """Fail-closed gate for the secrets API.

    The bug this closes: ``require_auth`` returns ``None`` (and every route passes)
    whenever ``auth.enabled=false`` — the shipped default — so on stock settings the
    entire ``/api/secrets`` API is open to any caller that can reach the port,
    including a drive-by request from a website open in the operator's browser.

    Behaviour:

    * ``auth.enabled=true``  -> authentication required and the manage-role check is
      enforced. Authorization tracks authentication (there is no separate switch), so
      a logged-in user without a manage role is refused — there is no configuration
      that authenticates a user and then skips the role check.
    * ``auth.enabled=false`` (default) -> allow only direct same-machine callers
      (see ``_is_local_ui_request``), and only when ``auth.allow_local_admin`` is on;
      refuse everything else with 401.
    """
    return await _require_local_or_manage(request, resource="The secrets API")


async def _require_local_or_manage(request: Request, *, resource: str) -> Optional[UserInfo]:
    """Shared fail-closed gate: manage-role when auth is on, else same-machine only.

    ``require_auth`` passes every caller when ``auth.enabled=false`` (the default), so a
    router that gates only on it is open to anyone who can reach the port. This gate
    requires a manage role when auth is on, and otherwise admits only direct loopback
    callers from this app's own UI — refusing remote, proxied, DNS-rebound and
    drive-by browser requests. Used by both the secrets API and the management API.
    """
    if _auth_enabled():
        return await require_manage_access(request)

    if _allow_local_admin() and _is_local_ui_request(request):
        return None

    raise HTTPException(
        status_code=401,
        detail=(
            f"{resource} is not available to unauthenticated remote callers. "
            "Enable authentication (auth.enabled=true) and use a manage-role token, "
            "or call it from the local machine's own UI."
        ),
    )


async def require_manage_or_local_access(request: Request) -> Optional[UserInfo]:
    """Gate for the management API (`/api/manage`).

    Same shape as ``require_secrets_access``: on default settings (auth off) an
    unauthenticated remote caller could repoint a tool's service URL or the LLM
    ``api_base`` and have a stored key sent to an attacker host. This admits only
    same-machine UI callers when auth is off, and enforces the manage role when on.
    """
    return await _require_local_or_manage(request, resource="The management API")
