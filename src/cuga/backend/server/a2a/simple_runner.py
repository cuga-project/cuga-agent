"""A2A compatibility wrapper around the protocol-neutral SimpleAgentRunner.

The actual runner behavior lives in
``cuga.backend.server.agent_protocol.simple_runner.SimpleAgentRunner``.
This module preserves the original ``SimpleA2ARunner`` three-argument
public API and hard-codes ``caller_user_id="a2a_user"`` so that A2A JSON
and SSE output is unchanged.
"""

from __future__ import annotations

from typing import Any

from cuga.backend.server.agent_protocol.simple_runner import SimpleAgentRunner


class SimpleA2ARunner(SimpleAgentRunner):
    """Thin A2A compatibility subclass of ``SimpleAgentRunner``.

    Keeps the original three-argument constructor
    ``(app_state_ref, event_stream_func, auto_approve=False)`` and passes
    ``caller_user_id="a2a_user"`` to the neutral base class so all A2A
    requests are attributed to the same well-known user identity.
    """

    def __init__(self, app_state_ref: Any, event_stream_func: Any, auto_approve: bool = False):
        """Initialize with a reference to app_state and the event_stream function.

        Args:
            app_state_ref: Reference to the FastAPI app_state (used to read
                graph state when detecting human-in-the-loop interrupts).
            event_stream_func: The ``event_stream`` async generator from main.py.
            auto_approve: When True, HITL interrupts are auto-confirmed and the
                graph is resumed; when False (default), the interrupt is surfaced
                to the A2A caller as an ``input_required`` terminal event.
        """
        super().__init__(
            app_state_ref,
            event_stream_func,
            auto_approve=auto_approve,
            caller_user_id="a2a_user",
        )
