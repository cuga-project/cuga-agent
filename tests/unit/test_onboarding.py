import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock
from contextlib import nullcontext

import pytest
from fastapi import HTTPException

from cuga.backend.server.onboarding import config_fingerprint, validate_llm

pytestmark = pytest.mark.unit


@pytest.mark.asyncio
async def test_validation_calls_selected_configuration_with_limits(monkeypatch):
    invoke = AsyncMock(return_value="OK")
    received = []

    def create(config):
        received.append(config)
        return SimpleNamespace(ainvoke=invoke)

    monkeypatch.setitem(
        sys.modules, "cuga.backend.llm.models", SimpleNamespace(create_llm_from_config=create)
    )
    await validate_llm(
        {"provider": "openrouter", "model": "selected", "api_key": "db://local"}  # pragma: allowlist secret
    )  # pragma: allowlist secret
    assert received == [
        {
            "provider": "openrouter",
            "model": "selected",
            "api_key": "db://local",  # pragma: allowlist secret
            "timeout": 20,
            "max_tokens": 32,
        }
    ]
    invoke.assert_awaited_once_with("Reply with OK.")


@pytest.mark.asyncio
async def test_validation_does_not_expose_provider_errors(monkeypatch):
    invoke = AsyncMock(side_effect=RuntimeError("private-credential-in-error"))
    monkeypatch.setitem(
        sys.modules,
        "cuga.backend.llm.models",
        SimpleNamespace(create_llm_from_config=lambda _: SimpleNamespace(ainvoke=invoke)),
    )
    with pytest.raises(HTTPException) as error:
        await validate_llm({"model": "selected"})
    assert error.value.status_code == 400
    assert "private-credential" not in error.value.detail


@pytest.mark.asyncio
async def test_missing_model_does_not_load_provider(monkeypatch):
    with pytest.raises(HTTPException) as error:
        await validate_llm({})
    assert error.value.status_code == 422


def test_configuration_change_requires_revalidation():
    assert config_fingerprint({"model": "a", "provider": "openai"}) == config_fingerprint(
        {"provider": "openai", "model": "a"}
    )
    assert config_fingerprint({"model": "a"}) != config_fingerprint({"model": "b"})


@pytest.mark.asyncio
@pytest.mark.parametrize("guided", [True, False])
async def test_knowledge_patch_rebuild_preserves_guided_model_and_container_context(monkeypatch, guided):
    import json

    from cuga.backend.llm import models
    from cuga.backend.secrets.secret_resolver import get_secret_agent_id
    from cuga.backend.server import config_store
    from cuga.backend.server.manage_routes import knowledge_routes

    monkeypatch.setenv("CUGA_GUIDED_SETUP", "true" if guided else "false")
    llm = {"provider": "openrouter", "model": "selected"}
    config = {"llm": llm, "knowledge": {"enabled": False}}
    monkeypatch.setattr(config_store, "load_draft", AsyncMock(return_value=config))
    monkeypatch.setattr(
        knowledge_routes, "resolve_registered_agent_id", AsyncMock(return_value="cuga-default")
    )
    monkeypatch.setattr(knowledge_routes, "save_draft_section_unlocked", AsyncMock(return_value=config))
    observations = []

    async def build():
        observations.append((models.get_current_llm_override(), get_secret_agent_id()))

    graph = SimpleNamespace(tool_provider=None, build_graph=AsyncMock(side_effect=build))
    state = SimpleNamespace(agent=graph)
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(app_state=None, draft_app_state=state)),
        json=AsyncMock(return_value={"knowledge": {"enabled": False}}),
    )

    class RegistryClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def post(self, *args, **kwargs):
            return SimpleNamespace(raise_for_status=lambda: None)

    monkeypatch.setattr(knowledge_routes.httpx, "AsyncClient", RegistryClient)
    with models.llm_config_context({"provider": "groq", "model": "container-model"}):
        response = await knowledge_routes.patch_draft_knowledge(request, "cuga-default")
        assert models.get_current_llm_override()["model"] == "container-model"
    assert response.status_code == 200
    assert json.loads(response.body)["status"] == "success"
    graph.build_graph.assert_awaited_once()
    assert graph.llm_config == llm
    override, secret_agent_id = observations[0]
    assert override["model"] == ("selected" if guided else "container-model")
    assert secret_agent_id == ("cuga-default" if guided else None)
    assert models.get_current_llm_override() is None
    assert get_secret_agent_id() is None


