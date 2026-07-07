"""The concierge — the RUNTIME ROUTER over PRE-BUILT agents.

Agents are built by a BUILDER (skill + MCP tools + policies + the channels/integrations they may
use). The concierge NEVER creates agents or picks tools. When an end user chats, it:

  1. understands the intent,
  2. if an existing agent can ANSWER NOW → runs it and returns the answer,
  3. if it's a STANDING request (schedule / watch / on-event) → REUSES a matching flow or CREATES
     one (flow grain follows the connectors' credential ownership),
  4. if nothing fits → DECLINES ("no agent is set up for that — ask a builder").

Per-user integrations trigger a just-in-time **connect**: the concierge relays a login link
(CUGA hosts the OAuth; AP holds the token). Its meta-tools are host-bound.
"""

from __future__ import annotations

import contextvars
import logging
import os
import uuid

try:
    from .principal import Principal, DEFAULT as DEFAULT_PRINCIPAL
    from . import credentials, oauth, perms
except ImportError:  # flat load (offline tests put the events dir on sys.path)
    from principal import Principal, DEFAULT as DEFAULT_PRINCIPAL
    import credentials, oauth, perms

log = logging.getLogger("cuga.events.concierge")

_origin: contextvars.ContextVar[str] = contextvars.ContextVar("origin", default="web:local")
_principal: contextvars.ContextVar = contextvars.ContextVar("principal", default=DEFAULT_PRINCIPAL)

CHAT_STYLE = ("\n\nReply for a chat app: short plain-text lines or simple '- ' bullets. "
              "No markdown tables/headings.")

CONCIERGE_PROMPT = (
    "You are the runtime concierge for an event-driven agent platform. Agents are PRE-BUILT by a "
    "builder — you NEVER create agents or choose their tools. Keep replies short.\n"
    "STEP 1 — ALWAYS call list_capabilities first: the agents that exist + the channels and "
    "integrations each one is wired for. Only ever act through an agent that is LISTED.\n"
    "STEP 2 — decide and act:\n"
    "  • Immediate question an existing agent can answer → call answer_now(agent, task) and relay it.\n"
    "  • Standing request → call find_or_create_flow(agent, kind, prompt, source=..., event=...). "
    "Choose kind CAREFULLY:\n"
    "      – push — watching an app for a NEW ITEM the agent has an integration for: gmail new-email, "
    "box new-file, github new-PR ('when a new email/file/PR…', 'whenever X lands in my inbox/Box/repo'). "
    "ALWAYS use push for this (pass source=gmail|box|github). Only push delivers the item's CONTENT to "
    "the agent; poll/cron CANNOT fetch it, so NEVER use them to watch an app's new items. If a LISTED "
    "agent already has that integration (e.g. mailbot [integrations: gmail]), YOU arm the push flow now "
    "via find_or_create_flow — do NOT decline or say a builder must add a trigger.\n"
    "      – cron — a fixed clock schedule ('every N minutes', 'daily at 9am').\n"
    "      – poll — re-check a value on a schedule and act only on change/threshold ('when the price crosses X').\n"
    "    It reuses a matching flow if one already exists, else creates it.\n"
    "  • NOTHING listed fits → DECLINE briefly: say no agent is set up for that and to ask a builder "
    "to create one. Do NOT invent an agent or a capability.\n"
    "STEP 3 — pick the agent BY CAPABILITY from the list only (a finance agent for any price, geo "
    "for any country fact). Never name an agent that isn't listed.\n"
    "If a tool reply starts with 'CONNECT NEEDED', relay that login link to the user verbatim and "
    "stop — they must connect their account first.\n"
    "Confirm in one line, stating only what the tool result actually says." + CHAT_STYLE)


