"""Regression coverage for PR #751 review fixes across the SDK, /run, and YAML."""

from __future__ import annotations

import asyncio
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, call

import pytest

from cuga.backend.cuga_graph.nodes.cuga_supervisor.child_checkpoint import resolve_child_checkpoint_id
from cuga.backend.cuga_graph.nodes.cuga_supervisor.supervisor_graph_adapter import SupervisorGraphAdapter
from cuga.backend.server import run_routes
from cuga.sdk import CugaAgent, CugaSupervisor
from cuga.supervisor_utils.supervisor_config import load_supervisor_config

pytestmark = pytest.mark.unit


def _capturing_supervisor(states):
    supervisor = CugaSupervisor(agents={}, model=MagicMock(), auto_load_policies=False)

    async def invoke(state, config=None):
        states.append(state)
        return {"final_answer": "ok"}

    supervisor._compiled_graph = SimpleNamespace(ainvoke=invoke)
    return supervisor


def _child_id(state, adapter):
    return resolve_child_checkpoint_id(state=state, adapter=adapter, agent_name="worker")


@pytest.mark.asyncio
@pytest.mark.parametrize("authenticated", [False, True])
async def test_run_keeps_users_isolated_on_the_same_parent_thread(monkeypatch, authenticated):
    """The roster path must pass its resolved caller through to child checkpoint derivation."""
    states = []
    supervisor = _capturing_supervisor(states)
    adapter = SupervisorGraphAdapter(agents={}, supervisor_id=supervisor._name)
    monkeypatch.setattr(run_routes, "_run_auth_failure", AsyncMock(return_value=None))
    authenticated_user = AsyncMock(return_value=None)
    monkeypatch.setattr(run_routes, "_authenticated_user_id", authenticated_user)
    get_supervisor = AsyncMock(return_value=supervisor)
    release = AsyncMock()
    monkeypatch.setattr(run_routes, "_get_supervisor", get_supervisor)
    monkeypatch.setattr(run_routes, "_release_supervisor", release)
    monkeypatch.setattr(run_routes.events_bridge, "forwards_to_events", lambda *_: False)

    for user_id in ("alice", "bob", "alice"):
        authenticated_user.return_value = user_id if authenticated else None
        request = SimpleNamespace(
            json=AsyncMock(
                return_value={
                    "query": "hello",
                    "thread_id": "shared-thread",
                    "user_id": "spoofed-user" if authenticated else user_id,
                }
            )
        )
        result = await run_routes.run_sync(request)
        assert result["ok"] is True
        assert result["thread_id"] == "shared-thread"

    assert [state.user_id for state in states] == ["alice", "bob", "alice"]
    child_ids = [_child_id(state, adapter) for state in states]
    assert child_ids[0] != child_ids[1]
    assert child_ids[0] == child_ids[2]
    assert get_supervisor.await_args_list == [call(retain=True)] * 3
    assert release.await_args_list == [call(supervisor)] * 3


@pytest.mark.asyncio
@pytest.mark.parametrize("cancelled", [False, True])
async def test_run_releases_supervisor_after_failed_or_cancelled_invoke(monkeypatch, cancelled):
    """Resolving the caller-identity conflict must retain main's supervisor lease cleanup."""
    error = asyncio.CancelledError() if cancelled else RuntimeError("invoke failed")
    supervisor = SimpleNamespace(invoke=AsyncMock(side_effect=error))
    release = AsyncMock()
    monkeypatch.setattr(run_routes, "_run_auth_failure", AsyncMock(return_value=None))
    monkeypatch.setattr(run_routes, "_authenticated_user_id", AsyncMock(return_value="alice"))
    monkeypatch.setattr(run_routes, "_get_supervisor", AsyncMock(return_value=supervisor))
    monkeypatch.setattr(run_routes, "_release_supervisor", release)
    monkeypatch.setattr(run_routes.events_bridge, "forwards_to_events", lambda *_: False)
    request = SimpleNamespace(json=AsyncMock(return_value={"query": "hello", "thread_id": "shared-thread"}))

    if cancelled:
        with pytest.raises(asyncio.CancelledError):
            await run_routes.run_sync(request)
    else:
        result = await run_routes.run_sync(request)
        assert result.status_code == 500

    supervisor.invoke.assert_awaited_once_with("hello", thread_id="shared-thread", user_id="alice")
    release.assert_awaited_once_with(supervisor)


@pytest.mark.asyncio
async def test_sdk_tenant_override_isolates_child_checkpoints(monkeypatch):
    """Omitted tenant uses the process tenant; explicit tenants get separate child state."""
    monkeypatch.setattr("cuga.config.get_tenant_id", lambda: "process-tenant")
    monkeypatch.setattr("cuga.config.get_service_instance_id", lambda: "instance-1")
    states = []
    supervisor = _capturing_supervisor(states)
    adapter = SupervisorGraphAdapter(agents={}, supervisor_id=supervisor._name)

    for tenant in (None, "other-tenant", "process-tenant"):
        await supervisor.invoke("hello", thread_id="shared-thread", user_id="alice", tenant_id=tenant)

    assert [state.service_scope for state in states] == [
        {"tenant_id": tenant, "instance_id": "instance-1"}
        for tenant in ("process-tenant", "other-tenant", "process-tenant")
    ]
    child_ids = [_child_id(state, adapter) for state in states]
    assert child_ids[0] != child_ids[1]
    assert child_ids[0] == child_ids[2]


@pytest.mark.asyncio
async def test_yaml_import_scopes_survive_loading_without_mutating_shared_agent(tmp_path, monkeypatch):
    """Exercise the complete YAML/Pydantic path for two supervisors importing the same agent."""
    shared = CugaAgent(tools=[], model=MagicMock(), auto_load_policies=False)
    module = ModuleType("_checkpoint_review_agents")
    module.worker = shared
    monkeypatch.setitem(sys.modules, module.__name__, module)
    configs = {}

    for scope in ("call", "conversation"):
        path = tmp_path / f"{scope}.yaml"
        path.write_text(
            "agents:\n"
            "  - name: worker\n"
            f"    import_from: {module.__name__}.worker\n"
            f"    memory_scope: {scope}\n"
        )
        configs[scope] = await load_supervisor_config(str(path))
        assert configs[scope].agents["worker"] is shared

    state = SimpleNamespace(user_id="alice", thread_id="thread", service_scope={})
    for scope, config in configs.items():
        adapter = SupervisorGraphAdapter(agents=config.agents, supervisor_id="supervisor")
        ids = [_child_id(state, adapter), _child_id(state, adapter)]
        assert (ids[0] == ids[1]) is (scope == "conversation")
    assert not hasattr(shared, "_memory_scope")
