"""The concierge — the NL→Flow COMPILER in front of THE one agent ("cuga").

SINGLE-AGENT WORLD (events_docs/plans/SUPERVISOR_REFACTOR.md): the concierge does NO agent
routing — there is nothing to route between. Every hand-off and every flow targets ``cuga``
(a supervisor over YAML-defined sub-agents when EVENTS_SUPERVISOR=1, else the plain classic
agent). When an end user chats, the concierge:

  1. understands the intent (deterministic pre-router first — flowspec.py; LLM for ambiguity),
  2. immediate question → runs THE agent and returns the answer,
  3. STANDING request (schedule / watch / on-event) → compiles the TRIGGER (kind, source, event,
     slots — ask-till-legit), validates it against the registry, then REUSES a matching flow or
     CREATES one — always targeting ``cuga``,
  4. unknown trigger / missing slot → a QUESTION, never a broken flow.

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

# THE one addressable agent (supervisor model — events_docs/plans/SUPERVISOR_REFACTOR.md).
# Every flow and every chat hand-off targets it; specialist routing happens INSIDE it.
THE_AGENT = "cuga"

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
    "You are the runtime concierge for an event-driven agent platform. There is exactly ONE "
    "agent: 'cuga' (it routes to its own specialists internally — you NEVER pick a specialist, "
    "never name one, and never create agents). Keep replies short.\n"
    "Decide and act:\n"
    "  • Immediate question → call answer_now(agent='cuga', task) and relay the answer.\n"
    "  • Standing request → call find_or_create_flow(agent='cuga', kind, prompt, ...). Pick kind "
    "by what the TRIGGER is — a clock, a value to re-check, or an app event:\n"
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
    "Never decline because of agent capability — 'cuga' handles everything; if a push trigger "
    "exists in the vocabulary below, arm it NOW.\n"
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




def _strip_cadence(prompt: str) -> str:
    """Remove recurrence phrasing from a cron/poll prompt so the per-tick agent run doesn't try to
    implement the schedule itself (loop/sleep → execution timeout). The AP schedule owns cadence."""
    t = prompt or ""
    # "every 5 minutes", "every 2 min", "every hour", "every day at 9am", "hourly", "each morning"
    t = re.sub(r"\bevery\s+\d+\s*(seconds?|secs?|minutes?|mins?|hours?|hrs?|days?)\b", "", t, flags=re.I)
    t = re.sub(r"\bevery\s+(second|minute|hour|day|morning|weekday|week|friday|monday)\b", "", t, flags=re.I)
    t = re.sub(r"\b(hourly|daily|weekly|continuously|periodically|repeatedly)\b", "", t, flags=re.I)
    t = re.sub(r"\bat\s+\d{1,2}(:\d\d)?\s*(am|pm)?\b", "", t, flags=re.I)
    # imperative recurrence verbs → single-check phrasing
    t = re.sub(r"\b(keep (an eye on|watching|monitoring|checking)|continuously (watch|monitor|check))\b",
               "check", t, flags=re.I)
    t = re.sub(r"\bmonitor(ing)?\b", "check", t, flags=re.I)
    t = re.sub(r"\s{2,}", " ", t).strip(" ,.;:")
    return t or prompt


# Words whose presence in a rewrite means cadence LEAKED through — the leaked prompt would make
# the per-tick agent implement the schedule itself, so a leaking LLM answer is discarded in favor
# of the (corpus-proven) regex.
_CADENCE_LEAK = re.compile(
    r"\b(every\s+(\d+|second|minute|hour|day|morning|weekday|week|month|monday|tuesday|wednesday|"
    r"thursday|friday|saturday|sunday)|hourly|daily|weekly|monthly|continuously|periodically|"
    r"repeatedly|keep\s+(watching|checking|monitoring|an\s+eye)|monitor(ing)?)\b", re.I)

_REWRITE_SYSTEM = (
    "You rewrite a user's recurring-task request into the instruction for ONE run of that task. "
    "The schedule already exists elsewhere — your rewrite must contain NO recurrence or cadence "
    "phrasing (no 'every X minutes', 'daily', 'at 9am', 'keep watching', 'monitor', 'track'). "
    "Rephrase continuous verbs (watch/monitor/track) as a single check done once, right now. "
    "PRESERVE everything else exactly: the subject, any condition ('only if ...', 'when it "
    "changes'), and any delivery instruction (where/how to send the result). Do NOT answer the "
    "request or add commentary. Reply with ONLY the rewritten instruction, no quotes."
)

_cadence_model = None  # cached chat model; tests inject a fake here


async def _single_shot_task(prompt: str) -> str:
    """The cadence stripper: LLM rewrite of a cron/poll utterance into its one-run task.

    Runs once at ARM time (never per tick), so the cost is one LLM call per flow. The regex
    ``_strip_cadence`` is the fallback — used when the LLM is disabled (EVENTS_CADENCE_LLM=0),
    unavailable, times out, or its answer still leaks cadence words / balloons in size. Either
    way the "ONE run, do NOT loop" framing wraps the result, so a miss degrades gracefully."""
    if os.environ.get("EVENTS_CADENCE_LLM", "1") != "1":
        return _strip_cadence(prompt)
    global _cadence_model
    try:
        from langchain_core.messages import HumanMessage, SystemMessage
        if _cadence_model is None:
            try:
                from .llm import default_model_factory
            except ImportError:  # flat load (offline tests)
                from llm import default_model_factory
            _cadence_model = default_model_factory(None)
        res = await asyncio.wait_for(
            _cadence_model.ainvoke(
                [SystemMessage(content=_REWRITE_SYSTEM), HumanMessage(content=prompt)]),
            timeout=float(os.environ.get("EVENTS_CADENCE_LLM_TIMEOUT", "20")))
        out = (res.content or "").strip().strip('"').strip()
        if out and len(out) <= 4 * max(len(prompt), 40) and not _CADENCE_LEAK.search(out):
            return out
        log.warning("cadence LLM rewrite rejected (leak/size) — regex fallback: %r", out[:120])
    except Exception as e:  # noqa: BLE001
        log.warning("cadence LLM rewrite failed (%s) — regex fallback", str(e)[:120])
    return _strip_cadence(prompt)


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
        """What the ONE agent ('cuga') can do: its specialists' domains and the trigger vocabulary
        live in your instructions. Kept for compatibility; there is nothing to pick."""
        n = len(runtime.list_agents(scope=_principal.get().agent_scope))
        return ("ONE agent: 'cuga' — it routes internally"
                + (f" across {n} specialists" if n > 1 else "")
                + ". Always use agent='cuga'.")

    @tool
    async def answer_now(agent: str, task: str) -> str:
        """Run THE agent ('cuga') ONCE now and return its answer (the immediate-question path)."""
        p = _principal.get()
        agent = THE_AGENT                       # single-agent world: the id is not a choice
        origin = _origin.get()
        try:
            # memory is per-USER conversation (thread_id carries p.scope)
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
            # SUPERVISOR MODEL: 'cuga' is always a valid target — the one agent exists by
            # construction even when the runtime has no roster row for it (classic mode).
            spec = runtime.get_agent(agent, scope=p.agent_scope)
            if spec is None and agent == THE_AGENT:
                from .runtime import AgentSpec as _Spec
                spec = _Spec(name=THE_AGENT, backend="cuga")
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
            # THE FIRED PROMPT IS SINGLE-SHOT. The SCHEDULE owns the recurrence — the agent runs
            # once per tick. Leaving "every 5 minutes"/"monitor"/"keep watching" in the prompt made
            # the agent try to implement the loop ITSELF (sleep + re-check) and hit the execution
            # timeout. LLM rewrite (regex fallback) + explicit one-run framing.
            task = await _single_shot_task(prompt)
            run_prompt = (
                "This is ONE run of a scheduled task (the schedule handles recurrence — do NOT "
                "loop, sleep, or wait). Do the check ONCE, right now, and report:\n" + task
                + ("" if kind == "cron" else
                   "\nThis is a POLL: report ONLY if the value changed since the last run; "
                   "otherwise say nothing changed."))
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
        # NL pre-router: fill-the-blanks / ask-till-legit. Arms ONLY a HIGH-confidence, registry-
        # validated PUSH spec (or asks its one missing question). Anything less confident falls
        # through to the LLM path below, exactly as before — the pre-router never guesses.
        pre = await self._pre_route(thread_id, p, text)
        if pre is not None:
            return pre
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

    async def _pre_route(self, thread_id: str, p, text: str) -> str | None:
        """The deterministic NL→Flow path. None = not ours, run the LLM (today's behavior).

        Two jobs:
          1. **Ask-till-legit** — if this thread has a parked question, try the reply as its
             answer; a reply that isn't one drops the parked spec and routes normally.
          2. **Fast path** — a HIGH-confidence resolved PUSH spec arms deterministically (same
             tool, same gates as the LLM path) or returns its ONE missing-slot question.
        """
        from . import flowspec
        tkey = p.thread(thread_id)
        parked = flowspec.pending_for(tkey)
        if parked is not None:
            spec0, utter0 = parked
            filled = flowspec.fill(spec0, text)
            if filled is not None:
                if filled.ask:                       # still one short (multi-slot triggers)
                    flowspec.park(tkey, filled, utter0)
                    return filled.ask
                return await self._arm_spec(thread_id, p, filled, utter0)
            # not an answer → fall through and let the LLM handle the new message
        spec = flowspec.resolve(text)
        if spec.kind != "push" or spec.confidence != "high":
            return None
        # fast-path only the sources proven through find_or_create_flow; webhook arms via
        # /api/events/hook and telegram is a channel first — the LLM path handles both as before
        if spec.source in ("webhook", "telegram"):
            return None
        if spec.ask:
            flowspec.park(tkey, spec, text)
            return spec.ask
        return await self._arm_spec(thread_id, p, spec, text)

    async def _arm_spec(self, thread_id: str, p, spec, utterance: str) -> str | None:
        """Arm a resolved FlowSpec through the SAME find_or_create_flow tool the LLM calls — one
        arming path, two front doors. None (→ LLM path) if the tool isn't available; the
        pre-router must never produce a worse answer than the LLM.

        SUPERVISOR MODEL: the concierge does NOT pick an agent — every flow targets the ONE
        agent, "cuga"; routing to a specialist happens inside it, per wake-up
        (events_docs/plans/SUPERVISOR_REFACTOR.md)."""
        tool = next((t for t in self._tools if t.name == "find_or_create_flow"), None)
        if tool is None:
            return None
        args = {"agent": THE_AGENT, "kind": "push", "prompt": utterance,
                "source": spec.source, "event": spec.event,
                **{k: v for k, v in spec.config.items() if k != "channel"},
                **({"watch_channel": spec.config["channel"]} if "channel" in spec.config else {})}
        t_origin = _origin.set(thread_id)
        t_princ = _principal.set(p)
        try:
            reply = await tool.ainvoke(args)
        finally:
            _origin.reset(t_origin)
            _principal.reset(t_princ)
        return reply

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
                         f"find_or_create_flow(kind={kind}, agent='{THE_AGENT}', …). "
                         f"Do NOT answer_now and do NOT decline.] "
                         f"{parsed['utterance']}")
            return await self.run(thread_id, directive, p)   # cadence via LLM; agent is fixed
        # PUSH — no agent picking (supervisor model): the flow targets THE one agent.
        tool = next((t for t in self._tools if t.name == "find_or_create_flow"), None)
        if tool is None:
            return "Flow arming isn't available (Activepieces not configured)."
        source = parsed.get("source")
        if not source:
            from . import classify
            se = classify.source_of(parsed["utterance"])
            source = se[0] if se else None
        if not source:
            return (f"/{parsed['cmd']}: tell me WHAT to watch (e.g. github/gmail/box/slack) — "
                    f"e.g. `/push when a PR opens on owner/repo, review it`.")
        args = {"agent": THE_AGENT, "kind": "push", "prompt": parsed["utterance"],
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
