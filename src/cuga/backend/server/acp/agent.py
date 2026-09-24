"""Official SDK implementation of the inbound CUGA ACP v1 agent."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from importlib.metadata import PackageNotFoundError, version
import logging
from pathlib import PurePath
from typing import Any
import uuid

from acp import PROTOCOL_VERSION, RequestError
from acp.schema import (
    AgentCapabilities,
    AgentMessageChunk,
    AllowedOutcome,
    Implementation,
    McpCapabilities,
    NewSessionResponse,
    PermissionOption,
    PromptCapabilities,
    PromptResponse,
    SessionCapabilities,
    TextContentBlock,
    ToolCallUpdate,
)

logger = logging.getLogger(__name__)

# A malicious or broken graph must not create an unbounded permission loop.
MAX_PERMISSION_RESUMES = 12
_TEXT_SEPARATOR = "\n\n"
_EMPTY_STREAM_FALLBACK = "Agent completed without a response."
_SAFE_OUTPUT_EVENTS = frozenset({"agent_message", "message", "output", "response"})


@dataclass(slots=True)
class SessionRecord:
    """Transport metadata and mutable lifecycle state for one ACP session."""

    session_id: str
    context_id: str
    cwd: str
    additional_directories: tuple[str, ...]
    active_task: asyncio.Task[PromptResponse] | None = None
    cancelled: bool = False
    terminal: bool = False
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class CugaAcpAgent:
    """Text-only stable ACP v1 adapter over a protocol-neutral CUGA runner."""

    def __init__(self, runner: Any) -> None:
        self._runner = runner
        self._client: Any | None = None
        self._sessions: dict[str, SessionRecord] = {}
        self._sessions_lock = asyncio.Lock()

    def on_connect(self, conn: Any) -> None:
        self._client = conn

    async def initialize(
        self,
        protocol_version: int,
        client_capabilities: Any | None = None,
        client_info: Any | None = None,
        **kwargs: Any,
    ) -> Any:
        del client_capabilities, client_info, kwargs
        if protocol_version != PROTOCOL_VERSION:
            raise RequestError.invalid_params()
        try:
            cuga_version = version("cuga")
        except PackageNotFoundError:
            cuga_version = "unknown"
        return __import__("acp.schema", fromlist=["InitializeResponse"]).InitializeResponse(
            protocolVersion=PROTOCOL_VERSION,
            agentCapabilities=AgentCapabilities(
                loadSession=False,
                promptCapabilities=PromptCapabilities(image=False, audio=False, embeddedContext=False),
                mcpCapabilities=McpCapabilities(http=False, sse=False, acp=False),
                sessionCapabilities=SessionCapabilities(),
            ),
            authMethods=[],
            agentInfo=Implementation(name="cuga", title="CUGA", version=cuga_version),
        )

    async def new_session(
        self,
        cwd: str,
        additional_directories: list[str] | None = None,
        mcp_servers: list[Any] | None = None,
        **kwargs: Any,
    ) -> NewSessionResponse:
        del mcp_servers, kwargs
        directories = tuple(additional_directories or ())
        if not _is_absolute_path(cwd) or any(not _is_absolute_path(path) for path in directories):
            raise RequestError.invalid_params()
        session_id = uuid.uuid4().hex
        record = SessionRecord(
            session_id=session_id,
            context_id=f"acp_{uuid.uuid4().hex}",
            cwd=cwd,
            additional_directories=directories,
        )
        async with self._sessions_lock:
            self._sessions[session_id] = record
        return NewSessionResponse(sessionId=session_id)

    def session_metadata(self, session_id: str) -> SessionRecord:
        """Return immutable session mapping metadata for tests and integrations."""
        record = self._sessions.get(session_id)
        if record is None:
            raise KeyError(session_id)
        return record

    async def prompt(self, session_id: str, prompt: list[Any], **kwargs: Any) -> PromptResponse:
        del kwargs
        message = extract_text_prompt(prompt)
        record = self._session_or_error(session_id)
        current = asyncio.current_task()
        if current is None:  # pragma: no cover - asyncio always supplies one here
            raise RequestError.internal_error()
        async with record.lock:
            if record.active_task is not None and not record.active_task.done():
                raise RequestError.invalid_request()
            record.cancelled = False
            record.terminal = False
            record.active_task = current
        try:
            return await self._run_turn(record, message)
        except asyncio.CancelledError:
            if record.cancelled:
                return PromptResponse(stopReason="cancelled")
            raise
        except RequestError:
            raise
        except Exception as exc:
            logger.error("Inbound ACP prompt failed (%s)", type(exc).__name__)
            raise RequestError.internal_error() from None
        finally:
            async with record.lock:
                if record.active_task is current:
                    record.active_task = None

    async def _run_turn(self, record: SessionRecord, message: str) -> PromptResponse:
        approval: dict[str, Any] | None = None
        final_sent = False
        emitted_text: set[str] = set()
        permission_resumes = 0
        while True:
            saw_event = False
            async for event in self._runner.run(message, context_id=record.context_id, approval=approval):
                saw_event = True
                if record.cancelled:
                    return PromptResponse(stopReason="cancelled")
                if event.name == "error":
                    raise RequestError.internal_error()
                if event.name == "input_required":
                    if permission_resumes >= MAX_PERMISSION_RESUMES:
                        raise RequestError.internal_error()
                    approval = await self._request_permission(record, event.data)
                    permission_resumes += 1
                    break
                text = _safe_event_text(event.data)
                if event.name == "final_answer":
                    async with record.lock:
                        if record.cancelled:
                            return PromptResponse(stopReason="cancelled")
                        record.terminal = True
                    final_text = text or _EMPTY_STREAM_FALLBACK
                    if final_text not in emitted_text:
                        await self._emit_text(record, final_text)
                        emitted_text.add(final_text)
                    final_sent = True
                    return PromptResponse(stopReason="end_turn")
                if event.name in _SAFE_OUTPUT_EVENTS and text:
                    await self._emit_text(record, text)
                    emitted_text.add(text)
            else:
                async with record.lock:
                    if record.cancelled:
                        return PromptResponse(stopReason="cancelled")
                    record.terminal = True
                if not saw_event or not final_sent:
                    await self._emit_text(record, _EMPTY_STREAM_FALLBACK)
                return PromptResponse(stopReason="end_turn")

    async def _request_permission(self, record: SessionRecord, data: Any) -> dict[str, Any]:
        if self._client is None:
            raise RequestError.internal_error()
        safe_text = _safe_event_text(data) or "CUGA requests permission to continue."
        action_id = _safe_action_id(data)
        response = await self._client.request_permission(
            record.session_id,
            ToolCallUpdate(
                toolCallId=f"permission-{uuid.uuid4().hex}",
                kind="other",
                status="pending",
                title=safe_text,
            ),
            [
                PermissionOption(optionId="allow-once", name="Allow once", kind="allow_once"),
                PermissionOption(optionId="reject-once", name="Reject once", kind="reject_once"),
            ],
        )
        confirmed = False
        outcome = response.outcome
        if isinstance(outcome, AllowedOutcome):
            if outcome.option_id == "allow-once":
                confirmed = True
            elif outcome.option_id != "reject-once":
                logger.warning("ACP client selected an unknown permission option")
        return {"action_id": action_id, "confirmed": confirmed}

    async def _emit_text(self, record: SessionRecord, text: str) -> None:
        if record.cancelled or self._client is None:
            return
        await self._client.session_update(
            record.session_id,
            AgentMessageChunk(
                sessionUpdate="agent_message_chunk",
                content=TextContentBlock(type="text", text=text),
            ),
        )

    async def cancel(self, session_id: str, **kwargs: Any) -> None:
        del kwargs
        record = self._sessions.get(session_id)
        if record is None:
            return
        async with record.lock:
            if record.terminal:
                return
            record.cancelled = True
            task = record.active_task
            if task is None or task.done() or task is asyncio.current_task():
                return
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    async def shutdown(self) -> None:
        async with self._sessions_lock:
            session_ids = tuple(self._sessions)
        await asyncio.gather(*(self.cancel(session_id) for session_id in session_ids))
        shutdown = getattr(self._runner, "shutdown", None)
        if callable(shutdown):
            await shutdown()

    async def load_session(self, **kwargs: Any) -> None:
        del kwargs
        raise RequestError.method_not_found("session/load")

    async def list_sessions(self, **kwargs: Any) -> None:
        del kwargs
        raise RequestError.method_not_found("session/list")

    async def set_session_mode(self, **kwargs: Any) -> None:
        del kwargs
        raise RequestError.method_not_found("session/set_mode")

    async def set_config_option(self, **kwargs: Any) -> None:
        del kwargs
        raise RequestError.method_not_found("session/set_config_option")

    async def authenticate(self, **kwargs: Any) -> None:
        del kwargs
        raise RequestError.method_not_found("authenticate")

    async def fork_session(self, **kwargs: Any) -> None:
        del kwargs
        raise RequestError.method_not_found("session/fork")

    async def resume_session(self, **kwargs: Any) -> None:
        del kwargs
        raise RequestError.method_not_found("session/resume")

    async def close_session(self, **kwargs: Any) -> None:
        del kwargs
        raise RequestError.method_not_found("session/close")

    async def ext_method(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        del params
        raise RequestError.method_not_found(f"_{method}")

    async def ext_notification(self, method: str, params: dict[str, Any]) -> None:
        del method, params

    def _session_or_error(self, session_id: str) -> SessionRecord:
        record = self._sessions.get(session_id)
        if record is None:
            raise RequestError.resource_not_found()
        return record


def extract_text_prompt(prompt: list[Any]) -> str:
    """Validate and join ordered text blocks using a blank-line separator."""
    if not prompt or any(not isinstance(block, TextContentBlock) for block in prompt):
        raise RequestError.invalid_params()
    text = _TEXT_SEPARATOR.join(block.text for block in prompt)
    if not text.strip():
        raise RequestError.invalid_params()
    return text


def _safe_event_text(data: Any) -> str:
    if isinstance(data, str):
        return data
    if isinstance(data, dict):
        value = data.get("text")
        return value if isinstance(value, str) else ""
    return ""


def _safe_action_id(data: Any) -> str:
    if isinstance(data, dict):
        value = data.get("action_id")
        if isinstance(value, str) and value:
            return value
    return "unknown"


def _is_absolute_path(value: Any) -> bool:
    return isinstance(value, str) and bool(value) and PurePath(value).is_absolute()
