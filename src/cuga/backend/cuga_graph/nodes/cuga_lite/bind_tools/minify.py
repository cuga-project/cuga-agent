"""Minified wire schemas for native ``bind_tools``.

Providers require JSON Schema for tools, but most of what Pydantic emits is
noise to the model: a ``title`` on every property and model, ``anyOf [T,
{"type": "null"}]`` wrappers on every optional parameter (``required`` already
says what is optional), ``default: null``, ``$defs`` indirection, and the full
multi-paragraph OpenAPI description. This strips that noise while keeping what
constrains a call — types, enums, items, nested fields, bounds, ``required``.

Used on the tool-search bind path only (``bind_tools/deferred.py``); the legacy
cap path keeps binding ``StructuredTool`` objects unchanged.
See ``docs/design/fc-deferred-tool-search.md`` §3.10 A.
"""

from __future__ import annotations

import copy
from typing import Any, Dict, List, Optional

from langchain_core.utils.function_calling import convert_to_openai_tool

from cuga.backend.cuga_graph.nodes.cuga_lite.bind_tools.wire_names import wire_name
from cuga.backend.cuga_graph.nodes.cuga_lite.helpers.signature_render import first_sentence

DEFAULT_DESCRIPTION_MAX_CHARS = 300
DEFAULT_PARAM_DESCRIPTION_MAX_CHARS = 120
_MAX_REF_DEPTH = 4
# Schema keywords whose value is a map of *names* → schemas: their keys are data,
# never keywords, so a property literally called "title" survives.
_NAME_MAP_KEYWORDS = ("properties", "$defs", "definitions", "patternProperties")


def _truncate(text: str, max_chars: int) -> str:
    text = " ".join((text or "").split())
    if not max_chars or len(text) <= max_chars:
        return text
    return text[: max_chars - 1].rstrip() + "…"


def _inline_refs(node: Any, defs: Dict[str, Any], depth: int = 0) -> Any:
    if isinstance(node, list):
        return [_inline_refs(v, defs, depth) for v in node]
    if not isinstance(node, dict):
        return node
    if "$ref" in node:
        key = str(node["$ref"]).rsplit("/", 1)[-1]
        if key in defs and depth < _MAX_REF_DEPTH:
            merged = {**defs[key], **{k: v for k, v in node.items() if k != "$ref"}}
            return _inline_refs(merged, defs, depth + 1)
        return {k: v for k, v in node.items() if k != "$ref"} or {"type": "object"}
    return {k: _inline_refs(v, defs, depth) for k, v in node.items()}


def _strip(node: Any, *, param_description_max_chars: int, top_level: bool = False) -> Any:
    if isinstance(node, list):
        return [_strip(v, param_description_max_chars=param_description_max_chars) for v in node]
    if not isinstance(node, dict):
        return node

    # anyOf/oneOf [T, null] → T: optionality is carried by ``required``.
    for key in ("anyOf", "oneOf"):
        variants = node.get(key)
        if isinstance(variants, list):
            non_null = [v for v in variants if not (isinstance(v, dict) and v.get("type") == "null")]
            if len(non_null) == 1 and len(non_null) < len(variants):
                merged = {k: v for k, v in node.items() if k != key}
                merged.update(non_null[0])
                node = merged
                break

    out: Dict[str, Any] = {}
    for key, value in node.items():
        if key == "title":
            continue
        if key == "default" and value is None:
            continue
        if key == "description":
            text = _truncate(value if isinstance(value, str) else "", param_description_max_chars)
            if text and not top_level:
                out[key] = text
            continue
        if key in _NAME_MAP_KEYWORDS and isinstance(value, dict):
            out[key] = {
                name: _strip(sub, param_description_max_chars=param_description_max_chars)
                for name, sub in value.items()
            }
            continue
        out[key] = _strip(value, param_description_max_chars=param_description_max_chars)
    return out


def minify_parameters(
    parameters: Dict[str, Any],
    *,
    param_description_max_chars: int = DEFAULT_PARAM_DESCRIPTION_MAX_CHARS,
) -> Dict[str, Any]:
    """Minify a tool's ``parameters`` JSON Schema (pure; input is not mutated)."""
    schema = copy.deepcopy(parameters or {})
    defs = {**(schema.pop("$defs", None) or {}), **(schema.pop("definitions", None) or {})}
    if defs:
        schema = _inline_refs(schema, defs)
    schema = _strip(schema, param_description_max_chars=param_description_max_chars, top_level=True)
    schema.setdefault("type", "object")
    schema.setdefault("properties", {})
    return schema


def minify_openai_tool(
    tool: Dict[str, Any],
    *,
    description_max_chars: int = DEFAULT_DESCRIPTION_MAX_CHARS,
    param_description_max_chars: int = DEFAULT_PARAM_DESCRIPTION_MAX_CHARS,
) -> Dict[str, Any]:
    """Minify one OpenAI-format tool (``{"type": "function", "function": {...}}``)."""
    fn = dict(tool.get("function") or {})
    description = fn.get("description") or ""
    if description_max_chars and len(description) > description_max_chars:
        # Prefer a whole first sentence when it fits; otherwise a hard cut.
        sentence = first_sentence(description, max_chars=description_max_chars)
        description = sentence if sentence else _truncate(description, description_max_chars)
    fn["description"] = " ".join(description.split())
    fn["name"] = wire_name(str(fn.get("name") or ""))  # provider limit (Bedrock: 64 chars)
    fn["parameters"] = minify_parameters(
        fn.get("parameters") or {}, param_description_max_chars=param_description_max_chars
    )
    return {"type": "function", "function": fn}


def minified_tools(
    tools: List[Any],
    *,
    description_max_chars: Optional[int] = None,
    param_description_max_chars: int = DEFAULT_PARAM_DESCRIPTION_MAX_CHARS,
) -> List[Dict[str, Any]]:
    """StructuredTools (or OpenAI dicts) → minified OpenAI-format tool dicts."""
    if description_max_chars is None:
        description_max_chars = description_max_chars_from_settings()
    out: List[Dict[str, Any]] = []
    for tool in tools:
        spec = (
            tool
            if isinstance(tool, dict) and tool.get("type") == "function"
            else convert_to_openai_tool(tool)
        )
        out.append(
            minify_openai_tool(
                spec,
                description_max_chars=description_max_chars,
                param_description_max_chars=param_description_max_chars,
            )
        )
    return out


def description_max_chars_from_settings() -> int:
    from cuga.config import settings

    try:
        return int(
            getattr(
                settings.advanced_features,
                "cuga_lite_tool_description_max_chars",
                DEFAULT_DESCRIPTION_MAX_CHARS,
            )
        )
    except (TypeError, ValueError):
        return DEFAULT_DESCRIPTION_MAX_CHARS