@pytest.mark.asyncio
async def test_deferred_draft_graph_uses_saved_policies_and_dynamic_tool_selection(monkeypatch):
    from cuga.backend.server import config_store
    from cuga.backend.server.onboarding import ensure_default_agent, runtime_llm_fingerprint

    config = {
        "llm": {"provider": "openai", "model": "selected"},
        "policies": {"policies": [{"id": "guard"}]},
        "tools": [{"name": "draft-service", "include": ["read"]}],
    }
    monkeypatch.setattr(config_store, "load_draft", AsyncMock(return_value=config))
    created = []

    def graph_factory(*args, **kwargs):
        created.append(kwargs)
        return SimpleNamespace(build_graph=AsyncMock())

    policy = AsyncMock(return_value=object())
    monkeypatch.setitem(
        sys.modules, "cuga.backend.cuga_graph.entry_graph", SimpleNamespace(CugaEntryGraph=graph_factory)
    )
    monkeypatch.setitem(
        sys.modules,
        "cuga.backend.cuga_graph.nodes.cuga_lite.providers.combined",
        SimpleNamespace(CombinedToolProvider=lambda **kwargs: kwargs),
    )
    monkeypatch.setitem(
        sys.modules,
        "cuga.backend.cuga_graph.policy.configurable",
        SimpleNamespace(create_agent_policy_system=policy),
    )
    monkeypatch.setitem(
        sys.modules,
        "cuga.backend.llm.models",
        SimpleNamespace(create_llm_from_config=lambda _: object(), llm_config_context=nullcontext),
    )
    monkeypatch.delenv("CUGA_LOAD_POLICIES", raising=False)
    prod = SimpleNamespace(agent=None, agent_graph_build_locks={})
    draft = SimpleNamespace(agent=None)
    result = await ensure_default_agent(None, prod, draft, True)
    assert draft.agent is result and prod.agent is None
    assert policy.call_args.kwargs["policies_data"] == [{"id": "guard"}]
    provider = created[0]["tool_provider"]
    assert provider["agent_id"] == "cuga-default--draft"
    assert provider["get_include_by_app"]() == ({"draft-service": ["read"]}, 0)
    draft.tools_include_by_app = {"draft-service": ["other"]}
    draft.tools_include_version = 1
    assert provider["get_include_by_app"]() == ({"draft-service": ["other"]}, 1)
    assert await ensure_default_agent(None, prod, draft, True) is result
    assert len(created) == 1

    # PATCH updates llm_config in place, but captured models must be rebuilt.
    config["llm"] = {
        "provider": "openrouter",
        "model": "replacement",
        "api_key": "db://second",  # pragma: allowlist secret
    }  # pragma: allowlist secret
    result.llm_config = config["llm"]
    replacement = await ensure_default_agent(None, prod, draft, True)
    assert replacement is not result
    assert created[1]["llm_config"] == config["llm"]
    assert policy.await_count == 2
    assert prod.retired_guided_graphs == [result]
    assert draft.built_llm_fingerprint == runtime_llm_fingerprint(config["llm"])
    config["llm"]["max_tokens"] = 2048
    assert await ensure_default_agent(None, prod, draft, True) is not replacement
    assert len(created) == 3


def test_guided_knowledge_ownership_survives_working_directory_change(monkeypatch, tmp_path):
    from cuga.backend.knowledge.session_provider import PersistentSessionProvider
    from cuga.backend.server.onboarding import knowledge_session_path

    monkeypatch.setenv("CUGA_GUIDED_SETUP", "true")
    path = tmp_path / "persistent-knowledge"
    provider = PersistentSessionProvider(knowledge_session_path(path))
    session = provider.get_or_create_session("thread", user_id="owner", tenant_id="tenant")
    session.filenames = ["contract.pdf"]
    provider.save_session("thread", session)
    agent = provider.get_or_create_agent("cuga-default", "1")
    agent.filenames = ["handbook.pdf"]
    provider.save_agent(agent)
    other = tmp_path / "another-directory"
    other.mkdir()
    monkeypatch.chdir(other)
    restarted = PersistentSessionProvider(knowledge_session_path(path))
    assert restarted.get_session("thread").filenames == ["contract.pdf"]
    assert restarted.get_agent("cuga-default:1").filenames == ["handbook.pdf"]
    assert restarted.check_session_access("thread", "owner", "tenant")
    assert not restarted.check_session_access("thread", "other-user", "tenant")
    monkeypatch.delenv("CUGA_GUIDED_SETUP")
    assert knowledge_session_path(path) == other / ".cuga/session_knowledge.json"


