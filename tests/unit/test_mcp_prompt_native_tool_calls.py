"""Native FC uses a separate template; the code-act prompt is untouched (issue #471)."""

from pathlib import Path

import cuga.backend.cuga_graph.nodes.cuga_lite.cuga_lite_graph as clg
from cuga.backend.cuga_graph.nodes.cuga_lite.prompt_utils import (
    create_mcp_prompt,
    get_native_mcp_prompt_template,
)
from cuga.backend.llm.utils.helpers import load_one_prompt


def _code_template():
    prompts_dir = Path(clg.__file__).parent / "prompts"
    return load_one_prompt(str(prompts_dir / "mcp_prompt.jinja2"), relative_to_caller=False)


def _render(template):
    return create_mcp_prompt([], prompt_template=template)


def test_code_act_prompt_is_the_code_template():
    prompt = _render(_code_template())
    assert "NO FUNCTION CALLING JSON" in prompt
    assert "Python Code Execution" in prompt


def test_native_prompt_is_function_calling_and_drops_code_language():
    prompt = _render(get_native_mcp_prompt_template())
    # native framing present
    assert "native function calling" in prompt.lower()
    assert "Tool Call(s)" in prompt
    assert "Available Tools" in prompt
    # code-act / sandbox language removed
    assert "NO FUNCTION CALLING JSON" not in prompt
    assert "Python Code Execution" not in prompt
    assert "```python" not in prompt
    assert "print()" not in prompt
    assert "sandbox" not in prompt.lower()


def test_native_prompt_keeps_shared_guidance():
    prompt = _render(get_native_mcp_prompt_template())
    # the durable cross-cutting rules carry over (reframed for tool calling)
    assert "PAGINATION" in prompt
    assert "DATA FROM TOOLS ONLY" in prompt
    assert "Cuga Agent" in prompt


def test_native_prompt_has_correct_and_incorrect_examples():
    prompt = _render(get_native_mcp_prompt_template())
    assert "Correct vs. Incorrect" in prompt
    assert "✅" in prompt and "❌" in prompt
    # tool-calling-specific bad examples present, no code-act artifacts
    assert "Announcing instead of acting" in prompt
    assert "App prefix" in prompt
    assert "```python" not in prompt


def test_native_prompt_skills_block_keeps_install_ordering():
    # native + skills + shell must retain the STEP 0/1/2 install-first ordering
    # (a loaded skill must install its requirements before use), reframed for
    # tool calls — no code-act language.
    prompt = create_mcp_prompt(
        [],
        prompt_template=get_native_mcp_prompt_template(),
        skills_enabled=True,
        enable_shell_tool=True,
        skills_prompt_section="<available_skills>demo</available_skills>",
    )
    assert "STEP 0" in prompt and "STEP 1" in prompt and "STEP 2" in prompt
    assert "uv pip install" in prompt
    assert "load_skill" in prompt
    assert "```python" not in prompt  # still no code-act artifacts
