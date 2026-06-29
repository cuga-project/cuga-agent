"""Detection-only warning for suspect CugaLite tool-call arguments.

History
-------
This module began as a pre-flight *coercion* layer (Wave-1 Change #4) that
rewrote a small set of malformed tool arguments — the "dict-as-string" bug and
friends — into valid values before the call reached the registry.

A 200-task M3 mining pass (bundle ``20260628_162414_default``) found that the
target failure does not occur in the current corpus/model: there were ZERO
registry arg-validation errors in agent execution. Every ``Input validation
error`` string was a *gold-trajectory expectation* surfaced by the scorer
(``Missing expected tool_responses``), not an error the agent actually hit and
failed to recover from. With no failure to fix, Change #4 is WONTFIX and the
coercion behavior was removed.

What remains is the *detector*: a zero-mutation warning that flags when the agent
passes an argument whose shape/type looks malformed for the tool's schema (a dict
where a scalar is required, a one-element list, a stringized number, …).
Arguments are ALWAYS forwarded unchanged — this only logs. It is cheap insurance
that will surface the dict-as-string failure in the logs if a future model or
corpus regresses, without silently altering any call.
"""

from __future__ import annotations

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
    """Wrap an async tool coroutine so keyword arguments are *inspected* against
    ``input_model`` and any suspect shapes are logged before the call. Arguments
    are forwarded unchanged. Returns ``tool_func`` untouched when disabled or
    when the tool has no input schema."""
    if not enable or input_model is None:
        return tool_func

    field_types: Dict[str, Optional[str]] = {
        name: jsonschema_type(f.annotation) for name, f in input_model.model_fields.items()
    }
    model_name = getattr(input_model, "__name__", "tool")

    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        # Only the kwargs path carries the dict-as-string shapes; positional
        # calls are left alone to avoid mis-reading bound arguments.
        if kwargs and not args:
            warn_suspect_kwargs(kwargs, field_types, model_name=model_name)
        return await tool_func(*args, **kwargs)

    return wrapper
