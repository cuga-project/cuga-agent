"""E2E tests for the skills component.

Two tiers:
  Tier 1 - component-level (no LLM, no graph): exercises the skills discovery,
            registry, and tool-creation pipeline directly.
  Tier 2 - graph-level (CaptureChatModel): runs CugaLite with a mock LLM and
            asserts that the skills block and load_skill tool are wired in.

How skills work end-to-end
--------------------------
A "skill" is a SKILL.md file placed by the user or operator under:

    <cwd>/.agents/skills/<skill_name>/SKILL.md

The file uses YAML frontmatter followed by a markdown body:

    ---
    name: summarize_report
    description: Summarizes complex reports into bullet points
    requirements: [python-pptx, npm:sharp]   # optional
    ---
    ## Instructions
    Read the report and output a bullet-point summary.

At agent startup, the pipeline runs in this order:

  1. discover_skills(cuga_folder)
       Scans <cwd>/.agents/skills/ and parses every SKILL.md into a SkillEntry.
       Returns a list[SkillEntry].

  2. SkillRegistry(entries)
       Holds the parsed entries. registry.load_skill(name) returns the full
       instruction string that the model will see when it invokes the skill,
       including any auto-generated package-install preamble.

  3. create_skill_tools(registry)
       Creates a single LangChain StructuredTool named "load_skill". When the model
       calls load_skill(skill_name="summarize_report"), it receives the full body
       returned by registry.load_skill("summarize_report").

  4. format_available_skills_block(registry)
       Produces a markdown block listing all available skills with their descriptions.
       This block is injected into the system prompt so the model knows what skills
       exist before deciding to call load_skill.

  5. CugaLiteGraph (Tier 2)
       In prepare_tools_and_apps, steps 1-4 run automatically when skills.enabled=True.
       The resulting skills block appears in the system message. If bind_tools mode
       includes "load_skill", the tool is also bound to the model via bind_tools().

Requirement types and the commands they emit
--------------------------------------------
  Plain name (e.g. "python-pptx"):   -> "uv pip install --quiet python-pptx"
  npm: prefix (e.g. "npm:sharp"):    -> "npm install sharp"

These install commands are placed in STEP 1 of the loaded skill output, before the
STEP 2 instruction body, so the model always installs dependencies before executing.
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
# Tier 1 - Skills discovery
# ---------------------------------------------------------------------------


class TestSkillDiscovery:
    """Tests discover_skills(), which scans the filesystem for SKILL.md files.

    Each test uses write_skill() (from conftest) to create the expected directory
    structure under tmp_path, then monkeypatches cwd to tmp_path so that
    discover_skills(".cuga") resolves relative paths correctly.

    File layout created by write_skill(root, name, ...):
        <root>/.agents/skills/<name>/SKILL.md

    discover_skills(".cuga") looks under:
        <cwd>/.agents/skills/

    The returned list contains SkillEntry objects with fields:
        .name          - the skill name from the YAML frontmatter
        .description   - the description from the YAML frontmatter
        .requirements  - tuple of requirement strings parsed from the frontmatter
        .body          - the markdown body below the frontmatter
        .source        - absolute path to the SKILL.md file
    """

    def test_skill_discovered_from_agents_skills_directory(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A single SKILL.md written to the expected path is discovered by discover_skills().

        Setup:
          - Writes tmp_path/.agents/skills/make_slides/SKILL.md with name="make_slides".
          - Monkeypatches cwd to tmp_path.

        Expected result:
          - discover_skills(".cuga") returns a list where at least one entry has
            .name == "make_slides".

        Failure modes:
          - Empty list or "make_slides" not in names: the loader is looking in a different
            directory, the YAML frontmatter format changed, or the file was written to the
            wrong path.
        """
        monkeypatch.chdir(tmp_path)
        write_skill(tmp_path, "make_slides", "Creates slide presentations", "## Do the thing")

        entries = discover_skills(".cuga")

        names = [e.name for e in entries]
        assert "make_slides" in names

    def test_multiple_skills_all_discovered(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """All SKILL.md files present under .agents/skills/ are returned in a single call.

        Setup:
          - Writes three SKILL.md files: "data_viz", "send_report", "schedule_meeting".

        Expected result:
          - All three names appear in the list returned by discover_skills(".cuga").
          - No skills are silently dropped due to ordering or concurrency issues.

        Failure modes:
          - One or more names missing: the loader stops after finding the first skill,
            or the directory glob pattern does not recurse correctly.
        """
        monkeypatch.chdir(tmp_path)
        for name in ("data_viz", "send_report", "schedule_meeting"):
            write_skill(tmp_path, name, f"{name} description", "## Body")

        entries = discover_skills(".cuga")
        names = [e.name for e in entries]

        assert "data_viz" in names
        assert "send_report" in names
        assert "schedule_meeting" in names

    def test_skill_entry_preserves_description(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """The description field from the YAML frontmatter is preserved exactly in SkillEntry.

        Setup:
          - Writes a skill with description="Exactly this description".

        Expected result:
          - The SkillEntry for "my_skill" has .description == "Exactly this description"
            with no leading/trailing whitespace changes or character substitutions.

        Why this matters:
          The description is what appears in the skills block shown to the model
          (via format_available_skills_block). An altered description could cause the
          model to invoke the wrong skill or fail to recognize a skill is relevant.
        """
        monkeypatch.chdir(tmp_path)
        write_skill(tmp_path, "my_skill", "Exactly this description", "## Steps")

        entries = discover_skills(".cuga")
        entry = next(e for e in entries if e.name == "my_skill")

        assert entry.description == "Exactly this description"

    def test_skill_with_pip_requirements_parsed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Pip package requirements in YAML frontmatter are parsed into SkillEntry.requirements.

        Setup:
          - Writes a skill with frontmatter: requirements: [python-pptx, pandas]

        Expected result:
          - entry.requirements contains "python-pptx" and "pandas" as separate items.
          - These strings are later used by SkillRegistry.load_skill() to generate
            "uv pip install --quiet python-pptx" and "uv pip install --quiet pandas"
            lines in the STEP 1 block.

        Failure modes:
          - requirements is empty: the YAML list was not parsed, possibly due to a
            format change in how requirements are declared in SKILL.md.
          - Single string instead of list: the brackets were not treated as a YAML list.
        """
        monkeypatch.chdir(tmp_path)
        write_skill(tmp_path, "needs_packages", "Needs pip packages", "## Body", "[python-pptx, pandas]")

        entries = discover_skills(".cuga")
        entry = next(e for e in entries if e.name == "needs_packages")

        assert "python-pptx" in entry.requirements
        assert "pandas" in entry.requirements

    def test_skill_with_npm_requirements_parsed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """npm package requirements (npm: prefix) are parsed and distinguished from pip packages.

        Setup:
          - Writes a skill with frontmatter: requirements: [npm:sharp, npm:imagemin]

        Expected result:
          - entry.requirements contains "npm:sharp" and "npm:imagemin" with the "npm:"
            prefix intact. The prefix is what SkillRegistry uses to route to "npm install"
            rather than "uv pip install".

        Failure modes:
          - Prefix stripped: "sharp" found but not "npm:sharp". The registry would then
            incorrectly try to install "sharp" as a Python package.
          - Items missing: npm: entries were filtered out during parsing.
        """
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
    """Tests SkillRegistry.load_skill(), which produces the full instruction string for a skill.

    The registry is constructed directly from SkillEntry objects (no filesystem I/O)
    using the _make_registry() helper, making these tests fast and isolated from the
    discovery layer.

    load_skill(name) returns a multi-section string. Its general structure is:

        [STEP 1 - only present when requirements exist]
        Before following these instructions, run these install commands:
          uv pip install --quiet <pip_package>
          npm install <npm_package>
        Command normalization override: ...

        [STEP 2 - always present]
        <the body text from the SKILL.md>
        Command normalization override: ...

    The "Command normalization override" hint is always included and tells the model
    to use "python" rather than "python3" when generating shell commands, for
    cross-platform compatibility.
    """

    def _make_registry(self, name: str, body: str, requirements: tuple = ()) -> SkillRegistry:
        """Build a SkillRegistry with a single entry without touching the filesystem.

        The source path is set to a plausible but non-existent path (/tmp/<name>/SKILL.md)
        since load_skill() uses the already-parsed body, not the source path.
        """
        entry = SkillEntry(
            name=name,
            description=f"{name} description",
            body=body,
            source=f"/tmp/{name}/SKILL.md",
            requirements=requirements,
        )
        return SkillRegistry([entry])

    def test_load_returns_skill_body_content(self) -> None:
        """load_skill() includes the full body text in the returned string, in order.

        Setup:
          - Skill body: "## Phase one\\nDo the thing\\n## Phase two\\nVerify it"

        Expected result:
          - "Do the thing" appears in the returned string.
          - "Verify it" appears after "Do the thing" (original order preserved).

        Failure modes:
          - Body text absent: the skill body was not included in the output.
          - Order reversed: the registry reorders sections, which could confuse the
            model about which phase comes first.
        """
        registry = self._make_registry("make_slides", "## Phase one\nDo the thing\n## Phase two\nVerify it")

        loaded = registry.load_skill("make_slides")

        assert "Do the thing" in loaded
        assert "Verify it" in loaded
        assert loaded.index("Do the thing") < loaded.index("Verify it")

    def test_pip_requirements_emit_uv_install_commands(self) -> None:
        """Pip requirements produce 'uv pip install' commands in the STEP 1 block.

        Setup:
          - requirements=("python-pptx", "pandas")

        Expected substrings in loaded output:
          - "uv pip install"     (the install command prefix)
          - "python-pptx"        (first package)
          - "pandas"             (second package)

        The full expected lines look like:
          "uv pip install --quiet python-pptx"
          "uv pip install --quiet pandas"
        """
        registry = self._make_registry(
            "slide_maker", "## Make slides", requirements=("python-pptx", "pandas")
        )

        loaded = registry.load_skill("slide_maker")

        assert "uv pip install" in loaded
        assert "python-pptx" in loaded
        assert "pandas" in loaded

    def test_npm_requirements_emit_npm_install_commands(self) -> None:
        """npm: requirements produce 'npm install' commands (not uv pip install).

        Setup:
          - requirements=("npm:sharp", "npm:imagemin")

        Expected substrings in loaded output:
          - "npm install"    (npm command, not pip)
          - "sharp"          (package name without the "npm:" prefix)
          - "imagemin"       (package name without the "npm:" prefix)

        The full expected lines look like:
          "npm install sharp"
          "npm install imagemin"
        """
        registry = self._make_registry(
            "image_tool", "## Image processing", requirements=("npm:sharp", "npm:imagemin")
        )

        loaded = registry.load_skill("image_tool")

        assert "npm install" in loaded
        assert "sharp" in loaded
        assert "imagemin" in loaded

    def test_mixed_pip_and_npm_requirements(self) -> None:
        """A skill with both pip and npm requirements produces both install command types.

        Setup:
          - requirements=("python-pptx", "npm:sharp")

        Expected substrings in loaded output:
          - "uv pip install --quiet python-pptx"
          - "npm install sharp"

        Expected ordering:
          - "STEP 1" (installs) appears before "STEP 2" (instructions) in the string.
            This ordering is enforced in registry.py:46,88 and ensures the model always
            installs packages before attempting to execute the skill instructions.

        Failure modes:
          - STEP 2 before STEP 1: the model would attempt to run the skill before its
            dependencies are installed, causing import errors.
          - One install type missing: pip or npm routing logic is broken for mixed cases.
        """
        registry = self._make_registry("mixed_skill", "## Mixed", requirements=("python-pptx", "npm:sharp"))

        loaded = registry.load_skill("mixed_skill")

        assert "uv pip install --quiet python-pptx" in loaded
        assert "npm install sharp" in loaded
        # STEP 1 (installs) must precede STEP 2 (instructions) — registry.py:46,88
        assert loaded.index("STEP 1") < loaded.index("STEP 2")

    def test_pip_install_followed_by_verification_command(self) -> None:
        """load_skill() includes a 'uv pip show' verification after the pip install step.

        Without a verification command, the LLM has no signal that the package
        was actually installed before it proceeds to STEP 2 instructions.
        A 'uv pip show <package>' command produces explicit output (Name, Version,
        Location) that the model can read to confirm success.
        """
        registry = self._make_registry("deck", "## Make slides", requirements=("python-pptx",))

        loaded = registry.load_skill("deck")

        assert "uv pip show" in loaded, (
            "Expected 'uv pip show <package>' verification command after pip install"
        )
        assert "python-pptx" in loaded

    def test_npm_install_followed_by_verification_command(self) -> None:
        """load_skill() includes an 'npm list' verification after the npm install step."""
        registry = self._make_registry("img", "## Image tool", requirements=("npm:sharp",))

        loaded = registry.load_skill("img")

        assert "npm list" in loaded, (
            "Expected 'npm list <package>' verification command after npm install"
        )
        assert "sharp" in loaded

    def test_verification_appears_after_install_and_before_step2(self) -> None:
        """Verification commands appear after the install block and before STEP 2."""
        registry = self._make_registry("mixed", "## Mixed", requirements=("python-pptx", "npm:sharp"))

        loaded = registry.load_skill("mixed")

        pip_show_pos = loaded.index("uv pip show")
        npm_list_pos = loaded.index("npm list")
        step2_pos = loaded.index("STEP 2")

        assert pip_show_pos < step2_pos, "pip verification must appear before STEP 2"
        assert npm_list_pos < step2_pos, "npm verification must appear before STEP 2"

    def test_load_skill_unknown_name_returns_helpful_error(self) -> None:
        """load_skill() returns a descriptive error for an unknown skill name.

        Setup:
          - Registry with one skill named "known_skill".

        Expected result:
          - Calling load_skill("nonexistent") returns a string containing:
            - "Unknown skill" (the error prefix)
            - "nonexistent" (the requested name)
            - "known_skill" (so the caller knows what's available)

        Why this matters:
          The LLM could call load_skill with a slightly wrong name (spacing, case).
          A useful error that lists known skills lets the model self-correct.
        """
        registry = self._make_registry("known_skill", "## Body")

        result = registry.load_skill("nonexistent")

        assert "Unknown skill" in result
        assert "nonexistent" in result
        assert "known_skill" in result

    def test_python_command_normalization_hint_present(self) -> None:
        """load_skill() always includes the 'Command normalization override' hint.

        Setup:
          - Skill with no requirements and a simple body "## Body".

        Expected result:
          - The string "Command normalization override" appears in the loaded output.

        Why this matters:
          This hint instructs the model to use "python" (not "python3") when generating
          shell commands. It is required for cross-platform compatibility and should be
          present even for skills with no requirements. If it goes missing, models may
          generate commands that fail on platforms where only "python" is on the PATH.
        """
        registry = self._make_registry("norm_skill", "## Body")

        loaded = registry.load_skill("norm_skill")

        assert "Command normalization override" in loaded


# ---------------------------------------------------------------------------
# Tier 1 - Tool creation and skills block formatting
# ---------------------------------------------------------------------------


class TestSkillToolsAndBlock:
    """Tests the LangChain tool and prompt block produced from a SkillRegistry.

    create_skill_tools(registry) -> list[StructuredTool]
      Returns a list with exactly one tool named "load_skill". The model calls this
      tool with {"skill_name": "<name>"} to receive the full instruction body for
      that skill. It is a single tool regardless of how many skills are registered,
      because the tool itself dispatches to the correct skill at call time.

    format_available_skills_block(registry) -> str
      Returns a markdown block like:

          ## Available Skills
          - **alpha**: Alpha skill
          - **beta**: Beta skill

      This block is injected into the system prompt so the model sees all available
      skills before deciding to call load_skill.
    """

    def test_create_skill_tools_returns_load_skill_tool(self) -> None:
        """create_skill_tools() returns exactly one tool and its name is 'load_skill'.

        Setup:
          - Registry with one skill entry named "my_skill".

        Expected result:
          - len(tools) == 1
          - tools[0].name == "load_skill"

        Note: the tool name is always "load_skill" regardless of how many skills are
        in the registry. The model uses this single entry point to load any skill by
        name at runtime.
        """
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
        """format_available_skills_block() includes every skill name registered.

        Setup:
          - Registry with two skills: "alpha" and "beta".

        Expected result:
          - The returned block string contains "alpha" and "beta".
          - Both names must be present; the model uses this block to decide which
            skill to load. A missing name means the model cannot know that skill exists.
        """
        entries = [
            SkillEntry("alpha", "Alpha skill", "## Body", "/tmp/a/SKILL.md", ()),
            SkillEntry("beta", "Beta skill", "## Body", "/tmp/b/SKILL.md", ()),
        ]
        registry = SkillRegistry(entries)

        block = format_available_skills_block(registry)

        assert "alpha" in block
        assert "beta" in block

    def test_format_available_skills_block_includes_descriptions(self) -> None:
        """format_available_skills_block() includes each skill's description alongside its name.

        Setup:
          - Registry with one skill: name="gamma", description="Gamma makes reports".

        Expected result:
          - "Gamma makes reports" appears in the block.
          - The description is what helps the model decide *which* skill is relevant to
            the user's request. Without it the model sees only skill names, which may
            not be self-explanatory.
        """
        entry = SkillEntry("gamma", "Gamma makes reports", "## Body", "/tmp/g/SKILL.md", ())
        registry = SkillRegistry([entry])

        block = format_available_skills_block(registry)

        assert "Gamma makes reports" in block


# ---------------------------------------------------------------------------
# Tier 2 - CugaLite graph integration
# ---------------------------------------------------------------------------


class TestSkillsCugaLiteIntegration:
    """Runs the full compiled CugaLiteGraph with a mock LLM and asserts on LLM inputs.

    These tests verify that the skills pipeline (discovery -> registry -> block/tool)
    is correctly wired into the graph. CaptureChatModel records everything the graph
    sends to the model without making any real LLM calls.

    Required monkeypatches in every test:
      - settings.skills.enabled = True
          Skills discovery is gated behind this flag. Without it, no skills block is
          built and the model never sees available skills.
      - CUGA_FOLDER env var = str(tmp_path / ".cuga")
          Some graph paths resolve the skills root from this env var.
      - settings.advanced_features.enable_shell_tool = True
          The skills block is silently cleared at prompt_utils.py:539-541 when the
          shell tool is disabled. This patch is required for the skill name to actually
          appear in the system message.
      - settings.advanced_features.cuga_lite_nl_auto_continue = False
          Prevents the graph from looping for a second LLM call, which would exhaust
          the CaptureChatModel's scripted response queue.
      - settings.policy.enabled = False
          Disables policy enforcement to avoid needing policy fixtures.
    """

    @pytest.mark.asyncio
    async def test_skills_block_appears_in_cuga_lite_system_prompt(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The skill name appears in the system message sent to the LLM.

        Setup:
          - Writes a SKILL.md for "summarize_report" with description
            "Summarizes complex reports into bullet points" under tmp_path/.agents/skills/.
          - Monkeypatches cwd to tmp_path so discover_skills() finds the skill.
          - Human message: "Can you summarize this report for me?"
          - Scripted model response: "I will summarize."

        Expected system message contains:
          - "summarize_report"   (the skill name listed in the available skills block)

        Full expected shape of the skills section in the system message (partial):
            ## Available Skills
            - **summarize_report**: Summarizes complex reports into bullet points

        Failure modes:
          - Skill name absent: skills.enabled was not picked up, CUGA_FOLDER pointed
            to the wrong location, enable_shell_tool=False cleared the block, or
            discover_skills() found no skills at the resolved path.
        """
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
        """When native bind-tools mode is 'tools' with load_skill, bind_tools receives it.

        Background:
          CugaLite supports two ways to expose tools to the model:
            1. Text mode (default): tool names and descriptions appear in the system prompt
               as plain text. The model references them by generating Python code.
            2. Native bind-tools mode: tools are passed to model.bind_tools(), which
               exposes them as function-calling schemas. The model calls them via the
               provider's native function-calling API.

          This test exercises mode 2 for the load_skill tool specifically.

        Setup:
          - Writes a SKILL.md for "data_extractor".
          - Sets cuga_lite_bind_tools_mode = "tools" and
            cuga_lite_bind_tools_tool_names = ["load_skill"].
          - Human message: "Extract the data from this document."
          - Scripted model response: "Done."

        Expected result:
          - capture_model.captured_tools contains an object with .name == "load_skill".
          - capture_model.captured_tools is populated by CaptureChatModel.bind_tools(),
            which the graph calls during prepare_tools_and_apps when bind-tools mode
            is active.

        Failure modes:
          - "load_skill" absent from captured_tools: bind_tools was never called, likely
            because cuga_lite_bind_tools_mode was not respected, or the skill was not
            discovered and therefore no load_skill tool was created.
          - captured_tools is empty: bind_tools mode setting was not picked up, so the
            graph fell back to text mode and never called bind_tools() on the model.
        """
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

    @pytest.mark.asyncio
    async def test_graph_completes_without_skills_block_when_no_skills_found(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """When skills_enabled=True but the skills directory is empty, the graph completes
        without error and the system message does not contain an <available_skills> block.

        Setup:
          - skills.enabled = True, CUGA_FOLDER set to tmp_path/.cuga
          - No SKILL.md files written anywhere — the skills directory does not exist.
          - enable_shell_tool = True so skills are not suppressed for an unrelated reason.
          - Scripted model response: "Done."

        Expected result:
          - The graph invokes the LLM successfully.
          - The system message does NOT contain "available_skills" or "load_skill".

        Failure modes:
          - Crash or exception: the graph does not handle empty discovery gracefully.
          - "available_skills" present: an empty or phantom skills block was injected.
        """
        from cuga.backend.cuga_graph.nodes.cuga_lite.cuga_lite_graph import (
            CugaLiteState,
            create_cuga_lite_graph,
        )
        from cuga.config import settings

        monkeypatch.chdir(tmp_path)
        # No skills written — directory does not exist

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
        assert "available_skills" not in system_content, (
            "Expected no skills block when no SKILL.md files exist. "
            f"Got system content: {system_content[:400]}"
        )
        assert "load_skill" not in system_content, (
            "Expected no load_skill tool mention when no skills are available."
        )


# ---------------------------------------------------------------------------
# Blocked / placeholder tests
# ---------------------------------------------------------------------------


class TestSkillsBlockedPaths:
    """Placeholder tests for skill execution paths that are not yet implemented.

    These tests are skipped with an explicit reason pointing to the blocking issue.
    They are kept in the suite (rather than deleted) so that:
      - The gap in coverage is visible when running pytest with -v.
      - When the blocking issue is resolved, the test scaffold is already in place.
      - The skip reason documents the dependency clearly for anyone reading the code.
    """

    @pytest.mark.skip(reason="Blocked on #199 - use_sub_agents skill execution path not yet wired")
    @pytest.mark.asyncio
    async def test_skill_executed_via_sub_agent(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Verify that a loaded skill is executed end-to-end via the sub-agent path.

        Planned flow (once #199 is resolved):
          1. Write a SKILL.md with use_sub_agents: true in its frontmatter.
          2. Run a graph invocation where the model calls load_skill().
          3. The graph routes to a sub-agent that executes the skill instructions.
          4. Assert the sub-agent's result is returned to the parent conversation.

        Once issue #199 is resolved (use_sub_agents wired for skill execution),
        this test should drive a full skill load -> sub-agent invocation -> result cycle.
        """
        raise NotImplementedError("Implement after #199 is resolved")
