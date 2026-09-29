"""ACP permission requests use standard supervisor HITL without respawning."""

from __future__ import annotations

import asyncio
from dataclasses import replace
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
async def test_each_sequential_operation_permission_pauses_with_fresh_pending_id() -> None:
    from cuga.backend.cuga_graph.nodes.cuga_supervisor.acp_client.pending import (
        PendingACPDelegationError,
        PendingACPDelegationRegistry,
    )
    from cuga.backend.cuga_graph.nodes.cuga_supervisor.acp_protocol import (
        ACPPermissionPause,
        ACPPermissionRuntimeBridge,
        resume_acp_delegation,
    )

    registry = PendingACPDelegationRegistry(capacity=2, ttl_seconds=30)
    bridge = ACPPermissionRuntimeBridge(
        registry=registry,
        thread_id="thread",
        agent_name="coder",
        interactive=True,
    )
    await bridge.register_lifecycle("a" * 32, SimpleNamespace(connection=object(), process=object()))

    async def two_operations():
        first = await bridge.permission_handler(_safe_request())
        second = await bridge.permission_handler(_safe_request())
        return {"result": {"first": first, "second": second}, "status": "completed", "variables": {}}

    with pytest.raises(ACPPermissionPause) as first_pause:
        await bridge.run(two_operations())

    # Resume from a fresh bridge, as the graph does after a checkpointed pause.
    with pytest.raises(ACPPermissionPause) as second_pause:
        await resume_acp_delegation(
            registry=registry,
            pending_id=first_pause.value.pending_id,
            thread_id="thread",
            agent_name="coder",
            approved=True,
        )
    assert second_pause.value.pending_id != first_pause.value.pending_id
    assert await registry.size() == 1

    # The first approval cannot be replayed against the second operation.
    with pytest.raises(PendingACPDelegationError):
        await resume_acp_delegation(
            registry=registry,
            pending_id=first_pause.value.pending_id,
            thread_id="thread",
            agent_name="coder",
            approved=True,
        )

    result = await resume_acp_delegation(
        registry=registry,
        pending_id=second_pause.value.pending_id,
        thread_id="thread",
        agent_name="coder",
        approved=False,
    )
    assert result["result"] == {"first": "allow", "second": "reject"}
    assert await registry.size() == 0


@pytest.mark.asyncio
async def test_resume_path_turns_next_operation_pause_into_new_tool_action() -> None:
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
            "acp_permission": {"pending_id": "first", "agent_name": "coder"},
            "acp_permission_resume": {"pending_id": "first", "agent_name": "coder", "approved": True},
        },
    )

    with patch(
        "cuga.backend.cuga_graph.nodes.cuga_supervisor.nodes.execute_agent_tool.resume_acp_delegation",
        new=AsyncMock(side_effect=ACPPermissionPause("second", "coder", _safe_request())),
    ):
        command = await create_execute_agent_tool_node(adapter)(
            state, {"configurable": {"thread_id": "thread"}}
        )

    adapter.record_delegation.assert_not_called()
    metadata = command.update["supervisor_metadata"]
    assert "acp_permission_resume" not in metadata
    assert metadata["acp_permission"]["pending_id"] == "second"
    assert command.update["hitl_action"].action_id == ActionIds.TOOL_APPROVAL
    assert command.update["hitl_action"].additional_data.tool["acp_permission"] == {
        "pending_id": "second",
        "agent_name": "coder",
    }


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
async def test_duplicate_and_concurrent_resume_losers_do_not_record() -> None:
    from cuga.backend.cuga_graph.nodes.cuga_supervisor.acp_client.pending import (
        PendingACPDelegationRegistry,
        PendingACPDelegationStateError,
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
            "acp_permission_resume": {"pending_id": "pending", "agent_name": "coder", "approved": True}
        },
    )

    with patch(
        "cuga.backend.cuga_graph.nodes.cuga_supervisor.nodes.execute_agent_tool.resume_acp_delegation",
        new=AsyncMock(side_effect=PendingACPDelegationStateError("already resumed")),
    ):
        result = await create_execute_agent_tool_node(adapter)(
            state, {"configurable": {"thread_id": "thread"}}
        )

    adapter.record_delegation.assert_not_called()
    assert result["final_answer"] == "ACP pending delegation is stale or could not be resumed."


