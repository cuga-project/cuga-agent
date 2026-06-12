"""A2A server module — exposes CUGA as an A2A-compatible agent.

This package is mounted only when ``settings.a2a.enabled`` is true. When
disabled, importing this package costs nothing beyond Python's module-load
overhead, and the rest of the server is byte-identical to a build without
this feature.
"""

from cuga.backend.server.a2a.agent_card import build_agent_card
from cuga.backend.server.a2a.router import build_router
from cuga.backend.server.a2a.task_adapter import stream_events_to_a2a

__all__ = ["build_agent_card", "build_router", "stream_events_to_a2a"]
