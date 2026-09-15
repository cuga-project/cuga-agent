"""The function-calling system prompt: short, generic, assembled from configuration."""

from __future__ import annotations

from pathlib import Path

import pytest

from cuga.backend.cuga_graph.nodes.cuga_lite.prompt_utils import render_fc_prompt

pytestmark = pytest.mark.unit

_TEMPLATE = Path(__file__).resolve().parents[1] / "prompts" / "fc_prompt.jinja2"
_STEP_LINE = "Make exactly ONE tool call at a time"
_EVIDENCE_LINE = "You MUST call at least one tool before giving a final answer"


def test_default_prompt_is_the_bare_contract():
    text = render_fc_prompt()
    assert text.startswith("# ROLE")
    assert "native function-calling" in text and "# FINAL ANSWER" in text
    assert "Do NOT write Python code" in text
    assert _STEP_LINE not in text and _EVIDENCE_LINE not in text
    assert "autonomously" not in text


def test_step_discipline_adds_the_one_call_line():
    assert _STEP_LINE in render_fc_prompt(step_discipline=True)


def test_fragments_are_opt_in_and_unknown_names_are_ignored():
    assert _EVIDENCE_LINE in render_fc_prompt(fragments=["evidence_first"])
    assert _EVIDENCE_LINE in render_fc_prompt(fragments=["Evidence_First"])
    assert _EVIDENCE_LINE not in render_fc_prompt(fragments=["something_else"])


def test_autonomous_subtask_line():
    assert "autonomously" in render_fc_prompt(is_autonomous_subtask=True)


def test_instructions_then_special_instructions_close_the_prompt():
    text = render_fc_prompt(
        step_discipline=True,
        fragments=["evidence_first"],
        instructions="  Agent instructions.  ",
        special_instructions="Answer format: one line.",
    )
    body = text.rstrip("\n")
    assert body.endswith("Agent instructions.\n\nAnswer format: one line.")
    assert text.index(_STEP_LINE) < text.index(_EVIDENCE_LINE) < text.index("# FINAL ANSWER")
    assert "\n\n\n" not in text, "no blank-line runs from unset optional sections"


def test_template_ships_no_benchmark_or_deployment_wording():
    text = _TEMPLATE.read_text()
    assert "[[" not in text
    assert "vakra" not in text.lower()
    for module in ("adapter/tool_exec_node.py", "adapter/graph_adapter.py", "prompt_utils.py"):
        assert "vakra" not in (_TEMPLATE.parents[1] / module).read_text().lower(), module