@pytest.mark.asyncio
async def test_stale_resume_without_registry_winner_does_not_record() -> None:
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

    adapter.record_delegation.assert_not_called()
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
@pytest.mark.parametrize("response_pending_id", [None, "different-pending", " pending-safe "])
async def test_entry_graph_acp_response_pending_id_must_match_checkpoint(response_pending_id) -> None:
    from cuga.backend.cuga_graph.nodes.cuga_supervisor.cuga_supervisor_node import CugaSupervisorNode
    from cuga.backend.cuga_graph.nodes.human_in_the_loop.followup_model import (
        ActionResponse,
        ActionType,
        AdditionalData,
    )
    from cuga.backend.cuga_graph.state.agent_state import AgentState
    from cuga.backend.cuga_graph.utils.nodes_names import ActionIds

    captured = {}

    class _FakeSubgraph:
        async def ainvoke(self, state, config=None):
            captured["metadata"] = state.supervisor_metadata
            return state

    response_tool = (
        {"acp_permission": {"pending_id": response_pending_id, "agent_name": "coder"}}
        if response_pending_id is not None
        else {}
    )
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
            confirmed=True,
            submitted_additional_data=AdditionalData(tool=response_tool),
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
async def test_entry_graph_exact_response_pending_id_resumes_once() -> None:
    from cuga.backend.cuga_graph.nodes.cuga_supervisor.cuga_supervisor_node import CugaSupervisorNode
    from cuga.backend.cuga_graph.nodes.human_in_the_loop.followup_model import (
        ActionResponse,
        ActionType,
        AdditionalData,
    )
    from cuga.backend.cuga_graph.state.agent_state import AgentState
    from cuga.backend.cuga_graph.utils.nodes_names import ActionIds

    calls = 0
    captured = {}

    class _FakeSubgraph:
        async def ainvoke(self, state, config=None):
            nonlocal calls
            calls += 1
            captured["metadata"] = state.supervisor_metadata
            return state

    permission = {"pending_id": "pending-safe", "agent_name": "coder"}
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
            confirmed=True,
            submitted_additional_data=AdditionalData(tool={"acp_permission": permission}),
        ),
        supervisor_metadata={"acp_permission": permission},
    )

    await node.node(state, config={"configurable": {"thread_id": "thread"}})

    assert calls == 1
    assert captured["metadata"]["acp_permission_resume"] == {**permission, "approved": True}


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
            submitted_additional_data=AdditionalData(tool={"acp_permission": permission}),
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
@pytest.mark.parametrize("response_pending_id", [None, "different-pending", " pending-safe "])
async def test_supervisor_sdk_callback_rejects_missing_or_mismatched_pending_id(
    response_pending_id,
) -> None:
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
    response_tool = (
        {"acp_permission": {"pending_id": response_pending_id, "agent_name": "coder"}}
        if response_pending_id is not None
        else {}
    )
    state = CugaSupervisorState(
        input="work",
        sender=NodeNames.WAIT_FOR_RESPONSE,
        hitl_response=ActionResponse(
            action_id=ActionIds.TOOL_APPROVAL,
            response_type=ActionType.CONFIRMATION,
            timestamp="now",
            confirmed=True,
            submitted_additional_data=AdditionalData(tool=response_tool),
        ),
        supervisor_metadata={"acp_permission": {"pending_id": "pending-safe", "agent_name": "coder"}},
    )

    command = await wrapper.nodes["SupervisorSDKCallback"].runnable.ainvoke(state, config={})

    assert command.goto == "SupervisorSubgraph"
    assert command.update["supervisor_metadata"]["acp_permission_resume"] == {
        "pending_id": "pending-safe",
        "agent_name": "coder",
        "approved": None,
    }


