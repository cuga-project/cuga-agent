"""Unit tests for policy observability on the public SDK result."""

import subprocess
import sys

import pytest

from cuga.backend.cuga_graph.policy.models import (
    PolicyDecisionOutcome,
    PolicyDecisionStage,
    PolicyType,
)
from cuga.sdk import InvokeResult

pytestmark = pytest.mark.unit


def test_empty_policy_decisions_do_not_eagerly_import_policy_modules():
    script = """
import sys

from cuga.sdk import InvokeResult

InvokeResult(answer="ok", policy_decisions=[])

unexpected = {
    "cuga.backend.cuga_graph.policy",
    "cuga.backend.cuga_graph.policy.models",
}.intersection(sys.modules)
assert not unexpected, f"policy modules were imported eagerly: {sorted(unexpected)}"
"""

    completed = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr


def test_invoke_result_defaults_to_no_policy_decisions():
    result = InvokeResult(answer="ok")

    assert result.policy_decisions == []
    assert str(result) == "ok"


def test_invoke_result_parses_and_serializes_policy_decisions():
    result = InvokeResult(
        answer="blocked",
        policy_decisions=[
            {
                "policy_id": "guard-1",
                "policy_name": "Destructive operation guard",
                "policy_type": "intent_guard",
                "action_type": "block_intent",
                "stage": "input",
                "outcome": "blocked",
                "confidence": 0.9,
                "reasoning": "Destructive request",
            }
        ],
    )

    decision = result.policy_decisions[0]
    assert decision.policy_type == PolicyType.INTENT_GUARD
    assert decision.stage == PolicyDecisionStage.INPUT
    assert decision.outcome == PolicyDecisionOutcome.BLOCKED
    assert result.model_dump(mode="json")["policy_decisions"][0]["policy_id"] == "guard-1"
