from pathlib import Path

from cuga.backend.skills.loader import discover_skills, get_skill_search_roots
from cuga.backend.skills.registry import SkillEntry, SkillRegistry


def _write_skill(
    root: Path,
    name: str,
    description: str,
    body: str = "Body",
    requirements: str = "",
    extra_frontmatter: str = "",
) -> None:
    skill_dir = root / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    requirements_block = f"requirements: {requirements}\n" if requirements else ""
    extra_block = f"{extra_frontmatter}\n" if extra_frontmatter else ""
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n{requirements_block}{extra_block}---\n{body}\n",
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


def test_loader_parses_arguments_and_allowed_tools_frontmatter(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    _write_skill(
        tmp_path / ".agents" / "skills",
        "review",
        "Review a PR",
        extra_frontmatter="arguments: pr_number title\nallowed-tools: [read_file, run_command]",
    )

    entries = discover_skills(None)
    by_name = {e.name: e for e in entries}

    assert by_name["review"].arguments == ("pr_number", "title")
    assert by_name["review"].allowed_tools == ("read_file", "run_command")


def test_loader_distinguishes_missing_from_empty_allowed_tools(tmp_path: Path, monkeypatch) -> None:
    """Allowed-tools enforcement relies on the loader returning ``None`` for an
    absent ``allowed-tools`` key (no restriction) vs ``()`` for an explicit
    empty list (allow nothing)."""
    monkeypatch.chdir(tmp_path)
    _write_skill(tmp_path / ".agents" / "skills", "no_key", "No allowed-tools key", extra_frontmatter="")
    _write_skill(
        tmp_path / ".agents" / "skills",
        "empty_list",
        "Explicit empty allowed-tools",
        extra_frontmatter="allowed-tools: []",
    )

    by_name = {e.name: e for e in discover_skills(None)}

    assert by_name["no_key"].allowed_tools is None
    assert by_name["empty_list"].allowed_tools == ()


def test_loader_rejects_skill_with_numeric_argument_name(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    _write_skill(
        tmp_path / ".agents" / "skills",
        "bad",
        "Has a numeric arg name",
        extra_frontmatter="arguments: title 2",
    )

    entries = discover_skills(None)

    # A numeric-only arg name collides with positional $N syntax — skill is dropped.
    assert "bad" not in {e.name for e in entries}


def test_load_skill_substitutes_args_into_body() -> None:
    registry = SkillRegistry(
        [
            SkillEntry(
                name="review",
                description="Review a PR",
                body="Review PR #$pr for $reason. Full: $ARGUMENTS",
                source="/tmp/SKILL.md",
                arguments=("pr", "reason"),
            )
        ]
    )

    loaded = registry.load_skill("review", "123 typos")

    assert "Review PR #123 for typos. Full: 123 typos" in loaded


def test_load_skill_without_args_leaves_body_verbatim() -> None:
    registry = SkillRegistry(
        [
            SkillEntry(
                name="review",
                description="Review a PR",
                body="Body with $ARGUMENTS placeholder",
                source="/tmp/SKILL.md",
            )
        ]
    )

    # Model-initiated load_skill calls pass no args — body must be untouched.
    loaded = registry.load_skill("review")

    assert "Body with $ARGUMENTS placeholder" in loaded
