"""Integration tests for agent spawning."""

import pytest


def test_invalid_module_raises_at_load_time(tmp_path):
    """tool_definition with bad module → ToolDefinitionError at build time."""
    from cuga.backend.agent_spawn.registry import ToolDefinition
    from cuga.backend.agent_spawn.tool_builder import ToolDefinitionError, build_tool_from_definition

    defn = ToolDefinition(
        name="bad",
        description="d",
        module="nonexistent.module",
        function="fn",
    )
    with pytest.raises(ToolDefinitionError):
        build_tool_from_definition(defn)


def test_number_theory_production_skill_agents_load():
    """The production number_theory SKILL.md registers tools directly in its frontmatter.

    In the fluid-spawning model there are no AGENT.md files; tools are pre-registered
    into CUGA's own context via the skill's tools: frontmatter block so ad-hoc subagents
    can inherit them.
    """
    from pathlib import Path as _Path

    from cuga.backend.agent_spawn.tool_builder import build_tools_from_skill_tool_definitions
    from cuga.backend.skills.loader import _parse_skill_file

    skill_md = (
        _Path(__file__).resolve().parents[2]
        / ".agents"
        / "skills"
        / "number_theory"
        / "SKILL.md"
    )
    if not skill_md.is_file():
        pytest.skip("production number_theory SKILL.md not found")

    entry = _parse_skill_file(skill_md)
    assert entry is not None

    # Tools are declared directly in SKILL.md frontmatter
    tool_names = {td["name"] for td in entry.tool_definitions}
    assert "prime_factorize" in tool_names, f"prime_factorize not in {tool_names}"
    assert "solve_crt" in tool_names, f"solve_crt not in {tool_names}"

    # Verify the tools are importable and buildable
    built = build_tools_from_skill_tool_definitions(entry)
    assert len(built) == 2
    built_names = {t.name for t in built}
    assert built_names == {"prime_factorize", "solve_crt"}
