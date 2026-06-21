"""Agent spawning support for CugaLite.

Enable via settings.toml: [agent_spawn] enabled = true
"""

from cuga.backend.agent_spawn.runtime import SpawnAgentRuntime, clear_runtime_caches
from cuga.backend.agent_spawn.tools import create_spawn_tools
from cuga.backend.agent_spawn.prompt_utils import format_available_agents_block

__all__: list[str] = [
    "SpawnAgentRuntime",
    "clear_runtime_caches",
    "create_spawn_tools",
    "format_available_agents_block",
]
