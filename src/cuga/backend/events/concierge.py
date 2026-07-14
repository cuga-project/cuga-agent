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

import asyncio
import contextvars
import logging
import os
import re
import uuid

try:
    from .principal import Principal, DEFAULT as DEFAULT_PRINCIPAL
    from . import credentials, oauth, perms, triggers as trigger_registry
except ImportError:  # flat load (offline tests put the events dir on sys.path)
    from principal import Principal, DEFAULT as DEFAULT_PRINCIPAL
    import credentials
    import oauth
    import perms
    import triggers as trigger_registry

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
    "  • Standing request → call find_or_create_flow(agent, kind, prompt, ...). Pick kind by what "
    "the TRIGGER is — a clock, a value to re-check, or an app event:\n"
    "      – cron — a fixed clock schedule: 'every day at 9am', 'every weekday at 8am', 'every hour'. "
    "Pass cron=... or every_minutes=N. NO integration needed.\n"
    "      – poll — re-check something on an interval and report only on a change/threshold: 'watch "
    "bitcoin every 2 minutes and ping me on any move', 'check the weather hourly and tell me only if "
    "it rains'. Pass every_minutes=N. **NO integration needed** — the agent re-runs its own tools. "
    "Any 'watch X every N / notify me when it changes' is POLL, whatever X is.\n"
    "      – push — an APP raises an event (see the trigger vocabulary below). Pass source=<app> AND "
    "event=<trigger>: 'when a new email arrives', 'when a PR opens on owner/repo', 'when a message "
    "gets a :bug: reaction'. Only push delivers the item's CONTENT to the agent, so NEVER use "
    "cron/poll to watch an app's events — and never use push for a plain clock or a value watch.\n"
    "    It reuses a matching flow if one already exists, else creates it.\n"
    "  • NOTHING listed fits → DECLINE briefly: say no agent is set up for that and to ask a builder "
    "to create one. Do NOT invent an agent or a capability.\n"
    "STEP 3 — pick the agent BY CAPABILITY from the list only (a finance agent for any price, geo "
    "for any country fact). Never name an agent that isn't listed. If a LISTED agent already has the "
    "integration a push needs (e.g. mailbot [integrations: gmail]), arm it NOW — do not decline or "
    "say a builder must add a trigger.\n"
    "If a tool reply starts with 'CONNECT NEEDED', relay that login link to the user verbatim and "
    "stop — they must connect their account first.\n"
    "Confirm in one line, stating only what the tool result actually says.\n"
    "\n"
    "TRIGGER VOCABULARY — **only for kind=push**. Ignore this entire block for cron and poll.\n"
    "  Format: app: event(needs <slot>) … ; * = that app's default event.\n"
    "  " + trigger_registry.prompt_vocabulary() + "\n"
    "  Slots: repo='owner/repo' (every github event) · label=<gmail label> (new_labeled_email) · "
    "emoji / pattern / watch_channel (slack + discord filters) · folder (box)."
    + CHAT_STYLE)


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


def _resolve_agent(agents, kind: str, source: str | None, utterance: str,
                   event: str | None = None) -> str | None:
    """Deterministically pick the pre-built agent for a slash command — no LLM. PUSH: the agent must
    have the integration for ``source``; then rank candidates by keyword overlap with the utterance
    (so 'summarize emails' → mailbot, not resume_judge). CRON/POLL: rank all agents the same way."""
    import re
    u = (utterance or "").lower()
    # the classifier may name a source by its sub-trigger (github_pr/github_issue); agents declare the
    # base integration app (github), so normalize before matching.
    base = {"github_pr": "github", "github_issue": "github"}.get(source, source)
    if kind == "push" and base:
        # trigger-grain: an integrations entry may carry "triggers": [event, …] to say WHICH of the
        # app's events the agent handles; no list = all of them (legacy declarations unchanged).
        ev = (event or "").lower()

        def _handles(a) -> bool:
            for i in (a.integrations or []):
                if i.get("app") != base:
                    continue
                trigs = i.get("triggers")
                if not trigs or not ev or ev in trigs:
                    return True
            return False
        cands = [a for a in agents if _handles(a)]
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


