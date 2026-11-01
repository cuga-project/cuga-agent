"""Whether the execd sandbox is on, where it is and how to authenticate to it.

The sandbox is on when ``advanced_features.sandbox_mode = "execd"`` or when it is
switched on in the Manage UI. The UI cannot switch off a sandbox the deployment
config requires.

The connection follows from what is configured, not from a separate choice:

* a Kubernetes secret mounted as files in ``advanced_features.sandbox_secret_dir``
  (``url`` and ``api_key``) is used whenever it carries both;
* otherwise the URL and API key come from ``execd_url`` / ``execd_api_key`` in
  ``settings.toml`` or the environment (local development, or ``sandbox_mode =
  "execd"`` without a secret).

The UI can switch the sandbox on only when one of those is there; it never takes
a URL or key itself. Its on/off choice is stored in the DB and loaded with
:func:`set_runtime_config`.

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

from cuga.backend.secrets.secret_resolver import resolve_secret
from cuga.config import settings

# File names accepted inside the mounted secret directory, first match wins.
URL_FILES = ("url", "execd_url", "EXECD_URL", "SANDBOX_URL")
API_KEY_FILES = ("api_key", "apiKey", "execd_api_key", "EXECD_API_KEY", "SANDBOX_API_KEY")


@dataclass(frozen=True)
class SandboxConnection:
    url: str
    api_key: str


_lock = threading.Lock()
# Runtime override loaded from the DB: {"enabled"}. Rows saved by older versions
# may also hold "url" / "api_key_ref", which are ignored.
_runtime: Optional[dict[str, Any]] = None
# (ref, resolved value) for the env API key, so a secret reference is not resolved per request.
_env_key_cache: Optional[tuple[str, str]] = None
# (file signature, connection) for the mounted cluster secret.
_cluster_cache: Optional[tuple[tuple, SandboxConnection]] = None


def _adv(name: str, default: Any = None) -> Any:
    return getattr(settings.advanced_features, name, default)


def env_defaults() -> dict[str, Any]:
    """URL and key as given by settings.toml / env.

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


def available() -> bool:
    """Whether a sandbox connection is configured, so the sandbox can be switched on."""
    return uses_secret() or bool(env_defaults()["url"])


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
    global _env_key_cache, _cluster_cache
    with _lock:
        _env_key_cache = None
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
    # Used only when the secret carries both; otherwise execd_url / execd_api_key apply.
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


def _resolve_env_key(ref: str) -> str:
    """The env API key: a literal value, or a ``vault://`` / ``aws://`` / ``env://`` reference."""
    global _env_key_cache
    if not ref:
        return ""
    if not ref.startswith(("vault://", "aws://", "env://")):
        # A key given directly in the environment; resolve_secret would mistake
        # an all-caps value for an env var name.
        return ref
    with _lock:
        if _env_key_cache is not None and _env_key_cache[0] == ref:
            return _env_key_cache[1]
    value = resolve_secret(ref) or ""
    if not value:
        # Not cached: a vault or env lookup that failed once (not ready yet, a
        # network blip) is retried on the next call instead of until a refresh.
        logger.warning("[execd:conn] Sandbox API key reference could not be resolved")
        return ""
    with _lock:
        _env_key_cache = (ref, value)
    return value


def configured() -> SandboxConnection:
    """The connection the deployment configures, whether or not the sandbox is on."""
    if uses_secret():
        return _read_cluster()
    env = env_defaults()
    return SandboxConnection(url=env["url"], api_key=_resolve_env_key(env["api_key_ref"]))


def resolve() -> Optional[SandboxConnection]:
    """Current connection, or ``None`` when the sandbox is off."""
    if not is_enabled():
        return None
    return configured()


def problem() -> Optional[str]:
    """What stops the sandbox from working, from configuration alone; ``None`` when nothing does."""
    try:
        conn = configured()
    except Exception as e:
        return str(e)
    if not conn.url:
        return "Sandbox URL is not configured"
    from . import tool_callback

    if not tool_callback.callback_url():
        # Every tool the code calls comes back to CUGA at this address.
        return "advanced_features.sandbox_callback_url is not set, so code in the sandbox cannot call tools"
    return None


def status() -> dict[str, Any]:
    """Description of the current configuration for the UI. Never includes the key itself."""
    secret = secret_status()
    enabled = is_enabled()
    result: dict[str, Any] = {
        "enabled": enabled,
        "required_by_config": required_by_config(),
        # The Manage UI shows the sandbox only when it can be switched on.
        "available": available() or enabled,
        "connection": "secret" if secret["available"] else "settings",
        "error": problem() if enabled else None,
    }
    if enabled and not result["error"]:
        result["effective_url"] = configured().url
    return result
