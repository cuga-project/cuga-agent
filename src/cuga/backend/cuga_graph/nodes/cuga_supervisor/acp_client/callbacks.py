"""Minimal, fail-closed ACP client callbacks for outbound delegation."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal, TypeAlias

_PERMISSION_ID_LIMIT = 128
_PERMISSION_TEXT_LIMIT = 512
_PERMISSION_KIND_LIMIT = 64
_PERMISSION_OPTION_LIMIT = 32
_REPLACEMENT = "�"

PermissionKind: TypeAlias = Literal["allow_once", "allow_always", "reject_once", "reject_always"]


@dataclass(frozen=True)
class PermissionOptionDTO:
    """Safe CUGA-owned representation of one ACP permission choice."""

    option_id: str
    name: str
    kind: PermissionKind


@dataclass(frozen=True)
class PermissionRequestDTO:
    """Safe CUGA-owned metadata for an operation-level permission request."""

    lifecycle_id: str
    session_id: str
    tool_call_id: str
    title: str
    kind: str | None
    options: tuple[PermissionOptionDTO, ...]


PermissionHandler: TypeAlias = Callable[[PermissionRequestDTO], Awaitable[str | None]]
LifecycleRegistrar: TypeAlias = Callable[[str, Any], Awaitable[None] | None]


def _display_text(value: Any, *, limit: int, fallback: str = "") -> str:
    raw = value if isinstance(value, str) else ""
    normalized = "".join(character if character.isprintable() else _REPLACEMENT for character in raw)
    normalized = " ".join(normalized.split())
    return normalized[:limit] or fallback


class ACPClientCallbacks:
    """Collect safe output and mediate agent-to-client requests for one session."""

    def __init__(
        self,
        permission_handler: PermissionHandler | None = None,
        *,
        lifecycle_id: str = "",
        lifecycle_registrar: LifecycleRegistrar | None = None,
    ) -> None:
        self._permission_handler = permission_handler
        self._lifecycle_id = lifecycle_id
        self._lifecycle_registrar = lifecycle_registrar
        self._session_id: str | None = None
        self._text_chunks: list[str] = []
        self.permission_required = False
        self.cancelled = False
        self.closed = False

    @property
    def text(self) -> str:
        return "".join(self._text_chunks)

    def bind_session(self, session_id: str) -> None:
        if self._session_id is not None and self._session_id != session_id:
            raise RuntimeError("ACP callback session is already bound")
        self._session_id = session_id

    async def register_lifecycle(self, owner: Any) -> None:
        """Register a live owner by opaque ID without placing it in permission metadata."""
        if self._lifecycle_registrar is not None:
            result = self._lifecycle_registrar(self._lifecycle_id, owner)
            if hasattr(result, "__await__"):
                await result

    def on_connect(self, _connection: Any) -> None:
        """The lifecycle owns the connection; callbacks keep no duplicate resource handle."""

    async def session_update(self, session_id: str, update: Any, **_kwargs: Any) -> None:
        if self.closed or session_id != self._session_id:
            return
        if getattr(update, "session_update", None) != "agent_message_chunk":
            return
        content = getattr(update, "content", None)
        if getattr(content, "type", None) == "text" and isinstance(getattr(content, "text", None), str):
            self._text_chunks.append(content.text)

    async def request_permission(
        self,
        session_id: str,
        tool_call: Any,
        options: list[Any],
        **_kwargs: Any,
    ) -> Any:
        from acp.schema import AllowedOutcome, DeniedOutcome, RequestPermissionResponse

        if self.closed or session_id != self._session_id:
            return RequestPermissionResponse(outcome=DeniedOutcome(outcome="cancelled"))

        safe_options_list: list[PermissionOptionDTO] = []
        original_option_ids: dict[str, str] = {}
        for index, option in enumerate(options[:_PERMISSION_OPTION_LIMIT]):
            kind = getattr(option, "kind", None)
            original_id = getattr(option, "option_id", None)
            if kind not in ("allow_once", "allow_always", "reject_once", "reject_always") or not isinstance(
                original_id, str
            ):
                continue
            token = f"option-{index + 1}"
            safe_options_list.append(
                PermissionOptionDTO(
                    option_id=token,
                    name=_display_text(getattr(option, "name", ""), limit=_PERMISSION_TEXT_LIMIT),
                    kind=kind,
                )
            )
            original_option_ids[token] = original_id
        safe_options = tuple(safe_options_list)
        request = PermissionRequestDTO(
            lifecycle_id=self._lifecycle_id,
            session_id=_display_text(session_id, limit=_PERMISSION_ID_LIMIT),
            tool_call_id=_display_text(getattr(tool_call, "tool_call_id", ""), limit=_PERMISSION_ID_LIMIT),
            title=_display_text(
                getattr(tool_call, "title", ""),
                limit=_PERMISSION_TEXT_LIMIT,
                fallback="Permission requested",
            ),
            kind=_display_text(getattr(tool_call, "kind", None), limit=_PERMISSION_KIND_LIMIT) or None,
            options=safe_options,
        )
        selected = await self._permission_handler(request) if self._permission_handler is not None else None
        if selected is not None and selected in original_option_ids:
            return RequestPermissionResponse(
                outcome=AllowedOutcome(outcome="selected", optionId=original_option_ids[selected])
            )
        self.permission_required = True
        return RequestPermissionResponse(outcome=DeniedOutcome(outcome="cancelled"))

    async def write_text_file(self, *_args: Any, **_kwargs: Any) -> None:
        raise NotImplementedError("ACP filesystem capability is not available")

    async def read_text_file(self, *_args: Any, **_kwargs: Any) -> None:
        raise NotImplementedError("ACP filesystem capability is not available")

    async def create_terminal(self, *_args: Any, **_kwargs: Any) -> None:
        raise NotImplementedError("ACP terminal capability is not available")

    async def terminal_output(self, *_args: Any, **_kwargs: Any) -> None:
        raise NotImplementedError("ACP terminal capability is not available")

    async def release_terminal(self, *_args: Any, **_kwargs: Any) -> None:
        raise NotImplementedError("ACP terminal capability is not available")

    async def wait_for_terminal_exit(self, *_args: Any, **_kwargs: Any) -> None:
        raise NotImplementedError("ACP terminal capability is not available")

    async def kill_terminal(self, *_args: Any, **_kwargs: Any) -> None:
        raise NotImplementedError("ACP terminal capability is not available")

    async def create_elicitation(self, *_args: Any, **_kwargs: Any) -> None:
        raise NotImplementedError("ACP elicitation capability is not available")

    async def complete_elicitation(self, *_args: Any, **_kwargs: Any) -> None:
        return None

    async def ext_method(self, _method: str, _params: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError("ACP extension methods are not available")

    async def ext_notification(self, _method: str, _params: dict[str, Any]) -> None:
        return None
