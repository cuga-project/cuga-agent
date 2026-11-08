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

import os
import time
from contextlib import contextmanager, nullcontext
from typing import Any, Dict, Iterator, Optional

from loguru import logger

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


def caller_has_trace(config: Optional[Dict[str, Any]]) -> bool:
    """The caller already traces this run (its own handler or a trace id)."""
    if not config:
        return False
    configurable = config.get("configurable") or {}
    if configurable.get("langfuse_trace_id"):
        return True
    from cuga.backend.cuga_graph.utils.langfuse_tracing import collect_langfuse_callbacks_from_config

    return bool(collect_langfuse_callbacks_from_config(config))


class RunTrace:
    """The root observation of one SDK run; ``end`` sets its output."""

    def __init__(self, span: Any = None, nested: bool = False):
        self.span = span
        self.nested = nested

    def end(
        self, output: Any = None, *, error: Optional[str] = None, metadata: Optional[Dict[str, Any]] = None
    ) -> None:
        if self.span is None:
            return
        try:
            self.span.update(
                output=_cut(output),
                metadata=_cut(metadata) if metadata else None,
                level="ERROR" if error else None,
                status_message=error,
            )
            if not self.nested:
                self.span.set_trace_io(output=_cut(output))
        except Exception as e:
            logger.debug(f"Langfuse: run output not recorded: {e}")


@contextmanager
def run_trace(
    name: str, message: Any, thread_id: Optional[str], config: Dict[str, Any]
) -> Iterator[RunTrace]:
    """Open the root observation of an SDK run and add a Langfuse callback handler to ``config["callbacks"]``.

    No-op (yields an empty ``RunTrace``) when tracing is off or the caller traces the run itself.
    """
    if not enabled() or caller_has_trace(config):
        yield RunTrace()
        return
    opts = dict((config.get("configurable") or {}).get("langfuse") or {})
    try:
        from langfuse import propagate_attributes
        from langfuse.langchain import CallbackHandler

        client = _client()
        nested = bool(
            client.get_current_trace_id()
        )  # inside a traced run (a supervisor's sub-agent): keep its trace
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
        span_cm = client.start_as_current_observation(
            name=name, as_type="agent", input=_message_text(message)
        )
    except Exception as e:
        logger.debug(f"Langfuse: trace not started: {e}")
        yield RunTrace()
        return

    with attrs, span_cm as span:
        trace = RunTrace(span, nested=nested)
        try:
            if not nested:
                span.set_trace_io(input=_message_text(message))
            callbacks = config.get("callbacks")
            callbacks = list(callbacks) if isinstance(callbacks, list) else ([callbacks] if callbacks else [])
            config["callbacks"] = callbacks + [_handler_class(CallbackHandler)()]
        except Exception as e:
            logger.debug(f"Langfuse: callback handler not added: {e}")
        try:
            yield trace
        except BaseException as e:
            trace.end(error=f"{type(e).__name__}: {e}")
            raise
        finally:
            flush()


def _handler_class(base: type) -> type:
    """The LangChain handler without its tool runs: ``record_tool_call`` records every tool call once, for every tool
    provider (registry, MCP, direct LangChain tools), with the app and the duration."""

    class CugaLangfuseHandler(base):  # type: ignore[misc,valid-type]
        def on_tool_start(self, *args: Any, **kwargs: Any) -> None:
            return None

        def on_tool_end(self, *args: Any, **kwargs: Any) -> None:
            return None

        def on_tool_error(self, *args: Any, **kwargs: Any) -> None:
            return None

    return CugaLangfuseHandler


def final_answer(state: Any) -> Any:
    """The ``final_answer`` in a streamed state (a dict, or a tuple of namespace and update), else the state."""
    stack = [state]
    while stack:
        item = stack.pop()
        if isinstance(item, dict):
            if item.get("final_answer"):
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

    ``langchain_tool``: the call ran through a LangChain tool, so a caller's own LangChain handler (a caller trace) has
    already recorded it; it is recorded here only under CUGA's handler, which leaves tool runs to this function.
    """
    if not enabled():
        return
    try:
        client = _client()
        trace_context = None
        if not client.get_current_trace_id():
            from cuga.backend.cuga_graph.utils.langfuse_tracing import _langfuse_trace_id

            tid = _langfuse_trace_id.get()
            if not tid:
                return  # no trace to attach to: a tool call outside a traced run
            trace_context = {"trace_id": tid}
            if langchain_tool:
                return
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
            level="ERROR" if error else None,
            status_message=_cut(error) if error else None,
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
        if not client.get_current_trace_id():
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
    try:
        if enabled():
            _client().flush()
    except Exception as e:
        logger.debug(f"Langfuse: flush failed: {e}")