def _resume_through_wait_for_response(submitted_pending_id, *, client_submitted_override=None):
    """Run the real WaitForResponse overwrite with a client approval for ``submitted_pending_id``."""
    from cuga.backend.cuga_graph.nodes.cuga_supervisor.cuga_supervisor_state import CugaSupervisorState
    from cuga.backend.cuga_graph.nodes.human_in_the_loop.followup_model import (
        ActionType,
        create_tool_approval_action,
    )
    from cuga.backend.cuga_graph.nodes.human_in_the_loop.wait_for_response import WaitForResponse
    from cuga.backend.cuga_graph.utils.nodes_names import ActionIds

    current = {"pending_id": "current-permission", "agent_name": "coder"}
    action = create_tool_approval_action(
        policy_name="ACP permission: Delete",
        required_tools=["delete"],
        code_preview=[],
        full_code="",
        return_to="SupervisorSDKCallback",
    )
    action.additional_data.tool["acp_permission"] = dict(current)
    state = CugaSupervisorState(
        input="work",
        sender="SupervisorSDKCallback",
        hitl_action=action,
        supervisor_metadata={"acp_permission": dict(current)},
    )
    client_response = {
        "action_id": ActionIds.TOOL_APPROVAL,
        "response_type": ActionType.CONFIRMATION,
        "timestamp": "now",
        "confirmed": True,
        "additional_data": {
            "tool": {"acp_permission": {"pending_id": submitted_pending_id, "agent_name": "coder"}}
        },
    }
    if client_submitted_override is not None:
        client_response["submitted_additional_data"] = client_submitted_override
    with patch(
        "cuga.backend.cuga_graph.nodes.human_in_the_loop.wait_for_response.interrupt",
        return_value=client_response,
    ):
        command = asyncio.run(WaitForResponse.node_handler(state))
    return CugaSupervisorState(**command.update)


@pytest.mark.parametrize(
    ("submitted_pending_id", "client_submitted_override", "expected"),
    [
        ("old-permission", None, None),
        # A client cannot bypass the check by supplying submitted_additional_data itself.
        ("old-permission", {"tool": {"acp_permission": {"pending_id": "current-permission"}}}, None),
        ("current-permission", None, True),
    ],
)
def test_stale_approval_through_wait_for_response_cannot_authorize_current_permission(
    submitted_pending_id, client_submitted_override, expected
) -> None:
    from cuga import CugaSupervisor
    from cuga.backend.cuga_graph.nodes.cuga_supervisor.cuga_supervisor_node import CugaSupervisorNode
    from cuga.backend.cuga_graph.state.agent_state import AgentState

    resumed = _resume_through_wait_for_response(
        submitted_pending_id, client_submitted_override=client_submitted_override
    )
    # WaitForResponse still restores the trusted action metadata for downstream consumers.
    assert resumed.hitl_response.additional_data.tool["acp_permission"]["pending_id"] == "current-permission"

    supervisor = CugaSupervisor(agents={}, model=MagicMock(), auto_load_policies=False)
    wrapper = supervisor._create_supervisor_hitl_wrapper_graph()
    command = asyncio.run(wrapper.nodes["SupervisorSDKCallback"].runnable.ainvoke(resumed, config={}))
    assert command.update["supervisor_metadata"]["acp_permission_resume"] == {
        "pending_id": "current-permission",
        "agent_name": "coder",
        "approved": expected,
    }

    captured = {}

    class _FakeSubgraph:
        async def ainvoke(self, state, config=None):
            captured["metadata"] = state.supervisor_metadata
            return state

    node = CugaSupervisorNode()
    node.set_subgraph(_FakeSubgraph())
    server_state = AgentState(
        input="work",
        thread_id="thread",
        sender=resumed.sender,
        hitl_response=resumed.hitl_response,
        supervisor_metadata=resumed.supervisor_metadata,
    )
    asyncio.run(node.node(server_state, config={"configurable": {"thread_id": "thread"}}))
    assert captured["metadata"]["acp_permission_resume"]["approved"] is expected


