"""Publishing or deleting a sub-agent must drop cached supervisor graphs that reference it."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from cuga.backend.server.manage_routes.helpers import (
    drop_cached_graphs_for_agent,
    supervisor_ids_referencing,
)

pytestmark = pytest.mark.unit


def test_supervisor_ids_referencing_internal_sub_agent():
    agents = [
        (
            "trip-supervisor",
            {
                "agent": {"kind": "supervisor"},
                "supervisor": {"subAgents": [{"kind": "internal", "ref": "crm-agent"}]},
            },
        ),
        (
            "other-supervisor",
            {
                "agent": {"kind": "supervisor"},
                "supervisor": {"subAgents": [{"kind": "a2a", "name": "hotel", "endpoint": "http://x"}]},
            },
        ),
        ("crm-agent", {"agent": {"kind": "single"}}),
    ]
    assert supervisor_ids_referencing("crm-agent", agents) == ["trip-supervisor"]


def test_drop_cached_graphs_for_agent_and_referrers():
    keep = object()
    cache = {
        ("crm-agent", False): object(),
        ("crm-agent", True): object(),
        ("trip-supervisor", False): object(),
        ("trip-supervisor", True): keep,
        ("unrelated", False): keep,
    }
    drop_cached_graphs_for_agent(
        cache,
        "crm-agent",
        draft=False,
        published=True,
        referrer_ids=["trip-supervisor"],
    )
    assert ("crm-agent", False) not in cache
    assert ("trip-supervisor", False) not in cache
    assert cache[("trip-supervisor", True)] is keep
    assert cache[("unrelated", False)] is keep
    assert ("crm-agent", True) in cache


def test_bump_agent_graph_generation_is_a_no_op_without_dict():
    from cuga.backend.server.manage_routes.helpers import bump_agent_graph_generation

    bump_agent_graph_generation(None, "sales-east")
    bump_agent_graph_generation(SimpleNamespace(), "sales-east")
    state = SimpleNamespace(agent_graph_generations={})
    bump_agent_graph_generation(state, "sales-east")
    bump_agent_graph_generation(state, "sales-east")
    assert state.agent_graph_generations["sales-east"] == 2
