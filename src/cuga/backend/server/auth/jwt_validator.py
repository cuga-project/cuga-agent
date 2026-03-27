import ssl
from typing import Any, Optional
from urllib.parse import urlparse, urlunparse

import jwt
import httpx
from jwt import PyJWKClient
from loguru import logger

from cuga.backend.server.auth.models import UserInfo


class JWTValidator:
    def __init__(
        self,
        jwks_uri: str,
        cache_ttl: int = 3600,
        issuer: Optional[str] = None,
        *,
        skip_verify: bool = False,
        ca_bundle: Optional[str] = None,
    ):
        self.jwks_uri = jwks_uri
        self.issuer = issuer
        ssl_context = None
        if skip_verify:
            ssl_context = ssl._create_unverified_context()
        elif ca_bundle:
            ssl_context = ssl.create_default_context(cafile=ca_bundle)
        self._client = PyJWKClient(jwks_uri, cache_keys=True, lifespan=cache_ttl, ssl_context=ssl_context)

    def validate_and_decode(
        self,
        token: str,
        *,
        audience: Optional[str] = None,
        algorithms: Optional[list[str]] = None,
    ) -> dict[str, Any]:
        algorithms = algorithms or ["RS256", "ES256"]
        try:
            signing_key = self._client.get_signing_key_from_jwt(token)
            payload = jwt.decode(
                token,
                signing_key.key,
                algorithms=algorithms,
                issuer=self.issuer if self.issuer else None,
                audience=audience,
                options={
                    "verify_exp": True,
                    "verify_iss": bool(self.issuer),
                    "verify_aud": audience is not None,
                },
            )
            return payload
        except jwt.PyJWTError as e:
            logger.debug(f"JWT validation failed: {e}")
            raise

    @staticmethod
    def _extract_roles(payload: dict[str, Any]) -> Optional[list[str]]:
        roles_claim = payload.get("roles")
        if isinstance(roles_claim, list):
            return roles_claim
        if isinstance(roles_claim, dict):
            # IAM tokens carry roles as {"SERVICE": ["ServiceUser"], ...} — flatten all values.
            flat: list[str] = []
            for v in roles_claim.values():
                if isinstance(v, list):
                    flat.extend(str(r) for r in v)
            return flat or None
        realm_access = payload.get("realm_access")
        if isinstance(realm_access, dict):
            roles = realm_access.get("roles")
            if isinstance(roles, list):
                return roles
        return None

    @staticmethod
    def _normalize_https_url(value: str) -> Optional[str]:
        try:
            p = urlparse(value.strip())
            if p.scheme.lower() != "https" or not p.hostname:
                return None
            host = p.hostname.lower()
            netloc = f"{host}:{p.port}" if p.port and p.port != 443 else host
            path = p.path.rstrip("/")
            return urlunparse(("https", netloc, path, "", "", ""))
        except Exception:
            return None

    def to_user_info(self, payload: dict[str, Any]) -> UserInfo:
        sub = payload.get("sub") or ""
        if not sub:
            raise ValueError("JWT payload missing 'sub' claim")
        return UserInfo(
            sub=sub,
            email=payload.get("email") or payload.get("preferred_username"),
            name=payload.get("name") or payload.get("preferred_username"),
            roles=self._extract_roles(payload),
            raw_claims={
                k: v for k, v in payload.items() if k not in ("sub", "email", "name", "roles", "exp", "iat")
            },
        )


