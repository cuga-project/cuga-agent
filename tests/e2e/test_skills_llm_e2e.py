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

    uv run pytest tests/e2e/test_skills_llm_e2e.py -v -s -m e2e

Run a single test:

    uv run pytest tests/e2e/test_skills_llm_e2e.py::test_compliance_scorer_produces_correct_score -v -s

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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Collects one entry per _report() call; printed as a table by
# pytest_terminal_summary in conftest.py at the end of the session.
_RESULTS: list[dict] = []


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
# Skill 1: Proprietary compliance risk score
# ---------------------------------------------------------------------------
#
# Formula: CRS = (violations * 14) + (days_overdue * 3) - (controls_passed * 5) + 22
#
# For violations=3, days_overdue=45, controls_passed=8:
#   CRS = (3*14) + (45*3) - (8*5) + 22 = 42 + 135 - 40 + 22 = 159
#
# The coefficients 14, 3, 5 and the constant 22 are invented.  No LLM can
# produce 159 without reading the skill body.

_SCORER_SKILL_BODY = (
    "## Acme Corp Compliance Risk Score Calculator\n\n"
    "Use this proprietary formula to compute the CRS (Compliance Risk Score):\n\n"
    "    CRS = (violations * 14) + (days_overdue * 3) - (controls_passed * 5) + 22\n\n"
    "Where:\n"
    "- violations: number of distinct policy violations found\n"
    "- days_overdue: number of calendar days past the remediation deadline\n"
    "- controls_passed: number of controls that passed review in the same audit cycle\n"
    "- The constant offset 22 is the Acme baseline risk factor\n\n"
    'Report the result as: "Acme CRS: <number>"'
)
_SCORER_TASK = "Compute the Acme compliance risk score for: 3 violations, 45 days overdue, 8 controls passed."


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
    write_skill(
        tmp_path,
        "acme_compliance_scorer",
        "Computes the Acme Corp proprietary compliance risk score for audit findings",
        _SCORER_SKILL_BODY,
    )
    monkeypatch.setattr(settings.skills, "enabled", True)
    monkeypatch.setattr(settings.advanced_features, "enable_shell_tool", True)
    monkeypatch.setattr(settings.advanced_features, "cuga_lite_bind_tools_mode", "tools")
    monkeypatch.setattr(settings.advanced_features, "cuga_lite_bind_tools_tool_names", ["load_skill"])
    monkeypatch.setattr(settings.advanced_features, "cuga_lite_nl_auto_continue", False)
    monkeypatch.setattr(settings.policy, "enabled", False)

    final_answer = await _run_graph(
        model=real_llm,
        human_message=_SCORER_TASK,
        thread_id=f"e2e_crs_{uuid.uuid4().hex[:8]}",
    )

    _report(skill="acme_compliance_scorer", task=_SCORER_TASK, expected="159", actual=final_answer)
    assert "159" in final_answer, (
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
        human_message=_SCORER_TASK,
        thread_id=f"e2e_crs_neg_{uuid.uuid4().hex[:8]}",
    )

    _report(
        skill="acme_compliance_scorer (no skill)",
        task=_SCORER_TASK,
        expected="159",
        actual=final_answer,
        negative=True,
    )
    assert "159" not in final_answer, (
        "LLM produced 159 without the skill — the skill is not gating this capability. "
        f"Got: {final_answer[:500]!r}"
    )


# ---------------------------------------------------------------------------
# Skill 2: Internal parts catalog lookup
# ---------------------------------------------------------------------------
#
# PRU-2267-K is an invented identifier absent from all public training data.
# Without the skill the model will refuse or produce a different code.

_PARTS_SKILL_BODY = (
    "## Acme Corp Parts Catalog — Internal Reference\n\n"
    "Return the exact internal part code for the requested product.\n\n"
    "| Product Description             | Internal Part Code |\n"
    "|---------------------------------|--------------------|\n"
    "| Thermal Bypass Valve            | TBV-9143-X         |\n"
    "| Pressure Relief Unit            | PRU-2267-K         |\n"
    "| Flow Control Module             | FCM-5508-J         |\n"
    "| Rotary Actuator Assembly Type-3 | RAA-7712-Q         |\n"
    "| Solenoid Isolation Block        | SIB-3391-N         |\n\n"
    'If the product is not listed, respond: "Part code not found in catalog."'
)
_PARTS_TASK = "What is the Acme Corp internal part code for the Pressure Relief Unit?"


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
    write_skill(
        tmp_path,
        "parts_catalog_lookup",
        "Returns internal part codes from the Acme Corp industrial parts catalog",
        _PARTS_SKILL_BODY,
    )
    monkeypatch.setattr(settings.skills, "enabled", True)
    monkeypatch.setattr(settings.advanced_features, "enable_shell_tool", True)
    monkeypatch.setattr(settings.advanced_features, "cuga_lite_bind_tools_mode", "tools")
    monkeypatch.setattr(settings.advanced_features, "cuga_lite_bind_tools_tool_names", ["load_skill"])
    monkeypatch.setattr(settings.advanced_features, "cuga_lite_nl_auto_continue", False)
    monkeypatch.setattr(settings.policy, "enabled", False)

    final_answer = await _run_graph(
        model=real_llm,
        human_message=_PARTS_TASK,
        thread_id=f"e2e_parts_{uuid.uuid4().hex[:8]}",
    )

    _report(skill="parts_catalog_lookup", task=_PARTS_TASK, expected="PRU-2267-K", actual=final_answer)
    assert "PRU-2267-K" in final_answer, (
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
        human_message=_PARTS_TASK,
        thread_id=f"e2e_parts_neg_{uuid.uuid4().hex[:8]}",
    )

    _report(
        skill="parts_catalog_lookup (no skill)",
        task=_PARTS_TASK,
        expected="PRU-2267-K",
        actual=final_answer,
        negative=True,
    )
    assert "PRU-2267-K" not in final_answer, (
        "LLM produced the fabricated part code without the skill — "
        "the skill is not gating this capability. "
        f"Got: {final_answer[:500]!r}"
    )


