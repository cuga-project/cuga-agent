"""Generated tool guards and .cuga folder policies on the published default runtime (#774).

The default runtime enforces a private policy snapshot. These tests check two cases where the
published config does not hold a policy that the shared policy store holds:

- a tool guard that was generated but could not be synced to the published config;
- policies loaded from the .cuga folder while the published policy list is empty.

The tests call the real guard route, config sync, runtime replacement, ``prepare_runtime`` and
config store. Only the LLM guard generation and, in the route tests, the graph build are replaced.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest

from cuga.backend.cuga_graph.policy.models import AlwaysTrigger, ToolApproval, ToolGuard, ToolGuide
from cuga.backend.server import config_store, main, runtime_refresh
from cuga.backend.server.config_fingerprint import compute_published_fingerprint

pytestmark = pytest.mark.unit

APPROVAL_ONLY = {"approval": False}  # {policy id: has a tool guard}
GUIDE_WITHOUT_GUARD = {"approval": False, "guide": False}
GUIDE_WITH_GUARD = {"approval": False, "guide": True}


def _approval(policy_id: str = "approval") -> ToolApproval:
    return ToolApproval(
        id=policy_id, name=policy_id.title(), description="Protect writes", required_tools=["write"]
    )


def _guide(with_guard: bool) -> ToolGuide:
    return ToolGuide(
        id="guide",
        name="Guide",
        description="Guide writes",
        triggers=[AlwaysTrigger()],
        target_tools=["write"],
        guide_content="Be careful.",
        tool_guards={"write": ToolGuard(policy_code="def check(args):\n    return False\n")}
        if with_guard
        else {},
    )


def _frontend(policy) -> dict:
    return main._policy_to_frontend_dict(policy.model_dump())


def _published(*policies) -> dict:
    """A published config as the Manage page stores it; with no policies the list is empty."""
    return {
        "special_instructions": "x",
        "policies": {"enablePolicies": True, "policies": [_frontend(p) for p in policies]},
    }


async def _policies(storage) -> dict:
    stored = await storage.list_policies(enabled_only=False, limit=1000, strict=True)
    return dict(sorted((p.id, bool(getattr(p, "tool_guards", None))) for p in stored))


async def _published_policies() -> dict:
    config, _ = await config_store.load_config(None, "cuga-default")
    listed = ((config or {}).get("policies") or {}).get("policies") or []
    return {p["id"]: bool(p.get("tool_guards")) for p in listed}


async def _policy_storage():
    from cuga.backend.cuga_graph.policy.configurable import PolicyConfigurable
    from cuga.backend.cuga_graph.policy.storage import PolicyStorage
    from cuga.backend.storage.policy.local import LocalPolicyStore

    storage = PolicyStorage(backend=LocalPolicyStore(":memory:", "source_policies"), embedding_dim=2)
    storage._embedding_initialized = True
    storage._embedding_function = AsyncMock(return_value=[1.0, 0.0])
    await storage.connect()
    await storage._create_collection()
    policy_system = PolicyConfigurable(storage=storage)
    policy_system.llm = MagicMock()
    await policy_system.initialize()
    return storage, policy_system


@pytest.fixture
async def state(monkeypatch):
    """The published app state with a shared policy store and a stand-in for the graph build."""
    from cuga.config import settings

    monkeypatch.setattr(settings.secrets, "force_env", False)
    monkeypatch.setattr(settings.policy, "enabled", True)
    monkeypatch.setattr("cuga.backend.llm.models.create_llm_from_config", lambda config: "llm")
    storage, policy_system = await _policy_storage()
    app_state = SimpleNamespace(
        policy_system=policy_system,
        policy_filesystem_sync=None,
        agent=None,
        agent_id="cuga-default",
        checkpointer=object(),
        current_llm="llm",
        tools_include_by_app=None,
        config_version=None,
        tools_include_version=0,
    )
    snapshots = []

    async def build(
        *, tool_provider, startup_config, startup_llm_cfg, startup_version, langfuse_handler, runtime_state
    ):
        # The attributes the last lines of ``_build_published_default_agent`` set.
        snapshots.append(runtime_state.runtime_policy_snapshot)
        return SimpleNamespace(
            aclose=AsyncMock(),
            tool_provider=object(),
            llm_config=None,
            _checkpoint_config_version=str(startup_version or 0),
            _config_fingerprint=await compute_published_fingerprint(startup_config),
            _runtime_policy_snapshot=runtime_state.runtime_policy_snapshot,
            _runtime_llm=runtime_state.current_llm,
            _runtime_config=startup_config or {},
        )

    async def generate(*, policy_system, policy_id, generation_agent):
        await policy_system.storage.update_policy(_guide(True))  # what the real generation stores
        return {"status": "ok", "policy_id": policy_id, "results": [{"tool": "write", "status": "ok"}]}

    monkeypatch.setattr(main, "_build_published_default_agent", build)
    monkeypatch.setattr(main, "build_tool_guard_generation_agent", Mock(return_value=object()))
    monkeypatch.setattr(main, "generate_tool_guards_for_policy", generate)
    monkeypatch.setattr(main, "app_state", app_state)
    monkeypatch.setattr(main, "_resolve_policy_agent_id", AsyncMock(return_value="cuga-default"))
    try:
        yield app_state
    finally:
        for snapshot in snapshots:
            if snapshot is not None:
                await snapshot.disconnect()
        await storage.disconnect()


async def _start(state, config: dict, *folder_policies) -> None:
    """The server startup order: load the .cuga folder policies into the shared store, apply the
    published policy list only when it is not empty, then build the default agent."""
    from cuga.backend.cuga_graph.policy.utils import apply_policies_data_to_storage

    for policy in folder_policies:
        await state.policy_system.storage.add_policy(policy)
    await config_store.save_config(config, "cuga-default")
    stored, version = await config_store.load_config(None, "cuga-default")
    published = (stored.get("policies") or {}).get("policies") or []
    if published:
        await apply_policies_data_to_storage(state.policy_system.storage, published, clear_existing=True)
    prepared = await runtime_refresh.prepare_runtime(state, stored, keep_store_when_list_empty=True)
    state.agent = await main._build_published_default_agent(
        tool_provider=None,
        startup_config=stored,
        startup_llm_cfg={},
        startup_version=version,
        langfuse_handler=None,
        runtime_state=prepared,
    )
    state.config_version = version


async def _generate_guard() -> dict:
    response = await main.generate_tool_guard_for_policy(
        "guide", SimpleNamespace(headers={}), current_user=None
    )
    assert response.status_code == 200
    return json.loads(response.body)


async def test_synced_guard_is_enforced(state):
    await _start(state, _published(_approval(), _guide(False)), _approval(), _guide(False))
    body = await _generate_guard()
    assert (body["config_synced"], body["guard_active"]) == (True, True)
    assert "guard_warning" not in body
    assert await _policies(state.policy_system.storage) == GUIDE_WITH_GUARD
    assert await _published_policies() == GUIDE_WITH_GUARD
    assert await _policies(state.agent._runtime_policy_snapshot) == GUIDE_WITH_GUARD


@pytest.mark.parametrize(
    "config",
    [{"special_instructions": "x"}, _published()],
    ids=["no-policies-section", "empty-policy-list"],
)
async def test_guard_for_a_folder_policy_is_enforced_without_a_sync(state, config):
    """The published config holds no policies, so the runtime enforces the shared store's policies."""
    await _start(state, config, _approval(), _guide(False))
    body = await _generate_guard()
    assert (body["config_synced"], body["guard_active"]) == (False, True)
    assert await _policies(state.policy_system.storage) == GUIDE_WITH_GUARD  # nothing was deleted
    assert await _policies(state.agent._runtime_policy_snapshot) == GUIDE_WITH_GUARD


