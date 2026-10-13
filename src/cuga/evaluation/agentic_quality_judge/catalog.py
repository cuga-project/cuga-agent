"""Fetch the registered tool catalog and format it as a markdown
<available_tools> block for the agentic_quality judge prompt.

Handles both shapes CUGA's registry represents a tool's `parameters` in:
- services:/OpenAPI-registered tools (e.g. digital_sales): a JSON-schema
  object, {"properties": {name: {type, description}}}.
- mcpServers:/MCP-registered tools: a flat list of
  {name, type, required, description, ...} dicts.
_param_rows() below normalizes both.
"""

import json


def _param_rows(parameters):
    if isinstance(parameters, dict):
        return [
            (name, info.get("type", "—"), info.get("description", ""))
            for name, info in (parameters.get("properties") or {}).items()
        ]
    if isinstance(parameters, list):
        return [
            (p.get("name", "?"), p.get("type", "—"), p.get("description", ""))
            for p in parameters
            if isinstance(p, dict)
        ]
    return []


async def fetch_tool_catalog(port):
    """Fetch all registered tools' docs from the registry server (GET /apis,
    the same source of truth CUGA itself uses) and format them into an
    <available_tools> markdown block. Call only after the registry server is
    confirmed up.

    Returns "" (not raising) if the fetch fails, so a judge run can still
    proceed with an empty <available_tools> block rather than aborting the
    whole `cuga evaluate` run.
    """
    import httpx

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(
                f"http://127.0.0.1:{port}/apis", params={"include_response_schema": "true"}
            )
            response.raise_for_status()
            apps = response.json()
    except Exception:
        return ""

    sections = []
    for tools in apps.values():
        for tool_name, tool in tools.items():
            rows = _param_rows(tool.get("parameters"))
            param_lines = [f"| `{name}` | {type_} | {desc} |" for name, type_, desc in rows] or [
                "| _(none)_ | — | No parameters required |"
            ]
            sections.append(
                f"### `{tool_name}`\n\n"
                f"> `{tool.get('method', 'GET')} {tool.get('path', '')}` — {tool.get('description', '')}\n\n"
                f"| Parameter | Type | Description |\n|-----------|------|--------------|\n"
                + "\n".join(param_lines)
                + f"\n\n**Response:** `{json.dumps(tool.get('response_schemas', {}))}`"
            )

    if not sections:
        return ""
    return "# Available Tools\n\n" + "\n\n---\n\n".join(sections)
