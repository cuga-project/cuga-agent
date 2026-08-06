"""Verify that FinalAnswerNode records native dict payloads in the tracker (issue #585)."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import pytest

from cuga.backend.cuga_graph.nodes.answer.final_answer import FinalAnswerNode
from cuga.backend.cuga_graph.state.agent_state import AgentState
from cuga.backend.cuga_graph.utils.nodes_names import NodeNames

pytestmark = pytest.mark.unit


def _make_state(**overrides):
    defaults = dict(input="test input", url="https://example.com", thread_id="t-x")
    defaults.update(overrides)
    return AgentState(**defaults)


def test_cuga_lite_sender_records_dict_step_data():
    """FinalAnswerNode (CugaLite branch) must record a dict, not a JSON string."""
    state = _make_state(
        sender=NodeNames.CUGA_LITE,
        final_answer="done",
    )

    mock_tracker = MagicMock()

    with patch(
        "cuga.backend.cuga_graph.nodes.answer.final_answer.tracker",
        mock_tracker,
    ):
        asyncio.run(
            FinalAnswerNode.node_handler(
                state,
                agent=None,
                name="FinalAnswerAgent",
                hitl_handler=None,
            )
        )

    mock_tracker.collect_step.assert_called_once()
    step = mock_tracker.collect_step.call_args.kwargs["step"]
    assert step.data == {"thoughts": [], "final_answer": "done"}
    # AI message content must remain serialized text (LangChain contract)
    assert isinstance(state.messages[-1].content, str)
