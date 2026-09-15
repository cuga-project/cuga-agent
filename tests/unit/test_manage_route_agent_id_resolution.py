"""Regression tests for agent-ID resolution at the Manage route boundary.

Covers:
1. Registry-disabled alias: arbitrary names resolve to cuga-default; alias rows
   are never created or mutated.
2. Registry-enabled unknown name: each newly resolved handler returns 404 before
   request parsing or side effects (body parse, persistence, lock, cache, policy,
   registry reload, knowledge ops).
3. Registry-enabled persisted name: resolved identity flows through to persistence,
   lock keys, response agent_id, cache invalidation, etc.
4. Error preservation: broad exception blocks do not mask resolver 404 or
   route-generated 4xx values.
5. Supervisor contract: registry-disabled returns 404; registry-enabled unknown
   name returns 404 without parsing.
"""

from __future__ import annotations

import asyncio
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from cuga.backend.server.auth import require_auth
from cuga.backend.server.config_store import reset_config_db
from cuga.backend.server.manage_routes import router
from cuga.backend.server.manage_routes.config_routes import delete_manage_config
from cuga.backend.server.manage_routes.draft_routes import (
    patch_draft_agent,
    patch_draft_llm,
    patch_draft_policies,
    patch_draft_special_instructions,
    patch_draft_supervisor,
    patch_draft_tools,
)
from cuga.backend.server.manage_routes.knowledge_routes import (
    patch_draft_knowledge,
    reindex_for_config_change,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


def _client() -> tuple[TestClient, FastAPI]:
    """Return a client and app with an isolated config database."""
    reset_config_db()
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[require_auth] = lambda: None
    app.state.app_state = SimpleNamespace(
        knowledge_engine=None,
        agent=None,
        config_version=None,
        tools_include_version=0,
        agent_graphs_cache={},
        agent_graph_generations={},
    )
    app.state.draft_app_state = SimpleNamespace(
        current_llm=None,
        agent=None,
        policy_system=None,
        tools_include_by_app=None,
        tools_include_version=0,
    )
    client = TestClient(app)
    return client, app


# ---------------------------------------------------------------------------
# 1. Registry-disabled alias tests
# ---------------------------------------------------------------------------


class TestRegistryDisabledAlias:
    """With registry disabled, any alias resolves to cuga-default."""

    def test_get_config_alias_returns_cuga_default(self, monkeypatch):
        monkeypatch.setattr("cuga.backend.server.agent_registry.is_agent_registry_enabled", lambda: False)
        client, _ = _client()
        resp = client.get("/api/manage/config", params={"agent_id": "my-alias"})
        assert resp.status_code == 200
        assert resp.json()["agent_id"] == "cuga-default"

    def test_get_config_alias_does_not_create_alias_row(self, monkeypatch):
        monkeypatch.setattr("cuga.backend.server.agent_registry.is_agent_registry_enabled", lambda: False)
        import asyncio
        from cuga.backend.server.config_store import load_config

        client, _ = _client()
        client.get("/api/manage/config", params={"agent_id": "my-alias"})
        published, _ = asyncio.run(load_config(None, "my-alias"))
        assert published is None, "alias row must not be created"

    def test_patch_draft_llm_alias_returns_cuga_default(self, monkeypatch):
        monkeypatch.setattr("cuga.backend.server.agent_registry.is_agent_registry_enabled", lambda: False)
        client, _ = _client()
        resp = client.patch(
            "/api/manage/config/draft/llm",
            params={"agent_id": "my-alias"},
            json={"llm": {"model": "gpt-4o"}},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["agent_id"] == "cuga-default"

    def test_patch_draft_llm_alias_does_not_write_alias_row(self, monkeypatch):
        monkeypatch.setattr("cuga.backend.server.agent_registry.is_agent_registry_enabled", lambda: False)
        import asyncio
        from cuga.backend.server.config_store import load_draft

        client, _ = _client()
        client.patch(
            "/api/manage/config/draft/llm",
            params={"agent_id": "my-alias"},
            json={"llm": {"model": "gpt-4o"}},
        )
        alias_draft = asyncio.run(load_draft("my-alias"))
        assert alias_draft is None, "alias draft row must not be created"

        cuga_draft = asyncio.run(load_draft("cuga-default"))
        assert cuga_draft is not None, "default row should be written"


# ---------------------------------------------------------------------------
# 2. Registry-enabled unknown name — 404 before side effects
# ---------------------------------------------------------------------------


def _unknown_agent_setup(monkeypatch, *, registered: list[str] | None = None):
    """Enable registry and set registered agents list."""
    monkeypatch.setattr("cuga.backend.server.agent_registry.is_agent_registry_enabled", lambda: True)
    rows = [{"agent_id": a} for a in (registered or [])]
    monkeypatch.setattr(
        "cuga.backend.server.config_store.list_agents_with_configs",
        AsyncMock(return_value=rows),
    )


UNKNOWN = "unknown-agent"


@pytest.mark.parametrize(
    "endpoint,method,json_body,params",
    [
        ("/api/manage/config", "GET", None, {"agent_id": UNKNOWN}),
        ("/api/manage/config/history", "GET", None, {"agent_id": UNKNOWN}),
        ("/api/manage/config", "DELETE", None, {"agent_id": UNKNOWN}),
        ("/api/manage/config/draft/llm", "PATCH", {"llm": {}}, {"agent_id": UNKNOWN}),
        ("/api/manage/config/draft/tools", "PATCH", {"tools": []}, {"agent_id": UNKNOWN}),
        ("/api/manage/config/draft/agent", "PATCH", {"agent": {"name": "X"}}, {"agent_id": UNKNOWN}),
        (
            "/api/manage/config/draft/special_instructions",
            "PATCH",
            {"special_instructions": "hi"},
            {"agent_id": UNKNOWN},
        ),
        ("/api/manage/config/draft/policies", "PATCH", {"policies": []}, {"agent_id": UNKNOWN}),
        ("/api/manage/config/draft/knowledge", "PATCH", {"knowledge": {}}, {"agent_id": UNKNOWN}),
        ("/api/manage/knowledge/reindex_for_config", "POST", None, {"agent_id": UNKNOWN}),
    ],
)
def test_unknown_agent_returns_404(monkeypatch, endpoint, method, json_body, params):
    """Every newly resolved route returns 404 for an unknown registry-enabled ID."""
    _unknown_agent_setup(monkeypatch)
    client, _ = _client()
    fn = getattr(client, method.lower())
    kwargs = {"params": params}
    if json_body is not None:
        kwargs["json"] = json_body
    resp = fn(endpoint, **kwargs)
    assert resp.status_code == 404, (
        f"{endpoint} should 404 for unknown agent, got {resp.status_code}: {resp.text}"
    )


def test_unknown_agent_get_config_no_persistence(monkeypatch):
    """GET config for unknown agent must not call load_draft or load_config."""
    _unknown_agent_setup(monkeypatch)
    load_draft = AsyncMock()
    load_config = AsyncMock()
    monkeypatch.setattr("cuga.backend.server.config_store.load_draft", load_draft)
    monkeypatch.setattr("cuga.backend.server.config_store.load_config", load_config)
    client, _ = _client()
    resp = client.get("/api/manage/config", params={"agent_id": UNKNOWN})
    assert resp.status_code == 404
    load_draft.assert_not_awaited()
    load_config.assert_not_awaited()


def test_unknown_agent_patch_llm_no_body_parse_no_persistence(monkeypatch):
    """PATCH llm for unknown agent must not parse the body or write to draft."""
    _unknown_agent_setup(monkeypatch)
    save_draft = AsyncMock()
    monkeypatch.setattr("cuga.backend.server.config_store.save_draft", save_draft)
    monkeypatch.setattr("cuga.backend.server.config_store.load_draft", AsyncMock(return_value={}))

    request_mock = SimpleNamespace(
        json=AsyncMock(),
        app=SimpleNamespace(state=SimpleNamespace(draft_app_state=None, app_state=None)),
    )
    import asyncio
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(patch_draft_llm(request_mock, agent_id=UNKNOWN))
    assert exc_info.value.status_code == 404
    request_mock.json.assert_not_awaited()
    save_draft.assert_not_awaited()


def test_unknown_agent_patch_tools_no_side_effects(monkeypatch):
    """PATCH tools rejects an unknown ID before parsing, locking, persistence, or reload."""
    _unknown_agent_setup(monkeypatch)
    lock = Mock()
    patch_draft = AsyncMock()
    registry_url = Mock(return_value="http://registry.test")
    monkeypatch.setattr("cuga.backend.server.manage_routes.draft_routes.agent_draft_lock", lock)
    monkeypatch.setattr("cuga.backend.server.manage_routes.draft_routes.load_and_patch_draft", patch_draft)
    monkeypatch.setitem(
        sys.modules,
        "cuga.backend.tools_env.registry.utils.api_utils",
        SimpleNamespace(get_registry_base_url=registry_url),
    )

    request_mock = SimpleNamespace(
        json=AsyncMock(),
        app=SimpleNamespace(state=SimpleNamespace(draft_app_state=None, app_state=None)),
    )

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(patch_draft_tools(request_mock, agent_id=UNKNOWN))
    assert exc_info.value.status_code == 404
    request_mock.json.assert_not_awaited()
    lock.assert_not_called()
    patch_draft.assert_not_awaited()
    registry_url.assert_not_called()


def test_unknown_agent_patch_agent_no_side_effects(monkeypatch):
    """PATCH agent for unknown agent must not parse body or write draft."""
    _unknown_agent_setup(monkeypatch)
    save_draft = AsyncMock()
    monkeypatch.setattr("cuga.backend.server.config_store.save_draft", save_draft)

    request_mock = SimpleNamespace(
        json=AsyncMock(),
        app=SimpleNamespace(state=SimpleNamespace(draft_app_state=None, app_state=None)),
    )
    import asyncio
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(patch_draft_agent(request_mock, agent_id=UNKNOWN))
    assert exc_info.value.status_code == 404
    request_mock.json.assert_not_awaited()
    save_draft.assert_not_awaited()


def test_unknown_agent_patch_policies_no_side_effects(monkeypatch):
    """PATCH policies rejects an unknown ID before parsing, locking, or persistence."""
    _unknown_agent_setup(monkeypatch)
    lock = Mock()
    patch_draft = AsyncMock()
    monkeypatch.setattr("cuga.backend.server.manage_routes.draft_routes.agent_draft_lock", lock)
    monkeypatch.setattr("cuga.backend.server.manage_routes.draft_routes.load_and_patch_draft", patch_draft)

    request_mock = SimpleNamespace(
        json=AsyncMock(),
        app=SimpleNamespace(state=SimpleNamespace(draft_app_state=None, app_state=None)),
    )

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(patch_draft_policies(request_mock, agent_id=UNKNOWN))
    assert exc_info.value.status_code == 404
    request_mock.json.assert_not_awaited()
    lock.assert_not_called()
    patch_draft.assert_not_awaited()


def test_unknown_agent_knowledge_routes_have_no_side_effects(monkeypatch):
    """Knowledge routes reject unknown IDs before parsing, locking, or migration."""
    _unknown_agent_setup(monkeypatch)
    lock = AsyncMock()
    save = AsyncMock()
    migrate = AsyncMock()
    monkeypatch.setattr("cuga.backend.server.manage_routes.knowledge_routes.agent_draft_lock", lock)
    monkeypatch.setattr(
        "cuga.backend.server.manage_routes.knowledge_routes.save_draft_section_unlocked", save
    )
    monkeypatch.setattr(
        "cuga.backend.server.manage_routes.knowledge_routes.migrate_and_reindex_for_agent", migrate
    )

    request = SimpleNamespace(
        json=AsyncMock(),
        app=SimpleNamespace(state=SimpleNamespace(app_state=None, draft_app_state=None)),
    )
    import asyncio
    from fastapi import HTTPException

    for handler in (patch_draft_knowledge, reindex_for_config_change):
        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(handler(request, agent_id=UNKNOWN))
        assert exc_info.value.status_code == 404

    request.json.assert_not_awaited()
    lock.assert_not_called()
    save.assert_not_awaited()
    migrate.assert_not_awaited()


def test_unknown_agent_delete_config_no_side_effects(monkeypatch):
    """DELETE config for unknown agent must not call delete_all_configs."""
    _unknown_agent_setup(monkeypatch)
    delete_all_configs = AsyncMock()
    monkeypatch.setattr("cuga.backend.server.config_store.delete_all_configs", delete_all_configs)
    import asyncio
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(delete_manage_config(agent_id=UNKNOWN))
    assert exc_info.value.status_code == 404
    delete_all_configs.assert_not_awaited()


def test_delete_config_reset_db_does_not_require_registration(monkeypatch):
    """reset_db=True is global and must not require agent registration."""
    _unknown_agent_setup(monkeypatch)
    import asyncio

    resp_coroutine = delete_manage_config(agent_id=None, reset_db=True)
    result = asyncio.run(resp_coroutine)
    # Should succeed (reset is non-agent-scoped)
    assert result.status_code == 200


# ---------------------------------------------------------------------------
# 3. Registry-enabled persisted name — identity propagation
# ---------------------------------------------------------------------------


REGISTERED = "my-registered-agent"


def _registry_enabled_with_agent(monkeypatch, agent_id: str = REGISTERED):
    monkeypatch.setattr("cuga.backend.server.agent_registry.is_agent_registry_enabled", lambda: True)
    monkeypatch.setattr(
        "cuga.backend.server.config_store.list_agents_with_configs",
        AsyncMock(return_value=[{"agent_id": agent_id}]),
    )


def test_get_config_discovers_persisted_registered_agent(monkeypatch):
    """GET resolves membership through a real persisted registry row."""
    monkeypatch.setattr("cuga.backend.server.agent_registry.is_agent_registry_enabled", lambda: True)
    from cuga.backend.server.config_store import save_draft

    client, _ = _client()
    asyncio.run(save_draft({"agent": {"name": "My Agent"}}, REGISTERED))

    resp = client.get("/api/manage/config", params={"agent_id": REGISTERED, "draft": "1"})

    assert resp.status_code == 200
    assert resp.json()["agent_id"] == REGISTERED
    assert resp.json()["config"]["agent"]["name"] == "My Agent"


def test_patch_draft_llm_persisted_agent_uses_resolved_id(monkeypatch):
    """PATCH llm for a registered agent writes the draft under the registered ID."""
    _registry_enabled_with_agent(monkeypatch)
    import asyncio
    from cuga.backend.server.config_store import load_draft

    client, _ = _client()
    resp = client.patch(
        "/api/manage/config/draft/llm",
        params={"agent_id": REGISTERED},
        json={"llm": {"model": "gpt-4o"}},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["agent_id"] == REGISTERED
    draft = asyncio.run(load_draft(REGISTERED))
    assert draft is not None
    assert (draft.get("llm") or {}).get("model") == "gpt-4o"


def test_patch_draft_agent_persisted_agent_uses_resolved_id(monkeypatch):
    """PATCH agent section for a registered agent writes under the registered ID."""
    _registry_enabled_with_agent(monkeypatch)
    import asyncio
    from cuga.backend.server.config_store import load_draft

    client, _ = _client()
    resp = client.patch(
        "/api/manage/config/draft/agent",
        params={"agent_id": REGISTERED},
        json={"agent": {"name": "My Renamed Agent"}},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["agent_id"] == REGISTERED
    draft = asyncio.run(load_draft(REGISTERED))
    assert draft is not None
    assert (draft.get("agent") or {}).get("name") == "My Renamed Agent"


def test_patch_draft_special_instructions_persisted_agent(monkeypatch):
    """PATCH special_instructions for a registered agent uses the registered ID."""
    _registry_enabled_with_agent(monkeypatch)
    import asyncio
    from cuga.backend.server.config_store import load_draft

    client, _ = _client()
    resp = client.patch(
        "/api/manage/config/draft/special_instructions",
        params={"agent_id": REGISTERED},
        json={"special_instructions": "Hello from registered agent"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["agent_id"] == REGISTERED
    draft = asyncio.run(load_draft(REGISTERED))
    assert (draft or {}).get("special_instructions") == "Hello from registered agent"


def test_patch_draft_knowledge_persisted_agent_uses_resolved_id(monkeypatch):
    """Knowledge persistence receives the registered identity."""
    _registry_enabled_with_agent(monkeypatch)
    saved = AsyncMock(return_value={"knowledge": {}})
    monkeypatch.setattr("cuga.backend.server.config_store.load_draft", AsyncMock(return_value={}))
    monkeypatch.setattr(
        "cuga.backend.server.manage_routes.knowledge_routes.save_draft_section_unlocked",
        saved,
    )
    request = SimpleNamespace(
        json=AsyncMock(return_value={"knowledge": {}}),
        app=SimpleNamespace(state=SimpleNamespace(app_state=None, draft_app_state=None)),
    )
    import asyncio

    response = asyncio.run(patch_draft_knowledge(request, agent_id=REGISTERED))

    assert response.status_code == 200
    saved.assert_awaited_once()
    assert saved.await_args.args[0] == REGISTERED


def test_reindex_persisted_agent_uses_resolved_id(monkeypatch):
    """Reindex migration receives the registered identity."""
    _registry_enabled_with_agent(monkeypatch)
    engine = SimpleNamespace(_reindex_in_progress=set())
    state = SimpleNamespace(knowledge_engine=engine)
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(app_state=state)))
    migrate = AsyncMock(return_value={"triggered": True})
    monkeypatch.setattr(
        "cuga.backend.server.manage_routes.knowledge_routes.migrate_and_reindex_for_agent",
        migrate,
    )
    import asyncio

    response = asyncio.run(reindex_for_config_change(request, agent_id=REGISTERED))

    assert response.status_code == 200
    migrate.assert_awaited_once_with(REGISTERED, engine, state)


def test_get_config_history_uses_resolved_agent(monkeypatch):
    """History lookup receives the registry-owned identity."""
    _registry_enabled_with_agent(monkeypatch)
    list_versions = AsyncMock(return_value=[{"version": "7"}])
    monkeypatch.setattr("cuga.backend.server.config_store.list_versions", list_versions)
    client, _ = _client()

    resp = client.get("/api/manage/config/history", params={"agent_id": REGISTERED})

    assert resp.status_code == 200
    assert resp.json()["versions"] == [{"version": "7"}]
    list_versions.assert_awaited_once_with(REGISTERED)


# ---------------------------------------------------------------------------
# 4. Error preservation
# ---------------------------------------------------------------------------


def test_patch_draft_agent_400_not_masked_as_500(monkeypatch):
    """patch_draft_agent's 400 for empty name must not be masked as 500."""
    _registry_enabled_with_agent(monkeypatch)
    client, _ = _client()
    resp = client.patch(
        "/api/manage/config/draft/agent",
        params={"agent_id": REGISTERED},
        json={"agent": {"name": ""}},
    )
    assert resp.status_code == 400
    assert "name" in resp.json().get("detail", "").lower()


def test_patch_draft_agent_400_missing_name_not_masked(monkeypatch):
    """patch_draft_agent's 400 for missing name field must not be masked as 500."""
    _registry_enabled_with_agent(monkeypatch)
    client, _ = _client()
    resp = client.patch(
        "/api/manage/config/draft/agent",
        params={"agent_id": REGISTERED},
        json={"agent": {"description": "no name field"}},
    )
    assert resp.status_code == 400


@pytest.mark.parametrize("handler", [patch_draft_tools, patch_draft_special_instructions])
def test_draft_patch_http_exception_is_preserved(monkeypatch, handler):
    """Broad draft handlers preserve explicit downstream HTTP errors."""
    _registry_enabled_with_agent(monkeypatch)
    expected = HTTPException(status_code=422, detail="invalid section")
    monkeypatch.setattr(
        "cuga.backend.server.manage_routes.draft_routes.load_and_patch_draft",
        AsyncMock(side_effect=expected),
    )
    request = SimpleNamespace(
        json=AsyncMock(return_value={}),
        app=SimpleNamespace(state=SimpleNamespace(draft_app_state=None, app_state=None)),
    )

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(handler(request, agent_id=REGISTERED))

    assert exc_info.value is expected


def test_patch_draft_policies_http_exception_is_preserved(monkeypatch):
    """The named-policy persistence path preserves explicit HTTP errors."""
    _registry_enabled_with_agent(monkeypatch)
    expected = HTTPException(status_code=422, detail="invalid policies")
    monkeypatch.setattr(
        "cuga.backend.server.manage_routes.draft_routes.save_draft_section_unlocked",
        AsyncMock(side_effect=expected),
    )
    request = SimpleNamespace(
        json=AsyncMock(return_value={"policies": []}),
        app=SimpleNamespace(state=SimpleNamespace(draft_app_state=None, app_state=None)),
    )

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(patch_draft_policies(request, agent_id=REGISTERED))

    assert exc_info.value is expected


def test_reindex_engine_not_ready_503_is_preserved(monkeypatch):
    monkeypatch.setattr("cuga.backend.server.agent_registry.is_agent_registry_enabled", lambda: False)
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(app_state=None)))
    import asyncio
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(reindex_for_config_change(request, agent_id="alias"))

    assert exc_info.value.status_code == 503


def test_reindex_prelock_409_is_preserved(monkeypatch):
    monkeypatch.setattr("cuga.backend.server.agent_registry.is_agent_registry_enabled", lambda: False)
    collection = "kb_agent_cuga_default_active"
    engine = SimpleNamespace(_reindex_in_progress={collection})
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(app_state=SimpleNamespace(knowledge_engine=engine)))
    )
    import asyncio
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(reindex_for_config_change(request, agent_id="alias"))

    assert exc_info.value.status_code == 409


