"""HTTPS normalization helpers for OIDC issuer and discovery URLs."""

from __future__ import annotations

from typing import Optional
from urllib.parse import urlparse, urlunparse

from loguru import logger


def normalize_https_issuer_url(value: str) -> Optional[str]:
    """Parse and normalize an https issuer URL; reject non-https and unsafe parts."""
    if not value or not isinstance(value, str):
        return None
    parsed = urlparse(value.strip())
    if parsed.scheme.lower() != "https":
        return None
    if not parsed.hostname:
        return None
    if parsed.params or parsed.query or parsed.fragment:
        logger.debug("issuer URL rejected: contains params, query, or fragment")
        return None
    host = parsed.hostname.lower()
    port = parsed.port
    netloc = f"{host}:{port}" if port is not None and port != 443 else host
    path = parsed.path.rstrip("/")
    return urlunparse(("https", netloc, path, "", "", ""))


def discovery_url_to_issuer_base(discovery_url: str) -> Optional[str]:
    """Map OIDC discovery document URL to the issuer prefix (path before /.well-known/...)."""
    parsed = urlparse(discovery_url.strip())
    if parsed.scheme.lower() != "https" or not parsed.hostname:
        return None
    if parsed.params or parsed.query or parsed.fragment:
        logger.debug("discovery URL rejected: params, query, or fragment")
        return None
    host = parsed.hostname.lower()
    port = parsed.port
    netloc = f"{host}:{port}" if port is not None and port != 443 else host
    path = parsed.path.rstrip("/")
    suffix = "/.well-known/openid-configuration"
    if path.lower().endswith(suffix):
        path = path[: -len(suffix)].rstrip("/")
    return normalize_https_issuer_url(urlunparse(("https", netloc, path, "", "", "")))


def normalize_issuer_for_discovery(issuer_raw: str) -> Optional[str]:
    """
    Normalize and validate an issuer URL for JWKS discovery.
    Only enforces that the issuer uses https and has a valid URL shape.
    Returns the normalized URL, or None (with a warning) if invalid.
    """
    normalized = normalize_https_issuer_url(issuer_raw)
    if not normalized:
        logger.warning("rejected token issuer (non-https or invalid URL): {!r}", issuer_raw[:120])
    return normalized


def normalize_discovery_url(discovery_url: str) -> Optional[str]:
    """
    Validate that a configured OIDC discovery URL uses https and has a valid shape.
    Returns the normalized URL, or None (with a warning) if invalid.
    """
    if not discovery_url or not isinstance(discovery_url, str):
        return None
    parsed = urlparse(discovery_url.strip())
    if parsed.scheme.lower() != "https" or not parsed.hostname:
        logger.warning("rejected OIDC discovery URL (non-https or invalid): {!r}", discovery_url[:120])
        return None
    return discovery_url.strip()
