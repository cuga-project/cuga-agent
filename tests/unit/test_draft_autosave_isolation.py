"""LLM/tools/policies/instructions autosaves must not mutate the default draft runtime."""

from __future__ import annotations

import asyncio
import copy
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from cuga.backend.server.auth import require_auth
from cuga.backend.server.config_store import reset_config_db
from cuga.backend.server.manage_routes import helpers as draft_helpers
from cuga.backend.server.manage_routes import draft_routes, router
from cuga.backend.server.manage_routes.draft_routes import patch_draft_policies, save_manage_config_draft

pytestmark = pytest.mark.unit


def _client(app_state, draft_app_state) -> TestClient:
    reset_config_db()
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[require_auth] = lambda: None
    app.state.app_state = app_state
    app.state.draft_app_state = draft_app_state
    return TestClient(app)


def _states():
    draft_agent = SimpleNamespace(special_instructions="default-draft", llm_config={"model": "default"})
    draft_state = SimpleNamespace(
        current_llm="draft-llm",
        agent=draft_agent,
        policy_system=None,
        tools_include_by_app={"crm": ["get_account"]},
        tools_include_version=3,
    )
    app_state = SimpleNamespace(
        agent_graphs_cache={
            ("sales-east", True): object(),
            ("sales-east", False): object(),
        }
    )
    return app_state, draft_state, draft_agent


@pytest.mark.asyncio
async def test_full_draft_save_rejects_unknown_agent_before_writes(monkeypatch):
    save_draft = AsyncMock()
    monkeypatch.setattr("cuga.backend.server.agent_registry.is_agent_registry_enabled", lambda: True)
    monkeypatch.setattr(
        "cuga.backend.server.config_store.list_agents_with_configs",
        AsyncMock(return_value=[{"agent_id": "registered-agent"}]),
    )
    monkeypatch.setattr("cuga.backend.server.config_store.save_draft", save_draft)
    request = SimpleNamespace(json=AsyncMock(), app=SimpleNamespace(state=SimpleNamespace()))

    with pytest.raises(HTTPException) as exc_info:
        await save_manage_config_draft(request, agent_id="unknown-agent")

    assert exc_info.value.status_code == 404
    request.json.assert_not_awaited()
    save_draft.assert_not_awaited()


@pytest.mark.asyncio
async def test_full_draft_save_passes_registry_owned_agent_id_to_policy_creation(monkeypatch):
    registry_agent_id = "".join(["registered", "-agent"])
    requested_agent_id = registry_agent_id.encode().decode()
    assert requested_agent_id == registry_agent_id
    assert requested_agent_id is not registry_agent_id

    captured = {}

    async def _capture_policy_creation(*, agent_id, draft, policies_data):
        captured["agent_id"] = agent_id
        captured["draft"] = draft
        captured["policies_data"] = policies_data

    monkeypatch.setattr("cuga.backend.server.agent_registry.is_agent_registry_enabled", lambda: True)
    monkeypatch.setattr(
        "cuga.backend.server.config_store.list_agents_with_configs",
        AsyncMock(return_value=[{"agent_id": registry_agent_id}]),
    )
    monkeypatch.setattr("cuga.backend.server.config_store.load_draft", AsyncMock(return_value={}))
    monkeypatch.setattr("cuga.backend.server.config_store.save_draft", AsyncMock())
    monkeypatch.setattr(
        "cuga.backend.cuga_graph.policy.configurable.create_agent_policy_system",
        _capture_policy_creation,
    )
    monkeypatch.setattr(draft_routes, "invalidate_agent_graph_cache", AsyncMock())
    monkeypatch.setitem(
        sys.modules,
        "cuga.backend.tools_env.registry.utils.api_utils",
        SimpleNamespace(get_registry_base_url=lambda: "http://registry.test"),
    )
    monkeypatch.setattr(
        draft_routes.httpx,
        "AsyncClient",
        lambda **_kwargs: SimpleNamespace(
            __aenter__=AsyncMock(
                return_value=SimpleNamespace(
                    post=AsyncMock(
                        return_value=SimpleNamespace(raise_for_status=lambda: None, json=lambda: {})
                    )
                )
            ),
            __aexit__=AsyncMock(return_value=None),
        ),
    )
    request = SimpleNamespace(
        json=AsyncMock(return_value={"config": {"policies": {"policies": [{"id": "policy-1"}]}}}),
        app=SimpleNamespace(state=SimpleNamespace(draft_app_state=None)),
    )

    response = await save_manage_config_draft(request, agent_id=requested_agent_id)

    assert response.status_code == 200
    assert captured["agent_id"] is registry_agent_id
    assert captured["draft"] is True
    assert captured["policies_data"] == [{"id": "policy-1"}]


