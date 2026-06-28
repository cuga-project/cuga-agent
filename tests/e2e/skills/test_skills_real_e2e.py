"""E2E tests using real skills fetched from github.com/vercel-labs/agent-skills.

Unlike the fabricated-data tests in test_skills_sdk_e2e.py, the skill content here
is real and publicly available, so the LLM may have encountered similar material
during training.  Content verification is therefore a "soft" signal: it increases
confidence that the skill was loaded and applied, but is not a hard gate the way
fabricated secret values are.

The primary value of these tests is integration coverage:
  - Skill discovery from a real, unmodified SKILL.md (not hand-crafted frontmatter).
  - The load_skill tool is invoked by the agent against that SKILL.md.
  - The agent's answer is richer and more specific than it would be without the skill.

Verification strategy
---------------------
We assert that rule identifiers specific to the Vercel taxonomy appear in the answer.
Identifiers like ``async-cheap-condition-before-await`` or ``server-no-shared-module-state``
are long and precise enough that they are unlikely to appear without reading the skill.

Note on negative controls
--------------------------
Negative controls (enable_skills=False) are omitted here because the LLM may reproduce
Vercel rule names from training data, making them unreliable for public skills.
Use test_skills_sdk_e2e.py for hard-gated negative controls with fabricated data.

How to run
----------
    uv run pytest tests/e2e/skills/test_skills_real_e2e.py -m e2e -v -s

Skip reason when GitHub is unreachable:
    Tests are skipped automatically if the raw.githubusercontent.com download fails.
"""

from __future__ import annotations

import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from cuga.sdk import CugaAgent

_GITHUB_RAW = "https://raw.githubusercontent.com/vercel-labs/agent-skills/main/skills"


def _fetch_skill_md(skill_path: str) -> str:
    """Download SKILL.md from vercel-labs/agent-skills.  Skip if unreachable."""
    url = f"{_GITHUB_RAW}/{skill_path}/SKILL.md"
    try:
        with urllib.request.urlopen(url, timeout=15) as resp:  # noqa: S310
            return resp.read().decode("utf-8")
    except (urllib.error.URLError, OSError) as exc:
        pytest.skip(f"GitHub unreachable ({exc}); skipping real-skill test")


def _install_skill(tmp_path: Path, dir_name: str, content: str) -> None:
    """Write a SKILL.md to the skills directory under tmp_path."""
    skill_dir = tmp_path / ".cuga" / "skills" / dir_name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(content, encoding="utf-8")


def _make_sdk_agent(
    *,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    enable_skills: bool,
    real_llm,
) -> "CugaAgent":
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


# ---------------------------------------------------------------------------
# Tier 3 – real LLM against real skills from vercel-labs/agent-skills
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.e2e
async def test_react_best_practices_skill_loaded_and_applied(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    real_llm,
) -> None:
    """Agent uses the real Vercel react-best-practices skill to answer a rule-specific query.

    The Vercel taxonomy uses identifiers like ``async-cheap-condition-before-await``
    that are long and precise enough to be unlikely in a generic React answer.
    """
    content = _fetch_skill_md("react-best-practices")
    _install_skill(tmp_path, "react-best-practices", content)

    agent = _make_sdk_agent(
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        enable_skills=True,
        real_llm=real_llm,
    )

    task = (
        "Using Vercel's react-best-practices skill, list the specific rule identifiers "
        "for all CRITICAL priority rules that prevent async waterfalls in React. "
        "Include the exact rule names as they appear in the skill."
    )

    result = await agent.invoke(task, thread_id=f"react_real_{uuid.uuid4().hex[:8]}")
    print(f"\n[react_real] answer: {result.answer[:600]}")

    # Rule IDs specific to the Vercel taxonomy — unlikely without reading the skill.
    expected_ids = [
        "async-cheap-condition-before-await",
        "async-defer-await",
        "async-parallel",
    ]
    found = [rid for rid in expected_ids if rid in result.answer]
    assert found, (
        f"Expected at least one Vercel rule identifier in answer but found none of {expected_ids}. "
        f"Got: {result.answer[:600]!r}"
    )


@pytest.mark.asyncio
@pytest.mark.e2e
async def test_react_best_practices_server_rules_present(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    real_llm,
) -> None:
    """Agent applies the server-side rules section from the real Vercel skill.

    ``server-no-shared-module-state`` is a particularly precise identifier — it would
    be unusual for a generic React answer to reproduce this exact string.
    """
    content = _fetch_skill_md("react-best-practices")
    _install_skill(tmp_path, "react-best-practices", content)

    agent = _make_sdk_agent(
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        enable_skills=True,
        real_llm=real_llm,
    )

    task = (
        "Using Vercel's react-best-practices skill, what are the HIGH priority "
        "server-side performance rules? List their exact rule identifiers."
    )

    result = await agent.invoke(task, thread_id=f"react_server_{uuid.uuid4().hex[:8]}")
    print(f"\n[react_server] answer: {result.answer[:600]}")

    server_ids = [
        "server-no-shared-module-state",
        "server-cache-react",
        "server-parallel-fetching",
    ]
    found = [rid for rid in server_ids if rid in result.answer]
    assert found, (
        f"Expected at least one server-side rule ID in answer but found none of {server_ids}. "
        f"Got: {result.answer[:600]!r}"
    )


@pytest.mark.asyncio
@pytest.mark.e2e
async def test_deploy_to_vercel_skill_guides_deployment_workflow(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    real_llm,
) -> None:
    """Agent follows the deploy-to-vercel skill's opinionated preview-first workflow.

    The skill explicitly mandates:
      - Always deploy as preview unless production is explicitly requested.
      - Run four specific state-gathering checks before choosing a deploy method.
    These are specific enough to distinguish skill-guided from generic answers.
    """
    content = _fetch_skill_md("deploy-to-vercel")
    _install_skill(tmp_path, "deploy-to-vercel", content)

    agent = _make_sdk_agent(
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        enable_skills=True,
        real_llm=real_llm,
    )

    task = (
        "Using the deploy-to-vercel skill, walk me through the correct process "
        "for deploying a project to Vercel. What do I check first, and should I "
        "deploy to production or preview by default?"
    )

    result = await agent.invoke(task, thread_id=f"deploy_real_{uuid.uuid4().hex[:8]}")
    print(f"\n[deploy_real] answer: {result.answer[:600]}")

    answer_lower = result.answer.lower()

    # The skill explicitly says "Always deploy as preview (not production)".
    assert "preview" in answer_lower, (
        f"Expected 'preview' in answer (skill mandates preview-first). Got: {result.answer[:500]!r}"
    )

    # The skill mandates four pre-deploy checks; at least one of the specific commands
    # or file paths should appear.
    deployment_signals = ["vercel whoami", ".vercel/project.json", "vercel teams list", ".vercel/repo.json"]
    found = [s for s in deployment_signals if s in result.answer]
    assert found, (
        f"Expected at least one deployment-state check from the skill ({deployment_signals}). "
        f"Got: {result.answer[:500]!r}"
    )
