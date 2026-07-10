"""The ``AgentRuntime`` port — the single seam between the event plane and "an agent".

Everything agent-related (concierge, /invoke, channels) goes through this interface, so
swapping frameworks = writing one adapter (events_docs/ARCHITECTURE.md). Three implementations:

  - ``StubRuntime``  — deterministic, in-memory, **no deps** → tests & dry-run.
  - ``ReactRuntime`` — LangGraph ``create_react_agent`` + ``MemorySaver`` (backend="react").
  - ``CugaRuntime``  — CUGA ``DynamicAgentGraph`` via ``get_or_build_agent_graph`` (backend="cuga").

The two real adapters import langgraph/cuga **lazily inside methods**, so importing this
module never drags in the heavy runtime — keeping the port and StubRuntime unit-testable.
"""

from __future__ import annotations

import abc
import os
from dataclasses import dataclass, field

# The single canonical default scope — MUST equal principal.DEFAULT.scope so an unset
# request and an unset agent land in the same namespace.
DEFAULT_SCOPE = "default/default/local"


@dataclass
class AgentSpec:
    """Framework-agnostic agent definition (maps to a CUGA config or a react agent).

    Agents are **built by a builder** (design time): skill (prompt) + tools (mcp_servers) +
    the connectors it may use — ``channels`` (converse-on) and ``integrations`` (watch/act-on,
    each with credential ownership). The runtime concierge only SELECTS among these; it never
    creates agents or picks tools (that's the builder's job)."""
    name: str
    prompt: str = ""
    backend: str = "react"                 # react | cuga
    mcp_servers: list = field(default_factory=list)   # server names (see mcp_catalog)
    builtin_tools: list = field(default_factory=list)
    channels: list = field(default_factory=list)       # converse-on: ["web","telegram",…]
    integrations: list = field(default_factory=list)   # watch/act-on: [{"app","ownership"},…]
    access: list = field(default_factory=list)         # roles/user_ids allowed ([] = everyone)


class AgentRuntime(abc.ABC):
    """Create/define agents and run them on a thread with per-thread memory.

    Every method is scoped by ``scope`` (a Principal.scope string) so two tenants/users get
    ISOLATED agents + memory. ``scope`` defaults to "default" for the single-tenant case."""

    @abc.abstractmethod
    def upsert_agent(self, spec: AgentSpec, *, scope: str = DEFAULT_SCOPE) -> str: ...

    @abc.abstractmethod
    def get_agent(self, agent_id: str, *, scope: str = DEFAULT_SCOPE) -> AgentSpec | None: ...

    @abc.abstractmethod
    def list_agents(self, *, scope: str = DEFAULT_SCOPE) -> list[AgentSpec]: ...

    @abc.abstractmethod
    async def run(self, agent_id: str, thread_id: str, text: str,
                  *, scope: str = DEFAULT_SCOPE, deliver_to: list | None = None) -> str: ...


# ---- StubRuntime (deterministic; for tests & dry-run) --------------------
class StubRuntime(AgentRuntime):
    """In-memory, deterministic runtime. ``run`` echoes a structured line and remembers
    per-thread turns so follow-up context can be asserted without an LLM."""

    def __init__(self) -> None:
        self._agents: dict[tuple[str, str], AgentSpec] = {}   # (scope, name) -> spec
        self._memory: dict[tuple[str, str], list[str]] = {}   # (scope, thread_id) -> [turns]

    def upsert_agent(self, spec: AgentSpec, *, scope: str = DEFAULT_SCOPE) -> str:
        self._agents[(scope, spec.name)] = spec
        return spec.name

    def get_agent(self, agent_id: str, *, scope: str = DEFAULT_SCOPE) -> AgentSpec | None:
        return self._agents.get((scope, agent_id))

    def list_agents(self, *, scope: str = DEFAULT_SCOPE) -> list[AgentSpec]:
        return [s for (sc, _), s in self._agents.items() if sc == scope]

    async def run(self, agent_id: str, thread_id: str, text: str,
                  *, scope: str = DEFAULT_SCOPE, deliver_to: list | None = None) -> str:
        if (scope, agent_id) not in self._agents:
            raise KeyError(f"unknown agent {agent_id!r} in scope {scope!r}")
        turns = self._memory.setdefault((scope, thread_id), [])
        turns.append(text)
        # deterministic answer includes prior-turn count → proves per-thread, per-scope memory
        return f"[{agent_id}] ran on thread={thread_id} turn#{len(turns)}: {text}"


