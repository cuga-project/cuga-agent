"""Faithful JSON-Schema → Pydantic input models for registry tools.

Pydantic is CUGA's Zod: one model per tool is the wire schema (``bind_tools``
derives it), the validator (``model_validate``) and the error vocabulary. The
flat mapping in ``schema_utils.python_type_for_schema`` keeps only the top-level
type, so enums, array item types, nested object fields and numeric/length
bounds never reach either the model or the validator. This module keeps them.

Registry tools arrive in the registry's parameter-list shape
(``{name, type, description, required, default, constraints, schema}``) in which
enums and bounds survive only as the constraint strings
``openapi_parser_v0._format_constraints`` writes ("must be one of: [a, b]",
">= 1", "length <= 5", ...). ``params_to_json_schema`` parses those back into
JSON-Schema keywords; ``build_input_model`` then builds the model recursively.

Fail-open throughout: ambiguous unions and untyped values stay ``Any``, a
constraint that cannot be parsed is dropped, and a tool whose model cannot be
built falls back to the legacy flat model — never to a registration failure.
See ``docs/design/fc-deferred-tool-search.md`` §3.9.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Literal, Optional, Tuple

from loguru import logger
from pydantic import BaseModel, ConfigDict, Field, create_model
from typing_extensions import Annotated

from cuga.backend.tools_env.registry.utils.schema_utils import (
    json_schema_type,
    python_type_for_schema,
    schema_type_is_ambiguous,
)

VALIDATION_MODE_OFF = "off"
VALIDATION_MODE_LAX = "lax"
VALIDATION_MODE_STRICT = "strict"
_VALIDATION_MODES = (VALIDATION_MODE_OFF, VALIDATION_MODE_LAX, VALIDATION_MODE_STRICT)

# Nested objects deeper than this validate as plain ``dict``.
MAX_NESTING_DEPTH = 3

_PRIMITIVES = {"string": str, "integer": int, "number": float, "boolean": bool}

_ENUM_RE = re.compile(r"^must be one of: \[(.*)\]$", re.DOTALL)
_BOUND_RE = re.compile(r"^(>=|>|<=|<)\s*(-?\d+(?:\.\d+)?)$")
_LENGTH_RE = re.compile(r"^length\s*(>=|<=)\s*(\d+)$")
_ITEMS_RE = re.compile(r"^(min|max) items:\s*(\d+)$")
_PATTERN_RE = re.compile(r"^matches pattern:\s*(.+)$", re.DOTALL)
_FORMAT_RE = re.compile(r"^format:\s*(\S+)$")
_MULTIPLE_RE = re.compile(r"^multiple of\s*(-?\d+(?:\.\d+)?)$")

_BOUND_KEYS = {">=": "minimum", ">": "exclusiveMinimum", "<=": "maximum", "<": "exclusiveMaximum"}


def resolve_validation_mode(raw: Any) -> str:
    """Normalize ``cuga_lite_tool_validation``; unknown values fall back to ``lax``."""
    key = str(raw or "").strip().lower()
    if key in _VALIDATION_MODES:
        return key
    if key:
        logger.warning("Unknown cuga_lite_tool_validation {!r}; using 'lax'", raw)
    return VALIDATION_MODE_LAX


def validation_mode_from_settings() -> str:
    from cuga.config import settings

    return resolve_validation_mode(getattr(settings.advanced_features, "cuga_lite_tool_validation", "lax"))


# ── Constraint strings → JSON-Schema keywords ───────────────────────────────


def _number(text: str) -> Any:
    return float(text) if "." in text else int(text)


def _coerce_enum_value(raw: str, json_type: str) -> Any:
    value = raw.strip()
    if json_type == "integer":
        return int(value)
    if json_type == "number":
        return float(value)
    if json_type == "boolean":
        lowered = value.lower()
        if lowered in ("true", "false"):
            return lowered == "true"
        raise ValueError(value)
    return value


def constraints_to_keywords(constraints: List[Any], json_type: str) -> Dict[str, Any]:
    """Parse ``_format_constraints`` strings back into JSON-Schema keywords.

    Unparseable strings are skipped. Enum values are split on ``", "`` — the
    joiner the registry used — and coerced to ``json_type``; if any value fails
    to coerce the whole enum is dropped rather than half-applied.
    """
    out: Dict[str, Any] = {}
    for item in constraints or []:
        if not isinstance(item, str):
            continue
        text = item.strip()
        if m := _ENUM_RE.match(text):
            body = m.group(1)
            if not body.strip():
                continue
            try:
                out["enum"] = [_coerce_enum_value(v, json_type) for v in body.split(", ")]
            except ValueError:
                continue
        elif m := _BOUND_RE.match(text):
            out[_BOUND_KEYS[m.group(1)]] = _number(m.group(2))
        elif m := _LENGTH_RE.match(text):
            out["minLength" if m.group(1) == ">=" else "maxLength"] = int(m.group(2))
        elif m := _ITEMS_RE.match(text):
            out["minItems" if m.group(1) == "min" else "maxItems"] = int(m.group(2))
        elif m := _PATTERN_RE.match(text):
            out["pattern"] = m.group(1).strip()
        elif m := _FORMAT_RE.match(text):
            out["format"] = m.group(1)
        elif m := _MULTIPLE_RE.match(text):
            out["multipleOf"] = _number(m.group(1))
        elif text == "items must be unique":
            out["uniqueItems"] = True
    return out


def _shape_to_schema(shape: Any) -> Optional[Dict[str, Any]]:
    """Registry ``schema`` summaries → JSON Schema, only when unambiguous.

    ``_summarize_param_schema`` emits ``"string"``-style primitives,
    ``{"type": "array", "items": <shape>}`` and ``{field: <shape>}`` objects — or
    an ``example`` value in their place, which carries no type and is ignored.
    """
    if isinstance(shape, str) and shape in _PRIMITIVES:
        return {"type": shape}
    if not isinstance(shape, dict) or not shape:
        return None
    if shape.get("type") == "array":
        items = _shape_to_schema(shape.get("items"))
        return {"type": "array", "items": items} if items else {"type": "array"}
    if "type" in shape:
        # Already JSON Schema (``{"type": "string", "enum": [...]}``); anything else
        # with a "type" key is an example payload, not a shape.
        return shape if shape["type"] in (*_PRIMITIVES, "object") else None
    props = {}
    for key, sub in shape.items():
        sub_schema = _shape_to_schema(sub)
        if sub_schema is None:
            return None
        props[key] = sub_schema
    return {"type": "object", "properties": props}


def params_to_json_schema(parameters: Any) -> Dict[str, Any]:
    """Registry parameter list → JSON-Schema object (dicts pass through unchanged)."""
    if not isinstance(parameters, list):
        return parameters if isinstance(parameters, dict) else {}

    properties: Dict[str, Any] = {}
    required: List[str] = []
    for param in parameters:
        if not isinstance(param, dict):
            continue
        name = param.get("name") or ""
        if not name:
            continue
        shape = param.get("schema")
        json_type = param.get("type") or (shape.get("type") if isinstance(shape, dict) else None) or "string"
        prop: Dict[str, Any] = {"type": json_type, "description": param.get("description", "") or ""}
        if isinstance(shape, dict) and isinstance(shape.get("type"), str) and shape.get("type") != "array":
            # A real JSON Schema (tracker tool defs) — keep its keywords as-is.
            structural = shape
        else:
            structural = _shape_to_schema(shape)
        if structural and structural.get("type") == json_type:
            prop.update({k: v for k, v in structural.items() if k not in ("type", "description", "title")})
        if param.get("default") is not None:
            prop["default"] = param["default"]
        constraints = param.get("constraints") or []
        if constraints:
            prop["constraints"] = constraints
            prop.update(constraints_to_keywords(constraints, json_type))
        properties[name] = prop
        if param.get("required", False):
            required.append(name)
    return {"type": "object", "properties": properties, "required": required}


# ── JSON Schema → Pydantic annotations ─────────────────────────────────────


def _non_null_type(prop: Dict[str, Any]) -> Tuple[Dict[str, Any], bool]:
    """Unwrap ``anyOf/oneOf [T, null]`` and ``type: [T, "null"]``; report nullability."""
    t = prop.get("type")
    if isinstance(t, list) and "null" in t:
        rest = [x for x in t if x != "null"]
        if len(rest) == 1:
            return {**prop, "type": rest[0]}, True
    for key in ("anyOf", "oneOf"):
        variants = prop.get(key)
        if isinstance(variants, list) and any(
            isinstance(v, dict) and v.get("type") == "null" for v in variants
        ):
            rest = [v for v in variants if isinstance(v, dict) and v.get("type") != "null"]
            if len(rest) == 1:
                merged = {k: v for k, v in prop.items() if k != key}
                merged.update(rest[0])
                return merged, True
    return prop, False


def _constraint_field(prop: Dict[str, Any], json_type: str) -> Dict[str, Any]:
    kw: Dict[str, Any] = {}
    if json_type in ("integer", "number"):
        for src, dst in (
            ("minimum", "ge"),
            ("maximum", "le"),
            ("exclusiveMinimum", "gt"),
            ("exclusiveMaximum", "lt"),
            ("multipleOf", "multiple_of"),
        ):
            value = prop.get(src)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                kw[dst] = value
    elif json_type == "string":
        for src, dst in (("minLength", "min_length"), ("maxLength", "max_length")):
            if isinstance(prop.get(src), int):
                kw[dst] = prop[src]
        pattern = prop.get("pattern")
        if isinstance(pattern, str) and pattern:
            try:
                re.compile(pattern)
                kw["pattern"] = pattern
            except re.error:
                pass
    elif json_type == "array":
        for src, dst in (("minItems", "min_length"), ("maxItems", "max_length")):
            if isinstance(prop.get(src), int):
                kw[dst] = prop[src]
    return kw


def annotation_for_schema(prop: Dict[str, Any], *, name_hint: str = "Field", depth: int = 0) -> Any:
    """Recursive JSON-Schema → annotation. Nullability is the caller's concern."""
    if not isinstance(prop, dict):
        return Any
    prop, _ = _non_null_type(prop)
    if schema_type_is_ambiguous(prop):
        return Any

    enum = prop.get("enum")
    if isinstance(enum, list) and enum and all(isinstance(v, (str, int, float, bool)) for v in enum):
        return Literal[tuple(enum)]  # type: ignore[valid-type]

    json_type = json_schema_type(prop, default="")
    constraint_kw = _constraint_field(prop, json_type)

    if json_type in _PRIMITIVES:
        base: Any = _PRIMITIVES[json_type]
    elif json_type == "array":
        items = prop.get("items")
        item_ann = (
            annotation_for_schema(items, name_hint=f"{name_hint}Item", depth=depth + 1)
            if isinstance(items, dict) and items
            else Any
        )
        base = List[item_ann]  # type: ignore[valid-type]
    elif json_type == "object":
        base = _object_annotation(prop, name_hint=name_hint, depth=depth)
    else:
        return python_type_for_schema(prop)

    if constraint_kw:
        return Annotated[base, Field(**constraint_kw)]
    return base


