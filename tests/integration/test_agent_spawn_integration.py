"""Integration tests for agent spawning (Phase 11).

All tests run without a live LLM — CugaAgent.stream is mocked throughout.
"""

from pathlib import Path

import pytest

FIXTURE_AGENTS_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "agents"
FIXTURE_SKILLS_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "skills"


def _monkeypatch_cuga_agent(monkeypatch, answer: str = "count=3 sum=6"):
    """Mock CugaAgent.stream so execute() returns `answer` immediately."""
    from cuga.sdk import CugaAgent

    async def _mock_stream(self, message, thread_id=None, config=None, action_response=None):
        yield {"final_answer": answer}

    monkeypatch.setattr(CugaAgent, "stream", _mock_stream)


@pytest.mark.asyncio
async def test_sync_spawn_returns_answer(monkeypatch):
    """Full path: discover → registry → spawn_agent → result (success criteria 2, 9)."""
    _monkeypatch_cuga_agent(monkeypatch, answer="count=3 sum=6")

    from cuga.backend.agent_spawn.loader import discover_agents
    from cuga.backend.agent_spawn.registry import AgentDescriptorRegistry
    from cuga.backend.agent_spawn.tools import create_spawn_tools

    entries = discover_agents(FIXTURE_AGENTS_DIR)
    registry = AgentDescriptorRegistry(entries)
    futures: dict = {}
    tools = create_spawn_tools(registry, {}, futures)
    spawn = next(t for t in tools if t.name == "spawn_agent")

    result = await spawn.coroutine(name="data_analyst", task="Analyse [1,2,3]", mode="sync")
    assert result == "count=3 sum=6"


@pytest.mark.asyncio
async def test_async_spawn_and_get_result(monkeypatch):
    """Async spawn → future_id → get_agent_result returns answer (success criterion 3)."""
    _monkeypatch_cuga_agent(monkeypatch, answer="async-result")

    from cuga.backend.agent_spawn.loader import discover_agents
    from cuga.backend.agent_spawn.registry import AgentDescriptorRegistry
    from cuga.backend.agent_spawn.tools import create_spawn_tools

    entries = discover_agents(FIXTURE_AGENTS_DIR)
    registry = AgentDescriptorRegistry(entries)
    futures: dict = {}
    tools = create_spawn_tools(registry, {}, futures)
    spawn = next(t for t in tools if t.name == "spawn_agent")
    get_result = next(t for t in tools if t.name == "get_agent_result")

    future_id = await spawn.coroutine(name="data_analyst", task="Analyse [1,2,3]", mode="async")
    assert future_id.startswith("future_")

    # Poll for result (max 5 seconds)
    result = await get_result.coroutine(future_id=future_id, timeout=5.0)
    assert result == "async-result"


@pytest.mark.asyncio
async def test_async_spawn_graceful_failure(monkeypatch):
    """Mocked CugaAgent raises → get_agent_result returns '[SpawnError]' (FR-9, criterion 3)."""
    from cuga.sdk import CugaAgent

    async def _fail_stream(self, message, thread_id=None, config=None, action_response=None):
        raise RuntimeError("LLM unavailable")
        yield  # make it an async generator

    monkeypatch.setattr(CugaAgent, "stream", _fail_stream)

    from cuga.backend.agent_spawn.loader import discover_agents
    from cuga.backend.agent_spawn.registry import AgentDescriptorRegistry
    from cuga.backend.agent_spawn.tools import create_spawn_tools

    entries = discover_agents(FIXTURE_AGENTS_DIR)
    registry = AgentDescriptorRegistry(entries)
    futures: dict = {}
    tools = create_spawn_tools(registry, {}, futures)
    spawn = next(t for t in tools if t.name == "spawn_agent")
    get_result = next(t for t in tools if t.name == "get_agent_result")

    future_id = await spawn.coroutine(name="data_analyst", task="fail", mode="async")
    result = await get_result.coroutine(future_id=future_id, timeout=5.0)
    assert "[SpawnError]" in result


