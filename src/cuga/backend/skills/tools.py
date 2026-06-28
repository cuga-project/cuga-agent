"""LangChain StructuredTools for the skills system."""

from __future__ import annotations

from pydantic import BaseModel, Field
from langchain_core.tools import StructuredTool

from cuga.backend.skills.guidance import AVAILABLE_SKILLS_USAGE
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
    lines.append(AVAILABLE_SKILLS_USAGE)
    return "\n".join(lines)
