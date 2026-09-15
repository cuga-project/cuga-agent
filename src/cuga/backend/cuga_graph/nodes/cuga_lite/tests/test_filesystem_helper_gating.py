"""The prompt must not advertise runtime helpers the executor does not inject.

``enable_filesystem_tools`` defaults to False (settings.toml), so ``read_file``,
``write_file`` and ``list_files`` are usually absent from the execution context.
Describing them anyway makes the model call them and hit ``NameError``.
"""

import json
from pathlib import Path

import pytest

from cuga.backend.cuga_graph.nodes.cuga_lite.prompt_utils import (
    create_mcp_prompt,
    drop_examples_using_absent_helpers,
)
from cuga.backend.llm.utils.helpers import load_one_prompt

pytestmark = pytest.mark.unit

HELPERS = ("read_file", "write_file", "list_files")
_PROMPT = Path(__file__).resolve().parents[1] / "prompts" / "mcp_prompt.jinja2"


def _render(**kwargs) -> str:
    return create_mcp_prompt(
        tools=[],
        prompt_template=load_one_prompt(str(_PROMPT), relative_to_caller=False),
        **kwargs,
    )


def test_helpers_absent_from_prompt_when_filesystem_tools_are_off():
    rendered = _render(enable_filesystem_tools=False, enable_shell_tool=False)
    for helper in HELPERS:
        assert helper not in rendered, f"prompt advertises {helper} but it is not injected"


def test_helpers_present_when_filesystem_tools_are_on():
    rendered = _render(enable_filesystem_tools=True, enable_shell_tool=True)
    assert "write_file" in rendered


def test_shell_block_alone_does_not_advertise_filesystem_helpers():
    # enable_shell_tool gates run_command, which is a separate capability.
    rendered = _render(enable_filesystem_tools=False, enable_shell_tool=True)
    for helper in HELPERS:
        assert helper not in rendered
    assert "run_command" in rendered


def test_bundled_few_shot_examples_are_filtered_when_helpers_are_absent():
    examples = [
        {"role": "assistant", "content": 'x = await read_file("./a.txt")'},
        {"role": "assistant", "content": 'y = await find_tools("q", "app")'},
    ]
    kept = drop_examples_using_absent_helpers(examples, filesystem_enabled=False)
    assert len(kept) == 1
    assert "read_file" not in kept[0]["content"]


def test_few_shot_examples_are_kept_when_helpers_are_present():
    examples = [{"role": "assistant", "content": 'x = await read_file("./a.txt")'}]
    assert drop_examples_using_absent_helpers(examples, filesystem_enabled=True) == examples


def test_bundled_find_tools_examples_would_be_filtered():
    """The shipped examples demonstrate the helpers; guard against silent regression."""
    path = _PROMPT.parent / "find_tools_few_shot_examples.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    turns = raw if isinstance(raw, list) else raw.get("examples", [])
    text = json.dumps(turns)
    assert any(h in text for h in HELPERS), "fixture no longer demonstrates helpers; drop this test"
