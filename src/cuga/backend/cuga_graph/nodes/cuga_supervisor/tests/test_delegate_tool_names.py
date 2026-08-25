"""Hyphenated and underscored agent ids must not share a delegate callable name."""

from __future__ import annotations

import pytest

from cuga.backend.cuga_graph.nodes.cuga_supervisor.nodes.execute_agent_tool import (
    _extract_pending_delegations,
)
from cuga.backend.cuga_graph.nodes.cuga_supervisor.nodes.prepare_agents_and_prompt import (
    _delegate_tool_name,
    delegate_tool_names,
)

pytestmark = pytest.mark.unit


def test_hyphen_and_underscore_ids_get_distinct_delegate_names():
    assert _delegate_tool_name("sales-east") != _delegate_tool_name("sales_east")


def test_delegate_tool_names_are_unique_for_colliding_sanitized_ids():
    names = delegate_tool_names(["sales-east", "sales_east"])
    assert names["sales-east"] != names["sales_east"]
    assert names["sales-east"].startswith("delegate_to_")
    assert names["sales_east"].startswith("delegate_to_")


def test_extract_pending_delegations_keeps_hyphen_and_underscore_agents_apart():
    names = delegate_tool_names(["sales-east", "sales_east"])
    script = f"{names['sales-east']}(task=\"hyphen-task\")\n{names['sales_east']}(task=\"underscore-task\")"
    pending = _extract_pending_delegations(script, {"sales-east", "sales_east"})
    assert pending == {"sales-east": "hyphen-task", "sales_east": "underscore-task"}
