"""Pre-flight argument coercion + validation for CugaLite tool calls.

Background
----------
In the CugaLite codeact path the sandbox calls a tool's raw coroutine directly
(see ``prepare_node.py`` which extracts ``tool.coroutine``), bypassing the
``StructuredTool``'s pydantic ``args_schema``. Nothing validates kwargs before
they reach the registry, so the MCP server's jsonschema is the first and only
validator and rejects bad calls with a string the model often fails to recover
from. The dominant real failure is the "dict-as-string" bug: the model passes
the whole result dict of a prior tool call where a scalar is required.

This module re-introduces the schema at that exact bypass point. Given a tool's
pydantic ``InputModel`` it coerces a small set of unambiguous mistakes back into
valid arguments *before* the call, then validates and forwards the
pydantic-coerced values (which also closes the one client/server parity gap:
stringized numbers like ``"2010"`` that pydantic coerces but the server rejects).

Coercion rules (validated live against the M3 disney registry)
-------------------------------------------------------------
* ``R1`` single-key dict whose key == the param name -> its value. (Safe: this
  is the real dict-as-string bug, e.g. ``director={'director': 'X'}`` -> ``'X'``.)
* ``R3`` one-element list -> the element. (Safe.)
* ``R5`` stringized number for an integer/number param -> the number. (Safe;
  mirrors what the server would have wanted.)
* ``R2`` single-key dict whose key != the param name but holds a single scalar
  -> that scalar. (Guesser: it assumes the lone value is the intended argument.
  Off by default; when off it shadow-logs what it *would* have done so its real
  frequency/hit-rate can be measured before it is trusted.)

``None`` and missing-required cannot be coerced (no value exists) and are left
untouched for the server to reject / the recovery directive to handle.
"""

from __future__ import annotations

import typing
from typing import Any, Callable, Dict, NamedTuple, Optional

from loguru import logger
from pydantic import BaseModel, ValidationError

_SENTINEL = object()

_PY_TO_JSONSCHEMA = {
    str: "string",
    int: "integer",
    float: "number",
    bool: "boolean",
    list: "array",
    dict: "object",
}


class Coercion(NamedTuple):
    """Outcome of attempting to coerce a single argument value."""

    rule: Optional[str]  # name of the rule that matched, or None
    value: Any  # the (possibly) replacement value
    applied: bool  # True -> caller should replace the original value
    shadow: bool  # True -> a rule matched but was withheld (R2 disabled)


def jsonschema_type(annotation: Any) -> Optional[str]:
    """Map a pydantic field annotation (possibly ``Optional[...]``) to a
    jsonschema type name, mirroring ``create_tool_from_api_dict``'s mapping."""
    origin = typing.get_origin(annotation)
    if origin is typing.Union:
        non_none = [a for a in typing.get_args(annotation) if a is not type(None)]
        if non_none:
            annotation = non_none[0]
    return _PY_TO_JSONSCHEMA.get(annotation)


def coerce_value(
    param: str,
    ptype: Optional[str],
    value: Any,
    *,
    enable_dict_scalar: bool,
) -> Coercion:
    """Decide how (if at all) to coerce ``value`` for parameter ``param``.

    Rule precedence: R1 (key match) > R3 (list) > R5 (numeric string) > R2
    (single-scalar dict, the guesser). R1 takes precedence over R2 so a
    key-matching dict is never treated as a guess.
    """
    # R1: single-key dict whose key == param name.
    if isinstance(value, dict) and len(value) == 1 and param in value:
        return Coercion("R1_dict_key_eq_param", value[param], True, False)

    # R3: one-element list -> element.
    if isinstance(value, list) and len(value) == 1:
        return Coercion("R3_list_unwrap", value[0], True, False)

    # R5: stringized number for an integer/number param.
    if isinstance(value, str) and ptype in ("integer", "number"):
        try:
            num = int(value) if ptype == "integer" else float(value)
            return Coercion("R5_str_to_num", num, True, False)
        except ValueError:
            pass

    # R2 (guesser): single-key dict, key != param, lone scalar value.
    if isinstance(value, dict) and len(value) == 1:
        lone = next(iter(value.values()))
        if isinstance(lone, (str, int, float, bool)):
            if enable_dict_scalar:
                return Coercion("R2_dict_single_scalar", lone, True, False)
            return Coercion("R2_dict_single_scalar", lone, False, True)

    return Coercion(None, value, False, False)


def coerce_kwargs(
    kwargs: Dict[str, Any],
    field_types: Dict[str, Optional[str]],
    *,
    enable_dict_scalar: bool,
    model_name: str = "tool",
) -> Dict[str, Any]:
    """Apply coercion rules to ``kwargs`` in place-safe fashion, logging applied
    and shadow (R2-withheld) coercions. Keys not in ``field_types`` are left
    untouched. Returns a new dict."""
    out = dict(kwargs)
    for name in list(out.keys()):
        if name not in field_types:
            continue
        res = coerce_value(name, field_types[name], out[name], enable_dict_scalar=enable_dict_scalar)
        if res.applied:
            logger.info(f"[arg-coerce] {model_name}: '{name}' coerced via {res.rule} -> {res.value!r}")
            out[name] = res.value
        elif res.shadow:
            logger.info(
                f"[arg-coerce-shadow] {model_name}: '{name}' {res.rule} would unwrap "
                f"-> {res.value!r} (NOT applied; R2 disabled)"
            )
    return out


def make_coercing_callable(
    tool_func: Callable[..., Any],
    input_model: Optional[type[BaseModel]],
    *,
    enable: bool,
    enable_dict_scalar: bool,
) -> Callable[..., Any]:
    """Wrap an async tool coroutine so keyword arguments are coerced and
    validated against ``input_model`` before the call.

    On successful validation the pydantic-coerced values are forwarded for known
    fields (this fixes the stringized-number parity gap); unknown/extra keys and
    positional args are passed through untouched. On validation failure the
    best-effort coerced kwargs are forwarded so the server returns its own
    validation error (preserving today's behavior for uncoercible cases such as
    ``None`` / missing-required). Returns ``tool_func`` unchanged when disabled.
    """
    if not enable or input_model is None:
        return tool_func

    field_types: Dict[str, Optional[str]] = {
        name: jsonschema_type(f.annotation) for name, f in input_model.model_fields.items()
    }
    model_name = getattr(input_model, "__name__", "tool")

    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        # Only the kwargs path carries the dict-as-string failures; positional
        # calls are forwarded untouched to avoid mis-binding arguments.
        if args or not kwargs:
            return await tool_func(*args, **kwargs)

        coerced = coerce_kwargs(
            kwargs, field_types, enable_dict_scalar=enable_dict_scalar, model_name=model_name
        )

        final = dict(coerced)
        try:
            validated = input_model(**coerced)
            known = set(coerced.keys()) & set(field_types.keys())
            final.update(validated.model_dump(include=known))
        except ValidationError:
            pass  # forward best-effort coerced kwargs; server reports the error

        return await tool_func(**final)

    return wrapper
