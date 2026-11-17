"""Published runtime replacement never exposes partially installed policies."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from cuga.backend.server import main, runtime_refresh
from cuga.backend.server.config_fingerprint import compute_fingerprint

pytestmark = pytest.mark.unit


@pytest.fixture
async def policy_state(monkeypatch):
    from cuga.backend.cuga_graph.policy.configurable import PolicyConfigurable
    from cuga.backend.cuga_graph.policy.models import ToolApproval
    from cuga.backend.cuga_graph.policy.storage import PolicyStorage
    from cuga.backend.storage.policy.local import LocalPolicyStore

    from cuga.config import settings

    monkeypatch.setattr(settings.secrets, "force_env", False)
    storage = PolicyStorage(backend=LocalPolicyStore(":memory:", "source_policies"), embedding_dim=2)
    storage._embedding_initialized = True
    storage._embedding_function = AsyncMock(return_value=[1.0, 0.0])
    await storage.connect()
    await storage._create_collection()
    policy = ToolApproval(
        id="approval", name="Approval", description="Protect writes", required_tools=["write"]
    )
    await storage.add_policy(policy)
    policy_system = PolicyConfigurable(storage=storage)
    policy_system.llm = MagicMock()
    await policy_system.initialize()
    monkeypatch.setattr("cuga.backend.llm.models.create_llm_from_config", lambda config: "new-llm")
    state = SimpleNamespace(
        policy_system=policy_system,
        agent=None,
        agent_id="cuga-default",
        checkpointer=object(),
        current_llm="old-llm",
        tools_include_by_app=None,
    )
    try:
        yield state
    finally:
        await storage.disconnect()


async def test_policy_snapshot_survives_live_store_clear_and_rejects_partial_build(policy_state):
    prepared = await runtime_refresh.prepare_runtime(policy_state, {})
    try:
        await policy_state.policy_system.storage.delete_policy("approval")
        policies = await prepared.policy_system.storage.list_policies(strict=True)
        assert [policy.id for policy in policies] == ["approval"]
        assert prepared.current_llm == "new-llm"
        assert policy_state.current_llm == "old-llm"
        with pytest.raises(ValueError, match="all published policies"):
            await runtime_refresh.prepare_runtime(policy_state, {"policies": {"policies": [{"type": "bad"}]}})
        assert [policy.id for policy in await prepared.policy_system.storage.list_policies(strict=True)] == [
            "approval"
        ]
    finally:
        await prepared.runtime_policy_snapshot.disconnect()


async def test_concurrent_refresh_builds_once_and_keeps_live_policy_until_swap(policy_state, monkeypatch):
    old = SimpleNamespace(aclose=AsyncMock(), policy_system=policy_state.policy_system)
    policy_state.agent = old
    policy_state.config_version = 1
    config = {"special_instructions": "new"}
    entered, finish = asyncio.Event(), asyncio.Event()
    replacement = SimpleNamespace(
        aclose=AsyncMock(), _config_fingerprint=compute_fingerprint(config), _checkpoint_config_version="2"
    )

    async def build(**kwargs):
        entered.set()
        assert policy_state.agent is old
        assert kwargs["runtime_state"].policy_system is not old.policy_system
        await finish.wait()
        return replacement

    builder = AsyncMock(side_effect=build)
    monkeypatch.setattr(main, "_build_published_default_agent", builder)
    tasks = [asyncio.create_task(main._replace_default_runtime(policy_state, config, 2)) for _ in range(3)]
    await asyncio.wait_for(entered.wait(), 5)
    assert [p.id for p in await old.policy_system.storage.list_policies(strict=True)] == ["approval"]
    assert policy_state.current_llm == "old-llm"
    finish.set()
    assert await asyncio.gather(*tasks) == [replacement] * 3
    builder.assert_awaited_once()
    assert policy_state.agent is replacement
    assert policy_state.current_llm == "new-llm"
    old.aclose.assert_awaited_once()
    await builder.call_args.kwargs["runtime_state"].runtime_policy_snapshot.disconnect()


async def test_failed_graph_build_keeps_previous_runtime(policy_state, monkeypatch):
    old = SimpleNamespace(aclose=AsyncMock())
    policy_state.agent = old
    monkeypatch.setattr(
        main, "_build_published_default_agent", AsyncMock(side_effect=RuntimeError("build failed"))
    )
    with pytest.raises(RuntimeError, match="build failed"):
        await main._replace_default_runtime(policy_state, {}, 2)
    assert policy_state.agent is old
    assert policy_state.current_llm == "old-llm"
    old.aclose.assert_not_awaited()
    assert [p.id for p in await policy_state.policy_system.storage.list_policies(strict=True)] == ["approval"]


async def test_retired_graph_closes_only_after_last_using_turn():
    owner = SimpleNamespace(aclose=AsyncMock())
    async with runtime_refresh.runtime_turn():
        runtime_refresh.retain_runtime(owner)
        runtime_refresh.retain_runtime(owner)  # repeated use in one turn counts once
        owner._runtime_retired = True
        await runtime_refresh.close_retired_runtime(owner)
        owner.aclose.assert_not_awaited()
    owner.aclose.assert_awaited_once()


async def test_runtime_snapshot_preserves_postgres_cosine_matching(policy_state):
    from cuga.backend.storage.policy.prod import ProdPolicyStore

    policies = await policy_state.policy_system.storage.list_policies(strict=True)
    policy_state.policy_system.storage._backend = MagicMock(spec=ProdPolicyStore)
    prepared = await runtime_refresh.prepare_runtime(
        policy_state, {"policies": {"policies": [policy.model_dump(mode="json") for policy in policies]}}
    )
    try:
        await prepared.policy_system.storage.update_policy(policies[0], embedding=[2.0, 0.0])
        matches = await prepared.policy_system.storage.search_policies([1.0, 0.0])
        assert matches[0][0].id == "approval"
        assert matches[0][1] == pytest.approx(1.0)
    finally:
        await prepared.runtime_policy_snapshot.disconnect()


async def test_direct_stream_retains_runtime_until_generator_closes():
    owner = SimpleNamespace(aclose=AsyncMock())

    @runtime_refresh.runtime_stream
    async def stream():
        runtime_refresh.retain_runtime(owner)
        yield "first"
        yield "second"

    iterator = stream()
    assert await anext(iterator) == "first"
    owner._runtime_retired = True
    await runtime_refresh.close_retired_runtime(owner)
    owner.aclose.assert_not_awaited()
    await iterator.aclose()
    owner.aclose.assert_awaited_once()


async def test_direct_policy_save_updates_published_config_and_future_runtime(policy_state, monkeypatch):
    from cuga.config import settings

    monkeypatch.setattr(settings.policy, "enabled", True)
    policy_state.policy_filesystem_sync = None
    policy_state.agent = SimpleNamespace(_runtime_policy_snapshot=object(), _runtime_config={})
    monkeypatch.setattr(main, "app_state", policy_state)
    monkeypatch.setattr(main, "_resolve_policy_agent_id", AsyncMock(return_value="cuga-default"))
    data = {"enablePolicies": True, "policies": []}
    request = SimpleNamespace(headers={}, json=AsyncMock(return_value=data))
    monkeypatch.setattr(
        "cuga.backend.server.config_store.load_config",
        AsyncMock(return_value=({"special_instructions": "keep"}, 3)),
    )
    persist = AsyncMock(return_value=True)
    monkeypatch.setattr("cuga.backend.server.config_store.update_published_config_at_version", persist)
    replace = AsyncMock()
    monkeypatch.setattr(main, "_replace_default_runtime", replace)
    response = await main.save_policies_config(request, agent_id="cuga-default", current_user=None)
    assert response.status_code == 200
    config = {"special_instructions": "keep", "policies": data}
    persist.assert_awaited_once_with(config, "cuga-default", 3)
    replace.assert_awaited_once_with(policy_state, config, 3, reread=True)
