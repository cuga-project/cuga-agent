"""Unit tests for issue #767: Policy isolation across multiple agents."""

from __future__ import annotations

import re
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from cuga.backend.cuga_graph.policy.configurable import (
    PolicyConfigurable,
    create_agent_policy_system,
    get_agent_policy_collection_name,
)
from cuga.backend.server import main as main_mod
from cuga.backend.server.manage_routes import draft_routes
from cuga.backend.server.manage_routes.draft_routes import patch_draft_policies
from cuga.supervisor_utils.supervisor_config import build_agents_from_stored_subagents

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _registry_on(monkeypatch):
    monkeypatch.setattr(
        "cuga.backend.server.agent_registry.is_agent_registry_enabled",
        lambda: True,
    )


def test_agent_policy_collection_name_scoping(monkeypatch):
    """Verify every generated name is safe and defaults ignore the storage setting."""
    monkeypatch.setattr(
        "cuga.backend.cuga_graph.policy.configurable.settings.policy.collection_name", "Custom-Policies"
    )

    default_names = [
        get_agent_policy_collection_name(None, draft=False),
        get_agent_policy_collection_name("cuga-default", draft=False),
        get_agent_policy_collection_name(None, draft=True),
        get_agent_policy_collection_name("cuga-default", draft=True),
    ]
    assert default_names == ["cuga_policies", "cuga_policies", "cuga_policies_draft", "cuga_policies_draft"]

    named_draft = get_agent_policy_collection_name("draft", draft=False)
    assert named_draft != get_agent_policy_collection_name(None, draft=True)

    punctuation_variants = {
        get_agent_policy_collection_name(agent_id, draft=False) for agent_id in ("a-b", "a_b", "a.b")
    }
    assert len(punctuation_variants) == 3

    named_collection = get_agent_policy_collection_name("crm-agent", draft=False)
    named_draft_collection = get_agent_policy_collection_name("crm-agent", draft=True)
    assert named_collection == get_agent_policy_collection_name("crm-agent", draft=False)
    assert named_draft_collection != named_collection
    assert named_draft_collection.endswith("__draft")

    generated_names = [
        *default_names,
        named_draft,
        *punctuation_variants,
        named_collection,
        named_draft_collection,
        get_agent_policy_collection_name("é" * 100, draft=False),
        get_agent_policy_collection_name("é" * 100, draft=True),
    ]
    for collection_name in generated_names:
        assert re.fullmatch(r"[a-z][a-z0-9_]{0,62}", collection_name, flags=re.ASCII)
        assert len(collection_name.encode("ascii")) <= 63


_EMBEDDING_CONFIG_PATCH = dict(
    dim=384,
    provider="sentence_transformers",
    model="unused",
    base_url=None,
    api_key=None,
)


def _make_mock_storage(mock_cls):
    """Wire up the standard async stubs on a MagicMock storage class."""
    storage = mock_cls.return_value
    storage.initialize_async = AsyncMock()
    storage._embedding_function = None
    storage._embedding_initialized = False
    return storage


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "configured_value, expected_collection",
    [
        ("custom_policies", "custom_policies"),  # truthy configured value is honoured
        ("", "cuga_policies"),  # empty string falls back to helper default
        (None, "cuga_policies"),  # None falls back to helper default
    ],
)
async def test_default_policy_runtime_uses_configured_collection_name(
    monkeypatch, configured_value, expected_collection
):
    """initialize() without an explicit argument must respect settings.policy.collection_name.

    Precedence (highest → lowest):
      1. explicit collection_name argument
      2. settings.policy.collection_name (truthy)
      3. get_agent_policy_collection_name() → "cuga_policies"
    """
    monkeypatch.setattr(
        "cuga.backend.cuga_graph.policy.configurable.settings.policy.collection_name",
        configured_value,
    )

    with (
        patch("cuga.backend.cuga_graph.policy.configurable.PolicyStorage") as mock_storage_cls,
        patch(
            "cuga.backend.storage.embedding.get_embedding_config",
            return_value=_EMBEDDING_CONFIG_PATCH,
        ),
    ):
        _make_mock_storage(mock_storage_cls)
        ps = PolicyConfigurable(llm=object(), agent=object())
        await ps.initialize()

    assert mock_storage_cls.call_args.kwargs["collection_name"] == expected_collection


@pytest.mark.asyncio
async def test_default_policy_runtime_explicit_arg_overrides_configured_collection(monkeypatch):
    """An explicit collection_name argument beats settings.policy.collection_name."""
    monkeypatch.setattr(
        "cuga.backend.cuga_graph.policy.configurable.settings.policy.collection_name",
        "custom_policies",
    )

    with (
        patch("cuga.backend.cuga_graph.policy.configurable.PolicyStorage") as mock_storage_cls,
        patch(
            "cuga.backend.storage.embedding.get_embedding_config",
            return_value=_EMBEDDING_CONFIG_PATCH,
        ),
    ):
        _make_mock_storage(mock_storage_cls)
        ps = PolicyConfigurable(llm=object(), agent=object())
        await ps.initialize(collection_name="explicit_policies")

    assert mock_storage_cls.call_args.kwargs["collection_name"] == "explicit_policies"


