"""Langfuse tracing for the CUGA SDK.

With ``advanced_features.langfuse_tracing = true`` and the Langfuse keys in the environment (``LANGFUSE_PUBLIC_KEY``,
``LANGFUSE_SECRET_KEY``, ``LANGFUSE_HOST`` or ``LANGFUSE_BASE_URL``), each ``CugaAgent.invoke`` / ``stream`` is one
Langfuse trace:

- a root ``agent`` observation ``cuga.invoke`` / ``cuga.stream`` with the user message as input and the answer as output;
- the LLM calls as ``generation`` observations (the LangChain ``CallbackHandler`` added to the run's callbacks, which the
  nested calls already share through ``langfuse_tracing``);
- one ``tool`` observation per tool call (``record_tool_call``, called where ``ToolCallTracker`` records a call), with
  arguments, result, app, duration and error.

The trace gets ``session_id`` = the thread id, and the user id, tags and metadata that the caller passes under
``config["configurable"]["langfuse"]`` (``{"user_id", "session_id", "tags", "metadata", "trace_name"}``).

A caller that brings its own Langfuse handler or ``langfuse_trace_id`` (an eval harness) keeps that trace: no root
observation is made, and tool observations go to the caller's trace id.

Tracing never changes the run: every Langfuse error is logged at debug level and dropped.
"""

from __future__ import annotations

import asyncio
import os
import time
from contextlib import ExitStack, asynccontextmanager, nullcontext
from contextvars import ContextVar
from typing import Any, AsyncIterator, Dict, Optional

from loguru import logger

_owned_trace: ContextVar[bool] = ContextVar('cuga_owned_langfuse_trace', default=False)

_UNSET = object()

MAX_FIELD_CHARS = 20000  # longer inputs and outputs are cut, so one tool result cannot fill the trace


def enabled() -> bool:
    """Tracing is on: the setting is true and the Langfuse keys are set."""
    try:
        from cuga.config import settings

        on = bool(getattr(getattr(settings, "advanced_features", None), "langfuse_tracing", False))
    except Exception:
        on = False
    return on and bool(os.environ.get("LANGFUSE_PUBLIC_KEY")) and bool(os.environ.get("LANGFUSE_SECRET_KEY"))


def _client() -> Any:
    from langfuse import get_client

    return get_client()