def _slash_parse(text: str) -> dict | None:
    """SLASH COMMANDS — an explicit "make me a flow" from ANY surface (web chat or a channel; both
    call ``run``). The advertised command is **``/automate <what>``** — one command whose ROUTER (the
    heuristic classifier) picks push vs cron vs poll from the phrasing. The five mode-specific
    commands (``/watch|/schedule|/cron|/poll|/push``) are kept as hidden power-user overrides that
    FORCE a mode. DETERMINISTIC either way — the arm bypasses the LLM entirely (no flaky mode pick, no
    decline). Returns None when it isn't a slash command (normal NL routing then applies)."""
    import re
    from . import classify
    m = re.match(r"\s*/(automate|watch|schedule|cron|poll|push)\b\s*(.*)", text or "", re.I | re.S)
    if not m:
        return None
    cmd, rest = m.group(1).lower(), (m.group(2) or "").strip()
    if not rest:
        return {"cmd": cmd, "error": (f"/{cmd}: tell me WHAT to automate — e.g. "
                                      f"`/{cmd} summarize new emails and message me`.")}
    # /automate and /watch let the router decide; the rest force a specific mode.
    forced = {"cron": "CRON", "schedule": "CRON", "poll": "POLL", "push": "PUSH"}.get(cmd)
    d = classify.decision(rest)
    mode = forced or (d.get("mode") if d.get("mode") in ("CRON", "POLL", "PUSH") else "PUSH")
    kind = {"CRON": "cron", "POLL": "poll", "PUSH": "push"}[mode]
    out = {"cmd": cmd, "kind": kind, "utterance": rest}
    if kind == "push":
        # /watch may force PUSH even when the classifier read the phrasing as NOW, so run the
        # source detector directly (not via decision(), which only fills source when mode==PUSH).
        se = d.get("source") and (d.get("source"), d.get("event")) or classify.source_of(rest)
        out["source"], out["event"] = (se if se else (None, None))
    else:
        cad = d.get("cadence") or {}
        if cad.get("cron"):
            out["cron"] = cad["cron"]
        elif cad.get("interval_seconds"):
            out["every_minutes"] = max(1, int(cad["interval_seconds"]) // 60)
    return out


def _resolve_agent(agents, kind: str, source: str | None, utterance: str) -> str | None:
    """Deterministically pick the pre-built agent for a slash command — no LLM. PUSH: the agent must
    have the integration for ``source``; then rank candidates by keyword overlap with the utterance
    (so 'summarize emails' → mailbot, not resume_judge). CRON/POLL: rank all agents the same way."""
    import re
    u = (utterance or "").lower()
    # the classifier may name a source by its sub-trigger (github_pr/github_issue); agents declare the
    # base integration app (github), so normalize before matching.
    base = {"github_pr": "github", "github_issue": "github"}.get(source, source)
    if kind == "push" and base:
        cands = [a for a in agents if any((i.get("app") == base) for i in (a.integrations or []))]
    else:
        cands = list(agents)
    if not cands:
        return None
    if len(cands) == 1:
        return cands[0].name
    words = set(re.findall(r"[a-z]{3,}", u))

    def score(a) -> int:
        hay = (f"{a.name} {(a.prompt or '')[:160]} {' '.join(a.mcp_servers or [])} "
               f"{' '.join(i.get('app', '') for i in (a.integrations or []))}").lower()
        s = sum(1 for w in words if w in hay)
        if a.name in u or a.name.replace("_", " ") in u:      # explicit name mention wins
            s += 5
        return s
    return max(cands, key=score).name


def _owner_scope(spec, p: Principal) -> str:
    """Grain follows credentials: tenant-wide if all connectors are shared, else the full user
    scope when any integration is per-user (that flow is necessarily per-user)."""
    per_user = any(credentials.is_per_user(i.get("ownership", "per-user"))
                   for i in (spec.integrations or []))
    return p.scope if per_user else p.tenant_id


async def _connect_needed(spec, p: Principal, engine) -> tuple[str, str] | None:
    """First per-user integration on the agent the user hasn't connected → (app, connect_msg)."""
    if engine is None:
        return None
    for integ in (spec.integrations or []):
        app, ownership = integ.get("app"), integ.get("ownership", "per-user")
        if not credentials.is_per_user(ownership):
            continue                       # shared → the builder connected it once
        ext = credentials.connection_external_id(app, ownership, p)
        try:
            grain = getattr(engine, "project_grain", "tenant")
            exists = await engine.connection_exists(ext, project_name=p.ap_project_name(grain))
        except Exception:  # noqa: BLE001
            exists = False
        if exists:
            continue
        if oauth.connect_kind(app) == "oauth" and oauth.is_configured(app):
            url = (f"{oauth.public_base()}/api/events/connect/{app}"
                   f"?scope={p.scope}&agent={spec.name}")
            return app, f"connect your {app}: {url}"
        if oauth.connect_kind(app) == "token":
            return app, (f"connect your {app}: paste your {app} token in the Studio "
                         f"(Integrations → {app} → Connect)")
        return app, (f"{app} isn't configured for OAuth on this deployment "
                     f"(set EVENTS_OAUTH_{app.upper()}_CLIENT_ID/SECRET)")
    return None


def make_concierge_tools(runtime, store=None, engine=None, users=None):
    """Build the concierge's host-bound router tools. ``users`` (UserStore) gates per-agent
    access by the caller's roles; without it, all agents are usable (backward compatible)."""
    from langchain_core.tools import BaseTool, tool

    def _roles(p) -> list:
        if users is None:
            return []
        u = users.get(p.user_id, p.tenant_id)
        return u.roles if u else ["user"]

    @tool
    async def list_capabilities() -> str:
        """List the PRE-BUILT agents THIS USER may use, and each one's channels + integrations.
        Pick an agent from here; if none fits, tell the user to ask a builder."""
        p = _principal.get()
        # agents are TENANT-shared (agent_scope); execution stays per-user (p.scope)
        agents = [a for a in runtime.list_agents(scope=p.agent_scope) if a.name != "concierge"]
        agents = perms.visible_agents(agents, _roles(p), p.user_id)   # permission filter
        if not agents:
            return "No agents are available to you. Ask a builder to create or grant one."
        lines = []
        for a in agents:
            integ = ", ".join(f"{i['app']}({i.get('ownership', 'per-user')})"
                              for i in (a.integrations or [])) or "none"
            lines.append(f"  - {a.name}: {(a.prompt or '').splitlines()[0][:70]} "
                         f"[tools: {', '.join(a.mcp_servers) or 'none'}] "
                         f"[channels: {', '.join(a.channels) or 'web'}] [integrations: {integ}]")
        return "PRE-BUILT AGENTS (use one of these; do not invent others):\n" + "\n".join(lines)

    @tool
    async def answer_now(agent: str, task: str) -> str:
        """Run an EXISTING agent ONCE now and return its answer (the immediate-question path)."""
        p = _principal.get()
        spec = runtime.get_agent(agent, scope=p.agent_scope)
        if spec is None:
            return f"error: no agent named '{agent}'. Choose one from list_capabilities."
        if not perms.can_use(spec, _roles(p), p.user_id):
            return f"error: you don't have access to '{agent}'."
        connect = await _connect_needed(spec, p, engine)
        if connect:
            return f"CONNECT NEEDED — {connect[1]}"
        origin = _origin.get()
        try:
            # run the tenant agent, but memory is per-USER (thread_id carries p.scope)
            answer = await runtime.run(agent, p.thread(f"{origin}:{agent}"), task, scope=p.agent_scope)
        except Exception as e:  # noqa: BLE001
            return f"error: run failed ({e})."
        log.info("concierge answer_now agent=%s scope=%s", agent, p.scope)
        return f"ANSWER from {agent}: {answer}"

    tools: list[BaseTool] = [list_capabilities, answer_now]

    if engine is not None and store is not None:
        from .subscriptions import Subscription

        @tool
        async def find_or_create_flow(agent: str, kind: str, prompt: str,
                                      every_minutes: int | None = None, cron: str | None = None,
                                      source: str | None = None, event: str | None = None,
                                      deliver_to: str | None = None) -> str:
            """Reuse-or-create a STANDING flow for an EXISTING agent. kind='cron'|'poll'|'push'.
            cron/poll: every_minutes OR cron (UTC). push: source (box|github|gmail) + event.
            Reuses a matching flow (agent+source+cadence+sink+owner) instead of duplicating."""
            p = _principal.get()
            spec = runtime.get_agent(agent, scope=p.agent_scope)
            if spec is None:
                return f"error: no agent named '{agent}'. Choose one from list_capabilities."
            if not perms.can_use(spec, _roles(p), p.user_id):
                return f"error: you don't have access to '{agent}'."
            # where does the reply go? explicit deliver_to, else the CHANNEL the caller asked from
            # ('send me' → origin thread 'gw:<channel>:<native>' delivers back there), else the
            # agent's first configured channel.
            from . import flows
            origin = _origin.get() or ""
            o_channel, o_native = "", ""
            if origin.startswith("gw:"):
                _parts = origin.split(":", 2)
                if len(_parts) == 3:
                    o_channel, o_native = _parts[1], _parts[2]
            from . import delivery
            sink = deliver_to or o_channel or (spec.channels[0] if spec.channels else "web")
            # Deliver to the origin channel only when we have the caller's native id and it's a
            # known channel connector. Then split by BACKEND: an AP-backed channel gets an AP send
            # step; a DIRECT channel (e.g. Slack) has CUGA send it — no AP connection, no send step.
            _chan_sink = sink if (sink in flows.CHANNELS and sink == o_channel and o_native) else None
            _direct = bool(_chan_sink and delivery.is_direct(_chan_sink))
            deliver_channel = None if _direct else _chan_sink          # AP send step (ap channels)
            deliver_target = o_native if deliver_channel else None
            deliver_connection = (credentials.connection_external_id(deliver_channel, "per-user", p)
                                  if deliver_channel else None)
            deliver_direct_channel = _chan_sink if _direct else None   # CUGA-side send (direct)
            deliver_direct_target = o_native if _direct else None
            # just-in-time connect for a per-user integration the flow (or agent) needs
            connect = await _connect_needed(spec, p, engine)
            if connect:
                return f"CONNECT NEEDED — {connect[1]}"
            # dedup identity — grain follows credentials (tenant-wide vs per-user)
            cadence = cron or (f"{every_minutes}m" if every_minutes else (event or "tick"))
            dedup_key = f"{agent}|{source or 'time'}|{cadence}|{sink}|{_owner_scope(spec, p)}"
            existing = store.find_by_dedup_key(dedup_key)
            if existing:
                nm = f"\"{existing.flow_name}\" " if getattr(existing, "flow_name", "") else ""
                return (f"REUSING existing flow {nm}({existing.mode}) for {agent} → {sink} "
                        f"(subscription {existing.id}). Nothing new created.")
            origin = _origin.get()
            if kind == "push":
                if not source:
                    return "error: push needs a source (box|github|gmail)."
                # the integration's per-user connection is wired as the trigger auth (required to publish)
                _own = next((i.get("ownership", "per-user") for i in (spec.integrations or [])
                             if i.get("app") == source), "per-user")
                push_conn = credentials.connection_external_id(source, _own, p)
                flow_name = f"push-{source}-{agent}"
                try:
                    grain = getattr(engine, "project_grain", "tenant")
                    ap_flow_id = await engine.create_push_flow(
                        source=source, event=event or "new_file", agent=agent,
                        thread_id=p.thread(origin), prompt=prompt,
                        project_name=p.ap_project_name(grain), scope=p.scope,
                        connection=push_conn, name=flow_name)
                except Exception as e:  # noqa: BLE001
                    return f"error: couldn't arm push flow ({e})."
                sub = Subscription(id=f"{agent}-{uuid.uuid4().hex[:6]}", mode="PUSH",
                                   target_agent=agent, tenant=p.scope, backend=spec.backend,
                                   source_type="integration", source_connector=source,
                                   ap_flow_id=ap_flow_id, deliver_to=[sink],
                                   thread_id=p.thread(origin), prompt=prompt, dedup_key=dedup_key,
                                   flow_name=flow_name)
                store.upsert(sub)
                log.info("concierge armed push agent=%s src=%s flow=%s", agent, source, ap_flow_id)
                return (f"ARMED push ({source}/{event or 'new_file'}) for {agent} → {sink}. "
                        f"Flow name: \"{flow_name}\" (subscription {sub.id}).")
            if kind not in ("cron", "poll"):
                return "error: kind must be cron, poll, or push."
            run_prompt = prompt + ("" if kind == "cron" else
                                   " Only report if it changed since last time; else say nothing changed.")
            origin = _origin.get()
            interval = (every_minutes * 60) if every_minutes else None
            cadence_tag = cron.replace(" ", "_") if cron else (f"{every_minutes}m" if every_minutes else kind)
            flow_name = f"ea:{kind}-{agent}-{cadence_tag}-{uuid.uuid4().hex[:4]}"
            try:
                grain = getattr(engine, "project_grain", "tenant")
                ap_flow_id = await engine.create_schedule_flow(
                    name=flow_name, agent=agent,
                    thread_id=p.thread(origin), prompt=run_prompt, cron=cron,
                    interval_seconds=interval, deliver=True,
                    project_name=p.ap_project_name(grain), scope=p.scope,
                    deliver_channel=deliver_channel, deliver_target=deliver_target,
                    deliver_connection=deliver_connection,
                    deliver_direct_channel=deliver_direct_channel,
                    deliver_direct_target=deliver_direct_target)
            except Exception as e:  # noqa: BLE001
                return f"error: couldn't arm flow ({e})."
            sub = Subscription(id=f"{agent}-{uuid.uuid4().hex[:6]}", mode=kind.upper(),
                               target_agent=agent, tenant=p.scope, backend=spec.backend,
                               source_type="time", source_connector="cron", ap_flow_id=ap_flow_id,
                               deliver_to=[sink], thread_id=p.thread(origin), prompt=run_prompt,
                               dedup_key=dedup_key, flow_name=flow_name)
            store.upsert(sub)
            log.info("concierge armed %s agent=%s flow=%s tenant=%s", kind, agent, ap_flow_id, p.scope)
            return (f"ARMED {kind} for {agent} → {sink}. "
                    f"Flow name: \"{flow_name}\" (subscription {sub.id}).")

        tools.append(find_or_create_flow)

    return tools


class Concierge:
    """The concierge as a react agent whose tools are the host-bound router meta-tools."""

    def __init__(self, runtime, store=None, engine=None, model_factory=None, users=None):
        self._runtime = runtime
        self._model_factory = model_factory
        self._tools = make_concierge_tools(runtime, store, engine, users=users)
        self._graph = None

    def _build(self):
        from langgraph.checkpoint.memory import MemorySaver
        from langgraph.prebuilt import create_react_agent
        if self._model_factory is None:
            from .llm import default_model_factory
            self._model_factory = default_model_factory
        model = self._model_factory(None)
        self._graph = create_react_agent(model, self._tools, prompt=CONCIERGE_PROMPT,
                                         checkpointer=MemorySaver())

    async def run(self, thread_id: str, text: str, principal: Principal | None = None) -> str:
        from langchain_core.messages import HumanMessage
        if self._graph is None:
            self._build()
        p = principal or DEFAULT_PRINCIPAL
        # /watch|/schedule|/cron|/poll|/push → deterministic arm (bypasses the LLM entirely)
        parsed = _slash_parse(text)
        if parsed is not None:
            return await self._arm_slash(thread_id, p, parsed)
        t_origin = _origin.set(thread_id)
        t_princ = _principal.set(p)
        try:
            res = await self._graph.ainvoke(
                {"messages": [HumanMessage(content=text)]},
                config={"configurable": {"thread_id": p.thread(thread_id)}})
        finally:
            _origin.reset(t_origin)
            _principal.reset(t_princ)
        return res["messages"][-1].content or ""

    async def _arm_slash(self, thread_id: str, p, parsed: dict) -> str:
        """Arm a slash flow. The MODE is always deterministic (the router). The AGENT is resolved by
        the method each mode is good at:
          • PUSH  → DETERMINISTIC (filter agents by the integration for the source). This is the case
            the LLM fumbles (it won't believe mailbot can push-watch gmail), so we never involve it.
          • CRON/POLL → the LLM picks the agent (a domain judgment it does well — 'bitcoin'→pricebot,
            'market brief'→market_briefer), but with the MODE FORCED so it can't mis-route or decline."""
        if parsed.get("error"):
            return parsed["error"]
        kind = parsed["kind"]
        if kind in ("cron", "poll"):
            directive = (f"[/automate — arm a STANDING {kind.upper()} flow now: call "
                         f"find_or_create_flow(kind={kind}, …) with the best-matching pre-built agent "
                         f"from list_capabilities. Do NOT answer_now and do NOT decline.] "
                         f"{parsed['utterance']}")
            return await self.run(thread_id, directive, p)   # LLM picks the agent; mode is forced
        # PUSH — deterministic agent from the integration filter
        agents = [a for a in self._runtime.list_agents(scope=p.agent_scope) if a.name != "concierge"]
        agent = _resolve_agent(agents, kind, parsed.get("source"), parsed["utterance"])
        if agent is None:
            names = ", ".join(a.name for a in agents) or "none"
            return (f"/{parsed['cmd']}: no agent is wired for that source. Available: {names}.")
        tool = next((t for t in self._tools if t.name == "find_or_create_flow"), None)
        if tool is None:
            return "Flow arming isn't available (Activepieces not configured)."
        source = parsed.get("source")
        if not source:   # infer from the resolved agent's integrations (first push-capable app)
            spec = next((a for a in agents if a.name == agent), None)
            apps = [i.get("app") for i in (spec.integrations or [])] if spec else []
            source = next((s for s in ("gmail", "box", "github") if s in apps),
                          (apps[0] if apps else None))
        args = {"agent": agent, "kind": "push", "prompt": parsed["utterance"],
                "source": source, "event": parsed.get("event")}
        t_origin = _origin.set(thread_id)
        t_princ = _principal.set(p)
        try:
            return await tool.ainvoke(args)
        finally:
            _origin.reset(t_origin)
            _principal.reset(t_princ)


# WORKER_BACKEND kept for import-compatibility (main.py + seed read it).
WORKER_BACKEND = os.environ.get("EVENTS_WORKER_BACKEND", "cuga")
