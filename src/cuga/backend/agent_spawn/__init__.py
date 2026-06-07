"""Agent spawning support for CugaLite.

Enable via settings.toml: [agent_spawn] enabled = true
"""

from cuga.backend.agent_spawn.registry import AgentDescriptorEntry, AgentDescriptorRegistry, ToolDefinition
from cuga.backend.agent_spawn.loader import discover_agents
from cuga.backend.agent_spawn.tool_builder import (
    ToolDefinitionError,
    build_tool_from_definition,
    build_tools_from_skill_tool_definitions,
)
from cuga.backend.agent_spawn.runtime import SpawnAgentRuntime, clear_runtime_caches, prewarm_agent_for_entry
from cuga.backend.agent_spawn.tools import create_spawn_tools
from cuga.backend.agent_spawn.prompt_utils import format_available_agents_block

__all__: list[str] = [
    "AgentDescriptorEntry",
    "AgentDescriptorRegistry",
    "ToolDefinition",
    "discover_agents",
    "ToolDefinitionError",
    "build_tool_from_definition",
    "build_tools_from_skill_tool_definitions",
    "SpawnAgentRuntime",
    "clear_runtime_caches",
    "prewarm_agent_for_entry",
    "create_spawn_tools",
    "format_available_agents_block",
]
