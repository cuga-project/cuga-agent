"""ACP permission requests use standard supervisor HITL without respawning."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.unit


def _external_agent():
    return {
        "type": "external",
        "config": {
            "name": "coder",
            "description": "Coding agent",
            "acp_protocol": {"enabled": True, "command": "fake-agent"},
        },
    }


def _safe_request():
    from cuga.backend.cuga_graph.nodes.cuga_supervisor.acp_client.permissions import (
        PermissionOptionDTO,
        SafePermissionRequest,
    )

    return SafePermissionRequest.create(
        lifecycle_id="a" * 32,
        session_id="session",
        tool_call_id="call",
        title="Delete generated file",
        description="Delete workspace/out.txt",
        kind="delete",
        locations=("workspace/out.txt",),
        options=(
            PermissionOptionDTO("allow", "Allow once", "allow_once"),
            PermissionOptionDTO("reject", "Reject once", "reject_once"),
        ),
        ttl_seconds=30,
    )


@pytest.mark.asyncio
async def test_interactive_bridge_pauses_then_resumes_original_task_once() -> None:
    from cuga.backend.cuga_graph.nodes.cuga_supervisor.acp_client.pending import (
        PendingACPDelegationRegistry,
    )
    from cuga.backend.cuga_graph.nodes.cuga_supervisor.acp_protocol import (
        ACPPermissionPause,
        ACPPermissionRuntimeBridge,
    )

    registry = PendingACPDelegationRegistry(capacity=2, ttl_seconds=30)
    bridge = ACPPermissionRuntimeBridge(
        registry=registry,
        thread_id="thread",
        agent_name="coder",
        interactive=True,
    )
    await bridge.register_lifecycle("a" * 32, SimpleNamespace(connection=object(), process=object()))

    calls = 0

    async def original_prompt():
        nonlocal calls
        calls += 1
        selected = await bridge.permission_handler(_safe_request())
        return {"result": selected, "status": "completed", "variables": {}}

    with pytest.raises(ACPPermissionPause) as raised:
        await bridge.run(original_prompt())
    pause = raised.value
    assert pause.pending_id
    assert pause.request.title == "Delete generated file"
    assert calls == 1

    result = await bridge.resume(pending_id=pause.pending_id, approved=True)
    assert result["result"] == "allow"
    assert calls == 1
    assert await registry.size() == 0


@pytest.mark.asyncio
async def test_headless_bridge_rejects_immediately_without_registry_entry() -> None:
    from cuga.backend.cuga_graph.nodes.cuga_supervisor.acp_client.pending import (
        PendingACPDelegationRegistry,
    )
    from cuga.backend.cuga_graph.nodes.cuga_supervisor.acp_protocol import ACPPermissionRuntimeBridge

    registry = PendingACPDelegationRegistry(capacity=2, ttl_seconds=30)
    bridge = ACPPermissionRuntimeBridge(
        registry=registry,
        thread_id="thread",
        agent_name="coder",
        interactive=False,
    )
    assert await bridge.permission_handler(_safe_request()) is None
    assert await registry.size() == 0


@pytest.mark.asyncio
async def test_resume_stale_mismatch_duplicate_and_ambiguous_fail_closed() -> None:
    from cuga.backend.cuga_graph.nodes.cuga_supervisor.acp_client.pending import (
        PendingACPDelegationError,
        PendingACPDelegationRegistry,
    )
    from cuga.backend.cuga_graph.nodes.cuga_supervisor.acp_protocol import (
        ACPPermissionPause,
        ACPPermissionRuntimeBridge,
    )

    registry = PendingACPDelegationRegistry(capacity=2, ttl_seconds=30)
    bridge = ACPPermissionRuntimeBridge(
        registry=registry,
        thread_id="thread",
        agent_name="coder",
        interactive=True,
    )
    await bridge.register_lifecycle("a" * 32, object())

    async def prompt():
        return await bridge.permission_handler(_safe_request())

    with pytest.raises(ACPPermissionPause) as raised:
        await bridge.run(prompt())
    pending_id = raised.value.pending_id

    wrong_owner = ACPPermissionRuntimeBridge(
        registry=registry,
        thread_id="other",
        agent_name="coder",
        interactive=True,
    )
    with pytest.raises(PendingACPDelegationError):
        await wrong_owner.resume(pending_id=pending_id, approved=True)
    with pytest.raises(PendingACPDelegationError):
        await bridge.resume(pending_id="stale", approved=True)
    with pytest.raises(PendingACPDelegationError):
        await bridge.resume(pending_id=pending_id, approved=None)
    with pytest.raises(PendingACPDelegationError):
        await bridge.resume(pending_id=pending_id, approved=True)


@pytest.mark.asyncio
async def test_execute_node_turns_typed_pause_into_safe_standard_tool_action() -> None:
    from cuga.backend.cuga_graph.nodes.cuga_supervisor.acp_client.pending import (
        PendingACPDelegationRegistry,
    )
    from cuga.backend.cuga_graph.nodes.cuga_supervisor.acp_protocol import ACPPermissionPause
    from cuga.backend.cuga_graph.nodes.cuga_supervisor.cuga_supervisor_state import CugaSupervisorState
    from cuga.backend.cuga_graph.nodes.cuga_supervisor.nodes.execute_agent_tool import (
        create_execute_agent_tool_node,
    )
    from cuga.backend.cuga_graph.nodes.cuga_supervisor.supervisor_graph_adapter import SupervisorGraphAdapter
    from cuga.backend.cuga_graph.utils.nodes_names import ActionIds

    registry = PendingACPDelegationRegistry(capacity=2, ttl_seconds=30)
    adapter = SupervisorGraphAdapter(
        agents={"coder": _external_agent()},
        pending_acp_registry=registry,
        interactive=True,
    )
    node = create_execute_agent_tool_node(adapter)
    pause = ACPPermissionPause("pending-safe", "coder", _safe_request())
    state = CugaSupervisorState(input="work", thread_id="thread", script="delegate_to_coder(task='work')")

    with patch(
        "cuga.backend.cuga_graph.nodes.cuga_supervisor.nodes.execute_agent_tool.CodeExecutor.eval_with_tools_async",
        new=AsyncMock(side_effect=pause),
    ):
        command = await node(state, config={"configurable": {"thread_id": "thread"}})

    action = command.update["hitl_action"]
    metadata = command.update["supervisor_metadata"]["acp_permission"]
    assert action.action_id == ActionIds.TOOL_APPROVAL
    assert metadata == {
        "pending_id": "pending-safe",
        "agent_name": "coder",
        "title": "Delete generated file",
        "description": "Delete workspace/out.txt",
        "kind": "delete",
        "locations": ["workspace/out.txt"],
    }
    assert action.additional_data.tool["acp_permission"]["pending_id"] == "pending-safe"
    assert "session-safe" not in repr(command.update)
    assert "allow-once" not in repr(command.update)
    assert "reject-once" not in repr(command.update)


@pytest.mark.asyncio
async def test_resume_path_uses_pending_prompt_not_code_executor_and_records_once() -> None:
    from cuga.backend.cuga_graph.nodes.cuga_supervisor.acp_client.pending import (
        PendingACPDelegationRegistry,
    )
    from cuga.backend.cuga_graph.nodes.cuga_supervisor.cuga_supervisor_state import CugaSupervisorState
    from cuga.backend.cuga_graph.nodes.cuga_supervisor.nodes.execute_agent_tool import (
        create_execute_agent_tool_node,
    )
    from cuga.backend.cuga_graph.nodes.cuga_supervisor.supervisor_graph_adapter import SupervisorGraphAdapter

    registry = PendingACPDelegationRegistry(capacity=2, ttl_seconds=30)
    adapter = SupervisorGraphAdapter(
        agents={"coder": _external_agent()},
        pending_acp_registry=registry,
        interactive=True,
    )
    adapter.record_delegation = MagicMock()
    state = CugaSupervisorState(
        input="work",
        thread_id="thread",
        script="delegate_to_coder(task='work')",
        supervisor_metadata={
            "acp_permission_resume": {"pending_id": "pending-safe", "agent_name": "coder", "approved": True}
        },
    )

    with (
        patch(
            "cuga.backend.cuga_graph.nodes.cuga_supervisor.nodes.execute_agent_tool.resume_acp_delegation",
            new=AsyncMock(return_value={"result": "done", "status": "completed", "variables": {}}),
        ) as resume,
        patch(
            "cuga.backend.cuga_graph.nodes.cuga_supervisor.nodes.execute_agent_tool.CodeExecutor.eval_with_tools_async",
            new=AsyncMock(),
        ) as execute,
    ):
        result = await create_execute_agent_tool_node(adapter)(
            state, {"configurable": {"thread_id": "thread"}}
        )

    resume.assert_awaited_once()
    execute.assert_not_awaited()
    adapter.record_delegation.assert_called_once()
    assert "acp_permission" not in result["supervisor_metadata"]
    assert "acp_permission_resume" not in result["supervisor_metadata"]


@pytest.mark.asyncio
async def test_stale_resume_records_one_final_failed_delegation() -> None:
    from cuga.backend.cuga_graph.nodes.cuga_supervisor.acp_client.pending import (
        PendingACPDelegationNotFoundError,
        PendingACPDelegationRegistry,
    )
    from cuga.backend.cuga_graph.nodes.cuga_supervisor.cuga_supervisor_state import CugaSupervisorState
    from cuga.backend.cuga_graph.nodes.cuga_supervisor.nodes.execute_agent_tool import (
        create_execute_agent_tool_node,
    )
    from cuga.backend.cuga_graph.nodes.cuga_supervisor.supervisor_graph_adapter import SupervisorGraphAdapter

    adapter = SupervisorGraphAdapter(
        agents={"coder": _external_agent()},
        pending_acp_registry=PendingACPDelegationRegistry(),
        interactive=True,
    )
    adapter.record_delegation = MagicMock()
    state = CugaSupervisorState(
        input="work",
        thread_id="thread",
        supervisor_metadata={
            "acp_permission_resume": {"pending_id": "stale", "agent_name": "coder", "approved": True}
        },
    )

    with patch(
        "cuga.backend.cuga_graph.nodes.cuga_supervisor.nodes.execute_agent_tool.resume_acp_delegation",
        new=AsyncMock(side_effect=PendingACPDelegationNotFoundError("stale")),
    ):
        result = await create_execute_agent_tool_node(adapter)(
            state, {"configurable": {"thread_id": "thread"}}
        )

    adapter.record_delegation.assert_called_once()
    recorded = adapter.record_delegation.call_args.kwargs["result"]
    assert recorded["status"] == "failed"
    assert result["final_answer"] == "ACP pending delegation is stale or could not be resumed."


@pytest.mark.asyncio
async def test_graph_adapter_routes_malformed_acp_response_to_fail_closed_resume() -> None:
    from cuga.backend.cuga_graph.nodes.cuga_supervisor.cuga_supervisor_node import CugaSupervisorNode
    from cuga.backend.cuga_graph.nodes.human_in_the_loop.followup_model import (
        ActionResponse,
        ActionType,
    )
    from cuga.backend.cuga_graph.state.agent_state import AgentState
    from cuga.backend.cuga_graph.utils.nodes_names import ActionIds

    captured = {}

    class _FakeSubgraph:
        async def ainvoke(self, state, config=None):
            captured["metadata"] = state.supervisor_metadata
            return state

    node = CugaSupervisorNode()
    node.set_subgraph(_FakeSubgraph())
    state = AgentState(
        input="work",
        thread_id="thread",
        sender="WaitForResponse",
        hitl_response=ActionResponse(
            action_id=ActionIds.TOOL_APPROVAL,
            response_type=ActionType.CONFIRMATION,
            timestamp="now",
            confirmed=None,
        ),
        supervisor_metadata={"acp_permission": {"pending_id": "pending-safe", "agent_name": "coder"}},
    )

    await node.node(state, config={"configurable": {"thread_id": "thread"}})

    assert captured["metadata"]["acp_permission_resume"] == {
        "pending_id": "pending-safe",
        "agent_name": "coder",
        "approved": None,
    }


@pytest.mark.asyncio
async def test_supervisor_sdk_callback_maps_exact_acp_response_for_resume() -> None:
    from cuga import CugaSupervisor
    from cuga.backend.cuga_graph.nodes.cuga_supervisor.cuga_supervisor_state import CugaSupervisorState
    from cuga.backend.cuga_graph.nodes.human_in_the_loop.followup_model import (
        ActionResponse,
        ActionType,
        AdditionalData,
    )
    from cuga.backend.cuga_graph.utils.nodes_names import ActionIds, NodeNames

    supervisor = CugaSupervisor(agents={}, model=MagicMock(), auto_load_policies=False)
    wrapper = supervisor._create_supervisor_hitl_wrapper_graph()
    permission = {"pending_id": "pending-safe", "agent_name": "coder"}
    state = CugaSupervisorState(
        input="work",
        sender=NodeNames.WAIT_FOR_RESPONSE,
        hitl_response=ActionResponse(
            action_id=ActionIds.TOOL_APPROVAL,
            response_type=ActionType.CONFIRMATION,
            timestamp="now",
            confirmed=False,
            additional_data=AdditionalData(tool={"acp_permission": permission}),
        ),
        supervisor_metadata={"acp_permission": permission},
    )

    command = await wrapper.nodes["SupervisorSDKCallback"].runnable.ainvoke(state, config={})
    assert command.goto == "SupervisorSubgraph"
    assert command.update["supervisor_metadata"]["acp_permission_resume"] == {
        **permission,
        "approved": False,
    }


@pytest.mark.asyncio
async def test_ambiguous_resume_cancels_owned_entry_clears_metadata_and_records_once() -> None:
    import asyncio

    from cuga.backend.cuga_graph.nodes.cuga_supervisor.acp_client.pending import (
        PendingACPDelegationRegistry,
    )
    from cuga.backend.cuga_graph.nodes.cuga_supervisor.cuga_supervisor_state import CugaSupervisorState
    from cuga.backend.cuga_graph.nodes.cuga_supervisor.nodes.execute_agent_tool import (
        create_execute_agent_tool_node,
    )
    from cuga.backend.cuga_graph.nodes.cuga_supervisor.supervisor_graph_adapter import SupervisorGraphAdapter

    registry = PendingACPDelegationRegistry(capacity=1, ttl_seconds=30)
    permission_future = asyncio.get_running_loop().create_future()
    prompt_task = asyncio.create_task(asyncio.Event().wait())
    await registry.insert(
        pending_id="pending-safe",
        thread_id="thread",
        agent_name="coder",
        request=_safe_request(),
        owner=object(),
        prompt_task=prompt_task,
        permission_future=permission_future,
    )
    adapter = SupervisorGraphAdapter(
        agents={"coder": _external_agent()},
        pending_acp_registry=registry,
        interactive=True,
    )
    adapter.record_delegation = MagicMock()
    state = CugaSupervisorState(
        input="work",
        thread_id="thread",
        supervisor_metadata={
            "acp_permission": {"pending_id": "pending-safe", "agent_name": "coder"},
            "acp_permission_resume": {
                "pending_id": "pending-safe",
                "agent_name": "coder",
                "approved": None,
            },
        },
    )

    result = await create_execute_agent_tool_node(adapter)(state, {"configurable": {"thread_id": "thread"}})

    assert await registry.size() == 0
    assert prompt_task.cancelled()
    assert "acp_permission" not in result["supervisor_metadata"]
    assert "acp_permission_resume" not in result["supervisor_metadata"]
    adapter.record_delegation.assert_called_once()
    assert adapter.record_delegation.call_args.kwargs["result"]["status"] == "failed"


@pytest.mark.asyncio
async def test_supervisor_aclose_reaps_registry_without_closing_supplied_agents() -> None:
    from cuga import CugaSupervisor

    supplied_agent = SimpleNamespace(aclose=AsyncMock())
    supervisor = CugaSupervisor(
        agents={"shared": supplied_agent}, model=MagicMock(), auto_load_policies=False
    )
    supervisor._pending_acp_registry.aclose = AsyncMock()
    await supervisor.aclose()
    supervisor._pending_acp_registry.aclose.assert_awaited_once()
    supplied_agent.aclose.assert_not_awaited()


def test_plan_approval_metadata_does_not_authorize_acp_permission() -> None:
    from cuga.backend.cuga_graph.nodes.cuga_supervisor.acp_protocol import ACPPermissionRuntimeBridge

    bridge = ACPPermissionRuntimeBridge(
        registry=MagicMock(),
        thread_id="thread",
        agent_name="coder",
        interactive=True,
    )
    assert not hasattr(bridge, "plan_approved")
