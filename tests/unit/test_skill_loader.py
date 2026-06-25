from pathlib import Path
from unittest.mock import patch

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
    assert "await run_command('npm install sharp')" in loaded
    assert "STEP 2 — SKILL INSTRUCTIONS" in loaded
    assert "`python -m <module> ...` → `uv run python -m <module> ...`" in loaded
    assert "`pip list` / `pip show` / `pip freeze` → `uv pip list`" in loaded
    assert "must never be rewritten as `uv npm`" in loaded
    assert "Do not use `uv npm`, `uv run node`, or `uv run npm`" in loaded


# ---------------------------------------------------------------------------
# Sandbox skill-copy path
# ---------------------------------------------------------------------------


def _write_agents_skill(root: Path, name: str, description: str = "desc", body: str = "Body") -> None:
    """Write a SKILL.md under root/.agents/skills/<name>/SKILL.md (the standard discovery path)."""
    skill_dir = root / ".agents" / "skills" / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n{body}\n",
        encoding="utf-8",
    )


def test_copy_skills_to_workspace_copies_skill_files(tmp_path: Path, monkeypatch) -> None:
    """_copy_skills_to_workspace() copies SKILL.md into the per-thread workspace skills dir."""
    from cuga.backend.cuga_graph.nodes.cuga_lite.executors.local.local_sandbox_executor import (
        LocalSandboxExecutor,
    )

    monkeypatch.chdir(tmp_path)
    _write_agents_skill(tmp_path, "my_skill")

    workspace_root = tmp_path / "ws"
    thread_id = "test-thread"

    with patch(
        "cuga.backend.cuga_graph.nodes.cuga_lite.executors.local.local_sandbox_executor.local_thread_workspace_root",
        return_value=workspace_root,
    ):
        executor = LocalSandboxExecutor()
        executor._copy_skills_to_workspace(
            thread_id=thread_id,
            cuga_folder=str(tmp_path / ".cuga"),
            skills_enabled=True,
        )

    dest = workspace_root / "skills" / "my_skill" / "SKILL.md"
    assert dest.exists(), f"Expected SKILL.md to be copied to {dest}"


def test_copy_skills_to_workspace_is_noop_when_disabled(tmp_path: Path, monkeypatch) -> None:
    """_copy_skills_to_workspace() copies nothing when skills_enabled=False."""
    from cuga.backend.cuga_graph.nodes.cuga_lite.executors.local.local_sandbox_executor import (
        LocalSandboxExecutor,
    )

    monkeypatch.chdir(tmp_path)
    _write_agents_skill(tmp_path, "my_skill")

    workspace_root = tmp_path / "ws"

    with patch(
        "cuga.backend.cuga_graph.nodes.cuga_lite.executors.local.local_sandbox_executor.local_thread_workspace_root",
        return_value=workspace_root,
    ):
        executor = LocalSandboxExecutor()
        executor._copy_skills_to_workspace(
            cuga_folder=str(tmp_path / ".cuga"),
            skills_enabled=False,
        )

    skills_dir = workspace_root / "skills"
    assert not skills_dir.exists(), (
        f"Expected no skills to be copied when skills_enabled=False, but found files under {skills_dir}"
    )


def test_skill_name_with_path_traversal_is_rejected(tmp_path: Path) -> None:
    """A SKILL.md whose frontmatter name contains path separators is silently skipped."""
    from cuga.backend.skills.loader import _parse_skill_file

    skill_path = tmp_path / "SKILL.md"
    skill_path.write_text(
        "---\nname: ../../etc/passwd\ndescription: bad skill\n---\nBody\n",
        encoding="utf-8",
    )

    result = _parse_skill_file(skill_path)

    assert result is None, "Expected None for a skill with a path-traversal name"


# ---------------------------------------------------------------------------
# Fix 4 – Jinja2 prompt-injection sanitization
# ---------------------------------------------------------------------------


def test_jinja_expression_in_description_is_stripped(tmp_path: Path) -> None:
    """A description containing {{ }} Jinja2 expression syntax is sanitized at parse time.

    Without sanitization, a malicious SKILL.md can inject arbitrary text into
    the system prompt by placing Jinja2 template expressions in the description
    field, which is rendered by the mcp_prompt.jinja2 template without escaping.
    """
    from cuga.backend.skills.loader import _parse_skill_file

    skill_path = tmp_path / "SKILL.md"
    skill_path.write_text(
        "---\nname: my_skill\ndescription: Legit desc {{ malicious_var }}\n---\nBody\n",
        encoding="utf-8",
    )

    result = _parse_skill_file(skill_path)

    assert result is not None
    assert "{{" not in result.description
    assert "}}" not in result.description
    assert "malicious_var" not in result.description
    assert "Legit desc" in result.description


def test_jinja_block_in_description_is_stripped(tmp_path: Path) -> None:
    """A description containing {% %} Jinja2 block syntax has the delimiters stripped.

    The sanitizer removes Jinja delimiter sequences ({% %}) to prevent the
    template engine from evaluating them.  Literal text between the delimiters
    may remain; what matters is that no {% or %} tokens reach the renderer.
    """
    from cuga.backend.skills.loader import _parse_skill_file

    skill_path = tmp_path / "SKILL.md"
    skill_path.write_text(
        "---\nname: my_skill\ndescription: Safe {% if True %}payload{% endif %} text\n---\nBody\n",
        encoding="utf-8",
    )

    result = _parse_skill_file(skill_path)

    assert result is not None
    assert "{%" not in result.description, "Jinja block-open delimiter must be removed"
    assert "%}" not in result.description, "Jinja block-close delimiter must be removed"
    assert "Safe" in result.description
    assert "text" in result.description


def test_jinja_expression_in_name_is_stripped(tmp_path: Path) -> None:
    """A name field containing Jinja2 syntax is sanitized (name is also rendered into the prompt)."""
    from cuga.backend.skills.loader import _parse_skill_file

    skill_path = tmp_path / "SKILL.md"
    skill_path.write_text(
        "---\nname: skill_{{ inject }}\ndescription: Fine description\n---\nBody\n",
        encoding="utf-8",
    )

    result = _parse_skill_file(skill_path)

    assert result is not None
    assert "{{" not in result.name
    assert "inject" not in result.name
    assert "skill_" in result.name


def test_clean_description_is_unchanged(tmp_path: Path) -> None:
    """A description with no Jinja2 syntax passes through unchanged."""
    from cuga.backend.skills.loader import _parse_skill_file

    skill_path = tmp_path / "SKILL.md"
    skill_path.write_text(
        "---\nname: my_skill\ndescription: Summarizes complex reports into bullet points\n---\nBody\n",
        encoding="utf-8",
    )

    result = _parse_skill_file(skill_path)

    assert result is not None
    assert result.description == "Summarizes complex reports into bullet points"


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