@pytest.mark.asyncio
async def test_default_policy_runtime_draft_unaffected_by_configured_collection(monkeypatch):
    """Default draft initialization always uses cuga_policies_draft regardless of the setting."""
    monkeypatch.setattr(
        "cuga.backend.cuga_graph.policy.configurable.settings.policy.collection_name",
        "custom_policies",
    )
    main_storage_cls = patch("cuga.backend.cuga_graph.policy.storage.PolicyStorage")
    with (
        main_storage_cls as mock_main_storage_cls,
        patch(
            "cuga.backend.storage.embedding.get_embedding_config",
            return_value=_EMBEDDING_CONFIG_PATCH,
        ),
    ):
        draft_storage = mock_main_storage_cls.return_value
        draft_storage.initialize_async = AsyncMock()
        draft_policy_system = SimpleNamespace(initialize=AsyncMock())

        with patch(
            "cuga.backend.cuga_graph.policy.configurable.PolicyConfigurable",
            return_value=draft_policy_system,
        ):
            (
                initialized_draft_system,
                draft_collection,
            ) = await main_mod._initialize_default_draft_policy_system()

    assert mock_main_storage_cls.call_args.kwargs["collection_name"] == "cuga_policies_draft"
    assert initialized_draft_system is draft_policy_system
    assert draft_collection == "cuga_policies_draft"
    draft_storage.initialize_async.assert_awaited_once_with()
    draft_policy_system.initialize.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_resolve_policy_agent_id_returns_registry_candidate():
    registry_agent_id = "".join(["crm", "-agent"])
    requested_agent_id = registry_agent_id.encode().decode()
    assert requested_agent_id == registry_agent_id
    assert requested_agent_id is not registry_agent_id

    with patch(
        "cuga.backend.server.config_store.list_agents_with_configs",
        new_callable=AsyncMock,
        return_value=[{"agent_id": registry_agent_id}],
    ):
        resolved = await main_mod._resolve_policy_agent_id(requested_agent_id)

    assert resolved is registry_agent_id


@pytest.mark.asyncio
@pytest.mark.parametrize("requested_agent_id", ["missing-agent", "crm-agent; DROP TABLE policies--"])
async def test_resolve_policy_agent_id_rejects_unknown_ids(requested_agent_id):
    with patch(
        "cuga.backend.server.config_store.list_agents_with_configs",
        new_callable=AsyncMock,
        return_value=[{"agent_id": "crm-agent"}],
    ):
        with pytest.raises(HTTPException) as exc_info:
            await main_mod._resolve_policy_agent_id(requested_agent_id)

    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_resolve_policy_agent_id_uses_default_when_registry_disabled(monkeypatch):
    monkeypatch.setattr(
        "cuga.backend.server.agent_registry.is_agent_registry_enabled",
        lambda: False,
    )

    assert await main_mod._resolve_policy_agent_id("crm-agent") == "cuga-default"


@pytest.mark.asyncio
@pytest.mark.parametrize("requested_agent_id", [None, "cuga-default"])
async def test_resolve_policy_agent_id_accepts_default_without_registry_lookup(requested_agent_id):
    with patch(
        "cuga.backend.server.config_store.list_agents_with_configs",
        new_callable=AsyncMock,
    ) as list_agents:
        assert await main_mod._resolve_policy_agent_id(requested_agent_id) == "cuga-default"

    list_agents.assert_not_awaited()


@pytest.mark.asyncio
async def test_patch_draft_policies_rejects_unknown_agent_before_request_parsing_or_storage(monkeypatch):
    request = SimpleNamespace(json=AsyncMock(), app=SimpleNamespace(state=SimpleNamespace()))
    save_draft_section = AsyncMock()
    create_policy_system = AsyncMock()
    invalidate_cache = AsyncMock()
    monkeypatch.setattr("cuga.backend.server.agent_registry.is_agent_registry_enabled", lambda: True)
    monkeypatch.setattr(
        "cuga.backend.server.config_store.list_agents_with_configs",
        AsyncMock(return_value=[{"agent_id": "registered-agent"}]),
    )
    monkeypatch.setattr(draft_routes, "save_draft_section_unlocked", save_draft_section)
    monkeypatch.setattr(
        "cuga.backend.cuga_graph.policy.configurable.create_agent_policy_system",
        create_policy_system,
    )
    monkeypatch.setattr(draft_routes, "invalidate_agent_graph_cache", invalidate_cache)

    with pytest.raises(HTTPException) as exc_info:
        await patch_draft_policies(request, agent_id="registered-agent'; DROP TABLE policies;--")

    assert exc_info.value.status_code == 404
    request.json.assert_not_awaited()
    save_draft_section.assert_not_awaited()
    create_policy_system.assert_not_awaited()
    invalidate_cache.assert_not_awaited()


