"""Protocol-neutral supervisor agent runner.

Wraps a lazily-created ``CugaSupervisor`` and translates its result into
``AgentStreamEvent`` instances. The supervisor is built on first request,
cached on a configurable ``app_state`` attribute, and serialized under a per-
instance lock so concurrent first-requests don't build two supervisors.

A2A and ACP adapters each instantiate (or subclass) this runner supplying
their own ``protocol_name`` label (used in log/error strings) and
``cache_attr`` (the attribute name on ``app_state`` where the supervisor is
stored) so the two protocols do not accidentally share a supervisor instance.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, AsyncIterator, Optional

from cuga.backend.server.agent_protocol.events import AgentStreamEvent

logger = logging.getLogger(__name__)


class SupervisorAgentRunner:
    """Run inbound messages through a lazily-created CugaSupervisor.

    The supervisor is built on first request rather than during lifespan
    startup, so deployments that enable a protocol adapter but never receive a
    request pay no init cost. The instance is cached on
    ``app_state.<cache_attr>`` afterward; concurrent first-requests are
    serialized by an ``asyncio.Lock`` (double-checked) so we don't build two
    supervisors in parallel.
    """

    def __init__(
        self,
        app_state_ref: Any,
        supervisor_config_path: str,
        protocol_name: str = "agent_protocol",
        cache_attr: str = "agent_protocol_supervisor",
    ):
        """Stash the app_state and YAML path; no I/O until ``run()``.

        Args:
            app_state_ref: Reference to the FastAPI app_state object on which
                the built supervisor is cached.
            supervisor_config_path: Path to the supervisor YAML configuration
                file consumed by ``CugaSupervisor.from_yaml``.
            protocol_name: Short protocol label used in log and error messages
                (e.g. ``"A2A"``, ``"ACP"``). Defaults to ``"agent_protocol"``.
            cache_attr: Attribute name on ``app_state_ref`` where the built
                supervisor is stored after the first request. Defaults to
                ``"agent_protocol_supervisor"``.
        """
        self._app_state = app_state_ref
        self._yaml_path = supervisor_config_path
        self._protocol_name = protocol_name
        self._cache_attr = cache_attr
        self._lock = asyncio.Lock()

    async def _ensure_supervisor(self) -> Any:
        """Return the cached supervisor or build one under a lock (double-checked)."""
        existing = getattr(self._app_state, self._cache_attr, None)
        if existing is not None:
            return existing
        async with self._lock:
            existing = getattr(self._app_state, self._cache_attr, None)
            if existing is not None:
                return existing
            # Imported lazily so test harnesses that don't go through this
            # path don't pay the import cost of the SDK supervisor.
            from cuga.sdk import CugaSupervisor

            supervisor = await CugaSupervisor.from_yaml(self._yaml_path)
            setattr(self._app_state, self._cache_attr, supervisor)
            return supervisor

    async def run(
        self, message: str, context_id: Optional[str] = None, approval: Optional[dict] = None
    ) -> AsyncIterator[AgentStreamEvent]:
        """Invoke the supervisor and emit one terminal event with its answer.

        Errors during graph execution are caught and surfaced as a terminal
        ``error`` event with only the exception class name on the wire — the
        full traceback is logged via ``logger.exception`` so operators can
        debug without leaking config to the caller.
        """
        try:
            supervisor = await self._ensure_supervisor()
            result = await supervisor.invoke(message, thread_id=context_id)
            answer = getattr(result, "answer", None) or str(result)
            error = getattr(result, "error", None)
            if error:
                # The supervisor's own `error` field is graph-internal text
                # already shaped for our UI — safe to relay; it carries no
                # stack frames or env state.
                yield AgentStreamEvent(
                    "error",
                    {"text": f"Supervisor error: {error}"},
                    final=True,
                )
                return
            yield AgentStreamEvent("final_answer", {"text": answer}, final=True)
        except Exception as exc:
            logger.exception(f"{self._protocol_name} inbound delegation failed")
            yield AgentStreamEvent(
                "error",
                {"text": f"{self._protocol_name} handler error: {type(exc).__name__}"},
                final=True,
            )
