"""E2E tests for the skills component.

Two tiers:
  Tier 1 – component-level (no LLM, no graph): exercises the skills discovery,
            registry, and tool-creation pipeline directly.
  Tier 2 – graph-level (CaptureChatModel): runs CugaLite with a mock LLM and
            asserts that the skills block and load_skill tool are wired in.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from cuga.backend.skills.loader import discover_skills
from cuga.backend.skills.registry import SkillEntry, SkillRegistry
from cuga.backend.skills.tools import create_skill_tools, format_available_skills_block

from .conftest import CaptureChatModel, MinimalToolProvider, extract_system_content, write_skill


# ---------------------------------------------------------------------------
# Tier 1 – Skills discovery
# ---------------------------------------------------------------------------


class TestSkillDiscovery:
    def test_skill_discovered_from_agents_skills_directory(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        write_skill(tmp_path, "make_slides", "Creates slide presentations", "## Do the thing")

        entries = discover_skills(".cuga")

        names = [e.name for e in entries]
        assert "make_slides" in names

    def test_multiple_skills_all_discovered(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        for name in ("data_viz", "send_report", "schedule_meeting"):
            write_skill(tmp_path, name, f"{name} description", "## Body")

        entries = discover_skills(".cuga")
        names = [e.name for e in entries]

        assert "data_viz" in names
        assert "send_report" in names
        assert "schedule_meeting" in names

    def test_skill_entry_preserves_description(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        write_skill(tmp_path, "my_skill", "Exactly this description", "## Steps")

        entries = discover_skills(".cuga")
        entry = next(e for e in entries if e.name == "my_skill")

        assert entry.description == "Exactly this description"

    def test_skill_with_pip_requirements_parsed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        write_skill(tmp_path, "needs_packages", "Needs pip packages", "## Body", "[python-pptx, pandas]")

        entries = discover_skills(".cuga")
        entry = next(e for e in entries if e.name == "needs_packages")

        assert "python-pptx" in entry.requirements
        assert "pandas" in entry.requirements

    def test_skill_with_npm_requirements_parsed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        write_skill(tmp_path, "needs_npm", "Needs npm packages", "## Body", "[npm:sharp, npm:imagemin]")

        entries = discover_skills(".cuga")
        entry = next(e for e in entries if e.name == "needs_npm")

        assert "npm:sharp" in entry.requirements
        assert "npm:imagemin" in entry.requirements


# ---------------------------------------------------------------------------
# Tier 1 – SkillRegistry
# ---------------------------------------------------------------------------


class TestSkillRegistry:
    def _make_registry(self, name: str, body: str, requirements: tuple = ()) -> SkillRegistry:
        entry = SkillEntry(
            name=name,
            description=f"{name} description",
            body=body,
            source=f"/tmp/{name}/SKILL.md",
            requirements=requirements,
        )
        return SkillRegistry([entry])

    def test_load_returns_skill_body_content(self) -> None:
        registry = self._make_registry("make_slides", "## Phase one\nDo the thing\n## Phase two\nVerify it")

        loaded = registry.load_skill("make_slides")

        assert "Do the thing" in loaded
        assert "Verify it" in loaded
        assert loaded.index("Do the thing") < loaded.index("Verify it")

    def test_pip_requirements_emit_uv_install_commands(self) -> None:
        registry = self._make_registry(
            "slide_maker", "## Make slides", requirements=("python-pptx", "pandas")
        )

        loaded = registry.load_skill("slide_maker")

        assert "uv pip install" in loaded
        assert "python-pptx" in loaded
        assert "pandas" in loaded

    def test_npm_requirements_emit_npm_install_commands(self) -> None:
        registry = self._make_registry(
            "image_tool", "## Image processing", requirements=("npm:sharp", "npm:imagemin")
        )

        loaded = registry.load_skill("image_tool")

        assert "npm install" in loaded
        assert "sharp" in loaded
        assert "imagemin" in loaded

    def test_mixed_pip_and_npm_requirements(self) -> None:
        registry = self._make_registry("mixed_skill", "## Mixed", requirements=("python-pptx", "npm:sharp"))

        loaded = registry.load_skill("mixed_skill")

        assert "uv pip install --quiet python-pptx" in loaded
        assert "npm install sharp" in loaded
        # STEP 1 (installs) must precede STEP 2 (instructions) — registry.py:46,88
        assert loaded.index("STEP 1") < loaded.index("STEP 2")

    def test_python_command_normalization_hint_present(self) -> None:
        registry = self._make_registry("norm_skill", "## Body")

        loaded = registry.load_skill("norm_skill")

        assert "Command normalization override" in loaded


# ---------------------------------------------------------------------------
# Tier 1 – Tool creation and skills block formatting
# ---------------------------------------------------------------------------


class TestSkillToolsAndBlock:
    def test_create_skill_tools_returns_load_skill_tool(self) -> None:
        entry = SkillEntry(
            name="my_skill",
            description="My skill",
            body="## Body",
            source="/tmp/SKILL.md",
            requirements=(),
        )
        registry = SkillRegistry([entry])

        tools = create_skill_tools(registry)

        assert len(tools) == 1
        assert tools[0].name == "load_skill"

    def test_format_available_skills_block_lists_all_skill_names(self) -> None:
        entries = [
            SkillEntry("alpha", "Alpha skill", "## Body", "/tmp/a/SKILL.md", ()),
            SkillEntry("beta", "Beta skill", "## Body", "/tmp/b/SKILL.md", ()),
        ]
        registry = SkillRegistry(entries)

        block = format_available_skills_block(registry)

        assert "alpha" in block
        assert "beta" in block

    def test_format_available_skills_block_includes_descriptions(self) -> None:
        entry = SkillEntry("gamma", "Gamma makes reports", "## Body", "/tmp/g/SKILL.md", ())
        registry = SkillRegistry([entry])

        block = format_available_skills_block(registry)

        assert "Gamma makes reports" in block


# ---------------------------------------------------------------------------
# Tier 2 – CugaLite graph integration
# ---------------------------------------------------------------------------


class TestSkillsCugaLiteIntegration:
    @pytest.mark.asyncio
    async def test_skills_block_appears_in_cuga_lite_system_prompt(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from cuga.backend.cuga_graph.nodes.cuga_lite.cuga_lite_graph import (
            CugaLiteState,
            create_cuga_lite_graph,
        )
        from cuga.config import settings

        monkeypatch.chdir(tmp_path)
        write_skill(
            tmp_path,
            "summarize_report",
            "Summarizes complex reports into bullet points",
            "## Instructions\nRead the report and summarize.",
        )

        monkeypatch.setattr(settings.skills, "enabled", True)
        monkeypatch.setenv("CUGA_FOLDER", str(tmp_path / ".cuga"))
        monkeypatch.setattr(settings.advanced_features, "cuga_lite_nl_auto_continue", False)
        monkeypatch.setattr(settings.policy, "enabled", False)
        # Skills block silently cleared at prompt_utils.py:539-541 when enable_shell_tool=False.
        monkeypatch.setattr(settings.advanced_features, "enable_shell_tool", True)

        capture_model = CaptureChatModel(responses=[AIMessage(content="I will summarize.")])
        graph = create_cuga_lite_graph(
            model=capture_model,
            tool_provider=MinimalToolProvider(),
            apps_list=[],
        ).compile()

        thread_id = f"e2e_skills_{uuid.uuid4().hex[:8]}"
        state = CugaLiteState(
            chat_messages=[HumanMessage(content="Can you summarize this report for me?")],
            thread_id=thread_id,
        )
        config = {
            "configurable": {
                "thread_id": thread_id,
                "apps_list": [],
            }
        }

        await graph.ainvoke(state, config=config)

        assert capture_model.captured_inputs, "CaptureChatModel was never invoked"
        system_content = extract_system_content(capture_model.captured_inputs[0])
        assert system_content, "No system message found in LLM inputs"
        assert "summarize_report" in system_content, (
            f"Expected skill name 'summarize_report' in system message. Got: {system_content[:600]}"
        )

    @pytest.mark.asyncio
    async def test_load_skill_tool_is_bound_to_model_when_native_tools_enabled(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """When native bind-tools mode is 'tools' with load_skill, bind_tools receives it."""
        from cuga.backend.cuga_graph.nodes.cuga_lite.cuga_lite_graph import (
            CugaLiteState,
            create_cuga_lite_graph,
        )
        from cuga.config import settings

        monkeypatch.chdir(tmp_path)
        write_skill(tmp_path, "data_extractor", "Extracts structured data", "## Extract data")

        monkeypatch.setattr(settings.skills, "enabled", True)
        monkeypatch.setenv("CUGA_FOLDER", str(tmp_path / ".cuga"))
        monkeypatch.setattr(settings.advanced_features, "cuga_lite_nl_auto_continue", False)
        monkeypatch.setattr(settings.policy, "enabled", False)
        # Skills block silently cleared at prompt_utils.py:539-541 when enable_shell_tool=False.
        monkeypatch.setattr(settings.advanced_features, "enable_shell_tool", True)
        # Enable native tool binding so bind_tools is called with the skill tool
        monkeypatch.setattr(settings.advanced_features, "cuga_lite_bind_tools_mode", "tools")
        monkeypatch.setattr(settings.advanced_features, "cuga_lite_bind_tools_tool_names", ["load_skill"])

        capture_model = CaptureChatModel(responses=[AIMessage(content="Done.")])
        graph = create_cuga_lite_graph(
            model=capture_model,
            tool_provider=MinimalToolProvider(),
            apps_list=[],
        ).compile()

        thread_id = f"e2e_tools_{uuid.uuid4().hex[:8]}"
        state = CugaLiteState(
            chat_messages=[HumanMessage(content="Extract the data from this document.")],
            thread_id=thread_id,
        )
        config = {"configurable": {"thread_id": thread_id, "apps_list": []}}

        await graph.ainvoke(state, config=config)

        tool_names = [getattr(t, "name", None) for t in capture_model.captured_tools]
        assert "load_skill" in tool_names, f"Expected 'load_skill' in bound tools, got: {tool_names}"


# ---------------------------------------------------------------------------
# Blocked / placeholder tests
# ---------------------------------------------------------------------------


class TestSkillsBlockedPaths:
    @pytest.mark.skip(reason="Blocked on #199 — use_sub_agents skill execution path not yet wired")
    @pytest.mark.asyncio
    async def test_skill_executed_via_sub_agent(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Verify that a loaded skill is executed end-to-end via the sub-agent path.

        Once issue #199 is resolved (use_sub_agents wired for skill execution),
        this test should drive a full skill load → sub-agent invocation → result cycle.
        """
        raise NotImplementedError("Implement after #199 is resolved")
