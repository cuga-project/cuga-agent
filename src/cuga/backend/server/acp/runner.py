"""ACP runner selection helpers.

Encapsulates the logic for picking the correct ``AgentRunner`` for an ACP
context based on whether a supervisor config path is provided.

Keeping this logic here (rather than inline in ``app.py``) lets tests and other
callers import and exercise the selection in isolation.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def build_acp_runner(
    *,
    app_state: Any,
    event_stream_func: Any,
    supervisor_config_path: str,
    auto_approve: bool,
) -> Any:
    """Return the appropriate runner for the given settings.

    If *supervisor_config_path* is set, a :class:`SupervisorAgentRunner`
    configured for ACP is returned.  Otherwise a :class:`SimpleAgentRunner`
    bound to *event_stream_func* is returned.

    Args:
        app_state: FastAPI ``app_state`` reference passed to the runner.
        event_stream_func: CUGA's ``event_stream`` async generator; used by the
            simple runner only.
        supervisor_config_path: Path to the supervisor YAML config, or empty
            string when supervisor mode is not requested.
        auto_approve: When True the simple runner will auto-approve HITL
            interrupts without surfacing them to the caller.

    Returns:
        An ``AgentRunner`` instance (either ``SimpleAgentRunner`` or
        ``SupervisorAgentRunner``).
    """
    from cuga.backend.server.agent_protocol.supervisor_runner import SupervisorAgentRunner
    from cuga.backend.server.agent_protocol.simple_runner import SimpleAgentRunner

    if supervisor_config_path:
        logger.info(
            "ACP inbound: routing requests through supervisor at %s",
            supervisor_config_path,
        )
        return SupervisorAgentRunner(
            app_state,
            supervisor_config_path,
            protocol_name="ACP",
            cache_attr="acp_supervisor",
        )

    logger.info(
        "ACP inbound: routing requests through simple runner (direct CUGA agent, auto_approve=%s)",
        auto_approve,
    )
    return SimpleAgentRunner(
        app_state_ref=app_state,
        event_stream_func=event_stream_func,
        auto_approve=auto_approve,
        caller_user_id="acp_user",
    )