def test_patch_draft_llm_for_non_default_invalidates_cache_only(monkeypatch):
    monkeypatch.setattr(
        "cuga.backend.server.agent_registry.is_agent_registry_enabled",
        lambda: True,
    )
    app_state, draft_state, draft_agent = _states()
    client = _client(app_state, draft_state)

    response = client.patch(
        "/api/manage/config/draft/llm",
        params={"agent_id": "sales-east"},
        json={"llm": {"provider": "openai", "model": "gpt-4o-mini"}},
    )

    assert response.status_code == 200
    assert draft_state.current_llm == "draft-llm"
    assert draft_agent.llm_config == {"model": "default"}
    assert ("sales-east", True) not in app_state.agent_graphs_cache
    assert ("sales-east", False) in app_state.agent_graphs_cache


def test_patch_draft_tools_for_non_default_does_not_rebuild_default(monkeypatch):
    monkeypatch.setattr(
        "cuga.backend.server.agent_registry.is_agent_registry_enabled",
        lambda: True,
    )
    rebuilt = {"called": False}

    async def _rebuild(*_args, **_kwargs):
        rebuilt["called"] = True

    monkeypatch.setattr(
        "cuga.backend.server.manage_routes.draft_routes.rebuild_agent_from_config",
        _rebuild,
    )
    monkeypatch.setitem(
        sys.modules,
        "cuga.backend.tools_env.registry.utils.api_utils",
        SimpleNamespace(get_registry_base_url=lambda: "http://registry.test"),
    )
    monkeypatch.setattr(
        "cuga.backend.server.manage_routes.draft_routes.httpx.AsyncClient",
        lambda **_kwargs: SimpleNamespace(
            __aenter__=AsyncMock(
                return_value=SimpleNamespace(
                    post=AsyncMock(
                        return_value=SimpleNamespace(raise_for_status=lambda: None, json=lambda: {})
                    )
                )
            ),
            __aexit__=AsyncMock(return_value=None),
        ),
    )
    app_state, draft_state, _draft_agent = _states()
    client = _client(app_state, draft_state)

    response = client.patch(
        "/api/manage/config/draft/tools",
        params={"agent_id": "sales-east"},
        json={"tools": [{"name": "other_app", "include": ["list_items"]}]},
    )

    assert response.status_code == 200
    assert rebuilt["called"] is False
    assert draft_state.tools_include_by_app == {"crm": ["get_account"]}
    assert draft_state.tools_include_version == 3
    assert ("sales-east", True) not in app_state.agent_graphs_cache


def test_patch_draft_policies_for_non_default_skips_shared_policy_system(monkeypatch):
    monkeypatch.setattr("cuga.backend.server.agent_registry.is_agent_registry_enabled", lambda: True)
    monkeypatch.setattr(
        "cuga.backend.server.config_store.list_agents_with_configs",
        AsyncMock(return_value=[{"agent_id": "sales-east"}]),
    )
    app_state, draft_state, _draft_agent = _states()
    draft_state.policy_system = SimpleNamespace(storage=object())
    client = _client(app_state, draft_state)

    response = client.patch(
        "/api/manage/config/draft/policies",
        params={"agent_id": "sales-east"},
        json={"policies": {"policies": []}},
    )

    assert response.status_code == 200
    assert ("sales-east", True) not in app_state.agent_graphs_cache


