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


# ---- HttpRuntime (backend="http") — CUGA as a SEPARATE SERVICE ------------
class HttpRuntime(CugaRuntime):
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


# ---- SupervisorRuntime (EVENTS_SUPERVISOR=1) — ONE agent, canonical roster ----
class SupervisorRuntime(AgentRuntime):
    """The single-agent world (events_docs/plans/SUPERVISOR_REFACTOR.md).

    There is exactly ONE addressable agent: **"cuga"**. With ``EVENTS_SUPERVISOR=1`` it is a
    ``CugaSupervisor`` whose sub-agents come from the CANONICAL roster file
    (``supervisor_agents.yaml``, CUGA-main's ``load_supervisor_config`` schema) — the SUPERVISOR
    does all routing, per wake-up, and the answer bubbles up. ``run()``'s ``agent_id`` is kept
    only as a caller hint recorded in runmeta; execution always goes to the one agent.

    Sub-agent management is CANONICAL: edit the YAML + ``make reload``. There is no store, no
    upsert — ``upsert_agent`` refuses loudly so the old fleet APIs can't silently half-work.
    ``list_agents`` serves the ROSTER as read-only AgentSpecs (name + prompt) for the Studio's
    view; nothing routes on it."""

    ROSTER_FILE = "supervisor_agents.yaml"

    def __init__(self, roster_path: str | None = None) -> None:
        # BYO agents: EVENTS_SUPERVISOR_ROSTER points at YOUR roster YAML (canonical CUGA-main
        # supervisor schema). Default = ./supervisor_agents.yaml (this repo ships a 27-agent
        # example; the infra carries no assumptions about it — see events_docs/SETUP.md).
        env_roster = os.environ.get("EVENTS_SUPERVISOR_ROSTER", "").split(" #", 1)[0].strip()
        self._roster_path = (roster_path or env_roster
                             or os.path.join(os.getcwd(), self.ROSTER_FILE))
        self._sup = None                       # lazy CugaSupervisor (needs the model config)
        self._specs: list[AgentSpec] = []      # read-only roster view

    # ---- roster (read-only; the YAML is the truth) --------------------------
    def _load_specs(self) -> list[AgentSpec]:
        if self._specs:
            return self._specs
        try:
            import yaml
            cfg = yaml.safe_load(open(self._roster_path)) or {}
            self._specs = [
                AgentSpec(name=a.get("name", ""), backend="cuga",
                          prompt=(a.get("special_instructions") or "").strip(),
                          mcp_servers=[m.get("name") if isinstance(m, dict) else str(m)
                                       for m in (a.get("mcp_servers") or [])])
                for a in (cfg.get("agents") or []) if a.get("name")
            ]
        except FileNotFoundError:
            self._specs = []
        return self._specs

    def upsert_agent(self, spec: AgentSpec, *, scope: str = DEFAULT_SCOPE) -> str:
        raise RuntimeError(
            "supervisor mode: sub-agents are defined in supervisor_agents.yaml (canonical "
            "CUGA-main schema) — edit the file and `make reload`. The fleet upsert API is retired.")

    def get_agent(self, agent_id: str, *, scope: str = DEFAULT_SCOPE) -> AgentSpec | None:
        if agent_id == "cuga":
            return AgentSpec(name="cuga", backend="cuga", prompt="the CUGA supervisor")
        return next((s for s in self._load_specs() if s.name == agent_id), None)

    def list_agents(self, *, scope: str = DEFAULT_SCOPE) -> list[AgentSpec]:
        return self._load_specs()

    # ---- execution — everything goes to THE agent ---------------------------
    async def _supervisor(self):
        if self._sup is None:
            from cuga.supervisor_utils.supervisor_config import load_supervisor_config
            from cuga.sdk import CugaSupervisor
            cfg = await load_supervisor_config(self._roster_path)
            self._sup = CugaSupervisor(
                agents=cfg.agents,
                special_instructions=(cfg.supervisor or {}).get("special_instructions"))
            logging_warn(f"supervisor 'cuga' up with {len(cfg.agents)} sub-agents "
                         f"(roster: {self._roster_path})")
        return self._sup

    async def run(self, agent_id: str, thread_id: str, text: str,
                  *, scope: str = DEFAULT_SCOPE, deliver_to: list | None = None) -> str:
        from . import runmeta
        runmeta.add(agent="cuga", backend="supervisor",
                    mcp=[f"hint:{agent_id}"] if agent_id not in ("", "cuga") else [])
        sup = await self._supervisor()
        # thread identity: per-conversation, NOT the delegation module's fixed per-agent thread —
        # scope+thread keeps users' contexts apart at the supervisor level.
        res = await sup.invoke(text, thread_id=f"{scope}:{thread_id}")
        # OBSERVABILITY (fix #1): the supervisor picks the sub-agent INTERNALLY, so the run log would
        # otherwise show the flat "cuga". Surface WHICH sub-agent actually handled the turn into
        # runmeta.agent (mutates the same dict start()/add() set). Best-effort + fully guarded — this
        # is events-scoped observability and must never break the run. Falls back to "cuga".
        try:
            _st = getattr(sup, "_supervisor_state", None)
            _sel = (_st.get("selected_agents") if isinstance(_st, dict)
                    else getattr(_st, "selected_agents", None))
            if isinstance(_sel, (list, tuple)) and _sel:
                runmeta.add(agent=str(_sel[-1]))       # the sub-agent that answered
        except Exception:  # noqa: BLE001
            pass
        return res.answer if hasattr(res, "answer") else str(res)


