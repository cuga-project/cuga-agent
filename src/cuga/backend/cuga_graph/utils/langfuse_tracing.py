"""Thread-local Langfuse callback propagation for nested LLM calls.

CugaLite and policy enactment run LLM calls outside the main ``call_model``
path (reflection, find_tools shortlister, NL auto-continue, output formatter,
context summarization). Those calls need the same trace-scoped Langfuse
``CallbackHandler`` as the parent ``agent.invoke`` so Langfuse shows one trace
per logical run instead of many sibling root traces.
"""

from __future__ import annotations

import importlib
from contextvars import ContextVar
from typing import Any, Optional

_LANGFUSE_HANDLER_CLASSES: tuple[type, ...] | None = None

_langfuse_callbacks: ContextVar[Optional[list[Any]]] = ContextVar("langfuse_callbacks", default=None)
_langfuse_trace_id: ContextVar[Optional[str]] = ContextVar("langfuse_trace_id", default=None)
_langfuse_primary_handler: ContextVar[Optional[Any]] = ContextVar("langfuse_primary_handler", default=None)
_langgraph_run_config: ContextVar[Optional[Any]] = ContextVar("langgraph_run_config", default=None)
_TRACE_SCOPED_HANDLER_CLASS: type | None = None


def _flatten_callbacks(value: Any) -> list[Any]:
    """Expand LangGraph/LangChain callback managers into handler instances."""
    if value is None:
        return []
    handlers = getattr(value, "handlers", None)
    if handlers is not None and not isinstance(value, (list, tuple)):
        return list(handlers)
    if isinstance(value, (list, tuple)):
        out: list[Any] = []
        for item in value:
            out.extend(_flatten_callbacks(item))
        return out
    return [value]


def collect_langfuse_callbacks_from_config(config: Any = None) -> list[Any]:
    """Return merged Langfuse callback handlers from a LangGraph ``config`` dict."""
    if not config or not hasattr(config, "get"):
        return []
    configurable = config.get("configurable") or {}
    merged: list[Any] = []
    seen: set[int] = set()
    for source in (config.get("callbacks"), configurable.get("callbacks")):
        for cb in _flatten_callbacks(source):
            if not is_langfuse_callback_handler(cb):
                continue
            key = id(cb)
            if key not in seen:
                seen.add(key)
                merged.append(cb)
    return merged


def set_langfuse_callbacks(callbacks: Optional[list[Any]]) -> None:
    """Store LangChain callbacks for the current async context."""
    _langfuse_callbacks.set(list(callbacks) if callbacks else None)


def set_langfuse_trace_id(trace_id: Optional[str]) -> None:
    """Remember the eval harness trace id for nested calls in this async context."""
    _langfuse_trace_id.set(trace_id)


def _trace_context_for_id(trace_id: str, *, parent_span_id: str | None = None) -> dict[str, str]:
    trace_context: dict[str, str] = {"trace_id": trace_id}
    if parent_span_id:
        trace_context["parent_span_id"] = parent_span_id
    return trace_context


def _handler_trace_id(handler: Any) -> str | None:
    ctx = getattr(handler, "_trace_context", None)
    if isinstance(ctx, dict):
        tid = ctx.get("trace_id")
        return str(tid) if tid else None
    return None


def _trace_scoped_handler_class() -> type | None:
    """Langfuse handler that always links orphan LLM runs to the eval trace id."""
    global _TRACE_SCOPED_HANDLER_CLASS
    if _TRACE_SCOPED_HANDLER_CLASS is not None:
        return _TRACE_SCOPED_HANDLER_CLASS

    base_classes = _langfuse_handler_classes()
    if not base_classes:
        return None

    base_cls = base_classes[0]

    class TraceScopedLangfuseCallbackHandler(base_cls):  # type: ignore[misc,valid-type]
        # NOTE: the real handler dispatches on_llm_start/on_chat_model_start to a
        # name-mangled private method (``__on_llm_action``). Overriding that name
        # here would itself be mangled to this class's own name and never get
        # called by the base class, so we override the public entry points
        # instead and temporarily swap ``_trace_context`` around the super() call.
        def _scoped_trace_context(self, parent_run_id: Any) -> dict[str, str] | None:
            trace_ctx = getattr(self, "_trace_context", None)
            if trace_ctx is None:
                tid = _langfuse_trace_id.get()
                if tid:
                    trace_ctx = _trace_context_for_id(tid)
            parent_missing = parent_run_id is None or parent_run_id not in getattr(self, "_runs", {})
            return trace_ctx if (trace_ctx is not None and parent_missing) else None

        def on_llm_start(self, serialized: Any, prompts: Any, *, parent_run_id: Any = None, **kwargs: Any):
            scoped = self._scoped_trace_context(parent_run_id)
            if scoped is None:
                return super().on_llm_start(serialized, prompts, parent_run_id=parent_run_id, **kwargs)
            original = self._trace_context
            self._trace_context = scoped
            try:
                return super().on_llm_start(serialized, prompts, parent_run_id=parent_run_id, **kwargs)
            finally:
                self._trace_context = original

        def on_chat_model_start(
            self, serialized: Any, messages: Any, *, parent_run_id: Any = None, **kwargs: Any
        ):
            scoped = self._scoped_trace_context(parent_run_id)
            if scoped is None:
                return super().on_chat_model_start(
                    serialized, messages, parent_run_id=parent_run_id, **kwargs
                )
            original = self._trace_context
            self._trace_context = scoped
            try:
                return super().on_chat_model_start(
                    serialized, messages, parent_run_id=parent_run_id, **kwargs
                )
            finally:
                self._trace_context = original

    TraceScopedLangfuseCallbackHandler.__name__ = "TraceScopedLangfuseCallbackHandler"
    _TRACE_SCOPED_HANDLER_CLASS = TraceScopedLangfuseCallbackHandler
    return _TRACE_SCOPED_HANDLER_CLASS