@pytest.mark.asyncio
async def test_malformed_missing_agent_does_not_cancel_same_thread_other_agent_or_record() -> None:
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
        agent_name="other-agent",
        request=_safe_request(),
        owner=object(),
        prompt_task=prompt_task,
        permission_future=permission_future,
    )
    adapter = SupervisorGraphAdapter(
        agents={"coder": _external_agent()}, pending_acp_registry=registry, interactive=True
    )
    adapter.record_delegation = MagicMock()
    state = CugaSupervisorState(
        input="work",
        thread_id="thread",
        supervisor_metadata={
            "acp_permission_resume": {
                "pending_id": "pending-safe",
                "approved": None,
            },
        },
    )

    result = await create_execute_agent_tool_node(adapter)(state, {"configurable": {"thread_id": "thread"}})

    assert await registry.size() == 1
    assert not prompt_task.done()
    adapter.record_delegation.assert_not_called()
    assert result["final_answer"] == "ACP pending delegation is stale or could not be resumed."
    await registry.cancel("pending-safe", reason="test cleanup")


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
@pytest.mark.parametrize("terminalization", ["ambiguous", "successful"])
async def test_cancelled_winner_terminalization_cleans_and_records_exactly_once(terminalization) -> None:
    from cuga.backend.cuga_graph.nodes.cuga_supervisor.acp_client.pending import (
        PendingACPDelegationRegistry,
    )
    from cuga.backend.cuga_graph.nodes.cuga_supervisor.acp_protocol import ACPPermissionRuntimeBridge
    from cuga.backend.cuga_graph.nodes.cuga_supervisor.cuga_supervisor_state import CugaSupervisorState
    from cuga.backend.cuga_graph.nodes.cuga_supervisor.nodes.execute_agent_tool import (
        create_execute_agent_tool_node,
    )
    from cuga.backend.cuga_graph.nodes.cuga_supervisor.supervisor_graph_adapter import SupervisorGraphAdapter

    cleanup_started = asyncio.Event()
    finish_cleanup = asyncio.Event()
    cleanup_count = 0

    async def cleanup() -> None:
        nonlocal cleanup_count
        cleanup_started.set()
        await finish_cleanup.wait()
        cleanup_count += 1

    registry = PendingACPDelegationRegistry(capacity=1, ttl_seconds=30)
    permission_future = asyncio.get_running_loop().create_future()
    request = _safe_request()
    if terminalization == "ambiguous":
        from cuga.backend.cuga_graph.nodes.cuga_supervisor.acp_client.permissions import (
            PermissionOptionDTO,
        )

        request = replace(
            request,
            options=(PermissionOptionDTO("reject", "Reject once", "reject_once"),),
        )
        prompt_task = asyncio.create_task(asyncio.Event().wait())
    else:
        prompt_task = asyncio.create_task(
            asyncio.sleep(0, result={"result": "done", "status": "completed", "variables": {}})
        )
        await prompt_task
    await registry.insert(
        pending_id="pending-safe",
        thread_id="thread",
        agent_name="coder",
        request=request,
        owner=object(),
        prompt_task=prompt_task,
        permission_future=permission_future,
        cleanup=cleanup,
    )
    adapter = SupervisorGraphAdapter(
        agents={"coder": _external_agent()}, pending_acp_registry=registry, interactive=True
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
                "approved": True,
            },
        },
    )

    execution = asyncio.create_task(
        create_execute_agent_tool_node(adapter)(state, {"configurable": {"thread_id": "thread"}})
    )
    await cleanup_started.wait()
    execution.cancel()
    await asyncio.sleep(0)
    finish_cleanup.set()

    with pytest.raises(asyncio.CancelledError):
        await execution

    assert cleanup_count == 1
    assert await registry.size() == 0
    assert "acp_permission" not in state.supervisor_metadata
    assert "acp_permission_resume" not in state.supervisor_metadata
    adapter.record_delegation.assert_called_once()

    loser = ACPPermissionRuntimeBridge(
        registry=registry, thread_id="thread", agent_name="coder", interactive=True
    )
    with pytest.raises(Exception):
        await loser.resume(pending_id="pending-safe", approved=True)
    adapter.record_delegation.assert_called_once()


