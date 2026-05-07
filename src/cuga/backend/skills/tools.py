"""LangChain StructuredTools for the skills system."""

from __future__ import annotations

from pydantic import BaseModel, Field
from langchain_core.tools import StructuredTool

from cuga.backend.skills.registry import SkillRegistry


class LoadSkillInput(BaseModel):
    name: str = Field(..., description="Skill id from <available_skills>")


def create_skill_tools(registry: SkillRegistry) -> list[StructuredTool]:
    def load_skill_impl(name: str) -> str:
        return registry.load_skill(name)

    load_tool = StructuredTool.from_function(
        func=load_skill_impl,
        name="load_skill",
        description=(
            "Fetch the full instructions for a named skill. "
            "Call this first, print the output, then follow the instructions."
        ),
        args_schema=LoadSkillInput,
    )

    return [load_tool]


def format_available_skills_block(registry: SkillRegistry) -> str:
    lines = ["<available_skills>"]
    for s in sorted(registry.summaries(), key=lambda x: x["name"]):
        lines.append(f"- **{s['name']}**: {s['description']}")
    lines.append("</available_skills>")
    lines.append("")
    lines.append(
        "**When a task matches a skill, follow these steps in strict order — no exceptions:**\n\n"
        "**What a SKILL.md provides:**\n"
        "A skill file is a task playbook. It typically includes frontmatter metadata (`name`, "
        "`description`, requirements), when to use the skill, quick-reference commands, workflows for "
        "reading/editing/creating artifacts, companion docs or scripts to use, quality checks, verification "
        "loops, export/conversion steps, and dependency notes. For example, a presentation skill may describe "
        "when any `.pptx` work should use it, how to read or edit decks, how to create decks from scratch, "
        "design rules, visual QA, image conversion, and required packages/tools.\n\n"
        "**STEP 0 — LOAD the skill (isolated code block):**\n"
        "Call `await load_skill(\"<skill_name>\")` in its own code block and `print` the returned text. "
        "Read the output carefully — it contains the install commands and skill instructions you must follow. "
        "Skill loading takes precedence over todos, `find_tools`, and application tools when a skill clearly matches.\n\n"
        "**STEP 1 — INSTALL REQUIREMENTS (your very first substantive code block, mandatory):**\n"
        "The `load_skill` output includes an ⚠️ STEP 1 section with install commands. "
        "You MUST run every install command listed there in a single isolated "
        "```python``` code block **before** you do anything else. Python package installs must use `uv pip install ...`; "
        "npm installs must remain plain `npm ...` commands and must never be rewritten as `uv npm`. This applies even if you believe "
        "the package is already installed. After installs, `await asyncio.sleep(5)` is already included "
        "so the environment can settle. Print the output of each install command.\n\n"
        "**STEP 2 — FOLLOW SKILL INSTRUCTIONS:**\n"
        "Only after requirements are installed, proceed with the skill instructions from the `load_skill` output. "
        "Do **not** re-read `SKILL.md`; `load_skill` already returned its full contents and those instructions "
        "are authoritative. Companion files live in the sandbox under `/workspace/skills/<skill_name>/` — "
        "if the loaded `SKILL.md` contains relative markdown links or says to read a companion file, treat those "
        "links as workflow routing instructions and read the relevant companion file(s) for the situation before "
        "implementing. Use **`await read_file(...)`** for companion files the loaded instructions require, "
        "**`await write_file(...)`** to create or edit scripts, "
        "**`await run_command(...)`** for CLI steps, **`await list_files(...)`** to browse, and "
        "**`await download_file(...)`** when the user needs an artifact from the sandbox. "
        "Explore the tree only when you need a helper script, template, or companion asset. "
        "When executing commands from skill docs, normalize only Python examples: `python -m ...` must become "
        "`uv run python -m ...`, Python scripts must run with `uv run /workspace/script.py`, Python package commands must use "
        "`uv pip install ...`, and Python package inspection must use `uv pip list` / `uv pip show` / `uv pip freeze` "
        "instead of `pip list` / `pip show` / `pip freeze`. Never prefix Node/npm with uv: Node commands must start "
        "with plain `node ...`, npm commands must start with plain `npm ...`, and packages must be installed locally "
        "as `npm install <package>` in `/workspace`. Do not use `uv npm`, `uv run node`, or `uv run npm`. "
        "If a loaded skill contains QA, verification, validation, export, or conversion steps, run them before the final response unless technically impossible."
    )
    return "\n".join(lines)
