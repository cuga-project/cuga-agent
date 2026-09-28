"""Lazy construction of the direct CUGA runner used by ACP stdio."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, AsyncIterator


class StdioCugaRunner:
    """Protocol-neutral runner backed by CUGA's public direct SDK path.

    The web server's event stream depends on its full FastAPI lifespan and has
    legacy stdout writes, so it is unsafe for a protocol-clean stdio process.
    This narrow runner invokes the same CUGA graph through ``CugaAgent`` without
    starting an HTTP server. Construction remains lazy until the first prompt.
    """

    def __init__(self) -> None:
        self._agent: Any | None = None
        self._initialization_lock = asyncio.Lock()
        self._initialized = False
        self._closed = False

    def _get_agent(self) -> Any:
        if self._agent is None:
            from cuga.sdk import CugaAgent

            self._agent = CugaAgent()
        return self._agent

    async def _get_initialized_agent(self) -> Any:
        """Initialize and compile the shared SDK agent exactly once."""
        if self._closed:
            raise RuntimeError("ACP runner is closed")
        agent = self._get_agent()
        if self._initialized:
            return agent
        async with self._initialization_lock:
            if not self._initialized:
                await agent.initialize()
                # Graph compilation is lazy and mutates shared agent state. Complete
                # it inside the same one-time barrier before concurrent invocations.
                _ = agent.graph
                self._initialized = True
        return agent

    async def run(
        self,
        message: str,
        context_id: str | None = None,
        approval: dict[str, Any] | None = None,
    ) -> AsyncIterator[Any]:
        from cuga.backend.server.agent_protocol.events import AgentStreamEvent

        agent = await self._get_initialized_agent()
        action_response = None
        invoke_message: str | None = message
        if approval is not None:
            action_id = approval.get("action_id")
            if not isinstance(action_id, str) or not action_id:
                yield AgentStreamEvent("error", final=True)
                return
            try:
                pending = _pending_action(agent, context_id)
            except Exception:
                yield AgentStreamEvent("error", final=True)
                return
            if pending is None or pending.get("action_id") != action_id:
                yield AgentStreamEvent("error", final=True)
                return

            from cuga.backend.cuga_graph.nodes.human_in_the_loop.followup_model import ActionResponse

            action_response = ActionResponse(
                action_id=action_id,
                response_type=pending.get("type") or "confirmation",
                timestamp=datetime.now(timezone.utc).isoformat(),
                user_id="acp_user",
                confirmed=bool(approval.get("confirmed", False)),
                button_clicked=bool(approval.get("confirmed", False)),
                # Bound to the server-side pending action looked up above, so its identity
                # (e.g. an ACP permission pending_id) is echoed rather than client-supplied.
                additional_data=pending.get("additional_data"),
            )
            invoke_message = None

        result = await agent.invoke(
            invoke_message,
            thread_id=context_id,
            action_response=action_response,
        )
        try:
            pending = _pending_action(agent, context_id)
        except Exception:
            yield AgentStreamEvent("error", final=True)
            return
        if pending is not None:
            yield AgentStreamEvent(
                "input_required",
                {
                    "text": pending.get("description") or "CUGA requests permission to continue.",
                    "action_id": pending.get("action_id"),
                },
                final=True,
            )
            return
        if result.error:
            yield AgentStreamEvent("error", final=True)
            return
        yield AgentStreamEvent("final_answer", {"text": result.answer}, final=True)

    async def shutdown(self) -> None:
        """Close and release the lazily constructed direct agent."""
        self._closed = True
        agent, self._agent = self._agent, None
        self._initialized = False
        if agent is not None:
            await agent.aclose()


def _pending_action(agent: Any, context_id: str | None) -> dict[str, Any] | None:
    if not context_id:
        return None
    snapshot = agent.graph.get_state({"configurable": {"thread_id": context_id}})
    if not getattr(snapshot, "next", None):
        return None
    pending = (getattr(snapshot, "values", None) or {}).get("hitl_action")
    if pending is None:
        raise RuntimeError("Paused CUGA state has no permission action")
    if isinstance(pending, dict):
        result = pending
    else:
        dump = getattr(pending, "model_dump", None)
        if not callable(dump):
            raise RuntimeError("Paused CUGA state has malformed permission action")
        result = dump()
    action_id = result.get("action_id")
    if not isinstance(action_id, str) or not action_id:
        raise RuntimeError("Paused CUGA state has no valid permission action id")
    return result


def create_runner() -> StdioCugaRunner:
    """Create the lazy direct runner; no CUGA graph imports occur here."""
    return StdioCugaRunner()
