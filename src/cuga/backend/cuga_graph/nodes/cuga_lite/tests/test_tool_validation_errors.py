"""Zod-style grouped validation errors (docs/design/fc-deferred-tool-search.md §3.9 D)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from cuga.backend.cuga_graph.nodes.cuga_lite.helpers.tool_validation_errors import (
    format_any_validation_error,
    format_validation_error,
)
from cuga.backend.tools_env.registry.utils.schema_model import build_input_model

_PARAMS = [
    {"name": "season", "type": "integer", "required": True},
    {"name": "order", "type": "string", "required": False, "constraints": ["must be one of: [asc, desc]"]},
    {"name": "limit", "type": "integer", "required": False, "constraints": [">= 1", "<= 100"]},
    {"name": "code", "type": "string", "required": False, "constraints": ["length <= 3"]},
    {"name": "tags", "type": "array", "required": False, "schema": {"type": "array", "items": "string"}},
]


def _error(payload: dict) -> ValidationError:
    model = build_input_model("hockey_get_games", _PARAMS, mode="lax")
    with pytest.raises(ValidationError) as exc:
        model.model_validate(payload)
    return exc.value


def _render(payload: dict) -> str:
    return format_validation_error(
        "hockey_get_games", _error(payload), ["season", "order", "limit", "code", "tags"]
    )


@pytest.mark.unit
def test_missing_required():
    text = _render({})
    assert text.splitlines()[0] == "Invalid arguments for hockey_get_games:"
    assert "- missing required: season" in text
    assert text.splitlines()[-1] == "Fix the arguments and call hockey_get_games again."


@pytest.mark.unit
def test_unexpected_lists_valid_parameters():
    text = _render({"season": 2020, "team_name": "x"})
    assert "- unexpected: team_name  (valid parameters: code, limit, order, season, tags)" in text


@pytest.mark.unit
def test_type_mismatch_reports_expected_and_received():
    text = _render({"season": "twenty"})
    assert '- type mismatch: season expected integer, received string "twenty"' in text


@pytest.mark.unit
def test_nested_path_is_rendered():
    text = _render({"season": 1, "tags": ["ok", 5]})
    assert "tags[1] expected string, received integer 5" in text


@pytest.mark.unit
def test_enum_violation_lists_choices():
    text = _render({"season": 1, "order": "ascending"})
    assert '- not allowed: order must be one of "asc" | "desc", received string "ascending"' in text


@pytest.mark.unit
def test_range_and_length_violations():
    text = _render({"season": 1, "limit": 0, "code": "toolong"})
    assert "- not allowed: limit must be >= 1, received integer 0" in text
    assert "- not allowed: code must have at most 3 characters" in text


@pytest.mark.unit
def test_long_received_values_are_truncated():
    text = _render({"season": "x" * 500})
    line = next(line for line in text.splitlines() if "type mismatch" in line)
    assert len(line) < 140 and line.endswith('…')


@pytest.mark.unit
def test_groups_are_ordered_missing_unexpected_type_not_allowed():
    text = _render({"order": "nope", "limit": "many", "extra": 1})
    order = [text.index(tag) for tag in ("missing required", "unexpected", "type mismatch", "not allowed")]
    assert order == sorted(order)


@pytest.mark.unit
def test_non_pydantic_errors_fall_back_to_a_plain_line():
    assert format_any_validation_error("t", ValueError("boom")) == "Tool input validation error for t: boom"