async def _assert_registry_terminalizer_racing_claim_records_once(terminalizer: str) -> None:
    from cuga.backend.cuga_graph.nodes.cuga_supervisor.acp_client.pending import (
        PendingACPDelegationRegistry,
    )
    from cuga.backend.cuga_graph.nodes.cuga_supervisor.cuga_supervisor_state import CugaSupervisorState
    from cuga.backend.cuga_graph.nodes.cuga_supervisor.nodes.execute_agent_tool import (
        create_execute_agent_tool_node,
    )
    from cuga.backend.cuga_graph.nodes.cuga_supervisor.supervisor_graph_adapter import SupervisorGraphAdapter

    registry = PendingACPDelegationRegistry(capacity=1, ttl_seconds=0.02)
    permission_future = asyncio.get_running_loop().create_future()
    prompt_resumed = asyncio.Event()
    cleanup_count = 0

    async def stalled_prompt() -> dict[str, object]:
        await permission_future
        prompt_resumed.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            if terminalizer == "expired":
                return {"result": "late success", "status": "completed", "variables": {}}
            raise RuntimeError("private late prompt failure") from None

    async def cleanup() -> None:
        nonlocal cleanup_count
        cleanup_count += 1
        raise RuntimeError("private registry cleanup failure")

    adapter = SupervisorGraphAdapter(
        agents={"coder": _external_agent()}, pending_acp_registry=registry, interactive=True
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
                "approved": True,
            },
        },
    )
    failed_result = {
        "result": f"ACP pending delegation ended without a valid resume ({terminalizer}).",
        "status": "failed",
        "variables": {},
    }

    def finalize(_outcome: str) -> None:
        adapter.record_delegation(
            state,
            "coder",
            result=failed_result,
            answer=failed_result["result"],
            variables={},
        )

    prompt_task = asyncio.create_task(stalled_prompt())
    await registry.insert(
        pending_id="pending-safe",
        thread_id="thread",
        agent_name="coder",
        request=_safe_request(),
        owner=object(),
        prompt_task=prompt_task,
        permission_future=permission_future,
        cleanup=cleanup,
        finalizer=finalize,
    )

    execution = asyncio.create_task(
        create_execute_agent_tool_node(adapter)(state, {"configurable": {"thread_id": "thread"}})
    )
    await prompt_resumed.wait()
    if terminalizer == "shutdown":
        await registry.aclose()

    result = await execution

    assert cleanup_count == 1
    assert await registry.size() == 0
    assert "acp_permission" not in result["supervisor_metadata"]
    assert "acp_permission_resume" not in result["supervisor_metadata"]
    assert result["final_answer"] == "ACP pending delegation is stale or could not be resumed."
    assert "private" not in repr(result)
    adapter.record_delegation.assert_called_once()
    assert adapter.record_delegation.call_args.kwargs["result"] == failed_result


@pytest.mark.asyncio
async def test_ttl_expiry_racing_claimed_resume_transfers_record_authority_once() -> None:
    await _assert_registry_terminalizer_racing_claim_records_once("expired")


@pytest.mark.asyncio
async def test_shutdown_racing_claimed_resume_transfers_record_authority_once() -> None:
    await _assert_registry_terminalizer_racing_claim_records_once("shutdown")


