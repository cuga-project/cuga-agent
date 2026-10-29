"""Publishing or deleting a sub-agent must drop cached supervisor graphs that reference it."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

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


@pytest.mark.asyncio
async def test_default_member_change_invalidates_named_parent(monkeypatch):
    from cuga.backend.server.manage_routes import helpers

    state = SimpleNamespace(agent_graphs_cache={("parent", False): object()}, agent_graph_generations={})
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(app_state=state)))
    monkeypatch.setattr(helpers, "_referrer_ids_for_agent", AsyncMock(return_value=["parent"]))

    await helpers.invalidate_agent_graph_cache(request, "cuga-default", draft=False, published=True)

    assert state.agent_graphs_cache == {}
    assert state.agent_graph_generations == {"cuga-default": 1, "parent": 1}


@pytest.mark.asyncio
@pytest.mark.parametrize("draft,published", [(True, False), (False, True), (True, True)])
async def test_child_change_rebuilds_default_parent_even_without_cached_graphs(monkeypatch, draft, published):
    from cuga.backend.server.manage_routes import helpers

    prod = SimpleNamespace()
    preview = SimpleNamespace()
    state = SimpleNamespace(agent=prod, agent_graphs_cache={}, agent_graph_generations={})
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(app_state=state, draft_app_state=SimpleNamespace(agent=preview))
        )
    )
    monkeypatch.setattr(helpers, "_referrer_ids_for_agent", AsyncMock(return_value=["cuga-default"]))
    config = {"agent": {"kind": "supervisor"}}
    monkeypatch.setattr("cuga.backend.server.config_store.load_config", AsyncMock(return_value=(config, 1)))
    monkeypatch.setattr("cuga.backend.server.config_store.load_draft", AsyncMock(return_value=config))
    rebuild = AsyncMock()
    monkeypatch.setattr("cuga.backend.server.manage_routes.draft_ops._apply_config_and_rebuild", rebuild)

    await helpers.invalidate_agent_graph_cache(request, "weatherbot", draft=draft, published=published)

    assert rebuild.await_count == int(draft) + int(published)
    if published:
        rebuild.assert_any_await(prod, config, use_draft=False)
    if draft:
        rebuild.assert_any_await(preview, config, use_draft=True)


@pytest.mark.asyncio
async def test_child_publish_invalidates_headless_roster_without_graph_cache(monkeypatch):
    from cuga.backend.server.manage_routes import helpers

    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(app_state=None)))
    monkeypatch.setattr(helpers, "_referrer_ids_for_agent", AsyncMock(return_value=["cuga"]))
    invalidate = AsyncMock()
    monkeypatch.setattr("cuga.backend.server.run_routes.invalidate_cached_supervisor", invalidate)

    await helpers.invalidate_agent_graph_cache(request, "weatherbot", draft=True, published=True)

    invalidate.assert_awaited_once()
