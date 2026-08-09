"""JSON-Schema helpers shared by tool-doc renderers."""

from typing import Any, Dict

_TYPE_MAPPING = {
    "string": str,
    "integer": int,
    "number": float,
    "boolean": bool,
    "array": list,
    "object": dict,
}


def json_schema_type(prop: Dict[str, Any], default: str = "string") -> str:
    """Resolve a JSON-Schema property's type.

    Pydantic renders optional fields as ``anyOf: [{...}, {"type": "null"}]`` with no
    top-level ``type`` key, so a plain ``prop.get("type", "string")`` silently falls back
    and reports *every* optional parameter as a string. That misled the agent into
    skipping a list-typed ``repeat_days`` param on AppWorld ``321ec38_1``.
    """
    t = prop.get("type")
    if isinstance(t, list):  # OpenAPI 3.1: type: ["string", "null"]
        return next((x for x in t if x != "null"), default)
    if t:
        return t
    for key in ("anyOf", "oneOf", "allOf"):
        for variant in prop.get(key) or []:
            if isinstance(variant, dict) and variant.get("type") != "null":
                if variant.get("type") or "properties" in variant:
                    return json_schema_type(variant, default)
    if "properties" in prop:
        return "object"
    return default


def schema_type_is_ambiguous(prop: Dict[str, Any]) -> bool:
    """True when the JSON schema cannot pin the param to one Python type.

    Covers unresolved ``$ref`` and genuine unions (``anyOf``/``oneOf`` with more than
    one non-null variant, or OpenAPI 3.1 ``type: [A, B]``), where ``json_schema_type``
    would narrow to the first branch. ``type: [T, "null"]`` stays narrow (Optional).
    """
    t = prop.get("type")
    if isinstance(t, list):
        return len([x for x in t if x != "null"]) > 1
    if t:
        return False
    if "$ref" in prop:
        return True
    for key in ("anyOf", "oneOf"):
        variants = [v for v in (prop.get(key) or []) if isinstance(v, dict) and v.get("type") != "null"]
        if len(variants) > 1:
            return True
    return False


def python_type_for_schema(prop: Dict[str, Any]) -> Any:
    """Map a JSON-schema property to a Pydantic field annotation.

    Ambiguous schemas validate as ``Any`` so fail-closed ``model_validate`` does not
    reject values the real OpenAPI schema would accept. Untyped properties also use
    ``Any`` (via an empty ``json_schema_type`` default) rather than silently becoming
    ``str``.
    """
    if schema_type_is_ambiguous(prop):
        return Any
    return _TYPE_MAPPING.get(json_schema_type(prop, default=""), Any)
