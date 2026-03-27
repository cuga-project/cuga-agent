import base64
import hashlib
import os
import secrets
import time
from typing import Any, Optional
from urllib.parse import quote, urlencode

import httpx
from loguru import logger

from cuga.backend.server.auth.models import TokenResponse, UserInfo
from cuga.backend.server.auth.jwt_validator import JWTValidator


def _pkce_pair() -> tuple[str, str]:
    """Return (code_verifier, code_challenge) using S256 method."""
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return verifier, challenge


class OIDCClient:
    def __init__(
        self,
        client_id: str,
        client_secret: str,
        discovery_url: str,
        redirect_uri: str,
        jwks_cache_ttl: int = 3600,
        skip_verify: bool = False,
        ca_bundle: Optional[str] = None,
        iam_proxy_url: str = "",
        iam_proxy_skip_verify: bool = False,
        iam_proxy_ca_bundle: Optional[str] = None,
    ):
        self.client_id = client_id
        self.client_secret = client_secret
        self.discovery_url = discovery_url.rstrip("/")
        self.redirect_uri = redirect_uri
        self.jwks_cache_ttl = jwks_cache_ttl
        self._skip_verify = skip_verify
        self._ca_bundle = ca_bundle
        self._ssl: bool | str = False if skip_verify else (ca_bundle or True)
        self.iam_proxy_url = iam_proxy_url.rstrip("/")
        self._iam_proxy_ssl: bool | str = False if iam_proxy_skip_verify else (iam_proxy_ca_bundle or True)
        self._discovery: Optional[dict[str, Any]] = None
        self._validator: Optional[JWTValidator] = None
        self._pkce_ttl = 300
        self._pkce_verifiers: dict[str, tuple[str, float]] = {}

    async def get_discovery(self) -> dict[str, Any]:
        if self._discovery is not None:
            return self._discovery
        from cuga.backend.server.auth.issuer_allowlist import normalize_discovery_url

        if normalize_discovery_url(self.discovery_url) is None:
            raise ValueError("OIDC discovery URL must be a valid https URL; fix OIDC_DISCOVERY_URL")
        async with httpx.AsyncClient(verify=self._ssl) as client:
            resp = await client.get(self.discovery_url)
            resp.raise_for_status()
            self._discovery = resp.json()
        return self._discovery

    def _get_validator(self, jwks_uri: str, issuer: Optional[str]) -> JWTValidator:
        if self._validator is None:
            self._validator = JWTValidator(
                jwks_uri=jwks_uri,
                cache_ttl=self.jwks_cache_ttl,
                issuer=issuer,
                skip_verify=self._skip_verify,
                ca_bundle=self._ca_bundle,
            )
        return self._validator

    async def get_authorization_url(self, state: Optional[str] = None) -> tuple[str, str]:
        discovery = await self.get_discovery()
        auth_endpoint = discovery["authorization_endpoint"]
        state = state or secrets.token_urlsafe(32)
        verifier, challenge = _pkce_pair()
        expiry = time.time() + self._pkce_ttl
        self._pkce_verifiers[state] = (verifier, expiry)
        params = {
            "response_type": "code",
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
            "scope": "openid profile email",
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
        url = f"{auth_endpoint}?{urlencode(params)}"
        return url, state

    def _prune_expired_pkce_verifiers(self) -> None:
        now = time.time()
        expired = [s for s, (_, exp) in self._pkce_verifiers.items() if now > exp]
        for s in expired:
            del self._pkce_verifiers[s]

    async def exchange_code(self, code: str, state: str) -> tuple[TokenResponse, UserInfo]:
        discovery = await self.get_discovery()
        token_endpoint = discovery["token_endpoint"]
        jwks_uri = discovery.get("jwks_uri", "")
        issuer = discovery.get("issuer")

        self._prune_expired_pkce_verifiers()
        entry = self._pkce_verifiers.pop(state, None)
        if entry is None:
            logger.warning(
                "exchange_code: PKCE verifier missing or expired for state={} (state not in self._pkce_verifiers); rejecting callback",
                state[:8] + "…" if len(state) > 8 else state,
            )
            raise ValueError(
                "PKCE verifier missing or expired for this authorization flow; state may be invalid, reused, or expired"
            )
        code_verifier, expiry = entry
        if time.time() > expiry:
            logger.warning(
                "exchange_code: PKCE verifier expired for state={}; rejecting callback",
                state[:8] + "…" if len(state) > 8 else state,
            )
            raise ValueError(
                "PKCE verifier missing or expired for this authorization flow; state may be invalid, reused, or expired"
            )

        token_data: dict[str, str] = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": self.redirect_uri,
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "code_verifier": code_verifier,
        }

        async with httpx.AsyncClient(verify=self._ssl) as client:
            resp = await client.post(
                token_endpoint,
                data=token_data,
                headers={"Accept": "application/json", "Content-Type": "application/x-www-form-urlencoded"},
            )
            if not resp.is_success:
                logger.error(
                    "Token exchange failed: {} {} — body: {}",
                    resp.status_code,
                    resp.reason_phrase,
                    resp.text,
                )
            resp.raise_for_status()
            data = resp.json()

        access_token = data.get("access_token") or ""
        id_token = data.get("id_token")
        token_type = data.get("token_type", "Bearer")
        expires_in = data.get("expires_in")
        refresh_token = data.get("refresh_token")

        token_response = TokenResponse(
            access_token=access_token,
            id_token=id_token,
            token_type=token_type,
            expires_in=expires_in,
            refresh_token=refresh_token,
        )

        if not id_token:
            raise ValueError("OIDC provider did not return id_token")

        validator = self._get_validator(jwks_uri, issuer)
        payload = validator.validate_and_decode(id_token, audience=self.client_id)
        user_info = validator.to_user_info(payload)

        return token_response, user_info

    async def exchange_service_token(self, access_token: str, instance_id: str) -> str:
        if not self.iam_proxy_url:
            return access_token
        if not access_token:
            raise ValueError("Missing access token for IAM proxy exchange")
        if not instance_id:
            raise ValueError("Missing service instance ID for IAM proxy exchange")

        encoded_instance = quote(instance_id, safe="")
        exchange_url = f"{self.iam_proxy_url}/api/2.0/services/{encoded_instance}/token"
        payload = {"jwt": access_token}
        async with httpx.AsyncClient(verify=self._iam_proxy_ssl) as client:
            resp = await client.post(
                exchange_url,
                headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json"},
                json=payload,
            )
            if resp.status_code == 415:
                # Some IAM proxy deployments require form-encoded posts for token endpoints.
                resp = await client.post(
                    exchange_url,
                    headers={
                        "Authorization": f"Bearer {access_token}",
                        "Accept": "application/json",
                        "Content-Type": "application/x-www-form-urlencoded",
                    },
                    data=payload,
                )
            if not resp.is_success:
                logger.error(
                    "IAM proxy token exchange failed: {} {} — body: {}",
                    resp.status_code,
                    resp.reason_phrase,
                    resp.text,
                )
            resp.raise_for_status()
            data = resp.json()

        if isinstance(data, dict):
            token = data.get("access_token") or data.get("token")
            if isinstance(token, str) and token:
                return token
        raise ValueError("IAM proxy did not return access_token/token in response")


