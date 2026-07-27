"""Agent spawning support for CugaLite.

Enable via settings.toml: [agent_spawn] enabled = true
"""

from cuga.backend.agent_spawn.runtime import (
    SpawnAgentRuntime,
    cancel_spawn_future,
    clear_runtime_caches,
    pending_spawn_tasks,
    reset_event_callback,
    set_event_callback,
    thread_spawn_futures,
    wait_pending_spawns,
)
from cuga.backend.agent_spawn.tools import create_spawn_tools
from cuga.backend.agent_spawn.prompt_utils import format_available_agents_block

__all__: list[str] = [
    "SpawnAgentRuntime",
    "cancel_spawn_future",
    "clear_runtime_caches",
    "create_spawn_tools",
    "format_available_agents_block",
    "pending_spawn_tasks",
    "reset_event_callback",
    "set_event_callback",
    "thread_spawn_futures",
    "wait_pending_spawns",
]
