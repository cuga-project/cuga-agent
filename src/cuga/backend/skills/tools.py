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
        "**First**, when a task matches a skill, you **must** load it: call "
        "`await load_skill(\"<skill_name>\")` using the skill id from the list above, `print` the returned "
        "text, and follow those instructions. Do not skip loading. Skill files live in the sandbox under "
        "`/tmp/cuga_workspace/skills/<skill_name>/` — use **`await read_file(...)`** to read them (optionally "
        "`start_line` / `end_line` and `grep_pattern` for large logs), **`await write_file(...)`** to create "
        "or edit scripts and small text assets, **`await run_command(...)`** for installs and CLI steps, "
        "**`await list_files(...)`** to browse, and **`await download_file(...)`** when the user needs an "
        "artifact from the sandbox. Explore the tree with e.g. "
        "`await run_command('ls -R /tmp/cuga_workspace/skills/<skill_name>')` when unsure what is there."
    )
    return "\n".join(lines)
