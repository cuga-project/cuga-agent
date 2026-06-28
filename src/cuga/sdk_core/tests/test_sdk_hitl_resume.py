"""Tests for SDK HITL resume (Command) and tool-approval denial paths."""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langgraph.types import Command

from cuga import CugaAgent
from cuga.backend.cuga_graph.nodes.human_in_the_loop.followup_model import (
    ActionResponse,
    ActionType,
)
from cuga.backend.cuga_graph.state.agent_state import AgentState


class TestSDKHitlResume:
    @pytest.mark.asyncio
    async def test_invoke_resumes_interrupt_with_command(self):
        agent = CugaAgent(tools=[], auto_load_policies=False, reset_policy_storage=True)
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
        agent = CugaAgent(tools=[], auto_load_policies=False, reset_policy_storage=True)
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


class TestToolApprovalDenial:
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
