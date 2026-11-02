"""Configuration fingerprint for durable session continuity (#774).

A fingerprint is computed from the agent's published config and the CUGA
runtime version. It replaces the plain ``published_config_version`` integer
that PR 1 stamped into every checkpoint.

Hash stability rules:
- ``sort_keys=True``, compact separators (no spaces)
- Secret *values* are dropped (they must not leak into checkpoints)
- Volatile keys (``updated_at``, ``published_at``, ``version``) are dropped
- The hash is truncated to 12 hex chars for event payloads (never the full hash)
"""

from __future__ import annotations

import enum
import hashlib
import json
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version
from typing import Any, Mapping, Optional

# Keys whose values change without a meaningful config change. ``knowledge_state`` is
# export/import metadata that publish snapshots into the stored config but strips before
# it applies the config at runtime, so the stored and the applied config must hash alike.
_VOLATILE_KEYS = frozenset({"updated_at", "published_at", "version", "created_at", "knowledge_state"})

# Keys whose *plaintext* values are secrets and must not reach the hash digest.
# A value in this set is dropped unless it looks like a reference (``vault://``,
# ``env://``, ``secret://`` …) — references *are* meaningful config and must be hashed
# so that rotating the reference (e.g. pointing ``api_key`` at a different Vault path)
# invalidates pending approvals.  ``embedding_api_key`` is blanked in the stored
# published snapshot but present in the config applied at publish time.
_SECRET_VALUE_KEYS = frozenset({"api_key", "token", "password", "secret", "key_env_var", "embedding_api_key"})

# URI schemes that indicate a secret *reference* rather than a bare credential.
# Matching is prefix-based and case-insensitive. ``vault://``, ``db://``, ``aws://`` and
# ``env://`` are the schemes the secret resolver and the Manage redactor
# (``manage_routes.helpers.is_secret_ref``) recognise; keep them in sync.
_REFERENCE_PREFIXES = ("vault://", "db://", "aws://", "env://", "secret://", "arn:", "sm://")


def _is_reference(value: Any) -> bool:
    """Return True when *value* looks like an opaque secret reference, not a bare credential."""
    if not isinstance(value, str):
        return False
    lower = value.lower()
    return any(lower.startswith(p) for p in _REFERENCE_PREFIXES)


def _scrub(obj: Any) -> Any:
    """Return a copy of *obj* with volatile keys and bare secret values removed.

    Secret-named keys whose value is a reference URI are preserved so that changing
    which secret is referenced is reflected in the hash.
    """
    if isinstance(obj, dict):
        result = {}
        for k, v in obj.items():
            if k in _VOLATILE_KEYS:
                continue
            if k in _SECRET_VALUE_KEYS and not _is_reference(v):
                # Bare credential (or empty) — drop entirely so it never leaks.
                continue
            result[k] = _scrub(v)
        return result
    if isinstance(obj, list):
        return [_scrub(item) for item in obj]
    return obj


def agent_config_hash(config: dict) -> str:
    """SHA-256 of the canonical JSON of *config* after removing volatile and secret value keys.

    Returns the full 64-char hex digest.
    """
    scrubbed = _scrub(config)
    canonical = json.dumps(scrubbed, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def runtime_version() -> str:
    """Return the installed CUGA package version (e.g. ``"0.3.1"``)."""
    try:
        return version("cuga")
    except PackageNotFoundError:
        return "unknown"


@dataclass(frozen=True)
class ConfigFingerprint:
    agent_hash: str  # full SHA-256 hex digest
    runtime_ver: str  # importlib.metadata version string

    def to_scope_dict(self) -> dict:
        """Serialisable form stored in ``service_scope["config_fingerprint"]``."""
        return {"agent_hash": self.agent_hash, "runtime_ver": self.runtime_ver}

    def short_hash(self) -> str:
        """12-char prefix for event payloads (opaque to clients)."""
        return self.agent_hash[:12]

    @classmethod
    def from_scope_dict(cls, d: Mapping[str, Any]) -> "ConfigFingerprint":
        return cls(agent_hash=d.get("agent_hash", ""), runtime_ver=d.get("runtime_ver", ""))


def compute_fingerprint(config: dict) -> ConfigFingerprint:
    """Compute a fingerprint from a published agent config dict."""
    return ConfigFingerprint(
        agent_hash=agent_config_hash(config),
        runtime_ver=runtime_version(),
    )


class ConfigDecision(enum.Enum):
    """What a stream request must do about a checkpoint written under another configuration."""

    NONE = "none"
    NOTICE = "notice"  # completed turn, the agent changed: tell the client, then run
    CANCEL_PENDING = "cancel_pending"  # paused turn, the agent or runtime changed: cancel it


def classify_config_change(
    scope: Mapping[str, Any],
    current: Optional[ConfigFingerprint],
    pending: bool,
    expected_version: Optional[str] = None,
) -> ConfigDecision:
    """Apply the decision table to a checkpoint's ``service_scope`` (#774).

    | turn      | agent changed | runtime only changed |
    |-----------|---------------|----------------------|
    | completed | NOTICE        | NONE (silent)        |
    | pending   | CANCEL_PENDING| CANCEL_PENDING       |

    A checkpoint that predates fingerprints (or a graph without one) is compared on
    ``published_config_version`` instead. Only a paused turn needs a decision there: it counts as
    an agent change when that version differs from *expected_version*.
    """
    stamped = scope.get("config_fingerprint")
    if current is not None and isinstance(stamped, Mapping) and stamped.get("agent_hash"):
        previous = ConfigFingerprint.from_scope_dict(stamped)
        agent_changed = previous.agent_hash != current.agent_hash
        runtime_changed = bool(previous.runtime_ver) and previous.runtime_ver != current.runtime_ver
    elif pending and expected_version is not None:
        agent_changed = scope.get("published_config_version") != expected_version
        runtime_changed = False
    else:
        return ConfigDecision.NONE

    if pending and (agent_changed or runtime_changed):
        return ConfigDecision.CANCEL_PENDING
    if agent_changed:
        return ConfigDecision.NOTICE
    return ConfigDecision.NONE
