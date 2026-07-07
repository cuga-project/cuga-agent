from __future__ import annotations

from typing import Any

import pytest

from cuga.backend.cuga_graph.policy.models import ToolGuide
from cuga.backend.cuga_graph.policy.utils import apply_policies_data_to_storage
from cuga.backend.server.main import _policy_to_frontend_dict


class FakePolicyStorage:
    def __init__(self) -> None:
        self.policies: list[Any] = []

    async def list_policies(self, enabled_only: bool = False) -> list[Any]:
        return list(self.policies)

    async def delete_policy(self, policy_id: str) -> None:
        self.policies = [policy for policy in self.policies if policy.id != policy_id]

    async def add_policy(self, policy: Any) -> None:
        self.policies.append(policy)


def _generated_tool_guide() -> ToolGuide:
    return ToolGuide(
        id="policy_toolguide_flights",
        name="Flight booking rules",
        description="Rules for booking flights.",
        triggers=[],
        target_tools=["book_flight"],
        target_apps=["travel"],
        guide_content="Only book refundable flights.",
        tool_guards={
            "book_flight": {
                "violating_examples": ["Book a non-refundable flight."],
                "compliance_examples": ["Book a refundable flight."],
                "policy_code": "def guard_tool_call(context):\n    return True\n",
            }
        },
        guards_enabled=False,
        prepend=False,
        priority=10,
        enabled=True,
    )


@pytest.mark.asyncio
async def test_frontend_export_import_roundtrip_preserves_tool_guards_and_guards_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    generation_calls: list[str] = []

    async def fail_if_generation_is_called(*args: Any, **kwargs: Any) -> None:
        generation_calls.append("called")
        raise AssertionError("ToolGuard generation must not run during import")

    monkeypatch.setattr(
        "cuga.backend.server.tool_guard_generation.generate_tool_guards_for_policy",
        fail_if_generation_is_called,
    )

    exported_policy = _policy_to_frontend_dict(_generated_tool_guide().model_dump())
    imported_storage = FakePolicyStorage()

    result = await apply_policies_data_to_storage(
        imported_storage,
        [exported_policy],
        clear_existing=True,
        filesystem_sync=None,
    )

    assert result == {"count": 1, "errors": []}
    assert generation_calls == []
    assert len(imported_storage.policies) == 1

    imported_policy = imported_storage.policies[0]
    assert isinstance(imported_policy, ToolGuide)
    assert imported_policy.guards_enabled is False
    assert imported_policy.tool_guards["book_flight"].violating_examples == [
        "Book a non-refundable flight."
    ]
    assert imported_policy.tool_guards["book_flight"].compliance_examples == [
        "Book a refundable flight."
    ]
    assert (
        imported_policy.tool_guards["book_flight"].policy_code
        == "def guard_tool_call(context):\n    return True\n"
    )
