from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from cuga.backend.evolve.memory import build_evolve_special_instructions_extension
from cuga.backend.evolve.memory import _memory_user_id

pytestmark = pytest.mark.unit


def test_default_user_is_only_sent_to_evolve_in_compliance_poc(monkeypatch):
    state = SimpleNamespace(user_id="default_user")

    monkeypatch.delenv("CUGA_COMPLIANCE_POC_SEED_ENABLED", raising=False)
    assert _memory_user_id(state) is None

    monkeypatch.setenv("CUGA_COMPLIANCE_POC_SEED_ENABLED", "1")
    assert _memory_user_id(state) == "default_user"


@pytest.mark.asyncio
async def test_prompt_context_records_exact_attributed_memory_ids():
    state = SimpleNamespace(
        sub_task="Prepare a concise renewal summary",
        chat_messages=[],
        user_id="user-a",
        service_scope={
            "tenant_id": "tenant-a",
            "agent_id": "agent-a",
            "memory_turn_id": "turn-a",
        },
        thread_id="thread-a",
        input="Prepare a concise renewal summary",
    )
    with (
        patch(
            "cuga.backend.evolve.memory.EvolveIntegration.is_enabled",
            return_value=True,
        ),
        patch(
            "cuga.backend.evolve.memory.EvolveIntegration.get_guidelines_with_attribution",
            new=AsyncMock(
                return_value={
                    "text": "Use the customer's preferred account name.",
                    "entity_ids": ["guideline-a"],
                }
            ),
        ),
        patch(
            "cuga.backend.evolve.memory.EvolveIntegration.retrieve_user_facts",
            new=AsyncMock(
                return_value={
                    "categories": {
                        "preferences": [
                            {
                                "id": "fact-a",
                                "content": "Prefers concise renewal summaries.",
                            }
                        ]
                    }
                }
            ),
        ),
        patch(
            "cuga.backend.evolve.memory.EvolveIntegration.store_user_facts",
            new=AsyncMock(),
        ),
        patch(
            "cuga.backend.evolve.memory.record_memory_usage",
            new=AsyncMock(),
        ) as record_usage,
    ):
        result = await build_evolve_special_instructions_extension(
            state=state,
            configurable={"agent_id": "agent-a", "thread_id": "thread-a"},
            timeout=1,
        )

    assert "preferred account name" in result
    assert "concise renewal summaries" in result
    record_usage.assert_awaited_once_with(
        turn_id="turn-a",
        agent_id="agent-a",
        user_id="user-a",
        entity_ids=["guideline-a", "fact-a"],
        thread_id="thread-a",
        conversation_label="Prepare a concise renewal summary",
    )