async def validate_iam_token(
    token: str,
    instance_id: str,
    *,
    skip_verify: bool = False,
    ca_bundle: Optional[str] = None,
) -> dict[str, Any]:
    """
    Validate an IAM-issued authorization token returned by exchange_service_token.

    Steps (per IBM IAM token contract):
      1. Decode iss (unverified) and resolve JWKS via OIDC discovery.
      2. Validate signature, expiration, and issuer (verify_exp + verify_iss).
      3. Confirm the token is bound to this instance by checking that instance_id
         appears in the ``aud`` list, or in the ``crn`` / ``account`` / ``sub``
         claims — whichever the IAM proxy populates.

    Raises ValueError with a descriptive message on any failure.
    Returns the decoded payload on success.
    """
    from cuga.backend.server.auth.issuer_allowlist import normalize_issuer_for_discovery

    try:
        unverified = jwt.decode(token, options={"verify_signature": False})
    except jwt.PyJWTError as exc:
        raise ValueError(f"IAM token is not a valid JWT: {exc}") from exc

    raw_iss = unverified.get("iss") or ""
    if not raw_iss:
        raise ValueError("IAM token missing 'iss' claim")

    normalized_iss = normalize_issuer_for_discovery(raw_iss)
    if not normalized_iss:
        raise ValueError(f"IAM token issuer must be a valid https URL: {raw_iss!r}")

    ssl_ctx: Optional[ssl.SSLContext] = None
    ssl_arg: bool | str
    if skip_verify:
        ssl_ctx = ssl._create_unverified_context()
        ssl_arg = False
    else:
        ssl_arg = ca_bundle or True
        if ca_bundle:
            ssl_ctx = ssl.create_default_context(cafile=ca_bundle)

    discovery_url = normalized_iss.rstrip("/") + "/.well-known/openid-configuration"
    try:
        async with httpx.AsyncClient(verify=ssl_arg) as http:
            resp = await http.get(discovery_url, follow_redirects=True)
            resp.raise_for_status()
            discovery = resp.json()
    except Exception as exc:
        raise ValueError(f"IAM token OIDC discovery failed for issuer {normalized_iss!r}: {exc}") from exc

    jwks_uri = discovery.get("jwks_uri") or ""
    if not jwks_uri:
        raise ValueError(f"IAM token OIDC discovery returned no jwks_uri for issuer {normalized_iss!r}")

    try:
        jwks_client = PyJWKClient(jwks_uri, cache_keys=True, lifespan=3600, ssl_context=ssl_ctx)
        signing_key = jwks_client.get_signing_key_from_jwt(token)
        payload = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256", "ES256"],
            issuer=normalized_iss,
            options={
                "verify_exp": True,
                "verify_iss": True,
                "verify_aud": False,
            },
        )
    except jwt.ExpiredSignatureError as exc:
        raise ValueError("IAM token has expired") from exc
    except jwt.InvalidIssuerError as exc:
        raise ValueError(f"IAM token issuer mismatch: {exc}") from exc
    except jwt.PyJWTError as exc:
        raise ValueError(f"IAM token signature/claim validation failed: {exc}") from exc

    _assert_iam_token_bound_to_instance(payload, instance_id)
    return payload


def _assert_iam_token_bound_to_instance(payload: dict[str, Any], instance_id: str) -> None:
    """
    Raise ValueError when the IAM token cannot be confirmed to be scoped to instance_id.

    IBM IAM tokens may express the instance binding in several ways:
      • ``aud`` list containing the instance_id or a CRN that includes it
      • ``crn`` string claim containing the instance_id
      • ``account`` dict / ``account_id`` string containing the instance_id
      • ``sub`` equal to the instance_id (service-credential tokens)
    """
    norm_id = instance_id.lower()

    aud = payload.get("aud")
    if isinstance(aud, str):
        aud = [aud]
    if isinstance(aud, list):
        for entry in aud:
            if isinstance(entry, str) and norm_id in entry.lower():
                return

    for claim in ("crn", "account_id"):
        val = payload.get(claim)
        if isinstance(val, str) and norm_id in val.lower():
            return

    account = payload.get("account")
    if isinstance(account, dict):
        acct_id = account.get("bss") or account.get("id") or ""
        if isinstance(acct_id, str) and norm_id in acct_id.lower():
            return

    sub = payload.get("sub") or ""
    if isinstance(sub, str) and norm_id in sub.lower():
        return

    raise ValueError(
        f"IAM token is not bound to instance {instance_id!r}: "
        "instance_id not found in aud, crn, account_id, or sub claims"
    )
