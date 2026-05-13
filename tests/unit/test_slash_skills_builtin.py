import asyncio

from cuga.backend.skills.registry import SkillEntry, SkillRegistry
from cuga.backend.slash_commands import build_slash_registry, parse_and_dispatch


def _make_registry(entries):
    return SkillRegistry(entries)


def test_skills_builtin_registered():
    reg = build_slash_registry()
    names = {c.name for c in reg.list_commands()}
    assert "skills" in names


def test_skills_lists_entries():
    skills = _make_registry(
        [
            SkillEntry(
                name="deck",
                description="Build slide decks",
                body="…",
                source="/p/deck/SKILL.md",
                requirements=("python-pptx",),
            ),
            SkillEntry(
                name="excel",
                description="Edit spreadsheets",
                body="…",
                source="/p/excel/SKILL.md",
            ),
        ]
    )
    reg = build_slash_registry(skill_registry=skills)
    result = asyncio.run(
        parse_and_dispatch("/skills", slash_registry=reg, skill_registry=skills)
    )
    assert result.kind == "builtin"
    text = result.text or ""
    assert "deck" in text
    assert "Build slide decks" in text
    assert "python-pptx" in text
    assert "excel" in text
    assert "Edit spreadsheets" in text


def test_skills_handles_empty_registry():
    skills = _make_registry([])
    reg = build_slash_registry(skill_registry=skills)
    result = asyncio.run(
        parse_and_dispatch("/skills", slash_registry=reg, skill_registry=skills)
    )
    assert result.kind == "builtin"
    assert "No skills installed" in (result.text or "")


def test_skills_handles_missing_registry():
    reg = build_slash_registry()
    result = asyncio.run(parse_and_dispatch("/skills", slash_registry=reg))
    assert result.kind == "builtin"
    assert "disabled" in (result.text or "").lower()


def test_skills_omits_requirements_line_when_empty():
    skills = _make_registry(
        [SkillEntry(name="lean", description="Lean skill", body="…", source="/p")]
    )
    reg = build_slash_registry(skill_registry=skills)
    result = asyncio.run(
        parse_and_dispatch("/skills", slash_registry=reg, skill_registry=skills)
    )
    assert "requirements" not in (result.text or "").lower()
