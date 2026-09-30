"""Compact TypeScript-style tool signatures for text the model reads.

A bound tool must travel as JSON Schema (every function-calling provider
requires it), but wherever the model *reads* a tool as text — ``tool_search``
results, the "schema not loaded" hint — a signature is 5-10x smaller and just
as precise::

    hockey_get_games(season: int, order?: "asc" | "desc" = "desc", limit?: int (1..100) = 50) -> Game[]
      Games for a season, newest first.

Rendered from the tool's ``args_schema`` — the same Pydantic model tool_exec
validates with — so the text can never disagree with what is enforced.
See ``docs/design/fc-deferred-tool-search.md`` §3.10 B.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

_PRIMITIVE_NAMES = {
    "string": "string",
    "integer": "int",
    "number": "number",
    "boolean": "boolean",
    "null": "null",
}
_FIRST_SENTENCE = re.compile(r"^(.+?[.!?])(?:\s|$)", re.DOTALL)

DEFAULT_MAX_DEPTH = 2
DEFAULT_SIGNATURE_MAX_CHARS = 600


def first_sentence(text: Optional[str], max_chars: int = 200) -> str:
    """One line: the first sentence, or the first line, capped at ``max_chars``."""
    text = " ".join((text or "").split())
    if not text:
        return ""
    m = _FIRST_SENTENCE.match(text)
    line = m.group(1) if m else text
    if len(line) > max_chars:
        line = line[: max_chars - 1].rstrip() + "…"
    return line


def _literal(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False) if isinstance(value, str) else json.dumps(value)


def _resolve(schema: Any, defs: Dict[str, Any]) -> Any:
    seen = 0
    while isinstance(schema, dict) and "$ref" in schema and seen < 8:
        ref = str(schema["$ref"])
        key = ref.rsplit("/", 1)[-1]
        if key not in defs:
            return {}
        schema = {**defs[key], **{k: v for k, v in schema.items() if k != "$ref"}}
        seen += 1
    return schema


def _range_suffix(schema: Dict[str, Any]) -> str:
    lo = schema.get("minimum", schema.get("exclusiveMinimum"))
    hi = schema.get("maximum", schema.get("exclusiveMaximum"))
    if lo is None and hi is None:
        return ""
    lo_s = "" if lo is None else f"{'>' if 'exclusiveMinimum' in schema else ''}{lo}"
    hi_s = "" if hi is None else f"{'<' if 'exclusiveMaximum' in schema else ''}{hi}"
    return f" ({lo_s}..{hi_s})"


def _length_suffix(schema: Dict[str, Any], lo_key: str, hi_key: str, unit: str) -> str:
    lo, hi = schema.get(lo_key), schema.get(hi_key)
    if lo is None and hi is None:
        return ""
    if lo is not None and hi is not None:
        return f" ({unit} {lo}..{hi})"
    return f" ({unit} ≥{lo})" if lo is not None else f" ({unit} ≤{hi})"


def render_type(
    schema: Any, defs: Optional[Dict[str, Any]] = None, depth: int = 0, max_depth: int = DEFAULT_MAX_DEPTH
) -> str:
    """JSON Schema → compact TS-style type."""
    defs = defs or {}
    schema = _resolve(schema, defs)
    if not isinstance(schema, dict) or not schema:
        return "any"

    if "const" in schema:
        return _literal(schema["const"])
    enum = schema.get("enum")
    if isinstance(enum, list) and enum:
        return " | ".join(_literal(v) for v in enum)

    for key in ("anyOf", "oneOf"):
        variants = schema.get(key)
        if isinstance(variants, list) and variants:
            non_null = [v for v in variants if not (isinstance(v, dict) and v.get("type") == "null")]
            parts = [render_type(v, defs, depth, max_depth) for v in non_null] or ["null"]
            return " | ".join(dict.fromkeys(parts))

    t = schema.get("type")
    if isinstance(t, list):
        non_null = [x for x in t if x != "null"]
        if len(non_null) == 1:
            return render_type({**schema, "type": non_null[0]}, defs, depth, max_depth)
        return " | ".join(_PRIMITIVE_NAMES.get(x, x) for x in non_null) or "any"

    if t == "array" or "items" in schema:
        item = render_type(schema.get("items") or {}, defs, depth + 1, max_depth)
        if " | " in item:
            item = f"({item})"
        return f"{item}[]" + _length_suffix(schema, "minItems", "maxItems", "items")

    if t == "object" or "properties" in schema:
        props = schema.get("properties")
        if not isinstance(props, dict) or not props:
            addl = schema.get("additionalProperties")
            if isinstance(addl, dict) and addl:
                return f"{{[key: string]: {render_type(addl, defs, depth + 1, max_depth)}}}"
            return "object"
        if depth >= max_depth:
            return "{…}"
        required = set(schema.get("required") or [])
        fields = [
            f"{name}{'' if name in required else '?'}: {render_type(sub, defs, depth + 1, max_depth)}"
            for name, sub in props.items()
        ]
        return "{" + ", ".join(fields) + "}"

    if t in _PRIMITIVE_NAMES:
        base = _PRIMITIVE_NAMES[t]
        fmt = schema.get("format")
        if t == "string" and isinstance(fmt, str) and fmt:
            base = f"string<{fmt}>"
        if t in ("integer", "number"):
            return base + _range_suffix(schema)
        if t == "string":
            return base + _length_suffix(schema, "minLength", "maxLength", "len")
        return base
    return "any"


def _looks_like_json_schema(value: Any) -> bool:
    return isinstance(value, dict) and (
        isinstance(value.get("type"), (str, list))
        or any(k in value for k in ("properties", "items", "anyOf", "oneOf", "$ref", "enum"))
    )


def render_shape(value: Any, depth: int = 0, max_depth: int = DEFAULT_MAX_DEPTH) -> str:
    """Registry response *summaries* / example payloads → TS-style type.

    ``openapi_parser_v0`` summarizes responses as ``{field: "string", ...}`` or
    as an example payload; primitive-name strings are types, other values are
    typed by their Python type.
    """
    if _looks_like_json_schema(value):
        return render_type(value, {}, depth, max_depth)
    if isinstance(value, str):
        return _PRIMITIVE_NAMES.get(value, "string")
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "number"
    if value is None:
        return "null"
    if isinstance(value, list):
        return f"{render_shape(value[0], depth + 1, max_depth)}[]" if value else "any[]"
    if isinstance(value, dict):
        if not value:
            return "object"
        if depth >= max_depth:
            return "{…}"
        return (
            "{" + ", ".join(f"{k}: {render_shape(v, depth + 1, max_depth)}" for k, v in value.items()) + "}"
        )
    return "any"


def _default_suffix(schema: Dict[str, Any]) -> str:
    if "default" not in schema or schema["default"] is None:
        return ""
    return f" = {_literal(schema['default'])}"


def render_params(args_schema: Any) -> str:
    """``p: T, q?: T = d`` from a Pydantic model class or a JSON-Schema dict."""
    schema = _json_schema_of(args_schema)
    if not schema:
        return ""
    defs = schema.get("$defs") or schema.get("definitions") or {}
    required = set(schema.get("required") or [])
    parts: List[str] = []
    for name, sub in (schema.get("properties") or {}).items():
        sub = _resolve(sub, defs) if isinstance(sub, dict) else {}
        mark = "" if name in required else "?"
        parts.append(f"{name}{mark}: {render_type(sub, defs)}{_default_suffix(sub)}")
    return ", ".join(parts)


def _json_schema_of(args_schema: Any) -> Dict[str, Any]:
    if isinstance(args_schema, dict):
        return args_schema
    for attr in ("model_json_schema", "schema"):
        fn = getattr(args_schema, attr, None)
        if callable(fn):
            try:
                out = fn()
                if isinstance(out, dict):
                    return out
            except Exception:
                continue
    return {}


def response_type(tool: Any) -> Optional[str]:
    """Rendered ``success`` response type, or ``None`` when the tool declares none."""
    func = getattr(tool, "func", None) or getattr(tool, "coroutine", None)
    schemas = getattr(func, "_response_schemas", None)
    if not isinstance(schemas, dict) or "success" not in schemas or schemas.get("_synthetic_placeholder"):
        return None
    success = schemas["success"]
    if isinstance(success, list):
        return f"{render_shape(success[0])}[]" if success else "any[]"
    return render_shape(success)


def render_signature(
    tool: Any,
    *,
    max_chars: int = DEFAULT_SIGNATURE_MAX_CHARS,
    include_description: bool = True,
    display_name: Optional[str] = None,
) -> str:
    """``name(params) -> Return`` plus a one-line description, within ``max_chars``.

    The description is indented on its own line. When the signature line itself
    would exceed the budget, the return type is truncated first, then params.
    """
    name = display_name or getattr(tool, "name", "") or ""
    params = render_params(getattr(tool, "args_schema", None))
    ret = response_type(tool)
    head = f"{name}({params})"
    line = f"{head} -> {ret}" if ret else head
    if len(line) > max_chars:
        line = head if len(head) <= max_chars else head[: max_chars - 2].rstrip() + "…)"
        if ret and len(head) + 8 < max_chars:
            room = max_chars - len(head) - 4
            line = f"{head} -> {ret[: room - 1]}…"
    if not include_description:
        return line
    desc = first_sentence(getattr(tool, "description", ""), max_chars=200)
    return f"{line}\n  {desc}" if desc else line