# ---- ClassicRuntime (EVENTS_SUPERVISOR unset) — the good old single cuga agent ----
class ClassicRuntime(CugaRuntime):
    """One plain CUGA agent, as main ships it — no fleet, no supervisor, no roster.

    Every ``run()`` executes the SAME generalist graph regardless of the requested agent id
    (kept only as a runmeta hint). ``upsert_agent`` refuses: in the single-agent world there is
    nothing to register. This is the EVENTS_SUPERVISOR-unset half of the supervisor refactor
    (events_docs/plans/SUPERVISOR_REFACTOR.md): unset = classic cuga, set = supervisor."""

    _CUGA = AgentSpec(name="cuga", backend="cuga",
                      prompt="",                       # no special instructions — the plain agent
                      channels=["web", "slack", "discord", "telegram"])

    def __init__(self, *, app_context=None, cache_size: int = 2) -> None:
        super().__init__(agent_store=None, app_context=app_context, cache_size=cache_size)

    def upsert_agent(self, spec: AgentSpec, *, scope: str = DEFAULT_SCOPE) -> str:
        raise RuntimeError("classic mode (EVENTS_SUPERVISOR unset): one plain cuga agent, "
                           "no registrations. Set EVENTS_SUPERVISOR=1 and edit "
                           "supervisor_agents.yaml for sub-agents.")

    def get_agent(self, agent_id: str, *, scope: str = DEFAULT_SCOPE) -> AgentSpec | None:
        return self._CUGA

    def list_agents(self, *, scope: str = DEFAULT_SCOPE) -> list[AgentSpec]:
        return [self._CUGA]

    async def run(self, agent_id: str, thread_id: str, text: str,
                  *, scope: str = DEFAULT_SCOPE, deliver_to: list | None = None) -> str:
        if agent_id not in ("", "cuga"):
            from . import runmeta
            runmeta.add(agent="cuga", backend="cuga", mcp=[f"hint:{agent_id}"])
        return await super().run("cuga", thread_id, text, scope=DEFAULT_SCOPE,
                                 deliver_to=deliver_to)


# ---- selection -----------------------------------------------------------
def make_runtime(backend: str, **kw) -> AgentRuntime:
    """Build the WORKER runtime. ``cuga`` (default) executes on CUGA — no silent react fallback
    (opt in with ``EVENTS_CUGA_FALLBACK_REACT=1``). ``react`` is the bare react runtime; ``stub``
    for tests. ``kw``: ``agent_store`` (shared storage), ``model_factory``/``checkpointer`` (react),
    ``app_context`` (cuga), ``cache_size``."""
    b = (backend or "cuga").lower()
    if b == "stub":
        return StubRuntime()
    if b == "react":     # dev/test-only lightweight loop (unchanged)
        return ReactRuntime(**{k: v for k, v in kw.items()
                               if k in ("model_factory", "agent_store", "checkpointer")})
    # SPLIT DEPLOYMENT: CUGA runs as its own service and the worker call crosses the wire.
    # Same agents, same graph — only the transport differs (see HttpRuntime).
    if b == "http":
        return HttpRuntime(agent_store=kw.get("agent_store"),
                           base_url=kw.get("cuga_url", ""), token=kw.get("cuga_token", ""))
    # THE SINGLE-AGENT WORLD (events_docs/plans/SUPERVISOR_REFACTOR.md). One addressable agent,
    # "cuga", in both modes — the fleet-routing runtime is retired:
    #   EVENTS_SUPERVISOR=1 → the canonical supervisor, sub-agents from supervisor_agents.yaml
    #   unset               → the plain classic cuga agent, as main ships it
    if os.environ.get("EVENTS_SUPERVISOR", "").split(" #", 1)[0].strip() in ("1", "true", "yes"):
        return SupervisorRuntime(roster_path=kw.get("roster_path"))
    return ClassicRuntime(app_context=kw.get("app_context"))


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
