"""The system prompt is rendered on every request; its Jinja2 template is compiled only once."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from langchain_core.prompts import PromptTemplate
from langchain_core.tools import StructuredTool

from cuga.backend.cuga_graph.nodes.cuga_lite import prompt_utils
from cuga.backend.cuga_graph.nodes.cuga_lite.prompt_utils import (
    create_mcp_prompt,
    render_fc_prompt,
    render_prompt_cached,
)
from cuga.backend.llm.utils.helpers import load_one_prompt

pytestmark = pytest.mark.unit

_PROMPTS = Path(prompt_utils.__file__).parent / "prompts"


def _tool(name: str) -> StructuredTool:
    def run(query: str) -> str:
        """Search."""
        return query

    return StructuredTool.from_function(run, name=name, description=f"{name} tool")


def test_cached_render_matches_langchain_for_the_codeact_template():
    template = load_one_prompt(str(_PROMPTS / "mcp_prompt.jinja2"), relative_to_caller=False)
    tools = [_tool("search_a"), _tool("search_b")]

    cached = create_mcp_prompt(tools, prompt_template=template, instructions="Be brief.")

    # The same variables through LangChain's own renderer give the same text.
    with patch.object(prompt_utils, "render_prompt_cached", lambda t, v: t.invoke(v).to_string()):
        reference = create_mcp_prompt(tools, prompt_template=template, instructions="Be brief.")

    assert cached == reference
    assert "search_a" in cached and "Be brief." in cached


def test_cached_render_matches_langchain_for_the_function_calling_template():
    kwargs = dict(base_prompt="Base.", instructions="Do it.", special_instructions="Rule 1.")
    cached = render_fc_prompt(**kwargs)

    with patch.object(prompt_utils, "render_prompt_cached", lambda t, v: t.format(**v)):
        reference = render_fc_prompt(**kwargs)

    assert cached == reference
    assert "Base." in cached


def test_template_is_compiled_once_across_renders():
    template = PromptTemplate.from_template("Hello {{ name }}", template_format="jinja2")
    prompt_utils._compiled_jinja.cache_clear()

    with patch.object(
        prompt_utils._JINJA_ENV, "from_string", wraps=prompt_utils._JINJA_ENV.from_string
    ) as from_string:
        outputs = [render_prompt_cached(template, {"name": f"user{i}"}) for i in range(20)]

    assert outputs == [f"Hello user{i}" for i in range(20)]
    assert from_string.call_count == 1


def test_partial_variables_use_the_langchain_path():
    template = PromptTemplate.from_template(
        "{{ a }} {{ b }}", template_format="jinja2", partial_variables={"a": "A"}
    )

    assert render_prompt_cached(template, {"b": "B"}) == "A B"


def test_prompt_template_subclass_keeps_its_own_format():
    class Suffixed(PromptTemplate):
        def format(self, **kwargs):
            return super().format(**kwargs) + " [custom]"

    template = Suffixed.from_template("Hello {{ name }}", template_format="jinja2")

    assert render_prompt_cached(template, {"name": "x"}) == template.invoke({"name": "x"}).to_string()
