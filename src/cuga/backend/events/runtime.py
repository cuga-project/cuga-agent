"""The ``AgentRuntime`` port — the single seam between the event plane and "an agent".

Everything agent-related (concierge, /invoke, channels) goes through this interface, so
swapping frameworks = writing one adapter (events_docs/ARCHITECTURE.md). Implementations:

  - ``HttpRuntime``       — **the production one.** Executes by calling CUGA's ``POST /run``.
  - ``AgentStoreRuntime`` — its base: scope-keyed storage on the shared AgentStore, no execution.
  - ``ReactRuntime``      — LangGraph ``create_react_agent`` + ``MemorySaver`` (dev/test).
  - ``StubRuntime``       — deterministic, in-memory, **no deps** → tests & dry-run.

There is no in-process CUGA adapter any more: the eventing layer is a separate service, so the
worker call always crosses the wire. ReactRuntime imports langgraph **lazily inside methods**, so
importing this module never drags in a heavy runtime.
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
    backend: str = "cuga"                  # cuga | react (worker default is cuga; react is dev/test)
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


# ---- AgentStoreRuntime — storage + isolation, no execution ----------------
class AgentStoreRuntime(AgentRuntime):
    """Scope-keyed agent storage on the shared ``AgentStore``. Execution is somebody else's job.

    This used to be ``CugaRuntime``: it also EXECUTED, by building a per-agent ``DynamicAgentGraph``
    in-process through ``_cuga_bridge``. That only worked when the events layer was mounted inside
    CUGA's own process — the retired "combined" topology — because it needed CUGA's live
    ``app_state``. With the eventing layer standalone, execution always crosses the wire
    (``HttpRuntime.run`` → ``POST /run``), so the in-process half was unreachable and is deleted
    along with ``_cuga_bridge``.

    That deletion is why this package no longer imports ``cuga.backend.cuga_graph`` at all — the
    only remaining tie to CUGA core is ``secret_seam`` → ``cuga.backend.secrets.resolve_secret``.
    """

    def __init__(self, *, agent_store, **_ignored) -> None:
        # **_ignored swallows app_context / react_fallback / cache_size from older call sites.
        self._store = agent_store              # AgentStore — storage + isolation (shared)

    def upsert_agent(self, spec: AgentSpec, *, scope: str = DEFAULT_SCOPE) -> str:
        self._store.upsert(scope, spec)
        return spec.name

    def get_agent(self, agent_id: str, *, scope: str = DEFAULT_SCOPE) -> AgentSpec | None:
        # A missing store is "no local agents", not a crash. HttpRuntime relies on this: its
        # `super().get_agent(…) or AgentSpec("cuga")` fallback — the guarantee that the one agent
        # is ALWAYS addressable — never ran if this raised first.
        return self._store.get(scope, agent_id) if self._store is not None else None

    def list_agents(self, *, scope: str = DEFAULT_SCOPE) -> list[AgentSpec]:
        return self._store.list(scope) if self._store is not None else []

    async def run(self, agent_id: str, thread_id: str, text: str,
                  *, scope: str = DEFAULT_SCOPE, deliver_to: list | None = None) -> str:
        raise NotImplementedError(
            "AgentStoreRuntime stores agents; it does not run them. Use HttpRuntime (the eventing "
            "service's runtime), which executes by calling CUGA's POST /run.")


def logging_warn(msg: str) -> None:
    import logging
    logging.getLogger("cuga.events").warning(msg)


# ---- HttpRuntime (backend="http") — CUGA as a SEPARATE SERVICE ------------
class HttpRuntime(AgentStoreRuntime):
    """Run the worker by calling CUGA's ``POST /run`` over HTTP instead of in-process.

    This is the seam that lets the eventing layer be its own deployable: everything upstream of
    the worker (triggers, scheduler, channels, concierge, delivery) already talks HTTP, and the
    single in-process tie was ``_cuga_bridge`` — which also needed CUGA's live ``app_state``
    objects. Calling ``/run`` removes that tie entirely: those objects stay on CUGA's side where
    they belong, and this process never imports the CUGA graph.

    Agent storage/isolation is inherited from CugaRuntime (the shared AgentStore) — only execution
    moves across the wire. ``/run`` is the non-streaming sibling of ``/stream``: same graph, same
    knowledge/history/policies, terminal answer only.
    """

    def __init__(self, *, agent_store, base_url: str = "", token: str = "",
                 timeout: float = 300.0, retries: int = 2) -> None:
        super().__init__(agent_store=agent_store)
        self._base = (base_url or os.environ.get("CUGA_URL")
                      or f"http://127.0.0.1:{os.environ.get('EVENTS_CUGA_PORT', '7860')}").rstrip("/")
        self._token = token or (os.environ.get("CUGA_RUN_TOKEN")
                                or os.environ.get("GATEWAY_TOKEN") or "").split(" #", 1)[0].strip()
        self._timeout = timeout
        self._retries = max(0, int(retries))
        self._roster: list[AgentSpec] = []
        self._roster_at = 0.0

    _ROSTER_TTL = 60.0      # a roster changes only on redeploy; re-ask rarely, never per call

    def _remote_roster(self) -> list[AgentSpec]:
        """Ask CUGA what it has loaded. THE ROSTER BELONGS TO WHOEVER EXECUTES — in a split that is
        CUGA, not this process, so guessing here is always wrong. ``/run/agents`` is the machine
        sibling of ``/run`` and reports the supervisor's sub-agents; ``/api/agents`` is the
        dashboard's endpoint (cookie-guarded, one card for the configured agent) and is only a
        fallback for a CUGA old enough not to serve the former."""
        import time
        now = time.monotonic()
        if self._roster and (now - self._roster_at) < self._ROSTER_TTL:
            return self._roster
        headers = {"X-Gateway-Token": self._token} if self._token else {}
        for path in ("/run/agents", "/api/agents"):
            try:
                import httpx
                r = httpx.get(f"{self._base}{path}", headers=headers, timeout=10)
                if r.status_code != 200:
                    continue
                rows = r.json()
                rows = rows.get("agents", rows) if isinstance(rows, dict) else rows
                out = []
                for a in rows or []:
                    d = a if isinstance(a, dict) else {"name": str(a)}
                    name = d.get("name") or ""
                    if name:
                        out.append(AgentSpec(name=name, backend="http",
                                             prompt=d.get("description") or "",
                                             mcp_servers=list(d.get("mcp_servers") or [])))
                if out:
                    self._roster, self._roster_at = out, now
                    return out
            except Exception as e:  # noqa: BLE001 — reporting must never break the service
                logging_warn(f"could not read the roster from {self._base}{path}: {e}")
        return []

    def list_agents(self, *, scope: str = DEFAULT_SCOPE) -> list[AgentSpec]:
        """What CUGA has loaded — asked, not assumed.

        CUGA WINS. In a split deployment execution happens on the CUGA side, so its roster is the
        only truth; this process's store holds at best a stale copy. Preferring the local store
        (the first cut) meant one leftover row from an earlier run — a "Digital Sales Agent" left
        in ~/.cuga/events.db — masked the entire live roster and the service reported 1 agent while
        CUGA was serving 9. The local store stays as the fallback for when CUGA can't be reached,
        so a reporting call never takes the events layer down.
        """
        remote = self._remote_roster()
        if remote:
            return remote
        # The supervisor is always addressable even when the roster can't be listed.
        return super().list_agents(scope=scope) or [
            AgentSpec(name="cuga", backend="http", prompt="the CUGA supervisor")]

    def get_agent(self, agent_id: str, *, scope: str = DEFAULT_SCOPE) -> AgentSpec | None:
        """SUPERVISOR MODEL: "cuga" is always addressable — the one agent exists by construction,
        exactly as SupervisorRuntime and find_or_create_flow already treat it. Without this the
        split's agent store starts empty (the roster lives in CUGA's process, not here) and every
        scheduled tick came back `404 unknown agent 'cuga'` — armed, never fired.

        Any OTHER name is resolved against CUGA's loaded roster for the same reason: a webhook
        pinned to ``?agent=incident_triage`` is naming a real sub-agent, and rejecting it here as
        unknown — which is what happened before, since only this process's empty store was
        consulted — failed the call before the supervisor ever got a say."""
        if agent_id == "cuga":
            return (super().get_agent(agent_id, scope=scope)
                    or AgentSpec(name="cuga", backend="http", prompt="the CUGA supervisor"))
        want = agent_id.split("::")[-1]
        remote = next((s for s in self._remote_roster() if s.name == want), None)
        return remote if remote is not None else super().get_agent(agent_id, scope=scope)

    async def run(self, agent_id: str, thread_id: str, text: str,
                  *, scope: str = DEFAULT_SCOPE, deliver_to: list | None = None) -> str:
        import asyncio
        import httpx
        spec = self.get_agent(agent_id, scope=scope)
        if spec is None:
            raise KeyError(f"unknown agent {agent_id!r} in scope {scope!r}")
        from . import runmeta
        runmeta.add(agent=agent_id.split("::")[-1], backend="http",
                    mcp=list(getattr(spec, "mcp_servers", []) or []) if spec else [])
        # Carry the caller's agent across the hop. In-process runtimes get this for free; over HTTP
        # it has to be said out loud, or a pinned specialist silently degrades to generic routing.
        body = {"query": text, "thread_id": thread_id, "user_id": scope,
                "disable_history": True, "agent": agent_id.split("::")[-1]}
        headers = {"X-Gateway-Token": self._token} if self._token else {}
        last = None
        for attempt in range(self._retries + 1):
            # Only TRANSPORT failures are retryable. An application-level answer (any HTTP
            # response at all) is decided below and either returned or raised — retrying a 401 or
            # an agent error just multiplies the damage and, worse, an early version swallowed
            # both into the retry loop and returned the NEXT attempt's answer.
            try:
                async with httpx.AsyncClient(timeout=self._timeout) as c:
                    r = await c.post(f"{self._base}/run", json=body, headers=headers)
            except Exception as e:  # noqa: BLE001 — connect/read/timeout: worth another go
                last = e
                if attempt < self._retries:
                    await asyncio.sleep(0.5 * (2 ** attempt))
                    continue
                break
            if r.status_code == 200:
                data = r.json() or {}
                if data.get("status") == "ok" or data.get("answer"):
                    return data.get("answer") or ""
                raise RuntimeError(
                    f"cuga /run: {data.get('error') or 'status=' + str(data.get('status'))}")
            if 400 <= r.status_code < 500:      # our fault (bad token/body) — retrying cannot help
                raise RuntimeError(f"cuga /run HTTP {r.status_code}: {r.text[:200]}")
            last = RuntimeError(f"cuga /run HTTP {r.status_code}")   # 5xx — transient, retry
            if attempt < self._retries:
                await asyncio.sleep(0.5 * (2 ** attempt))
        raise RuntimeError(f"cuga /run unreachable at {self._base} ({last})")


# ---- selection -----------------------------------------------------------
def make_runtime(backend: str = "http", **kw) -> AgentRuntime:
    """Build the WORKER runtime.

    **There is one production runtime: ``http``.** The eventing layer is its own service, so the
    worker call always crosses the wire to CUGA's ``POST /run``. The in-process runtimes
    (SupervisorRuntime / ClassicRuntime) existed only for the retired "combined" topology, where
    events mounted onto CUGA's app and could reach its objects directly; with that gone they were
    unreachable, so they are gone too.

    ``react`` and ``stub`` remain for tests and dry-runs. ``kw``: ``agent_store`` (shared storage),
    ``cuga_url``/``cuga_token`` (http), ``model_factory``/``checkpointer`` (react).
    """
    b = (backend or "http").lower()
    if b == "stub":
        return StubRuntime()
    if b == "react":     # dev/test-only lightweight loop (unchanged)
        return ReactRuntime(**{k: v for k, v in kw.items()
                               if k in ("model_factory", "agent_store", "checkpointer")})
    if b not in ("http", "cuga", ""):
        raise ValueError(f"unknown worker backend {backend!r} (expected http | react | stub)")
    # "cuga" is accepted as a legacy alias so an old EVENTS_WORKER_BACKEND=cuga in someone's .env
    # keeps working — it means the same thing now: execute on CUGA, over HTTP.
    return HttpRuntime(agent_store=kw.get("agent_store"),
                       base_url=kw.get("cuga_url", ""), token=kw.get("cuga_token", ""))


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
