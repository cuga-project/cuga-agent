"""E2E tests for agent spawning with the number-theory fixture agents.

These tests require a live LLM and verify the full path:
  CugaAgent (parent) → spawn_agent tool → prime_factorizer / modular_solver sub-agents.

Run with:
  uv run pytest tests/e2e/test_agent_spawn_number_theory.py -v -m e2e

Expected answers
  Query 1: φ(720720) = 138240
  Query 2: x = 808 (CRT), 9699690 = 2·3·5·7·11·13·17·19, 808 ∤ 9699690
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest


def _contains_number(text: str, n: int) -> bool:
    """Return True if the integer n appears in text regardless of digit-group separators.

    LLMs use various thousands separators: comma ("138,240"), regular space ("138 240"),
    narrow no-break space U+202F ("138 240"), or none ("138240"). Normalise by
    stripping every non-digit character between digit runs before comparing.
    """
    import re
    # Collapse any run of non-digit chars sandwiched between digits into nothing.
    normalised = re.sub(r"(?<=\d)[^\d]+(?=\d)", "", text)
    return str(n) in normalised

FIXTURE_AGENTS_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "agents"
# Parent of the agents dir — CUGA_FOLDER + agents_dir must resolve to FIXTURE_AGENTS_DIR.
FIXTURE_AGENTS_BASE = str(FIXTURE_AGENTS_DIR.parent)


@pytest.fixture()
def number_theory_agent(monkeypatch):
    """CugaAgent with agent_spawn enabled and pointed at the number-theory fixtures."""
    monkeypatch.setenv("CUGA_FOLDER", FIXTURE_AGENTS_BASE)
    monkeypatch.setattr("cuga.config.settings.agent_spawn.enabled", True, raising=False)
    monkeypatch.setattr(
        "cuga.config.settings.agent_spawn.agents_dir", "agents", raising=False
    )

    from cuga.sdk import CugaAgent

    return CugaAgent()


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_single_agent_euler_totient(number_theory_agent):
    """Query 1 – prime_factorizer only.

    "What is the Euler totient of 720720?"
    Expected: 138240

    Correct answer in the response proves the sub-agent ran end-to-end.
    (spawn_agent is not decorated with @tracked_tool so it does not appear
    in result.tool_calls; answer correctness is the e2e signal.)
    """
    result = await number_theory_agent.invoke(
        "What is the Euler totient of 720720?",
    )

    assert _contains_number(result.answer, 138240), (
        f"Expected Euler totient 138240 in answer, got:\n{result.answer}"
    )


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_both_agents_factorize_crt_divisor(number_theory_agent):
    """Query 2 – prime_factorizer + modular_solver together.

    "Factorize 9699690, then find the smallest positive x that leaves remainder 3 mod 7,
    remainder 5 mod 11, and remainder 2 mod 13. Is that x a divisor of 9699690?"

    Expected:
      - x = 808 (CRT solution) — proves modular_solver sub-agent ran
      - 9699690 = 2 × 3 × 5 × 7 × 11 × 13 × 17 × 19 — proves prime_factorizer sub-agent ran
      - 808 does NOT divide 9699690
    """
    query = (
        "Factorize 9699690, then find the smallest positive x that leaves "
        "remainder 3 mod 7, remainder 5 mod 11, and remainder 2 mod 13. "
        "Is that x a divisor of 9699690?"
    )
    result = await number_theory_agent.invoke(query)

    answer = result.answer

    assert _contains_number(answer, 808), (
        f"Expected CRT solution 808 in answer, got:\n{answer}"
    )

    # 9699690 = 2·3·5·7·11·13·17·19 — check all prime factors present
    for prime in ("2", "3", "5", "7", "11", "13", "17", "19"):
        assert prime in answer, (
            f"Expected prime factor {prime} of 9699690 in answer, got:\n{answer}"
        )

    # 808 does not divide 9699690.
    # Strip markdown emphasis markers before phrase-matching so that
    # "does *not* divide" and "**not** a divisor" both match cleanly.
    import re as _re
    answer_plain = _re.sub(r"\*+|_+", "", answer).lower()
    assert any(
        phrase in answer_plain
        for phrase in ("does not divide", "not a divisor", "not divide", "is not a divisor")
    ), f"Expected answer to state 808 does not divide 9699690, got:\n{answer}"