@pytest.mark.asyncio
async def test_guided_startup_is_available_when_saved_knowledge_initialization_fails(monkeypatch):
    from cuga.backend.server import config_store, managed_mcp
    from cuga.backend.server.onboarding import manager_lifespan
    from cuga.backend.storage import facade
    from cuga.backend.knowledge.config import KnowledgeConfig

    monkeypatch.setattr(
        config_store, "load_config", AsyncMock(return_value=({"knowledge": {"enabled": True}}, "1"))
    )
    monkeypatch.setattr(managed_mcp, "write_managed_mcp_yaml", lambda *args: None)
    monkeypatch.setattr(KnowledgeConfig, "coerce_and_validate", lambda _: SimpleNamespace(enabled=True))
    engine = SimpleNamespace(aclose=AsyncMock(), shutdown=lambda: None)
    statuses = {}
    storage = SimpleNamespace(close_relational_stores=AsyncMock())
    monkeypatch.setattr(facade, "get_storage", lambda: storage)

    async def fail(state, config):
        state.knowledge_engine = engine
        raise RuntimeError("Cannot acquire knowledge lock")

    state = SimpleNamespace(
        initialize_knowledge_engine=fail,
        set_subsystem_status=lambda name, status, *args: statuses.update({name: status}),
        background_tasks=[],
        knowledge_engine=None,
        agent=None,
        agent_graphs_cache={},
    )
    async with manager_lifespan(state, SimpleNamespace(agent=None)):
        assert state.knowledge_engine is None
        assert statuses["knowledge"] == "failed"
        engine.aclose.assert_awaited_once()
    storage.close_relational_stores.assert_awaited_once()


@pytest.mark.asyncio
async def test_run_builds_published_guided_graph_before_first_stream(monkeypatch):
    import httpx
    from fastapi import FastAPI
    from cuga.backend.server import main, onboarding, run_routes
    from cuga.backend.llm import models

    monkeypatch.setenv("CUGA_GUIDED_SETUP", "true")
    llm = {"provider": "openai", "model": "saved-model", "api_key": "db://local"}  # pragma: allowlist secret
    graph = SimpleNamespace(llm_config=llm)
    published = SimpleNamespace(agent=None, current_llm=object())
    draft = SimpleNamespace(agent=None)

    async def ensure(request, app_state, draft_state, use_draft):
        assert app_state is published and draft_state is draft and not use_draft
        app_state.agent = graph
        return graph

    build = AsyncMock(side_effect=ensure)
    monkeypatch.setattr(onboarding, "ensure_default_agent", build)
    monkeypatch.setattr(run_routes, "_run_auth_failure", AsyncMock(return_value=None))
    monkeypatch.setattr(run_routes, "_authenticated_user_id", AsyncMock(return_value=None))
    monkeypatch.setattr(run_routes, "_get_supervisor", AsyncMock(return_value=None))
    monkeypatch.setattr(main, "app_state", published)

    async def stream(*args, **kwargs):
        assert kwargs["agent"] is graph
        assert kwargs["current_llm"] is published.current_llm
        assert models.get_current_llm_override()["model"] == "saved-model"
        yield 'event: Answer\ndata: {"data": "first task completed"}\n\n'

    monkeypatch.setattr(main, "event_stream", stream)
    app = FastAPI()
    app.state.app_state, app.state.draft_app_state = published, draft
    app.include_router(
        run_routes.build_run_router(event_stream=main.configured_event_stream, default_user_id="local")
    )
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        result = await client.post("/run", json={"query": "Hello"})
    assert result.status_code == 200
    assert result.json()["answer"] == "first task completed"
    assert result.json()["ok"] is True
    build.assert_awaited_once()
    assert models.get_current_llm_override() is None


@pytest.mark.asyncio
@pytest.mark.parametrize("preset", ["default", "crm", "knowledge", "digital_sales", "health", "docs"])
async def test_ubi_stream_preserves_existing_provider_selection(monkeypatch, preset):
    from cuga.backend.server import main
    from cuga.backend.llm import models
    from cuga.backend.secrets.secret_resolver import get_secret_agent_id

    monkeypatch.delenv("CUGA_GUIDED_SETUP", raising=False)
    monkeypatch.setenv("CUGA_DEMO_MODE", preset)
    graph = SimpleNamespace(llm_config={"model": "seeded-model"})

    async def stream(*args, **kwargs):
        # Container seeds may omit provider. They must not override TOML/Groq.
        assert models.get_current_llm_override() == {"platform": "groq", "model": "existing-model"}
        assert get_secret_agent_id() is None
        yield "done"

    monkeypatch.setattr(main, "event_stream", stream)
    with models.llm_config_context({"platform": "groq", "provider": "groq", "model": "existing-model"}):
        original = models.get_current_llm_override()
        # Keep the established override API's exact shape for this check.
        models.set_current_llm_override({"platform": "groq", "model": "existing-model"})
        assert [frame async for frame in main.configured_event_stream(agent=graph)] == ["done"]
        assert models.get_current_llm_override() == {"platform": "groq", "model": "existing-model"}
        models.set_current_llm_override(original)