_oidc_client_instance: Optional[OIDCClient] = None


def get_oidc_client() -> Optional[OIDCClient]:
    global _oidc_client_instance
    if _oidc_client_instance is not None:
        return _oidc_client_instance

    client_id = os.getenv("OIDC_CLIENT_ID")
    client_secret = os.getenv("OIDC_CLIENT_SECRET")
    discovery_url = os.getenv("OIDC_DISCOVERY_URL")
    redirect_uri = os.getenv("OIDC_REDIRECT_URI")
    if not all((client_id, client_secret, discovery_url, redirect_uri)):
        logger.debug("OIDC client not configured: missing env vars")
        return None
    jwks_cache_ttl = 3600
    skip_verify = False
    ca_bundle: Optional[str] = None
    iam_proxy_url = ""
    iam_proxy_skip_verify = False
    iam_proxy_ca_bundle: Optional[str] = None
    try:
        from cuga.config import settings

        auth = getattr(settings, "auth", None)
        if auth is not None:
            jwks_cache_ttl = getattr(auth, "jwks_cache_ttl", 3600) or 3600
            skip_verify = bool(getattr(auth, "oidc_skip_verify", False))
            ca_bundle = getattr(auth, "oidc_ca_bundle", None) or None
            iam_proxy_url = getattr(auth, "iam_proxy_url", "") or ""
            iam_proxy_skip_verify = bool(getattr(auth, "iam_proxy_skip_verify", False))
            iam_proxy_ca_bundle = getattr(auth, "iam_proxy_ca_bundle", None) or None
    except Exception:
        pass
    if skip_verify:
        logger.warning(
            "OIDC SSL verification is disabled (DYNACONF_AUTH__OIDC_SKIP_VERIFY=true) — do not use in production"
        )
    _oidc_client_instance = OIDCClient(
        client_id=client_id,
        client_secret=client_secret,
        discovery_url=discovery_url,
        redirect_uri=redirect_uri,
        jwks_cache_ttl=jwks_cache_ttl,
        skip_verify=skip_verify,
        ca_bundle=ca_bundle,
        iam_proxy_url=iam_proxy_url,
        iam_proxy_skip_verify=iam_proxy_skip_verify,
        iam_proxy_ca_bundle=iam_proxy_ca_bundle,
    )
    return _oidc_client_instance