@pytest.mark.asyncio
async def test_cancel_during_registry_owned_finalization_records_once_then_reraises() -> None:
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
    prompt_resumed = asyncio.Event()
    finalizer_started = asyncio.Event()
    finish_finalizer = asyncio.Event()
    cleanup_count = 0

    async def stalled_prompt() -> None:
        await permission_future
        prompt_resumed.set()
        await asyncio.Event().wait()

    async def cleanup() -> None:
        nonlocal cleanup_count
        cleanup_count += 1

    adapter = SupervisorGraphAdapter(
        agents={"coder": _external_agent()}, pending_acp_registry=registry, interactive=True
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
                "approved": True,
            },
        },
    )
    failed_result = {
        "result": "ACP pending delegation ended without a valid resume (shutdown).",
        "status": "failed",
        "variables": {},
    }

    async def finalize(_outcome: str) -> None:
        finalizer_started.set()
        await finish_finalizer.wait()
        adapter.record_delegation(
            state,
            "coder",
            result=failed_result,
            answer=failed_result["result"],
            variables={},
        )

    prompt_task = asyncio.create_task(stalled_prompt())
    await registry.insert(
        pending_id="pending-safe",
        thread_id="thread",
        agent_name="coder",
        request=_safe_request(),
        owner=object(),
        prompt_task=prompt_task,
        permission_future=permission_future,
        cleanup=cleanup,
        finalizer=finalize,
    )
    execution = asyncio.create_task(
        create_execute_agent_tool_node(adapter)(state, {"configurable": {"thread_id": "thread"}})
    )
    await prompt_resumed.wait()
    shutdown = asyncio.create_task(registry.aclose())
    await finalizer_started.wait()
    execution.cancel()
    await asyncio.sleep(0)
    finish_finalizer.set()

    with pytest.raises(asyncio.CancelledError):
        await execution
    await shutdown

    assert cleanup_count == 1
    assert "acp_permission" not in state.supervisor_metadata
    assert "acp_permission_resume" not in state.supervisor_metadata
    adapter.record_delegation.assert_called_once()
    assert adapter.record_delegation.call_args.kwargs["result"] == failed_result


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "finalizer_error", [RuntimeError("private record failure"), asyncio.CancelledError()]
)
async def test_registry_finalizer_failure_returns_record_authority_to_claimant(finalizer_error) -> None:
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
    prompt_resumed = asyncio.Event()

    async def stalled_prompt() -> None:
        await permission_future
        prompt_resumed.set()
        await asyncio.Event().wait()

    async def finalize(_outcome: str) -> None:
        raise finalizer_error

    adapter = SupervisorGraphAdapter(
        agents={"coder": _external_agent()}, pending_acp_registry=registry, interactive=True
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
                "approved": True,
            },
        },
    )
    prompt_task = asyncio.create_task(stalled_prompt())
    await registry.insert(
        pending_id="pending-safe",
        thread_id="thread",
        agent_name="coder",
        request=_safe_request(),
        owner=object(),
        prompt_task=prompt_task,
        permission_future=permission_future,
        finalizer=finalize,
    )
    execution = asyncio.create_task(
        create_execute_agent_tool_node(adapter)(state, {"configurable": {"thread_id": "thread"}})
    )
    await prompt_resumed.wait()
    await registry.aclose()

    with pytest.raises(asyncio.CancelledError):
        await execution

    assert "acp_permission" not in state.supervisor_metadata
    assert "acp_permission_resume" not in state.supervisor_metadata
    adapter.record_delegation.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("terminalization", ["ambiguous", "successful", "cancelled_prompt"])