@pytest.mark.parametrize("failure", ["stale-version", "sync-raises"])
async def test_failed_sync_keeps_the_guard_in_the_shared_store(state, monkeypatch, failure):
    await _start(state, _published(_approval(), _guide(False)), _approval(), _guide(False))
    if failure == "stale-version":
        stale = AsyncMock(return_value=False)
        monkeypatch.setattr(config_store, "update_published_config_at_version", stale)
    else:
        monkeypatch.setattr(main, "_sync_policy_to_config_store", AsyncMock(side_effect=RuntimeError()))
    body = await _generate_guard()
    assert (body["config_synced"], body["guard_active"]) == (False, False)
    assert body["guard_warning"]
    assert await _policies(state.policy_system.storage) == GUIDE_WITH_GUARD
    # The published list does not hold the guard, so the running agent does not enforce it yet.
    assert await _policies(state.agent._runtime_policy_snapshot) == GUIDE_WITHOUT_GUARD


async def test_failed_sync_keeps_a_policy_that_the_published_list_does_not_hold(state):
    await _start(state, _published(_approval()), _approval())
    await state.policy_system.storage.add_policy(_guide(False))  # the store is ahead of the config
    body = await _generate_guard()
    assert (body["config_synced"], body["guard_active"]) == (False, False)
    assert body["guard_warning"]
    assert await _policies(state.policy_system.storage) == GUIDE_WITH_GUARD
    assert await _policies(state.agent._runtime_policy_snapshot) == APPROVAL_ONLY