def create_trace_langfuse_handler(trace_id: str, *, parent_span_id: str | None = None) -> Any | None:
    """Return a Langfuse handler scoped to *trace_id* (one trace per eval task)."""
    handler_cls = _trace_scoped_handler_class()
    if handler_cls is None:
        return None
    return handler_cls(trace_context=_trace_context_for_id(trace_id, parent_span_id=parent_span_id))


def _remember_primary_handler(callbacks: list[Any]) -> None:
    for cb in callbacks:
        if is_langfuse_callback_handler(cb):
            _langfuse_primary_handler.set(cb)
            return


def _primary_handler_for_trace(trace_id: str) -> Any | None:
    handler = _langfuse_primary_handler.get()
    if handler is not None and _handler_trace_id(handler) == trace_id:
        return handler
    return None


def sync_langfuse_callbacks_from_config(config: Any = None) -> None:
    """Copy Langfuse trace id, handlers, and RunnableConfig into this async context."""
    if not config or not hasattr(config, "get"):
        return
    _langgraph_run_config.set(config)
    configurable = config.get("configurable") or {}
    trace_id = configurable.get("langfuse_trace_id") or None
    set_langfuse_trace_id(trace_id)
    callbacks = collect_langfuse_callbacks_from_config(config)
    if callbacks:
        _remember_primary_handler(callbacks)
    elif trace_id:
        handler = _primary_handler_for_trace(trace_id) or create_trace_langfuse_handler(trace_id)
        if handler:
            _langfuse_primary_handler.set(handler)
            callbacks = [handler]
    else:
        _langfuse_primary_handler.set(None)
    set_langfuse_callbacks(callbacks or None)


def get_langfuse_callbacks() -> list[Any]:
    """Return callbacks previously set for this async context (may be empty)."""
    return list(_langfuse_callbacks.get() or [])


def nested_langgraph_invoke_config(run_config: Any = None) -> dict[str, Any]:
    """Prefer explicit or context-synced ``RunnableConfig``; fall back to callbacks-only dict."""
    if run_config is not None and hasattr(run_config, "get"):
        return run_config
    ctx_config = _langgraph_run_config.get()
    if ctx_config is not None and hasattr(ctx_config, "get"):
        return ctx_config
    return get_langfuse_invoke_config()


def get_langfuse_invoke_config() -> dict[str, Any]:
    """LangChain ``config`` for nested ``ainvoke`` (find_tools, reflection, etc.).

    Reuse handlers synced from the parent ``agent.invoke`` config when present so
    repeated shortlister calls share one trace-scoped handler. Otherwise build
    from ``langfuse_trace_id`` (sandbox paths that only receive the trace id).
    """
    callbacks = [cb for cb in get_langfuse_callbacks() if is_langfuse_callback_handler(cb)]
    if callbacks:
        return {"callbacks": callbacks}
    trace_id = _langfuse_trace_id.get()
    if trace_id:
        handler = _primary_handler_for_trace(trace_id) or create_trace_langfuse_handler(trace_id)
        if handler:
            _langfuse_primary_handler.set(handler)
            return {"callbacks": [handler]}
    return {}


def _langfuse_handler_classes() -> tuple[type, ...]:
    global _LANGFUSE_HANDLER_CLASSES
    if _LANGFUSE_HANDLER_CLASSES is not None:
        return _LANGFUSE_HANDLER_CLASSES
    classes: list[type] = []
    for module_name, class_name in (
        ("langfuse.langchain", "CallbackHandler"),
        ("langfuse.callback", "CallbackHandler"),
    ):
        try:
            mod = importlib.import_module(module_name)
            cls = getattr(mod, class_name, None)
            if isinstance(cls, type):
                classes.append(cls)
        except ImportError:
            continue
    _LANGFUSE_HANDLER_CLASSES = tuple(classes)
    return _LANGFUSE_HANDLER_CLASSES


def is_langfuse_callback_handler(cb: Any) -> bool:
    """True if *cb* is a Langfuse LangChain callback handler."""
    name = type(cb).__name__
    if name not in ("CallbackHandler", "LangchainCallbackHandler", "TraceScopedLangfuseCallbackHandler"):
        return False
    for handler_cls in _langfuse_handler_classes():
        if isinstance(cb, handler_cls):
            return True
    mod = getattr(type(cb), "__module__", "") or ""
    return "langfuse" in mod
