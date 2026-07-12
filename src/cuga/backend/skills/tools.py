"""LangChain StructuredTools for the skills system."""

from __future__ import annotations

import asyncio
import importlib

from loguru import logger
from pydantic import BaseModel, Field
from langchain_core.tools import StructuredTool

from cuga.backend.skills.guidance import AVAILABLE_SKILLS_USAGE
from cuga.backend.skills.registry import SkillEntry, SkillRegistry


class LoadSkillInput(BaseModel):
    name: str = Field(..., description="Skill id from <available_skills>")


def _build_callable_tools_from_entry(entry: SkillEntry) -> list[StructuredTool]:
    """Import and wrap callable tools declared in a SkillEntry's tool_definitions.

    Each definition's ``import_from`` follows the same dotted-path convention as
    ``supervisor_config.py``'s agent ``import_from``: everything before the last
    dot is the module path, the final segment is the attribute to import.
    """
    out: list[StructuredTool] = []
    for raw in entry.tool_definitions:
        import_from = raw.get("import_from", "")
        tool_name = raw.get("name", "")
        description = raw.get("description", "")
        try:
            module_path, fn_name = import_from.rsplit(".", 1)
            mod = importlib.import_module(module_path)
            fn = getattr(mod, fn_name)
        except (ValueError, ImportError, AttributeError) as exc:
            logger.warning(f"skill {entry.name!r}: cannot import tool {import_from!r}: {exc}")
            continue
        kwargs: dict = {"name": tool_name, "description": description}
        if asyncio.iscoroutinefunction(fn):
            kwargs["coroutine"] = fn
        else:
            kwargs["func"] = fn
        out.append(StructuredTool.from_function(**kwargs))
    return out


def create_skill_tools(registry: SkillRegistry) -> list[StructuredTool]:
    def load_skill_impl(name: str) -> str:
        instructions = registry.load_skill(name)
        # Print unconditionally: the code-agent's stdout capture is the only
        # guaranteed way these instructions reach the agent's context. If the
        # agent's own code discards the return value instead of printing it
        # (e.g. `await load_skill(...); print("Skill loaded successfully")`),
        # it otherwise never sees the skill body and improvises from scratch.
        print(instructions)
        return instructions

    load_tool = StructuredTool.from_function(
        func=load_skill_impl,
        name="load_skill",
        description=(
            "Fetch the full instructions for a named skill. "
            "Call this first, print the output, then follow the instructions."
        ),
        args_schema=LoadSkillInput,
    )

    callable_tools: list[StructuredTool] = []
    for entry in registry.entries():
        callable_tools.extend(_build_callable_tools_from_entry(entry))

    return [load_tool, *callable_tools]


def format_available_skills_block(registry: SkillRegistry) -> str:
    lines = ["<available_skills>"]
    for s in sorted(registry.summaries(), key=lambda x: x["name"]):
        lines.append(f"- **{s['name']}**: {s['description']}")
    lines.append("</available_skills>")
    lines.append("")
    lines.append(AVAILABLE_SKILLS_USAGE)
    return "\n".join(lines)
