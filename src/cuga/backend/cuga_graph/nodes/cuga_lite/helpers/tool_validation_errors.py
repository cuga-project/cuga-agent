"""Readable tool-argument validation errors, grouped the way Claude Code's
``formatZodValidationError`` groups Zod issues (free-code ``utils/toolErrors.ts``).

Pydantic's ``str(ValidationError)`` is written for Python developers ("1
validation error for XInput / limit / Input should be a valid integer, unable to
parse string as an integer [type=int_parsing, input_value='fifty', ...]"). The
model needs the same facts as a correction: what is missing, what is not a
parameter at all (and what the valid ones are), and which values have the wrong
type or fall outside what the schema allows.
"""

from __future__ import annotations

import json
from typing import Any, Dict, Iterable, List, Optional, Sequence

from pydantic import ValidationError

_MAX_RECEIVED_CHARS = 60

_TYPE_NAMES = {
    "int": "integer",
    "float": "number",
    "string": "string",
    "str": "string",
    "bool": "boolean",
    "list": "array",
    "tuple": "array",
    "set": "array",
    "dict": "object",
    "model": "object",
    "model_attributes": "object",
}

_RANGE_OPS = {
    "greater_than_equal": (">=", "ge"),
    "greater_than": (">", "gt"),
    "less_than_equal": ("<=", "le"),
    "less_than": ("<", "lt"),
}


def _path(loc: Sequence[Any]) -> str:
    out = ""
    for part in loc:
        if isinstance(part, int):
            out += f"[{part}]"
        elif out:
            out += f".{part}"
        else:
            out = str(part)
    return out or "(arguments)"


def _json_type(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, (list, tuple)):
        return "array"
    if isinstance(value, dict):
        return "object"
    return type(value).__name__


def _received(value: Any) -> str:
    try:
        text = json.dumps(value, ensure_ascii=False, default=str)
    except Exception:
        text = repr(value)
    if len(text) > _MAX_RECEIVED_CHARS:
        text = text[: _MAX_RECEIVED_CHARS - 1] + "…"
    return f"{_json_type(value)} {text}"


def _expected_type(error_type: str) -> Optional[str]:
    for suffix in ("_parsing", "_type"):
        if error_type.endswith(suffix):
            return _TYPE_NAMES.get(error_type[: -len(suffix)])
    return None


def _literal_choices(ctx: Dict[str, Any]) -> str:
    expected = str(ctx.get("expected", ""))
    # Pydantic renders "'asc' or 'desc'" / "'a', 'b' or 'c'"; normalize to a union.
    return expected.replace("' or '", "' | '").replace("', '", "' | '").replace("'", '"')


def format_validation_error(
    tool_name: str,
    error: ValidationError,
    valid_parameters: Optional[Iterable[str]] = None,
) -> str:
    """Render a Pydantic ``ValidationError`` as a model-facing correction."""
    missing: List[str] = []
    unexpected: List[str] = []
    type_mismatch: List[str] = []
    not_allowed: List[str] = []
    other: List[str] = []

    for issue in error.errors(include_url=False):
        error_type = issue.get("type", "")
        loc = issue.get("loc", ())
        path = _path(loc)
        ctx = issue.get("ctx") or {}
        value = issue.get("input")

        if error_type == "missing":
            missing.append(path)
        elif error_type == "extra_forbidden":
            unexpected.append(path)
        elif error_type in ("literal_error", "enum"):
            not_allowed.append(f"{path} must be one of {_literal_choices(ctx)}, received {_received(value)}")
        elif error_type in _RANGE_OPS:
            op, key = _RANGE_OPS[error_type]
            not_allowed.append(f"{path} must be {op} {ctx.get(key)}, received {_received(value)}")
        elif error_type in ("too_short", "too_long", "string_too_short", "string_too_long"):
            shorter = error_type.endswith("too_short")
            bound = "at least" if shorter else "at most"
            limit = ctx.get("min_length") if shorter else ctx.get("max_length")
            unit = "characters" if error_type.startswith("string_") else "items"
            not_allowed.append(f"{path} must have {bound} {limit} {unit}, received {_received(value)}")
        elif error_type == "string_pattern_mismatch":
            not_allowed.append(f"{path} must match pattern {ctx.get('pattern')}, received {_received(value)}")
        elif error_type == "multiple_of":
            not_allowed.append(
                f"{path} must be a multiple of {ctx.get('multiple_of')}, received {_received(value)}"
            )
        elif (expected := _expected_type(error_type)) is not None:
            type_mismatch.append(f"{path} expected {expected}, received {_received(value)}")
        else:
            other.append(f"{path}: {issue.get('msg', error_type)}")

    lines = [f"Invalid arguments for {tool_name}:"]
    if missing:
        lines.append(f"- missing required: {', '.join(missing)}")
    if unexpected:
        valid = sorted(valid_parameters or [])
        hint = f"  (valid parameters: {', '.join(valid)})" if valid else ""
        lines.append(f"- unexpected: {', '.join(unexpected)}{hint}")
    lines.extend(f"- type mismatch: {item}" for item in type_mismatch)
    lines.extend(f"- not allowed: {item}" for item in not_allowed)
    lines.extend(f"- {item}" for item in other)
    lines.append(f"Fix the arguments and call {tool_name} again.")
    return "\n".join(lines)


def format_any_validation_error(
    tool_name: str,
    error: BaseException,
    valid_parameters: Optional[Iterable[str]] = None,
) -> str:
    """``format_validation_error`` for Pydantic errors; a plain line for anything else."""
    if isinstance(error, ValidationError):
        return format_validation_error(tool_name, error, valid_parameters)
    return f"Tool input validation error for {tool_name}: {error}"
