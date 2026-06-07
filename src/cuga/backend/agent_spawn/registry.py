"""In-memory registry of discovered agent descriptors."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    module: str
    function: str
    args_schema: Optional[str] = None  # Pydantic class name in module


@dataclass(frozen=True)
class AgentDescriptorEntry:
    name: str  # unique, used as tool param
    description: str  # shown in <available_agents>
    source: str  # absolute path of AGENT.md
    tools: tuple[str, ...] = ()  # parent-context tool names
    skill_tools: tuple[str, ...] = ()  # SKILL.md names to load
    tool_definitions: tuple[ToolDefinition, ...] = ()  # inline tool specs
    model: Optional[str] = None
    thread_id_prefix: str = "agent"
    max_steps: int = 8
    inherit_parent_tools: bool = False


class AgentDescriptorRegistry:
    def __init__(self, entries: List[AgentDescriptorEntry]) -> None:
        self._by_name: Dict[str, AgentDescriptorEntry] = {e.name: e for e in entries}

    def get(self, name: str) -> Optional[AgentDescriptorEntry]:
        return self._by_name.get(name.strip())

    def all(self) -> List[AgentDescriptorEntry]:
        return list(self._by_name.values())

    def summaries(self) -> List[Dict[str, str]]:
        return [{"name": e.name, "description": e.description} for e in self._by_name.values()]
