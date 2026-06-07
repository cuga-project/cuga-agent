"""Build LangChain StructuredTools from ToolDefinition descriptors."""

from __future__ import annotations

import asyncio
import importlib
from typing import TYPE_CHECKING

from langchain_core.tools import StructuredTool

from cuga.backend.agent_spawn.registry import ToolDefinition

if TYPE_CHECKING:
    from cuga.backend.skills.registry import SkillEntry


class ToolDefinitionError(Exception):
    """Raised at load time when a tool_definition entry is invalid."""


def build_tool_from_definition(defn: ToolDefinition) -> StructuredTool:
    """Import module.function and wrap as StructuredTool.

    Raises ToolDefinitionError immediately if the module or function cannot be
    imported — errors surface at descriptor load time, not at spawn time.
    """
    try:
        mod = importlib.import_module(defn.module)
    except ImportError as e:
        raise ToolDefinitionError(
            f"Cannot import module {defn.module!r} for tool {defn.name!r}: {e}"
        ) from e

    fn = getattr(mod, defn.function, None)
    if fn is None:
        raise ToolDefinitionError(
            f"Module {defn.module!r} has no attribute {defn.function!r}"
        )

    schema_cls = None
    if defn.args_schema:
        schema_cls = getattr(mod, defn.args_schema, None)
        if schema_cls is None:
            raise ToolDefinitionError(
                f"args_schema {defn.args_schema!r} not found in {defn.module!r}"
            )

    kwargs: dict = {"name": defn.name, "description": defn.description}
    if asyncio.iscoroutinefunction(fn):
        kwargs["coroutine"] = fn
    else:
        kwargs["func"] = fn
    if schema_cls:
        kwargs["args_schema"] = schema_cls

    return StructuredTool.from_function(**kwargs)


def build_tools_from_skill_tool_definitions(skill_entry: "SkillEntry") -> list[StructuredTool]:
    """Build StructuredTools from a SKILL.md tools: block."""
    out: list[StructuredTool] = []
    for raw in skill_entry.tool_definitions:
        defn = ToolDefinition(
            name=raw["name"],
            description=raw.get("description", ""),
            module=raw["module"],
            function=raw["function"],
            args_schema=raw.get("args_schema"),
        )
        out.append(build_tool_from_definition(defn))
    return out