@pytest.mark.asyncio
async def test_patch_draft_policies_passes_registry_owned_agent_id_to_policy_creation(monkeypatch):
    registry_agent_id = "".join(["registered", "-agent"])
    requested_agent_id = registry_agent_id.encode().decode()
    assert requested_agent_id == registry_agent_id
    assert requested_agent_id is not registry_agent_id
    request = SimpleNamespace(
        json=AsyncMock(return_value={"policies": {"policies": []}}),
        app=SimpleNamespace(state=SimpleNamespace()),
    )
    captured = {}

    async def _save_draft_section(agent_id, section, value):
        captured["saved_agent_id"] = agent_id
        assert section == "policies"
        return {"policies": value}

    async def _create_policy_system(*, agent_id, draft, policies_data):
        captured["policy_agent_id"] = agent_id
        assert draft is True
        assert policies_data == []

    monkeypatch.setattr("cuga.backend.server.agent_registry.is_agent_registry_enabled", lambda: True)
    monkeypatch.setattr(
        "cuga.backend.server.config_store.list_agents_with_configs",
        AsyncMock(return_value=[{"agent_id": registry_agent_id}]),
    )
    monkeypatch.setattr(draft_routes, "save_draft_section_unlocked", _save_draft_section)
    monkeypatch.setattr(
        "cuga.backend.cuga_graph.policy.configurable.create_agent_policy_system",
        _create_policy_system,
    )
    monkeypatch.setattr(draft_routes, "invalidate_agent_graph_cache", AsyncMock())

    response = await patch_draft_policies(request, agent_id=requested_agent_id)

    assert response.status_code == 200
    assert captured["saved_agent_id"] is registry_agent_id
    assert captured["policy_agent_id"] is registry_agent_id


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("route", "method", "agent_id"),
    [
        (main_mod.get_policies_config, "GET", None),
        (main_mod.save_policies_config, "POST", "query-agent"),
    ],
)
async def test_policy_config_routes_resolve_external_agent_id_before_storage(route, method, agent_id):
    request = SimpleNamespace(
        method=method,
        headers={"X-Agent-ID": "header-agent"},
    )
    rejection = HTTPException(status_code=404, detail="unknown agent")

    with patch.object(
        main_mod, "_resolve_policy_agent_id", new_callable=AsyncMock, side_effect=rejection
    ) as resolver:
        with pytest.raises(HTTPException) as exc_info:
            await route(request, agent_id=agent_id, current_user=None)

    resolver.assert_awaited_once_with(agent_id or "header-agent")
    assert exc_info.value is rejection


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

    assert ps_a.storage.collection_name == get_agent_policy_collection_name("agent-a")
    assert ps_b.storage.collection_name == get_agent_policy_collection_name("agent-b")
    assert ps_a.storage.collection_name != ps_b.storage.collection_name

    policies_in_a = await ps_a.storage.list_policies(enabled_only=False)
    policies_in_b = await ps_b.storage.list_policies(enabled_only=False)

    assert len(policies_in_a) == 1
    assert policies_in_a[0].id == "policy-crm"

    assert len(policies_in_b) == 1
    assert policies_in_b[0].id == "policy-docs"


@pytest.mark.asyncio
async def test_resolve_stream_agent_creates_isolated_policy_system():
    """_resolve_stream_agent instantiates an agent-specific policy system for non-default agents.

    Graph construction is read-only w.r.t. policy storage: policies_data from the config
    snapshot is NOT passed to create_agent_policy_system, so a concurrent POST /api/config/policies
    save is never overwritten by a first stream. The collection is populated by the save/publish
    flows, not by graph construction.
    """
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
    assert resolved_graph.policy_system.storage.collection_name == get_agent_policy_collection_name(
        "crm-agent"
    )

    # Graph build is read-only: the collection is NOT seeded from the config snapshot.
    # Population is the responsibility of save/publish flows.
    policies = await resolved_graph.policy_system.storage.list_policies(enabled_only=False)
    assert len(policies) == 0


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
    assert agent._policy_system.storage.collection_name == get_agent_policy_collection_name("crm-sub")
    # The supervisor build does NOT seed the collection from ref_config (to avoid overwriting
    # more-recent saves); the collection is empty at construction time.
    policies = await agent._policy_system.storage.list_policies(enabled_only=False)
    assert len(policies) == 0
