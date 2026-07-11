"""The FC prompt conditional must not change the default (code-mode) prompt (issue #471)."""

from pathlib import Path

import cuga.backend.cuga_graph.nodes.cuga_lite.cuga_lite_graph as clg
from cuga.backend.cuga_graph.nodes.cuga_lite.prompt_utils import create_mcp_prompt
from cuga.backend.llm.utils.helpers import load_one_prompt


def _template():
    prompts_dir = Path(clg.__file__).parent / "prompts"
    return load_one_prompt(str(prompts_dir / "mcp_prompt.jinja2"), relative_to_caller=False)


def _render(**kwargs):
    return create_mcp_prompt([], prompt_template=_template(), **kwargs)


def test_default_prompt_keeps_the_no_function_calling_rule():
    prompt = _render()
    assert "NO FUNCTION CALLING JSON" in prompt
    assert "You may either call tools natively" not in prompt


def test_code_mode_is_byte_identical_to_default():
    assert _render() == _render(allow_native_tool_calls=False)


def test_native_mode_permits_tool_calls_and_drops_the_ban():
    prompt = _render(allow_native_tool_calls=True)
    assert "NO FUNCTION CALLING JSON" not in prompt
    assert "You may either call tools natively" in prompt
