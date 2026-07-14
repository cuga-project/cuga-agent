from pathlib import Path
from unittest.mock import patch

import pytest

from cuga.backend.skills.loader import discover_skills, get_skill_root
from cuga.backend.skills.registry import SkillEntry, SkillRegistry
from cuga.backend.skills.tools import create_skill_tools

pytestmark = pytest.mark.unit


def _write_skill(root: Path, name: str, description: str, body: str = "Body", requirements: str = "") -> None:
    skill_dir = root / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    requirements_block = f"requirements: {requirements}\n" if requirements else ""
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n{requirements_block}---\n{body}\n",
        encoding="utf-8",
    )


def test_get_skill_root_defaults_to_cuga(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)

    root = get_skill_root(
        ".cuga",
        global_skills_root=str(tmp_path / "global_agents"),
        legacy_global_skills_root=str(tmp_path / "global_cuga"),
    )

    assert root == tmp_path / ".cuga" / "skills"


def test_get_skill_root_agents_preset(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)

    root = get_skill_root(".cuga", root="agents")

    assert root == tmp_path / ".agents" / "skills"


def test_discover_skills_uses_single_root_only(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    global_cuga = tmp_path / "global_cuga"
    global_agents = tmp_path / "global_agents"

    _write_skill(global_cuga, "shared", "legacy global")
    _write_skill(global_agents, "shared", "agents global")
    _write_skill(tmp_path / ".cuga" / "skills", "shared", "cuga local")
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

    assert set(by_name) == {"shared"}
    assert by_name["shared"].description == "cuga local"


def test_discover_skills_agents_root_reads_agents_directory_only(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    _write_skill(tmp_path / ".cuga" / "skills", "cuga_only", "cuga local")
    _write_skill(
        tmp_path / ".agents" / "skills",
        "agents_only",
        "agents local",
        requirements="[python-pptx, npm:sharp]",
    )

    entries = discover_skills(".cuga", root="agents")
    by_name = {entry.name: entry for entry in entries}

    assert set(by_name) == {"agents_only"}
    assert by_name["agents_only"].requirements == ("python-pptx", "npm:sharp")


def test_skill_registry_load_skill_includes_install_normalization_guidance() -> None:
    registry = SkillRegistry(
        [
            SkillEntry(
                name="deck",
                description="Deck skill",
                body="## Dependencies\n\nuv pip install python-pptx",
                source="/skills/deck/SKILL.md",
                requirements=("python-pptx", "npm:sharp"),
            )
        ]
    )

    loaded = registry.load_skill("deck")

    assert "await run_command('uv pip install" not in loaded
    assert "uv pip install python-pptx" in loaded
    assert "STEP 1 — SKILL INSTRUCTIONS" in loaded
    assert "STEP 2 — SKILL INSTRUCTIONS" not in loaded
    assert "follow that skill's own structure" in loaded
    assert "uv pip install" in loaded
    assert "python -c" in loaded
    assert "python -m pip" in loaded
    assert "retry once with" in loaded
    assert "Never use bare `uv run`" in loaded
    assert "Do NOT list or explore" in loaded
    assert "never `uv npm`" in loaded.lower() or "never `uv npm` or `uv run node`" in loaded


def test_skill_registry_load_skill_without_requirements_skips_install_step() -> None:
    registry = SkillRegistry(
        [
            SkillEntry(
                name="analysis",
                description="Analysis skill",
                body="Analyze uploads.",
                source="/skills/deck/SKILL.md",
            )
        ]
    )

    loaded = registry.load_skill("analysis")

    assert "INSTALL REQUIREMENTS" not in loaded
    assert "STEP 1 — SKILL INSTRUCTIONS" in loaded
    assert "STEP 2 — SKILL INSTRUCTIONS" not in loaded
    assert "Analyze uploads." in loaded


def test_load_skill_tool_prints_instructions_even_if_agent_discards_return(capsys) -> None:
    """The code-agent's stdout capture is the only guaranteed path for skill
    instructions to reach the agent's context. If the agent's own code discards
    `load_skill`'s return value instead of printing it, the tool must still
    surface the instructions via a print side-effect, or the agent never sees
    them and improvises instead of following the skill.
    """
    registry = SkillRegistry(
        [
            SkillEntry(
                name="deck",
                description="Deck skill",
                body="## Body\n\nDistinctive skill instructions marker.",
                source="/skills/deck/SKILL.md",
            )
        ]
    )
    load_tool = create_skill_tools(registry)[0]
    assert load_tool.name == "load_skill"

    result = load_tool.func(name="deck")

    printed = capsys.readouterr().out
    assert "Distinctive skill instructions marker." in printed
    assert result == printed.rstrip("\n") or "Distinctive skill instructions marker." in result


# ---------------------------------------------------------------------------
# Sandbox skill-copy path
# ---------------------------------------------------------------------------


def _write_agents_skill(root: Path, name: str, description: str = "desc", body: str = "Body") -> None:
    """Write a SKILL.md under root/.cuga/skills/<name>/SKILL.md (default discovery path)."""
    skill_dir = root / ".cuga" / "skills" / name
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
    """A description containing {{ }} Jinja2 expression syntax is sanitized at parse time."""
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
    """A description containing {% %} Jinja2 block syntax has the delimiters stripped."""
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
    """A name field containing Jinja2 syntax is sanitized."""
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