def _object_annotation(prop: Dict[str, Any], *, name_hint: str, depth: int) -> Any:
    props = prop.get("properties")
    if not isinstance(props, dict) or not props or depth >= MAX_NESTING_DEPTH:
        addl = prop.get("additionalProperties")
        if isinstance(addl, dict) and addl and depth < MAX_NESTING_DEPTH:
            return Dict[str, annotation_for_schema(addl, name_hint=f"{name_hint}Value", depth=depth + 1)]  # type: ignore[misc]
        return dict
    # Nested objects keep unknown keys: only the top level is ``extra="forbid"``,
    # because nested payload schemas are the ones most often under-specified.
    extra = "forbid" if prop.get("additionalProperties") is False else "allow"
    fields = _field_definitions(props, prop.get("required") or [], name_hint=name_hint, depth=depth + 1)
    return create_model(f"{name_hint}", __config__=ConfigDict(extra=extra), **fields)


def _safe_default(value: Any) -> Any:
    # Mutable defaults were historically dropped (unhashable); keep that contract.
    return None if isinstance(value, (list, dict)) else value


def _field_definitions(
    props: Dict[str, Any], required: List[str], *, name_hint: str, depth: int
) -> Dict[str, Tuple[Any, Any]]:
    fields: Dict[str, Tuple[Any, Any]] = {}
    for name, sub in props.items():
        if not isinstance(sub, dict):
            sub = {}
        ann = annotation_for_schema(sub, name_hint=f"{name_hint}_{name}", depth=depth)
        desc = sub.get("description", "") or ""
        if name in required:
            fields[name] = (ann, Field(..., description=desc))
        else:
            fields[name] = (Optional[ann], Field(default=_safe_default(sub.get("default")), description=desc))
    return fields


