"""CUGA adapter bridge — the genuinely-new glue that lets us run an arbitrary CUGA
``agent_id`` per request (verified: CUGA's runtime is single-agent today; see
events_docs/IMPACT_AND_COMPATIBILITY.md §5).

This is the CUGA half of ``AgentRuntime`` (``runtime.CugaRuntime``). Everything here
imports CUGA internals **lazily inside functions** and is exercised by the e2e tier
(needs the running CUGA stack). Signatures below follow the july code review; each is
marked VERIFY — confirm against the live modules before relying on it.

Reused CUGA pieces:
  - config_store.save_config / load_config          (agent definitions; multi-agent_id)
  - DynamicAgentGraph(...).build_graph()            (per-agent graph; parameterless __init__)
  - event_stream(agent=<graph>, thread_id=...)      (accepts a pre-built graph — main.py:1286)
"""

from __future__ import annotations

import logging

from .runtime import AgentSpec

log = logging.getLogger("cuga.events.bridge")

_AGENT_PREFIX = ""  # optionally namespace event-created agents, e.g. "ev-"


# NOTE: worker STORAGE (definitions) is delegated to the shared react ``AgentStore`` (see
# runtime.CugaRuntime — it wraps a ReactRuntime for storage + isolation + fallback). So this
# bridge only holds the CUGA-specific EXECUTION glue (build a graph + run it). Persisting
# workers as first-class CUGA ``config_store`` rows (tenant_id per-call) is a future step
# (events_docs/TODO.md); until then the AgentStore is the source of truth for both backends.


async def build_worker_graph(spec: AgentSpec, *, scope: str, ctx: dict):
    """Build a per-agent CUGA ``DynamicAgentGraph`` from an AgentSpec — the piece CUGA lacks
    today (its runtime is single-agent; IMPACT_AND_COMPATIBILITY.md §5). Faithful to the
    startup pattern at ``main.py:814-831``: a per-``agent_id`` ``CombinedToolProvider`` +
    ``DynamicAgentGraph(...)`` + ``build_graph()``.

    ``ctx`` carries the running server's shared context (resolved lazily from ``app_state``):
      { policy_system, langfuse_handler, get_include_by_app }.
    Runs ONLY inside the full CUGA server; ``CugaRuntime`` guards this and falls back to react
    when ``ctx`` is None or the build raises. **VERIFY** against the live modules on first run.
    """
    from cuga.backend.cuga_graph.graph import DynamicAgentGraph
    from cuga.backend.cuga_graph.nodes.cuga_lite.providers.combined import CombinedToolProvider

    agent_id = f"{_AGENT_PREFIX}{scope}::{spec.name}"     # scope-namespaced (isolation stopgap)
    # ``app_names`` = the worker's declared MCP servers; the registry serves them from
    # mcp_servers_cuga_apps.yaml and CombinedToolProvider filters to just these. None → all apps.
    # Hyphens → underscores: CUGA composes `<app>_<tool>` as a Python identifier, so a hyphenated
    # app ('cuga-finance') would yield 'cuga-finance_get_crypto_price' → parsed as subtraction →
    # NameError. The registry keys are underscore names (cuga_finance); map to match.
    # Requires the registry running with the cuga-apps config (events_docs/MCP_SETUP.md); no
    # registry → no tools (the worker still runs, toolless).
    app_names = [n.replace("-", "_") for n in spec.mcp_servers] or None
    tool_provider = CombinedToolProvider(
        app_names=app_names,
        get_include_by_app=ctx.get("get_include_by_app"), agent_id=agent_id)
    graph = DynamicAgentGraph(
        None,
        langfuse_handler=ctx.get("langfuse_handler"),
        policy_system=ctx.get("policy_system"),
        tool_provider=tool_provider,
        special_instructions=spec.prompt or None,
    )
    await graph.build_graph()
    log.info("built CUGA worker graph agent=%s scope=%s mcp=%s", spec.name, scope, spec.mcp_servers)
    return graph


async def run_graph(graph, thread_id: str, text: str) -> str:
    """Run a pre-built ``DynamicAgentGraph`` to a final answer — the SAME path ``/stream`` uses.

    CUGA's graph is NOT a react agent: it's driven by an ``AgentLoop`` over an ``AgentState``
    (``input`` = the query), streaming ``AgentLoopAnswer`` events. We construct the loop with
    explicit args (no ``app_state`` dependency, so this also runs standalone) and return the
    final ``event.answer``. Verified live via ``tests/events/live_cuga_worker_check.py``.
    """
    from cuga.backend.activity_tracker.tracker import ActivityTracker
    from cuga.backend.cuga_graph.state.agent_state import default_state
    from cuga.backend.cuga_graph.utils.agent_loop import AgentLoop, AgentLoopAnswer

    tid = thread_id or "default"
    state = default_state(page=None, observation=None, goal="")
    state.input = text
    state.thread_id = tid
    tracker = ActivityTracker()
    tracker.intent = text

    loop = AgentLoop(
        thread_id=tid,
        langfuse_handler=getattr(graph, "langfuse_handler", None),
        graph=graph.graph,                       # the compiled StateGraph
        tracker=tracker,
        policy_system=graph.policy_system,
        special_instructions=getattr(graph, "special_instructions", None),
        enable_todos=getattr(graph, "enable_todos", None),
        reflection_enabled=getattr(graph, "reflection_enabled", None),
        shortlisting_tool_threshold=getattr(graph, "shortlisting_tool_threshold", None),
        cuga_lite_max_steps=getattr(graph, "cuga_lite_max_steps", None),
        enable_filesystem_tools=getattr(graph, "enable_filesystem_tools", None),
    )
    answer = ""
    async for event in loop.run_stream(state=state):
        if isinstance(event, AgentLoopAnswer):
            if event.answer is not None:
                answer = event.answer
            if event.end:
                break
    return answer if isinstance(answer, str) else str(answer)
