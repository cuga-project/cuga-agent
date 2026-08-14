"""Render ranked candidates as the markdown ``find_tools`` returns.

Extracted verbatim from ``PromptUtils.find_tools`` so that ranking and
presentation can vary independently: a cosine strategy produces the same output
shape as the LLM one, just with ``reasoning`` supplied differently.

Output format is load-bearing — the agent reads this string from sandbox stdout
— so changes here are behavior changes. Keep byte-compatible with the pre-split
implementation.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List

from langchain_core.tools import StructuredTool

from cuga.backend.cuga_graph.nodes.cuga_lite.shortlister.base import ShortlistCandidate

NO_MATCH_MESSAGE = "No matching tools found for your query."


def _output_schema_for(tool: StructuredTool) -> Dict[str, Any]:
    """Normalize ``_response_schemas['success']`` into a dict schema."""
    func = getattr(tool, "func", None)
    if func is None or not hasattr(func, "_response_schemas"):
        return {}
    response_schemas = func._response_schemas
    if not (response_schemas and isinstance(response_schemas, dict) and 'success' in response_schemas):
        return {}
    raw = response_schemas['success']
    if isinstance(raw, list):
        if len(raw) > 0 and isinstance(raw[0], dict):
            return {"type": "array", "items": raw[0]}
        return {"type": "array", "items": raw[0] if raw else {}}
    if isinstance(raw, dict):
        return raw
    return {"value": raw} if raw is not None else {}


def _input_schema_for(tool: StructuredTool) -> Dict[str, Any]:
    args_schema = getattr(tool, "args_schema", None)
    if not args_schema:
        return {}
    try:
        return args_schema.schema()
    except Exception:
        return {}


def render_tools_markdown(
    candidates: List[ShortlistCandidate],
    tools: List[StructuredTool],
    display_query: str,
) -> str:
    """Render ``candidates`` as markdown.

    ``display_query`` is shown verbatim in the ``**Query:**`` header — callers
    pass the same composed string the LLM saw, so output is unchanged.

    Candidates whose name matches no tool in ``tools`` are silently skipped,
    preserving the original ``if not actual_tool: continue``.
    """
    from cuga.backend.cuga_graph.nodes.cuga_lite.executors.common.variable_utils import VariableUtils
    from cuga.backend.cuga_graph.nodes.cuga_lite.prompt_utils import PromptUtils, Tool

    by_name = {t.name: t for t in tools}

    enriched_tools: List[Tool] = []
    for candidate in candidates:
        actual_tool = by_name.get(candidate.name)
        if not actual_tool:
            continue
        params_doc, response_doc = PromptUtils.get_tool_docs(actual_tool)
        enriched_tools.append(
            Tool(
                name=candidate.name,
                input=VariableUtils.sanitize_value(_input_schema_for(actual_tool)),
                reasoning=candidate.reasoning,
                output_schema=VariableUtils.sanitize_value(_output_schema_for(actual_tool)),
                params_doc=params_doc,
                response_doc=response_doc,
            )
        )

    if not enriched_tools:
        return NO_MATCH_MESSAGE

    tool_descriptions = {
        tool.name: getattr(tool, 'description', None) for tool in tools if hasattr(tool, 'description')
    }

    markdown_lines = [
        f"# Found {len(enriched_tools)} Matching Tool(s)\n",
        f"**Query:** {display_query}\n",
    ]

    for idx, tool in enumerate(enriched_tools, 1):
        markdown_lines.append(f"## {idx}. `{tool.name}`\n")

        tool_description = tool_descriptions.get(tool.name)
        if tool_description:
            markdown_lines.append(f"**Description:** {tool_description}\n")

        markdown_lines.append(f"**Reasoning:** {tool.reasoning}\n")

        if tool.params_doc:
            markdown_lines.append("**Parameters:**\n")
            markdown_lines.append(f"{tool.params_doc}\n")
        else:
            markdown_lines.append("**Parameters:** No parameters required\n")

        if tool.response_doc:
            markdown_lines.append("**Response Schema:**\n")
            markdown_lines.append(f"{tool.response_doc}\n")

        if tool.input_ and tool.input_ != {}:
            markdown_lines.append("**Input Schema:**\n")
            markdown_lines.append(f"```json\n{json.dumps(tool.input_, indent=2)}\n```\n")

        if tool.output_schema and tool.output_schema != {}:
            markdown_lines.append("**Output Schema:**\n")
            markdown_lines.append(f"```json\n{json.dumps(tool.output_schema, indent=2)}\n```\n")

        markdown_lines.append("---\n")

    return "\n".join(markdown_lines)