# ── Public entry points ─────────────────────────────────────────────────────


def build_legacy_input_model(tool_name: str, parameters: Dict[str, Any]) -> type[BaseModel]:
    """Today's flat model: top-level types only, unknown keys ignored."""
    fields: Dict[str, Tuple[Any, Any]] = {}
    props = parameters.get("properties") if isinstance(parameters, dict) else None
    if isinstance(props, dict):
        required = parameters.get("required", []) or []
        for name, sub in props.items():
            sub = sub if isinstance(sub, dict) else {}
            py_t = python_type_for_schema(sub)
            desc = sub.get("description", "") or ""
            if name in required:
                fields[name] = (py_t, Field(..., description=desc))
            else:
                fields[name] = (
                    Optional[py_t],
                    Field(default=_safe_default(sub.get("default")), description=desc),
                )
    return create_model(f"{tool_name}Input", **fields)


def build_input_model(
    tool_name: str,
    parameters: Any,
    *,
    mode: Optional[str] = None,
) -> type[BaseModel]:
    """Build the tool's input model.

    ``parameters`` is a registry parameter list or a JSON-Schema object.
    ``mode`` is ``off`` (legacy flat model), ``lax`` (faithful, Pydantic's
    lax coercion — ``"5"`` validates as ``5``) or ``strict`` (faithful, no
    coercion). Defaults to ``settings.advanced_features.cuga_lite_tool_validation``.
    """
    resolved_mode = resolve_validation_mode(mode) if mode is not None else validation_mode_from_settings()
    schema = params_to_json_schema(parameters)
    if resolved_mode == VALIDATION_MODE_OFF:
        return build_legacy_input_model(tool_name, schema)
    try:
        props = schema.get("properties") or {}
        fields = _field_definitions(
            props, schema.get("required") or [], name_hint=f"{tool_name}Input", depth=0
        )
        config = ConfigDict(extra="forbid", strict=resolved_mode == VALIDATION_MODE_STRICT)
        model = create_model(f"{tool_name}Input", __config__=config, **fields)
        model.model_json_schema()  # surfaces schema-generation errors here, not at bind time
        return model
    except Exception as exc:
        logger.warning("Faithful input model for {} failed ({}); using the flat model", tool_name, exc)
        return build_legacy_input_model(tool_name, schema)
