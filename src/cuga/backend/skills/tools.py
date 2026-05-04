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
        "Read the output carefully — it contains the install commands and skill instructions you must follow.\n\n"
        "**STEP 1 — INSTALL REQUIREMENTS (your very first substantive code block, mandatory):**\n"
        "The `load_skill` output includes an ⚠️ STEP 1 section with install commands. "
        "You MUST run every install command listed there — pip, npm, or any other — in a single isolated "
        "```python``` code block **before** you do anything else. This applies even if you believe "
        "the package is already installed. After installs, `await asyncio.sleep(5)` is already included "
        "so the environment can settle. Print the output of each install command.\n\n"
        "**STEP 2 — FOLLOW SKILL INSTRUCTIONS:**\n"
        "Only after requirements are installed, proceed with the skill instructions from the `load_skill` output. "
        "Do **not** re-read `SKILL.md`; `load_skill` already returned its full contents and those instructions "
        "are authoritative. Companion files live in the sandbox under `/tmp/cuga_workspace/skills/<skill_name>/` — "
        "use **`await read_file(...)`** only for companion files the loaded instructions require, "
        "**`await write_file(...)`** to create or edit scripts, "
        "**`await run_command(...)`** for CLI steps, **`await list_files(...)`** to browse, and "
        "**`await download_file(...)`** when the user needs an artifact from the sandbox. "
        "Explore the tree only when you need a helper script, template, or companion asset."
    )
    return "\n".join(lines)
