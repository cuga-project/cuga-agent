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
        "Skill files live in the sandbox under `/tmp/cuga_workspace/skills/<skill_name>/` — "
        "use **`await read_file(...)`** to read them, **`await write_file(...)`** to create or edit scripts, "
        "**`await run_command(...)`** for CLI steps, **`await list_files(...)`** to browse, and "
        "**`await download_file(...)`** when the user needs an artifact from the sandbox. "
        "Explore the tree with e.g. `await run_command('ls -R /tmp/cuga_workspace/skills/<skill_name>')` "
        "when unsure what is there."
    )
    return "\n".join(lines)