@pytest.mark.asyncio
async def test_full_named_agent_draft_blocks_newer_policy_patch_until_replacement_finishes(monkeypatch):
    agent_id = "sales-east"
    full_policies = [{"id": "full-save-policy"}]
    patch_policies = [{"id": "newer-patch-policy"}]
    stored_config = {}
    stored_policies = []
    full_replacement_started = asyncio.Event()
    release_full_replacement = asyncio.Event()
    patch_config_saved = asyncio.Event()
    replacement_order = []

    async def _load_draft(_agent_id):
        assert _agent_id == agent_id
        return copy.deepcopy(stored_config)

    async def _save_draft(config, _agent_id):
        assert _agent_id == agent_id
        stored_config.clear()
        stored_config.update(copy.deepcopy(config))
        if stored_config.get("policies") == {"policies": patch_policies}:
            patch_config_saved.set()

    async def _create_agent_policy_system(*, agent_id: str, draft: bool, policies_data: list):
        assert agent_id == "sales-east"
        assert draft is True
        if policies_data == full_policies:
            full_replacement_started.set()
            await release_full_replacement.wait()
        replacement_order.append(copy.deepcopy(policies_data))
        stored_policies[:] = copy.deepcopy(policies_data)

    # Keep the production lock factory and registry behavior, but isolate this test's registry.
    monkeypatch.setattr(draft_helpers, "AGENT_DRAFT_LOCKS", {})
    monkeypatch.setattr("cuga.backend.server.agent_registry.is_agent_registry_enabled", lambda: True)
    monkeypatch.setattr(
        "cuga.backend.server.config_store.list_agents_with_configs",
        AsyncMock(return_value=[{"agent_id": agent_id}]),
    )
    monkeypatch.setattr("cuga.backend.server.config_store.load_draft", _load_draft)
    monkeypatch.setattr("cuga.backend.server.config_store.save_draft", _save_draft)
    monkeypatch.setattr(
        "cuga.backend.cuga_graph.policy.configurable.create_agent_policy_system",
        _create_agent_policy_system,
    )
    monkeypatch.setattr(
        "cuga.backend.server.manage_routes.draft_routes.invalidate_agent_graph_cache",
        AsyncMock(),
    )
    monkeypatch.setitem(
        sys.modules,
        "cuga.backend.tools_env.registry.utils.api_utils",
        SimpleNamespace(get_registry_base_url=lambda: "http://registry.test"),
    )
    monkeypatch.setattr(
        "cuga.backend.server.manage_routes.draft_routes.httpx.AsyncClient",
        lambda **_kwargs: SimpleNamespace(
            __aenter__=AsyncMock(
                return_value=SimpleNamespace(
                    post=AsyncMock(
                        return_value=SimpleNamespace(raise_for_status=lambda: None, json=lambda: {})
                    )
                )
            ),
            __aexit__=AsyncMock(return_value=None),
        ),
    )

    app = SimpleNamespace(state=SimpleNamespace(draft_app_state=None))
    full_request = SimpleNamespace(
        json=AsyncMock(return_value={"config": {"policies": {"policies": full_policies}}}),
        app=app,
    )
    patch_request = SimpleNamespace(
        json=AsyncMock(return_value={"policies": {"policies": patch_policies}}),
        app=app,
    )

    full_save = asyncio.create_task(save_manage_config_draft(full_request, agent_id=agent_id))
    policy_patch = None
    try:
        await asyncio.wait_for(full_replacement_started.wait(), timeout=1)
        policy_patch = asyncio.create_task(patch_draft_policies(patch_request, agent_id=agent_id))

        with pytest.raises(TimeoutError):
            await asyncio.wait_for(asyncio.shield(patch_config_saved.wait()), timeout=0.05)
        assert not policy_patch.done()
        assert stored_config["policies"] == {"policies": full_policies}

        release_full_replacement.set()
        full_response, patch_response = await asyncio.wait_for(
            asyncio.gather(full_save, policy_patch),
            timeout=1,
        )

        assert full_response.status_code == 200
        assert patch_response.status_code == 200
        assert replacement_order == [full_policies, patch_policies]
        assert stored_config["policies"] == {"policies": patch_policies}
        assert stored_policies == patch_policies
    finally:
        release_full_replacement.set()
        tasks = [task for task in (full_save, policy_patch) if task is not None]
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


def test_patch_draft_instructions_for_non_default_does_not_overwrite_default():
    app_state, draft_state, draft_agent = _states()
    client = _client(app_state, draft_state)

    response = client.patch(
        "/api/manage/config/draft/special_instructions",
        params={"agent_id": "sales-east"},
        json={"special_instructions": "east-only"},
    )

    assert response.status_code == 200
    assert draft_agent.special_instructions == "default-draft"
    assert ("sales-east", True) not in app_state.agent_graphs_cache