def test_reindex_lock_protected_409_is_preserved(monkeypatch):
    monkeypatch.setattr("cuga.backend.server.agent_registry.is_agent_registry_enabled", lambda: False)
    collection = "kb_agent_cuga_default_active"
    engine = SimpleNamespace(_reindex_in_progress=set())
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(app_state=SimpleNamespace(knowledge_engine=engine)))
    )

    class _RaceLock:
        async def __aenter__(self):
            engine._reindex_in_progress.add(collection)

        async def __aexit__(self, *_args):
            engine._reindex_in_progress.discard(collection)

    monkeypatch.setattr(
        "cuga.backend.server.manage_routes.knowledge_routes.agent_draft_lock",
        lambda _agent_id: _RaceLock(),
    )
    import asyncio
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(reindex_for_config_change(request, agent_id="alias"))

    assert exc_info.value.status_code == 409


def test_patch_draft_policies_404_not_masked_as_500(monkeypatch):
    """patch_draft_policies resolver 404 for unknown ID must not be masked as 500."""
    _unknown_agent_setup(monkeypatch)
    client, _ = _client()
    resp = client.patch(
        "/api/manage/config/draft/policies",
        params={"agent_id": UNKNOWN},
        json={"policies": []},
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# 5. Supervisor contract
# ---------------------------------------------------------------------------


class TestSupervisorContract:
    """patch_draft_supervisor: registry-disabled returns 404 without parsing.
    Registry-enabled unknown name returns 404 without parsing/locking.
    Persisted name preserves stale-write 409.
    """

    def test_supervisor_404_when_registry_disabled(self, monkeypatch):
        monkeypatch.setattr("cuga.backend.server.agent_registry.is_agent_registry_enabled", lambda: False)
        client, _ = _client()
        resp = client.patch(
            "/api/manage/config/draft/supervisor",
            params={"agent_id": "trip-supervisor"},
            json={"supervisor": {"subAgents": [], "planApproval": False}},
        )
        assert resp.status_code == 404
        assert "registry" in resp.json()["detail"].lower()

    def test_supervisor_disabled_no_body_parse(self, monkeypatch):
        """When registry is disabled, body must not be read."""
        monkeypatch.setattr("cuga.backend.server.agent_registry.is_agent_registry_enabled", lambda: False)
        import asyncio
        from fastapi import HTTPException

        request_mock = SimpleNamespace(
            json=AsyncMock(),
            app=SimpleNamespace(state=SimpleNamespace(draft_app_state=None, app_state=None)),
        )
        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(patch_draft_supervisor(request_mock, agent_id="trip-supervisor"))
        assert exc_info.value.status_code == 404
        request_mock.json.assert_not_awaited()

    def test_supervisor_unknown_agent_registry_enabled_404(self, monkeypatch):
        """Registry enabled + unknown name returns 404 before body parse."""
        _unknown_agent_setup(monkeypatch)
        import asyncio
        from fastapi import HTTPException

        request_mock = SimpleNamespace(
            json=AsyncMock(),
            app=SimpleNamespace(state=SimpleNamespace(draft_app_state=None, app_state=None)),
        )
        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(patch_draft_supervisor(request_mock, agent_id=UNKNOWN))
        assert exc_info.value.status_code == 404
        request_mock.json.assert_not_awaited()

    def test_supervisor_stale_409_preserved(self, monkeypatch):
        """Stale saveSeq write returns 409 (not 500) for a registered agent."""
        monkeypatch.setattr("cuga.backend.server.agent_registry.is_agent_registry_enabled", lambda: True)
        monkeypatch.setattr(
            "cuga.backend.server.config_store.list_agents_with_configs",
            AsyncMock(return_value=[{"agent_id": "trip-supervisor"}]),
        )
        client, _ = _client()
        first = client.patch(
            "/api/manage/config/draft/supervisor",
            params={"agent_id": "trip-supervisor"},
            json={"supervisor": {"subAgents": [], "planApproval": False}, "saveSeq": 2},
        )
        assert first.status_code == 200
        stale = client.patch(
            "/api/manage/config/draft/supervisor",
            params={"agent_id": "trip-supervisor"},
            json={"supervisor": {"subAgents": [], "planApproval": False}, "saveSeq": 1},
        )
        assert stale.status_code == 409


# ---------------------------------------------------------------------------
# 6. Resolver 404 preserved through existing test_draft_autosave tests
#    (extra boundary sanity for already-resolved handlers)
# ---------------------------------------------------------------------------


def test_save_manage_config_draft_unknown_agent_no_side_effects(monkeypatch):
    """Full draft save for unknown registry-enabled agent: 404 before body parse or save."""
    from cuga.backend.server.manage_routes.draft_routes import save_manage_config_draft

    _unknown_agent_setup(monkeypatch)
    save_draft = AsyncMock()
    monkeypatch.setattr("cuga.backend.server.config_store.save_draft", save_draft)

    import asyncio
    from fastapi import HTTPException

    request_mock = SimpleNamespace(
        json=AsyncMock(),
        app=SimpleNamespace(state=SimpleNamespace(draft_app_state=None, app_state=None)),
    )
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(save_manage_config_draft(request_mock, agent_id=UNKNOWN))
    assert exc_info.value.status_code == 404
    request_mock.json.assert_not_awaited()
    save_draft.assert_not_awaited()
