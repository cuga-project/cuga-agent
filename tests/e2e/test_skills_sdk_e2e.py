"""E2E tests for skills via the CugaAgent SDK.

Tier 1 — SDK configuration:
    Verifies that CugaAgent(enable_skills=True, skills_folder=...) correctly
    routes the skills_enabled / skills_folder values into the graph configurable,
    without making any LLM calls.

Tier 3 — Real LLM via SDK:
    Runs CugaAgent(enable_skills=True) with the real configured LLM and asserts
    that skills containing proprietary data (fabricated formulas / codes) are
    loaded and applied correctly.  Paired negative controls run with
    enable_skills=False and assert the expected value is absent.

Same fabricated-data approach as test_skills_llm_e2e.py — the LLM cannot
produce the correct answer without reading the skill body.

How to run
----------
Tier 1 only (fast, no LLM):

    uv run pytest tests/e2e/test_skills_sdk_e2e.py::TestSkillsSdkConfiguration -v

Tier 3 only (real LLM):

    uv run pytest tests/e2e/test_skills_sdk_e2e.py -m e2e -v -s
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from .conftest import write_skill

if TYPE_CHECKING:
    from cuga.sdk import CugaAgent


def _normalize_hyphens(text: str) -> str:
    """Replace Unicode dash variants with ASCII hyphen for robust assertions."""
    for ch in "‐‑‒–—―−":
        text = text.replace(ch, "-")
    return text


# Collects one entry per _report() call; printed in the end-of-session summary
# by pytest_terminal_summary in conftest.py (alongside test_skills_llm_e2e results).
_RESULTS: list[dict] = []


def _report(*, skill: str, expected: str | list[str], actual: str, negative: bool = False) -> None:
    terms = expected if isinstance(expected, list) else [expected]
    passed = all(t not in actual for t in terms) if negative else all(t in actual for t in terms)
    display = repr(terms[0]) + (f" (+{len(terms) - 1} more)" if len(terms) > 1 else "")
    _RESULTS.append({"skill": skill, "expected": display, "actual": actual, "negative": negative, "passed": passed})


# ---------------------------------------------------------------------------
# Tier 1 – SDK configuration (no LLM calls)
# ---------------------------------------------------------------------------


class TestSkillsSdkConfiguration:
    """Verify that enable_skills / skills_folder are stored and forwarded correctly.

    These tests inspect the agent's internal state without invoking the graph.
    They confirm the SDK wiring before any real LLM call.
    """

    def test_enable_skills_defaults_to_none(self) -> None:
        """CugaAgent() without enable_skills stores None (auto from settings)."""
        from cuga.sdk import CugaAgent

        agent = CugaAgent(enable_knowledge=False)
        assert agent._enable_skills is None

    def test_enable_skills_true_is_stored(self) -> None:
        """CugaAgent(enable_skills=True) stores True on the agent."""
        from cuga.sdk import CugaAgent

        agent = CugaAgent(enable_skills=True, enable_knowledge=False)
        assert agent._enable_skills is True

    def test_enable_skills_false_is_stored(self) -> None:
        """CugaAgent(enable_skills=False) stores False on the agent."""
        from cuga.sdk import CugaAgent

        agent = CugaAgent(enable_skills=False, enable_knowledge=False)
        assert agent._enable_skills is False

    def test_skills_folder_is_stored(self, tmp_path: Path) -> None:
        """CugaAgent(skills_folder=...) stores the path on the agent."""
        from cuga.sdk import CugaAgent

        agent = CugaAgent(enable_skills=True, skills_folder=str(tmp_path), enable_knowledge=False)
        assert agent._skills_folder == str(tmp_path)

    @pytest.mark.asyncio
    async def test_skills_configurable_injected_into_invoke_config(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """skills_enabled and skills_folder appear in run_config after _build_run_config.

        We intercept graph.ainvoke to capture the config rather than running the graph.
        """
        from cuga.sdk import CugaAgent

        agent = CugaAgent(
            enable_skills=True,
            skills_folder=str(tmp_path),
            enable_knowledge=False,
        )

        captured: list[dict] = []

        async def fake_ainvoke(state, config=None):
            captured.append(config or {})
            return {"final_answer": "ok"}

        # Patch the compiled graph's ainvoke so we can inspect the config
        monkeypatch.setattr(agent.graph, "ainvoke", fake_ainvoke)

        await agent.invoke("test")

        assert captured, "graph.ainvoke was never called"
        cfg = captured[0].get("configurable", {})
        assert cfg.get("skills_enabled") is True
        # SDK converts workspace root → workspace/.cuga for discover_skills compatibility
        assert cfg.get("skills_folder") == str(tmp_path / ".cuga")

    @pytest.mark.asyncio
    async def test_skills_folder_with_cuga_suffix_is_not_double_suffixed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """CugaAgent(skills_folder='.../path/.cuga') must NOT become '.../path/.cuga/.cuga'.

        If a user reads the docs and passes a path that already ends in '.cuga',
        the SDK previously appended another '.cuga', silently directing discovery
        to the wrong location and producing zero skills with no error.
        """
        from cuga.sdk import CugaAgent

        already_suffixed = str(tmp_path / ".cuga")
        agent = CugaAgent(
            enable_skills=True,
            skills_folder=already_suffixed,
            enable_knowledge=False,
        )

        captured: list[dict] = []

        async def fake_ainvoke(state, config=None):
            captured.append(config or {})
            return {"final_answer": "ok"}

        monkeypatch.setattr(agent.graph, "ainvoke", fake_ainvoke)
        await agent.invoke("test")

        cfg = captured[0].get("configurable", {})
        result = cfg.get("skills_folder", "")
        assert result == already_suffixed, (
            f"SDK must not double-append '.cuga'. "
            f"Expected {already_suffixed!r}, got {result!r}"
        )
        assert not result.endswith(".cuga/.cuga"), (
            "skills_folder was double-suffixed — discovery will resolve to the wrong directory."
        )

    @pytest.mark.asyncio
    async def test_skills_folder_without_cuga_suffix_gets_cuga_appended(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """CugaAgent(skills_folder='/project') becomes '/project/.cuga' in the configurable."""
        from cuga.sdk import CugaAgent

        agent = CugaAgent(
            enable_skills=True,
            skills_folder=str(tmp_path),
            enable_knowledge=False,
        )

        captured: list[dict] = []

        async def fake_ainvoke(state, config=None):
            captured.append(config or {})
            return {"final_answer": "ok"}

        monkeypatch.setattr(agent.graph, "ainvoke", fake_ainvoke)
        await agent.invoke("test")

        cfg = captured[0].get("configurable", {})
        assert cfg.get("skills_folder") == str(tmp_path / ".cuga"), (
            "A plain workspace root should have .cuga appended by the SDK"
        )


# ---------------------------------------------------------------------------
# Tier 3 – real LLM via SDK
# ---------------------------------------------------------------------------
#
# Same three skills as test_skills_llm_e2e.py so the gating guarantees carry
# over.  The SDK surface is what changes: CugaAgent.invoke() instead of the
# raw graph helper _run_graph().

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


def _make_sdk_agent(
    *,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    enable_skills: bool,
    real_llm,
) -> "CugaAgent":
    """Create a CugaAgent configured for skill e2e tests.

    monkeypatch.chdir(tmp_path) so that relative paths inside the graph
    resolve to the test's temporary directory.
    """
    from cuga.config import settings
    from cuga.sdk import CugaAgent

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(settings.advanced_features, "enable_shell_tool", True)
    monkeypatch.setattr(settings.advanced_features, "cuga_lite_bind_tools_mode", "tools")
    monkeypatch.setattr(settings.advanced_features, "cuga_lite_bind_tools_tool_names", ["load_skill"])
    monkeypatch.setattr(settings.advanced_features, "cuga_lite_nl_auto_continue", False)
    monkeypatch.setattr(settings.policy, "enabled", False)

    return CugaAgent(
        model=real_llm,
        enable_skills=enable_skills,
        skills_folder=str(tmp_path),
        enable_knowledge=False,
    )


@pytest.mark.asyncio
@pytest.mark.e2e
async def test_sdk_compliance_scorer_produces_correct_score(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    real_llm,
) -> None:
    """SDK: LLM computes the proprietary Acme CRS formula (159) via skills.

    Expected: "159" in result.answer  (3*14 + 45*3 - 8*5 + 22 = 159).
    """
    write_skill(
        tmp_path,
        "acme_compliance_scorer",
        "Computes the Acme Corp proprietary compliance risk score for audit findings",
        _SCORER_SKILL_BODY,
    )
    agent = _make_sdk_agent(
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        enable_skills=True,
        real_llm=real_llm,
    )

    result = await agent.invoke(
        _SCORER_TASK,
        thread_id=f"sdk_crs_{uuid.uuid4().hex[:8]}",
    )

    print(f"\n[sdk_crs] answer: {result.answer[:400]}")
    _report(skill="sdk/acme_compliance_scorer", expected="159", actual=result.answer)
    assert "159" in result.answer, (
        f"Expected CRS=159 in SDK answer (3*14 + 45*3 - 8*5 + 22 = 159). Got: {result.answer[:500]!r}"
    )


@pytest.mark.asyncio
@pytest.mark.e2e
async def test_sdk_compliance_scorer_cannot_produce_correct_score_without_skill(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    real_llm,
) -> None:
    """SDK negative control: 159 is absent when enable_skills=False."""
    agent = _make_sdk_agent(
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        enable_skills=False,
        real_llm=real_llm,
    )

    result = await agent.invoke(
        _SCORER_TASK,
        thread_id=f"sdk_crs_neg_{uuid.uuid4().hex[:8]}",
    )

    print(f"\n[sdk_crs_neg] answer: {result.answer[:400]}")
    _report(skill="sdk/acme_compliance_scorer (no skill)", expected="159", actual=result.answer, negative=True)
    assert "159" not in result.answer, (
        "LLM produced 159 without the skill via SDK — skill is not gating this capability. "
        f"Got: {result.answer[:500]!r}"
    )


@pytest.mark.asyncio
@pytest.mark.e2e
async def test_sdk_parts_catalog_returns_internal_code(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    real_llm,
) -> None:
    """SDK: LLM returns fabricated internal code PRU-2267-K from the skill body."""
    write_skill(
        tmp_path,
        "parts_catalog_lookup",
        "Returns internal part codes from the Acme Corp industrial parts catalog",
        _PARTS_SKILL_BODY,
    )
    agent = _make_sdk_agent(
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        enable_skills=True,
        real_llm=real_llm,
    )

    result = await agent.invoke(
        _PARTS_TASK,
        thread_id=f"sdk_parts_{uuid.uuid4().hex[:8]}",
    )

    print(f"\n[sdk_parts] answer: {result.answer[:400]}")
    normalized = _normalize_hyphens(result.answer)
    _report(skill="sdk/parts_catalog_lookup", expected="PRU-2267-K", actual=normalized)
    assert "PRU-2267-K" in normalized, (
        f"Expected part code 'PRU-2267-K' in SDK answer. Got: {result.answer[:500]!r}"
    )


@pytest.mark.asyncio
@pytest.mark.e2e
async def test_sdk_parts_catalog_cannot_return_code_without_skill(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    real_llm,
) -> None:
    """SDK negative control: PRU-2267-K absent when enable_skills=False."""
    agent = _make_sdk_agent(
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        enable_skills=False,
        real_llm=real_llm,
    )

    result = await agent.invoke(
        _PARTS_TASK,
        thread_id=f"sdk_parts_neg_{uuid.uuid4().hex[:8]}",
    )

    print(f"\n[sdk_parts_neg] answer: {result.answer[:400]}")
    normalized = _normalize_hyphens(result.answer)
    _report(skill="sdk/parts_catalog_lookup (no skill)", expected="PRU-2267-K", actual=normalized, negative=True)
    assert "PRU-2267-K" not in normalized, (
        f"LLM produced the fabricated part code via SDK without the skill. Got: {result.answer[:500]!r}"
    )


@pytest.mark.asyncio
@pytest.mark.e2e
async def test_sdk_vendor_onboarding_uses_internal_system_names(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    real_llm,
) -> None:
    """SDK: LLM produces onboarding guide naming all four fabricated internal systems."""
    write_skill(
        tmp_path,
        "acme_vendor_onboarding",
        "Guides the Acme Corp vendor onboarding process with all required internal steps",
        _ONBOARDING_SKILL_BODY,
    )
    agent = _make_sdk_agent(
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        enable_skills=True,
        real_llm=real_llm,
    )

    result = await agent.invoke(
        _ONBOARDING_TASK,
        thread_id=f"sdk_onboard_{uuid.uuid4().hex[:8]}",
    )

    print(f"\n[sdk_onboard] answer: {result.answer[:400]}")
    _report(skill="sdk/acme_vendor_onboarding", expected=list(_ONBOARDING_SYSTEMS), actual=result.answer)
    for system in _ONBOARDING_SYSTEMS:
        assert system in result.answer, (
            f"Expected internal system name '{system}' in SDK answer. Got: {result.answer[:500]!r}"
        )


@pytest.mark.asyncio
@pytest.mark.e2e
async def test_sdk_vendor_onboarding_lacks_internal_names_without_skill(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    real_llm,
) -> None:
    """SDK negative control: fabricated system names absent when enable_skills=False."""
    agent = _make_sdk_agent(
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        enable_skills=False,
        real_llm=real_llm,
    )

    result = await agent.invoke(
        _ONBOARDING_TASK,
        thread_id=f"sdk_onboard_neg_{uuid.uuid4().hex[:8]}",
    )

    print(f"\n[sdk_onboard_neg] answer: {result.answer[:400]}")
    found = [s for s in _ONBOARDING_SYSTEMS if s in result.answer]
    _report(skill="sdk/acme_vendor_onboarding (no skill)", expected=list(_ONBOARDING_SYSTEMS), actual=result.answer, negative=True)
    assert not found, (
        f"LLM produced fabricated system names without the skill via SDK: {found}. "
        f"Got: {result.answer[:500]!r}"
    )
