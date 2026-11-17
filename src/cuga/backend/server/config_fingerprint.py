"""Configuration fingerprint for durable session continuity (#774).

A fingerprint is computed from the agent's published config and the CUGA
runtime version. It replaces the plain ``published_config_version`` integer
that PR 1 stamped into every checkpoint.

Hash stability rules:
- ``sort_keys=True``, compact separators (no spaces)
- Secret *values* are dropped (they must not leak into checkpoints)
- Metadata is dropped only at known configuration paths
- The hash is truncated to 12 hex chars for event payloads (never the full hash)
"""

from __future__ import annotations

import enum
import hashlib
import json
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version
from typing import Any, Mapping, Optional

# Metadata is excluded only at its known location, never by key name at every depth.
_ROOT_METADATA_KEYS = frozenset({"updated_at", "published_at", "version", "created_at", "knowledge_state"})
_KNOWLEDGE_METADATA_KEYS = frozenset({"_vector_config_hash", "_adaptation_hash", "_glossary_hash"})


def normalized_config_view(config: dict) -> dict:
    """Return the common fingerprint input for stored and applied configurations.

    Knowledge credentials are blanked in published snapshots, including references,
    so they cannot participate in comparisons with the applied configuration.
    Other secret references remain meaningful configuration. Real knowledge settings
    (model, chunks, adaptation text) remain in the fingerprint.
    """
    from cuga.backend.server.manage_routes.helpers import is_secret_field_name, is_secret_ref

    def normalize(obj: Any, path: tuple[str, ...]) -> Any:
        if isinstance(obj, dict):
            result = {}
            for key, value in obj.items():
                if not path and key in _ROOT_METADATA_KEYS:
                    continue
                if path == ("knowledge",) and (key in _KNOWLEDGE_METADATA_KEYS or key == "embedding_api_key"):
                    continue
                # Match the metadata attached to tool declarations, while retaining
                # arbitrary user fields inside tool env/config dictionaries.
                if path == ("tools", "[]") and key in {"created_at", "updated_at"}:
                    continue
                if is_secret_field_name(key) and not isinstance(value, (dict, list)):
                    if not is_secret_ref(value):
                        continue
                result[key] = normalize(value, (*path, key))
            return result
        if isinstance(obj, list):
            return [normalize(item, (*path, "[]")) for item in obj]
        return obj

    return normalize(config, ())


def agent_config_hash(config: dict) -> str:
    """SHA-256 of the canonical JSON of *config* after removing volatile and secret value keys.

    Returns the full 64-char hex digest.
    """
    scrubbed = normalized_config_view(config)
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


class ReferencedConfigUnavailable(RuntimeError):
    """A supervisor's effective published configuration cannot be verified."""


async def compute_published_fingerprint(config: dict) -> ConfigFingerprint:
    """Include published internal children in a supervisor's effective configuration.

    Capture this before building the graph. A publish during construction then
    remains detectable on the next request instead of stamping an older graph
    with a newer child's fingerprint. External agents are described by the roster
    itself; their remotely managed configuration is outside this contract.
    """
    fingerprint = compute_fingerprint(config)
    if (config.get("agent") or {}).get("kind") != "supervisor":
        return fingerprint

    from cuga.backend.server.config_store import load_config

    refs = sorted(
        {
            entry["ref"]
            for entry in (config.get("supervisor") or {}).get("subAgents") or []
            if entry.get("kind") == "internal" and entry.get("ref")
        }
    )
    if not refs:
        return fingerprint
    children = {}
    for ref in refs:
        try:
            child, _ = await load_config(None, ref)
            if not child:
                raise ReferencedConfigUnavailable("Referenced sub-agent has no published configuration")
            children[ref] = agent_config_hash(child)
        except Exception as exc:
            raise ReferencedConfigUnavailable("Could not verify referenced sub-agent configuration") from exc
    effective = {"supervisor": fingerprint.agent_hash, "children": children}
    digest = hashlib.sha256(json.dumps(effective, sort_keys=True).encode("utf-8")).hexdigest()
    return ConfigFingerprint(agent_hash=digest, runtime_ver=fingerprint.runtime_ver)


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
