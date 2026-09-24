"""Lightweight protocol-neutral agent interfaces for CUGA adapters."""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any

from cuga.backend.server.agent_protocol.events import AgentStreamEvent
from cuga.backend.server.agent_protocol.protocol import AgentRunner

if TYPE_CHECKING:
    from cuga.backend.server.agent_protocol.simple_runner import SimpleAgentRunner
    from cuga.backend.server.agent_protocol.supervisor_runner import SupervisorAgentRunner

__all__ = ["AgentStreamEvent", "AgentRunner", "SimpleAgentRunner", "SupervisorAgentRunner"]

_lazy_exports = {
    "SimpleAgentRunner": ("cuga.backend.server.agent_protocol.simple_runner", "SimpleAgentRunner"),
    "SupervisorAgentRunner": (
        "cuga.backend.server.agent_protocol.supervisor_runner",
        "SupervisorAgentRunner",
    ),
}


def __getattr__(name: str) -> Any:
    """Load concrete runners only when an adapter requests them."""
    target = _lazy_exports.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attribute = target
    value = getattr(import_module(module_name), attribute)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    """Expose lazy public exports to standard module introspection."""
    return sorted(set(globals()) | set(_lazy_exports))
