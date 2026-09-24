"""Minimal, fail-closed ACP client callbacks for outbound delegation."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal, TypeAlias

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

    session_id: str
    tool_call_id: str
    title: str
    kind: str | None
    options: tuple[PermissionOptionDTO, ...]


PermissionHandler: TypeAlias = Callable[[PermissionRequestDTO], Awaitable[str | None]]


class ACPClientCallbacks:
    """Collect safe output and mediate agent-to-client requests for one session."""

    def __init__(self, permission_handler: PermissionHandler | None = None) -> None:
        self._permission_handler = permission_handler
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

        safe_options = tuple(
            PermissionOptionDTO(option_id=option.option_id, name=option.name, kind=option.kind)
            for option in options
        )
        request = PermissionRequestDTO(
            session_id=session_id,
            tool_call_id=str(getattr(tool_call, "tool_call_id", "")),
            title=str(getattr(tool_call, "title", "") or "Permission requested"),
            kind=getattr(tool_call, "kind", None),
            options=safe_options,
        )
        self.permission_required = True
        selected = await self._permission_handler(request) if self._permission_handler is not None else None
        if selected is not None and any(option.option_id == selected for option in safe_options):
            return RequestPermissionResponse(outcome=AllowedOutcome(outcome="selected", optionId=selected))
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
