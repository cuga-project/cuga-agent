import ssl
from typing import Any, Optional

import jwt
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
