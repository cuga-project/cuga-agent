"""Tests for SDK HITL resume (Command) and tool-approval denial paths."""

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langgraph.types import Command

from cuga import CugaAgent, CugaSupervisor
from cuga.backend.cuga_graph.nodes.human_in_the_loop.followup_model import (
    ActionResponse,
    ActionType,
)
from cuga.backend.cuga_graph.state.agent_state import AgentState
from cuga.sdk import _record_denied_policy_decision

pytestmark = pytest.mark.unit


class TestSDKHitlResume:
    @pytest.mark.asyncio
    async def test_invoke_resumes_interrupt_with_command(self):
        agent = CugaAgent(
            tools=[],
            model=MagicMock(),
            auto_load_policies=False,
            reset_policy_storage=True,
        )
        mock_graph = MagicMock()
        mock_graph.ainvoke = AsyncMock(return_value={"final_answer": "done", "tool_calls": []})
        agent._compiled_graph = mock_graph

        approval = ActionResponse(
            action_id="tool_approval",
            response_type=ActionType.CONFIRMATION,
            confirmed=True,
            timestamp=datetime.now().isoformat(),
        )

        with patch("cuga.sdk.init_openlit"), patch("cuga.sdk.set_session_attribute"):
            result = await agent.invoke(None, thread_id="hitl-resume-test", action_response=approval)

        assert result.answer == "done"
        mock_graph.ainvoke.assert_awaited_once()
        resume_arg = mock_graph.ainvoke.await_args.args[0]
        assert isinstance(resume_arg, Command)
        assert resume_arg.resume == approval.model_dump()
        mock_graph.update_state.assert_not_called()

    @pytest.mark.asyncio
    async def test_stream_resumes_interrupt_with_command(self):
        agent = CugaAgent(
            tools=[],
            model=MagicMock(),
            auto_load_policies=False,
            reset_policy_storage=True,
        )
        mock_graph = MagicMock()

        async def fake_astream(resume_input, **kwargs):
            assert isinstance(resume_input, Command)
            yield ("", {"node": {}})

        mock_graph.astream = fake_astream
        agent._compiled_graph = mock_graph

        approval = ActionResponse(
            action_id="tool_approval",
            response_type=ActionType.CONFIRMATION,
            confirmed=True,
            timestamp=datetime.now().isoformat(),
        )

        with patch("cuga.sdk.init_openlit"), patch("cuga.sdk.set_session_attribute"):
            chunks = [
                chunk
                async for chunk in agent.stream(None, thread_id="hitl-stream-test", action_response=approval)
            ]

        assert len(chunks) == 1

    @pytest.mark.asyncio
    async def test_stream_starts_new_turn_with_empty_policy_decisions(self):
        agent = CugaAgent(
            tools=[],
            model=MagicMock(),
            auto_load_policies=False,
        )
        mock_graph = MagicMock()
        captured = {}

        async def fake_astream(initial_state, **kwargs):
            captured["initial_state"] = initial_state
            yield ("", {"node": {}})

        mock_graph.astream = fake_astream
        agent._compiled_graph = mock_graph

        with (
            patch.object(agent, "_ensure_initialized", new=AsyncMock()),
            patch.object(agent, "_apply_callbacks"),
            patch.object(agent, "_inject_knowledge_to_config"),
            patch("cuga.sdk.init_openlit"),
            patch("cuga.sdk.set_session_attribute"),
        ):
            chunks = [
                chunk
                async for chunk in agent.stream(
                    "start a new turn",
                    thread_id="stream-policy-reset-test",
                )
            ]

        assert len(chunks) == 1
        assert captured["initial_state"]["cuga_lite_metadata"]["policy_decisions"] == []