# ---- ReactRuntime (LangGraph; backend="react") --------------------------
class ReactRuntime(AgentRuntime):
    """Standalone LangGraph ReAct agents. Ported from event-agent-ap's executor: one
    ``create_react_agent`` per agent, ``MemorySaver`` keyed by thread_id. langgraph +
    the MCP client are imported lazily so this module stays import-light."""

    def __init__(self, model_factory=None, agent_store=None, checkpointer=None) -> None:
        self._specs: dict[tuple[str, str], AgentSpec] = {}   # in-mem cache when no store
        self._graphs: dict[tuple[str, str], object] = {}     # (scope, name) -> graph (per-process)
        self._model_factory = model_factory   # callable -> chat model (lazy; None → error at run)
        self._store = agent_store             # persistent AgentStore → survives restart / shared
        self._checkpointer = checkpointer     # persistent LangGraph checkpointer → shared memory

    def upsert_agent(self, spec: AgentSpec, *, scope: str = DEFAULT_SCOPE) -> str:
        if self._store is not None:
            self._store.upsert(scope, spec)                # write-through to shared storage
        else:
            self._specs[(scope, spec.name)] = spec
        self._graphs.pop((scope, spec.name), None)         # force rebuild on next run
        return spec.name

    def get_agent(self, agent_id: str, *, scope: str = DEFAULT_SCOPE) -> AgentSpec | None:
        if self._store is not None:
            return self._store.get(scope, agent_id)        # any replica reads the same store
        return self._specs.get((scope, agent_id))

    def list_agents(self, *, scope: str = DEFAULT_SCOPE) -> list[AgentSpec]:
        if self._store is not None:
            return self._store.list(scope)
        return [s for (sc, _), s in self._specs.items() if sc == scope]

    async def _build(self, spec: AgentSpec):
        from langgraph.checkpoint.memory import MemorySaver
        from langgraph.prebuilt import create_react_agent
        from . import mcp_catalog
        from .tools_bridge import build_tools     # lazy: pulls langchain tools

        if self._model_factory is None:
            raise RuntimeError("ReactRuntime needs a model_factory (no LLM configured)")
        model = self._model_factory(spec)
        tools = await build_tools(spec.builtin_tools, mcp_catalog.resolve(spec.mcp_servers))
        checkpointer = self._checkpointer or MemorySaver()   # persistent if provided
        return create_react_agent(model, tools, prompt=spec.prompt, checkpointer=checkpointer)

    async def run(self, agent_id: str, thread_id: str, text: str,
                  *, scope: str = DEFAULT_SCOPE, deliver_to: list | None = None) -> str:
        from langchain_core.messages import HumanMessage
        spec = self.get_agent(agent_id, scope=scope)         # from store (shared) or cache
        if spec is None:
            raise KeyError(f"unknown agent {agent_id!r} in scope {scope!r}")
        graph = self._graphs.get((scope, agent_id))
        if graph is None:
            graph = self._graphs[(scope, agent_id)] = await self._build(spec)
        cfg = {"configurable": {"thread_id": thread_id or "default"}}
        result = await graph.ainvoke({"messages": [HumanMessage(content=text)]}, config=cfg)
        return result["messages"][-1].content or ""


