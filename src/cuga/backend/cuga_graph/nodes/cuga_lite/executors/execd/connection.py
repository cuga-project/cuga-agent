"""Whether the execd sandbox is on, where it is and how to authenticate to it.

The sandbox is on when ``advanced_features.sandbox_mode = "execd"`` or when it is
switched on in the Manage UI. The UI cannot switch off a sandbox the deployment
config requires.

The connection follows from what is configured, not from a separate choice:

* a Kubernetes secret mounted as files in ``advanced_features.sandbox_secret_dir``
  (``url`` and ``api_key``) is used whenever it carries both;
* otherwise the URL and API key come from ``execd_url`` / ``execd_api_key`` or
  from the UI. A key entered in the UI is kept in the encrypted secrets store
  and only its ``db://`` reference is persisted.

The UI choice is stored in the DB and loaded with :func:`set_runtime_config`; it
takes precedence over ``settings.toml`` / ``DYNACONF_ADVANCED_FEATURES__*`` until cleared.

Nothing here touches the Kubernetes API. The mounted files are re-read only when
their mtime changes, so a rotated secret is picked up without a restart and
without reading the files on every request.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from loguru import logger

from cuga.backend.secrets.secret_resolver import parse_ref, resolve_secret
from cuga.config import settings

# File names accepted inside the mounted secret directory, first match wins.
URL_FILES = ("url", "execd_url", "EXECD_URL", "SANDBOX_URL")
API_KEY_FILES = ("api_key", "apiKey", "execd_api_key", "EXECD_API_KEY", "SANDBOX_API_KEY")


@dataclass(frozen=True)
class SandboxConnection:
    url: str
    api_key: str


_lock = threading.Lock()
# Runtime override loaded from the DB: {"enabled", "url", "api_key_ref"}.
_runtime: Optional[dict[str, Any]] = None
# (ref, resolved value) for the manual API key, so the secrets store is not hit per request.
_manual_key_cache: Optional[tuple[str, str]] = None
# (file signature, connection) for the mounted cluster secret.
_cluster_cache: Optional[tuple[tuple, SandboxConnection]] = None


def _adv(name: str, default: Any = None) -> Any:
    return getattr(settings.advanced_features, name, default)


def env_defaults() -> dict[str, Any]:
    """URL and key as given by settings.toml / env, before any UI override.

    ``sandbox_url`` / ``sandbox_api_key`` are what the sandbox-binding secret
    sets when injected with ``envFrom`` (``DYNACONF_ADVANCED_FEATURES__SANDBOX_*``).
    """
    return {
        "url": str(_adv("execd_url", "") or _adv("sandbox_url", "") or ""),
        "api_key_ref": str(_adv("execd_api_key", "") or _adv("sandbox_api_key", "") or ""),
    }


def set_runtime_config(config: Optional[dict[str, Any]]) -> None:
    """Install (or clear, with ``None``) the override stored in the DB."""
    global _runtime
    config = dict(config) if config else None
    if config is not None and "enabled" not in config and "source" in config:
        # Rows saved before the source choice was dropped.
        config["enabled"] = config.pop("source") != "off"
    with _lock:
        _runtime = config
    refresh()


def get_runtime_config() -> Optional[dict[str, Any]]:
    with _lock:
        return dict(_runtime) if _runtime else None


def required_by_config() -> bool:
    """The deployment asks for execd with ``sandbox_mode = "execd"``; the UI cannot turn it off."""
    return str(_adv("sandbox_mode", "") or "").strip().lower() == "execd"


def is_enabled() -> bool:
    if required_by_config():
        return True
    return bool((get_runtime_config() or {}).get("enabled"))


def manual_config() -> dict[str, Any]:
    """URL and key reference used when no secret is mounted: the UI's, else the environment's."""
    runtime = get_runtime_config() or {}
    if "url" in runtime:
        return {"url": runtime.get("url") or "", "api_key_ref": runtime.get("api_key_ref") or ""}
    return env_defaults()


def effective_sandbox_mode(configured: str) -> str:
    """``advanced_features.sandbox_mode`` as the rest of CUGA should see it.

    Turning the execd sandbox on in the UI moves shell, filesystem tools and the
    workspace API there too, whatever ``sandbox_mode`` says.
    """
    return "execd" if is_enabled() else configured


def execd_active() -> bool:
    """True when generated code should run in execd: UI toggle on, or ``sandbox_mode = "execd"``."""
    return is_enabled()


def refresh() -> None:
    """Drop cached secrets so the next lookup re-reads them."""
    global _manual_key_cache, _cluster_cache
    with _lock:
        _manual_key_cache = None
        _cluster_cache = None


def secret_dir() -> Optional[Path]:
    """Directory holding the mounted sandbox secret, if the deployment configured one."""
    explicit = str(_adv("sandbox_secret_dir", "") or "").strip()
    return Path(explicit) if explicit else None


def secret_status() -> dict[str, Any]:
    directory = secret_dir()
    info: dict[str, Any] = {"dir": str(directory) if directory else None}
    if directory is not None:
        info["found"] = directory.is_dir()
        info["has_url"] = _find_file(directory, URL_FILES) is not None
        info["has_api_key"] = _find_file(directory, API_KEY_FILES) is not None
    # Used only when the secret carries both; otherwise the URL and key are entered by hand.
    info["available"] = bool(info.get("found") and info.get("has_url") and info.get("has_api_key"))
    return info


def uses_secret() -> bool:
    return secret_status()["available"]


def _find_file(directory: Path, names: tuple[str, ...]) -> Optional[Path]:
    for name in names:
        candidate = directory / name
        if candidate.is_file():
            return candidate
    return None


def _read_cluster() -> SandboxConnection:
    global _cluster_cache
    directory = secret_dir()
    if directory is None:
        raise RuntimeError(
            "No sandbox secret is configured; set DYNACONF_ADVANCED_FEATURES__SANDBOX_SECRET_DIR"
        )
    url_file = _find_file(directory, URL_FILES)
    key_file = _find_file(directory, API_KEY_FILES)
    if url_file is None and key_file is None:
        raise RuntimeError(f"Sandbox secret not found or empty at {directory}")

    # Kubelet rotates mounted secrets by swapping a symlink, which changes the
    # target's mtime; stat() follows the link, so this catches rotation.
    signature = tuple((str(f), f.stat().st_mtime_ns) for f in (url_file, key_file) if f is not None)
    with _lock:
        if _cluster_cache is not None and _cluster_cache[0] == signature:
            return _cluster_cache[1]

    url = url_file.read_text().strip() if url_file else ""
    key = key_file.read_text().strip() if key_file else ""
    # A cluster-internal Service may not be part of the secret; fall back to the env URL.
    conn = SandboxConnection(url=url or env_defaults()["url"], api_key=key)
    with _lock:
        _cluster_cache = (signature, conn)
    logger.info(f"[execd:conn] Loaded sandbox secret from {directory} url={conn.url} api_key_set={bool(key)}")
    return conn


def _resolve_manual_key(ref: str) -> str:
    global _manual_key_cache
    if not ref:
        return ""
    if not ref.startswith(("db://", "vault://", "aws://", "env://")):
        # A key given directly in the environment; resolve_secret would mistake
        # an all-caps value for an env var name.
        return ref
    with _lock:
        if _manual_key_cache is not None and _manual_key_cache[0] == ref:
            return _manual_key_cache[1]
    if ref.startswith("db://"):
        # Never read the DB synchronously: get_secret_sync runs the query on a new
        # event loop in a worker thread, which fails against (and can poison) the
        # asyncpg pool owned by the server's loop. load_manual_key() fills the cache.
        logger.warning(
            "[execd:conn] Sandbox API key {} is not loaded; requests go out without X-API-Key. "
            "Use Manage → Code sandbox → Test again, or re-enter the key.",
            ref,
        )
        value = ""
    else:
        value = resolve_secret(ref) or ""
        if not value:
            logger.warning("[execd:conn] Sandbox API key reference could not be resolved")
    with _lock:
        _manual_key_cache = (ref, value)
    return value


def prime_manual_key(ref: str, value: str) -> None:
    """Cache the value of the manual key reference, so requests need no secrets lookup."""
    global _manual_key_cache
    with _lock:
        _manual_key_cache = (ref, value)


async def load_manual_key() -> None:
    """Read a ``db://`` manual key from the secrets store into the cache.

    Called from async code (startup, PUT, refresh) because the DB can only be
    read safely on the server's own event loop.
    """
    ref = manual_config()["api_key_ref"]
    if not ref.startswith("db://"):
        return
    from cuga.backend.storage import secrets_store

    _, secret_id = parse_ref(ref)
    value = await secrets_store.get_secret(secret_id) or ""
    if not value:
        logger.warning(
            f"[execd:conn] Sandbox API key {ref} not found in the secrets store (is CUGA_SECRET_KEY set?)"
        )
    prime_manual_key(ref, value)


def resolve() -> Optional[SandboxConnection]:
    """Current connection, or ``None`` when the sandbox is off."""
    if not is_enabled():
        return None
    if uses_secret():
        return _read_cluster()
    manual = manual_config()
    return SandboxConnection(url=manual["url"], api_key=_resolve_manual_key(manual["api_key_ref"]))


def status() -> dict[str, Any]:
    """Description of the current configuration for the UI. Never includes the key itself."""
    secret = secret_status()
    manual = manual_config()
    enabled = is_enabled()
    result: dict[str, Any] = {
        "enabled": enabled,
        "required_by_config": required_by_config(),
        "overridden": get_runtime_config() is not None,
        "connection": "secret" if secret["available"] else "manual",
        "secret": secret,
        # Reported while the secret is in use too, so the UI can prefill the fields if it goes away.
        "manual": {"url": manual["url"], "api_key_set": bool(manual["api_key_ref"])},
        "error": None,
    }
    if enabled:
        try:
            conn = resolve()
            result["effective_url"] = conn.url if conn else None
        except Exception as e:
            result["error"] = str(e)
    return result
