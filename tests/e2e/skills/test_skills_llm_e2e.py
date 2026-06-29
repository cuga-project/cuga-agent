"""E2E tests for the skills component — Tier 3 (real LLM).

These tests run the full CugaLiteGraph with the project's configured LLM (oss120b
via the rits platform, or whatever AGENT_SETTING_CONFIG points to) against skills
that contain proprietary information the LLM cannot know from training: custom
formula coefficients, fabricated internal codes, invented system names.

The task for each test is designed so that only a model that has read the skill
body can produce the correct, verifiable output.  Paired negative controls run
the same task with skills disabled and assert the expected value is absent,
confirming the skill is the genuine gating factor.

The model and credentials are loaded from the same .env + AGENT_SETTING_CONFIG
that cuga uses in production — no test-specific keys or model overrides.  If
credentials are missing the test fails (not skips) so the gap is visible in CI.

How to run
----------
Run all Tier 3 tests:

    uv run pytest tests/e2e/skills/test_skills_llm_e2e.py -v -s -m e2e

Run a single test:

    uv run pytest tests/e2e/skills/test_skills_llm_e2e.py::test_compliance_scorer_produces_correct_score -v -s

The -s flag is required to see the expected/actual output printed by each test.

Graph execution flow (bind_tools mode, two LLM turns)
------------------------------------------------------
  Turn 1  — LLM receives the <available_skills> block (name + description) and
             the load_skill function schema via bind_tools.  It issues a native
             tool call: load_skill(name="<skill_name>").
  Sandbox — _extract_code_from_response_tool_calls converts the tool call to
             Python; code_executor forces local mode for skills; the skill body
             is returned as execution output.
  Turn 2  — LLM receives the skill body, follows its instructions, and produces
             a final NL answer.  nl_auto_continue=False routes to END.
  Result  — ainvoke returns a dict; result["final_answer"] holds that answer.

Required patches (positive tests)
----------------------------------
  settings.skills.enabled = True
  CUGA_FOLDER = str(tmp_path / ".cuga")
  settings.advanced_features.enable_shell_tool = True
      (skills block cleared at prompt_utils.py:539-541 when False)
  settings.advanced_features.cuga_lite_bind_tools_mode = "tools"
  settings.advanced_features.cuga_lite_bind_tools_tool_names = ["load_skill"]
  settings.advanced_features.cuga_lite_nl_auto_continue = False
  settings.policy.enabled = False
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from langchain_core.messages import HumanMessage

from .conftest import MinimalToolProvider, write_skill
from .skills_artifact import COMPLIANCE_SCORER, PARTS_CATALOG, VENDOR_ONBOARDING


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Collects one entry per _report() call; printed as a table by
# pytest_terminal_summary in conftest.py at the end of the session.
_RESULTS: list[dict] = []


def _normalize_hyphens(text: str) -> str:
    """Replace Unicode dash variants with ASCII hyphen for robust assertions."""
    for ch in ("\u2010", "\u2011", "\u2012", "\u2013", "\u2014", "\u2015", "\u2212"):
        text = text.replace(ch, "-")
    return text


def _report(
    *,
    skill: str,
    task: str,
    expected: str | list[str],
    actual: str,
    negative: bool = False,
) -> None:
    """Print an expected-vs-actual summary (visible with pytest -s) and
    append the result to _RESULTS for the end-of-session summary table.

    expected may be a single string or a list of strings (all must be present).
    Call this before the assertion so the output is visible for both
    passing and failing tests.
    """
    terms = expected if isinstance(expected, list) else [expected]
    if negative:
        passed = all(t not in actual for t in terms)
    else:
        passed = all(t in actual for t in terms)

    display = repr(terms[0]) + (f" (+{len(terms) - 1} more)" if len(terms) > 1 else "")
    _RESULTS.append(
        {
            "skill": skill,
            "task": task,
            "expected": display,
            "actual": actual,
            "negative": negative,
            "passed": passed,
        }
    )
    width = 64
    verb = "NOT in" if negative else "in"
    check = f"{display} {verb} response"
    print(f"\n{'─' * width}")
    print(f"  skill    : {skill}")
    print(f"  task     : {task[:70]}{'…' if len(task) > 70 else ''}")
    print(f"  expected : {check}")
    print(f"  actual   :\n    {actual[:400]}{'…' if len(actual) > 400 else ''}")
    print(f"{'─' * width}")


async def _run_graph(model, human_message: str, thread_id: str) -> str:
    """Compile and invoke CugaLiteGraph; return the final NL answer.

    Caller must monkeypatch cwd and CUGA_FOLDER before calling so that
    discover_skills() resolves to the test's tmp_path.
    """
    from cuga.backend.cuga_graph.nodes.cuga_lite.cuga_lite_graph import (
        CugaLiteState,
        create_cuga_lite_graph,
    )

    graph = create_cuga_lite_graph(
        model=model,
        tool_provider=MinimalToolProvider(),
        apps_list=[],
    ).compile()

    state = CugaLiteState(
        chat_messages=[HumanMessage(content=human_message)],
        thread_id=thread_id,
    )
    config = {
        "configurable": {
            "thread_id": thread_id,
            "apps_list": [],
            "cuga_lite_max_steps": 6,
        }
    }
    result = await graph.ainvoke(state, config=config)
    final_answer = result.get("final_answer", "")
    if not final_answer:
        for msg in reversed(result.get("chat_messages", [])):
            if getattr(msg, "type", None) == "ai" and getattr(msg, "content", ""):
                final_answer = msg.content
                break
    return final_answer


# ---------------------------------------------------------------------------
# Skill 1: Proprietary compliance risk score (CRS = 159 for the test inputs)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.e2e
async def test_compliance_scorer_produces_correct_score(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    real_llm,
) -> None:
    """LLM computes the proprietary Acme CRS formula and returns 159.

    Setup:
      - Writes acme_compliance_scorer SKILL.md under tmp_path/.agents/skills/.
      - Enables skills + bind_tools so the LLM can call load_skill natively.

    Expected result:
      - "159" appears in final_answer (3*14 + 45*3 - 8*5 + 22 = 159).

    Why the LLM cannot produce this without the skill:
      The coefficients 14, 3, 5 and the constant offset 22 are fabricated.
      Without the skill body the model has no basis for these values.
    """
    from cuga.config import settings

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CUGA_FOLDER", str(tmp_path / ".cuga"))
    write_skill(tmp_path, COMPLIANCE_SCORER.name, COMPLIANCE_SCORER.description, COMPLIANCE_SCORER.body)
    monkeypatch.setattr(settings.skills, "enabled", True)
    monkeypatch.setattr(settings.advanced_features, "enable_shell_tool", True)
    monkeypatch.setattr(settings.advanced_features, "cuga_lite_bind_tools_mode", "tools")
    monkeypatch.setattr(settings.advanced_features, "cuga_lite_bind_tools_tool_names", ["load_skill"])
    monkeypatch.setattr(settings.advanced_features, "cuga_lite_nl_auto_continue", False)
    monkeypatch.setattr(settings.policy, "enabled", False)

    final_answer = await _run_graph(
        model=real_llm,
        human_message=COMPLIANCE_SCORER.task,
        thread_id=f"e2e_crs_{uuid.uuid4().hex[:8]}",
    )

    _report(
        skill=COMPLIANCE_SCORER.name,
        task=COMPLIANCE_SCORER.task,
        expected=COMPLIANCE_SCORER.expected,
        actual=final_answer,
    )
    assert COMPLIANCE_SCORER.expected in final_answer, (
        f"Expected CRS=159 in final answer (3*14 + 45*3 - 8*5 + 22 = 159). Got: {final_answer[:500]!r}"
    )


@pytest.mark.asyncio
@pytest.mark.e2e
async def test_compliance_scorer_cannot_produce_correct_score_without_skill(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    real_llm,
) -> None:
    """Negative control: LLM cannot produce 159 when the skill is not loaded.

    skills.enabled=False so the skill body is never delivered to the model.
    The model has no knowledge of the proprietary formula and will produce
    a different answer.
    """
    from cuga.config import settings

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CUGA_FOLDER", str(tmp_path / ".cuga"))
    monkeypatch.setattr(settings.skills, "enabled", False)
    monkeypatch.setattr(settings.advanced_features, "cuga_lite_nl_auto_continue", False)
    monkeypatch.setattr(settings.policy, "enabled", False)

    final_answer = await _run_graph(
        model=real_llm,
        human_message=COMPLIANCE_SCORER.task,
        thread_id=f"e2e_crs_neg_{uuid.uuid4().hex[:8]}",
    )

    _report(
        skill=f"{COMPLIANCE_SCORER.name} (no skill)",
        task=COMPLIANCE_SCORER.task,
        expected=COMPLIANCE_SCORER.expected,
        actual=final_answer,
        negative=True,
    )
    assert COMPLIANCE_SCORER.expected not in final_answer, (
        "LLM produced 159 without the skill — the skill is not gating this capability. "
        f"Got: {final_answer[:500]!r}"
    )


# ---------------------------------------------------------------------------
# Skill 2: Internal parts catalog lookup (PRU-2267-K is a fabricated code)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.e2e
async def test_parts_catalog_returns_internal_code(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    real_llm,
) -> None:
    """LLM returns the fabricated internal code PRU-2267-K from the skill body.

    Setup:
      - Writes parts_catalog_lookup SKILL.md with a table of fabricated codes.

    Expected result:
      - "PRU-2267-K" appears in final_answer.

    Why the LLM cannot produce this without the skill:
      PRU-2267-K is a made-up identifier absent from all public training data.
      Without the skill the model will either refuse or produce a plausible-looking
      but incorrect code.
    """
    from cuga.config import settings

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CUGA_FOLDER", str(tmp_path / ".cuga"))
    write_skill(tmp_path, PARTS_CATALOG.name, PARTS_CATALOG.description, PARTS_CATALOG.body)
    monkeypatch.setattr(settings.skills, "enabled", True)
    monkeypatch.setattr(settings.advanced_features, "enable_shell_tool", True)
    monkeypatch.setattr(settings.advanced_features, "cuga_lite_bind_tools_mode", "tools")
    monkeypatch.setattr(settings.advanced_features, "cuga_lite_bind_tools_tool_names", ["load_skill"])
    monkeypatch.setattr(settings.advanced_features, "cuga_lite_nl_auto_continue", False)
    monkeypatch.setattr(settings.policy, "enabled", False)

    final_answer = await _run_graph(
        model=real_llm,
        human_message=PARTS_CATALOG.task,
        thread_id=f"e2e_parts_{uuid.uuid4().hex[:8]}",
    )

    _report(
        skill=PARTS_CATALOG.name,
        task=PARTS_CATALOG.task,
        expected=PARTS_CATALOG.expected,
        actual=final_answer,
    )
    normalized = _normalize_hyphens(final_answer)
    assert PARTS_CATALOG.expected in normalized, (
        f"Expected part code 'PRU-2267-K' in final answer. Got: {final_answer[:500]!r}"
    )


@pytest.mark.asyncio
@pytest.mark.e2e
async def test_parts_catalog_cannot_return_code_without_skill(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    real_llm,
) -> None:
    """Negative control: LLM cannot return PRU-2267-K without the skill body."""
    from cuga.config import settings

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CUGA_FOLDER", str(tmp_path / ".cuga"))
    monkeypatch.setattr(settings.skills, "enabled", False)
    monkeypatch.setattr(settings.advanced_features, "cuga_lite_nl_auto_continue", False)
    monkeypatch.setattr(settings.policy, "enabled", False)

    final_answer = await _run_graph(
        model=real_llm,
        human_message=PARTS_CATALOG.task,
        thread_id=f"e2e_parts_neg_{uuid.uuid4().hex[:8]}",
    )

    _report(
        skill=f"{PARTS_CATALOG.name} (no skill)",
        task=PARTS_CATALOG.task,
        expected=PARTS_CATALOG.expected,
        actual=final_answer,
        negative=True,
    )
    assert PARTS_CATALOG.expected not in final_answer, (
        f"LLM produced the fabricated part code without the skill. Got: {final_answer[:500]!r}"
    )


# ---------------------------------------------------------------------------
# Skill 3: Internal vendor onboarding (NEXUS/CERBERUS/IRONGATE/DOCUVAULT are fabricated)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.e2e
async def test_vendor_onboarding_uses_internal_system_names(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    real_llm,
) -> None:
    """LLM produces an onboarding guide that names all four fabricated internal systems.

    Setup:
      - Writes acme_vendor_onboarding SKILL.md with a 5-step process using
        NEXUS, CERBERUS, IRONGATE, and DOCUVAULT as internal system names.

    Expected result:
      - All four system names appear in final_answer.

    Why the LLM cannot produce this without the skill:
      NEXUS/CERBERUS/IRONGATE/DOCUVAULT are invented names absent from any public
      training corpus.  Without the skill the model produces a generic onboarding
      process with no reference to these systems.
    """
    from cuga.config import settings

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CUGA_FOLDER", str(tmp_path / ".cuga"))
    write_skill(tmp_path, VENDOR_ONBOARDING.name, VENDOR_ONBOARDING.description, VENDOR_ONBOARDING.body)
    monkeypatch.setattr(settings.skills, "enabled", True)
    monkeypatch.setattr(settings.advanced_features, "enable_shell_tool", True)
    monkeypatch.setattr(settings.advanced_features, "cuga_lite_bind_tools_mode", "tools")
    monkeypatch.setattr(settings.advanced_features, "cuga_lite_bind_tools_tool_names", ["load_skill"])
    monkeypatch.setattr(settings.advanced_features, "cuga_lite_nl_auto_continue", False)
    monkeypatch.setattr(settings.policy, "enabled", False)

    final_answer = await _run_graph(
        model=real_llm,
        human_message=VENDOR_ONBOARDING.task,
        thread_id=f"e2e_onboard_{uuid.uuid4().hex[:8]}",
    )

    _report(
        skill=VENDOR_ONBOARDING.name,
        task=VENDOR_ONBOARDING.task,
        expected=list(VENDOR_ONBOARDING.expected),
        actual=final_answer,
    )
    for system in VENDOR_ONBOARDING.expected:
        assert system in final_answer, (
            f"Expected internal system name '{system}' in final answer. Got: {final_answer[:500]!r}"
        )


@pytest.mark.asyncio
@pytest.mark.e2e
async def test_vendor_onboarding_lacks_internal_names_without_skill(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    real_llm,
) -> None:
    """Negative control: without the skill the LLM produces generic onboarding guidance.

    None of the four fabricated system names should appear in a response that
    has no access to the skill body.
    """
    from cuga.config import settings

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CUGA_FOLDER", str(tmp_path / ".cuga"))
    monkeypatch.setattr(settings.skills, "enabled", False)
    monkeypatch.setattr(settings.advanced_features, "cuga_lite_nl_auto_continue", False)
    monkeypatch.setattr(settings.policy, "enabled", False)

    final_answer = await _run_graph(
        model=real_llm,
        human_message=VENDOR_ONBOARDING.task,
        thread_id=f"e2e_onboard_neg_{uuid.uuid4().hex[:8]}",
    )

    _report(
        skill=f"{VENDOR_ONBOARDING.name} (no skill)",
        task=VENDOR_ONBOARDING.task,
        expected=list(VENDOR_ONBOARDING.expected),
        actual=final_answer,
        negative=True,
    )
    found = [s for s in VENDOR_ONBOARDING.expected if s in final_answer]
    assert not found, (
        f"LLM produced fabricated system names without the skill: {found}. Got: {final_answer[:500]!r}"
    )