# ---- CugaRuntime (backend="cuga") — the DEFAULT worker backend ----------
class CugaRuntime(AgentRuntime):
    """Runs workers as CUGA agents (a per-``agent_id`` ``DynamicAgentGraph``) so **CUGA does the
    hard work of reasoning/answering** (policies / knowledge / supervisor / tools; events_docs/ARCHITECTURE.md).
    Default backend for concierge-provisioned workers (the concierge itself stays react).

    - **Storage + isolation** use the shared ``AgentStore`` directly (scope-keyed) — the single
      source of truth for both backends. No react involved in storage.
    - **Execution is CUGA, full stop.** There is **NO silent react fallback**. If the CUGA stack
      isn't present (``app_context`` → None) or a build/run raises, ``run`` **raises** — so a
      misconfiguration is loud, never masked by react quietly doing the work. A react fallback is
      available **only** when explicitly opted in via ``EVENTS_CUGA_FALLBACK_REACT=1`` (a
      ``react_fallback`` runtime is then injected), for dev / partial environments.

    ``app_context`` is a zero-arg callable → the running server's shared context
    ({policy_system, langfuse_handler, get_include_by_app}) or None. Resolved **lazily at run
    time** because the events layer mounts before CUGA's startup populates ``app_state``.
    """

    def __init__(self, *, agent_store, app_context=None, react_fallback=None,
                 cache_size: int = 32) -> None:
        self._store = agent_store              # AgentStore — storage + isolation (shared)
        self._app_context = app_context        # callable -> ctx dict | None
        self._react = react_fallback           # ONLY set when EVENTS_CUGA_FALLBACK_REACT (opt-in)
        self._cache_size = cache_size
        self._graphs: dict[tuple, object] = {}    # (scope, name) -> DynamicAgentGraph (FIFO)

    # storage / isolation → the shared AgentStore (scope-keyed)
    def upsert_agent(self, spec: AgentSpec, *, scope: str = DEFAULT_SCOPE) -> str:
        spec.backend = "cuga"
        self._graphs.pop((scope, spec.name), None)   # force rebuild on next run
        self._store.upsert(scope, spec)
        return spec.name

    def get_agent(self, agent_id: str, *, scope: str = DEFAULT_SCOPE) -> AgentSpec | None:
        return self._store.get(scope, agent_id)

    def list_agents(self, *, scope: str = DEFAULT_SCOPE) -> list[AgentSpec]:
        return self._store.list(scope)

    def _ctx(self):
        try:
            return self._app_context() if self._app_context else None
        except Exception:  # noqa: BLE001
            return None

    async def _get_or_build(self, spec: AgentSpec, scope: str, ctx: dict):
        key = (scope, spec.name)
        if key in self._graphs:
            return self._graphs[key]
        from ._cuga_bridge import build_worker_graph
        graph = await build_worker_graph(spec, scope=scope, ctx=ctx)
        if len(self._graphs) >= self._cache_size:
            self._graphs.pop(next(iter(self._graphs)))            # simple FIFO eviction
        self._graphs[key] = graph
        return graph

    async def run(self, agent_id: str, thread_id: str, text: str,
                  *, scope: str = DEFAULT_SCOPE, deliver_to: list | None = None) -> str:
        spec = self.get_agent(agent_id, scope=scope)
        if spec is None:
            raise KeyError(f"unknown agent {agent_id!r} in scope {scope!r}")
        ctx = self._ctx()
        if ctx is None:
            if self._react is not None:          # explicit opt-in only
                logging_warn("cuga stack unavailable; EVENTS_CUGA_FALLBACK_REACT on → react")
                return await self._react.run(agent_id, thread_id, text,
                                             scope=scope, deliver_to=deliver_to)
            raise RuntimeError(
                "CUGA worker backend requires the full CUGA stack (app_context is None — start "
                "via the CUGA server so app_state is populated). No react fallback (set "
                "EVENTS_CUGA_FALLBACK_REACT=1 to allow one).")
        try:
            from ._cuga_bridge import run_graph
            from . import runmeta
            runmeta.add(agent=agent_id.split("::")[-1], backend="cuga",
                        mcp=list(getattr(spec, "mcp_servers", []) or []))
            graph = await self._get_or_build(spec, scope, ctx)
            return await run_graph(graph, thread_id, text)
        except Exception as e:
            if self._react is not None:          # explicit opt-in only
                logging_warn(f"cuga worker failed for {agent_id} ({e}); "
                             "EVENTS_CUGA_FALLBACK_REACT on → react")
                return await self._react.run(agent_id, thread_id, text,
                                             scope=scope, deliver_to=deliver_to)
            raise                                # CUGA does the work — surface the failure


def logging_warn(msg: str) -> None:
    import logging
    logging.getLogger("cuga.events").warning(msg)


# ---- selection -----------------------------------------------------------
def make_runtime(backend: str, **kw) -> AgentRuntime:
    """Build the WORKER runtime. ``cuga`` (default) executes on CUGA — no silent react fallback
    (opt in with ``EVENTS_CUGA_FALLBACK_REACT=1``). ``react`` is the bare react runtime; ``stub``
    for tests. ``kw``: ``agent_store`` (shared storage), ``model_factory``/``checkpointer`` (react),
    ``app_context`` (cuga), ``cache_size``."""
    b = (backend or "cuga").lower()
    if b == "stub":
        return StubRuntime()
    if b == "react":
        return ReactRuntime(**{k: v for k, v in kw.items()
                               if k in ("model_factory", "agent_store", "checkpointer")})
    # cuga (default): storage via AgentStore; react fallback ONLY if explicitly opted in.
    react_fb = None
    if os.environ.get("EVENTS_CUGA_FALLBACK_REACT"):
        react_fb = ReactRuntime(**{k: v for k, v in kw.items()
                                   if k in ("model_factory", "agent_store", "checkpointer")})
    return CugaRuntime(agent_store=kw.get("agent_store"), app_context=kw.get("app_context"),
                       react_fallback=react_fb,
                       **{k: v for k, v in kw.items() if k in ("cache_size",)})


async def make_sqlite_checkpointer(path: str):
    """A PERSISTENT LangGraph checkpointer — conversation memory survives restarts and is
    shared across replicas that point at the same sqlite file (or use Postgres in prod).
    Lazy imports so the port stays import-light."""
    import aiosqlite
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
    conn = await aiosqlite.connect(path)
    saver = AsyncSqliteSaver(conn)
    await saver.setup()
    return saver
