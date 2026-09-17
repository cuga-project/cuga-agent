"""ACP child application factory.

The public entry point is ``build_acp_app_for_settings``.  It is called by
the CUGA main application when ``settings.acp.enabled`` is True, and returns
a fully-wired :class:`~fastapi.FastAPI` instance ready to be mounted at the
configured path prefix.

All ACP SDK imports are deferred to *inside* this function so that the rest of
the CUGA server pays no import cost when ACP is disabled.

HITL resume notes
-----------------
Direct-agent mode (``SimpleAgentRunner``) fully supports ACP HITL resume:
:class:`~cuga.backend.server.acp.agent.CugaACPAgent` yields
``MessageAwaitRequest`` on HITL interrupts and processes the ``MessageAwaitResume``
reply.  Supervisor mode (``SupervisorAgentRunner``) exposes only the terminal
behaviour supported by ``CugaSupervisor.invoke()`` and must not advertise a
stronger guarantee.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI

logger = logging.getLogger(__name__)


def build_acp_app_for_settings(
    acp_settings: Any,
    app_state: Any,
    *,
    event_stream_func: Any,
) -> FastAPI:
    """Build and return the ACP child FastAPI application.

    The ACP SDK (``acp_sdk``) is imported inside this function so that importing
    this module never triggers an ``ImportError`` when the SDK is absent.

    Args:
        acp_settings: A validated :class:`~cuga.backend.server.acp.settings.ACPSettings`
            instance (or duck-typed equivalent).
        app_state: The FastAPI ``app_state`` reference forwarded to the runner.
        event_stream_func: CUGA's ``event_stream`` async generator; required for
            ``SimpleAgentRunner`` (direct-agent mode).

    Returns:
        A fully configured :class:`~fastapi.FastAPI` application.

    Raises:
        ImportError: When ``acp_sdk`` is not installed, with an actionable
            message directing the operator to install ``cuga[acp]``.
    """
    try:
        import acp_sdk.server
        from acp_sdk.server import MemoryStore
    except ImportError as exc:
        raise ImportError(
            "The ACP integration requires the acp_sdk package. Install it with: pip install cuga[acp]"
        ) from exc

    from datetime import timedelta

    from cuga.backend.server.acp.agent import CugaACPAgent
    from cuga.backend.server.acp.dependencies import build_auth_dependencies
    from cuga.backend.server.acp.runner import build_acp_runner

    supervisor_config_path: str = getattr(acp_settings, "supervisor_config_path", "") or ""
    auto_approve: bool = bool(getattr(acp_settings, "auto_approve", False))
    store_limit: int = int(getattr(acp_settings, "store_limit", 1000))
    store_ttl_seconds: int = int(getattr(acp_settings, "store_ttl_seconds", 3600))
    auth_required: bool = bool(getattr(acp_settings, "auth_required", True))
    agent_name: str = str(getattr(acp_settings, "agent_name", "cuga"))
    agent_description: str = str(getattr(acp_settings, "agent_description", ""))

    runner = build_acp_runner(
        app_state=app_state,
        event_stream_func=event_stream_func,
        supervisor_config_path=supervisor_config_path,
        auto_approve=auto_approve,
    )

    agent = CugaACPAgent(
        runner=runner,
        name=agent_name,
        description=agent_description,
    )

    store = MemoryStore(
        limit=store_limit,
        ttl=timedelta(seconds=store_ttl_seconds),
    )

    dependencies = build_auth_dependencies(auth_required=auth_required)

    app: FastAPI = acp_sdk.server.create_app(
        agent,
        store=store,
        enable_playground_cors=False,
        dependencies=dependencies or None,
    )

    logger.info(
        "ACP child application built (agent=%s, auth_required=%s, supervisor=%s)",
        agent_name,
        auth_required,
        bool(supervisor_config_path),
    )

    return app
