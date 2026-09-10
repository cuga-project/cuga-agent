"""Unit tests for issue #767: Policy isolation across multiple agents."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from cuga.backend.cuga_graph.policy.configurable import (
    create_agent_policy_system,
    get_agent_policy_collection_name,
)
from cuga.backend.server import main as main_mod
from cuga.supervisor_utils.supervisor_config import build_agents_from_stored_subagents

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _registry_on(monkeypatch):
    monkeypatch.setattr(
        "cuga.backend.server.agent_registry.is_agent_registry_enabled",
        lambda: True,
    )


def test_agent_policy_collection_name_scoping():
    """Verify collection name generation for default vs named agents, draft vs published."""
    assert get_agent_policy_collection_name(None, draft=False) == "cuga_policies"
    assert get_agent_policy_collection_name("cuga-default", draft=False) == "cuga_policies"
    assert get_agent_policy_collection_name(None, draft=True) == "cuga_policies_draft"
    assert get_agent_policy_collection_name("cuga-default", draft=True) == "cuga_policies_draft"

    # Hyphens are substituted with underscores for registry-issued slugified IDs ([a-z0-9-]).
    assert get_agent_policy_collection_name("crm-agent", draft=False) == "cuga_policies_crm_agent"
    assert get_agent_policy_collection_name("crm-agent", draft=True) == "cuga_policies_crm_agent_draft"

    # Registry-issued IDs are [a-z0-9-] only (see _slugify in agents_routes.py); distinct
    # IDs remain distinct after hyphen substitution.
    assert get_agent_policy_collection_name("sales-eu", draft=False) != get_agent_policy_collection_name(
        "sales-us", draft=False
    )


@pytest.mark.asyncio
async def test_create_agent_policy_system_isolates_storage():
    """Verify create_agent_policy_system initializes an agent-specific PolicyConfigurable."""
    policies_a = [
        {
            "id": "policy-crm",
            "name": "CRM Account Guard",
            "description": "Guard for CRM",
            "type": "intent_guard",
            "enabled": True,
            "triggers": [{"type": "keyword", "value": ["account"], "target": "intent", "operator": "and"}],
            "response": {"type": "natural_language", "content": "CRM policy fired"},
        }
    ]
    policies_b = [
        {
            "id": "policy-docs",
            "name": "Docs Search Guard",
            "description": "Guard for Docs",
            "type": "intent_guard",
            "enabled": True,
            "triggers": [{"type": "keyword", "value": ["search"], "target": "intent", "operator": "and"}],
            "response": {"type": "natural_language", "content": "Docs policy fired"},
        }
    ]

    ps_a = await create_agent_policy_system(agent_id="agent-a", draft=False, policies_data=policies_a)
    ps_b = await create_agent_policy_system(agent_id="agent-b", draft=False, policies_data=policies_b)

    assert ps_a.storage.collection_name == "cuga_policies_agent_a"
    assert ps_b.storage.collection_name == "cuga_policies_agent_b"

    policies_in_a = await ps_a.storage.list_policies(enabled_only=False)
    policies_in_b = await ps_b.storage.list_policies(enabled_only=False)

    assert len(policies_in_a) == 1
    assert policies_in_a[0].id == "policy-crm"

    assert len(policies_in_b) == 1
    assert policies_in_b[0].id == "policy-docs"


@pytest.mark.asyncio
async def test_resolve_stream_agent_creates_isolated_policy_system():
    """_resolve_stream_agent instantiates an agent-specific policy system for non-default agents."""
    request = SimpleNamespace()
    request.app = SimpleNamespace(state=SimpleNamespace(draft_app_state=None))

    mock_state = SimpleNamespace(
        agent=object(),
        agent_graphs_cache={},
        agent_graph_build_locks={},
        agent_graph_generations={},
        policy_system=None,
    )

    agent_config = {
        "agent": {"kind": "single", "name": "CRM Agent"},
        "tools": [],
        "policies": [
            {
                "id": "policy-crm-1",
                "name": "CRM Guard",
                "description": "CRM Guard description",
                "type": "intent_guard",
                "enabled": True,
                "triggers": [{"type": "keyword", "value": ["crm"], "target": "intent", "operator": "and"}],
                "response": {"type": "natural_language", "content": "CRM Guard response"},
            }
        ],
    }

    with patch.object(main_mod, "app_state", mock_state):
        with patch(
            "cuga.backend.server.config_store.load_config",
            new_callable=AsyncMock,
            return_value=(agent_config, None),
        ):
            resolved_graph = await main_mod._resolve_stream_agent(request, "crm-agent", use_draft=False)

    assert resolved_graph is not None
    assert resolved_graph.policy_system is not None
    assert resolved_graph.policy_system.storage.collection_name == "cuga_policies_crm_agent"

    policies = await resolved_graph.policy_system.storage.list_policies(enabled_only=False)
    assert len(policies) == 1
    assert policies[0].id == "policy-crm-1"


@pytest.mark.asyncio
async def test_supervisor_subagents_have_isolated_policy_systems():
    """Supervisor subagents resolved via build_agents_from_stored_subagents get isolated policy systems.

    build_agents_from_stored_subagents intentionally does NOT seed the policy collection from
    ref_config: passing policies_data to create_agent_policy_system would clear and repopulate
    persistent storage on every supervisor build, potentially overwriting more-recent saves.
    The test therefore verifies that the subagent receives a policy system scoped to the correct
    per-agent collection; population of that collection is the responsibility of the save/publish
    flows, not of subagent construction.
    """
    crm_config = {
        "agent": {"name": "CRM Agent"},
        "tools": [],
        "policies": [
            {
                "id": "subagent-crm-policy",
                "name": "Subagent CRM Policy",
                "description": "Subagent CRM Policy Description",
                "type": "intent_guard",
                "enabled": True,
                "triggers": [{"type": "keyword", "value": ["deal"], "target": "intent", "operator": "and"}],
                "response": {"type": "natural_language", "content": "Deal policy"},
            }
        ],
    }

    sub_agents = [{"kind": "internal", "ref": "crm-sub"}]

    with patch(
        "cuga.backend.server.config_store.load_config",
        new_callable=AsyncMock,
        return_value=(crm_config, None),
    ):
        agents_dict = await build_agents_from_stored_subagents(sub_agents, use_draft=False)

    assert "crm-sub" in agents_dict
    agent = agents_dict["crm-sub"]
    assert agent._policy_system is not None
    # Verify the policy system is scoped to the correct per-agent collection.
    assert agent._policy_system.storage.collection_name == "cuga_policies_crm_sub"
    # The supervisor build does NOT seed the collection from ref_config (to avoid overwriting
    # more-recent saves); the collection is empty at construction time.
    policies = await agent._policy_system.storage.list_policies(enabled_only=False)
    assert len(policies) == 0
