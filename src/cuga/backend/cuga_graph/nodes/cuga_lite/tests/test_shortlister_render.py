"""Markdown rendering.

Guards the rank/render extraction: the output format is what the agent reads
from sandbox stdout, so drift here is a behavior change.
"""

import pytest
from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from cuga.backend.cuga_graph.nodes.cuga_lite.shortlister import ShortlistCandidate
from cuga.backend.cuga_graph.nodes.cuga_lite.shortlister.render import (
    NO_MATCH_MESSAGE,
    render_tools_markdown,
)

pytestmark = pytest.mark.unit


class _ContactArgs(BaseModel):
    email: str = Field(..., description="filter by email address")
    limit: int = Field(default=10, description="")


def _tool(name="crm_get_contacts_contacts_get", description="Get Contacts", args=_ContactArgs):
    def fn(**kwargs):
        return None

    fn.__name__ = "fn"
    return StructuredTool.from_function(func=fn, name=name, description=description, args_schema=args)


# --- rendering --------------------------------------------------------------


def test_render_produces_the_expected_markdown_shape():
    tools = [_tool(name="tool_a", description="Tool A does things")]
    out = render_tools_markdown(
        [ShortlistCandidate(name="tool_a", score=0.9, reasoning="because")],
        tools,
        display_query="find contacts",
    )

    assert out.startswith("# Found 1 Matching Tool(s)")
    assert "**Query:** find contacts" in out
    assert "## 1. `tool_a`" in out
    assert "**Description:** Tool A does things" in out
    assert "**Reasoning:** because" in out
    assert "**Parameters:**" in out
    assert "---" in out


def test_render_empty_returns_the_no_match_message():
    assert render_tools_markdown([], [], display_query="q") == NO_MATCH_MESSAGE


def test_render_skips_candidates_with_no_matching_tool():
    """Preserves the original `if not actual_tool: continue`."""
    tools = [_tool(name="real_tool")]
    out = render_tools_markdown(
        [ShortlistCandidate(name="hallucinated"), ShortlistCandidate(name="real_tool")],
        tools,
        display_query="q",
    )
    assert "# Found 1 Matching Tool(s)" in out
    assert "hallucinated" not in out


def test_render_uses_the_composed_query_verbatim():
    """The header must show what the LLM saw, unchanged by the query split."""
    composed = "Query: list contacts\nTask context (initial user message): Book a flight"
    out = render_tools_markdown(
        [ShortlistCandidate(name="tool_a")], [_tool(name="tool_a")], display_query=composed
    )
    assert f"**Query:** {composed}" in out