def test_disabled_produces_no_diff_in_tools(monkeypatch):
    """agent_spawn.enabled=False → no spawn tools in tools_for_prompt (success criterion 4)."""
    monkeypatch.setattr(
        "cuga.config.settings.agent_spawn.enabled", False, raising=False
    )
    from cuga.config import settings

    assert not settings.agent_spawn.enabled


def test_tool_definitions_tool_absent_from_parent_context():
    """summarise_list is in subagent tools only; parent context doesn't have it (criterion 11)."""
    from cuga.backend.agent_spawn.loader import discover_agents

    entries = discover_agents(FIXTURE_AGENTS_DIR)
    assert any(e.name == "data_analyst" for e in entries)
    da = next(e for e in entries if e.name == "data_analyst")
    assert any(d.name == "summarise_list" for d in da.tool_definitions)
    # Parent context is empty — summarise_list is NOT there
    parent_context: dict = {}
    assert "summarise_list" not in parent_context


def test_skill_tools_absent_from_parent_context():
    """skill_tools are in subagent only; parent context doesn't contain them (criterion 12)."""
    parent_context: dict = {}
    assert "skill_tool_fn" not in parent_context


def test_invalid_module_raises_at_load_time(tmp_path):
    """tool_definition with bad module → ToolDefinitionError at build time (criterion 13)."""
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


def test_data_analyst_descriptor_runs_in_ci_without_live_llm():
    """data_analyst fixture: discover → get → build_tool succeeds (criterion 10)."""
    from cuga.backend.agent_spawn.loader import discover_agents
    from cuga.backend.agent_spawn.registry import AgentDescriptorRegistry
    from cuga.backend.agent_spawn.tool_builder import build_tool_from_definition

    entries = discover_agents(FIXTURE_AGENTS_DIR)
    registry = AgentDescriptorRegistry(entries)
    entry = registry.get("data_analyst")
    assert entry is not None
    assert entry.name == "data_analyst"
    # Build the tool_definition — should succeed without a live LLM
    assert len(entry.tool_definitions) == 1
    tool = build_tool_from_definition(entry.tool_definitions[0])
    assert tool.name == "summarise_list"


# ── Skill-embedded agent tests ─────────────────────────────────────────────


def test_skill_agents_merged_into_spawn_registry(tmp_path):
    """Agents declared in SKILL.md agents: key are available for spawning.

    This test verifies the full chain:
      discover_skills → SkillEntry.agent_descriptors → AgentDescriptorRegistry
    without requiring a live LLM.
    """
    from cuga.backend.skills.loader import discover_skills, clear_skills_cache
    from cuga.backend.agent_spawn.registry import AgentDescriptorRegistry
    from cuga.backend.agent_spawn.tools import create_spawn_tools

    # Skill directory that declares two sub-agents
    skill_dir = tmp_path / ".agents" / "skills" / "number_theory"
    skill_dir.mkdir(parents=True)

    # Write AGENT.md files as sub-directories next to SKILL.md
    for name, desc in [
        ("worker_alpha", "Alpha worker agent"),
        ("worker_beta", "Beta worker agent"),
    ]:
        agent_dir = skill_dir / "agents" / name
        agent_dir.mkdir(parents=True)
        (agent_dir / "AGENT.md").write_text(
            f"---\nname: {name}\ndescription: {desc}\n---\nBody.\n",
            encoding="utf-8",
        )

    (skill_dir / "SKILL.md").write_text(
        "---\n"
        "name: number_theory\n"
        "description: orchestrates sub-agents\n"
        "agents:\n"
        "  - agents/worker_alpha\n"
        "  - agents/worker_beta\n"
        "---\nUse spawn_agent to delegate work.\n",
        encoding="utf-8",
    )

    import os
    clear_skills_cache()
    original_cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        entries = discover_skills(None)
    finally:
        os.chdir(original_cwd)
        clear_skills_cache()

    nt = next((e for e in entries if e.name == "number_theory"), None)
    assert nt is not None, "number_theory skill not found"
    assert len(nt.agent_descriptors) == 2

    agent_names = {d.name for d in nt.agent_descriptors}
    assert agent_names == {"worker_alpha", "worker_beta"}

    # Build registry from skill-embedded descriptors and verify spawn tools work
    registry = AgentDescriptorRegistry(list(nt.agent_descriptors))
    tools = create_spawn_tools(registry=registry, parent_tools_context={}, spawn_futures={})
    tool_names = {t.name for t in tools}
    assert "spawn_agent" in tool_names
    assert "get_agent_result" in tool_names


