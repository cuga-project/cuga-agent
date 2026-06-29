from __future__ import annotations

from typing import Any

import pytest

from cuga.backend.cuga_graph.policy.models import ToolGuide
from cuga.backend.cuga_graph.policy.utils import apply_policies_data_to_storage


def test_tool_guide_accepts_null_tool_guards() -> None:
    policy = ToolGuide(
        id="finance_revenue_requirements",
        name="Finance eligibility revenue requirements",
        description="Revenue requirements for finance eligibility.",
        triggers=[],
        target_tools=["*"],
        guide_content="Use revenue eligibility requirements.",
        tool_guards=None,
    )

    assert policy.tool_guards == {}


class FakePolicyStorage:
    def __init__(self) -> None:
        self.policies: list[Any] = []

    async def add_policy(self, policy: Any) -> None:
        self.policies.append(policy)


@pytest.mark.asyncio
async def test_apply_policies_data_to_storage_loads_tool_guide_with_null_tool_guards() -> None:
    storage = FakePolicyStorage()

    result = await apply_policies_data_to_storage(
        storage,
        [
            {
                "type": "tool_guide",
                "id": "finance_revenue_requirements",
                "name": "Finance eligibility revenue requirements",
                "description": "Revenue requirements for finance eligibility.",
                "triggers": [],
                "target_tools": ["*"],
                "guide_content": "Use revenue eligibility requirements.",
                "tool_guards": None,
            }
        ],
        clear_existing=False,
    )

    assert result == {"count": 1, "errors": []}
    assert len(storage.policies) == 1
    assert isinstance(storage.policies[0], ToolGuide)
    assert storage.policies[0].tool_guards == {}


@pytest.mark.asyncio
async def test_apply_policies_data_to_storage_loads_playbook_with_null_triggers_and_steps() -> None:
    storage = FakePolicyStorage()

    result = await apply_policies_data_to_storage(
        storage,
        [
            {
                "type": "playbook",
                "id": "playbook_1",
                "name": "Playbook 1",
                "description": "Test playbook",
                "triggers": None,
                "steps": None,
                "markdown_content": "# Guide",
            }
        ],
        clear_existing=False,
    )

    assert result == {"count": 1, "errors": []}
    assert len(storage.policies) == 1
    assert storage.policies[0].triggers == []
    assert storage.policies[0].steps == []
