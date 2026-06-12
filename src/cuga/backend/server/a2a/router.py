"""FastAPI router exposing CUGA over A2A.

Wire shape (v0.3 compat):

  GET  /.well-known/agent.json   — AgentCard JSON
  POST /a2a                      — JSON-RPC 2.0
                                   - method=message/send   → JSON envelope
                                   - method=message/stream → SSE stream of frames

The router is created on demand by the application factory and is only
mounted when ``settings.a2a.enabled`` is true; that opt-in lives in
``main.py`` so importing this package does not change CUGA's surface
area.
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any, AsyncIterator, Mapping, Protocol

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sse_starlette.sse import EventSourceResponse

from cuga.backend.server.a2a._a2a_types import (
    Message,
    MessageSendParams,
    Task,
    TaskState,
    TaskStatus,
)
from cuga.backend.server.a2a.agent_card import build_agent_card
from cuga.backend.server.a2a.task_adapter import stream_events_to_a2a

logger = logging.getLogger(__name__)


class GraphRunner(Protocol):
    """Minimal contract a graph runner must satisfy.

    Anything async that yields events with ``name`` / ``data`` / optional
    ``final`` is acceptable. We don't import CUGA's graph types here.
    """

    def run(self, message: str, context_id: str | None = None) -> AsyncIterator[Any]:  # pragma: no cover
        """Yield graph events for ``message`` on the given thread."""
        ...


# JSON-RPC error codes we use.
_PARSE_ERROR = -32700
_INVALID_REQUEST = -32600
_METHOD_NOT_FOUND = -32601
_INVALID_PARAMS = -32602
_INTERNAL_ERROR = -32603


def _rpc_error(rid: Any, code: int, message: str, data: Any | None = None) -> dict[str, Any]:
    """Build a JSON-RPC 2.0 error envelope echoing the request id."""
    err: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        err["data"] = data
    return {"jsonrpc": "2.0", "id": rid, "error": err}


def _rpc_result(rid: Any, result: Any) -> dict[str, Any]:
    """Build a JSON-RPC 2.0 success envelope echoing the request id."""
    return {"jsonrpc": "2.0", "id": rid, "result": result}


def _extract_message_text(params: MessageSendParams) -> str:
    """Concatenate every text part of the inbound message in order."""
    parts = []
    for p in params.message.parts:
        root = getattr(p, "root", p)
        text = getattr(root, "text", None)
        if isinstance(text, str):
            parts.append(text)
    return "\n".join(parts)


def _ensure_context_id(params: MessageSendParams) -> str:
    """Return the caller-supplied contextId, or mint a fresh one."""
    return params.message.context_id or uuid.uuid4().hex


def build_router(*, runner: GraphRunner, settings: Mapping[str, Any], **_kwargs: Any) -> APIRouter:
    """Build the A2A FastAPI router bound to a specific graph runner.

    The runner is captured by closure so callers can plug in any CUGA
    graph instance (production or scripted) without the router having to
    know how the graph is constructed.
    """

    router = APIRouter()
    skill_specs = list(settings.get("skill_ids") or [])
    skills = [{"id": s, "name": s, "description": s} for s in skill_specs]

    async def _agent_card_response() -> JSONResponse:
        """Serialize the AgentCard with camelCase aliases for SDK clients."""
        card = build_agent_card(settings, skills)
        return JSONResponse(card.model_dump(mode="json", exclude_none=True, by_alias=True))

    # Both paths are served: /.well-known/agent.json is the original A2A
    # well-known path; /.well-known/agent-card.json is what the 1.x SDK
    # resolver fetches. Older clients keep working, newer clients work too.
    @router.get("/.well-known/agent.json")
    async def agent_card_endpoint_legacy() -> JSONResponse:
        """Serve the AgentCard at the v0.3 well-known path."""
        return await _agent_card_response()

    @router.get("/.well-known/agent-card.json")
    async def agent_card_endpoint() -> JSONResponse:
        """Serve the AgentCard at the path the 1.x SDK resolver fetches."""
        return await _agent_card_response()

    async def _run_and_collect(message_text: str, context_id: str, task_id: str) -> Task:
        """Drive the runner to completion and fold the events into a Task."""
        agen = runner.run(message_text, context_id)
        events = []
        async for ev in agen:
            events.append(ev)
        history: list[Message] = []
        last_state: TaskState = TaskState.completed
        for upd in stream_events_to_a2a(events, task_id=task_id, context_id=context_id):
            if upd.status and upd.status.message:
                history.append(upd.status.message)
            if upd.status and upd.status.state:
                last_state = upd.status.state
        return Task(
            id=task_id,
            context_id=context_id,
            status=TaskStatus(state=last_state),
            history=history,
        )

    async def _sse_stream(
        message_text: str, context_id: str, task_id: str, rpc_id: Any
    ) -> AsyncIterator[dict]:
        """Yield SSE frames carrying JSON-RPC envelopes per task update.

        If the runner or adapter raises, we still emit one final JSON-RPC
        error frame before closing — otherwise clients see a half-open
        stream and can't distinguish a network drop from a server bug.
        """
        try:
            agen = runner.run(message_text, context_id)
            events = []
            async for ev in agen:
                events.append(ev)
            for upd in stream_events_to_a2a(events, task_id=task_id, context_id=context_id):
                frame = _rpc_result(rpc_id, upd.model_dump(mode="json", exclude_none=True, by_alias=True))
                yield {"data": json.dumps(frame)}
        except Exception as exc:
            logger.exception("A2A SSE stream failed for task %s", task_id)
            err_frame = _rpc_error(rpc_id, _INTERNAL_ERROR, f"Internal error: {type(exc).__name__}")
            yield {"data": json.dumps(err_frame)}

    @router.post("/a2a")
    async def jsonrpc_endpoint(request: Request):
        """Dispatch a JSON-RPC 2.0 request to the right A2A method.

        Returns a JSON envelope for ``message/send`` and an SSE stream
        for ``message/stream``. Malformed JSON, non-object payloads, and
        unknown methods are mapped to standard JSON-RPC error codes
        (-32700 / -32600 / -32601 / -32602) rather than HTTP 5xx, so
        SDK clients can parse the failure mode.
        """
        raw = await request.body()
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            return JSONResponse(_rpc_error(None, _PARSE_ERROR, "Parse error", str(exc)))

        if not isinstance(payload, dict):
            return JSONResponse(_rpc_error(None, _INVALID_REQUEST, "Invalid Request"))

        rpc_id = payload.get("id")
        if payload.get("jsonrpc") != "2.0" or not isinstance(payload.get("method"), str):
            return JSONResponse(_rpc_error(rpc_id, _INVALID_REQUEST, "Invalid Request"))

        method = payload["method"]
        params_dict = payload.get("params") or {}

        if method == "message/send":
            try:
                params = MessageSendParams.model_validate(params_dict)
            except Exception as exc:
                return JSONResponse(_rpc_error(rpc_id, _INVALID_PARAMS, "Invalid params", str(exc)))
            message_text = _extract_message_text(params)
            context_id = _ensure_context_id(params)
            task_id = uuid.uuid4().hex
            try:
                task = await _run_and_collect(message_text, context_id, task_id)
            except Exception as exc:  # graph blew up
                # Don't echo raw exception text across the wire (paths,
                # config, etc.). Keep details in server logs; tell the
                # caller only the exception class so they can route it.
                logger.exception("A2A message/send graph run failed for task %s", task_id)
                return JSONResponse(
                    _rpc_error(rpc_id, _INTERNAL_ERROR, f"Internal error: {type(exc).__name__}")
                )
            return JSONResponse(
                _rpc_result(rpc_id, task.model_dump(mode="json", exclude_none=True, by_alias=True))
            )

        if method == "message/stream":
            try:
                params = MessageSendParams.model_validate(params_dict)
            except Exception as exc:
                return JSONResponse(_rpc_error(rpc_id, _INVALID_PARAMS, "Invalid params", str(exc)))
            message_text = _extract_message_text(params)
            context_id = _ensure_context_id(params)
            task_id = uuid.uuid4().hex
            return EventSourceResponse(_sse_stream(message_text, context_id, task_id, rpc_id))

        return JSONResponse(_rpc_error(rpc_id, _METHOD_NOT_FOUND, "Method not found"))

    return router
