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
    try:
        from cuga.config import settings

        auth = getattr(settings, "auth", None)
        return bool(auth and getattr(auth, "authorization_enabled", False))
    except Exception as e:
        logger.warning(
            "authorization config check failed (fail-open): {}",
            e,
            exc_info=True,
        )
        return False


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

    # Check if user has any of the required manage roles
    manage_roles = _get_manage_roles()
    user_roles = user.roles or []

    if not any(role in manage_roles for role in user_roles):
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
