"""JSON-Schema helpers shared by tool-doc renderers."""

from typing import Any, Dict


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
