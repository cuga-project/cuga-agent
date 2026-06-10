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

    uv run pytest tests/e2e/skills/test_skills_sdk_e2e.py::TestSkillsSdkConfiguration -v

Tier 3 only (real LLM):

    uv run pytest tests/e2e/skills/test_skills_sdk_e2e.py -m e2e -v -s
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from .conftest import write_skill
from .skills_artifact import COMPLIANCE_SCORER, PARTS_CATALOG, VENDOR_ONBOARDING

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
    _RESULTS.append(
        {"skill": skill, "expected": display, "actual": actual, "negative": negative, "passed": passed}
    )


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
            f"SDK must not double-append '.cuga'. Expected {already_suffixed!r}, got {result!r}"
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
# Tier 3 - real LLM via SDK
# ---------------------------------------------------------------------------
# Same three skills as test_skills_llm_e2e.py — imported from skills_artifact.


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
    write_skill(tmp_path, COMPLIANCE_SCORER.name, COMPLIANCE_SCORER.description, COMPLIANCE_SCORER.body)
    agent = _make_sdk_agent(
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        enable_skills=True,
        real_llm=real_llm,
    )

    result = await agent.invoke(
        COMPLIANCE_SCORER.task,
        thread_id=f"sdk_crs_{uuid.uuid4().hex[:8]}",
    )

    print(f"\n[sdk_crs] answer: {result.answer[:400]}")
    _report(skill=f"sdk/{COMPLIANCE_SCORER.name}", expected=COMPLIANCE_SCORER.expected, actual=result.answer)
    assert COMPLIANCE_SCORER.expected in result.answer, (
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
        COMPLIANCE_SCORER.task,
        thread_id=f"sdk_crs_neg_{uuid.uuid4().hex[:8]}",
    )

    print(f"\n[sdk_crs_neg] answer: {result.answer[:400]}")
    _report(
        skill=f"sdk/{COMPLIANCE_SCORER.name} (no skill)",
        expected=COMPLIANCE_SCORER.expected,
        actual=result.answer,
        negative=True,
    )
    assert COMPLIANCE_SCORER.expected not in result.answer, (
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
    write_skill(tmp_path, PARTS_CATALOG.name, PARTS_CATALOG.description, PARTS_CATALOG.body)
    agent = _make_sdk_agent(
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        enable_skills=True,
        real_llm=real_llm,
    )

    result = await agent.invoke(
        PARTS_CATALOG.task,
        thread_id=f"sdk_parts_{uuid.uuid4().hex[:8]}",
    )

    print(f"\n[sdk_parts] answer: {result.answer[:400]}")
    normalized = _normalize_hyphens(result.answer)
    _report(skill=f"sdk/{PARTS_CATALOG.name}", expected=PARTS_CATALOG.expected, actual=normalized)
    assert PARTS_CATALOG.expected in normalized, (
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
        PARTS_CATALOG.task,
        thread_id=f"sdk_parts_neg_{uuid.uuid4().hex[:8]}",
    )

    print(f"\n[sdk_parts_neg] answer: {result.answer[:400]}")
    normalized = _normalize_hyphens(result.answer)
    _report(
        skill=f"sdk/{PARTS_CATALOG.name} (no skill)",
        expected=PARTS_CATALOG.expected,
        actual=normalized,
        negative=True,
    )
    assert PARTS_CATALOG.expected not in normalized, (
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
    write_skill(tmp_path, VENDOR_ONBOARDING.name, VENDOR_ONBOARDING.description, VENDOR_ONBOARDING.body)
    agent = _make_sdk_agent(
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        enable_skills=True,
        real_llm=real_llm,
    )

    result = await agent.invoke(
        VENDOR_ONBOARDING.task,
        thread_id=f"sdk_onboard_{uuid.uuid4().hex[:8]}",
    )

    print(f"\n[sdk_onboard] answer: {result.answer[:400]}")
    _report(
        skill=f"sdk/{VENDOR_ONBOARDING.name}", expected=list(VENDOR_ONBOARDING.expected), actual=result.answer
    )
    for system in VENDOR_ONBOARDING.expected:
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
        VENDOR_ONBOARDING.task,
        thread_id=f"sdk_onboard_neg_{uuid.uuid4().hex[:8]}",
    )

    print(f"\n[sdk_onboard_neg] answer: {result.answer[:400]}")
    found = [s for s in VENDOR_ONBOARDING.expected if s in result.answer]
    _report(
        skill=f"sdk/{VENDOR_ONBOARDING.name} (no skill)",
        expected=list(VENDOR_ONBOARDING.expected),
        actual=result.answer,
        negative=True,
    )
    assert not found, (
        f"LLM produced fabricated system names without the skill via SDK: {found}. "
        f"Got: {result.answer[:500]!r}"
    )