class TestToolApprovalDenial:
    def test_denial_preserves_approval_and_records_tool_name(self):
        approval_required = {
            "policy_id": "approval-1",
            "policy_name": "Approve database deletion",
            "policy_type": "tool_approval",
            "action_type": "tool_require_approval",
            "stage": "tool",
            "outcome": "approval_required",
            "tool_name": "delete_database",
        }
        metadata = {
            "policy_id": "approval-1",
            "policy_name": "Approve database deletion",
            "policy_type": "tool_approval",
            "required_tools": ["delete_database"],
            "policy_decisions": [approval_required],
        }

        updated = _record_denied_policy_decision(metadata)

        decisions = updated["policy_decisions"]
        assert [decision["outcome"] for decision in decisions] == [
            "approval_required",
            "denied",
        ]
        assert decisions[0]["policy_id"] == approval_required["policy_id"]
        assert decisions[0]["tool_name"] == approval_required["tool_name"]
        assert decisions[1]["tool_name"] == "delete_database"

    @pytest.mark.asyncio
    async def test_agent_sdk_callback_records_denial(self):
        from cuga.backend.cuga_graph.nodes.answer.final_answer_agent.final_answer_agent import (
            FinalAnswerAgent,
        )
        from cuga.backend.cuga_graph.utils.nodes_names import NodeNames

        agent = CugaAgent(
            tools=[],
            model=MagicMock(),
            auto_load_policies=False,
            enable_knowledge=False,
        )
        with patch.object(
            FinalAnswerAgent,
            "create",
            return_value=SimpleNamespace(name="FinalAnswerAgent"),
        ):
            wrapper = agent._create_hitl_wrapper_graph()

        state = AgentState(
            input="delete the database",
            url="",
            sender=NodeNames.WAIT_FOR_RESPONSE,
            hitl_response=_denial_response(),
            cuga_lite_metadata=_approval_metadata(),
        )

        command = await wrapper.nodes["SDKCallback"].runnable.ainvoke(state, config={})

        assert command.goto == "FinalAnswerAgent"
        decisions = command.update["cuga_lite_metadata"]["policy_decisions"]
        assert [decision["outcome"] for decision in decisions] == [
            "approval_required",
            "denied",
        ]

    @pytest.mark.asyncio
    async def test_supervisor_sdk_callback_records_denial(self):
        from langgraph.graph import END

        from cuga.backend.cuga_graph.nodes.cuga_supervisor.cuga_supervisor_state import (
            CugaSupervisorState,
        )
        from cuga.backend.cuga_graph.utils.nodes_names import NodeNames

        supervisor = CugaSupervisor(
            agents={},
            model=MagicMock(),
            auto_load_policies=False,
        )
        wrapper = supervisor._create_supervisor_hitl_wrapper_graph()
        state = CugaSupervisorState(
            input="delete the database",
            url="",
            sender=NodeNames.WAIT_FOR_RESPONSE,
            hitl_response=_denial_response(),
            supervisor_metadata=_approval_metadata(),
        )

        command = await wrapper.nodes["SupervisorSDKCallback"].runnable.ainvoke(state, config={})

        assert command.goto == END
        decisions = command.update["supervisor_metadata"]["policy_decisions"]
        assert [decision["outcome"] for decision in decisions] == [
            "approval_required",
            "denied",
        ]

    def test_agent_state_has_no_execution_complete_field(self):
        state = AgentState(input="test", url="")
        with pytest.raises(ValueError, match="execution_complete"):
            state.execution_complete = True

    def test_denial_final_answer_does_not_need_execution_complete(self):
        state = AgentState(input="test", url="")
        state.final_answer = "❌ **Execution Cancelled**"
        dumped = state.model_dump()
        assert "cancel" in dumped["final_answer"].lower()
        assert "execution_complete" not in dumped


def _approval_metadata():
    return {
        "policy_id": "approval-1",
        "policy_name": "Approve database deletion",
        "policy_type": "tool_approval",
        "required_tools": ["delete_database"],
        "policy_decisions": [
            {
                "policy_id": "approval-1",
                "policy_name": "Approve database deletion",
                "policy_type": "tool_approval",
                "action_type": "tool_require_approval",
                "stage": "tool",
                "outcome": "approval_required",
                "tool_name": "delete_database",
            }
        ],
    }


def _denial_response():
    return ActionResponse(
        action_id="tool_approval",
        response_type=ActionType.CONFIRMATION,
        confirmed=False,
        timestamp=datetime.now().isoformat(),
    )
