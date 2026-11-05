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
    starting an HTTP server. Construction remains lazy until the first prompt,
    or until :meth:`warm` does it ahead of one.
    """

    def __init__(self) -> None:
        self._agent: Any | None = None
        self._initialization_lock = asyncio.Lock()
        self._initialized = False
        self._closed = False
        self._session_by_context: dict[str, str] = {}
        self._instructions_by_context: dict[str, str] = {}
        self._model_by_context: dict[str, str] = {}

    def describe_session(self, context_id: str, session_id: str, instructions: str) -> None:
        """Record the ACP session id and the host instructions read at session start."""
        self._session_by_context[context_id] = session_id
        self._instructions_by_context[context_id] = instructions

    def set_session_model(self, context_id: str, model: str) -> None:
        """Run this session's next turns on ``model``; other sessions keep theirs."""
        self._model_by_context[context_id] = model

    def _llm_override(self, context_id: str | None) -> dict[str, Any] | None:
        """The model and request headers this session's LLM calls carry.

        ``CUGA_SESSION_HEADER`` names a header that carries the ACP session id on
        every model call, so a model gateway in front of CUGA can attribute each
        call to its session. Unset, no header is sent.
        """
        import os

        override: dict[str, Any] = {}
        model = self._model_by_context.get(context_id or "")
        if model:
            override["model"] = model
        header = os.environ.get("CUGA_SESSION_HEADER", "").strip()
        session_id = self._session_by_context.get(context_id or "")
        if header and session_id:
            override["default_headers"] = {header: session_id}
        return override or None

    def _get_agent(self) -> Any:
        if self._agent is None:
            from cuga.sdk import CugaAgent

            self._agent = CugaAgent()
        return self._agent

    async def _get_initialized_agent(self, context_id: str | None = None) -> Any:
        """Initialize and compile the shared SDK agent exactly once."""
        del context_id
        if self._closed:
            raise RuntimeError("ACP runner is closed")
        return await self._initialize_default_agent()

    async def _initialize_default_agent(self) -> Any:
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

    async def warm(self) -> None:
        """Do the first prompt's one-time work ahead of it.

        A cold process spends tens of seconds importing the graph, loading the
        local embedding model and compiling the graph, and the first prompt
        used to wait for all of it. The imports and the model load run in a
        worker thread, so the ACP connection keeps answering while they run;
        the agent is then initialized on the event loop, as a prompt would.
        A session's own agent, built for its MCP servers, reuses all of it.
        """
        if self._closed:
            return
        await asyncio.to_thread(_preload, self.preload_modules)
        await self._before_warm_initialize()
        await self._initialize_default_agent()

    # Modules a host's runner imports on a first prompt, preloaded with CUGA's own.
    preload_modules: tuple[str, ...] = ()

    async def _before_warm_initialize(self) -> None:
        """What a host's runner sets up before its default agent, such as a checkpointer."""

    async def run(
        self,
        message: str,
        context_id: str | None = None,
        approval: dict[str, Any] | None = None,
    ) -> AsyncIterator[Any]:
        from cuga.backend.server.agent_protocol.events import AgentStreamEvent

        agent = await self._get_initialized_agent(context_id)
        action_response = None
        invoke_message: str | None = message
        if approval is not None:
            action_id = approval.get("action_id")
            if not isinstance(action_id, str) or not action_id:
                yield AgentStreamEvent("error", final=True)
                return
            try:
                pending = await _pending_action(agent, context_id)
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

        from cuga.backend.llm.models import reset_llm_override, set_current_llm_override

        token = set_current_llm_override(self._llm_override(context_id))
        try:
            configurable: dict[str, Any] = {}
            if self._llm_override(context_id):
                from cuga.backend.llm.models import LLMManager
                from cuga.config import settings

                configurable["llm"] = LLMManager().get_model(settings.agent.code.model)
            instructions = self._instructions_by_context.get(context_id or "")
            if instructions:
                configurable["special_instructions"] = instructions
            result = await agent.invoke(
                invoke_message,
                thread_id=context_id,
                action_response=action_response,
                **({"config": {"configurable": configurable}} if configurable else {}),
            )
        finally:
            reset_llm_override(token)
        try:
            pending = await _pending_action(agent, context_id)
        except Exception:
            yield AgentStreamEvent("error", final=True)
            return
        if pending is not None:
            event_data = {
                "text": pending.get("description") or "CUGA requests permission to continue.",
                "action_id": pending.get("action_id"),
            }
            details = pending.get("additional_data")
            tool = details.get("tool") if isinstance(details, dict) else None
            if isinstance(tool, dict) and isinstance(tool.get("full_code"), str):
                event_data["approval_code"] = tool["full_code"]
            yield AgentStreamEvent(
                "input_required",
                event_data,
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


async def _pending_action(agent: Any, context_id: str | None) -> dict[str, Any] | None:
    if not context_id:
        return None
    config = {"configurable": {"thread_id": context_id}}
    async_get_state = getattr(agent.graph, "aget_state", None)
    snapshot = await async_get_state(config) if callable(async_get_state) else agent.graph.get_state(config)
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


def _preload(extra_modules: tuple[str, ...] = ()) -> None:
    """Import what an agent's first initialization imports, and load its embedding model."""
    import importlib

    for module in (*_PRELOAD_MODULES, *extra_modules):
        importlib.import_module(module)
    from cuga.backend.storage.embedding import get_embedding_config
    from cuga.backend.storage.embedding.embedding_service import get_shared_text_embedding

    config = get_embedding_config()
    if config["provider"] == "local":
        get_shared_text_embedding(config["model"])


# What CugaAgent imports inside its methods on the way to a compiled graph.
_PRELOAD_MODULES = (
    "cuga.sdk",
    "cuga.backend.llm.models",
    "langchain_openai",
    "cuga.backend.cuga_graph.policy.configurable",
    "cuga.backend.cuga_graph.nodes.cuga_lite.cuga_lite_graph",
    "cuga.backend.cuga_graph.nodes.answer.final_answer",
    "cuga.backend.cuga_graph.utils.agent_loop",
)


def create_runner() -> StdioCugaRunner:
    """Create the lazy direct runner; no CUGA graph imports occur here."""
    return StdioCugaRunner()
