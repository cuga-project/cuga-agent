"""Helpers for mutating ToolGuard policy fields."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from cuga.backend.cuga_graph.policy.models import ToolGuard


def merge_tool_guards(
    existing: Mapping[str, ToolGuard] | None,
    incoming: Mapping[str, Mapping[str, Any] | ToolGuard],
) -> dict[str, ToolGuard]:
    """Merge incoming tool guard updates into existing tool guards.

    Omitted tool entries are preserved. For an existing tool guard, omitted fields
    are also preserved so updating examples does not accidentally drop generated
    policy code, and updating policy code does not drop examples.
    """
    merged = dict(existing or {})

    for tool_name, guard_data in incoming.items():
        existing_guard = merged.get(tool_name)

        if isinstance(guard_data, ToolGuard):
            incoming_data = guard_data.model_dump(exclude_unset=True)
        else:
            incoming_data = dict(guard_data)

        merged[tool_name] = ToolGuard(
            violating_examples=incoming_data.get(
                "violating_examples",
                existing_guard.violating_examples if existing_guard else [],
            ),
            compliance_examples=incoming_data.get(
                "compliance_examples",
                existing_guard.compliance_examples if existing_guard else [],
            ),
            policy_code=incoming_data.get(
                "policy_code",
                existing_guard.policy_code if existing_guard else "",
            ),
        )

    return merged