def _cut(value: Any) -> Any:
    """Make a value JSON-friendly and cut long text."""
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return (
            value if len(value) <= MAX_FIELD_CHARS else value[:MAX_FIELD_CHARS] + f"... [{len(value)} chars]"
        )
    if isinstance(value, dict):
        return {str(k): _cut(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_cut(v) for v in value[:200]]
    try:
        import json

        return _cut(json.dumps(value, default=str))
    except Exception:
        return _cut(str(value))


def _message_text(message: Any) -> Any:
    """The user message as text (a list of LangChain messages becomes role/content pairs)."""
    if message is None or isinstance(message, str):
        return message
    if isinstance(message, list):
        return [
            {"role": getattr(m, "type", "?"), "content": _cut(getattr(m, "content", str(m)))} for m in message
        ]
    return _cut(message)


def caller_has_trace(config: Optional[Dict[str, Any]], default_callbacks: Any = None) -> bool:
    """The caller already traces this run (its own handler or a trace id)."""
    configurable = (config or {}).get("configurable") or {}
    if configurable.get("langfuse_trace_id"):
        return True
    from cuga.backend.cuga_graph.utils.langfuse_tracing import collect_langfuse_callbacks_from_config

    return bool(
        collect_langfuse_callbacks_from_config(config)
        or (default_callbacks and collect_langfuse_callbacks_from_config({"callbacks": default_callbacks}))
    )


def _has_current_span() -> bool:
    """Probe OTel without Langfuse's warning for an absent active observation."""
    from opentelemetry.trace import get_current_span

    return get_current_span().get_span_context().is_valid


def caller_handler_records_tool(tool: Any) -> bool:
    """A caller's stock Langfuse handler receives this LangChain tool's callbacks when CUGA calls ``tool.ainvoke``.

    Checks the two callback sources that ``BaseTool.ainvoke`` uses: the inherited run config and ``tool.callbacks``.
    The SDK's own handler and ``create_sdk_tool_handler()`` skip tool callbacks, so they do not count.
    """
    if not enabled():
        return False
    from langchain_core.runnables.config import ensure_config

    from cuga.backend.cuga_graph.utils.langfuse_tracing import collect_langfuse_callbacks_from_config

    handlers = collect_langfuse_callbacks_from_config(
        {
            "callbacks": ensure_config().get("callbacks"),
            "configurable": {"callbacks": getattr(tool, "callbacks", None)},
        }
    )
    return any(
        not getattr(handler, "_cuga_skips_tool_observations", False)
        and not getattr(handler, "ignore_agent", False)
        for handler in handlers
    )


def inner_tool_config(tool_name: str) -> Optional[Dict[str, Any]]:
    """The ``config`` for a LangChain tool that CUGA calls inside a tool call that is already recorded.

    A ToolGuard-wrapped tool reaches the real tool through a second ``tool.ainvoke``. When the caller's handler
    already received the outer call, the inner call must not reach a Langfuse handler again, or one call becomes two
    observations. Returns ``None`` (keep LangChain's inherited callbacks) in every other case.
    """
    if not enabled():
        return None
    from cuga.backend.cuga_graph.nodes.cuga_lite.tracking.tracker import outer_callback_records

    if not outer_callback_records(tool_name):
        return None
    from langchain_core.callbacks import BaseCallbackManager
    from langchain_core.runnables.config import ensure_config

    from cuga.backend.cuga_graph.utils.langfuse_tracing import is_langfuse_callback_handler

    callbacks = ensure_config().get("callbacks")
    if isinstance(callbacks, BaseCallbackManager):
        manager = callbacks.copy()
        for handler in list(manager.handlers) + list(manager.inheritable_handlers):
            if is_langfuse_callback_handler(handler):
                manager.remove_handler(handler)
        return {"callbacks": manager}
    if isinstance(callbacks, (list, tuple)):
        return {"callbacks": [cb for cb in callbacks if not is_langfuse_callback_handler(cb)]}
    return None


def check_tool_ownership(config: Optional[Dict[str, Any]], default_callbacks: Any = None) -> None:
    """Warn about handler setups that cannot give one tool observation per call in one trace.

    Called at the start of ``invoke``, ``stream`` and the supervisor, in SDK-owned and caller-owned runs alike.
    """
    if not enabled():
        return
    try:
        from cuga.backend.cuga_graph.utils.langfuse_tracing import collect_langfuse_callbacks_from_config

        config = config or {}
        handlers = collect_langfuse_callbacks_from_config(config)
        handlers += collect_langfuse_callbacks_from_config({"callbacks": default_callbacks})
        managed = [h for h in handlers if getattr(h, "_cuga_skips_tool_observations", False)]
        managed = [h for h in managed if not getattr(h, "_cuga_sdk_owned", False)]
        stock = [h for h in handlers if not getattr(h, "_cuga_skips_tool_observations", False)]
        if managed and stock:
            logger.warning(
                "CUGA tool ownership mixed: stock and managed Langfuse handlers; "
                "strict single-writer guarantee unavailable"
            )
        if not managed:
            return
        trace_id = (config.get("configurable") or {}).get("langfuse_trace_id")
        if not trace_id and not _has_current_span():
            logger.warning(
                "CUGA managed tool observations require an active span or an explicit langfuse_trace_id; "
                "tool observations are not recorded for this run"
            )
            return
        if trace_id:
            for handler in managed:
                if getattr(handler, "_cuga_trace_id", None) != str(trace_id):
                    logger.warning(
                        "CUGA managed handler trace id does not match configurable.langfuse_trace_id; "
                        "tool observations and chain/model observations go to different traces. "
                        "Create the handler with create_sdk_tool_handler(trace_id=<the same id>)"
                    )
                    return
    except Exception as e:
        logger.debug(f"Langfuse: tool ownership check failed: {e}")


class RunTrace:
    """The root observation of one SDK run; ``end`` sets its output."""

    def __init__(self, span: Any = None, nested: bool = False):
        self.span = span
        self.nested = nested
        self.closed = False

    def end(
        self, output: Any = _UNSET, *, error: Optional[str] = None, metadata: Optional[Dict[str, Any]] = None
    ) -> None:
        if self.span is None or self.closed:
            return
        try:
            fields = {}
            if output is not _UNSET:
                fields["output"] = _cut(output)
            if metadata is not None:
                fields["metadata"] = _cut(metadata)
            if error is not None:
                fields.update(level="ERROR", status_message=_cut(error))
            self.span.update(**fields)
            if not self.nested and output is not _UNSET:
                self.span.set_trace_io(output=_cut(output))
        except Exception as e:
            logger.debug(f"Langfuse: run output not recorded: {e}")

    def close(self) -> None:
        """End the root observation once. Later ``end`` and ``close`` calls do nothing.

        ``run_trace`` calls this when the run leaves its context. A stream calls it earlier when its cleanup
        outlives the grace period, so the root observation does not stay open until the detached work finishes.
        """
        if self.span is None or self.closed:
            return
        self.closed = True
        try:
            self.span.end()
        except Exception as e:
            logger.debug(f"Langfuse: run observation not ended: {e}")


@asynccontextmanager
async def run_trace(
    name: str,
    message: Any,
    thread_id: Optional[str],
    config: Dict[str, Any],
    *,
    default_callbacks: Any = None,
) -> AsyncIterator[RunTrace]:
    """Preserve caller ancestry and diagnose incompatible tool observation ownership."""
    check_tool_ownership(config, default_callbacks)
    async with _run_trace(name, message, thread_id, config, default_callbacks=default_callbacks) as trace:
        yield trace


@asynccontextmanager
async def _run_trace(
    name: str,
    message: Any,
    thread_id: Optional[str],
    config: Dict[str, Any],
    *,
    default_callbacks: Any = None,
) -> AsyncIterator[RunTrace]:
    """Open the root observation of an SDK run and add a Langfuse callback handler to ``config["callbacks"]``.

    No-op (yields an empty ``RunTrace``) when tracing is off or the caller traces the run itself.
    """
    if not enabled() or caller_has_trace(config, default_callbacks):
        yield RunTrace()
        return
    stack = ExitStack()
    try:
        opts = dict((config.get("configurable") or {}).get("langfuse") or {})
        from langfuse import propagate_attributes
        from langfuse.langchain import CallbackHandler

        client = _client()
        owned_parent = _owned_trace.get()
        nested = owned_parent or _has_current_span()
        attrs = (
            nullcontext()
            if nested
            else propagate_attributes(
                session_id=str(opts.get("session_id") or thread_id or "") or None,
                user_id=opts.get("user_id"),
                tags=list(opts.get("tags") or []) or None,
                metadata={str(k): str(v)[:200] for k, v in (opts.get("metadata") or {}).items()} or None,
                trace_name=opts.get("trace_name") or name,
            )
        )
        stack.enter_context(attrs)
        span = stack.enter_context(
            client.start_as_current_observation(
                name=name, as_type="agent", input=_message_text(message), end_on_exit=False
            )
        )
    except Exception as e:
        try:
            stack.close()
        except Exception as cleanup_error:
            logger.debug(f"Langfuse: failed trace startup cleanup: {cleanup_error}")
        logger.debug(f"Langfuse: trace not started: {e}")
        yield RunTrace()
        return

    token = _owned_trace.set(True)
    trace = RunTrace(span, nested=nested)
    try:
        try:
            if not nested:
                span.set_trace_io(input=_message_text(message))
            callbacks = config.get("callbacks")
            callbacks = (
                list(callbacks)
                if isinstance(callbacks, (list, tuple))
                else ([callbacks] if callbacks else [])
            )
            sdk_handler = _handler_class(CallbackHandler)()
            sdk_handler._cuga_sdk_owned = True
            config["callbacks"] = callbacks + [sdk_handler]
        except Exception as e:
            logger.debug(f"Langfuse: callback handler not added: {e}")
        try:
            yield trace
        except asyncio.CancelledError:
            trace.end(error="cancelled")
            raise
        except Exception as e:
            trace.end(error=f"{type(e).__name__}: {e}")
            raise
    finally:
        _owned_trace.reset(token)
        try:
            stack.close()
        except Exception as e:
            logger.debug(f"Langfuse: trace cleanup failed: {e}")
        trace.close()
        # The client's batch processor exports completed observations. Do not await or
        # schedule an exporter here: even to_thread delays asyncio.run() shutdown.
        # Short-lived applications explicitly call flush() after their runs close.


def _handler_class(base: type) -> type:
    """The LangChain handler without its tool runs: ``record_tool_call`` records every tool call once, for every tool
    provider (registry, MCP, direct LangChain tools), with the app and the duration."""

    class CugaLangfuseHandler(base):  # type: ignore[misc,valid-type]
        _cuga_skips_tool_observations = True

        def on_tool_start(self, *args: Any, **kwargs: Any) -> None:
            return None

        def on_tool_end(self, *args: Any, **kwargs: Any) -> None:
            return None

        def on_tool_error(self, *args: Any, **kwargs: Any) -> None:
            return None

    return CugaLangfuseHandler


def create_sdk_tool_handler(
    *, trace_id: Optional[str] = None, public_key: Optional[str] = None, trace_context: Any = None
) -> Any:
    """Create an explicitly opted-in handler: Langfuse owns chains/LLMs, CUGA tools.

    Pass this *new* handler instead of a stock Langfuse handler. Never automatically
    convert or mutate an existing supplied instance. Requires SDK tracing enabled
    and active span or explicit trace ID for CUGA tool observations.

    ``trace_id`` must be the same id as ``configurable.langfuse_trace_id``. Without it, the
    handler writes chain and model observations to a new trace while CUGA writes tool
    observations to the configured one, so one run is split into two traces.
    ``trace_id`` is a shortcut for ``trace_context={"trace_id": trace_id}``.
    """
    from langfuse.langchain import CallbackHandler

    if trace_id is not None:
        if trace_context is not None and dict(trace_context).get("trace_id") != trace_id:
            raise ValueError("create_sdk_tool_handler: trace_id and trace_context['trace_id'] differ")
        trace_context = {**dict(trace_context or {}), "trace_id": trace_id}
    handler = _handler_class(CallbackHandler)(public_key=public_key, trace_context=trace_context)
    handler_trace_id = dict(trace_context or {}).get("trace_id")
    # Kept on the CUGA subclass so check_tool_ownership never reads the handler's private state.
    handler._cuga_trace_id = str(handler_trace_id) if handler_trace_id else None
    return handler


def final_answer(state: Any) -> Any:
    """The ``final_answer`` in a streamed state (a dict, or a tuple of namespace and update), else the state."""
    stack = [state]
    while stack:
        item = stack.pop()
        if isinstance(item, dict):
            if "final_answer" in item:
                return item["final_answer"]
            stack.extend(item.values())
        elif isinstance(item, (list, tuple)):
            stack.extend(item)
    return state


def record_tool_call(
    tool_name: str,
    arguments: Any,
    result: Any = None,
    *,
    app_name: Optional[str] = None,
    duration_ms: Optional[float] = None,
    error: Optional[str] = None,
    langchain_tool: bool = False,
) -> None:
    """One ``tool`` observation for a finished tool call, timed from ``duration_ms`` back to now.

    ``langchain_tool`` means a caller-provided Langfuse handler received this tool's
    callbacks. That handler owns the observation; the SDK's silent handler does not.
    """
    if not enabled() or langchain_tool:
        return
    try:
        client = _client()
        trace_context = None
        if not _has_current_span():
            from cuga.backend.cuga_graph.utils.langfuse_tracing import _langfuse_trace_id

            tid = _langfuse_trace_id.get()
            if not tid:
                return  # no trace to attach to: a tool call outside a traced run
            trace_context = {"trace_id": tid}
        obs = client.start_observation(
            trace_context=trace_context,
            name=tool_name,
            as_type="tool",
            input=_cut(arguments),
            output=_cut(result),
            metadata={
                "app": app_name,
                "duration_ms": round(duration_ms, 1) if duration_ms is not None else None,
            },
            level="ERROR" if error is not None else None,
            status_message=_cut(error) if error is not None else None,
        )
        _backdate(obs, duration_ms)
        obs.end()
    except Exception as e:
        logger.debug(f"Langfuse: tool call {tool_name} not recorded: {e}")


def record_event(
    name: str,
    *,
    input: Any = None,
    output: Any = None,
    metadata: Optional[Dict[str, Any]] = None,
    level: Optional[str] = None,
) -> None:
    """A point-in-time event in the current trace (a hook decision, a policy verdict)."""
    if not enabled():
        return
    try:
        client = _client()
        if not _has_current_span():
            return
        client.create_event(
            name=name, input=_cut(input), output=_cut(output), metadata=_cut(metadata), level=level
        )
    except Exception as e:
        logger.debug(f"Langfuse: event {name} not recorded: {e}")


def _backdate(obs: Any, duration_ms: Optional[float]) -> None:
    """Move the start of the observation's span back by the call's duration (the call is recorded after it ends)."""
    if not duration_ms:
        return
    span = getattr(obs, "_otel_span", None)
    if span is not None and hasattr(span, "_start_time"):
        try:
            span._start_time = time.time_ns() - int(duration_ms * 1e6)
        except Exception:
            pass


def flush() -> None:
    """Flush completed observations once at application shutdown, not per run.

    Close streams and leave caller-owned trace contexts first. This is a blocking
    exporter operation; async applications can explicitly await asyncio.to_thread(flush)
    at their shutdown boundary. Long-lived applications rely on batch export.
    """
    try:
        if enabled():
            _client().flush()
    except Exception as e:
        logger.debug(f"Langfuse: flush failed: {e}")