async def _connect_needed(spec, p: Principal, engine,
                          only_app: str | None = None) -> tuple[str, str] | None:
    """First per-user integration on the agent the user hasn't connected → (app, connect_msg).

    ``only_app`` scopes the gate to ONE app (the push source): the agent holds no credentials, so
    arming a github watcher must not demand the agent's unrelated integrations be connected."""
    if engine is None:
        return None
    for integ in (spec.integrations or []):
        app, ownership = integ.get("app"), integ.get("ownership", "per-user")
        if only_app is not None and app != only_app:
            continue
        if not credentials.is_per_user(ownership):
            continue                       # shared → the builder connected it once
        if app == "box" and os.environ.get("EVENTS_BOX_BACKEND") == "direct":
            continue                       # DIRECT box polls with BOX_DEV_TOKEN — no AP connection
        ext = credentials.connection_external_id(app, ownership, p)
        # Ask AP whether the connection exists — and DISTINGUISH "no" from "AP didn't answer".
        # A bare `except: exists = False` made an unreachable/slow/rate-limited AP look exactly
        # like a never-connected account, so the user was told to "connect your credentials" for a
        # credential they had already connected (proven: 5 of 14 github arms in one burst).
        # One retry absorbs a transient blip; a persistent failure is reported as an AP problem.
        exists, ap_error = False, ""
        grain = getattr(engine, "project_grain", "tenant")
        for _attempt in range(2):
            try:
                exists = await engine.connection_exists(ext, project_name=p.ap_project_name(grain))
                ap_error = ""
                break
            except Exception as e:  # noqa: BLE001
                ap_error = str(e)
                await asyncio.sleep(0.4)
        if ap_error:
            log.warning("connect gate: AP unreachable checking %s (%s) — NOT reporting "
                        "connect-needed", app, ap_error[:120])
            return app, (f"I couldn't verify your {app} connection because Activepieces didn't "
                         f"answer ({ap_error[:120]}). This is NOT a missing credential — check AP "
                         f"is up (`make status` / `make tunnels`) and try again.")
        if exists:
            continue
        if oauth.connect_kind(app) == "oauth" and oauth.is_configured(app):
            url = (f"{oauth.public_base()}/api/events/connect/{app}"
                   f"?scope={p.scope}&agent={spec.name}")
            return app, f"connect your {app}: {url}"
        if oauth.connect_kind(app) == "token":
            hint = ""
            if app == "github":
                # PR/issue triggers create a repo WEBHOOK, so the token must be able to manage hooks.
                # Give the EXACT, correct guidance (classic uses scopes, fine-grained uses named
                # permissions — don't mix them) so it can't be relayed as misleading instructions.
                hint = (" — for PR/issue watchers the token must manage repo webhooks. Easiest: a "
                        "CLASSIC PAT at github.com/settings/tokens/new with the **`repo`** scope "
                        "(covers webhooks + PR read). Or a FINE-GRAINED PAT at "
                        "github.com/settings/personal-access-tokens/new, resource owner = the repo's "
                        "owner, Only-select-repositories = that repo, then Repository permissions → "
                        "**Webhooks: Read and write** + **Pull requests: Read** (+ Contents: Read)")
            return app, (f"connect your {app}: paste your {app} token in the Studio "
                         f"(Integrations → {app} → Connect){hint}")
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
        from .subscriptions import Subscription, DuplicateSubscription

        @tool
        async def find_or_create_flow(agent: str, kind: str, prompt: str,
                                      every_minutes: int | None = None, cron: str | None = None,
                                      source: str | None = None, event: str | None = None,
                                      deliver_to: str | None = None, repo: str | None = None,
                                      label: str | None = None, folder: str | None = None,
                                      emoji: str | None = None, pattern: str | None = None,
                                      watch_channel: str | None = None) -> str:
            """Reuse-or-create a STANDING flow for an EXISTING agent. kind='cron'|'poll'|'push'.
            cron/poll: every_minutes OR cron (UTC). push: source (the app) + event (WHICH of the
            app's triggers — see the trigger vocabulary in your instructions; omit for the app's
            default). Slots when the trigger needs them: repo='owner/repo' (github), label (gmail),
            folder (box id), emoji / pattern / watch_channel (slack/discord filters).
            Reuses a matching flow (agent+source+event+cadence+sink+owner) instead of duplicating."""
            from . import triggers as _tr
            p = _principal.get()
            spec = runtime.get_agent(agent, scope=p.agent_scope)
            if spec is None:
                return f"error: no agent named '{agent}'. Choose one from list_capabilities."
            if not perms.can_use(spec, _roles(p), p.user_id):
                return f"error: you don't have access to '{agent}'."
            # canonicalize the event kind (LLM may pass 'new-email'; the envelope only accepts
            # 'new_email') so the AP flow body + dedup_key are built with the valid form.
            if event:
                from .envelope import normalize_kind
                event = normalize_kind(event)
            # ── the registry validation gate (PUSH only) ─────────────────────────────────
            # The LLM proposes (source, event, slots); the REGISTRY disposes. An unknown trigger or
            # a missing required slot comes back as a message BEFORE anything is built — the old
            # path silently armed a flow on a nonexistent trigger that could never publish.
            trig_row = None
            config: dict = {k: v for k, v in (("repo", repo), ("label", label), ("folder", folder),
                                              ("emoji", emoji), ("pattern", pattern),
                                              ("channel", watch_channel)) if v}
            if kind == "push" and source:
                # slot back-fill from the utterance (deterministic, before the gate asks)
                if "repo" not in config:
                    m = re.search(r"\b([A-Za-z0-9][\w.-]*)/([A-Za-z0-9][\w.-]*)\b", prompt or "")
                    if m and "." not in m.group(0).split("/")[0]:      # skip URLs/filenames
                        config["repo"] = m.group(0)
                if "label" not in config and source == "gmail":
                    # A QUOTED label is the reliable signal ("when I label an email 'Read-later'").
                    # The old unquoted pattern captured the words right after "label", so
                    # "…label an email 'Read-later'" became label="an email" — a garbage value that
                    # would be sent to Gmail's trigger verbatim.
                    m = re.search(r"['\"\u2018\u2019\u201c\u201d]([\w][\w .\-/]{1,38})"
                                  r"['\"\u2018\u2019\u201c\u201d]", prompt or "")
                    if not m:
                        m = re.search(r"label(?:ed)?\s+(?:as\s+|it\s+)?([\w-]{2,30})",
                                      prompt or "", re.I)
                        _STOP = {"an", "a", "the", "my", "this", "it", "email", "emails",
                                 "message", "messages", "is", "was", "gets"}
                        if m and m.group(1).lower() in _STOP:
                            m = None
                    if m:
                        config["label"] = m.group(1).strip()
                trig_row, problem = _tr.validate(source, event or "", config)
                if trig_row is None:
                    return f"error: {problem}"
                if problem:                          # a required slot is missing → ask, don't arm
                    return problem
                # normalize to the registry's canonical names for everything downstream
                source, event = trig_row.app, trig_row.event
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
            # just-in-time connect for a per-user integration the flow needs. PUSH gates only
            # on the SOURCE app: the agent holds no credentials (AP owns trigger + sink), so a
            # github push watcher must not demand the agent's UNRELATED integrations be connected.
            # cron/poll keep the legacy whole-spec gate (an agent whose CONTENT depends on an
            # integration, e.g. mailbot, still prompts to connect).
            _gate_app = None
            if kind == "push" and source:
                _gate_app = {"github_pr": "github", "github_issue": "github"}.get(source, source)
            connect = await _connect_needed(spec, p, engine, only_app=_gate_app)
            if connect:
                return f"CONNECT NEEDED — {connect[1]}"
            # dedup identity — grain follows credentials (tenant-wide vs per-user)
            cadence = cron or (f"{every_minutes}m" if every_minutes else (event or "tick"))
            # per-watch config (repo/label/…) is part of the identity: watching two repos with the
            # same trigger must be two flows, not a dedup collision.
            _cfg_tag = ",".join(f"{k}={config[k]}" for k in sorted(config)) if config else ""
            dedup_key = (f"{agent}|{source or 'time'}|{cadence}|{_cfg_tag}|{sink}|"
                         f"{_owner_scope(spec, p)}")
            existing = store.find_by_dedup_key(dedup_key)
            if existing:
                nm = f"\"{existing.flow_name}\" " if getattr(existing, "flow_name", "") else ""
                return (f"REUSING existing flow {nm}({existing.mode}) for {agent} → {sink} "
                        f"(subscription {existing.id}). Nothing new created.")
            origin = _origin.get()
            if kind == "push":
                if not source:
                    return "error: push needs a source (box|github|gmail)."
                # The trigger sub-name (github_pr/github_issue) is NOT the connection app — the AP
                # connection is under the BASE app (github). Normalize so the auth references the real
                # connection (else the trigger references a non-existent one and publish fails).
                base_app = {"github_pr": "github", "github_issue": "github"}.get(source, source)
                # DIRECT box: no AP OAuth, no box connection. Arm a schedule→/box/poll watcher that
                # polls Box with BOX_DEV_TOKEN and fires the agent per NEW file (matches
                # EVENTS_BOX_BACKEND=direct — the path the operator actually set up).
                if base_app == "box" and os.environ.get("EVENTS_BOX_BACKEND") == "direct":
                    folder = (os.environ.get("BOX_FOLDER_ID", "") or "0").split(" #", 1)[0].strip() or "0"
                    every = every_minutes or 5
                    # baseline the watermark so ONLY files added AFTER arming fire (not the backlog)
                    try:
                        from . import box_direct
                        seen = await box_direct.new_files_since(folder, None)
                        box_direct.save_since(folder, max((f.get("created_at", "") for f in seen), default=""))
                    except Exception:  # noqa: BLE001
                        pass
                    flow_name = f"box-poll-{agent}"
                    try:
                        grain = getattr(engine, "project_grain", "tenant")
                        ap_flow_id = await engine.create_box_poll_flow(
                            name=flow_name, agent=agent, folder_id=folder,
                            deliver_to=(deliver_direct_channel or (sink if sink in flows.CHANNELS else None)),
                            deliver_target=deliver_direct_target, interval_seconds=every * 60,
                            scope=p.scope, project_name=p.ap_project_name(grain))
                    except Exception as e:  # noqa: BLE001
                        return f"error: couldn't arm box watcher ({e})."
                    sub = Subscription(id=f"{agent}-{uuid.uuid4().hex[:6]}", mode="POLL",
                                       target_agent=agent, tenant=p.scope, backend=spec.backend,
                                       source_type="integration", source_connector="box",
                                       ap_flow_id=ap_flow_id, deliver_to=[sink],
                                       thread_id=p.thread(origin), prompt=prompt, dedup_key=dedup_key,
                                       flow_name=flow_name)
                    store.upsert(sub)
                    log.info("concierge armed DIRECT box watcher agent=%s folder=%s flow=%s",
                             agent, folder, ap_flow_id)
                    return (f"ARMED box watcher (direct poll every {every}m, folder {folder}) for "
                            f"{agent} → {sink}. Flow: \"{flow_name}\" (subscription {sub.id}).")
                # ── DIRECT triggers (slack / discord / telegram watchers) ────────────────
                # CUGA already receives these transports itself (Slack Events API, Discord Gateway,
                # the telegram message stream) — there is NO AP flow and NO AP connection. Arming is
                # just a subscription row; the direct-event dispatcher (direct_events.py) matches
                # incoming events against it and fires the agent through the same /invoke seam.
                if trig_row is not None and trig_row.backend == "direct" and base_app != "box":
                    if base_app == "webhook":
                        hookname = (config.get("pattern") or "my-hook").replace(" ", "-")
                        return ("No arming needed — the generic webhook endpoint is always live. "
                                f"POST JSON to /api/events/hook/{hookname}?agent={agent} (pinned) "
                                "or ?route=1 (concierge picks the agent).")
                    sub = Subscription(id=f"{agent}-{uuid.uuid4().hex[:6]}", mode="PUSH",
                                       target_agent=agent, tenant=p.scope, backend=spec.backend,
                                       source_type="integration", source_connector=base_app,
                                       ap_flow_id=None, deliver_to=[sink],
                                       thread_id=p.thread(origin), prompt=prompt,
                                       dedup_key=dedup_key, flow_name=f"direct-{base_app}-"
                                       f"{(event or 'default').replace('_', '-')}-{agent}",
                                       event=event or "", config=config)
                    try:
                        store.upsert(sub)
                    except DuplicateSubscription:
                        ex = store.find_by_dedup_key(dedup_key)
                        return (f"REUSING existing watcher ({ex.id})." if ex
                                else "REUSING an existing identical watcher.")
                    log.info("concierge armed DIRECT watcher app=%s event=%s agent=%s",
                             base_app, event, agent)
                    _cfg = f" [{', '.join(f'{k}={v}' for k, v in config.items())}]" if config else ""
                    _act = ("" if base_app != "slack" else
                            " (the Slack app must be subscribed to this event type — see "
                            "events_docs/setup/SLACK.md)")
                    return (f"ARMED direct watcher ({base_app}/{event}{_cfg}) for {agent} → {sink}"
                            f"{_act}. Subscription {sub.id}.")
                _own = next((i.get("ownership", "per-user") for i in (spec.integrations or [])
                             if i.get("app") == base_app), "per-user")
                push_conn = credentials.connection_external_id(base_app, _own, p)
                # github triggers REQUIRE a repository (owner/repo) — the registry gate already
                # collected it into config["repo"] (param or utterance) or asked for it.
                src_input, repo_label = None, ""
                if base_app == "github":
                    repo_label = config.get("repo", "")
                    if not repo_label or "/" not in repo_label:
                        return ("Which repo? Name it as owner/repo — e.g. "
                                "`/automate new PRs on psf/requests and summarize them`.")
                    _owner_part, _repo_part = repo_label.split("/", 1)
                    src_input = {"repository": {"owner": _owner_part, "repo": _repo_part}}
                if base_app == "gmail" and config.get("label"):
                    src_input = {"label": config["label"]}
                # the EVENT is part of the name: AP flow creation deletes any same-named flow, so a
                # name without the event meant a 2nd trigger on the same app destroyed the 1st.
                flow_name = f"push-{source}-{(event or 'default').replace('_', '-')}-{agent}"
                try:
                    grain = getattr(engine, "project_grain", "tenant")
                    ap_flow_id = await engine.create_push_flow(
                        source=source, event=event or "new_file", agent=agent,
                        thread_id=p.thread(origin), prompt=prompt,
                        project_name=p.ap_project_name(grain), scope=p.scope,
                        connection=push_conn, source_input=src_input, name=flow_name)
                except Exception as e:  # noqa: BLE001
                    msg = str(e)
                    # github's PR/issue trigger creates a repo WEBHOOK on publish; GitHub rejects it if
                    # the token can't manage webhooks (fine-grained PAT missing "Webhooks" permission,
                    # surfaced by AP as TRIGGER_UPDATE_STATUS / bad credentials). Make that actionable.
                    if base_app == "github" and ("TRIGGER_UPDATE_STATUS" in msg
                            or "credential" in msg.lower() or "webhook" in msg.lower()):
                        # The usual cause is NOT the token's scopes, however much it looks like it.
                        # `@activepieces/piece-github` accepts only OAUTH2 or CUSTOM_AUTH (GitHub
                        # App); it does not accept SECRET_TEXT. CUGA stores a PAT as SECRET_TEXT, AP
                        # accepts the row, and then the piece runs with no usable credential and
                        # GitHub answers "401 Bad credentials" — identical to an under-scoped token.
                        # Verify with: curl $AP_BASE_URL/api/v1/pieces/@activepieces/piece-github
                        # A PAT that creates a webhook fine via `curl` still fails here.
                        # ALWAYS carry the underlying error: this branch fires on any message merely
                        # containing "webhook", so it will otherwise blame the token for a missing
                        # piece, an unreachable AP, or a bad repo name.
                        return (f"GitHub wouldn't arm the watcher on {repo_label}. The most likely "
                                f"cause is that Activepieces' github piece accepts only an OAuth2 (or "
                                f"GitHub App) connection, while this deployment stores a PAT as "
                                f"SECRET_TEXT — the piece then authenticates with nothing and GitHub "
                                f"replies 401, which looks exactly like a scope problem. Check "
                                f"`GET {getattr(engine, 'base', '<AP>')}/api/v1/pieces/"
                                f"@activepieces/piece-github`. If it does list SECRET_TEXT, then the "
                                f"token really does lack **Webhooks: Read and write** (fine-grained) "
                                f"or `admin:repo_hook` + `repo` (classic). "
                                f"Activepieces said: {msg[:250]}")
                    return f"error: couldn't arm push flow ({e})."
                sub = Subscription(id=f"{agent}-{uuid.uuid4().hex[:6]}", mode="PUSH",
                                   target_agent=agent, tenant=p.scope, backend=spec.backend,
                                   source_type="integration", source_connector=source,
                                   ap_flow_id=ap_flow_id, deliver_to=[sink],
                                   thread_id=p.thread(origin), prompt=prompt, dedup_key=dedup_key,
                                   flow_name=flow_name, event=event or "", config=config)
                try:
                    store.upsert(sub)
                except DuplicateSubscription:
                    # a concurrent arm with the same identity won the check-then-write race — the
                    # DB's unique index made us the loser; report reuse, and drop our extra AP flow.
                    try:
                        await engine.delete_flow(ap_flow_id)
                    except Exception:  # noqa: BLE001
                        pass
                    ex = store.find_by_dedup_key(dedup_key)
                    return (f"REUSING existing flow ({ex.mode}) for {agent} → {sink} "
                            f"(subscription {ex.id})." if ex else
                            "REUSING an existing identical flow. Nothing new created.")
                log.info("concierge armed push agent=%s src=%s/%s flow=%s",
                         agent, source, event, ap_flow_id)
                _cfg_note = f" [{', '.join(f'{k}={v}' for k, v in config.items())}]" if config else ""
                return (f"ARMED push ({source}/{event or 'new_file'}{_cfg_note}) for {agent} → "
                        f"{sink}. Flow name: \"{flow_name}\" (subscription {sub.id}).")
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
        agent = _resolve_agent(agents, kind, parsed.get("source"), parsed["utterance"],
                               event=parsed.get("event"))
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
