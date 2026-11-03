"""Checkpoint compatibility: an ``AgentState`` written by an older release must still load.

Durable checkpointers keep a thread's state across CUGA upgrades, and LangGraph does
not migrate application state (design §5.3, issue #774). ``fixtures/agent_state_v*.json``
holds an ``AgentState`` dumped by a past release; every fixture is loaded on every build.

Add a fixture for a release with ``uv run python tests/unit/state/test_agent_state_compat.py``.
Never edit an existing fixture: it records what that release wrote.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from pydantic import AliasChoices

from cuga.backend.cuga_graph.state.agent_state import AgentState

pytestmark = pytest.mark.unit

FIXTURES_DIR = Path(__file__).parent / "fixtures"

# Keys an older release wrote that AgentState no longer declares, mapped to the reason.
# A rename must add a validation_alias for the old name instead of landing here.
ACKNOWLEDGED_REMOVED_FIELDS: dict[str, str] = {}


def _accepted_input_keys() -> set[str]:
    keys = set()
    for name, field in AgentState.model_fields.items():
        keys.add(name)
        if field.alias:
            keys.add(field.alias)
        if isinstance(field.validation_alias, AliasChoices):
            keys.update(c for c in field.validation_alias.choices if isinstance(c, str))
        elif isinstance(field.validation_alias, str):
            keys.add(field.validation_alias)
    return keys


@pytest.fixture(params=sorted(FIXTURES_DIR.glob("agent_state_v*.json")), ids=lambda p: p.stem)
def fixture_values(request) -> dict:
    return json.loads(request.param.read_text(encoding="utf-8"))


def test_old_checkpoint_loads(fixture_values):
    """A field added without a default, or a type narrowed incompatibly, fails here."""
    AgentState(**fixture_values)


def test_old_checkpoint_keys_are_still_accepted(fixture_values):
    """A stored key the model no longer accepts is dropped silently, so it must be deliberate."""
    unknown = set(fixture_values) - _accepted_input_keys() - set(ACKNOWLEDGED_REMOVED_FIELDS)
    assert not unknown, (
        f"stored keys no longer accepted by AgentState: {sorted(unknown)}. "
        "Add a validation_alias for a rename, or list a removal in ACKNOWLEDGED_REMOVED_FIELDS."
    )


def test_unknown_keys_are_ignored():
    """``extra="forbid"`` would break old checkpoints; ``extra="allow"`` would resurrect removed fields."""
    assert AgentState.model_config.get("extra", "ignore") == "ignore"


if __name__ == "__main__":
    import tomllib

    root = Path(__file__).resolve().parents[3]
    version = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    target = FIXTURES_DIR / f"agent_state_v{version.replace('.', '_')}.json"
    if target.exists():
        sys.exit(f"{target} exists; fixtures are never rewritten")
    target.write_text(json.dumps(AgentState().model_dump(mode="json"), indent=2, sort_keys=True) + "\n")
    print(f"wrote {target}")
