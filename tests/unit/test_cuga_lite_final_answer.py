from types import SimpleNamespace

import pytest

from cuga.backend.cuga_graph.nodes.answer.final_answer import FinalAnswerNode
from cuga.backend.cuga_graph.nodes.cuga_lite.cuga_lite_node import CugaLiteNode
from cuga.backend.cuga_graph.state.agent_state import AgentState
from cuga.backend.cuga_graph.utils.nodes_names import NodeNames


pytestmark = pytest.mark.unit


class _Variables:
    def __init__(self, values):
        self._values = values

    def get_variable(self, name):
        return self._values.get(name)


def _state_with_top_account():
    return SimpleNamespace(
        variables_manager=_Variables({"top_account": {"name": "Andromeda Inc.", "revenue": 9_700_000}})
    )


def test_signoff_without_result_requests_final_answer_generation():
    state = _state_with_top_account()
    answer = "Great! Let me know if you'd like any additional details about that account."

    assert CugaLiteNode._should_regenerate_final_answer(state, answer, ["top_account"])


def test_signoff_with_result_value_keeps_fast_path():
    state = _state_with_top_account()
    answer = (
        "The top account is Andromeda Inc. with revenue of 9,700,000. Let me know if you need anything else."
    )

    assert not CugaLiteNode._should_regenerate_final_answer(state, answer, ["top_account"])


def test_incidental_earlier_scalar_does_not_hide_omitted_latest_result():
    state = SimpleNamespace(
        variables_manager=_Variables(
            {
                "status": "done",
                "top_account": {"name": "Andromeda Inc.", "revenue": 9_700_000},
            }
        )
    )
    answer = "The status is done. Let me know if you'd like any additional details."

    assert CugaLiteNode._should_regenerate_final_answer(state, answer, ["status", "top_account"])


def test_signoff_without_new_variables_keeps_fast_path():
    state = _state_with_top_account()
    answer = "Great! Let me know if you'd like any additional details."

    assert not CugaLiteNode._should_regenerate_final_answer(state, answer, [])


@pytest.mark.asyncio
async def test_regeneration_metadata_dispatches_to_final_answer_generator(monkeypatch):
    state = AgentState(
        sender=NodeNames.CUGA_LITE,
        cuga_lite_metadata={"regenerate_final_answer": True},
    )
    agent = SimpleNamespace(name="FinalAnswerAgent")
    calls = []

    async def fake_generate(state_arg, agent_arg, name_arg):
        calls.append((state_arg, agent_arg, name_arg))

    monkeypatch.setattr(FinalAnswerNode, "_generate_final_answer", fake_generate)

    result = await FinalAnswerNode.node_handler(
        state,
        agent=agent,
        name=agent.name,
        hitl_handler=SimpleNamespace(),
    )

    assert calls == [(state, agent, agent.name)]
    assert result.goto == NodeNames.END
