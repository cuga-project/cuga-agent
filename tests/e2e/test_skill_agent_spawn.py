"""E2E tests for skill-based sub-agent spawning.

Verifies the full path:
  SKILL.md (agents: key) → SkillEntry.agent_descriptors
  → spawn_agent tool available to parent agent
  → CugaAgent (parent) → prime_factorizer / modular_solver sub-agents

This is the skill-based counterpart to test_agent_spawn_number_theory.py.
The difference: agent_spawn.enabled remains False — spawn tools are activated
exclusively through the skill's agents: declaration.

Run with:
  uv run pytest tests/e2e/test_skill_agent_spawn.py -v -m e2e

Expected answers
  Query 1: φ(720720) = 138240
  Query 2: x = 808 (CRT), 9699690 = 2·3·5·7·11·13·17·19, 808 ∤ 9699690
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from tests.e2e.test_agent_spawn_number_theory import _contains_number

FIXTURE_SKILLS_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "skills"
# The fixture SKILL.md uses relative paths to point at the fixture agents directory
# (../../agents/prime_factorizer etc.), so discovery must run from the right cwd.
FIXTURE_SKILLS_BASE = str(FIXTURE_SKILLS_DIR.parent)


@pytest.fixture()
def skill_number_theory_agent(monkeypatch):
    """CugaAgent with skills enabled and number_theory fixture skill loaded.

    agent_spawn.enabled stays False — the only source of spawn tools is the
    number_theory SKILL.md's ``agents:`` declaration.
    """
    monkeypatch.setenv("CUGA_FOLDER", FIXTURE_SKILLS_BASE)
    monkeypatch.setattr("cuga.config.settings.skills.enabled", True, raising=False)
    # agent_spawn global directory scanning stays off — skills provide the agents
    monkeypatch.setattr("cuga.config.settings.agent_spawn.enabled", False, raising=False)

    # The fixture SKILL.md uses ../../agents/... paths relative to itself.
    # Changing cwd to the fixture base ensures the relative path resolution works.
    original_cwd = os.getcwd()
    monkeypatch.chdir(FIXTURE_SKILLS_BASE)

    from cuga.backend.skills.loader import clear_skills_cache
    clear_skills_cache()

    from cuga.sdk import CugaAgent

    yield CugaAgent()

    clear_skills_cache()
    os.chdir(original_cwd)


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_skill_spawn_euler_totient(skill_number_theory_agent):
    """Skill-based spawning — prime_factorizer only.

    The parent agent loads the number_theory skill, which declares prime_factorizer.
    spawn_agent becomes available through skill discovery (not agent_spawn.enabled).
    Expected: φ(720720) = 138240
    """
    result = await skill_number_theory_agent.invoke(
        "What is the Euler totient of 720720?",
    )

    assert _contains_number(result.answer, 138240), (
        f"Expected Euler totient 138240 in answer, got:\n{result.answer}"
    )


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_skill_spawn_factorize_and_crt(skill_number_theory_agent):
    """Skill-based spawning — prime_factorizer + modular_solver together.

    Expected:
      - x = 808 (CRT solution)
      - 9699690 = 2 × 3 × 5 × 7 × 11 × 13 × 17 × 19
      - 808 does NOT divide 9699690
    """
    query = (
        "Factorize 9699690, then find the smallest positive x that leaves "
        "remainder 3 mod 7, remainder 5 mod 11, and remainder 2 mod 13. "
        "Is that x a divisor of 9699690?"
    )
    result = await skill_number_theory_agent.invoke(query)
    answer = result.answer

    assert _contains_number(answer, 808), (
        f"Expected CRT solution 808 in answer, got:\n{answer}"
    )

    for prime in ("2", "3", "5", "7", "11", "13", "17", "19"):
        assert prime in answer, (
            f"Expected prime factor {prime} of 9699690 in answer, got:\n{answer}"
        )

    import re as _re
    answer_plain = _re.sub(r"\*+|_+", "", answer).lower()
    assert any(
        phrase in answer_plain
        for phrase in ("does not divide", "not a divisor", "not divide", "is not a divisor")
    ), f"Expected answer to state 808 does not divide 9699690, got:\n{answer}"