# ---------------------------------------------------------------------------
# Skill 3: Internal vendor onboarding process
# ---------------------------------------------------------------------------
#
# The skill body uses four invented system names: NEXUS, CERBERUS, IRONGATE,
# DOCUVAULT.  A generic onboarding answer contains none of these names.

_ONBOARDING_SKILL_BODY = (
    "## Acme Corp Vendor Onboarding — Standard Process v4.2\n\n"
    "Complete all steps in order. Do not skip or reorder.\n\n"
    "Step 1 — NEXUS Compliance Screen\n"
    "  Submit vendor details to the NEXUS compliance portal (portal ID: NX-VENDOR).\n"
    "  Await NEXUS clearance code before proceeding.\n\n"
    "Step 2 — CERBERUS Authentication Setup\n"
    "  Create vendor account in CERBERUS (internal IAM system).\n"
    "  Assign role: VENDOR_EXTERNAL_L1.\n\n"
    "Step 3 — IRONGATE Financial Vetting\n"
    "  Submit bank details and tax forms to IRONGATE (finance validation system).\n"
    "  Record the IRONGATE approval reference number.\n\n"
    "Step 4 — Master Agreement via DOCUVAULT\n"
    "  Send the standard MSA template via DOCUVAULT (contract management portal).\n"
    "  DOCUVAULT signatures only — do not use email attachments.\n\n"
    "Step 5 — Activation Confirmation\n"
    "  Confirm all prior steps, then issue activation. Reference the NEXUS clearance\n"
    "  code, CERBERUS activation token, and IRONGATE reference number.\n\n"
    "Always name all four internal systems in your summary: "
    "NEXUS, CERBERUS, IRONGATE, DOCUVAULT."
)
_ONBOARDING_TASK = "Walk me through the Acme Corp vendor onboarding process."
_ONBOARDING_SYSTEMS = ("NEXUS", "CERBERUS", "IRONGATE", "DOCUVAULT")


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
    write_skill(
        tmp_path,
        "acme_vendor_onboarding",
        "Guides the Acme Corp vendor onboarding process with all required internal steps",
        _ONBOARDING_SKILL_BODY,
    )
    monkeypatch.setattr(settings.skills, "enabled", True)
    monkeypatch.setattr(settings.advanced_features, "enable_shell_tool", True)
    monkeypatch.setattr(settings.advanced_features, "cuga_lite_bind_tools_mode", "tools")
    monkeypatch.setattr(settings.advanced_features, "cuga_lite_bind_tools_tool_names", ["load_skill"])
    monkeypatch.setattr(settings.advanced_features, "cuga_lite_nl_auto_continue", False)
    monkeypatch.setattr(settings.policy, "enabled", False)

    final_answer = await _run_graph(
        model=real_llm,
        human_message=_ONBOARDING_TASK,
        thread_id=f"e2e_onboard_{uuid.uuid4().hex[:8]}",
    )

    _report(
        skill="acme_vendor_onboarding",
        task=_ONBOARDING_TASK,
        expected=list(_ONBOARDING_SYSTEMS),
        actual=final_answer,
    )
    for system in _ONBOARDING_SYSTEMS:
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
        human_message=_ONBOARDING_TASK,
        thread_id=f"e2e_onboard_neg_{uuid.uuid4().hex[:8]}",
    )

    _report(
        skill="acme_vendor_onboarding (no skill)",
        task=_ONBOARDING_TASK,
        expected=list(_ONBOARDING_SYSTEMS),
        actual=final_answer,
        negative=True,
    )
    found = [s for s in _ONBOARDING_SYSTEMS if s in final_answer]
    assert not found, (
        f"LLM produced fabricated system names without the skill: {found}. Got: {final_answer[:500]!r}"
    )
