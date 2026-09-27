"""Safe, protocol-neutral metadata and option selection for ACP permissions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Literal, TypeAlias

PermissionKind: TypeAlias = Literal["allow_once", "allow_always", "reject_once", "reject_always"]

_ID_LIMIT = 128
_TITLE_LIMIT = 512
_DESCRIPTION_LIMIT = 1024
_KIND_LIMIT = 64
_LOCATION_LIMIT = 512
_LOCATION_COUNT_LIMIT = 16
_OPTION_COUNT_LIMIT = 32
_REPLACEMENT = "�"


def _safe_text(value: object, *, limit: int, fallback: str = "") -> str:
    raw = value if isinstance(value, str) else ""
    normalized = "".join(character if character.isprintable() else _REPLACEMENT for character in raw)
    normalized = " ".join(normalized.split())
    return normalized[:limit] or fallback


@dataclass(frozen=True)
class PermissionOptionDTO:
    """One bounded display-safe permission option with an opaque CUGA token."""

    option_id: str
    name: str
    kind: PermissionKind


@dataclass(frozen=True)
class SafePermissionRequest:
    """The complete permission shape allowed to cross a graph checkpoint."""

    lifecycle_id: str
    session_id: str
    tool_call_id: str
    title: str
    description: str
    kind: str | None
    locations: tuple[str, ...]
    options: tuple[PermissionOptionDTO, ...]
    created_at: str
    expires_at: str

    @classmethod
    def create(
        cls,
        *,
        lifecycle_id: str,
        session_id: str,
        tool_call_id: str,
        title: str,
        description: str = "",
        kind: str | None = None,
        locations: tuple[str, ...] = (),
        options: tuple[PermissionOptionDTO, ...],
        ttl_seconds: float,
    ) -> SafePermissionRequest:
        now = datetime.now(timezone.utc)
        safe_options = tuple(
            PermissionOptionDTO(
                option_id=_safe_text(option.option_id, limit=_ID_LIMIT),
                name=_safe_text(option.name, limit=_TITLE_LIMIT),
                kind=option.kind,
            )
            for option in options[:_OPTION_COUNT_LIMIT]
            if option.kind in ("allow_once", "allow_always", "reject_once", "reject_always")
            and _safe_text(option.option_id, limit=_ID_LIMIT)
        )
        return cls(
            lifecycle_id=_safe_text(lifecycle_id, limit=_ID_LIMIT),
            session_id=_safe_text(session_id, limit=_ID_LIMIT),
            tool_call_id=_safe_text(tool_call_id, limit=_ID_LIMIT),
            title=_safe_text(title, limit=_TITLE_LIMIT, fallback="Permission requested"),
            description=_safe_text(description, limit=_DESCRIPTION_LIMIT),
            kind=_safe_text(kind, limit=_KIND_LIMIT) or None,
            locations=tuple(
                text
                for value in locations[:_LOCATION_COUNT_LIMIT]
                if (text := _safe_text(value, limit=_LOCATION_LIMIT))
            ),
            options=safe_options,
            created_at=now.isoformat(),
            expires_at=(now + timedelta(seconds=ttl_seconds)).isoformat(),
        )


def select_permission_option(
    options: tuple[PermissionOptionDTO, ...], *, approved: bool | None
) -> str | None:
    """Select one exact offered token, requiring a one-time option and failing closed."""

    if approved is True:
        allow_once = [option.option_id for option in options if option.kind == "allow_once"]
        return allow_once[0] if len(allow_once) == 1 else None
    if approved is False:
        reject_once = [option.option_id for option in options if option.kind == "reject_once"]
        return reject_once[0] if len(reject_once) == 1 else None
    return None