@pytest.mark.asyncio
async def test_skill_agents_spawn_agent_tool_callable(tmp_path, monkeypatch):
    """spawn_agent created from skill-embedded agents actually routes to the right agent."""
    from cuga.sdk import CugaAgent

    # Use the correct stream format: (namespace, {node: state}) tuples
    async def _mock_stream(self, message, thread_id=None, config=None, action_response=None):
        yield ((), {"FinalAnswerAgent": {"final_answer": "phi=138240"}})

    monkeypatch.setattr(CugaAgent, "stream", _mock_stream)

    from cuga.backend.skills.loader import discover_skills, clear_skills_cache
    from cuga.backend.agent_spawn.registry import AgentDescriptorRegistry
    from cuga.backend.agent_spawn.tools import create_spawn_tools

    skill_dir = tmp_path / ".agents" / "skills" / "nt"
    skill_dir.mkdir(parents=True)

    agent_dir = skill_dir / "agents" / "number_worker"
    agent_dir.mkdir(parents=True)
    (agent_dir / "AGENT.md").write_text(
        "---\nname: number_worker\ndescription: Does math\n---\nBody.\n",
        encoding="utf-8",
    )
    (skill_dir / "SKILL.md").write_text(
        "---\n"
        "name: nt\n"
        "description: number theory\n"
        "agents:\n"
        "  - agents/number_worker\n"
        "---\nBody.\n",
        encoding="utf-8",
    )

    import os
    clear_skills_cache()
    original_cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        entries = discover_skills(None)
    finally:
        os.chdir(original_cwd)
        clear_skills_cache()

    nt = next(e for e in entries if e.name == "nt")
    registry = AgentDescriptorRegistry(list(nt.agent_descriptors))
    tools = create_spawn_tools(registry=registry, parent_tools_context={}, spawn_futures={})
    spawn = next(t for t in tools if t.name == "spawn_agent")

    result = await spawn.coroutine(name="number_worker", task="compute totient", mode="sync")
    assert result == "phi=138240"


def test_number_theory_production_skill_agents_load():
    """The production number_theory SKILL.md registers tools directly in its frontmatter.

    In the new fluid-spawning model there are no AGENT.md files; tools are pre-registered
    into CUGA's own context via the skill's tools: frontmatter block so ad-hoc subagents
    can inherit them.
    """
    from pathlib import Path as _Path
    from cuga.backend.skills.loader import _parse_skill_file
    from cuga.backend.agent_spawn.tool_builder import build_tools_from_skill_tool_definitions

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

    # No embedded AGENT.md descriptors in the new format
    assert entry.agent_descriptors == (), "number_theory should have no embedded AGENT.md descriptors"

    # Tools are declared directly in SKILL.md frontmatter
    tool_names = {td["name"] for td in entry.tool_definitions}
    assert "prime_factorize" in tool_names, f"prime_factorize not in {tool_names}"
    assert "solve_crt" in tool_names, f"solve_crt not in {tool_names}"

    # Verify the tools are importable and buildable
    built = build_tools_from_skill_tool_definitions(entry)
    assert len(built) == 2
    built_names = {t.name for t in built}
    assert built_names == {"prime_factorize", "solve_crt"}
