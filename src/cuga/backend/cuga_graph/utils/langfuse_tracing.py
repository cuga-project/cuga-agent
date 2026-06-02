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


def sync_langfuse_callbacks_from_config(config: Any = None) -> None:
    """Copy callbacks from LangGraph config into the current async context."""
    set_langfuse_callbacks(collect_langfuse_callbacks_from_config(config))


def get_langfuse_callbacks() -> list[Any]:
    """Return callbacks previously set for this async context (may be empty)."""
    return list(_langfuse_callbacks.get() or [])


def get_langfuse_invoke_config() -> dict[str, Any]:
    """LangChain ``config`` dict for nested ``ainvoke`` calls, or empty."""
    callbacks = get_langfuse_callbacks()
    return {"callbacks": callbacks} if callbacks else {}


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
    if name not in ("CallbackHandler", "LangchainCallbackHandler"):
        return False
    for handler_cls in _langfuse_handler_classes():
        if isinstance(cb, handler_cls):
            return True
    mod = getattr(type(cb), "__module__", "") or ""
    return "langfuse" in mod
