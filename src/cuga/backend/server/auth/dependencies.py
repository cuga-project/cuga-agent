from typing import Optional

from fastapi import HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from loguru import logger

from cuga.backend.server.auth.models import UserInfo
from cuga.backend.server.auth.jwt_validator import JWTValidator

security = HTTPBearer(auto_error=False)

_validator_cache: dict[str, JWTValidator] = {}


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
    cache_key = jwks_uri
    if cache_key not in _validator_cache:
        jwks_cache_ttl = 3600
        try:
            from cuga.config import settings

            auth = getattr(settings, "auth", None)
            if auth is not None:
                jwks_cache_ttl = getattr(auth, "jwks_cache_ttl", 3600) or 3600
        except Exception:
            pass
        _validator_cache[cache_key] = JWTValidator(
            jwks_uri=jwks_uri,
            cache_ttl=jwks_cache_ttl,
            issuer=issuer,
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

    validator = await _get_validator()
    if not validator:
        raise HTTPException(status_code=503, detail="Auth not configured")

    try:
        payload = validator.validate_and_decode(token)
        return validator.to_user_info(payload)
    except Exception:
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
