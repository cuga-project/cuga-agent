"""FastAPI wiring for the events layer — registered onto the eventing service's own app.

Two new endpoints (the events docs (ARCHITECTURE.md)):
  - ``POST /invoke``         — the seam AP calls back through (X-Gateway-Token).
  - ``POST /api/concierge``  — NL → decide; ``?dry_run=1`` builds the flow without publishing.

``register_events_routes(app, runtime=..., store=...)`` is called from CUGA's ``main.py``
lifespan ONLY when the flag is on, so vanilla CUGA is untouched when off. fastapi is a CUGA
dependency; the dependency-light core doesn't import this module.
"""

from __future__ import annotations

import html
import logging
import os
import re
import time as time_module

from fastapi import Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse

from . import arming as _arming
from . import concierge_plan
from .envelope import Envelope
from .principal import resolve as resolve_principal
from .trace import Trace, new_trace_id

_elog = logging.getLogger("cuga.events")


def _iso(ts: float) -> str:
    """epoch seconds → the ISO shape Activepieces uses (UTC, milliseconds, Z) so NOW runs and AP
    flow-runs sort together as strings in the unified Runs log."""
    from datetime import datetime, timezone

    dt = datetime.fromtimestamp(ts or 0, tz=timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


def _events_env_path():
    """The repo .env (config.py load_dotenv finds it from cwd; $CUGA_REPO overrides)."""
    import pathlib

    repo = os.environ.get("CUGA_REPO") or str(pathlib.Path(__file__).resolve().parents[4])
    return pathlib.Path(repo) / ".env"


def _env_upsert(key: str, value: str) -> None:
    """Set ``KEY=value`` in the repo .env AND in the live process.

    Replaces the existing line (active or commented-out) in place, preserving every other line and
    all comments; appends if absent. Also sets ``os.environ[key]`` so use-time readers (Slack/Box/…)
    pick it up without a restart.
    """
    p = _events_env_path()
    line = f"{key}={value}"
    out, found = [], False
    if p.is_file():
        for raw in p.read_text().splitlines():
            s = raw.lstrip()
            bare = s[1:].lstrip() if s.startswith("#") else s  # tolerate a commented-out line
            if "=" in bare and bare.split("=", 1)[0].strip() == key:
                if not found:
                    out.append(line)
                    found = True  # first hit → the new value
                # any further (dup/commented) copies of the same key are dropped
            else:
                out.append(raw)
    if not found:
        out.append(line)
    p.write_text("\n".join(out) + "\n")
    os.environ[key] = value


# Slack sends the SAME message as two events when an app is subscribed to both `message.channels`
# and `app_mention` — dedup on the message ts so the bot answers a "@bot …" exactly once.
_SLACK_ANSWERED_TS: list = []


def _slack_first_touch(ts: str) -> bool:
    """True the first time we see this Slack message ts (and records it); False on a duplicate."""
    if not ts:
        return True
    if ts in _SLACK_ANSWERED_TS:
        return False
    _SLACK_ANSWERED_TS.append(ts)
    if len(_SLACK_ANSWERED_TS) > 300:
        del _SLACK_ANSWERED_TS[:100]
    return True


def register_events_routes(
    app,
    *,
    runtime,
    store=None,
    concierge=None,
    engine=None,
    users=None,
    identity=None,
    oauth_store=None,
    gateway_token: str | None = None,
) -> None:
    """Attach /invoke, /api/concierge and the read-only Studio endpoints to a FastAPI ``app``.

    ``engine`` is the (optional) AP engine — passed so the Integrations endpoint can report
    **real** connection status. The read endpoints exist so the Studio UI stays dumb: it renders
    exactly what the backend can do, no client-side business logic.
    """
    from .secret_seam import secret as _secret

    token = gateway_token if gateway_token is not None else _secret("GATEWAY_TOKEN")
    if not token:
        # /invoke runs agents on a caller-supplied scope; with no token the seam is open. Fine for
        # local dev, dangerous in a shared deploy — warn loudly rather than fail silently.
        _elog.warning(
            "events: GATEWAY_TOKEN is empty — /invoke and the poll/webhook seams are "
            "UNAUTHENTICATED. Set GATEWAY_TOKEN before exposing this server."
        )
    if not os.environ.get("EVENTS_WEBHOOK_KEY"):
        # The hook runs an agent and can post into a channel. Unset, `?key=` is not merely optional —
        # it is ignored, so a WRONG key is accepted too. That surprises people; say it plainly.
        _elog.warning(
            "events: EVENTS_WEBHOOK_KEY is empty — POST /api/events/hook/<name> accepts "
            "ANY request, including one with a wrong ?key=. Set it before exposing this "
            "server on a public URL."
        )
    if not os.environ.get("SLACK_SIGNING_SECRET") and os.environ.get("SLACK_BOT_TOKEN"):
        _elog.warning(
            "events: SLACK_SIGNING_SECRET is empty — /api/events/slack/events accepts "
            "UNSIGNED requests from anyone who finds your public URL."
        )

    # NOW-run log: immediate (chat / channel DM) answers, which never become AP flow-runs. Same DB
    # file as the subscription index; ":memory:" when no EVENTS_DB (ephemeral, fine for dev/tests).
    from .now_runs import NowRunStore

    now_runs = NowRunStore(os.environ.get("EVENTS_DB", ":memory:"))

    # The WEB channel's outbound transport. Slack/Discord/Telegram get pushed into; a browser can
    # only be drained, so a web-armed flow's fire lands in this per-thread mailbox and the chat
    # surface polls GET /api/events/inbox. Same DB as the subscription index → a fire survives an
    # instance replace and is still waiting when the tab comes back.
    from . import web_inbox

    web_inbox.init(os.environ.get("EVENTS_DB", ":memory:"))

    def _log_now(
        *,
        scope,
        agent,
        channel,
        prompt,
        answer,
        status,
        ms=0,
        meta=None,
        trace_id="",
        thread_id="",
        kind="chat",
        subscription_id="",
        event_kind="",
        db=True,
        mode="NOW",
        backend="direct",
    ):
        """Record one execution: the in-DB log (Runs tab; ``db=False`` for AP-backed fires whose
        run history AP already keeps) + a DETAILED per-run file on disk for EVERY execution
        (results/run_logs/<date>/…, see run_logger). Never let logging break the response.
        ``mode``/``backend`` tag the run's trigger type so the Runs API can filter (cron/poll/push/now
        · native/ap)."""
        if db:
            try:
                now_runs.add(
                    scope=scope,
                    agent=agent or "",
                    channel=channel or "",
                    prompt=prompt or "",
                    answer=answer if isinstance(answer, str) else str(answer),
                    status=status,
                    ms=ms or 0,
                    mcp=(meta or {}).get("mcp") or [],
                    tools=(meta or {}).get("tools") or [],
                    trace_id=trace_id or "",
                    mode=mode or "NOW",
                    backend=backend or "direct",
                    subscription_id=subscription_id or "",
                    event_kind=event_kind or "",
                )
            except Exception:  # noqa: BLE001
                pass
        try:
            from . import run_logger

            run_logger.dump(
                kind=kind,
                scope=scope,
                agent=agent or "",
                channel=channel or "",
                thread_id=thread_id,
                subscription_id=subscription_id,
                event_kind=event_kind,
                text_in=prompt or "",
                answer_out=answer,
                status=status,
                ms=ms or 0,
                trace_id=trace_id or "",
                meta=meta or {},
            )
        except Exception:  # noqa: BLE001
            pass

    # let OAuth app creds come from the admin store (UI) before .env
    if oauth_store is not None:
        from . import oauth as _oauth

        _oauth.set_cred_resolver(
            lambda app, key: oauth_store.get((resolve_principal(headers={}).tenant_id), app, key)
        )

    # ── THE THREE DOORS INTO THE CONCIERGE/AGENT (they are NOT duplicates) ───────────────────────────
    #   POST /invoke        — the MACHINE seam. Envelope-in {agent,text,source,event,scope,thread},
    #                         authed by X-Gateway-Token. Called by AP callbacks, the channel adapters
    #                         (slack/discord/telegram), pollers, webhooks, and the native scheduler.
    #                         Owns delivery + run-logging + poll-delta + bounded-run expiry. May target
    #                         agent='concierge' (route/arm) OR a concrete agent (run it).
    #   POST /api/concierge — the STUDIO/programmatic surface. NL {text}-in, header principal (no
    #                         gateway token). ?dry_run=1 = deterministic plan; ?flow=1|full = armed flows.
    #   POST /stream (main.py) — the HUMAN web-chat door. SSE; decides chat-vs-concierge and streams.
    # All three converge on concierge.run; the difference is the caller, the envelope, and the auth model.
    @app.post("/invoke")
    async def invoke(request: Request):
        """Execute ONE agent run. The single entry point every trigger funnels into — the native
        scheduler, a webhook, an inbound channel message, or Activepieces. Gated by
        X-Gateway-Token; `deliver` decides whether the answer is also pushed to a channel."""
        body = await request.json()
        tr = Trace(body.get("trace_id") or new_trace_id())
        if token and request.headers.get("X-Gateway-Token") != token:
            return JSONResponse({"ok": False, "error": "bad or missing X-Gateway-Token"}, 401)
        from .envelope import validate as _validate_envelope

        problems = _validate_envelope(body)
        if problems:
            tr.error("error", reason="invalid envelope", problems=problems)
            return JSONResponse({"ok": False, "error": "invalid envelope: " + "; ".join(problems)}, 400)
        env = Envelope.from_dict(body)
        env.trace_id = tr.id
        # isolation scope: from the AP body (env.scope, set when the flow was armed); else, for a
        # CHANNEL message, resolve the sender's native id → user via the identity map (decision
        # 0007); else fall back to headers.
        scope = env.scope
        if not scope and env.source.type == "channel" and identity is not None:
            from .principal import channel_user_id, resolve_channel

            nid = channel_user_id(env.source)  # the AUTHOR (per-user), not the channel
            cp = resolve_channel(env.source.name, nid, identity) if nid else None
            if cp is not None:
                scope = cp.scope
            else:
                tr("channel.unlinked", channel=env.source.name, native=nid)
        if not scope:
            scope = resolve_principal(headers=request.headers).scope
        tr(
            "inbound",
            source=f"{env.source.type}/{env.source.name}",
            thread=env.thread_id,
            kind=env.event.kind,
            scope=scope,
            text=env.text[:80],
        )
        # BOUNDED-RUN EXPIRY GATE ("… for one hour"): AP schedules cannot end themselves, so the
        # deadline baked into the flow's body is enforced HERE — the first tick past expires_at
        # deletes the flow + subscription instead of running the agent. Lazy on purpose: it works
        # even if the server was down at the deadline, and a flow never outlives it by more than
        # one tick.
        _exp = body.get("expires_at")
        if _exp and time_module.time() > float(_exp):
            _sub_id = str(body.get("subscription_id") or "")
            _sub = store.get(_sub_id) if (store is not None and _sub_id) else None
            if _sub is not None and _sub.ap_flow_id and engine is not None:
                try:
                    await engine.delete_flow(_sub.ap_flow_id)
                except Exception as e:  # noqa: BLE001
                    tr("expire.flow_delete_failed", err=str(e)[:120])
            if _sub is not None:
                store.delete(_sub_id)
            tr("expired", subscription=_sub_id, expires_at=_exp)
            return {
                "ok": True,
                "expired": True,
                "trace_id": tr.id,
                "answer": "This bounded flow reached its end time and has been removed.",
            }
        # account-linking handshake: a channel message "/start <token>" / "/link <token>" binds
        # the sender's native id → the profile that issued the token (decision 0007).
        if env.source.type == "channel" and identity is not None:
            from .principal import channel_user_id

            txt = (env.text or "").strip()
            for pfx in ("/start ", "/link "):
                if txt.startswith(pfx):
                    nid = channel_user_id(env.source)  # bind the AUTHOR's native id
                    uid = identity.redeem_token(txt[len(pfx) :].strip(), nid) if nid else None
                    tr("channel.link", channel=env.source.name, native=nid, ok=bool(uid))
                    return {
                        "ok": True,
                        "linked": bool(uid),
                        "trace_id": tr.id,
                        "answer": (
                            "Your account is linked — you can chat now."
                            if uid
                            else "That link code is invalid or expired."
                        ),
                    }
        # agents are tenant-shared (agent_scope = first 2 scope segments); run-state is per-user
        agent_scope = "/".join(scope.split("/")[:2]) or scope
        from . import runmeta

        runmeta.start()
        ms = None
        agent = env.agent
        # a human DM on a channel is a NOW run (logged here); a time/integration source is an armed
        # flow whose run history already lives in Activepieces — don't double-count it.
        is_now = env.source.type == "channel"
        # STATEFUL POLL delta gate (Phase 3): a native poll with watch_state reports only on a change.
        # Load its state now and ask the agent to emit a comparable SIGNAL (augment the prompt); the
        # change decision + delivery gate happen post-answer. No watch_state row → nothing changes
        # (plain cron / channel / AP fire is entirely unaffected).
        _poll_ws = None
        _poll_sid = str(body.get("subscription_id") or "")
        if not is_now and _poll_sid and store is not None:
            try:
                _poll_ws = store.get_watch_state(_poll_sid)
            except Exception:  # noqa: BLE001
                _poll_ws = None
            if _poll_ws is not None:
                from . import poll_state as _ps

                env.text = (env.text or "") + _ps.augment_prompt(_poll_ws)
                tr("poll.watch", subscription=_poll_sid, kind=_poll_ws.get("kind"))
        # 'concierge' is the runtime ROUTER (picks among pre-built agents / arms flows), not a
        # worker agent — inbound CHANNEL messages arm agent='concierge', so route those through
        # the router. A concrete agent id runs directly on the worker runtime.
        # ── channel-message WATCHERS (direct subscriptions) ──────────────────────────────
        # An armed "when someone posts in #x…" / "when I send the bot a link…" watcher consumes a
        # channel message INSTEAD of the converse path: the first match becomes the worker (its
        # reply returns exactly like a concierge answer — same thread, same delivery); additional
        # matches fire in the background. No watchers armed (the default) → zero behavior change.
        # Slash COMMANDS outrank watchers: "/cron …" typed in a watched Telegram chat must reach
        # the concierge, not be consumed as "a link to summarize" (caught live by the exhaustive
        # matrix, 2026-07-17 — an armed link-watcher swallowed the arm command).
        _is_slash = bool(
            re.match(r"\s*/(automate|watch|schedule|cron|poll|push|start|link)\b", env.text or "", re.I)
        )
        if (
            agent == "concierge"
            and store is not None
            and env.source.type == "channel"
            and env.event.kind == "message"
            and not _is_slash
        ):
            from . import direct_events
            from .principal import channel_origin as _chorigin

            _org = _chorigin(env.thread_id) or (env.source.name, "")
            _watchers = direct_events.match(
                store, env.source.name, "new_channel_message", channel=_org[1], text=env.text
            )
            if _watchers:
                import asyncio as _aio

                tr(
                    "direct.watch",
                    channel=env.source.name,
                    matched=len(_watchers),
                    agent=_watchers[0].target_agent,
                )
                _wpay = {"text": env.text, "channel": _org[1]}
                # The first matched watcher becomes the foreground worker; extras fire in the background.
                if len(_watchers) > 1:
                    _aio.create_task(
                        direct_events.dispatch_all(
                            _watchers[1:],
                            app=env.source.name,
                            event="new_channel_message",
                            payload=_wpay,
                            engine=engine,
                        )
                    )
                agent = _watchers[0].target_agent
                env.agent = agent
                env.text = f"{_watchers[0].prompt}\n\nThe watched message just arrived: {env.text}"
        if agent == "concierge":
            if concierge is None:
                tr.error("error", reason="concierge not configured")
                return JSONResponse({"ok": False, "error": "concierge not configured"}, 501)
            try:
                import time

                t0 = time.time()
                principal = _principal_from(scope, request.headers)
                answer = await concierge.run(env.thread_id, env.text, principal)
                ms = int((time.time() - t0) * 1000)
                tr("concierge", ok=True, scope=scope, ms=ms)
            except Exception as e:  # noqa: BLE001
                tr.error("error", agent=agent, err=str(e))
                if is_now:
                    _log_now(
                        scope=scope,
                        agent=agent,
                        channel=env.source.name,
                        prompt=env.text,
                        answer=str(e),
                        status="error",
                        ms=ms,
                        trace_id=tr.id,
                    )
                return JSONResponse({"ok": False, "error": str(e)}, 500)
        else:
            if not agent or runtime.get_agent(agent, scope=agent_scope) is None:
                tr.error("error", reason="unknown agent", agent=agent, scope=agent_scope)
                return JSONResponse({"ok": False, "error": f"unknown agent '{agent}'"}, 404)
            try:
                import time

                t0 = time.time()
                # EXECUTION THREAD ≠ DELIVERY THREAD. env.thread_id carries the ORIGIN conversation
                # (channel + chat id + in-thread locus) and stays the delivery target below. But
                # running every fire under it too meant one frozen thread accumulated conversation
                # memory forever: fire #288 of "IBM every 5 min" dragged 287 prior turns — costly,
                # and the agent starts answering from stale context. A watch tick is independent by
                # nature (the poll change-gate, not chat memory, is what carries "did it change"),
                # so fires get a FRESH per-fire thread. EVENTS_FIRE_MEMORY=continuous restores the
                # old accumulating behaviour for a task that genuinely wants it.
                _exec_thread = env.thread_id
                if not is_now and os.environ.get("EVENTS_FIRE_MEMORY", "stateless") != "continuous":
                    _exec_thread = f"fire:{body.get('subscription_id') or env.thread_id}#{tr.id}"
                answer = await runtime.run(
                    agent,
                    _exec_thread,
                    env.worker_input(),
                    scope=agent_scope,
                    deliver_to=[env.source.name] if env.deliver else None,
                )
                ms = int((time.time() - t0) * 1000)
                tr("worker.done", agent=agent, ok=True, ms=ms)
            except Exception as e:  # noqa: BLE001
                tr.error("error", agent=agent, err=str(e))
                if is_now:
                    _log_now(
                        scope=scope,
                        agent=agent,
                        channel=env.source.name,
                        prompt=env.text,
                        answer=str(e),
                        status="error",
                        ms=ms,
                        trace_id=tr.id,
                    )
                return JSONResponse({"ok": False, "error": str(e)}, 500)
        # metadata footer — who answered + which tools ran — appended to the reply so it shows on
        # every channel (Telegram/Discord/…). Structured `meta` also rides in the API response.
        # Off with EVENTS_REPLY_METADATA=0.
        meta = runmeta.get() or {}
        # STATEFUL POLL delta decision (Phase 3): pull the agent's SIGNAL out of the answer, decide
        # whether anything CHANGED against the tiny durable state, and persist the next state. If
        # nothing changed we suppress delivery (below) and log the run as 'nochange' so it's visible
        # in the timeline without pinging the user. Tier 0/1/2 all resolve here.
        _poll_changed = True
        _poll_reason = ""
        if _poll_ws is not None and isinstance(answer, str):
            from . import poll_state as _ps

            _clean, _signal = _ps.parse_signal(answer)
            answer = _clean  # strip the machine SIGNAL line from the human reply
            _dec = _ps.decide(_poll_ws, _signal, time_module.time())
            _poll_changed, _poll_reason = _dec.changed, _dec.reason
            try:
                store.set_watch_state(_dec.state)
            except Exception as e:  # noqa: BLE001
                tr.error("poll.state", err=str(e)[:120])
            tr("poll.decide", subscription=_poll_sid, changed=_poll_changed, reason=_poll_reason)
        base_answer = answer if isinstance(answer, str) else str(answer)  # log the reply, sans footer
        if os.environ.get("EVENTS_REPLY_METADATA", "1") != "0" and isinstance(answer, str):
            foot = runmeta.footer(meta, ms=ms)
            if foot:
                answer = f"{answer}\n\n{foot}"
        # Delivery. AP-backed channels deliver via an AP send step (deliver=False here). deliver=True
        # is CUGA-owned delivery, in priority order:
        #   1. DIRECT channel sink (e.g. Slack): the reply goes to the gw:<channel>:<native> origin
        #      encoded in the thread_id — the caller's channel — whether the flow was triggered BY that
        #      channel (a reply) or was armed from it to deliver TO it (a push/cron/poll flow, whose
        #      source is an integration/timer, so source.name is NOT the sink). CUGA sends via the
        #      channel's direct adapter (no AP connection). AP-backed sinks never reach here (they use
        #      an AP send step + deliver=False), so a deliver=True direct-send is unambiguous.
        #   2. capture sink: POST to EA_CAPTURE_URL when set (assertable real-HTTP target for e2e/web).
        if env.deliver and _poll_changed:
            from . import delivery
            from .principal import channel_origin, channel_locus

            direct_done = False
            origin = channel_origin(env.thread_id)  # (channel, native) from the thread_id
            # A browser thread has no gw: origin — the Studio and the main chat pass their own
            # conversation id. Treat that as the WEB channel, whose delivery address IS the thread
            # id: the fire lands in the mailbox the chat surface drains. Without this a web-armed
            # flow ran, logged, and told nobody (the "it fired but I don't see it" gap).
            if origin is None and env.thread_id and not is_now:
                from .principal import unscoped_thread

                origin = ("web", unscoped_thread(env.thread_id))
            if origin and delivery.is_direct(origin[0]) and origin[1] and isinstance(answer, str):
                # a FIRE (not a chat message) is labeled so the reader knows WHY the bot spoke:
                # which flow/trigger produced this, at a glance. Chat replies stay unlabeled.
                out_text = answer
                _flow_name = ""
                _sid = str(body.get("subscription_id") or "")
                if env.event.kind != "message":
                    if _sid and store is not None:
                        _s = store.get(_sid)
                        _flow_name = (_s.flow_name or "") if _s is not None else ""
                    _flow = f" · {_flow_name}" if _flow_name else ""
                    _what = "cron tick" if env.event.kind == "tick" else f"{env.source.name}/{env.event.kind}"
                    out_text = f"⚡ flow fired · {_what}{_flow}\n{answer}"
                # locus = the in-channel anchor (Slack thread_ts / Discord message id): deliver
                # INTO the thread the flow was armed from, not to the channel root
                ok, why = await delivery.send_direct(
                    origin[0],
                    origin[1],
                    out_text,
                    locus=channel_locus(env.thread_id),
                    scope=scope,
                    meta={
                        "agent": meta.get("agent") or agent,
                        "subscription_id": _sid,
                        "flow_name": _flow_name,
                        "event_kind": str(getattr(env.event, "kind", "") or ""),
                    },
                )
                tr("deliver", via="direct", channel=origin[0], ok=ok, reason=why)
                direct_done = ok
            cap = os.environ.get("EA_CAPTURE_URL")
            if not direct_done and cap:
                try:
                    import httpx

                    async with httpx.AsyncClient(timeout=10) as hc:
                        await hc.post(
                            cap,
                            json={
                                "agent": agent,
                                "answer": answer,
                                "thread_id": env.thread_id,
                                "trace_id": tr.id,
                            },
                        )
                    tr("deliver", via="capture", ok=True)
                except Exception as e:  # noqa: BLE001
                    tr.error("deliver", via="capture", err=str(e))
        # EVERY execution gets a detailed disk log (chat AND fires); the in-DB Runs row is for NOW
        # answers AND for NATIVE scheduled fires. AP-backed fires stay db=False because AP keeps their
        # own run history — but a NATIVE cron/poll has NO AP history, so without this its recurring
        # output would be invisible in the Runs tab (the "I don't see the response" gap).
        _native_fire = False
        _run_mode = "NOW"
        _run_backend = "direct"
        _sub_id = str(body.get("subscription_id") or "")
        if not is_now and _sub_id and store is not None:
            try:
                _s = store.get(_sub_id)
                if _s is not None:
                    _native_fire = _s.backend == "native"
                    _run_mode = _s.mode or "NOW"  # CRON | POLL | PUSH
                    _run_backend = _s.backend or "ap"  # native | react/ap
            except Exception:  # noqa: BLE001
                _native_fire = False
        elif is_now:
            _run_mode = "NOW"
            _run_backend = "direct"
        # a stateful poll that found no change is a real run (it executed) but delivered nothing —
        # mark it 'nochange' so the timeline shows the tick without implying the user was pinged.
        _run_status = "nochange" if (_poll_ws is not None and not _poll_changed) else "ok"
        # Log the DELIVERY channel, not the trigger's name. A native fire's source.name is the MODE
        # ("cron"/"poll"), so a web-armed fire logged channel="cron" — and the web chat's new-fire
        # toast (which matches ""/"web") never fired, leaving web users with no notification at
        # all. A fire that originated in a real channel keeps that channel; the rest are web fires.
        from .principal import channel_origin as _log_chorigin

        _lc_origin = _log_chorigin(env.thread_id)
        _log_channel = _lc_origin[0] if _lc_origin else (env.source.name if is_now else "web")
        _log_now(
            scope=scope,
            agent=meta.get("agent") or (agent if agent != "concierge" else "concierge"),
            # log what the worker actually RAN ON (framing + [event] payload for a fire; the
            # bare utterance for chat) — env.text alone hid the payload from the disk log
            channel=_log_channel,
            prompt=env.worker_input(),
            answer=base_answer,
            status=_run_status,
            ms=ms,
            meta=meta,
            trace_id=tr.id,
            thread_id=env.thread_id,
            kind=("chat" if is_now else "fire"),
            event_kind=str(getattr(env.event, "kind", "") or ""),
            db=(is_now or _native_fire),
            mode=_run_mode,
            backend=_run_backend,
            subscription_id=_sub_id,
        )
        return {
            "ok": True,
            "agent": agent,
            "answer": answer,
            "trace_id": tr.id,
            # HITL arming state (present only for an arming turn) — see /api/concierge
            **({k: v for k, v in (_arming.get() or {}).items() if v}),
            # poll delta result: delivered? and why (absent for non-poll fires)
            **({"poll": {"changed": _poll_changed, "reason": _poll_reason}} if _poll_ws is not None else {}),
            "meta": {
                "agent": meta.get("agent") or (agent if agent != "concierge" else None),
                "backend": meta.get("backend"),
                "mcp": meta.get("mcp") or [],
                "tools": meta.get("tools") or [],
                "ms": ms,
            },
        }

    @app.get("/api/events/dry-run")
    @app.post("/api/events/dry-run")
    async def dry_run_utterance(request: Request, text: str | None = None, utterance: str | None = None):
        """DRY-RUN an utterance — see exactly what the concierge WOULD do, with ZERO side effects
        (nothing armed, nothing persisted). Browser-friendly (just paste a URL):

            GET /api/events/dry-run?text=every 5 minutes give me a productivity tip
            GET /api/events/dry-run?text=when a new email arrives, summarize it

        Also accepts POST {"text": "..."} — the same utterance the web UX sends. Returns the routed
        decision: mode (CRON|POLL|PUSH|NOW), native-vs-AP backend, cadence + next-fire preview, the
        resolved source/event for a push, and whether it would ARM, ASK, DECLINE, or ANSWER — and why."""
        _utt = text or utterance or ""
        if not _utt and request.method == "POST":
            _b = await _safe_json(request)
            _utt = (_b or {}).get("text") or (_b or {}).get("utterance") or ""
        text = (_utt or "").strip()
        if not text:
            return JSONResponse(
                {"ok": False, "error": "provide ?text=<utterance> (or POST {\"text\": ...})"}, 400
            )
        try:
            from . import classify, flowspec, native_scheduler
        except ImportError:  # flat load
            import classify
            import flowspec
            import native_scheduler  # type: ignore
        import time as _t

        mode = classify.classify(text)
        out = {"ok": True, "utterance": text, "mode": mode, "side_effects": "none (dry run)"}
        if mode in ("CRON", "POLL"):
            cad = classify.cadence_of(text)
            ttl = classify.ttl_of(text)
            native = native_scheduler.enabled()
            interval, cron = cad.get("interval_seconds"), cad.get("cron")
            nxt = None
            try:
                _now = _t.time()
                nxt = (
                    (_now + interval)
                    if interval
                    else (native_scheduler.next_cron(cron, _now) if cron else None)
                )
            except Exception:  # noqa: BLE001
                nxt = None
            out.update(
                {
                    "backend": "native" if native else "ap",
                    "routing": (
                        "native scheduler — runs in-process, no Activepieces"
                        if native
                        else "Activepieces schedule piece (EVENTS_SCHEDULER=ap)"
                    ),
                    "cadence": (
                        {"interval_seconds": interval} if interval else {"cron": cron} if cron else {}
                    ),
                    "cadence_human": (
                        f"every {interval // 60} min"
                        if interval and interval % 60 == 0
                        else f"every {interval}s"
                        if interval
                        else f"cron '{cron}'"
                        if cron
                        else "none detected — would ask"
                    ),
                    "bounded_run_seconds": ttl,
                    "next_fire_preview": (_iso(nxt) if nxt else None),
                    "would": "arm" if (interval or cron) else "ask (no cadence detected)",
                }
            )
        elif mode == "PUSH":
            spec = flowspec.resolve(text)
            ap_up = False
            if engine is not None:
                try:
                    ap_up = (await engine.reachable()) if hasattr(engine, "reachable") else True
                except Exception:  # noqa: BLE001
                    ap_up = False
            would = (
                "arm (AP integration)"
                if (spec.confidence == "high" and ap_up)
                else "decline — Activepieces not reachable"
                if (spec.confidence == "high" and not ap_up)
                else "ask — ambiguous source"
                if spec.confidence == "ambiguous"
                else "unresolved — no integration matched (try 'when a new <app> …')"
            )
            out.update(
                {
                    "backend": "ap",
                    "routing": "Activepieces (integration piece)",
                    "source": spec.source,
                    "event": spec.event,
                    "confidence": spec.confidence,
                    "candidates": [f"{a}/{e}" for a, e in (spec.candidates or [])],
                    "ask": spec.ask,
                    "ap_reachable": ap_up,
                    "would": would,
                }
            )
        else:  # NOW
            out.update(
                {
                    "backend": "—",
                    "routing": "answered by the agent now (no flow armed)",
                    "would": "answer",
                }
            )
        return out

    @app.post("/api/concierge")
    async def api_concierge(request: Request):  # NOT 'concierge' — that name is the instance arg
        """Natural language in, a decision out: answer now, or propose a standing flow to arm.
        `?dry_run=1` builds the plan with no side effects. Arming requires the `/automate` verb —
        plain chat can never create a flow."""
        body = await request.json()
        text = (body or {}).get("text", "")
        dry = request.query_params.get("dry_run") in ("1", "true", "yes")
        tr = Trace(new_trace_id())
        tr("inbound", source="api/concierge", dry_run=dry, text=text[:80])
        if dry:
            # reason → build, NO side effects (deterministic planner)
            planned = concierge_plan.plan(
                text,
                agent=(body or {}).get("agent", "worker"),
                thread_id=(body or {}).get("thread_id", "web:local"),
            )
            tr("flow.build", mode=planned["decision"]["mode"], dry_run=True)
            return {"ok": True, "dry_run": True, **planned, "trace_id": tr.id}
        # live path: run the LLM concierge (host-bound meta-tools) — reuse/create + run/arm.
        if concierge is None:
            planned = concierge_plan.plan(text, agent=(body or {}).get("agent", "worker"))
            return JSONResponse(
                {
                    "ok": False,
                    "reason": "concierge not configured; use ?dry_run=1 for the plan",
                    "plan": planned,
                    "trace_id": tr.id,
                },
                501,
            )
        thread_id = (body or {}).get("thread_id", "web:local")
        # CHANNEL-ORIGINATED ARMING. CUGA is the door now: a Slack/Telegram/Discord "/automate …"
        # reaches /run first and is forwarded here with the originating channel attached. Resolve
        # the SENDER's identity from it, exactly as /invoke does for a channel envelope — otherwise
        # the flow arms as the fallback user while the Studio lists another scope, and it is armed,
        # firing, delivering, and invisible.
        principal = resolve_principal(headers=request.headers)
        _ch = (body or {}).get("channel")
        if isinstance(_ch, dict) and _ch.get("name") and identity is not None:
            from .principal import resolve_channel

            _cp = resolve_channel(str(_ch["name"]), str(_ch.get("user") or ""), identity)
            if _cp is not None:
                principal = _cp
            else:
                tr("channel.unlinked", channel=_ch.get("name"), native=_ch.get("user"))
        # `?flow=1` → also return the flow(s) this utterance armed, so a caller can check the pieces
        # are right without a second round trip to /subscriptions/<id>/flow. `?flow=full` adds the
        # raw Activepieces flow JSON. Off by default: it costs one AP call per new subscription.
        want_flow = request.query_params.get("flow", "") in ("1", "true", "yes", "digest", "full")
        full_flow = request.query_params.get("flow", "") == "full"
        before = {s.id for s in store.list(scope=principal.scope)} if (store and want_flow) else set()
        # /watch|/schedule|/cron|/poll|/push slash commands are handled inside concierge.run (so they
        # work from every surface — web chat AND channels), no interception needed here.
        from . import runmeta
        import time as _t

        runmeta.start()
        _t0 = _t.time()
        try:
            reply = await concierge.run(thread_id, text, principal)
            _ms = int((_t.time() - _t0) * 1000)
            tr("concierge", ok=True, scope=principal.scope)
        except Exception as e:  # noqa: BLE001
            tr.error("error", err=str(e))
            _log_now(
                scope=principal.scope,
                agent="concierge",
                channel="web",
                prompt=text,
                answer=str(e),
                status="error",
                ms=int((_t.time() - _t0) * 1000),
                trace_id=tr.id,
            )
            return JSONResponse({"ok": False, "error": str(e), "trace_id": tr.id}, 500)
        _meta = runmeta.get() or {}
        _log_now(
            scope=principal.scope,
            agent=_meta.get("agent") or "concierge",
            channel="web",
            prompt=text,
            answer=reply if isinstance(reply, str) else str(reply),
            status="ok",
            ms=_ms,
            meta=_meta,
            trace_id=tr.id,
            thread_id=thread_id or "",
            kind="chat",
        )
        out = {"ok": True, "reply": reply, "scope": principal.scope, "trace_id": tr.id}
        # HITL arming: the machine-readable half of the reply. `state` tells a caller whether the
        # thread is mid-dialogue (needs_input|confirm → keep routing here) or done (armed|
        # cancelled), so the UI can render a confirm card and the channel edges can stay sticky
        # without re-classifying the follow-up.
        _arm = _arming.get()
        if _arm:
            out.update({k: v for k, v in _arm.items() if v})
        if want_flow:
            out["flows"] = await _armed_flows(before, principal.scope, full=full_flow)
        return out

    def _flow_digest(ap_flow: dict) -> dict:
        """The Activepieces flow, reduced to the question people actually ask: which pieces, in what
        order, wired to what. The raw flow JSON is a few hundred lines of AP bookkeeping."""
        ver = (ap_flow or {}).get("version") or {}
        trig = ver.get("trigger") or {}
        ts = trig.get("settings") or {}
        steps, node = [], trig.get("nextAction")
        while node:
            s = node.get("settings") or {}
            steps.append(
                {
                    "name": node.get("name"),
                    "display": node.get("displayName"),
                    "type": node.get("type"),
                    "piece": s.get("pieceName"),
                    "action": s.get("actionName"),
                    # the send step's text is a template that reads step_1's HTTP RESPONSE —
                    # this is the seam that proves the answer flows into the sink
                    "text": ((s.get("input") or {}).get("text") or (s.get("input") or {}).get("message")),
                }
            )
            node = node.get("nextAction")
        return {
            "id": ap_flow.get("id"),
            "status": ap_flow.get("status"),
            "trigger": {
                "piece": ts.get("pieceName"),
                "name": ts.get("triggerName"),
                "input": ts.get("input"),
            },
            "steps": steps,
        }

    async def _armed_flows(before: set, scope: str, *, full: bool = False) -> list[dict]:
        """Subscriptions this call created, each with its live AP flow. An utterance that REUSED an
        existing flow adds nothing here — by design, since nothing was armed. The empty list is the
        honest answer; `GET /api/events/subscriptions` shows what already exists."""
        if store is None:
            return []
        out = []
        for sub in store.list(scope=scope):
            if sub.id in before:
                continue
            row: dict = {
                "subscription_id": sub.id,
                "mode": sub.mode,
                "agent": sub.target_agent,
                "deliver_to": list(sub.deliver_to or []),
                "flow_name": sub.flow_name,
                "ap_flow_id": sub.ap_flow_id,
                "dedup_key": sub.dedup_key,
            }
            ap_flow = None
            if engine is not None and sub.ap_flow_id:
                try:
                    ap_flow = await engine.get_flow(sub.ap_flow_id)
                except Exception as e:  # noqa: BLE001 — AP down must not 500 the arm
                    row["flow_error"] = str(e)
            # `ap_flow: null` on a subscription that names an ap_flow_id means DANGLING: the flow does
            # not exist in AP, so the watcher can never fire. Surface it here rather than let the
            # caller infer "armed" from a non-empty ap_flow_id.
            row["exists_in_ap"] = bool(ap_flow)
            if ap_flow:
                row["flow"] = ap_flow if full else _flow_digest(ap_flow)
            out.append(row)
        return out

    @app.get("/api/events/subscriptions")
    async def list_subscriptions(
        request: Request, mode: str | None = None, backend: str | None = None, status: str | None = None
    ):
        """Every armed watcher for this caller (isolation), with a by-type summary.

        Filter with ``?mode=CRON|POLL|PUSH|NOW`` · ``?backend=native|ap|react`` · ``?status=active|paused``.
        The ``summary`` block counts watchers by trigger type and by AP vs no-AP so a dashboard can show
        'N crons · N polls · N pushes · M native (no AP) · K AP flows' at a glance."""
        scope = resolve_principal(headers=request.headers).scope
        subs = store.as_dicts(scope=scope) if store else []
        f_mode = (mode or "").upper() or None
        f_backend = (backend or "").lower() or None
        f_status = (status or "").lower() or None

        def _keep(s):
            return (
                (not f_mode or str(s.get("mode") or "").upper() == f_mode)
                and (not f_backend or str(s.get("backend") or "").lower() == f_backend)
                and (not f_status or str(s.get("status") or "").lower() == f_status)
            )

        subs = [s for s in subs if _keep(s)]
        # attach the stateful-poll delta state (kind/threshold + live baseline/seen-count) so the
        # dashboard can show WHAT each poll watches and its current reference point.
        if store is not None:
            for s in subs:
                if str(s.get("mode") or "").upper() == "POLL":
                    try:
                        ws = store.get_watch_state(s.get("subscription_id") or s.get("id"))
                    except Exception:  # noqa: BLE001
                        ws = None
                    if ws:
                        s["watch"] = {
                            "kind": ws.get("kind"),
                            "threshold": ws.get("threshold"),
                            "watching": ws.get("value_path"),
                            "baseline": ws.get("baseline"),
                            "seen": len(ws.get("seen_keys") or []),
                        }
        summary = {
            "total": len(subs),
            "by_mode": {
                m: sum(1 for s in subs if str(s.get("mode") or "").upper() == m)
                for m in ("CRON", "POLL", "PUSH", "NOW")
            },
            "native_no_ap": sum(1 for s in subs if s.get("backend") == "native"),
            "ap_flows": sum(1 for s in subs if s.get("ap_flow_id")),
            "active": sum(1 for s in subs if s.get("status") == "active"),
            "paused": sum(1 for s in subs if s.get("status") == "paused"),
        }
        return {"scope": scope, "summary": summary, "subscriptions": subs}

    # --- flow lifecycle: pause / resume / delete from the CUGA UI (AP is driven internally) -------
    def _owned_sub(sub_id: str, request: Request):
        """Fetch a subscription IFF it belongs to the caller (isolation), else (None, scope)."""
        scope = resolve_principal(headers=request.headers).scope
        sub = store.get(sub_id) if store else None
        return (sub if (sub and sub.tenant == scope) else None), scope

    @app.post("/api/events/subscriptions/{sub_id}/pause")
    async def pause_subscription(sub_id: str, request: Request):
        """Stop a flow firing without deleting it. Keeps its history and can be resumed."""
        sub, _ = _owned_sub(sub_id, request)
        if sub is None:
            return JSONResponse({"ok": False, "error": "subscription not found"}, 404)
        if engine is not None and sub.ap_flow_id:
            await engine.set_flow_status(sub.ap_flow_id, enabled=False)  # disable in AP
        store.set_status(sub_id, "paused")
        Trace(new_trace_id())("sub.pause", id=sub_id, flow=sub.ap_flow_id)
        return {"ok": True, "id": sub_id, "status": "paused"}

    @app.post("/api/events/subscriptions/{sub_id}/resume")
    async def resume_subscription(sub_id: str, request: Request):
        """Re-arm a paused flow. Next fire follows its normal schedule."""
        sub, _ = _owned_sub(sub_id, request)
        if sub is None:
            return JSONResponse({"ok": False, "error": "subscription not found"}, 404)
        if engine is not None and sub.ap_flow_id:
            await engine.set_flow_status(sub.ap_flow_id, enabled=True)  # re-enable in AP
        store.set_status(sub_id, "active")
        Trace(new_trace_id())("sub.resume", id=sub_id, flow=sub.ap_flow_id)
        return {"ok": True, "id": sub_id, "status": "active"}

    @app.delete("/api/events/subscriptions/{sub_id}")
    async def delete_subscription(sub_id: str, request: Request):
        """Permanently disarm a flow and remove its trigger from the backend that owned it."""
        sub, _ = _owned_sub(sub_id, request)
        if sub is None:
            return JSONResponse({"ok": False, "error": "subscription not found"}, 404)
        if engine is not None and sub.ap_flow_id:
            await engine.delete_flow(sub.ap_flow_id)  # delete in AP
        store.delete(sub_id)
        Trace(new_trace_id())("sub.delete", id=sub_id, flow=sub.ap_flow_id)
        return {"ok": True, "id": sub_id, "deleted": True}

    @app.get("/api/events/subscriptions/{sub_id}/flow")
    async def subscription_flow(sub_id: str, request: Request):
        """Rich read-only flow view: the CUGA subscription model + the live AP flow JSON (trigger +
        steps), so the Studio can render the flow like AP does without opening the AP console."""
        import dataclasses as _dc

        sub, _ = _owned_sub(sub_id, request)
        if sub is None:
            return JSONResponse({"ok": False, "error": "subscription not found"}, 404)
        ap_flow = None
        if engine is not None and sub.ap_flow_id:
            ap_flow = await engine.get_flow(sub.ap_flow_id)
        return {"ok": True, "subscription": _dc.asdict(sub), "ap_flow": ap_flow}

    @app.get("/api/events/dashboard")
    async def events_dashboard():
        """The Events Dashboard — a LIVE, self-contained control plane: every watcher, run, and
        channel, pretty and readable, with pause/resume/delete/run + an inline dry-run. Reads only the
        /api/events/* APIs; no build step, so it can't break the pre-built Studio bundle."""
        from .events_dashboard import DASHBOARD_HTML

        return HTMLResponse(DASHBOARD_HTML)

    # --- the web channel's mailbox: fires waiting for a browser to come and collect them ----------
    @app.get("/api/events/inbox")
    async def web_inbox_read(
        request: Request, thread_id: str, since: float = 0.0, limit: int = 50, max_age: float = 0.0
    ):
        """Messages delivered to ONE web thread, **oldest first**, newer than ``since``.

        This is how a browser receives an asynchronous fire. A flow armed in the Studio chat or the
        main chat fires minutes or days later, with no request in flight — Slack would get a push,
        a tab can only be drained. So ``delivery.send_direct("web", …)`` writes the answer here and
        the chat surface polls this endpoint with the ``ts`` of the last message it rendered.

        ``since`` is EXCLUSIVE (pass back the last ``ts`` you saw and you will never re-render it).
        Pass ``since=0`` to get the thread's whole backlog — which is what a reloaded tab does, so
        the fires it missed while closed appear in the transcript rather than being lost.

        ``max_age`` (seconds) bounds that backlog on a FIRST load, and the cutoff is computed with
        the SERVER's clock. A minute-by-minute cron accumulates hundreds of fires; replaying all of
        them into a chat window is not "recovering what you missed", it is a flood. The clients ask
        for a day. It is a server-side parameter on purpose: the cursor is a server timestamp, so
        letting a browser subtract from its own clock would skip or repeat messages whenever the two
        disagree. Ignored once ``since`` is set, which is every poll after the first.

        Isolation: only this caller's messages, and only for the thread asked for.
        """
        scope = resolve_principal(headers=request.headers).scope
        if max_age > 0 and since <= 0:
            since = max(0.0, time_module.time() - max_age)
        msgs = web_inbox.list_since(thread_id=thread_id, since=since, scope=scope, limit=limit)
        return {
            "thread_id": thread_id,
            "count": len(msgs),
            "scope": scope,
            # the cursor to send back next time — the client never has to compute it
            "cursor": (msgs[-1]["ts"] if msgs else since),
            "messages": msgs,
        }

    # --- execution log: which flows RAN, succeeded/failed, and their output -----------------------
    @app.get("/api/events/runs")
    async def list_runs(
        request: Request,
        mode: str | None = None,
        backend: str | None = None,
        agent: str | None = None,
        status: str | None = None,
        kind: str | None = None,
        source: str | None = None,
        subscription_id: str | None = None,
        limit: int = 150,
    ):
        """The unified execution log — every run, tagged with its TRIGGER TYPE and rich metadata.

        Each row carries: ``mode`` (CRON | POLL | PUSH | NOW), ``backend`` (native | ap | direct),
        ``agent``, ``answer`` (the agent's reply), ``tools``/``mcp`` (what it invoked), ``ms``,
        ``status``, timestamps, ``subscription_id``/``flow_id``. So you can see *what fired, how it
        was triggered, what it answered, and which tools it used* — for AP-backed and native runs alike.

        Filter with query params (all optional, AND-combined):
          ``?mode=CRON|POLL|PUSH|NOW`` · ``?backend=native|ap|direct`` · ``?agent=<name>``
          · ``?status=SUCCEEDED|FAILED`` · ``?kind=flow|now`` · ``?source=<connector>``
          · ``?subscription_id=<id>`` (a single watcher's run history) · ``?limit=<n>``.
        No params ⇒ ALL runs. Isolation: only THIS caller's runs.

        NB: this is the log of runs that HAVE HAPPENED (status SUCCEEDED/FAILED). To see what is
        SCHEDULED / upcoming (and each watcher's last_fire / fire_count / next_fire), use
        ``GET /api/events/subscriptions`` — the schedule lives on the watcher, not here.
        """
        f_mode = (mode or "").upper() or None
        f_backend = (backend or "").lower() or None
        f_agent = agent or None
        f_status = (status or "").upper() or None
        f_kind = (kind or "").lower() or None
        f_source = source or None
        f_sub = subscription_id or None
        limit = max(1, min(500, limit))
        scope = resolve_principal(headers=request.headers).scope
        subs = store.as_dicts(scope=scope) if store else []
        by_flow = {s.get("ap_flow_id"): s for s in subs if s.get("ap_flow_id")}
        out = []
        # ── AP flow-runs (backend=ap): Activepieces keeps their run history ──
        runs = await engine.list_runs(limit=120) if engine is not None else []
        for r in runs:
            sub = by_flow.get(r.get("flowId"))
            if sub is None:
                continue  # isolation: skip runs whose flow isn't the caller's
            out.append(
                {
                    "id": r.get("id"),
                    "status": r.get("status"),
                    "started_at": r.get("startTime"),
                    "finished_at": r.get("finishTime"),
                    "agent": sub.get("target_agent"),
                    "mode": sub.get("mode"),
                    "backend": "ap",
                    "integration": sub.get("source_connector"),
                    "channel": ", ".join(sub.get("deliver_to") or []),
                    "utterance": sub.get("prompt"),
                    "answer": None,
                    "tools": [],
                    "mcp": [],
                    "ms": None,
                    "event_kind": sub.get("event") or "",
                    "flow_name": sub.get("flow_name"),
                    "subscription_id": sub.get("id"),
                    "flow_id": r.get("flowId"),
                    "trace_id": "",
                    "kind": "flow",
                }
            )
        # ── NOW answers + NATIVE fires (backend=native/direct): logged here, no AP history ──
        for nr in now_runs.list(scope=scope, limit=200):
            out.append(
                {
                    "id": nr["id"],
                    "status": "SUCCEEDED" if nr.get("status") == "ok" else "FAILED",
                    "started_at": _iso(nr["ts"]),
                    "finished_at": _iso(nr["ts"]),
                    "agent": nr.get("agent") or "concierge",
                    "mode": nr.get("mode") or "NOW",
                    "backend": nr.get("backend") or "direct",
                    "integration": "—",
                    "channel": nr.get("channel"),
                    "utterance": nr.get("prompt"),
                    "answer": nr.get("answer"),  # the agent's actual reply
                    "tools": nr.get("tools") or [],
                    "mcp": nr.get("mcp") or [],  # what it invoked
                    "ms": nr.get("ms"),
                    "event_kind": nr.get("event_kind") or "",
                    "flow_name": "",
                    "subscription_id": nr.get("subscription_id") or None,
                    "flow_id": "",
                    "trace_id": nr.get("trace_id") or "",
                    "kind": "flow" if (nr.get("mode") or "NOW") != "NOW" else "now",
                }
            )

        # ── filters (AND) ──
        def _keep(r):
            if f_mode and str(r.get("mode") or "").upper() != f_mode:
                return False
            if f_backend and str(r.get("backend") or "").lower() != f_backend:
                return False
            if f_agent and r.get("agent") != f_agent:
                return False
            if f_status and str(r.get("status") or "").upper() != f_status:
                return False
            if f_kind and r.get("kind") != f_kind:
                return False
            if f_source and r.get("integration") != f_source:
                return False
            if f_sub and r.get("subscription_id") != f_sub:
                return False
            return True

        out = [r for r in out if _keep(r)]
        out.sort(key=lambda r: r.get("started_at") or "", reverse=True)
        return {
            "scope": scope,
            "count": len(out[:limit]),
            "filters": {
                "mode": f_mode,
                "backend": f_backend,
                "agent": f_agent,
                "status": f_status,
                "kind": f_kind,
                "source": f_source,
            },
            "runs": out[:limit],
        }

    def _dissect_run(run: dict) -> tuple[str | None, object, str | None]:
        """(answer, trigger_payload, error) out of an AP run's step tree.

        The agent's answer lives at ``steps.<n>.output.body.answer`` — the shape /invoke returns,
        which the flow's send step reads as ``{{step_1.body.answer}}``."""
        steps = run.get("steps") or {}
        answer = trigger_payload = error = None
        for name, s in steps.items() if isinstance(steps, dict) else []:
            if not isinstance(s, dict):
                continue
            outp = s.get("output")
            if name == "trigger":
                trigger_payload = outp
            elif isinstance(outp, dict) and isinstance(outp.get("body"), dict) and "answer" in outp["body"]:
                answer = outp["body"]["answer"]
            if s.get("status") not in ("SUCCEEDED", None) and s.get("errorMessage"):
                error = s.get("errorMessage")
        return answer, trigger_payload, error

    @app.get("/api/events/runs/{run_id}")
    async def run_detail(run_id: str, request: Request):
        """One run's detail + the agent's OUTPUT: the CUGA /invoke answer, the trigger payload, and
        any error. Scoped to the caller's own flows."""
        scope = resolve_principal(headers=request.headers).scope
        # a NOW run (chat / channel DM) lives in our own log, not Activepieces — check it first.
        nr = now_runs.get(run_id)
        if nr is not None:
            if nr.get("scope") != scope:
                return JSONResponse({"ok": False, "error": "run not found"}, 404)
            is_ok = nr["status"] == "ok"
            return {
                "ok": True,
                "run": {
                    "id": nr["id"],
                    "status": "SUCCEEDED" if is_ok else "FAILED",
                    "started_at": _iso(nr["ts"]),
                    "finished_at": _iso(nr["ts"]),
                },
                "answer": nr["answer"] if is_ok else None,
                "trigger_payload": {
                    "prompt": nr["prompt"],
                    "channel": nr["channel"],
                    "agent": nr["agent"],
                    "mcp": nr["mcp"],
                    "tools": nr["tools"],
                    "ms": nr["ms"],
                },
                "error": None if is_ok else nr["answer"],
            }
        owned = {s.get("ap_flow_id") for s in (store.as_dicts(scope=scope) if store else [])}
        run = await engine.get_run(run_id) if engine is not None else None
        if run is None or run.get("flowId") not in owned:
            return JSONResponse({"ok": False, "error": "run not found"}, 404)
        answer, trigger_payload, error = _dissect_run(run)
        return {
            "ok": True,
            "run": {
                "id": run.get("id"),
                "status": run.get("status"),
                "started_at": run.get("startTime"),
                "finished_at": run.get("finishTime"),
            },
            "answer": answer,
            "trigger_payload": trigger_payload,
            "error": error,
        }

    @app.post("/api/events/subscriptions/{sub_id}/run")
    async def run_subscription_now(sub_id: str, request: Request):
        """**DEBUG ONLY** — fire an armed flow now, out of band of its own trigger.

        A cron flow that runs at 09:00 is painful to test: you either wait, or re-arm it on a
        one-minute schedule. This fires the real flow immediately, through Activepieces, so the run
        exercises the real trigger→/invoke→sink chain with the real credentials.

        **This has real side effects.** The flow delivers wherever it was armed to deliver — it will
        post to your Slack channel, message your Telegram. It is not a dry run. Nothing about the run
        is marked as a test in Activepieces' own log.

        Whether the body you POST does anything depends on the flow's trigger:
          * SCHEDULE (cron/poll) — the /invoke body is frozen at arm time (agent, prompt, thread_id,
            scope), so the body is inert. It surfaces only as the run's trigger payload.
          * PUSH via a WEBHOOK-trigger piece (github) — the invoke body carries `{{trigger.<field>}}`
            templates, so the body you POST *becomes* the trigger output. Shape it like the piece's
            real trigger output and you exercise the watcher without opening a real pull request.

        NOT every flow can be fired this way. Verified 2026-07-10: the schedule piece and github's
        WEBHOOK trigger both run; gmail's `gmail_new_email_received` is an app-POLLING trigger, and AP
        accepts the trigger POST with 200 while producing no run at all. So a `timed_out` response on
        a gmail/box watcher means "this trigger type cannot be fired out of band", not "it is broken".
        Check the piece's trigger `type` at GET <AP>/api/v1/pieces/<piece>.

        ``?wait=0`` returns as soon as AP accepts the trigger. The default polls the run log for the
        run this call produced and returns its answer. Disable the endpoint entirely with
        ``EVENTS_DEBUG_RUN=0``.
        """
        import asyncio
        import time

        if os.environ.get("EVENTS_DEBUG_RUN", "1") == "0":
            return JSONResponse(
                {"ok": False, "error": "debug run endpoint disabled (EVENTS_DEBUG_RUN=0)"}, 403
            )
        # Same seam as /invoke: this runs an agent with the caller's credentials and delivers to a
        # real channel, so it must not be weaker than /invoke's gate.
        if token and request.headers.get("X-Gateway-Token") != token:
            return JSONResponse({"ok": False, "error": "bad or missing X-Gateway-Token"}, 401)
        sub, _ = _owned_sub(sub_id, request)
        if sub is None:
            return JSONResponse({"ok": False, "error": "subscription not found"}, 404)
        # NATIVE watch (cron/poll, no AP flow): fire it the SAME way the native scheduler does — POST
        # /invoke directly. So "run now" works for native watches too (there is no AP flow to trigger),
        # returns the agent's answer inline, and the fire is logged to GET /api/events/runs. Gate on
        # backend=='native' ONLY: an AP-backed sub that has LOST its flow id is dangling → still 409.
        if sub.backend == "native":
            import httpx

            try:
                from . import native_scheduler as _nsched
            except ImportError:  # flat load
                import native_scheduler as _nsched  # type: ignore
            _port = os.environ.get("EVENTS_CUGA_PORT", "7860")
            _gw = (os.environ.get("GATEWAY_TOKEN", "") or "").split(" #", 1)[0].strip()
            try:
                async with httpx.AsyncClient(timeout=200) as c:
                    r = await c.post(
                        f"http://127.0.0.1:{_port}/invoke",
                        headers={"X-Gateway-Token": _gw} if _gw else {},
                        json=_nsched._invoke_body(sub),
                    )
                    ans = (r.json() or {}).get("answer") if r.status_code == 200 else None
                return {
                    "ok": True,
                    "id": sub.id,
                    "backend": "native",
                    "fired": True,
                    "answer": ans,
                    "note": "native watch fired via /invoke (no AP) — also in GET /api/events/runs.",
                }
            except Exception as e:  # noqa: BLE001
                return JSONResponse({"ok": False, "error": f"native fire failed: {e}"}, 500)
        if engine is None:
            return JSONResponse({"ok": False, "error": "AP not configured"}, 501)
        if not sub.ap_flow_id:
            return JSONResponse({"ok": False, "error": "subscription has no AP flow to run"}, 409)
        # A dangling subscription would otherwise 'trigger' happily and never run anything.
        if not await engine.get_flow(sub.ap_flow_id):
            return JSONResponse(
                {
                    "ok": False,
                    "error": (
                        f"DANGLING: subscription points at AP flow {sub.ap_flow_id}, which does not exist in "
                        f"Activepieces. Nothing to run."
                    ),
                    "ap_flow_id": sub.ap_flow_id,
                },
                409,
            )

        wait = request.query_params.get("wait", "1") not in ("0", "false", "no")
        try:
            timeout = min(int(request.query_params.get("timeout", "120")), 600)
        except ValueError:
            timeout = 120
        tr = Trace(new_trace_id())
        # Snapshot first: AP gives us no run id back, so the new run is identified by difference.
        before = {r.get("id") for r in await engine.list_runs(limit=60) if r.get("flowId") == sub.ap_flow_id}
        # Reproduce the PROVIDER's delivery headers so the piece can identify the event. One GitHub
        # repo webhook carries every subscribed event type, so the piece disambiguates on
        # X-GitHub-Event — a synthetic POST without it makes the piece emit NOTHING (AP accepts the
        # trigger, no run is ever created). Proven on new_release / new_commit.
        from . import triggers as _tr

        _row = _tr.get(sub.source_connector or "", sub.event or "")
        _hdrs = (
            {"X-GitHub-Event": _row.hook_event}
            if (_row is not None and _row.app == "github" and _row.hook_event)
            else None
        )
        ok, detail = await engine.trigger_flow(sub.ap_flow_id, await _safe_json(request), headers=_hdrs)
        tr(
            "debug.run",
            sub=sub_id,
            flow=sub.ap_flow_id,
            ok=ok,
            detail=detail,
            hook_event=(_row.hook_event if _row is not None else ""),
        )
        if not ok:
            return JSONResponse(
                {
                    "ok": False,
                    "error": f"Activepieces refused the trigger: {detail}",
                    "ap_flow_id": sub.ap_flow_id,
                    "trace_id": tr.id,
                },
                502,
            )
        out = {
            "ok": True,
            "debug": True,
            "subscription_id": sub_id,
            "ap_flow_id": sub.ap_flow_id,
            "triggered": True,
            "trace_id": tr.id,
            "warning": "this fired the real flow: it delivered to its real sink",
        }
        if not wait:
            return out

        deadline = time.time() + timeout
        run = None
        while time.time() < deadline:
            await asyncio.sleep(3)
            fresh = [
                r
                for r in await engine.list_runs(limit=60)
                if r.get("flowId") == sub.ap_flow_id and r.get("id") not in before
            ]
            done = [r for r in fresh if r.get("status") not in ("RUNNING", "PAUSED", None)]
            if done:
                run = await engine.get_run(done[0]["id"])
                break
            if fresh:
                out["run"] = {"id": fresh[0]["id"], "status": fresh[0].get("status")}
        if run is None:
            # Triggered, but no FINISHED run inside the budget. Say which, rather than imply failure.
            out["timed_out"] = True
            out["note"] = (
                f"AP accepted the trigger but no run finished within {timeout}s. "
                f"Poll GET /api/events/runs, or retry with a larger ?timeout=."
            )
            return out
        answer, trigger_payload, error = _dissect_run(run)
        out["run"] = {
            "id": run.get("id"),
            "status": run.get("status"),
            "started_at": run.get("startTime"),
            "finished_at": run.get("finishTime"),
        }
        out["answer"], out["trigger_payload"], out["error"] = answer, trigger_payload, error
        return out

    # --- Studio read endpoints (dumb UI reads these; all real state) -------------
    def _capability_lines():
        try:
            from . import capability

            # Split out, the roster is CUGA's — ask the runtime that talks to it (HttpRuntime
            # caches, so this is not a per-request round trip).
            from .runtime import HttpRuntime

            remote = [s.name for s in runtime.list_agents()] if isinstance(runtime, HttpRuntime) else None
            return capability.report(remote)
        except Exception:  # noqa: BLE001
            return []

    def _durability():
        """Whether armed flows survive the instance being replaced. See db_persist.py."""
        try:
            from . import db_persist

            return db_persist.status(os.environ.get("EVENTS_DB", ":memory:"))
        except Exception:  # noqa: BLE001 — diagnostics must never break /status
            return {"durable": False, "reason": "unavailable"}

    @app.get("/api/events/status")
    async def events_status(request: Request):
        """What the events layer can do right now — the UI uses this to decide what to show."""
        scope = resolve_principal(headers=request.headers).scope
        grain = os.environ.get("EVENTS_AP_PROJECT_GRAIN", "tenant")
        worker_backend = os.environ.get("EVENTS_WORKER_BACKEND", "cuga")
        try:
            from . import native_scheduler as _ns
        except ImportError:  # flat load (offline tests put the events dir on sys.path)
            import native_scheduler as _ns
        native_clock = _ns.enabled()
        return {
            "ok": True,
            # Always true when this endpoint answers — reaching it means the layer is mounted. The
            # "off" signal is the REQUEST failing (in a split deployment CUGA's SPA catch-all returns
            # HTML, which getEventsStatus treats as off). Kept because EventsStatus declares it.
            "enabled": True,
            "scope": scope,
            # concierge (NL→flow) = react; workers (answer the question) = cuga by default
            "concierge_backend": "react",
            "worker_backend": worker_backend,
            "backends": ["react", "cuga"],
            "ap_configured": engine is not None,
            "project_grain": grain,
            # What can actually be armed right now. These used to report cron/poll/push as
            # `engine is not None` — AP-only — which has been wrong since the native scheduler
            # became the default: with no AP configured the Studio said schedules were unavailable
            # while the in-process scheduler was firing them every tick.
            "scheduler": "native" if native_clock else "ap",
            "features": {
                "now": True,
                # timers need a clock, not Activepieces: ours, or AP's schedule piece
                "cron": native_clock or engine is not None,
                "poll": native_clock or engine is not None,
                # AP-backed integration webhooks (gmail, github, …) — these do need the engine
                "push": engine is not None,
                # channel + inbound-webhook triggers (slack/discord/telegram/webhook, box folder
                # and comment events) are received by CUGA directly and need no AP at all
                "push_direct": True,
                "channels_inbound": engine is not None,
            },
            # the same tiered capability report printed at startup — queryable, so the Studio
            # (or a smoke test) can show exactly what's live vs what needs infra.
            "capability": _capability_lines(),
            # Are armed flows durable? On an ephemeral filesystem the answer is NO, and that is
            # invisible until the day an instance is replaced and every subscription is gone.
            # Surfaced so a smoke test can assert it instead of discovering it in production.
            "durability": _durability(),
        }

    @app.get("/api/events/channels")
    async def events_channels(request: Request):
        """The four conversational channels (web, Slack, Discord, Telegram) with live connection state
        and which backend carries each — direct, or Activepieces."""
        from .connectors import channels_status

        return {"channels": channels_status()}

    @app.get("/api/events/integrations")
    async def events_integrations(request: Request):
        """Watchable third-party apps (Box, GitHub, Gmail, Calendar, RSS, …) with per-user connection
        state, so the Studio can show CONNECT where credentials are missing."""
        from .connectors import integrations_status
        from .principal import resolve as _resolve

        p = _resolve(headers=request.headers)
        conns = None
        connect_url = None
        if engine is not None:
            connect_url = f"{getattr(engine, 'base', '')}/connections"
            try:
                grain = os.environ.get("EVENTS_AP_PROJECT_GRAIN", "tenant")
                conns = await engine.list_connections(project_name=p.ap_project_name(grain))
            except Exception as e:  # noqa: BLE001 — AP down → report 'unknown', never 500
                conns = None
                Trace(new_trace_id()).error("integrations", err=str(e))
        rows = integrations_status(conns, ap_configured=engine is not None, ap_connect_url=connect_url)
        # Which backend owns each integration's trigger + connection: AP (OAuth/PAT connection, the
        # default) vs DIRECT (CUGA polls the app's API with a token — no AP, no OAuth). Surfaced in
        # the UI so it's explicit that e.g. box-direct connects/tests differently from gmail-on-AP.
        for r in rows:
            r["backend"] = "ap"
        # DIRECT-backend override: Box in direct-poll mode connects via a token, not an AP
        # connection, so AP-derived status would wrongly read 'not_connected'. Reflect the token.
        if os.environ.get("EVENTS_BOX_BACKEND") == "direct":
            from . import box_direct

            has_tok = bool(box_direct.token())
            for r in rows:
                if r["name"] == "box":
                    r["status"] = "connected" if has_tok else "not_connected"
                    r["connected"] = has_tok
                    r["backend"] = "direct"
                    r["note"] = (
                        "DIRECT backend — CUGA polls Box with BOX_DEV_TOKEN (no AP, no OAuth). "
                        "Fires via POST /api/events/box/poll; test with live_box_direct_check.py."
                    )
        # No INTEGRATION auto-connects from an env token any more: github is OAuth (piece-github takes
        # OAUTH2 only — GITHUB_TOKEN does NOT connect it), gmail/box are OAuth, box-direct uses its own
        # token path. So an unconnected integration means "connect it" (OAuth consent), never "wait for
        # auto-connect". The env-token auto-connect path now applies only to CHANNELS (telegram/discord
        # bot tokens), which are reported by channels_status, not here.
        return {"integrations": rows}

    @app.get("/api/events/agents")
    async def events_agents(request: Request):
        """The pre-built worker fleet (geobot, pricebot, …) the concierge routes among. Dumb read:
        the UI renders the specs; visibility follows per-agent access (perms.can_use)."""
        from . import perms
        from .catalog import as_list as _examples

        p = _principal_from(request.query_params.get("scope"), request.headers)
        roles = ["user"]
        if users is not None:
            u = users.get(p.user_id, p.tenant_id)
            if u:
                roles = u.roles
        agent_scope = "/".join(p.scope.split("/")[:2]) or p.scope
        specs = runtime.list_agents(scope=agent_scope) if runtime is not None else []
        # per-agent example utterances (from the catalog) so the UI can show "try this" per agent
        by_agent: dict[str, list[str]] = {}
        for ex in _examples():
            by_agent.setdefault(ex.get("agent", ""), []).append(ex.get("utterance", ""))
        agents = []
        for s in specs:
            usable = perms.can_use(s, roles=roles, user_id=p.user_id)
            agents.append(
                {
                    "name": s.name,
                    "prompt": getattr(s, "prompt", ""),
                    "backend": getattr(s, "backend", ""),
                    "mcp_servers": list(getattr(s, "mcp_servers", []) or []),
                    "channels": list(getattr(s, "channels", []) or []),
                    "integrations": list(getattr(s, "integrations", []) or []),
                    "access": list(getattr(s, "access", []) or []),
                    "restricted": bool(getattr(s, "access", []) or []),
                    "examples": [u for u in by_agent.get(s.name, []) if u][:3],
                    "can_use": usable,
                }
            )
        agents.sort(key=lambda a: a["name"])
        return {"scope": agent_scope, "agents": agents}

    @app.get("/api/events/mcp-servers")
    async def events_mcp_servers():
        """The tool servers a builder can attach to an agent (name + one-line hint). Drives the
        Agent-editor form so the UI never hardcodes the catalog. Also returns the MCP registry URL so
        the Studio can link out to the tool explorer (its interactive /docs)."""
        from . import mcp_catalog

        registry_url = (os.environ.get("EVENTS_REGISTRY_URL") or "http://localhost:8001").rstrip("/")
        return {
            "servers": [{"name": n, "hint": mcp_catalog.HINTS.get(n, "")} for n in mcp_catalog.known_names()],
            "registry_url": registry_url,
            "explorer_url": f"{registry_url}/docs",
        }

    def _agent_spec_from_body(body: dict):
        """Validate an agent-editor payload → AgentSpec (or a (None, error) pair)."""
        from .runtime import AgentSpec
        from . import mcp_catalog

        name = (body.get("name") or "").strip()
        if not name or any(ch.isspace() for ch in name):
            return None, "name is required and must not contain whitespace"
        backend = (body.get("backend") or "cuga").strip()
        if backend not in ("react", "cuga"):
            return None, "backend must be 'react' or 'cuga'"
        known = set(mcp_catalog.known_names())
        mcp = [str(m) for m in (body.get("mcp_servers") or [])]
        bad = [m for m in mcp if m not in known]
        if bad:
            return None, f"unknown mcp_servers: {bad} (known: {sorted(known)})"
        channels = [str(c) for c in (body.get("channels") or [])]
        bad_ch = [c for c in channels if c not in ("web", "telegram", "slack", "discord")]
        if bad_ch:
            return None, f"unknown channels: {bad_ch}"
        from . import triggers as _tr

        integrations = []
        for it in body.get("integrations") or []:
            app_name = (it.get("app") or "").strip() if isinstance(it, dict) else ""
            own = (it.get("ownership") or "per-user") if isinstance(it, dict) else "per-user"
            if not app_name:
                return None, "each integration needs an 'app'"
            if own not in ("shared", "per-user"):
                return None, "integration ownership must be 'shared' or 'per-user'"
            entry = {"app": app_name, "ownership": own}
            # trigger-grain declaration: WHICH of the app's triggers this agent handles. Absent/empty
            # = all of them. Validated against the registry, stored in canonical event names — the
            # editor used to drop this field on save, silently widening the agent to every trigger.
            trigs = []
            for ev in (it.get("triggers") or []) if isinstance(it, dict) else []:
                row = _tr.get(app_name, str(ev))
                if row is None:
                    known = ", ".join(t.event for t in _tr.events_for(app_name)) or "none"
                    return None, (f"unknown trigger '{app_name}/{ev}' (known for {app_name}: {known})")
                if row.event not in trigs:
                    trigs.append(row.event)
            if trigs:
                entry["triggers"] = trigs
            integrations.append(entry)
        access = [str(a) for a in (body.get("access") or [])]
        return AgentSpec(
            name=name,
            prompt=body.get("prompt", "") or "",
            backend=backend,
            mcp_servers=mcp,
            channels=channels,
            integrations=integrations,
            access=access,
        ), None

    @app.post("/api/events/agents")
    async def create_agent(request: Request):
        """Builder: create (or upsert) a worker agent from the Studio. Builder/admin only.
        Agents are TENANT-shared (stored at agent_scope), so the whole tenant's users can route to
        the new agent immediately. Idempotent by name (re-POST = update)."""
        body = await _safe_json(request)
        p = _principal_from(body.get("scope") or request.query_params.get("scope"), request.headers)
        if not _is_builder(p):
            return JSONResponse({"ok": False, "error": "builder or admin only"}, 403)
        spec, err = _agent_spec_from_body(body)
        if err:
            return JSONResponse({"ok": False, "error": err}, 400)
        agent_scope = "/".join(p.scope.split("/")[:2]) or p.scope
        try:
            runtime.upsert_agent(spec, scope=agent_scope)
        except RuntimeError as e:
            # single-agent world: sub-agents are defined in supervisor_agents.yaml (canonical
            # CUGA-main schema) — the fleet registration API is retired, and says so.
            return JSONResponse({"ok": False, "error": str(e)}, 410)
        except Exception as e:  # noqa: BLE001
            return JSONResponse({"ok": False, "error": str(e)}, 500)
        Trace(new_trace_id())("agent.upsert", name=spec.name, scope=agent_scope, backend=spec.backend)
        return {"ok": True, "name": spec.name, "scope": agent_scope}

    @app.put("/api/events/agents/{name}")
    async def update_agent(name: str, request: Request):
        """Builder: update an existing agent (must already exist). Builder/admin only."""
        body = await _safe_json(request)
        body.setdefault("name", name)
        if (body.get("name") or "").strip() != name:
            return JSONResponse({"ok": False, "error": "name in body must match the URL"}, 400)
        p = _principal_from(body.get("scope") or request.query_params.get("scope"), request.headers)
        if not _is_builder(p):
            return JSONResponse({"ok": False, "error": "builder or admin only"}, 403)
        agent_scope = "/".join(p.scope.split("/")[:2]) or p.scope
        if runtime.get_agent(name, scope=agent_scope) is None:
            return JSONResponse({"ok": False, "error": f"no such agent '{name}'"}, 404)
        spec, err = _agent_spec_from_body(body)
        if err:
            return JSONResponse({"ok": False, "error": err}, 400)
        try:
            runtime.upsert_agent(spec, scope=agent_scope)
        except RuntimeError as e:
            return JSONResponse({"ok": False, "error": str(e)}, 410)  # single-agent world
        except Exception as e:  # noqa: BLE001
            return JSONResponse({"ok": False, "error": str(e)}, 500)
        Trace(new_trace_id())("agent.update", name=spec.name, scope=agent_scope)
        return {"ok": True, "name": spec.name, "scope": agent_scope}

    @app.get("/api/events/examples")
    async def events_examples():
        """The example catalog straight from catalog.py — the utterances the Studio Examples tab lists
        and the source of truth for what this platform can be asked to do."""
        from .catalog import as_list

        return {"examples": as_list()}

    @app.get("/api/events/triggers")
    async def events_triggers():
        """The trigger registry — every (integration, event) the platform can watch, straight from
        triggers.py. Drives the Studio's agent editor (trigger-grain declarations) and the slides,
        so neither can drift from the code. Grouped per app, default trigger first."""
        from . import triggers as _tr

        out = []
        for app_name in _tr.apps():
            rows = []
            for t in sorted(_tr.events_for(app_name), key=lambda x: (not x.default, x.event)):
                rows.append(
                    {
                        "event": t.event,
                        "title": t.title,
                        "backend": t.backend,
                        "default": t.default,
                        "fire": t.fire,
                        "piece": t.piece,
                        "ap_trigger": t.ap_trigger,
                        "direct_kind": t.direct_kind,
                        "slots": [
                            {"name": n, "question": _tr.SLOTS[n].question, "required": _tr.SLOTS[n].required}
                            for n in t.slots
                        ],
                    }
                )
            out.append({"app": app_name, "triggers": rows})
        return {"apps": out, "total": len(_tr.rows()), "kinds": list(_tr.event_kinds())}

    @app.get("/api/events/setup-guides")
    async def events_setup_guides(request: Request):
        """Per-connector 'how to connect' guides for the Studio (creds, ownership, steps) PLUS the
        live connection status — the ACTUAL 'am I connected' (an AP connection / direct token exists),
        which is distinct from 'is the credential in .env'. Also tags each as USER vs TENANT."""
        from . import setup_guides
        from .connectors import channels_status, integrations_status

        pub = os.environ.get("EVENTS_PUBLIC_URL", "").rstrip("/") or "<EVENTS_PUBLIC_URL>"
        # live connection status per connector
        p = resolve_principal(headers=request.headers)
        conns = None
        if engine is not None:
            try:
                grain = os.environ.get("EVENTS_AP_PROJECT_GRAIN", "tenant")
                conns = await engine.list_connections(project_name=p.ap_project_name(grain))
            except Exception:  # noqa: BLE001 — AP down → 'unknown', never 500
                conns = None
        st: dict[str, str] = {}
        for c in channels_status():
            st[c["name"]] = c["status"]
        for i in integrations_status(conns, ap_configured=engine is not None):
            st[i.get("app") or i["name"]] = i["status"]
        if os.environ.get("EVENTS_BOX_BACKEND", "").lower() == "direct":  # box direct = a USER token
            from . import box_direct

            st["box"] = "connected" if box_direct.token() else "not_connected"
        out = []

        def _cred_scope(key: str) -> str:
            # TENANT: OAuth *app* creds + channel bot tokens (one per org). USER: personal tokens/PATs.
            k = key.upper()
            if (
                k.startswith("EVENTS_OAUTH_")
                or k.endswith("_BOT_TOKEN")
                or k.endswith("_SIGNING_SECRET")
                or k.endswith("_BOT_USERNAME")
            ):
                return "tenant"
            return "user"  # GITHUB_TOKEN, BOX_DEV_TOKEN, …

        for g in setup_guides.as_list():
            creds = [
                {**c, "present": bool(os.environ.get(c["key"])), "scope": _cred_scope(c["key"])}
                for c in g.get("creds", [])
            ]
            steps = [s.replace("<EVENTS_PUBLIC_URL>", pub) for s in g.get("steps", [])]
            # how you connect it (drives the Studio's button): oauth consent · paste token · direct · none
            # a guide may declare `connect` explicitly (e.g. Box's default direct-token path); else derive.
            app = g["app"]
            if g.get("connect"):
                connect = g["connect"]
            elif app == "slack":
                connect = "direct"
            else:
                # Ask the provider registry, not the shape of the guide's cred list. Deriving "token"
                # from "the guide mentions GITHUB_TOKEN" told users to paste a PAT into an OAuth-only
                # piece for months. oauth.connect_kind is the single source of truth.
                from . import oauth as _oauth

                connect = _oauth.connect_kind(app) or ("token" if g.get("creds") else "none")
            status = st.get(app, "n/a")
            # the CONNECTION is per-USER for integrations (each user logs in) · TENANT for channels (one bot)
            conn_scope = "user" if g.get("kind") == "integration" else "tenant"
            out.append(
                {
                    **g,
                    "creds": creds,
                    "steps": steps,
                    "connect": connect,
                    "conn_status": status,
                    "connected": status == "connected",
                    "connection_scope": conn_scope,
                    "needs_connection": connect != "none",
                }
            )
        return {"public_url": pub, "guides": out}

    # --- DIRECT Slack (default backend; no AP) — Slack Events API → /invoke → chat.postMessage -----
    @app.get("/api/events/slack/events")
    async def slack_events_probe():
        """A human opened this URL in a browser. Say so, instead of a bare 405.

        Slack only ever POSTs here, so GET is genuinely Not Allowed — but "405 Method Not Allowed"
        in a browser reads exactly like a broken endpoint, and costs an afternoon of debugging the
        wrong thing. Answer the question the person is actually asking: is this the right URL, and
        is it alive?
        """
        return {
            "ok": True,
            "endpoint": "slack events",
            "note": (
                "This endpoint is healthy. Slack delivers events by POST — a GET from a "
                "browser cannot exercise it. Paste this exact URL into Slack → your app → "
                "Event Subscriptions → Request URL and it will verify."
            ),
            "self_test": (
                "curl -X POST <this-url> -H 'Content-Type: application/json' "
                "-d '{\"type\":\"url_verification\",\"challenge\":\"probe\"}'  "
                "# should print: probe"
            ),
            "wrong_host_hint": (
                "Slack points at the EVENTING SERVICE (cuga-events-svc), never at "
                "CUGA (cuga-core) — CUGA answers 405 here and Slack reports "
                "'your request URL returned an HTTP error'."
            ),
        }

    @app.post("/api/events/slack/events")
    async def slack_events(request: Request):
        """Slack Events API receiver. Handles the url_verification handshake, verifies the request
        signature, and (for a real human message) answers via the concierge + posts the reply back.
        Acks in <3s and does the slow agent work in the background (Slack's timeout)."""
        import asyncio
        import json as _json
        from . import slack_direct

        raw = (await request.body()).decode("utf-8", "replace")
        try:
            body = _json.loads(raw or "{}")
        except Exception:  # noqa: BLE001
            return JSONResponse({"ok": False, "error": "bad json"}, 400)
        # 1) URL verification handshake (must echo the challenge; no signature yet)
        if body.get("type") == "url_verification":
            return PlainTextResponse(body.get("challenge", ""))
        # 2) verify it's really Slack
        ok_sig, why = slack_direct.verify_signature(request.headers, raw)
        if not ok_sig:
            Trace(new_trace_id()).error("slack", reason=why)
            return JSONResponse({"ok": False, "error": why}, 401)
        # 3a) a real human message → answer it (in the background; ack now). Channel-message
        #     WATCHERS are matched inside /invoke (one seam for slack/discord/telegram messages),
        #     so nothing extra happens here for messages.
        ev = body.get("event") or {}
        if slack_direct.should_process(ev):
            # EVENTS_SLACK_CHAT=mention: only messages that @mention the bot reach CHAT (DMs always
            # do). A gated message still feeds channel-message WATCHERS below — they normally ride
            # the chat path's /invoke, so skipping chat must not skip them.
            to_chat, text = await slack_direct.mention_gate(ev)
            if to_chat and _slack_first_touch(ev.get("ts")):
                # thread identity: a threaded reply carries thread_ts; a root message uses its own
                # ts so the bot's reply STARTS a thread; the conversation stays in that thread.
                thread_ts = ev.get("thread_ts") or ev.get("ts")
                asyncio.create_task(_slack_answer(text, ev.get("channel", ""), ev.get("user", ""), thread_ts))
            elif store is not None:
                from . import direct_events

                subs = direct_events.match(
                    store,
                    "slack",
                    "new_channel_message",
                    channel=str(ev.get("channel") or ""),
                    text=str(ev.get("text") or ""),
                )
                if subs:
                    Trace(new_trace_id())(
                        "slack.direct", event="new_channel_message", matched=len(subs), gated="mention"
                    )
                    asyncio.create_task(
                        direct_events.dispatch_all(
                            subs,
                            app="slack",
                            event="new_channel_message",
                            payload={"text": ev.get("text") or "", "channel": ev.get("channel") or ""},
                            engine=engine,
                        )
                    )
            return {"ok": True}
        # 3a-bis) an APP MENTION (`@bot …`) is unambiguously addressed to the bot → answer it in CHAT,
        #     even under EVENTS_SLACK_CHAT=mention. Slack delivers `app_mention` (NOT `message`) when the
        #     app is subscribed to app-mention events but not `message.channels`, so WITHOUT this an
        #     @mention silently gets no reply (the exact "@eda-test-app what's the weather" symptom).
        #     Dedup on ts so we don't double-answer when both subscriptions are on. Falls through to 3b
        #     so any `new_slack_mention` watcher still fires too.
        if (
            ev.get("type") == "app_mention"
            and ev.get("text")
            and ev.get("channel")
            and _slack_first_touch(ev.get("ts"))
        ):
            _uid = await slack_direct.bot_user_id()
            _txt = ev.get("text") or ""
            if _uid:
                _txt = _txt.replace(f"<@{_uid}>", " ").strip()
            _thread_ts = ev.get("thread_ts") or ev.get("ts")
            asyncio.create_task(_slack_answer(_txt, ev.get("channel", ""), ev.get("user", ""), _thread_ts))
        # 3b) every OTHER event type (app_mention → new_slack_mention watchers, reaction_added,
        #     channel_created, team_join, emoji_changed, star_added, …) → the DIRECT watcher dispatcher.
        #     These used to be silently dropped at should_process — 15 of Slack's event types were
        #     unreachable. Requires the Slack app to be SUBSCRIBED to the event (the events docs (setup/SLACK.md)).
        ev_type = ev.get("type") or ""
        if ev_type and store is not None:
            from . import direct_events

            kind = direct_events.kind_for("slack", ev_type)
            if kind:
                item = ev.get("item") or {}
                subs = direct_events.match(
                    store,
                    "slack",
                    kind,
                    channel=str(ev.get("channel") or item.get("channel") or ""),
                    text=str(ev.get("text") or ""),
                    emoji=str(ev.get("reaction") or ""),
                )
                if subs:
                    Trace(new_trace_id())("slack.direct", event=kind, matched=len(subs))
                    asyncio.create_task(
                        direct_events.dispatch_all(
                            subs, app="slack", event=kind, payload=dict(ev), engine=engine
                        )
                    )
        return {"ok": True}

    async def _slack_answer(text: str, channel: str, user: str, thread_ts: str | None = None) -> None:
        """Route a Slack message through CUGA's /run and post the reply BACK INTO THE THREAD.

        CUGA IS THE DOOR: this adapter makes no routing decision — it owns the Slack token and the
        thread bookkeeping, nothing more. /run decides chat-vs-arming and calls the eventing layer
        only when the utterance is a slash verb or continues an open arming dialogue.

        thread_ts keys conversation memory per-thread (one Slack thread = one topic); the native id
        for identity/delivery stays the channel — ``channel_native_id`` strips the ``#<ts>``."""
        from . import cuga_door, slack_direct

        tr = Trace(new_trace_id())
        try:
            answer = await cuga_door.ask(
                text, channel="slack", native_id=channel, user=user, locus=thread_ts or ""
            )
            if answer:
                res = await slack_direct.send_message(channel, answer, thread_ts=thread_ts)
                tr("slack.reply", channel=channel, thread=thread_ts, ok=res.get("ok"))
            else:
                tr.error("slack", reason="no answer from CUGA /run")
        except Exception as e:  # noqa: BLE001
            tr.error("slack", err=str(e))

    # --- DIRECT Box (OAuth-free watcher; polls Box's API with a token) -----------------------------
    @app.post("/api/events/box/poll")
    async def box_poll(request: Request):
        """Poll a Box folder for NEW files and fire the watcher agent on each — the OPT-IN direct Box
        path (behind EVENTS_BOX_BACKEND=direct; sidesteps AP's OAuth wall + paid-app webhook). Box
        defaults to the AP PUSH trigger (create_push_flow) — this endpoint is the manual/AP-free
        alternative you drive/schedule yourself. Gateway-token protected. Body:
        {folder_id, since?, agent?, deliver_to?, scope?}. Returns the new files + newest created_at."""
        if token and request.headers.get("X-Gateway-Token") != token:
            return JSONResponse({"ok": False, "error": "bad or missing X-Gateway-Token"}, 401)
        from . import box_direct

        body = await _safe_json(request)
        # AP's HTTP action posts the body nested under "data" ({"data": {...}}, see _http_action).
        # A flat/manual caller posts the fields at top level. Unwrap so BOTH shapes read the same —
        # without this, an AP schedule tick loses folder_id/agent/deliver_to and silently polls root.
        if isinstance(body.get("data"), dict):
            body = body["data"]
        folder = str(body.get("folder_id") or "0")
        # `since` in the body wins (manual/test poll); otherwise use the SERVER-tracked last-seen for
        # this folder, so a standing scheduled poll fires only on files added since the previous run.
        since = body.get("since")
        server_tracked = since is None
        if server_tracked:
            since = box_direct.load_since(folder)
        agent = body.get("agent") or "resume_judge"
        deliver_to = body.get("deliver_to")  # e.g. a direct channel: "slack"
        deliver_target = body.get("deliver_target")  # the channel-native id (e.g. Slack channel id)
        # WHICH box event to poll for: new_file (default — the proven resume path), new_folder,
        # or new_box_comment. One poller, three trigger kinds (the trigger registry's box rows).
        kind = (body.get("kind") or "new_file").replace("-", "_")
        tr = Trace(new_trace_id())
        try:
            if kind == "new_folder":
                items = await box_direct.new_folders_since(folder, since)
            elif kind == "new_box_comment":
                items = await box_direct.new_comments_since(folder, since)
            else:
                kind = "new_file"
                items = await box_direct.new_files_since(folder, since)
        except Exception as e:  # noqa: BLE001
            tr.error("box.poll", folder=folder, kind=kind, err=str(e))
            return JSONResponse({"ok": False, "error": str(e)}, 502)
        tr("box.poll", folder=folder, kind=kind, new=len(items), since=since)
        processed, newest = [], since or ""
        # Resolved once per poll, not per file: a JD in a Box file would otherwise be downloaded
        # once for every resume in the folder.
        jd = await _resume_jd(body) if kind == "new_file" else ""
        for f in items:
            newest = max(newest, f.get("created_at") or "")
            if kind == "new_file":
                await _box_dispatch(agent, f, deliver_to, body.get("scope"), deliver_target, jd=jd)
                processed.append({"id": f["id"], "name": f.get("name")})
            else:
                # folder/comment events carry their metadata straight to the agent — there is no
                # file body to download. Same /invoke seam, same delivery mechanics.
                await _box_event_dispatch(agent, kind, f, deliver_to, body.get("scope"), deliver_target)
                processed.append({"id": f.get("id"), "name": f.get("name") or f.get("message", "")[:60]})
        if server_tracked and newest and newest != (since or ""):
            box_direct.save_since(folder, newest)  # advance the watermark for the next poll
        return {
            "ok": True,
            "folder": folder,
            "kind": kind,
            "processed": processed,
            "newest": newest,
            "trace_id": tr.id,
        }

    async def _box_event_dispatch(
        agent: str, kind: str, item: dict, deliver_to, scope, deliver_target=None
    ) -> None:
        """Fire one non-file Box event (a new folder / a new comment) through /invoke(agent)."""
        import httpx as _httpx
        from . import delivery

        tr = Trace(new_trace_id())
        port = os.environ.get("EVENTS_CUGA_PORT", "7860")
        gw = (os.environ.get("GATEWAY_TOKEN", "") or "").split(" #", 1)[0].strip()
        direct = bool(deliver_to and delivery.is_direct(deliver_to))
        if kind == "new_folder":
            text = (
                f"A new folder appeared in the watched Box folder: '{item.get('name')}' "
                f"(id {item.get('id')}, created {item.get('created_at')}). "
                "Summarize what this means and suggest the next step."
            )
        else:
            text = (
                f"A new comment was posted on Box file '{item.get('file')}' by "
                f"{item.get('by') or 'someone'}: \"{item.get('message', '')}\". "
                "Summarize the comment and flag any action items."
            )
        src = (
            {"type": "channel", "name": deliver_to, "thread_id": f"gw:{deliver_to}:{deliver_target}"}
            if (direct and deliver_target)
            else {"type": "integration", "name": "box", "thread_id": f"box:{kind}:{item.get('id')}"}
        )
        inv = {
            "agent": agent,
            "text": text,
            "deliver": bool(direct and deliver_target),
            "source": src,
            "event": {"kind": kind, "payload": dict(item)},
        }
        if scope:
            inv["scope"] = scope
        try:
            async with _httpx.AsyncClient(timeout=180) as c:
                await c.post(f"http://127.0.0.1:{port}/invoke", headers={"X-Gateway-Token": gw}, json=inv)
            tr("box.event", kind=kind, agent=agent, ok=True)
        except Exception as e:  # noqa: BLE001
            tr.error("box.event", kind=kind, err=str(e))

    # --- SYNTHETIC FIRE (connection-free trigger testing) ------------------------------------------
    @app.post("/api/events/synth-fire")
    async def synth_fire(request: Request):
        """Fire ANY registry trigger with a piece-exact SYNTHETIC payload at the /invoke seam — no
        Activepieces flow, no connection, no real event. This is how you test a polling/AP trigger
        (calendar / pinterest / youtube / rss / gmail / box) whose real fire needs a live connection.

        It reproduces exactly what a real event would deliver: the agent runs on the same payload
        shape (the registry's ``synth`` sample, or one you POST) with the same event.kind, and — if
        you pass deliver_to/deliver_target — delivers to that channel just like the real watcher.

        Gateway-token protected. Body: {source, event?, prompt?, agent?, payload?, deliver_to?,
        deliver_target?, scope?}. ``event`` defaults to the source's default trigger; ``payload``
        overrides the registry synth; ``prompt`` overrides the agent instruction.
        Disable with EVENTS_DEBUG_RUN=0 (same switch as /run)."""
        if os.environ.get("EVENTS_DEBUG_RUN", "1") == "0":
            return JSONResponse({"ok": False, "error": "debug endpoint disabled (EVENTS_DEBUG_RUN=0)"}, 403)
        if token and request.headers.get("X-Gateway-Token") != token:
            return JSONResponse({"ok": False, "error": "bad or missing X-Gateway-Token"}, 401)
        from . import triggers as _triggers

        body = await _safe_json(request)
        source = (body.get("source") or "").strip()
        row = _triggers.get(source, body.get("event") or "")
        if row is None:
            return JSONResponse(
                {"ok": False, "error": f"no trigger for source={source!r} event={body.get('event')!r}"}, 404
            )
        if row.backend != "ap":
            return JSONResponse(
                {
                    "ok": False,
                    "error": f"{row.app}/{row.event} is a DIRECT trigger — "
                    "fire it through its real transport (or /api/events/box/poll for box)",
                },
                400,
            )
        payload = body.get("payload") or row.synth
        if not payload:
            return JSONResponse(
                {
                    "ok": False,
                    "error": f"{row.app}/{row.event} has no synth sample; pass an explicit 'payload'",
                },
                422,
            )
        agent = body.get("agent") or "cuga"
        prompt = (
            body.get("prompt")
            or f"A {row.title} event just fired on {row.app}. "
            "Summarize what happened and what I should do next."
        )
        deliver_to = body.get("deliver_to")
        deliver_target = body.get("deliver_target")
        direct = bool(deliver_to and deliver_target)
        src = (
            {"type": "channel", "name": deliver_to, "thread_id": f"gw:{deliver_to}:{deliver_target}"}
            if direct
            else {"type": "integration", "name": row.app, "thread_id": f"synth:{row.app}:{row.event}"}
        )
        inv = {
            "agent": agent,
            "text": prompt,
            "deliver": direct,
            "scope": body.get("scope") or "",
            "source": src,
            "event": {"kind": row.event, "payload": {**payload, "_raw": payload}},
        }
        tr = Trace(new_trace_id())
        import httpx as _httpx

        port = os.environ.get("EVENTS_CUGA_PORT", "7860")
        gw = (os.environ.get("GATEWAY_TOKEN", "") or "").split(" #", 1)[0].strip()
        try:
            async with _httpx.AsyncClient(timeout=180) as c:
                r = await c.post(f"http://127.0.0.1:{port}/invoke", headers={"X-Gateway-Token": gw}, json=inv)
            answer = ""
            try:
                answer = (r.json() or {}).get("answer") or ""
            except Exception:  # noqa: BLE001
                answer = r.text[:400]
            tr("synth.fire", source=row.app, event=row.event, delivered=direct, ok=r.status_code == 200)
            return {
                "ok": r.status_code == 200,
                "source": row.app,
                "event": row.event,
                "delivered_to": deliver_to if direct else None,
                "answer": answer,
                "trace_id": tr.id,
            }
        except Exception as e:  # noqa: BLE001
            tr.error("synth.fire", err=str(e))
            return JSONResponse({"ok": False, "error": str(e)}, 502)

    async def _resume_jd(body: dict) -> str:
        """The job description a resume is judged against.

        The watcher's prompt has always said "judge fit vs the JD" while supplying no JD, so the agent
        could only ever ask for one — and, delivered to a channel, that question reaches nobody who can
        answer it. Sources, in order: the poll body, a Box file id, the environment.
        """
        from . import box_direct

        jd = (body or {}).get("jd") or ""
        if jd:
            return jd
        fid = (body or {}).get("jd_file_id") or os.environ.get("EVENTS_RESUME_JD_FILE_ID", "")
        if fid:
            got = await box_direct.fetch_content(fid, "job description")
            if got["kind"] == "text":
                return got["text"]
            Trace(new_trace_id()).error("box.jd", file=fid, kind=got["kind"], reason=got.get("reason", ""))
        return os.environ.get("EVENTS_RESUME_JD", "")

    async def _box_dispatch(
        agent: str, file: dict, deliver_to, scope, deliver_target=None, jd: str = ""
    ) -> None:
        """Fire one Box file through /invoke(agent). If deliver_to is a DIRECT channel the reply is
        sent CUGA-side (no AP); otherwise the answer just rides back in the /invoke response.
        ``deliver_target`` is the channel-native id (e.g. the Slack channel) so the answer lands in
        the right place — without it a direct sink has no destination."""
        import httpx
        from . import box_direct, delivery

        tr = Trace(new_trace_id())
        port = os.environ.get("EVENTS_CUGA_PORT", "7860")
        gw = (os.environ.get("GATEWAY_TOKEN", "") or "").split(" #", 1)[0].strip()
        direct = bool(deliver_to and delivery.is_direct(deliver_to))
        src = (
            {"type": "channel", "name": deliver_to, "thread_id": f"gw:{deliver_to}:{deliver_target or ''}"}
            if direct
            else {"type": "integration", "name": "box", "thread_id": f"box:{file['id']}"}
        )

        # THE DOWNLOAD STEP. A watcher that knows only the filename cannot judge a resume, and the
        # agent holds no Box credential to fetch it with. So the server fetches: CUGA downloads the
        # bytes with the token it already has and hands the agent CONTENT. The credential never
        # leaves this process. See box_direct.fetch_content for the caps.
        name = file.get("name") or file["id"]
        content = await box_direct.fetch_content(file["id"], name)
        tr("box.download", file=name, kind=content["kind"], bytes=content.get("bytes"))
        if content["kind"] == "text":
            body = content["text"] + ("\n…[truncated]" if content.get("truncated") else "")
            excerpt = f"\n\n--- contents of {name} ---\n{body}\n--- end ---"
        elif content["kind"] == "binary":
            excerpt = (
                f"\n\nThe file is binary ({content['bytes']} bytes). Its base64 is in "
                f"event.payload.file_base64 — decode it with extract_text_from_bytes."
            )
        else:
            excerpt = (
                f"\n\nThe file could not be downloaded ({content['reason']}). Judge from the "
                f"filename alone, or say you could not read it — do not invent its contents."
            )

        ev_payload = {"file_id": file["id"], "name": name, "content_kind": content["kind"]}
        if content["kind"] == "binary":
            ev_payload["file_base64"] = content["base64"]
        elif content["kind"] == "skipped":
            ev_payload["download_error"] = content["reason"]

        # No JD means no judgement. Say so, rather than asking a question into a channel where nobody
        # is listening — a watcher runs unattended, so "please provide the JD" is a dead end.
        if jd:
            ask = (
                "Judge this candidate's fit against the job description below. "
                "Start your reply with MATCH or SKIP, then give two lines of reasoning citing the "
                f"resume.\n\n--- job description ---\n{jd}\n--- end ---"
            )
            ev_payload["has_jd"] = True
        else:
            ask = (
                "No job description is configured, so do NOT ask for one — nobody is reading. "
                "Start your reply with SKIP, say the JD is missing, and summarise the candidate in "
                "two lines so a human can triage."
            )
            ev_payload["has_jd"] = False

        payload = {
            "agent": agent,
            "deliver": bool(direct),
            "scope": scope or "",
            "text": f"A file '{name}' landed in Box. {ask}{excerpt}",
            "source": src,
            "event": {"kind": "new_file", "payload": ev_payload},
        }
        try:
            async with httpx.AsyncClient(timeout=180) as c:
                r = await c.post(
                    f"http://127.0.0.1:{port}/invoke", headers={"X-Gateway-Token": gw}, json=payload
                )
            tr("box.dispatch", file=file.get("name"), ok=r.status_code == 200, deliver=deliver_to)
        except Exception as e:  # noqa: BLE001
            tr.error("box.dispatch", file=file.get("name"), err=str(e))

    # --- GENERIC inbound WEBHOOK (direct, no AP) — any system POSTs a payload → agent → deliver -----
    @app.post("/api/events/hook/{name}")
    async def inbound_webhook(name: str, request: Request):
        """A generic inbound webhook: any external system (monitoring, CI, a form, a payment provider)
        POSTs a JSON payload to <EVENTS_PUBLIC_URL>/api/events/hook/<name> and CUGA runs an agent on it,
        optionally delivering the result to a channel.

        TWO WAYS to choose the agent — the whole point is you never have to guess wrong:

          * PINNED — ``?agent=<agent>`` (default ``incident_triage``). Deterministic: this exact agent
            handles every event. Right for a high-volume, known source (Datadog, Stripe) where you want
            one agent, one channel, fully observable. The caller must know the agent exists.

          * ROUTED — ``?route=1`` (aliases: ``true``/``yes``/``llm``/``concierge``). CUGA picks the
            best-fit agent the SAME WAY the chat concierge does for a Slack/web message: it lists the
            pre-built agents and routes by capability. The caller needs to know NOTHING about the agent
            catalog — rename or add agents and the URL never changes. It CANNOT ask a clarifying
            question (a webhook can't answer one), so if nothing fits it falls back to a generic triage
            rather than blocking. Costs one router pass per call; use PINNED for high firehose volume.

        Other query params: ?deliver_to=<channel> + ?target=<native id> (deliver the result to e.g. a
        Slack channel) · ?key=<secret> (required iff EVENTS_WEBHOOK_KEY is set).

        No AP — it reuses the /invoke seam. ROUTED is literally ``agent="concierge"`` on /invoke, the
        exact path an inbound channel message takes, so routing + delivery are shared with chat."""
        import hmac as _hmac
        import json as _json
        import httpx

        want = os.environ.get("EVENTS_WEBHOOK_KEY")
        if want and not _hmac.compare_digest(request.query_params.get("key") or "", want):
            return JSONResponse({"ok": False, "error": "bad or missing ?key"}, 401)
        payload = await _safe_json(request)
        route = (request.query_params.get("route") or "").strip().lower()
        routed = route in ("1", "true", "yes", "llm", "concierge")
        # ROUTED → the concierge router (agent picked by capability, like chat). PINNED → the named agent.
        agent = "concierge" if routed else (request.query_params.get("agent") or "incident_triage")
        deliver_to = request.query_params.get("deliver_to")
        target = request.query_params.get("target")
        tr = Trace(new_trace_id())
        tr("hook", name=name, agent=agent, routed=routed, deliver=deliver_to)
        body_txt = _json.dumps(payload, indent=2)[:4000] if payload else "(empty body)"
        if routed:
            # Phrase it as a one-shot AND force delegation: without the explicit "call an agent
            # tool" instruction the concierge sometimes handles an interesting payload (a code
            # diff) itself — then /invoke's meta carries no picked agent and the caller sees
            # agent='concierge', which defeats the point of routed mode.
            text = (
                f"An external system sent this event via webhook '{name}'. Route it: CALL the "
                f"one best-suited pre-built agent tool on this payload and return its answer — "
                f"do NOT answer it yourself and do NOT set up a recurring flow:\n\n{body_txt}"
            )
        else:
            text = f"An external system POSTed to webhook '{name}'. Triage this payload:\n\n{body_txt}"
        port = os.environ.get("EVENTS_CUGA_PORT", "7860")
        gw = (os.environ.get("GATEWAY_TOKEN", "") or "").split(" #", 1)[0].strip()
        deliver = bool(deliver_to and target)
        # ROUTED events are independent one-shots — give each a FRESH thread. On a shared
        # `hook:{name}` thread the router's memory of past events biases it toward answering
        # itself (it has seen the answer shape before), and the picked-agent meta degrades.
        hook_thread = f"hook:{name}:{tr.id}" if routed else f"hook:{name}"
        src = (
            {"type": "channel", "name": deliver_to, "thread_id": f"gw:{deliver_to}:{target}"}
            if deliver
            else {"type": "integration", "name": "webhook", "thread_id": hook_thread}
        )
        inv = {
            "agent": agent,
            "text": text,
            "deliver": deliver,
            "source": src,
            "event": {"kind": "message", "payload": payload if isinstance(payload, dict) else {}},
        }
        try:
            async with httpx.AsyncClient(timeout=180) as c:
                r = await c.post(f"http://127.0.0.1:{port}/invoke", headers={"X-Gateway-Token": gw}, json=inv)
            j = r.json() if r.status_code == 200 else {}
        except Exception as e:  # noqa: BLE001
            tr.error("hook", err=str(e))
            return JSONResponse({"ok": False, "webhook": name, "error": str(e)}, 502)
        # SINGLE-AGENT WORLD: the executor is always 'cuga' (the supervisor picks a specialist
        # internally, per wake-up). Surface 'cuga' for routed calls — the old "which agent did
        # the concierge choose" question is retired with fleet routing.
        resolved = (j.get("meta") or {}).get("agent") or agent
        if routed and resolved in ("", "concierge"):
            resolved = "cuga"
        return {
            "ok": r.status_code == 200,
            "webhook": name,
            "routed": routed,
            "agent": resolved,
            "answer": j.get("answer"),
            "delivered": deliver,
            "trace_id": tr.id,
        }

    # --- DIRECT Discord (default backend; a Gateway WebSocket bot — no AP, no public URL) ----------
    async def _discord_answer(msg: dict) -> None:
        """A Discord Gateway MESSAGE_CREATE → CUGA /run → reply back to the channel.
        CUGA IS THE DOOR: this adapter only owns the token and the reply; /run decides whether the
        utterance is chat or arming. thread_id keys memory per channel; user = the author.

        EVENTS_DISCORD_CHAT=mention: only @bot messages / DMs / replies-to-the-bot reach CHAT —
        a gated-out message still feeds the channel-message WATCHERS (mirrors the Slack gate)."""
        from . import discord_direct

        tr = Trace(new_trace_id())
        channel_id = str(msg.get("channel_id") or "")
        author = str((msg.get("author") or {}).get("id") or "")
        to_chat, text = discord_direct.mention_gate(msg)
        if not to_chat:
            if store is not None:
                from . import direct_events

                subs = direct_events.match(
                    store, "discord", "new_channel_message", channel=channel_id, text=msg.get("content") or ""
                )
                if subs:
                    tr("discord.direct", event="new_channel_message", matched=len(subs), gated="mention")
                    await direct_events.dispatch_all(
                        subs,
                        app="discord",
                        event="new_channel_message",
                        payload={"text": msg.get("content") or "", "channel": channel_id},
                        engine=engine,
                    )
            return
        try:
            from . import cuga_door

            answer = await cuga_door.ask(text, channel="discord", native_id=channel_id, user=author)
            if answer:
                # reply-to the asking message: the answer visibly attaches to its question
                res = await discord_direct.send_message(channel_id, answer, reply_to=str(msg.get("id") or ""))
                tr("discord.reply", channel=channel_id, ok=res.get("ok"))
            else:
                tr.error("discord", reason="no answer from CUGA /run")
        except Exception as e:  # noqa: BLE001
            tr.error("discord", err=str(e))

    # register the Gateway as a startup background task (direct is the default). The server's
    # lifespan launches app.state.events_background; nothing to arm (the bot connects on boot).
    if os.environ.get("EVENTS_DISCORD_BACKEND", "direct") != "ap":
        from . import discord_direct as _dd

        if _dd.bot_token():

            async def _discord_event(dispatch_type: str, data: dict) -> None:
                """Non-message Gateway dispatches (GUILD_MEMBER_ADD, MESSAGE_REACTION_ADD) → the
                direct watchers. Lift the channel + emoji so the channel/emoji filters can match a
                reaction (a member-join carries neither and matches on guild only)."""
                from . import direct_events

                kind = direct_events.kind_for("discord", dispatch_type)
                if not kind or store is None:
                    return
                chan = str(data.get("channel_id") or data.get("guild_id") or "")
                _emoji = data.get("emoji")
                emoji = (_emoji.get("name") or "") if isinstance(_emoji, dict) else ""
                subs = direct_events.match(store, "discord", kind, channel=chan, emoji=emoji)
                if subs:
                    Trace(new_trace_id())("discord.direct", event=kind, matched=len(subs))
                    await direct_events.dispatch_all(
                        subs, app="discord", event=kind, payload=dict(data), engine=engine
                    )

            async def _discord_gateway():
                await _dd.run_gateway(_discord_answer, on_event=_discord_event)

            _bg = list(getattr(app.state, "events_background", []) or [])
            _bg.append(_discord_gateway)
            app.state.events_background = _bg

    # --- DIRECT Telegram (default backend; long-poll — no AP, no public URL) -----------------------
    async def _telegram_answer(msg: dict) -> None:
        """A Telegram getUpdates message → CUGA /run → reply back to the chat. Mirrors
        _discord_answer: thread_id keys memory per chat; source.user = the sender (per-user identity).

        EVENTS_TELEGRAM_CHAT=mention: in a GROUP, only @bot messages reach CHAT (a private chat always
        does) — a gated-out message still feeds the channel-message WATCHERS (mirrors Slack/Discord)."""
        from . import telegram_direct

        tr = Trace(new_trace_id())
        chat_id = str((msg.get("chat") or {}).get("id") or "")
        sender = str((msg.get("from") or {}).get("id") or "")
        to_chat, text = telegram_direct.mention_gate(msg)
        if not to_chat:
            if store is not None:
                from . import direct_events

                subs = direct_events.match(
                    store, "telegram", "new_channel_message", channel=chat_id, text=msg.get("text") or ""
                )
                if subs:
                    tr("telegram.direct", event="new_channel_message", matched=len(subs), gated="mention")
                    await direct_events.dispatch_all(
                        subs,
                        app="telegram",
                        event="new_channel_message",
                        payload={"text": msg.get("text") or "", "channel": chat_id},
                        engine=engine,
                    )
            return
        try:
            from . import cuga_door

            answer = await cuga_door.ask(text, channel="telegram", native_id=chat_id, user=sender)
            if answer:
                res = await telegram_direct.send_message(chat_id, answer, reply_to=msg.get("message_id"))
                tr("telegram.reply", chat=chat_id, ok=res.get("ok"))
            else:
                tr.error("telegram", reason="no answer from CUGA /run")
        except Exception as e:  # noqa: BLE001
            tr.error("telegram", err=str(e))

    # register the long-poll loop as a startup background task (direct is the DEFAULT — no AP, no
    # tunnel). The AP webhook backend stays behind EVENTS_TELEGRAM_BACKEND=ap.
    if os.environ.get("EVENTS_TELEGRAM_BACKEND", "direct") != "ap":
        from . import telegram_direct as _tg

        if _tg.bot_token():

            async def _telegram_poller():
                await _tg.run_poller(_telegram_answer)

            _bg = list(getattr(app.state, "events_background", []) or [])
            _bg.append(_telegram_poller)
            app.state.events_background = _bg

    # --- NATIVE scheduler (AP-free cron/poll) — an in-process loop firing due native subscriptions.
    # No AP, no tunnel. Gated by EVENTS_SCHEDULER (native default | ap keeps the legacy AP-schedule).
    if store is not None:
        from . import native_scheduler as _nsched

        if _nsched.enabled():

            async def _native_scheduler():
                _port = os.environ.get("EVENTS_CUGA_PORT", "7860")
                _gw = (os.environ.get("GATEWAY_TOKEN", "") or "").split(" #", 1)[0].strip()
                await _nsched.run_scheduler(store, port=_port, token=_gw)

            _bg = list(getattr(app.state, "events_background", []) or [])
            _bg.append(_native_scheduler)
            app.state.events_background = _bg

    # Auto-connect .env USER tokens (single-operator convenience): a token set in .env becomes the
    # operator's AP connection on startup, so "set in .env" == "connected". Multi-user deployments
    # leave these blank and each user connects their own in the Studio.
    async def _autoconnect_env_tokens():
        if engine is None:
            return
        import logging as _lg
        from . import credentials, oauth

        log = _lg.getLogger("cuga.events")
        p = resolve_principal(headers={})  # the operator principal (EVENTS_USER_ID / defaults)
        grain = os.environ.get("EVENTS_AP_PROJECT_GRAIN", "tenant")
        # token-auth pieces whose secret sits in .env → create the operator's SECRET_TEXT AP
        # connection on startup, so "set in .env" == "connected" (mirrors the Studio's connect).
        # Without this the piece's inbound flow can't publish (AP: ConnectionNotFound) at arm time.
        #   · telegram — bot token, but only when the AP backend is selected (default is the direct
        #                long-poll, which needs no AP connection — same as Discord's Gateway)
        #   · discord  — bot token, but only when the AP backend is selected (default is the direct
        #                Gateway, which connects the socket on boot and needs no AP connection)
        #
        # github is DELIBERATELY absent. Its AP piece accepts only OAUTH2/CUSTOM_AUTH, so writing
        # GITHUB_TOKEN here as SECRET_TEXT produces a connection AP stores and the piece cannot use;
        # the flow then fails at publish with "401 Bad credentials" that reads like a scope problem.
        # GitHub connects through the OAuth consent flow: GET /api/events/connect/github.
        autoconn = []
        if os.environ.get("EVENTS_TELEGRAM_BACKEND", "direct") == "ap":
            autoconn.append(("telegram", os.environ.get("TELEGRAM_BOT_TOKEN", "")))
        if os.environ.get("EVENTS_DISCORD_BACKEND", "direct") == "ap":
            autoconn.append(("discord", os.environ.get("DISCORD_BOT_TOKEN", "")))
        import asyncio

        for app_name, raw in autoconn:
            tok = (raw or "").split(" #", 1)[0].strip()
            if not tok:
                continue
            ext = credentials.connection_external_id(app_name, "per-user", p)
            # AP can be briefly not-ready right after a cold co-start (make up/restart recreates the
            # container); retry with backoff so the channel still connects without a manual re-run.
            for attempt in range(1, 5):
                try:
                    if not await engine.connection_exists(ext, project_name=p.ap_project_name(grain)):
                        # update=False: booting must never clobber a connection the user authorized by
                        # hand with whatever stale value is sitting in .env. To ROTATE a token, use
                        # POST /api/events/connect/<app>/token (or the Studio's Connect button), which
                        # overwrites on purpose.
                        await engine.ensure_secret_connection(
                            ext,
                            oauth.provider(app_name)["piece"],
                            tok,
                            project_name=p.ap_project_name(grain),
                            update=False,
                        )
                    log.info("autoconnect: %s connected from .env (%s)", app_name, ext)
                    break
                except Exception as e:  # noqa: BLE001
                    if attempt == 4:
                        log.warning(
                            "autoconnect %s failed after %d attempts: %r", app_name, attempt, e, exc_info=True
                        )
                    else:
                        await asyncio.sleep(2 * attempt)

    if engine is not None and any(
        os.environ.get(k) for k in ("GITHUB_TOKEN", "TELEGRAM_BOT_TOKEN", "DISCORD_BOT_TOKEN")
    ):
        _bg = list(getattr(app.state, "events_background", []) or [])
        _bg.append(_autoconnect_env_tokens)
        app.state.events_background = _bg

    # --- per-user connect (CUGA hosts the OAuth; AP holds the token) -------------
    def _principal_from(scope: str | None, headers):
        from .principal import Principal, resolve as _resolve

        if scope and scope.count("/") == 2:
            t, i, u = scope.split("/")
            return Principal(tenant_id=t, instance_id=i, user_id=u)
        return _resolve(headers=headers)

    @app.get("/api/events/connect/{app}")
    async def connect_start(app: str, request: Request):
        """Begin connecting the caller's own account for ``app``. OAuth → redirect to consent;
        token app → tell the UI to collect a token."""
        from . import oauth

        p = _principal_from(request.query_params.get("scope"), request.headers)
        kind = oauth.connect_kind(app)
        if kind is None:
            return JSONResponse({"ok": False, "error": f"unknown app '{app}'"}, 404)
        if kind == "token":
            return {
                "ok": True,
                "app": app,
                "kind": "token",
                "message": f"POST your {app} token to /api/events/connect/{app}/token",
            }
        if not oauth.is_configured(app):
            return JSONResponse(
                {
                    "ok": False,
                    "app": app,
                    "kind": "oauth",
                    "error": f"OAuth not configured — set EVENTS_OAUTH_{app.upper()}_"
                    "CLIENT_ID / _CLIENT_SECRET",
                },
                501,
            )
        state = oauth.encode_state(
            scope=p.scope,
            app=app,
            agent=request.query_params.get("agent", ""),
            ownership=request.query_params.get("ownership", "per-user"),
            ret=request.query_params.get("return", ""),
        )
        return RedirectResponse(oauth.authorize_url(app, state), status_code=302)

    @app.get("/api/events/connect/{app}/callback")
    async def connect_callback(app: str, request: Request):
        """OAuth redirect target: exchange the code + create the user's AP connection.

        The ``state`` is HMAC-signed by /connect/{app} (oauth.encode_state) — a state that fails
        verification is a HARD reject, never a fallback to a header principal: an unsigned state
        let a crafted callback bind the new credential into an arbitrary scope."""
        from . import oauth, credentials

        code = request.query_params.get("code")
        raw_state = request.query_params.get("state", "")
        st = oauth.decode_state(raw_state)
        if raw_state and not st:
            return HTMLResponse(
                "<h3>Connect failed</h3><p>Bad or expired state — start again from "
                "the Connect button/link.</p>",
                400,
            )
        p = _principal_from(st.get("scope"), request.headers)
        if not code:
            return HTMLResponse("<h3>Connect failed</h3><p>No authorization code.</p>", 400)
        try:
            # AP does the code→token exchange itself (UpsertOAuth2Request wants the code, not tokens)
            own = "shared" if st.get("ownership") in ("tenant", "shared") else "per-user"
            ext = credentials.connection_external_id(app, own, p)
            if engine is not None:
                prov = oauth.provider(app)
                grain = os.environ.get("EVENTS_AP_PROJECT_GRAIN", "tenant")
                await engine.ensure_oauth_connection(
                    ext,
                    prov["piece"],
                    code,
                    client_id=os.environ.get(f"EVENTS_OAUTH_{app.upper()}_CLIENT_ID", ""),
                    client_secret=os.environ.get(f"EVENTS_OAUTH_{app.upper()}_CLIENT_SECRET", ""),
                    scope=" ".join(prov.get("scopes", [])),
                    redirect_url=oauth.redirect_uri(app),
                    authorization_method=prov.get("authorization_method", "BODY"),
                    project_name=p.ap_project_name(grain),
                )
        except Exception as e:  # noqa: BLE001
            # The trace keeps the detail; the RESPONSE must not. Echoing str(e) into HTML both
            # reflected user-controlled text (XSS) and leaked internals — AP URLs, connection ids —
            # to whoever opened the link. The trace id is the handle for anyone debugging.
            tr = Trace(new_trace_id())
            tr.error("connect", app=app, err=str(e))
            return HTMLResponse(
                f"<h3>Connect failed</h3><p>Something went wrong completing the connection. "
                f"Reference: {html.escape(tr.id)}</p>",
                500,
            )
        # `ret` comes from our own signed state, but its VALUE originated in a query param anyone
        # could put in a link — allow only same-origin/relative targets (no open redirect).
        ret = st.get("ret") or ""
        from . import oauth as _oauth

        if ret and (ret.startswith("/") or ret.startswith(_oauth.public_base())):
            return RedirectResponse(ret, status_code=302)
        # `app` is a PATH PARAMETER — anyone can put anything in the URL, so it must never reach the
        # page as-is (reflected XSS). Allow-list it against the provider registry and escape what
        # is left: the response can now only contain a name we ship.
        safe_app = html.escape(app) if oauth.provider(app) else "the app"
        return HTMLResponse(f"<h3>✅ {safe_app} connected</h3><p>You can return to your chat.</p>")

    @app.post("/api/events/connect/{app}/token")
    async def connect_token(app: str, request: Request):
        """Paste a raw credential → a SECRET_TEXT AP connection. ONLY for token-auth pieces
        (GitHub PAT, Telegram/Discord bot token).

        OAuth pieces (Box/Gmail/Slack/Outlook) CANNOT use a pasted token: AP's OAuth2 connection
        schema requires the authorization **code** and does the exchange itself — it will not accept
        a pre-obtained access/dev token. Those must go through the OAuth login at
        ``GET /api/events/connect/{app}`` (consent → callback). We return a clear 400 here instead
        of a cryptic AP validation error.
        """
        from . import oauth, credentials

        body = await request.json()
        token = (body or {}).get("token", "")
        prov = oauth.provider(app)
        if prov is None:
            return JSONResponse({"ok": False, "error": f"unknown app '{app}'"}, 404)
        if not token:
            return JSONResponse({"ok": False, "error": "missing 'token'"}, 400)
        if oauth.connect_kind(app) != "token":
            return JSONResponse(
                {
                    "ok": False,
                    "app": app,
                    "error": (
                        f"'{app}' is an OAuth connector — AP can't build a connection from a pasted token. "
                        f"Use the OAuth login: GET /api/events/connect/{app} (consent → callback)."
                    ),
                },
                400,
            )
        p = _principal_from((body or {}).get("scope"), request.headers)
        # ownership: 'tenant' (shared across the tenant) | 'per_user' (each user's own). Default per-user.
        own = "shared" if (body or {}).get("ownership") in ("tenant", "shared") else "per-user"
        ext = credentials.connection_external_id(app, own, p)
        if engine is None:
            return JSONResponse({"ok": False, "error": "AP not configured"}, 501)
        grain = os.environ.get("EVENTS_AP_PROJECT_GRAIN", "tenant")
        try:
            await engine.ensure_secret_connection(
                ext, prov["piece"], token, project_name=p.ap_project_name(grain)
            )
        except Exception as e:  # noqa: BLE001
            import traceback

            Trace(new_trace_id()).error("connect_token", app=app, err=repr(e), tb=traceback.format_exc())
            return JSONResponse({"ok": False, "error": repr(e) or type(e).__name__}, 500)
        out = {"ok": True, "app": app, "connection": ext}
        # AP stores any SECRET_TEXT connection without complaint, but a piece that does not ACCEPT
        # SECRET_TEXT will later run with no usable credential — and the failure surfaces far away, at
        # flow-publish time, as a bare "401 Bad credentials" that looks like a token-scope problem.
        # `piece-github` is the live example: OAUTH2 or CUSTOM_AUTH only. Say so here, at the paste.
        accepted = await _piece_auth_types(prov["piece"])
        if accepted and "SECRET_TEXT" not in accepted:
            out["warning"] = (
                f"Stored — but Activepieces' {prov['piece']} accepts only {', '.join(accepted)}, not "
                f"SECRET_TEXT. A flow using it will fail at publish with a credentials error that "
                f"looks like a token-scope problem. Connect this app via OAuth instead."
            )
            out["usable_by_piece"] = False
        return out

    async def _piece_auth_types(piece: str) -> list[str]:
        """Which connection types the AP piece will actually accept. [] if AP can't be asked."""
        import httpx

        if engine is None:
            return []
        try:
            async with httpx.AsyncClient(timeout=10) as c:
                r = await c.get(f"{engine.base}/api/v1/pieces/{piece}")
            auth = (r.json() or {}).get("auth") if r.status_code == 200 else None
        except Exception:  # noqa: BLE001
            return []
        if isinstance(auth, list):
            return [a.get("type") for a in auth if isinstance(a, dict) and a.get("type")]
        if isinstance(auth, dict) and auth.get("type"):
            return [auth["type"]]
        return []

    @app.get("/api/events/connections")
    async def list_user_connections(request: Request):
        """The caller's own AP connections (which integrations they've logged into)."""
        p = _principal_from(request.query_params.get("scope"), request.headers)
        if engine is None:
            return {"scope": p.scope, "connections": []}
        grain = os.environ.get("EVENTS_AP_PROJECT_GRAIN", "tenant")
        try:
            conns = await engine.list_connections(project_name=p.ap_project_name(grain))
        except Exception:  # noqa: BLE001
            conns = []
        mine = [c for c in conns if f"::{p.user_id}::" in (c.get("externalId") or "")]
        return {"scope": p.scope, "connections": mine}

    # --- profile (the identity anchor) + admin (users) — decision 0007 -----------
    @app.get("/api/events/me")
    async def me(request: Request):
        """The caller's profile: who they are, their roles, linked channels, connections."""
        p = _principal_from(request.query_params.get("scope"), request.headers)
        roles, email = ["user"], ""
        if users is not None:
            u = users.get(p.user_id, p.tenant_id)
            if u:
                roles, email = u.roles, u.email
        links = identity.links_for_user(p.tenant_id, p.user_id) if identity is not None else []
        conns = []
        if engine is not None:
            grain = os.environ.get("EVENTS_AP_PROJECT_GRAIN", "tenant")
            try:
                all_c = await engine.list_connections(project_name=p.ap_project_name(grain))
                conns = [c for c in all_c if f"::{p.user_id}::" in (c.get("externalId") or "")]
            except Exception:  # noqa: BLE001
                conns = []
        return {
            "scope": p.scope,
            "user_id": p.user_id,
            "email": email,
            "roles": roles,
            "linked_channels": links,
            "connections": conns,
        }

    @app.post("/api/events/link/{channel}")
    async def link_channel(channel: str, request: Request):
        """Issue a link token FROM the authenticated profile; the user sends it to the bot to
        bind their channel-native id → this account (decision 0007)."""
        if identity is None:
            return JSONResponse({"ok": False, "error": "identity map not configured"}, 501)
        p = _principal_from(
            (await _safe_json(request)).get("scope") or request.query_params.get("scope"), request.headers
        )
        token = identity.issue_token(p.tenant_id, p.user_id, channel)
        bot = os.environ.get(f"EVENTS_{channel.upper()}_BOT_USERNAME", "")
        if channel == "telegram" and bot:
            how = f"open https://t.me/{bot}?start={token}"
        elif channel == "telegram":
            how = f"send '/start {token}' to your Telegram bot"
        else:
            how = f"send '/link {token}' to the {channel} bot"
        return {"ok": True, "channel": channel, "token": token, "how": how}

    @app.get("/api/events/admin/users")
    async def admin_list_users(request: Request):
        """Identities known to this tenant. Admin-only; returns an empty list when no user store is
        configured."""
        if users is None:
            return {"users": []}
        p = _principal_from(request.query_params.get("scope"), request.headers)
        if not _is_admin(p):
            return JSONResponse({"ok": False, "error": "admin only"}, 403)
        return {
            "users": [
                {"user_id": u.user_id, "email": u.email, "roles": u.roles} for u in users.list(p.tenant_id)
            ]
        }

    @app.post("/api/events/admin/users")
    async def admin_add_user(request: Request):
        """Register an identity in this tenant so channel messages can resolve to a real user rather
        than the fallback. Admin-only; 501 when no user store is configured."""
        if users is None:
            return JSONResponse({"ok": False, "error": "user store not configured"}, 501)
        body = await _safe_json(request)
        p = _principal_from(body.get("scope") or request.query_params.get("scope"), request.headers)
        if not _is_admin(p):
            return JSONResponse({"ok": False, "error": "admin only"}, 403)
        uid = body.get("user_id")
        if not uid:
            return JSONResponse({"ok": False, "error": "missing user_id"}, 400)
        u = users.add(
            uid,
            email=body.get("email", ""),
            roles=body.get("roles") or ["user"],
            password=body.get("password"),
            tenant=p.tenant_id,
        )
        return {"ok": True, "user": {"user_id": u.user_id, "email": u.email, "roles": u.roles}}

    @app.post("/api/events/admin/channels/{channel}/arm")
    async def admin_arm_channel(channel: str, request: Request):
        """Admin: arm a channel INBOUND flow. Slack uses the DIRECT backend by default (no AP — the
        Slack Events API posts straight to /api/events/slack/events); set EVENTS_SLACK_BACKEND=ap to
        use the (kept-for-revisit) AP path. Other channels go through AP."""
        body = await _safe_json(request)
        p = _principal_from(body.get("scope") or request.query_params.get("scope"), request.headers)
        if not _is_admin(p):
            return JSONResponse({"ok": False, "error": "admin only"}, 403)
        # Discord DIRECT backend (default): nothing to arm in AP — the Gateway bot connects on boot.
        if channel == "discord" and os.environ.get("EVENTS_DISCORD_BACKEND", "direct") != "ap":
            from . import discord_direct

            if not discord_direct.bot_token():
                return JSONResponse({"ok": False, "error": "set DISCORD_BOT_TOKEN"}, 400)
            return {
                "ok": True,
                "channel": "discord",
                "backend": "direct",
                "note": "Direct Gateway backend — the bot connects on server start; nothing to "
                "arm. Ensure MESSAGE CONTENT INTENT is on (Developer Portal → Bot → "
                "Privileged Gateway Intents). Set EVENTS_DISCORD_BACKEND=ap for the "
                "(polling) AP path.",
            }
        # Slack DIRECT backend (default): nothing to arm in AP — the CUGA endpoint is always live.
        if channel == "slack" and os.environ.get("EVENTS_SLACK_BACKEND", "direct") != "ap":
            from . import slack_direct

            pub = os.environ.get("EVENTS_PUBLIC_URL", "").rstrip("/")
            if not slack_direct.bot_token():
                return JSONResponse({"ok": False, "error": "set SLACK_BOT_TOKEN"}, 400)
            return {
                "ok": True,
                "channel": "slack",
                "backend": "direct",
                "events_url": f"{pub}/api/events/slack/events"
                if pub
                else "<EVENTS_PUBLIC_URL>/api/events/slack/events",
                "signature_verification": "on"
                if slack_direct.signing_secret()
                else "OFF (set SLACK_SIGNING_SECRET)",
                "note": "Set this events_url as your Slack app's Event Subscriptions Request URL "
                "and subscribe the bot event 'message.channels'. No AP flow needed.",
            }
        # Telegram DIRECT backend (default): nothing to arm in AP — the long-poll loop connects
        # (outbound) on server start. No AP, no public URL. Set EVENTS_TELEGRAM_BACKEND=ap for the
        # (webhook) AP path.
        if channel == "telegram" and os.environ.get("EVENTS_TELEGRAM_BACKEND", "direct") != "ap":
            from . import telegram_direct

            if not telegram_direct.bot_token():
                return JSONResponse({"ok": False, "error": "set TELEGRAM_BOT_TOKEN"}, 400)
            return {
                "ok": True,
                "channel": "telegram",
                "backend": "direct",
                "note": "Direct long-poll backend — the bot polls getUpdates on server start; "
                "nothing to arm, no AP, no public URL. DM the bot to test. Set "
                "EVENTS_TELEGRAM_BACKEND=ap for the (webhook) AP path.",
            }
        if engine is None:
            return JSONResponse({"ok": False, "error": "AP not configured"}, 501)
        grain = os.environ.get("EVENTS_AP_PROJECT_GRAIN", "tenant")
        from . import credentials

        conn = credentials.connection_external_id(channel, "per-user", p)  # the bot connection
        # channel-specific trigger inputs (e.g. Discord polls ONE channel → pass {"channel": "<id>"})
        trigger_input = {k: v for k, v in body.items() if k not in ("scope",)}
        try:
            flow_id = await engine.create_inbound_flow(
                channel=channel,
                agent="concierge",
                connection=conn,
                project_name=p.ap_project_name(grain),
                scope=p.scope,
                trigger_input=trigger_input,
            )
        except Exception as e:  # noqa: BLE001
            import traceback

            Trace(new_trace_id()).error("arm", channel=channel, err=repr(e), tb=traceback.format_exc())
            return JSONResponse({"ok": False, "error": repr(e) or type(e).__name__}, 500)
        return {"ok": True, "channel": channel, "ap_flow_id": flow_id}

    @app.get("/api/events/admin/oauth-apps")
    async def admin_oauth_apps(request: Request):
        """Which OAuth providers have client id/secret configured (via UI or .env). No secrets returned."""
        p = _principal_from(request.query_params.get("scope"), request.headers)
        if not _is_admin(p):
            return JSONResponse({"ok": False, "error": "admin only"}, 403)
        if oauth_store is None:
            return {"apps": []}
        return {"apps": oauth_store.status(p.tenant_id)}

    @app.post("/api/events/admin/oauth-apps")
    async def admin_set_oauth_app(request: Request):
        """Admin enters a provider's OAuth app client id/secret once (UI instead of .env)."""
        if oauth_store is None:
            return JSONResponse({"ok": False, "error": "oauth store not configured"}, 501)
        body = await _safe_json(request)
        p = _principal_from(body.get("scope") or request.query_params.get("scope"), request.headers)
        if not _is_admin(p):
            return JSONResponse({"ok": False, "error": "admin only"}, 403)
        app_name = (body.get("app") or "").lower()
        cid, sec = body.get("client_id", ""), body.get("client_secret", "")
        if not app_name or not cid or not sec:
            return JSONResponse({"ok": False, "error": "need app, client_id, client_secret"}, 400)
        oauth_store.set(p.tenant_id, app_name, cid, sec, body.get("scopes", ""))
        return {"ok": True, "app": app_name, "configured": True}

    # credentials read at USE-TIME → a live os.environ update applies immediately; the rest are read
    # at startup and need `make reload` (Discord additionally needs its gateway to reconnect).
    _CRED_LIVE = {"SLACK_BOT_TOKEN", "SLACK_SIGNING_SECRET", "BOX_DEV_TOKEN", "EVENTS_BOX_TOKEN"}

    @app.post("/api/events/admin/credential")
    async def admin_set_credential(request: Request):
        """Set/modify ONE connector credential (its .env variable) from the Studio — the universal
        'edit this credential' seam for channels + integrations. Persists to .env AND updates the live
        process. Whitelisted to the known connector cred keys (from setup_guides) so the UI can never
        inject arbitrary environment. Admin only. Reports whether it applied live or needs a reload."""
        from . import setup_guides

        body = await _safe_json(request)
        p = _principal_from(body.get("scope") or request.query_params.get("scope"), request.headers)
        if not _is_admin(p):
            return JSONResponse({"ok": False, "error": "admin only"}, 403)
        key = (body.get("key") or "").strip()
        value = body.get("value", "")
        known = {c["key"] for g in setup_guides.as_list() for c in g.get("creds", [])}
        if key not in known:
            return JSONResponse(
                {"ok": False, "error": f"'{key}' is not an editable connector credential"}, 400
            )
        if value == "" or value is None:
            return JSONResponse({"ok": False, "error": "missing 'value'"}, 400)
        try:
            _env_upsert(key, str(value))
        except Exception as e:  # noqa: BLE001
            return JSONResponse({"ok": False, "error": f"could not write .env: {e}"}, 500)
        live = key in _CRED_LIVE or key.startswith("EVENTS_OAUTH_")
        note = (
            "Saved to .env and applied live — no restart needed."
            if live
            else (
                "Saved to .env — run `make reload` to apply (read at startup"
                + ("; Discord's gateway reconnects on restart)." if key == "DISCORD_BOT_TOKEN" else ").")
            )
        )
        Trace(new_trace_id())("credential.set", key=key, live=live)
        return {"ok": True, "key": key, "live": live, "note": note}

    async def _safe_json(request):
        try:
            return await request.json()
        except Exception:  # noqa: BLE001
            return {}

    def _is_admin(p) -> bool:
        if users is None:
            return True  # no user store → open (dev)
        u = users.get(p.user_id, p.tenant_id)
        return bool(u and u.has_role("admin"))

    def _is_builder(p) -> bool:
        """Builders (and admins) may create/edit agents — that's design-time work (ADR-0005)."""
        if users is None:
            return True  # no user store → open (dev)
        u = users.get(p.user_id, p.tenant_id)
        return bool(u and (u.has_role("builder") or u.has_role("admin")))
