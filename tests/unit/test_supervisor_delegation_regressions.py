"""Exercise supervisor response routing through the compiled delegation graph."""

import asyncio
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
    """Provide a real SDK child with deterministic delegation responses."""
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
    """Replay model responses through the real compiled supervisor graph."""
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
@pytest.mark.parametrize("reply", [UNEXECUTED_ANSWER, "User ID: {user_id}", "The user ID is `{user_id}`."])
async def test_unexecuted_playbook_answer_does_not_end_before_delegation(worker, reply):
    """Correct explicit result placeholders by executing the requested delegation."""
    result = await run_supervisor(
        worker,
        [reply, DELEGATION_CODE, "Alice: user_alice_99."],
    )
    assert result["selected_agents"] == ["user_finder"]
    assert result["metrics"]["delegation_count"] == 1
    assert result["final_answer"] == "Alice: user_alice_99."
    worker.invoke.assert_awaited_once()


@pytest.mark.asyncio
async def test_direct_code_records_delegation(worker):
    """Record delegation when the first model response already contains code."""
    result = await run_supervisor(worker, [DELEGATION_CODE, "Alice: user_alice_99."])
    assert result["selected_agents"] == ["user_finder"]
    assert result["metrics"]["delegation_count"] == 1
    worker.invoke.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("reply", ["Hello!", "Which Alice do you mean?", "I cannot proceed without consent."])
async def test_completed_or_blocked_reply_does_not_force_delegation(worker, reply):
    """Return conversational and blocked replies without invoking a child."""
    result = await run_supervisor(worker, [reply])
    assert result["final_answer"] == reply
    assert result["selected_agents"] == []
    worker.invoke.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "reply",
    [
        "I need the customer's {account_number} before I can continue.",
        "Please provide {account_number} so I can find the account.",
        "What is the customer's {account_number}?",
        "I cannot proceed without {consent}.",
        "Her user ID is {user_id}, but I need your consent before continuing.",
        "Her user ID is {user_id}. Awaiting customer approval.",
        "The required account number is {account_number}.",
        "Use {user_id} in the template.",
    ],
)
async def test_placeholder_input_or_template_does_not_retry(worker, reply):
    """Input requests must finish before a subsequent model reply can delegate."""
    result = await run_supervisor(worker, [reply, DELEGATION_CODE, "Done."])
    assert result["final_answer"] == reply
    assert result["step_count"] == 1
    assert result["selected_agents"] == []
    worker.invoke.assert_not_awaited()


@pytest.mark.asyncio
async def test_repeated_placeholder_answer_is_not_retried_forever(worker):
    """Spend at most one corrective retry on an unresolved result."""
    result = await run_supervisor(worker, [UNEXECUTED_ANSWER])
    assert result["step_count"] == 2
    worker.invoke.assert_not_awaited()


@pytest.mark.asyncio
async def test_template_outside_playbook_is_not_retried(worker):
    """Leave non-playbook placeholder templates unchanged."""
    result = await run_supervisor(worker, ["Use {user_id} in the template."], playbook=False)
    assert result["step_count"] == 1
    worker.invoke.assert_not_awaited()


@pytest.mark.asyncio
async def test_placeholder_correction_respects_step_limit(worker):
    """Honor the model step budget before attempting a corrective delegation."""
    result = await run_supervisor(worker, [UNEXECUTED_ANSWER, DELEGATION_CODE], max_steps=1)
    assert "Maximum step limit" in result["final_answer"]
    worker.invoke.assert_not_awaited()


@pytest.mark.asyncio
async def test_sub_agent_can_outlive_a_normal_sandbox_block(worker, monkeypatch):
    """Allow a child to finish beyond the ordinary sandbox deadline."""
    monkeypatch.setattr(settings.advanced_features, "sandbox_execution_timeout", 0.05)
    monkeypatch.setattr(settings.supervisor, "execution_timeout", 1, raising=False)

    async def slow_agent(*args, **kwargs):
        await asyncio.sleep(0.15)
        return SimpleNamespace(answer="user_alice_99", variables={}, chat_messages=[])

    worker.invoke.side_effect = slow_agent
    result = await run_supervisor(worker, [DELEGATION_CODE, "Alice: user_alice_99."])
    assert result["selected_agents"] == ["user_finder"]
    assert result["metrics"]["delegation_count"] == 1
    assert settings.advanced_features.sandbox_execution_timeout == 0.05


@pytest.mark.asyncio
async def test_supervisor_delegation_deadline_still_cancels_stalled_agent(worker, monkeypatch):
    """Cancel stalled children at the supervisor-specific deadline."""
    monkeypatch.setattr(settings.supervisor, "execution_timeout", 0.05)
    cancelled = []

    async def stalled_agent(*args, **kwargs):
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            cancelled.append(True)
            raise

    worker.invoke.side_effect = stalled_agent
    result = await run_supervisor(worker, [DELEGATION_CODE, "The delegation timed out."])
    assert cancelled == [True]
    assert result["selected_agents"] == []
    assert any("timed out after 0.05" in message.content for message in result["supervisor_chat_messages"])
