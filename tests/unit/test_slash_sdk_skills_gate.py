"""Unit tests for the SDK-side skills gate in CugaAgent._dispatch_slash.

The SDK must mirror the server's gating (main.py _skills_effective_enabled):
when skills are disabled, a slash message is not dispatched or synthesized —
it reaches the planner unchanged.
"""

import asyncio
from pathlib import Path

import pytest

from cuga.sdk import CugaAgent

pytestmark = pytest.mark.unit


def _make_agent(enable_skills, cuga_folder: str) -> CugaAgent:
    """Bare CugaAgent carrying only the attributes _dispatch_slash reads."""
    agent = object.__new__(CugaAgent)
    agent._enable_skills = enable_skills
    agent.cuga_folder = cuga_folder
    return agent


def _write_skill(root: Path, name: str, description: str) -> None:
    skill_dir = root / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\nBody\n",
        encoding="utf-8",
    )


def test_skills_disabled_slash_message_passes_through(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _write_skill(tmp_path / ".cuga" / "skills", "gateskill", "gate test skill")

    agent = _make_agent(False, ".cuga")
    result = asyncio.run(agent._dispatch_slash("/gateskill make 3 slides", None))

    assert result is not None
    assert result.kind == "passthrough"
    # No synthesis: invoke() only injects messages when kind == "skill" and
    # injected_messages is non-empty, so the raw message reaches the planner
    # unchanged.
    assert result.injected_messages == []
    assert result.raw_input == "/gateskill make 3 slides"


def test_skills_flag_none_falls_back_to_settings_disabled(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _write_skill(tmp_path / ".cuga" / "skills", "gateskill", "gate test skill")

    from cuga.config import settings

    monkeypatch.setattr(settings.skills, "enabled", False, raising=False)

    agent = _make_agent(None, ".cuga")
    result = asyncio.run(agent._dispatch_slash("/gateskill hello", None))

    assert result is not None
    assert result.kind == "passthrough"
    assert result.injected_messages == []


def test_skills_enabled_still_dispatches_skill(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _write_skill(tmp_path / ".cuga" / "skills", "gateskill", "gate test skill")

    agent = _make_agent(True, ".cuga")
    result = asyncio.run(agent._dispatch_slash("/gateskill do it", None))

    assert result is not None
    assert result.kind == "skill"
    assert result.injected_messages
