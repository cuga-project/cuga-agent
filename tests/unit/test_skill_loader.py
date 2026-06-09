from pathlib import Path

from cuga.backend.skills.loader import discover_skills, get_skill_search_roots
from cuga.backend.skills.registry import SkillEntry, SkillRegistry


def _write_skill(root: Path, name: str, description: str, body: str = "Body", requirements: str = "") -> None:
    skill_dir = root / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    requirements_block = f"requirements: {requirements}\n" if requirements else ""
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n{requirements_block}---\n{body}\n",
        encoding="utf-8",
    )


def _write_agent(root: Path, name: str, description: str) -> None:
    agent_dir = root / name
    agent_dir.mkdir(parents=True, exist_ok=True)
    (agent_dir / "AGENT.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\nAgent body.\n",
        encoding="utf-8",
    )


def test_skill_search_roots_prioritize_agents_paths_with_legacy_fallbacks(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)

    roots = get_skill_search_roots(
        ".cuga",
        global_skills_root=str(tmp_path / "global_agents"),
        legacy_global_skills_root=str(tmp_path / "global_cuga"),
    )

    assert roots == [
        tmp_path / "global_cuga",
        tmp_path / "global_agents",
        tmp_path / ".cuga" / "skills",
        tmp_path / ".cuga" / ".skills",
        tmp_path / ".agents" / "skills",
    ]


def test_discover_skills_agents_paths_override_legacy_fallbacks_and_preserve_requirements(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    global_cuga = tmp_path / "global_cuga"
    global_agents = tmp_path / "global_agents"

    _write_skill(global_cuga, "shared", "legacy global")
    _write_skill(global_agents, "shared", "agents global")
    _write_skill(tmp_path / ".cuga" / "skills", "shared", "legacy local")
    _write_skill(
        tmp_path / ".agents" / "skills",
        "shared",
        "agents local",
        requirements="[python-pptx, npm:sharp]",
    )
    _write_skill(global_agents, "global_only", "only global agents")

    entries = discover_skills(
        ".cuga",
        global_skills_root=str(global_agents),
        legacy_global_skills_root=str(global_cuga),
    )
    by_name = {entry.name: entry for entry in entries}

    assert by_name["shared"].description == "agents local"
    assert by_name["shared"].requirements == ("python-pptx", "npm:sharp")
    assert by_name["global_only"].description == "only global agents"


def test_skill_agents_key_loads_agent_descriptors(tmp_path: Path) -> None:
    """agents: in SKILL.md frontmatter populates SkillEntry.agent_descriptors."""
    from cuga.backend.skills.loader import _parse_skill_file

    skill_dir = tmp_path / "myskill"
    skill_dir.mkdir()
    agents_dir = skill_dir / "agents"
    _write_agent(agents_dir, "helper_bot", "Helpful assistant")

    (skill_dir / "SKILL.md").write_text(
        "---\n"
        "name: myskill\n"
        "description: My orchestrating skill\n"
        "agents:\n"
        "  - agents/helper_bot\n"
        "---\nSkill body.\n",
        encoding="utf-8",
    )

    entry = _parse_skill_file(skill_dir / "SKILL.md")
    assert entry is not None
    assert len(entry.agent_descriptors) == 1
    assert entry.agent_descriptors[0].name == "helper_bot"
    assert entry.agent_descriptors[0].description == "Helpful assistant"


def test_skill_agents_key_missing_agent_md_is_skipped(tmp_path: Path) -> None:
    """A missing AGENT.md path in agents: is silently skipped, skill still loads."""
    from cuga.backend.skills.loader import _parse_skill_file

    skill_dir = tmp_path / "myskill"
    skill_dir.mkdir()

    (skill_dir / "SKILL.md").write_text(
        "---\n"
        "name: myskill\n"
        "description: My skill\n"
        "agents:\n"
        "  - agents/nonexistent\n"
        "---\nBody.\n",
        encoding="utf-8",
    )

    entry = _parse_skill_file(skill_dir / "SKILL.md")
    assert entry is not None
    assert entry.agent_descriptors == ()


def test_skill_agents_key_absent_gives_empty_tuple(tmp_path: Path) -> None:
    """A SKILL.md without agents: key has empty agent_descriptors."""
    from cuga.backend.skills.loader import _parse_skill_file

    skill_dir = tmp_path / "myskill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\nname: myskill\ndescription: desc\n---\nBody.\n",
        encoding="utf-8",
    )
    entry = _parse_skill_file(skill_dir / "SKILL.md")
    assert entry is not None
    assert entry.agent_descriptors == ()


def test_skill_agents_key_multiple_agents(tmp_path: Path) -> None:
    """Multiple entries in agents: all load into agent_descriptors."""
    from cuga.backend.skills.loader import _parse_skill_file

    skill_dir = tmp_path / "orchestrator"
    skill_dir.mkdir()
    agents_dir = skill_dir / "agents"
    _write_agent(agents_dir, "worker_a", "Worker A")
    _write_agent(agents_dir, "worker_b", "Worker B")

    (skill_dir / "SKILL.md").write_text(
        "---\n"
        "name: orchestrator\n"
        "description: Orchestrator skill\n"
        "agents:\n"
        "  - agents/worker_a\n"
        "  - agents/worker_b\n"
        "---\nBody.\n",
        encoding="utf-8",
    )

    entry = _parse_skill_file(skill_dir / "SKILL.md")
    assert entry is not None
    names = {d.name for d in entry.agent_descriptors}
    assert names == {"worker_a", "worker_b"}


def test_skill_agents_fixture_number_theory() -> None:
    """The bundled number_theory fixture SKILL.md loads prime_factorizer and modular_solver."""
    from pathlib import Path as _Path
    from cuga.backend.skills.loader import _parse_skill_file

    skill_md = _Path(__file__).resolve().parents[1] / "fixtures" / "skills" / "number_theory" / "SKILL.md"
    if not skill_md.is_file():
        import pytest
        pytest.skip("fixture skill not found")

    entry = _parse_skill_file(skill_md)
    assert entry is not None
    names = {d.name for d in entry.agent_descriptors}
    assert "prime_factorizer" in names
    assert "modular_solver" in names


def test_skill_registry_load_skill_emits_install_steps_for_requirements() -> None:
    registry = SkillRegistry(
        [
            SkillEntry(
                name="deck",
                description="Deck skill",
                body="Make slides.",
                source="/tmp/SKILL.md",
                requirements=("python-pptx", "npm:sharp"),
            )
        ]
    )

    loaded = registry.load_skill("deck")

    assert "await run_command('uv pip install --quiet python-pptx')" in loaded
    assert "await run_command('cd /tmp && npm install sharp')" in loaded
    assert "STEP 2 — SKILL INSTRUCTIONS" in loaded
    assert "`python -m <module> ...` → `uv run python -m <module> ...`" in loaded
    assert "`pip list` / `pip show` / `pip freeze` → `uv pip list`" in loaded
    assert "must never be rewritten as `uv npm`" in loaded
    assert "Do not use `uv npm`, `uv run node`, or `uv run npm`" in loaded
