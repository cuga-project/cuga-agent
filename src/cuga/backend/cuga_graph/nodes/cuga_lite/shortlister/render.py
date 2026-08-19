"""Render ranked candidates as the markdown ``find_tools`` returns.

Ranking and presentation stay independent: cosine and LLM strategies produce
the same markdown shape, with ``reasoning`` supplied differently.

Markdown assembly is ``_render_find_tools_markdown`` (schema trim from #644).
The agent reads this string from sandbox stdout, so changes here are behavior
changes.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

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
    notes: Optional[List[str]] = None,
) -> str:
    """Render ``candidates`` as markdown.

    ``display_query`` is shown verbatim in the ``**Query:**`` header — callers
    pass the same composed string the LLM saw, so output is unchanged.

    Candidates whose name matches no tool in ``tools`` are silently skipped,
    preserving the original ``if not actual_tool: continue``.
    """
    from cuga.backend.cuga_graph.nodes.cuga_lite.executors.common.variable_utils import VariableUtils
    from cuga.backend.cuga_graph.nodes.cuga_lite.prompt_utils import (
        PromptUtils,
        Tool,
        _render_find_tools_markdown,
    )

    by_name = {t.name: t for t in tools}
    note_text = "\n\n".join(n for n in (notes or []) if n)

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
        return f"{NO_MATCH_MESSAGE}\n\n{note_text}" if note_text else NO_MATCH_MESSAGE

    tool_descriptions = {
        tool.name: getattr(tool, 'description', None) for tool in tools if hasattr(tool, 'description')
    }

    markdown = _render_find_tools_markdown(display_query, enriched_tools, tool_descriptions)
    if note_text:
        return f"{markdown}\n{note_text}"
    return markdown