async def test_claimant_cleanup_failure_preserves_record_authority(terminalization: str) -> None:
    from cuga.backend.cuga_graph.nodes.cuga_supervisor.acp_client.pending import (
        PendingACPDelegationRegistry,
    )
    from cuga.backend.cuga_graph.nodes.cuga_supervisor.cuga_supervisor_state import CugaSupervisorState
    from cuga.backend.cuga_graph.nodes.cuga_supervisor.nodes.execute_agent_tool import (
        create_execute_agent_tool_node,
    )
    from cuga.backend.cuga_graph.nodes.cuga_supervisor.supervisor_graph_adapter import SupervisorGraphAdapter

    permission_future = asyncio.get_running_loop().create_future()

    async def prompt() -> dict[str, object]:
        await permission_future
        if terminalization == "cancelled_prompt":
            raise asyncio.CancelledError
        return {"result": "done", "status": "completed", "variables": {}}

    async def cleanup() -> None:
        raise RuntimeError("private cleanup failure")

    request = _safe_request()
    approved = True
    if terminalization == "ambiguous":
        from cuga.backend.cuga_graph.nodes.cuga_supervisor.acp_client.permissions import (
            PermissionOptionDTO,
        )

        request = replace(request, options=(PermissionOptionDTO("reject", "Reject once", "reject_once"),))

    registry = PendingACPDelegationRegistry(capacity=1, ttl_seconds=30)
    prompt_task = asyncio.create_task(prompt())
    await registry.insert(
        pending_id="pending-safe",
        thread_id="thread",
        agent_name="coder",
        request=request,
        owner=object(),
        prompt_task=prompt_task,
        permission_future=permission_future,
        cleanup=cleanup,
    )
    adapter = SupervisorGraphAdapter(
        agents={"coder": _external_agent()}, pending_acp_registry=registry, interactive=True
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
                "approved": approved,
            },
        },
    )

    execution = create_execute_agent_tool_node(adapter)(state, {"configurable": {"thread_id": "thread"}})
    if terminalization == "cancelled_prompt":
        with pytest.raises(asyncio.CancelledError):
            await execution
    else:
        result = await execution
        assert result["final_answer"] == "ACP pending delegation is stale or could not be resumed."
        assert "private" not in repr(result)

    assert await registry.size() == 0
    assert "acp_permission" not in state.supervisor_metadata
    assert "acp_permission_resume" not in state.supervisor_metadata
    adapter.record_delegation.assert_called_once()


@pytest.mark.asyncio
async def test_failed_prompt_cancelled_during_completion_cleanup_records_once_then_reraises() -> None:
    from cuga.backend.cuga_graph.nodes.cuga_supervisor.acp_client.pending import (
        PendingACPDelegationRegistry,
    )
    from cuga.backend.cuga_graph.nodes.cuga_supervisor.acp_protocol import ACPPermissionRuntimeBridge
    from cuga.backend.cuga_graph.nodes.cuga_supervisor.cuga_supervisor_state import CugaSupervisorState
    from cuga.backend.cuga_graph.nodes.cuga_supervisor.nodes.execute_agent_tool import (
        create_execute_agent_tool_node,
    )
    from cuga.backend.cuga_graph.nodes.cuga_supervisor.supervisor_graph_adapter import SupervisorGraphAdapter

    cleanup_started = asyncio.Event()
    finish_cleanup = asyncio.Event()
    cleanup_attempts = 0
    permission_future = asyncio.get_running_loop().create_future()

    async def failed_prompt() -> None:
        await permission_future
        raise RuntimeError("private prompt failure")

    async def cleanup() -> None:
        nonlocal cleanup_attempts
        cleanup_attempts += 1
        cleanup_started.set()
        await finish_cleanup.wait()

    registry = PendingACPDelegationRegistry(capacity=1, ttl_seconds=30)
    prompt_task = asyncio.create_task(failed_prompt())
    await registry.insert(
        pending_id="pending-safe",
        thread_id="thread",
        agent_name="coder",
        request=_safe_request(),
        owner=object(),
        prompt_task=prompt_task,
        permission_future=permission_future,
        cleanup=cleanup,
    )
    adapter = SupervisorGraphAdapter(
        agents={"coder": _external_agent()}, pending_acp_registry=registry, interactive=True
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
                "approved": True,
            },
        },
    )

    execution = asyncio.create_task(
        create_execute_agent_tool_node(adapter)(state, {"configurable": {"thread_id": "thread"}})
    )
    await cleanup_started.wait()
    execution.cancel()
    await asyncio.sleep(0)
    execution.cancel()
    await asyncio.sleep(0)
    assert cleanup_attempts == 1
    assert not execution.done()
    finish_cleanup.set()

    with pytest.raises(asyncio.CancelledError):
        await execution

    assert cleanup_attempts == 1
    assert await registry.size() == 0
    assert "acp_permission" not in state.supervisor_metadata
    assert "acp_permission_resume" not in state.supervisor_metadata
    adapter.record_delegation.assert_called_once()
    recorded = adapter.record_delegation.call_args.kwargs["result"]
    assert recorded == {
        "result": "ACP pending delegation is stale or could not be resumed.",
        "status": "failed",
        "variables": {},
    }

    loser = ACPPermissionRuntimeBridge(
        registry=registry, thread_id="thread", agent_name="coder", interactive=True
    )
    with pytest.raises(Exception):
        await loser.resume(pending_id="pending-safe", approved=True)
    adapter.record_delegation.assert_called_once()


