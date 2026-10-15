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


@dataclass(frozen=True, slots=True)
class SessionMetadata:
    """Immutable, safe projection of one ACP-to-CUGA session mapping."""

    session_id: str
    context_id: str
    cwd: str
    additional_directories: tuple[str, ...]


@dataclass(slots=True)
class SessionRecord:
    """Transport metadata and lock-protected lifecycle state for one ACP session."""

    session_id: str
    context_id: str
    cwd: str
    additional_directories: tuple[str, ...]
    generation: int = 0
    issued_generation: int = 0
    cancel_generation: int = 0
    state: str = "idle"
    active_task: asyncio.Task[PromptResponse] | None = None
    registering_tasks: dict[int, asyncio.Task[PromptResponse]] = field(default_factory=dict)
    pending_action_id: str | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class CugaAcpAgent:
    """Text-only stable ACP v1 adapter over a protocol-neutral CUGA runner."""

    def __init__(self, runner: Any) -> None:
        self._runner = runner
        self._client: Any | None = None
        self._sessions: dict[str, SessionRecord] = {}
        self._sessions_lock = asyncio.Lock()
        self._closing = False

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

    def session_metadata(self, session_id: str) -> SessionMetadata:
        """Return an immutable projection without synchronized lifecycle objects."""
        record = self._sessions.get(session_id)
        if record is None:
            raise KeyError(session_id)
        return SessionMetadata(
            session_id=record.session_id,
            context_id=record.context_id,
            cwd=record.cwd,
            additional_directories=record.additional_directories,
        )

    async def prompt(self, session_id: str, prompt: list[Any], **kwargs: Any) -> PromptResponse:
        del kwargs
        message = extract_text_prompt(prompt)
        record = self._session_or_error(session_id)
        current = asyncio.current_task()
        if current is None:  # pragma: no cover - asyncio always supplies one here
            raise RequestError.internal_error()

        # Async functions run synchronously until their first await. Publish this
        # registration intent before contending for the session lock so a cancel
        # task dispatched by the SDK cannot pass unnoticed while prompt waits.
        record.issued_generation += 1
        generation = record.issued_generation
        record.registering_tasks[generation] = current

        try:
            async with record.lock:
                if self._closing:
                    raise RequestError.invalid_request()
                if record.cancel_generation >= generation:
                    record.state = "cancelled"
                    return PromptResponse(stopReason="cancelled")
                if record.active_task is not None and not record.active_task.done():
                    raise RequestError.invalid_request()
                record.generation = generation
                record.active_task = current
                record.pending_action_id = None
                record.state = "running"
            return await self._run_turn(record, generation, message)
        except asyncio.CancelledError:
            if await self._is_cancelled(record, generation):
                return PromptResponse(stopReason="cancelled")
            raise
        except RequestError:
            raise
        except Exception as exc:
            logger.error("Inbound ACP prompt failed (%s)", type(exc).__name__)
            raise RequestError.internal_error() from None
        finally:
            async with record.lock:
                record.registering_tasks.pop(generation, None)
                if record.generation == generation and record.active_task is current:
                    record.active_task = None
                    record.pending_action_id = None
                    if record.state not in {"committed", "cancelled"}:
                        record.state = "idle"

    async def _run_turn(self, record: SessionRecord, generation: int, message: str) -> PromptResponse:
        approval: dict[str, Any] | None = None
        approval_action_id: str | None = None
        permission_resumes = 0
        while True:
            if approval is not None:
                async with record.lock:
                    if not self._turn_is_live(record, generation):
                        return PromptResponse(stopReason="cancelled")
                    if record.pending_action_id != approval_action_id:
                        raise RequestError.internal_error()
                    record.pending_action_id = None

            async for event in self._runner.run(message, context_id=record.context_id, approval=approval):
                if await self._is_cancelled(record, generation):
                    return PromptResponse(stopReason="cancelled")
                if event.name == "error":
                    raise RequestError.internal_error()
                if event.name == "input_required":
                    if permission_resumes >= MAX_PERMISSION_RESUMES:
                        raise RequestError.internal_error()
                    action_id = _required_action_id(event.data)
                    async with record.lock:
                        if not self._turn_is_live(record, generation):
                            return PromptResponse(stopReason="cancelled")
                        record.pending_action_id = action_id
                    approval = await self._request_permission(record, generation, event.data, action_id)
                    approval_action_id = action_id
                    permission_resumes += 1
                    break
                text = _safe_event_text(event.data)
                if event.name == "final_answer":
                    final_text = text or _EMPTY_STREAM_FALLBACK
                    if not await self._begin_final_delivery(record, generation):
                        return PromptResponse(stopReason="cancelled")
                    await self._emit_text(record, final_text)
                    if not await self._commit_turn(record, generation):
                        return PromptResponse(stopReason="cancelled")
                    return PromptResponse(stopReason="end_turn")
                if event.name in _SAFE_OUTPUT_EVENTS and text:
                    if event.final:
                        if not await self._begin_final_delivery(record, generation):
                            return PromptResponse(stopReason="cancelled")
                        await self._emit_text(record, text)
                        if not await self._commit_turn(record, generation):
                            return PromptResponse(stopReason="cancelled")
                        return PromptResponse(stopReason="end_turn")
                    await self._emit_text(record, text)
                elif event.final:
                    if not await self._begin_final_delivery(record, generation):
                        return PromptResponse(stopReason="cancelled")
                    await self._emit_text(record, _EMPTY_STREAM_FALLBACK)
                    if not await self._commit_turn(record, generation):
                        return PromptResponse(stopReason="cancelled")
                    return PromptResponse(stopReason="end_turn")
            else:
                if not await self._begin_final_delivery(record, generation):
                    return PromptResponse(stopReason="cancelled")
                # A stream without an explicit final event always gets one terminal fallback.
                await self._emit_text(record, _EMPTY_STREAM_FALLBACK)
                if not await self._commit_turn(record, generation):
                    return PromptResponse(stopReason="cancelled")
                return PromptResponse(stopReason="end_turn")

    async def _request_permission(
        self, record: SessionRecord, generation: int, data: Any, action_id: str
    ) -> dict[str, Any]:
        if self._client is None:
            raise RequestError.internal_error()
        safe_text = _safe_event_text(data) or "CUGA requests permission to continue."
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
        async with record.lock:
            if not self._turn_is_live(record, generation):
                raise asyncio.CancelledError
            if record.pending_action_id != action_id:
                raise RequestError.internal_error()
        confirmed = False
        outcome = response.outcome
        if isinstance(outcome, AllowedOutcome):
            if outcome.option_id == "allow-once":
                confirmed = True
            elif outcome.option_id != "reject-once":
                logger.warning("ACP client selected an unknown permission option")
        return {"action_id": action_id, "confirmed": confirmed}

    async def _begin_final_delivery(self, record: SessionRecord, generation: int) -> bool:
        async with record.lock:
            if not self._turn_is_live(record, generation):
                return False
            record.state = "final_delivery"
            return True

    async def _commit_turn(self, record: SessionRecord, generation: int) -> bool:
        async with record.lock:
            if not self._turn_is_live(record, generation):
                return False
            record.state = "committed"
            return True

    async def _is_cancelled(self, record: SessionRecord, generation: int) -> bool:
        async with record.lock:
            return not self._turn_is_live(record, generation)

    @staticmethod
    def _turn_is_live(record: SessionRecord, generation: int) -> bool:
        return (
            record.generation == generation
            and record.cancel_generation < generation
            and record.state not in {"cancelled", "committed"}
        )

    async def _emit_text(self, record: SessionRecord, text: str) -> None:
        if self._client is None:
            raise RequestError.internal_error()
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
            task = record.active_task
            generation = record.generation
            if task is None:
                pending = sorted(record.registering_tasks.items())
                if not pending:
                    return
                generation, task = pending[0]
            elif record.state == "committed":
                return
            record.cancel_generation = max(record.cancel_generation, generation)
            record.state = "cancelled"
            record.pending_action_id = None
            if task.done() or task is asyncio.current_task():
                return
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    async def shutdown(self) -> None:
        async with self._sessions_lock:
            self._closing = True
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


def _required_action_id(data: Any) -> str:
    if isinstance(data, dict):
        value = data.get("action_id")
        if isinstance(value, str) and value:
            return value
    raise RequestError.internal_error()


def _is_absolute_path(value: Any) -> bool:
    return isinstance(value, str) and bool(value) and PurePath(value).is_absolute()
