"""E2E tests for the skills component.

Two tiers:
  Tier 1 - component-level (no LLM, no graph): exercises the skills discovery,
            registry, and tool-creation pipeline directly.
  Tier 2 - graph-level (CaptureChatModel): runs CugaLite with a mock LLM and
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
from .skills_artifact import (
    ALPHA_SKILL,
    BETA_SKILL,
    DATA_EXTRACTOR,
    GAMMA_SKILL,
    SINGLE_SKILL,
    SUMMARIZE_REPORT,
)


# ---------------------------------------------------------------------------
# Tier 1 - Skills discovery
# ---------------------------------------------------------------------------


class TestSkillDiscovery:
    def test_skill_discovered_from_cuga_skills_directory(
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
# Tier 1 - SkillRegistry
# ---------------------------------------------------------------------------


class TestSkillRegistry:
    def _make_registry(self, name: str, body: str, requirements: tuple = ()) -> SkillRegistry:
        entry = SkillEntry(
            name=name,
            description=f"{name} description",
            body=body,
            source=f"skills/{name}/SKILL.md",
            requirements=requirements,
        )
        return SkillRegistry([entry])

    def test_load_returns_skill_body_content(self) -> None:
        registry = self._make_registry("make_slides", "## Phase one\nDo the thing\n## Phase two\nVerify it")

        loaded = registry.load_skill("make_slides")

        assert "Do the thing" in loaded
        assert "Verify it" in loaded
        assert loaded.index("Do the thing") < loaded.index("Verify it")

    def test_pip_requirements_do_not_auto_emit_install_commands(self) -> None:
        registry = self._make_registry(
            "slide_maker",
            "## Dependencies\n\n`uv pip install python-pptx pandas`",
            requirements=("python-pptx", "pandas"),
        )

        loaded = registry.load_skill("slide_maker")

        assert "await run_command('uv pip install" not in loaded
        assert "python-pptx" in loaded
        assert "pandas" in loaded
        assert "follow that skill's own structure" in loaded

    def test_npm_requirements_do_not_auto_emit_install_commands(self) -> None:
        registry = self._make_registry(
            "image_tool",
            "## Setup\n\n`npm install sharp imagemin`",
            requirements=("npm:sharp", "npm:imagemin"),
        )

        loaded = registry.load_skill("image_tool")

        assert "await run_command('npm install" not in loaded
        assert "sharp" in loaded
        assert "imagemin" in loaded

    def test_mixed_requirements_keep_skill_body_without_generated_install_block(self) -> None:
        registry = self._make_registry(
            "mixed_skill",
            "## Mixed\n\nInstall what this section says.",
            requirements=("python-pptx", "npm:sharp"),
        )

        loaded = registry.load_skill("mixed_skill")

        assert "INSTALL REQUIREMENTS" not in loaded
        assert "Install what this section says." in loaded
        assert "STEP 1 — SKILL INSTRUCTIONS" in loaded

    def test_no_auto_pip_show_or_npm_list_commands(self) -> None:
        registry = self._make_registry("deck", "## Make slides", requirements=("python-pptx",))

        loaded = registry.load_skill("deck")

        assert "await run_command('uv pip show" not in loaded
        assert "await run_command('npm list" not in loaded

    def test_skill_body_install_guidance_precedes_instructions(self) -> None:
        registry = self._make_registry("mixed", "## Mixed", requirements=("python-pptx", "npm:sharp"))

        loaded = registry.load_skill("mixed")

        assert loaded.index("follow that skill's own structure") < loaded.index("STEP 1 — SKILL INSTRUCTIONS")

    def test_load_skill_unknown_name_returns_helpful_error(self) -> None:
        registry = self._make_registry("known_skill", "## Body")

        result = registry.load_skill("nonexistent")

        assert "Unknown skill" in result
        assert "nonexistent" in result
        assert "known_skill" in result

    def test_python_command_normalization_hint_present(self) -> None:
        registry = self._make_registry("norm_skill", "## Body")

        loaded = registry.load_skill("norm_skill")

        assert "Command normalization override" in loaded


# ---------------------------------------------------------------------------
# Tier 1 - Tool creation and skills block formatting
# ---------------------------------------------------------------------------


class TestSkillToolsAndBlock:
    def test_create_skill_tools_returns_load_skill_tool(self) -> None:
        registry = SkillRegistry([SINGLE_SKILL])

        tools = create_skill_tools(registry)

        assert len(tools) == 1
        assert tools[0].name == "load_skill"

    def test_format_available_skills_block_lists_all_skill_names(self) -> None:
        registry = SkillRegistry([ALPHA_SKILL, BETA_SKILL])

        block = format_available_skills_block(registry)

        assert "alpha" in block
        assert "beta" in block

    def test_format_available_skills_block_includes_descriptions(self) -> None:
        registry = SkillRegistry([GAMMA_SKILL])

        block = format_available_skills_block(registry)

        assert "Gamma makes reports" in block


# ---------------------------------------------------------------------------
# Tier 2 - CugaLite graph integration
# ---------------------------------------------------------------------------


class TestSkillsCugaLiteIntegration:
    """Runs the full compiled CugaLiteGraph with a mock LLM and asserts on LLM inputs.

    Required monkeypatches in every test:
      - settings.skills.enabled = True
      - CUGA_FOLDER env var = str(tmp_path / ".cuga")
      - settings.advanced_features.enable_shell_tool = True
          (skills block cleared at prompt_utils.py:539-541 when False)
      - settings.advanced_features.cuga_lite_nl_auto_continue = False
      - settings.policy.enabled = False
    """

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
        write_skill(tmp_path, SUMMARIZE_REPORT.name, SUMMARIZE_REPORT.description, SUMMARIZE_REPORT.body)

        monkeypatch.setattr(settings.skills, "enabled", True)
        monkeypatch.setenv("CUGA_FOLDER", str(tmp_path / ".cuga"))
        monkeypatch.setattr(settings.advanced_features, "cuga_lite_nl_auto_continue", False)
        monkeypatch.setattr(settings.policy, "enabled", False)
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
        config = {"configurable": {"thread_id": thread_id, "apps_list": []}}

        await graph.ainvoke(state, config=config)

        assert capture_model.captured_inputs, "CaptureChatModel was never invoked"
        system_content = extract_system_content(capture_model.captured_inputs[0])
        assert system_content, "No system message found in LLM inputs"
        assert SUMMARIZE_REPORT.name in system_content, (
            f"Expected skill name in system message. Got: {system_content[:600]}"
        )

    @pytest.mark.asyncio
    async def test_load_skill_tool_is_bound_to_model_when_native_tools_enabled(
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
        write_skill(tmp_path, DATA_EXTRACTOR.name, DATA_EXTRACTOR.description, DATA_EXTRACTOR.body)

        monkeypatch.setattr(settings.skills, "enabled", True)
        monkeypatch.setenv("CUGA_FOLDER", str(tmp_path / ".cuga"))
        monkeypatch.setattr(settings.advanced_features, "cuga_lite_nl_auto_continue", False)
        monkeypatch.setattr(settings.policy, "enabled", False)
        monkeypatch.setattr(settings.advanced_features, "enable_shell_tool", True)
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

    @pytest.mark.asyncio
    async def test_graph_completes_without_skills_block_when_no_skills_found(
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

        monkeypatch.setattr(settings.skills, "enabled", True)
        monkeypatch.setenv("CUGA_FOLDER", str(tmp_path / ".cuga"))
        monkeypatch.setattr(settings.advanced_features, "cuga_lite_nl_auto_continue", False)
        monkeypatch.setattr(settings.policy, "enabled", False)
        monkeypatch.setattr(settings.advanced_features, "enable_shell_tool", True)

        capture_model = CaptureChatModel(responses=[AIMessage(content="Done.")])
        graph = create_cuga_lite_graph(
            model=capture_model,
            tool_provider=MinimalToolProvider(),
            apps_list=[],
        ).compile()

        thread_id = f"e2e_no_skills_{uuid.uuid4().hex[:8]}"
        state = CugaLiteState(
            chat_messages=[HumanMessage(content="Hello.")],
            thread_id=thread_id,
        )
        config = {"configurable": {"thread_id": thread_id, "apps_list": []}}

        await graph.ainvoke(state, config=config)

        assert capture_model.captured_inputs, "CaptureChatModel was never invoked"
        system_content = extract_system_content(capture_model.captured_inputs[0])
        assert "available_skills" not in system_content
        assert "load_skill" not in system_content


# ---------------------------------------------------------------------------
# Blocked / placeholder tests
# ---------------------------------------------------------------------------


class TestSkillsBlockedPaths:
    @pytest.mark.skip(reason="Blocked on #199 - use_sub_agents skill execution path not yet wired")
    @pytest.mark.asyncio
    async def test_skill_executed_via_sub_agent(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        raise NotImplementedError("Implement after #199 is resolved")
