"""Tool calls are stored in a shape every provider accepts when the thread is replayed.

A name like ``search<|channel|>commentary`` or an invalid call with non-JSON arguments,
persisted as the model sent it, made Bedrock reject every later request in the run
(``toolUse.name`` must match ``[a-zA-Z0-9_-]+``; ``toolUse.input`` must be an object).
"""

from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage

from cuga.backend.cuga_graph.nodes.cuga_lite.adapter.graph_adapter import (
    _clean_tool_name,
    _replayable_tool_calls,
)

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "raw, clean",
    [
        ("search_knowledge_base", "search_knowledge_base"),
        ("search_knowledge_base<|channel|>commentary", "search_knowledge_base"),
        ("functions.search_knowledge_base", "search_knowledge_base"),
        ("get x/y", "get_x_y"),
        ("<|channel|>", "unknown_tool"),
        ("", "unknown_tool"),
    ],
)
def test_names_are_cleaned_to_the_provider_pattern(raw, clean):
    assert _clean_tool_name(raw) == clean


def test_valid_calls_are_left_alone():
    msg = AIMessage(content="", tool_calls=[{"name": "a_b", "args": {"x": 1}, "id": "1"}])
    assert _replayable_tool_calls(msg) is msg


def test_bad_names_and_non_json_invalid_args_are_fixed_and_ids_kept():
    msg = AIMessage(
        content="",
        tool_calls=[{"name": "search<|channel|>commentary", "args": {"q": "x"}, "id": "1"}],
        invalid_tool_calls=[{"name": "functions.read", "args": "{oops", "id": "2", "error": "bad json"}],
    )
    out = _replayable_tool_calls(msg)
    assert [(c["name"], c["args"], c["id"]) for c in out.tool_calls] == [("search", {"q": "x"}, "1")]
    assert [(c["name"], c["args"], c["id"]) for c in out.invalid_tool_calls] == [("read", "{}", "2")]