@pytest.mark.asyncio
async def test_failed_prompt_cleanup_exception_preserves_winner_record_authority() -> None:
    from cuga.backend.cuga_graph.nodes.cuga_supervisor.acp_client.pending import (
        PendingACPDelegationRegistry,
    )
    from cuga.backend.cuga_graph.nodes.cuga_supervisor.acp_protocol import ACPPermissionRuntimeBridge
    from cuga.backend.cuga_graph.nodes.cuga_supervisor.cuga_supervisor_state import CugaSupervisorState
    from cuga.backend.cuga_graph.nodes.cuga_supervisor.nodes.execute_agent_tool import (
        create_execute_agent_tool_node,
    )
    from cuga.backend.cuga_graph.nodes.cuga_supervisor.supervisor_graph_adapter import SupervisorGraphAdapter

    cleanup_attempts = 0
    permission_future = asyncio.get_running_loop().create_future()

    async def failed_prompt() -> None:
        await permission_future
        raise RuntimeError("private prompt failure")

    async def cleanup() -> None:
        nonlocal cleanup_attempts
        cleanup_attempts += 1
        raise RuntimeError("private cleanup failure")

    registry = PendingACPDelegationRegistry(capacity=1, ttl_seconds=30)
    prompt_task = asyncio.create_task(failed_prompt())
    await registry.insert(
        pending_id="pending-safe",
        thread_id="thread",
        agent_name="coder",
        request=_safe_request(),
        owner=object(),
        prompt_task=prompt_task,
        permission_future=permission_future,
        cleanup=cleanup,
    )
    adapter = SupervisorGraphAdapter(
        agents={"coder": _external_agent()}, pending_acp_registry=registry, interactive=True
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
                "approved": True,
            },
        },
    )

    result = await create_execute_agent_tool_node(adapter)(state, {"configurable": {"thread_id": "thread"}})

    assert cleanup_attempts == 1
    assert await registry.size() == 0
    assert "acp_permission" not in result["supervisor_metadata"]
    assert "acp_permission_resume" not in result["supervisor_metadata"]
    assert result["final_answer"] == "ACP pending delegation is stale or could not be resumed."
    assert "private" not in repr(result)
    adapter.record_delegation.assert_called_once()
    recorded = adapter.record_delegation.call_args.kwargs["result"]
    assert recorded == {
        "result": "ACP pending delegation is stale or could not be resumed.",
        "status": "failed",
        "variables": {},
    }

    loser = ACPPermissionRuntimeBridge(
        registry=registry, thread_id="thread", agent_name="coder", interactive=True
    )
    with pytest.raises(Exception):
        await loser.resume(pending_id="pending-safe", approved=True)
    adapter.record_delegation.assert_called_once()


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
