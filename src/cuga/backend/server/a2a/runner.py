"""Production-side A2A runner glue and the opt-in mount helper.

Lives separate from ``router.py`` so the router stays a pure
contract-implementation (FastAPI + JSON-RPC) and can be reused with any
``GraphRunner`` Protocol implementation. This module is what wires CUGA
itself behind that protocol and provides the one entry point ``main.py``
calls at startup.

The router only depends on ``runner.GraphRunner`` (a duck-typed Protocol
in ``router.py``); none of these classes leak back the other way.
"""

from __future__ import annotations

import logging
from typing import Any, AsyncIterator, Optional

from fastapi import APIRouter

from cuga.backend.server.a2a.router import build_router
from cuga.backend.server.agent_protocol.events import AgentStreamEvent

logger = logging.getLogger(__name__)

# Compatibility alias: A2AStreamEvent is now AgentStreamEvent.  Code that
# imports ``A2AStreamEvent`` from this module continues to work unchanged.
A2AStreamEvent = AgentStreamEvent


class SupervisorA2ARunner:
    """Thin A2A compatibility subclass of ``SupervisorAgentRunner``.

    Configured with protocol name ``"A2A"`` and cache attribute
    ``"a2a_supervisor"`` so the A2A adapter's cached supervisor is
    stored under the same attribute name as before.
    """

    def __init__(self, app_state_ref: Any, supervisor_config_path: str):
        """Stash the app_state and YAML path; no I/O until ``run()``."""
        from cuga.backend.server.agent_protocol.supervisor_runner import SupervisorAgentRunner

        self._delegate = SupervisorAgentRunner(
            app_state_ref,
            supervisor_config_path,
            protocol_name="A2A",
            cache_attr="a2a_supervisor",
        )

    async def run(
        self, message: str, context_id: Optional[str] = None, approval: Optional[dict] = None
    ) -> AsyncIterator[A2AStreamEvent]:
        """Invoke the supervisor and emit one terminal event with its answer."""
        async for event in self._delegate.run(message, context_id=context_id, approval=approval):
            yield event


class PlaceholderA2ARunner:
    """Used when ``settings.a2a.supervisor_config_path`` is unset.

    Returns a clear "endpoint reached but unconfigured" terminal event
    so callers get a well-formed Task envelope instead of an HTTP 5xx.
    """

    async def run(
        self, message: str, context_id: Optional[str] = None, approval: Optional[dict] = None
    ) -> AsyncIterator[A2AStreamEvent]:
        """Yield a single terminal event explaining the missing config."""
        yield A2AStreamEvent(
            "final_answer",
            {"text": "A2A inbound endpoint reached, but settings.a2a.supervisor_config_path is not set."},
            final=True,
        )


def _settings_to_card_dict(a2a_settings: Any) -> dict[str, Any]:
    """Project a Dynaconf ``settings.a2a`` block into a plain dict.

    Defensive defaults match the documented schema in ``settings.toml``;
    we accept loose mappings (test fixtures, alt loaders) by going through
    ``getattr`` rather than dotted attribute access.
    """
    return {
        "agent_name": getattr(a2a_settings, "agent_name", "cuga"),
        "agent_description": getattr(a2a_settings, "agent_description", "CUGA agent exposed over A2A."),
        "agent_version": getattr(a2a_settings, "agent_version", "0.0.0"),
        "agent_url": getattr(a2a_settings, "agent_url", "http://localhost:8000"),
        "auth_required": getattr(a2a_settings, "auth_required", False),
        "skill_ids": list(getattr(a2a_settings, "skill_ids", []) or []),
    }


def build_a2a_router_for_settings(
    a2a_settings: Any, app_state: Any, event_stream_func: Any = None
) -> APIRouter:
    """Build the A2A FastAPI router bound to a runner picked by settings.

    If ``a2a_settings.supervisor_config_path`` is set we lazily front the
    real CUGA supervisor; if ``event_stream_func`` is provided we use the
    simple runner that directly uses CUGA's event stream; otherwise we mount
    a placeholder so the endpoints at least respond with a well-formed Task envelope.
    Returns a router ready for ``app.include_router(...)``
    """
    supervisor_path = getattr(a2a_settings, "supervisor_config_path", "") or ""

    if supervisor_path:
        runner: Any = SupervisorA2ARunner(app_state, supervisor_path)
        logger.info(f"A2A inbound: routing requests through supervisor at {supervisor_path}")
    elif event_stream_func is not None:
        from cuga.backend.server.a2a.simple_runner import SimpleA2ARunner

        auto_approve = bool(getattr(a2a_settings, "auto_approve", False))
        runner = SimpleA2ARunner(app_state, event_stream_func, auto_approve=auto_approve)
        logger.info(
            "A2A inbound: routing requests through simple runner (direct CUGA agent, auto_approve=%s)",
            auto_approve,
        )
    else:
        runner = PlaceholderA2ARunner()
        logger.warning(
            "A2A inbound enabled but settings.a2a.supervisor_config_path is empty; using placeholder runner."
        )

    return build_router(runner=runner, settings=_settings_to_card_dict(a2a_settings))
