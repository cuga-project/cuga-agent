"""Detection-only warning for suspect CugaLite tool-call arguments.

Flags when the agent passes an argument whose shape/type looks malformed for
the tool's schema (a dict where a scalar is required, a one-element list, a
stringized number, …). Arguments are ALWAYS forwarded unchanged — this only
logs. Detection-only by design: mining of past eval runs showed the malformed
shapes do not currently cause agent-visible failures, so mutation (auto-
coercion) is not warranted; the warning surfaces a regression in the logs if a
future model or corpus reintroduces them.
"""

from __future__ import annotations

import inspect
import typing
from typing import Any, Callable, Dict, Optional

from loguru import logger
from pydantic import BaseModel

_PY_TO_JSONSCHEMA = {
    str: "string",
    int: "integer",
    float: "number",
    bool: "boolean",
    list: "array",
    dict: "object",
}

_SCALAR_TYPES = ("string", "integer", "number", "boolean")


def jsonschema_type(annotation: Any) -> Optional[str]:
    """Map a pydantic field annotation (possibly ``Optional[...]``) to a
    jsonschema type name, mirroring ``create_tool_from_api_dict``'s mapping."""
    origin = typing.get_origin(annotation)
    if origin is typing.Union:
        non_none = [a for a in typing.get_args(annotation) if a is not type(None)]
        if non_none:
            annotation = non_none[0]
    return _PY_TO_JSONSCHEMA.get(annotation)


def suspect_reason(ptype: Optional[str], value: Any) -> Optional[str]:
    """Return a short human reason if ``value`` looks malformed for a scalar
    parameter of jsonschema type ``ptype``, else ``None``.

    Detects exactly the shapes the old coercion layer used to rewrite:
    a dict/list passed where a scalar is required (the dict-as-string bug and
    its list-wrap sibling) and a stringized number for an integer/number param.
    Correct scalar values never trigger a warning.
    """
    if ptype not in _SCALAR_TYPES:
        return None  # only scalar params exhibit these shape mismatches
    if isinstance(value, dict):
        return f"dict passed where {ptype} expected (dict-as-string)"
    if isinstance(value, list):
        return f"list passed where {ptype} expected"
    # bool is a subclass of int; exclude it from the stringized-number check.
    if isinstance(value, str) and ptype in ("integer", "number"):
        try:
            int(value) if ptype == "integer" else float(value)
        except ValueError:
            return None
        return f"stringized {ptype}"
    return None


def warn_suspect_kwargs(
    kwargs: Dict[str, Any],
    field_types: Dict[str, Optional[str]],
    *,
    model_name: str = "tool",
) -> None:
    """Log a warning for each kwarg that looks malformed for its schema. Pure
    side effect — never mutates ``kwargs``. Keys not in ``field_types`` are
    ignored (unknown/extra args are not ours to judge)."""
    for name, value in kwargs.items():
        ptype = field_types.get(name)
        if ptype is None:
            continue
        reason = suspect_reason(ptype, value)
        if reason:
            logger.warning(
                f"[arg-warning] {model_name}: '{name}' looks malformed — {reason}; "
                f"value={value!r}. Forwarded unchanged (no coercion; see arg_warning.py)."
            )


def make_arg_warning_callable(
    tool_func: Callable[..., Any],
    input_model: Optional[type[BaseModel]],
    *,
    enable: bool,
) -> Callable[..., Any]:
    """Wrap a tool callable so keyword arguments are *inspected* against
    ``input_model`` and any suspect shapes are logged before the call. Arguments
    are forwarded unchanged. Returns ``tool_func`` untouched when disabled or
    when the tool has no input schema.

    ``prepare_tools_and_apps`` routes both async coroutines (``tool.coroutine``)
    and sync callables (``tool.func`` / ``tool._run``) through here. The wrapper
    preserves the sync/async nature of ``tool_func``: an async tool gets an
    async wrapper, a sync tool a sync wrapper. This matters because the result
    is handed to ``make_tool_awaitable`` — wrapping a *sync* callable in an
    ``async def`` would make it await inline on the event loop instead of being
    dispatched to a worker thread via ``run_in_executor``, so a blocking sync
    tool could stall the loop (a silent, default-on behavior change)."""
    if not enable or input_model is None:
        return tool_func

    field_types: Dict[str, Optional[str]] = {
        name: jsonschema_type(f.annotation) for name, f in input_model.model_fields.items()
    }
    model_name = getattr(input_model, "__name__", "tool")

    # Only the kwargs path carries the dict-as-string shapes; positional calls
    # are left alone to avoid mis-reading bound arguments.
    if inspect.iscoroutinefunction(tool_func):

        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            if kwargs and not args:
                warn_suspect_kwargs(kwargs, field_types, model_name=model_name)
            return await tool_func(*args, **kwargs)

        return async_wrapper

    def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
        if kwargs and not args:
            warn_suspect_kwargs(kwargs, field_types, model_name=model_name)
        return tool_func(*args, **kwargs)

    return sync_wrapper