async def test_policy_save_after_a_failed_sync_activates_the_guard(state):
    await _start(state, _published(_approval()), _approval())
    await state.policy_system.storage.add_policy(_guide(False))
    assert (await _generate_guard())["guard_active"] is False
    data = {"enablePolicies": True, "policies": [_frontend(_approval()), _frontend(_guide(True))]}
    request = SimpleNamespace(headers={}, json=AsyncMock(return_value=data))
    response = await main.save_policies_config(request, agent_id="cuga-default", current_user=None)
    assert response.status_code == 200
    assert await _published_policies() == GUIDE_WITH_GUARD
    assert await _policies(state.policy_system.storage) == GUIDE_WITH_GUARD
    assert await _policies(state.agent._runtime_policy_snapshot) == GUIDE_WITH_GUARD


async def test_running_turn_keeps_its_snapshot_through_a_forced_rebuild(state):
    await _start(state, _published(), _approval(), _guide(False))
    old = state.agent
    async with runtime_refresh.runtime_turn():
        runtime_refresh.retain_runtime(old)  # a turn is running on the old runtime
        assert (await _generate_guard())["guard_active"] is True
        assert state.agent is not old
        assert old._runtime_retired is True
        old.aclose.assert_not_awaited()
        assert await _policies(old._runtime_policy_snapshot) == GUIDE_WITHOUT_GUARD
        assert await _policies(state.agent._runtime_policy_snapshot) == GUIDE_WITH_GUARD
    old.aclose.assert_awaited_once()  # closed after the turn ended


async def test_startup_snapshot_of_folder_policies_is_a_private_copy(state):
    await _start(state, _published(), _approval())
    snapshot = state.agent._runtime_policy_snapshot
    assert snapshot is not state.policy_system.storage
    assert await _policies(snapshot) == APPROVAL_ONLY
    await state.policy_system.storage.delete_policy("approval")
    assert await _policies(snapshot) == APPROVAL_ONLY


async def test_publishing_an_empty_policy_list_still_replaces_the_policies(state):
    """A publish follows the published config; only startup and forced rebuilds keep the store."""
    await _start(state, _published(), _approval())
    await config_store.save_config(_published(), "cuga-default")
    await main._replace_default_runtime(state, {}, None, reread=True)
    assert await _policies(state.policy_system.storage) == {}
    assert await _policies(state.agent._runtime_policy_snapshot) == {}


@pytest.mark.parametrize("published", ["empty-policy-list", "no-policies-section"])
async def test_policy_file_in_the_cuga_folder_is_enforced_after_startup(monkeypatch, tmp_path, published):
    """The real folder load and the real default agent build, as at server startup."""
    from langgraph.checkpoint.memory import MemorySaver

    from cuga.backend.cuga_graph.nodes.cuga_lite.providers.base import ToolProviderInterface
    from cuga.backend.cuga_graph.policy.filesystem_sync import PolicyFilesystemSync
    from cuga.backend.cuga_graph.policy.folder_loader import load_policies_from_folder
    from cuga.backend.llm.models import LLMManager
    from cuga.backend.storage.checkpoint import CheckpointerHandle
    from cuga.config import settings

    monkeypatch.setattr(settings.secrets, "force_env", True)
    storage, policy_system = await _policy_storage()
    folder = tmp_path / ".cuga"
    PolicyFilesystemSync(cuga_folder=str(folder)).save_policy_to_file(_approval("folder_approval"))
    await load_policies_from_folder(folder_path=str(folder), storage=storage, clear_existing=False)
    config = _published() if published == "empty-policy-list" else {"special_instructions": "x"}
    config_store.normalize_policies_for_save(config)
    handle = CheckpointerHandle(saver=MemorySaver(), kind="memory")
    monkeypatch.setattr(main.app_state, "checkpointer_handle", handle)
    monkeypatch.setattr(main.app_state, "policy_system", policy_system)
    tool_provider = MagicMock(spec=ToolProviderInterface)
    tool_provider.initialize = AsyncMock()
    tool_provider.get_apps = AsyncMock(return_value=[])
    agent = None
    try:
        with patch.object(LLMManager, "get_model", return_value=MagicMock()):
            agent = await main._build_published_default_agent(
                tool_provider=tool_provider, startup_config=config, startup_llm_cfg={}, startup_version=1
            )
        assert await _policies(agent.policy_system.storage) == {"folder_approval": False}
    finally:
        if agent is not None and agent._runtime_policy_snapshot is not None:
            await agent._runtime_policy_snapshot.disconnect()
        await storage.disconnect()
