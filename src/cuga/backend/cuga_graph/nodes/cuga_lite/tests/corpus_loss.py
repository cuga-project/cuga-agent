from __future__ import annotations

from typing import Any

from cuga.backend.cuga_graph.nodes.cuga_lite.prompt_utils import (
    _render_find_tools_markdown,
    input_schema_adds_detail,
)
from cuga.backend.cuga_graph.nodes.cuga_lite.prompt_utils import Tool
from cuga.backend.tools_env.registry.utils.schema_utils import json_schema_type

IMPORTANT_SCHEMA_KEYS = frozenset(
    {
        "enum",
        "const",
        "pattern",
        "format",
        "minimum",
        "maximum",
        "exclusiveMinimum",
        "exclusiveMaximum",
        "minLength",
        "maxLength",
        "minItems",
        "maxItems",
        "multipleOf",
        "uniqueItems",
        "default",
        "prefixItems",
        "patternProperties",
        "contains",
        "dependentRequired",
        "dependentSchemas",
        "if",
        "not",
        "contentEncoding",
        "contentMediaType",
        "$ref",
        "$dynamicRef",
        "$recursiveRef",
    }
)

_HTTP = ("get", "post", "put", "patch", "delete")
_TYPE_MAP = {
    "string": "str",
    "integer": "int",
    "number": "float",
    "boolean": "bool",
    "array": "list",
    "object": "dict",
}


def params_doc_from_schema(schema: dict) -> str:
    if not isinstance(schema, dict):
        return "No parameters required"
    properties = schema.get("properties") or {}
    required = schema.get("required") or []
    lines = []
    for name, prop in properties.items():
        if not isinstance(prop, dict):
            lines.append(f"- `{name}`: unknown")
            continue
        py = _TYPE_MAP.get(json_schema_type(prop), json_schema_type(prop))
        mark = " (required)" if name in required else " (optional)"
        desc = prop.get("description") or ""
        constraints = prop.get("constraints") or []
        extra = f" [Constraints: {', '.join(constraints)}]" if constraints else ""
        lines.append(f"- `{name}`: {py}{mark} - {desc}{extra}")
    return "\n".join(lines) if lines else "No parameters required"


def important_facts(schema: Any, path: str = "$") -> set[str]:
    facts: set[str] = set()
    _collect_facts(schema, path, facts)
    return facts


def _collect_facts(node: Any, path: str, facts: set[str]) -> None:
    if isinstance(node, list):
        for i, item in enumerate(node):
            _collect_facts(item, f"{path}[{i}]", facts)
        return
    if not isinstance(node, dict):
        return
    for key in IMPORTANT_SCHEMA_KEYS:
        if key not in node:
            continue
        val = node[key]
        if isinstance(val, (str, int, float, bool)) or val is None:
            facts.add(f"{key}@{path}={val}")
        elif isinstance(val, list) and all(not isinstance(x, (dict, list)) for x in val):
            facts.add(f"{key}@{path}={'|'.join(map(str, val))}")
        else:
            facts.add(f"{key}@{path}")
    ap = node.get("additionalProperties")
    if isinstance(ap, dict) and ap:
        facts.add(f"additionalProperties.schema@{path}")
        _collect_facts(ap, f"{path}.additionalProperties", facts)
    props = node.get("properties")
    if isinstance(props, dict):
        for name, prop in props.items():
            if isinstance(prop, dict) and isinstance(prop.get("properties"), dict) and prop["properties"]:
                facts.add(f"nested.properties@{path}.properties.{name}")
            _collect_facts(prop, f"{path}.properties.{name}", facts)
    for k in (
        "items",
        "prefixItems",
        "anyOf",
        "oneOf",
        "allOf",
        "not",
        "if",
        "then",
        "else",
        "contains",
        "patternProperties",
        "dependentSchemas",
        "$defs",
        "definitions",
    ):
        if k in node:
            _collect_facts(node[k], f"{path}.{k}", facts)


def facts_visible_in_params_doc(facts: set[str], params_doc: str) -> set[str]:
    visible = set()
    text = params_doc or ""
    for fact in facts:
        if fact.startswith("nested.properties@"):
            continue
        if fact.startswith("additionalProperties.schema@"):
            continue
        payload = fact.split("@", 1)[-1]
        value = payload.split("=", 1)[-1] if "=" in payload else ""
        key = fact.split("@", 1)[0]
        tokens = [
            t for t in (value.replace("|", " ").split() if value else []) if t not in {"$", "true", "false"}
        ]
        if (
            key in {"enum", "const", "default", "pattern", "format", "minimum", "maximum"}
            and value
            and str(value) in text
        ):
            visible.add(fact)
        elif (meaningful_tokens := [tok for tok in tokens if len(tok) > 1]) and all(
            tok in text for tok in meaningful_tokens
        ):
            visible.add(fact)
    return visible


def trim_drops_important_facts(schema: dict) -> list[str]:
    params = params_doc_from_schema(schema)
    facts = important_facts(schema)
    uncovered = facts - facts_visible_in_params_doc(facts, params)
    if not uncovered:
        return []
    if input_schema_adds_detail(schema):
        return []
    return sorted(uncovered)


def extract_openapi_operations(spec: dict, source: str) -> list[dict]:
    out = []
    for path, methods in (spec.get("paths") or {}).items():
        if not isinstance(methods, dict):
            continue
        for method, op in methods.items():
            if method.lower() not in _HTTP or not isinstance(op, dict):
                continue
            schema = _operation_input_schema(op)
            oid = op.get("operationId") or f"{method}_{path}"
            out.append(
                {
                    "id": f"{source}:{method.upper()}:{path}",
                    "source": source,
                    "method": method.upper(),
                    "path": path,
                    "operation_id": oid,
                    "input_schema": schema,
                }
            )
    return out


def _operation_input_schema(op: dict) -> dict:
    properties: dict = {}
    required: list[str] = []
    for param in op.get("parameters") or []:
        if not isinstance(param, dict):
            continue
        name = param.get("name")
        if not name:
            continue
        sch = param.get("schema") or {}
        if param.get("description") and isinstance(sch, dict) and "description" not in sch:
            sch = {**sch, "description": param.get("description")}
        properties[name] = sch
        if param.get("required"):
            required.append(name)
    content = (op.get("requestBody") or {}).get("content") or {}
    for body in content.values():
        if not isinstance(body, dict):
            continue
        sch = body.get("schema") or {}
        if isinstance(sch, dict) and sch.get("properties"):
            properties.update(sch["properties"])
            required.extend(sch.get("required") or [])
        elif isinstance(sch, dict) and sch:
            properties["_body"] = sch
        break
    return {"type": "object", "properties": properties, "required": list(dict.fromkeys(required))}


def render_with_schema(name: str, schema: dict, params_doc: str) -> str:
    tool = Tool(
        name=name,
        input=schema,
        reasoning="corpus",
        output_schema={},
        params_doc=params_doc,
        response_doc="",
    )
    return _render_find_tools_markdown("q", [tool], {})
