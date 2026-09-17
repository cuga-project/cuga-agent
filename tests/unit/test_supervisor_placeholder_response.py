"""Exercise supervisor response routing through the compiled delegation graph."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.messages import HumanMessage

from cuga.backend.cuga_graph.nodes.cuga_supervisor.cuga_supervisor_graph import create_cuga_supervisor_graph
from cuga.backend.cuga_graph.nodes.cuga_supervisor.cuga_supervisor_state import CugaSupervisorState
from cuga.config import settings
from cuga.sdk import CugaAgent

pytestmark = pytest.mark.unit

DELEGATION_CODE = '''```python
user_id = await delegate_to_user_finder("Find Alice's user ID")
print(user_id)
```'''

# Captured from the live SDK onboarding failure: no code/tool call preceded it.
UNEXECUTED_ANSWER = (
    "Alice has been onboarded. Her user ID is **{alice_user_id}** "
    "and her account value is **{alice_account_value}**."
)


@pytest.fixture
def worker(monkeypatch):
    monkeypatch.setattr(settings.policy, "enabled", False)
    monkeypatch.setattr(settings.advanced_features, "registry", False)
    agent = CugaAgent(
        model=FakeListChatModel(responses=["unused"]),
        auto_load_policies=False,
        filesystem_sync=False,
    )
    agent.description = "Find user IDs"
    agent.invoke = AsyncMock(
        return_value=SimpleNamespace(answer="user_alice_99", variables={}, chat_messages=[])
    )
    return agent


async def run_supervisor(worker, responses, *, playbook=True, max_steps=10):
    model = FakeListChatModel(responses=responses)
    graph = create_cuga_supervisor_graph(
        model,
        {"user_finder": worker},
        special_instructions="Delegate to user_finder to get Alice's user ID.",
    ).compile()
    return await graph.ainvoke(
        CugaSupervisorState(
            input="Find Alice's user ID",
            supervisor_chat_messages=[HumanMessage(content="Find Alice's user ID")],
            cuga_lite_max_steps=max_steps,
            supervisor_metadata={"policy_type": "playbook", "playbook_guidance": "Delegate to user_finder"}
            if playbook
            else {},
        )
    )


@pytest.mark.asyncio
async def test_unexecuted_playbook_answer_does_not_end_before_delegation(worker):
    result = await run_supervisor(
        worker,
        [UNEXECUTED_ANSWER, DELEGATION_CODE, "Alice: user_alice_99."],
    )
    assert result["selected_agents"] == ["user_finder"]
    assert result["metrics"]["delegation_count"] == 1
    assert result["final_answer"] == "Alice: user_alice_99."
    worker.invoke.assert_awaited_once()


@pytest.mark.asyncio
async def test_direct_code_records_delegation(worker):
    result = await run_supervisor(worker, [DELEGATION_CODE, "Alice: user_alice_99."])
    assert result["selected_agents"] == ["user_finder"]
    assert result["metrics"]["delegation_count"] == 1
    worker.invoke.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("reply", ["Hello!", "Which Alice do you mean?", "I cannot proceed without consent."])
async def test_completed_or_blocked_reply_does_not_force_delegation(worker, reply):
    result = await run_supervisor(worker, [reply])
    assert result["final_answer"] == reply
    assert result["selected_agents"] == []
    worker.invoke.assert_not_awaited()


@pytest.mark.asyncio
async def test_repeated_placeholder_answer_is_not_retried_forever(worker):
    result = await run_supervisor(worker, [UNEXECUTED_ANSWER])
    assert result["step_count"] == 2
    worker.invoke.assert_not_awaited()


@pytest.mark.asyncio
async def test_template_outside_playbook_is_not_retried(worker):
    result = await run_supervisor(worker, ["Use {user_id} in the template."], playbook=False)
    assert result["step_count"] == 1
    worker.invoke.assert_not_awaited()


@pytest.mark.asyncio
async def test_placeholder_correction_respects_step_limit(worker):
    result = await run_supervisor(worker, [UNEXECUTED_ANSWER, DELEGATION_CODE], max_steps=1)
    assert "Maximum step limit" in result["final_answer"]
    worker.invoke.assert_not_awaited()
