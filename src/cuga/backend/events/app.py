"""FastAPI wiring for the events layer — mounted on CUGA's app, behind ``EVENTS_ENABLED``.

Two new endpoints (DESIGN §5):
  - ``POST /invoke``         — the seam AP calls back through (X-Gateway-Token).
  - ``POST /api/concierge``  — NL → decide; ``?dry_run=1`` builds the flow without publishing.

``register_events_routes(app, runtime=..., store=...)`` is called from CUGA's ``main.py``
lifespan ONLY when the flag is on, so vanilla CUGA is untouched when off. fastapi is a CUGA
dependency; the dependency-light core doesn't import this module.
"""

from __future__ import annotations

import logging
import os

from fastapi import Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse

from . import concierge_plan
from .envelope import Envelope
from .principal import resolve as resolve_principal
from .trace import Trace, new_trace_id

_elog = logging.getLogger("cuga.events")


def register_events_routes(app, *, runtime, store=None, concierge=None, engine=None,
                           users=None, identity=None, oauth_store=None,
                           gateway_token: str | None = None) -> None:
    """Attach /invoke, /api/concierge and the read-only Studio endpoints to a FastAPI ``app``.

    ``engine`` is the (optional) AP engine — passed so the Integrations endpoint can report
    **real** connection status. The read endpoints exist so the Studio UI stays dumb: it renders
    exactly what the backend can do, no client-side business logic.
    """
    token = gateway_token if gateway_token is not None else os.environ.get("GATEWAY_TOKEN", "")
    if not token:
        # /invoke runs agents on a caller-supplied scope; with no token the seam is open. Fine for
        # local dev, dangerous in a shared deploy — warn loudly rather than fail silently.
        _elog.warning("events: GATEWAY_TOKEN is empty — /invoke and the poll/webhook seams are "
                      "UNAUTHENTICATED. Set GATEWAY_TOKEN before exposing this server.")

    # let OAuth app creds come from the admin store (UI) before .env
    if oauth_store is not None:
        from . import oauth as _oauth
        _oauth.set_cred_resolver(
            lambda app, key: oauth_store.get(
                (resolve_principal(headers={}).tenant_id), app, key))

    @app.post("/invoke")
    async def invoke(request: Request):
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
            nid = channel_user_id(env.source)          # the AUTHOR (per-user), not the channel
            cp = resolve_channel(env.source.name, nid, identity) if nid else None
            if cp is not None:
                scope = cp.scope
            else:
                tr("channel.unlinked", channel=env.source.name, native=nid)
        if not scope:
            scope = resolve_principal(headers=request.headers).scope
        tr("inbound", source=f"{env.source.type}/{env.source.name}", thread=env.thread_id,
           kind=env.event.kind, scope=scope, text=env.text[:80])
        # account-linking handshake: a channel message "/start <token>" / "/link <token>" binds
        # the sender's native id → the profile that issued the token (decision 0007).
        if env.source.type == "channel" and identity is not None:
            from .principal import channel_user_id
            txt = (env.text or "").strip()
            for pfx in ("/start ", "/link "):
                if txt.startswith(pfx):
                    nid = channel_user_id(env.source)          # bind the AUTHOR's native id
                    uid = identity.redeem_token(txt[len(pfx):].strip(), nid) if nid else None
                    tr("channel.link", channel=env.source.name, native=nid, ok=bool(uid))
                    return {"ok": True, "linked": bool(uid), "trace_id": tr.id,
                            "answer": ("Your account is linked — you can chat now."
                                       if uid else "That link code is invalid or expired.")}
        # agents are tenant-shared (agent_scope = first 2 scope segments); run-state is per-user
        agent_scope = "/".join(scope.split("/")[:2]) or scope
        from . import runmeta
        runmeta.start()
        ms = None
        agent = env.agent
        # 'concierge' is the runtime ROUTER (picks among pre-built agents / arms flows), not a
        # worker agent — inbound CHANNEL messages arm agent='concierge', so route those through
        # the router. A concrete agent id runs directly on the worker runtime.
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
                return JSONResponse({"ok": False, "error": str(e)}, 500)
        else:
            if not agent or runtime.get_agent(agent, scope=agent_scope) is None:
                tr.error("error", reason="unknown agent", agent=agent, scope=agent_scope)
                return JSONResponse({"ok": False, "error": f"unknown agent '{agent}'"}, 404)
            try:
                import time
                t0 = time.time()
                answer = await runtime.run(agent, env.thread_id, env.worker_input(), scope=agent_scope,
                                           deliver_to=[env.source.name] if env.deliver else None)
                ms = int((time.time() - t0) * 1000)
                tr("worker.done", agent=agent, ok=True, ms=ms)
            except Exception as e:  # noqa: BLE001
                tr.error("error", agent=agent, err=str(e))
                return JSONResponse({"ok": False, "error": str(e)}, 500)
        # metadata footer — who answered + which tools ran — appended to the reply so it shows on
        # every channel (Telegram/Discord/…). Structured `meta` also rides in the API response.
        # Off with EVENTS_REPLY_METADATA=0.
        meta = runmeta.get() or {}
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
        if env.deliver:
            from . import delivery
            from .principal import channel_origin
            direct_done = False
            origin = channel_origin(env.thread_id)     # (channel, native) from the thread_id
            if origin and delivery.is_direct(origin[0]) and origin[1] and isinstance(answer, str):
                ok, why = await delivery.send_direct(origin[0], origin[1], answer)
                tr("deliver", via="direct", channel=origin[0], ok=ok, reason=why)
                direct_done = ok
            cap = os.environ.get("EA_CAPTURE_URL")
            if not direct_done and cap:
                try:
                    import httpx
                    async with httpx.AsyncClient(timeout=10) as hc:
                        await hc.post(cap, json={"agent": agent, "answer": answer,
                                                 "thread_id": env.thread_id, "trace_id": tr.id})
                    tr("deliver", via="capture", ok=True)
                except Exception as e:  # noqa: BLE001
                    tr.error("deliver", via="capture", err=str(e))
        return {"ok": True, "agent": agent, "answer": answer, "trace_id": tr.id,
                "meta": {"agent": meta.get("agent") or (agent if agent != "concierge" else None),
                         "backend": meta.get("backend"), "mcp": meta.get("mcp") or [],
                         "tools": meta.get("tools") or [], "ms": ms}}

    @app.post("/api/concierge")
    async def api_concierge(request: Request):   # NOT 'concierge' — that name is the instance arg
        body = await request.json()
        text = (body or {}).get("text", "")
        dry = request.query_params.get("dry_run") in ("1", "true", "yes")
        tr = Trace(new_trace_id())
        tr("inbound", source="api/concierge", dry_run=dry, text=text[:80])
        if dry:
            # reason → build, NO side effects (deterministic planner)
            planned = concierge_plan.plan(text, agent=(body or {}).get("agent", "worker"),
                                          thread_id=(body or {}).get("thread_id", "web:local"))
            tr("flow.build", mode=planned["decision"]["mode"], dry_run=True)
            return {"ok": True, "dry_run": True, **planned, "trace_id": tr.id}
        # live path: run the LLM concierge (host-bound meta-tools) — reuse/create + run/arm.
        if concierge is None:
            planned = concierge_plan.plan(text, agent=(body or {}).get("agent", "worker"))
            return JSONResponse(
                {"ok": False, "reason": "concierge not configured; use ?dry_run=1 for the plan",
                 "plan": planned, "trace_id": tr.id}, 501)
        thread_id = (body or {}).get("thread_id", "web:local")
        principal = resolve_principal(headers=request.headers)
        # `?flow=1` → also return the flow(s) this utterance armed, so a caller can check the pieces
        # are right without a second round trip to /subscriptions/<id>/flow. `?flow=full` adds the
        # raw Activepieces flow JSON. Off by default: it costs one AP call per new subscription.
        want_flow = request.query_params.get("flow", "") in ("1", "true", "yes", "digest", "full")
        full_flow = request.query_params.get("flow", "") == "full"
        before = {s.id for s in store.list(scope=principal.scope)} if (store and want_flow) else set()
        # /watch|/schedule|/cron|/poll|/push slash commands are handled inside concierge.run (so they
        # work from every surface — web chat AND channels), no interception needed here.
        try:
            reply = await concierge.run(thread_id, text, principal)
            tr("concierge", ok=True, scope=principal.scope)
        except Exception as e:  # noqa: BLE001
            tr.error("error", err=str(e))
            return JSONResponse({"ok": False, "error": str(e), "trace_id": tr.id}, 500)
        out = {"ok": True, "reply": reply, "scope": principal.scope, "trace_id": tr.id}
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
            steps.append({"name": node.get("name"), "display": node.get("displayName"),
                          "type": node.get("type"),
                          "piece": s.get("pieceName"), "action": s.get("actionName"),
                          # the send step's text is a template that reads step_1's HTTP RESPONSE —
                          # this is the seam that proves the answer flows into the sink
                          "text": ((s.get("input") or {}).get("text")
                                   or (s.get("input") or {}).get("message"))})
            node = node.get("nextAction")
        return {
            "id": ap_flow.get("id"), "status": ap_flow.get("status"),
            "trigger": {"piece": ts.get("pieceName"), "name": ts.get("triggerName"),
                        "input": ts.get("input")},
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
            row: dict = {"subscription_id": sub.id, "mode": sub.mode, "agent": sub.target_agent,
                         "deliver_to": list(sub.deliver_to or []), "flow_name": sub.flow_name,
                         "ap_flow_id": sub.ap_flow_id, "dedup_key": sub.dedup_key}
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
    async def list_subscriptions(request: Request):
        # only THIS principal's subscriptions (isolation)
        scope = resolve_principal(headers=request.headers).scope
        return {"scope": scope, "subscriptions": store.as_dicts(scope=scope) if store else []}

    # --- flow lifecycle: pause / resume / delete from the CUGA UI (AP is driven internally) -------
    def _owned_sub(sub_id: str, request: Request):
        """Fetch a subscription IFF it belongs to the caller (isolation), else (None, scope)."""
        scope = resolve_principal(headers=request.headers).scope
        sub = store.get(sub_id) if store else None
        return (sub if (sub and sub.tenant == scope) else None), scope

    @app.post("/api/events/subscriptions/{sub_id}/pause")
    async def pause_subscription(sub_id: str, request: Request):
        sub, _ = _owned_sub(sub_id, request)
        if sub is None:
            return JSONResponse({"ok": False, "error": "subscription not found"}, 404)
        if engine is not None and sub.ap_flow_id:
            await engine.set_flow_status(sub.ap_flow_id, enabled=False)   # disable in AP
        store.set_status(sub_id, "paused")
        Trace(new_trace_id())("sub.pause", id=sub_id, flow=sub.ap_flow_id)
        return {"ok": True, "id": sub_id, "status": "paused"}

    @app.post("/api/events/subscriptions/{sub_id}/resume")
    async def resume_subscription(sub_id: str, request: Request):
        sub, _ = _owned_sub(sub_id, request)
        if sub is None:
            return JSONResponse({"ok": False, "error": "subscription not found"}, 404)
        if engine is not None and sub.ap_flow_id:
            await engine.set_flow_status(sub.ap_flow_id, enabled=True)    # re-enable in AP
        store.set_status(sub_id, "active")
        Trace(new_trace_id())("sub.resume", id=sub_id, flow=sub.ap_flow_id)
        return {"ok": True, "id": sub_id, "status": "active"}

    @app.delete("/api/events/subscriptions/{sub_id}")
    async def delete_subscription(sub_id: str, request: Request):
        sub, _ = _owned_sub(sub_id, request)
        if sub is None:
            return JSONResponse({"ok": False, "error": "subscription not found"}, 404)
        if engine is not None and sub.ap_flow_id:
            await engine.delete_flow(sub.ap_flow_id)                      # delete in AP
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

    @app.get("/api/events/flows/console")
    async def flows_console():
        """Self-contained Flows console: list · pause/resume/delete · rich read-only flow view.
        A plain HTML page (no build step) so it can't break the pre-built Studio bundle."""
        from .flows_console import FLOWS_CONSOLE_HTML
        return HTMLResponse(FLOWS_CONSOLE_HTML)

    # --- execution log: which flows RAN, succeeded/failed, and their output -----------------------
    @app.get("/api/events/runs")
    async def list_runs(request: Request):
        """The execution log. Recent Activepieces flow-runs, each JOINED to its CUGA subscription so
        the row carries agent / mode(trigger) / integration / channel — the Studio filters+sorts on
        those. Isolation: only runs for THIS caller's own flows."""
        scope = resolve_principal(headers=request.headers).scope
        subs = store.as_dicts(scope=scope) if store else []
        by_flow = {s.get("ap_flow_id"): s for s in subs if s.get("ap_flow_id")}
        runs = await engine.list_runs(limit=80) if engine is not None else []
        out = []
        for r in runs:
            sub = by_flow.get(r.get("flowId"))
            if sub is None:
                continue   # isolation: skip runs whose flow isn't the caller's
            out.append({
                "id": r.get("id"), "status": r.get("status"),
                "started_at": r.get("startTime"), "finished_at": r.get("finishTime"),
                "agent": sub.get("target_agent"), "mode": sub.get("mode"),
                "integration": sub.get("source_connector"),
                "channel": ", ".join(sub.get("deliver_to") or []),
                "utterance": sub.get("prompt"),
                "flow_name": sub.get("flow_name"), "subscription_id": sub.get("id"),
            })
        return {"scope": scope, "runs": out}

    @app.get("/api/events/runs/{run_id}")
    async def run_detail(run_id: str, request: Request):
        """One run's detail + the agent's OUTPUT: the CUGA /invoke answer, the trigger payload, and
        any error. Scoped to the caller's own flows."""
        scope = resolve_principal(headers=request.headers).scope
        owned = {s.get("ap_flow_id") for s in (store.as_dicts(scope=scope) if store else [])}
        run = await engine.get_run(run_id) if engine is not None else None
        if run is None or run.get("flowId") not in owned:
            return JSONResponse({"ok": False, "error": "run not found"}, 404)
        steps = run.get("steps") or {}
        answer = trigger_payload = error = None
        for name, s in (steps.items() if isinstance(steps, dict) else []):
            if not isinstance(s, dict):
                continue
            outp = s.get("output")
            if name == "trigger":
                trigger_payload = outp
            elif isinstance(outp, dict) and isinstance(outp.get("body"), dict) and "answer" in outp["body"]:
                answer = outp["body"]["answer"]
            if s.get("status") not in ("SUCCEEDED", None) and s.get("errorMessage"):
                error = s.get("errorMessage")
        return {"ok": True,
                "run": {"id": run.get("id"), "status": run.get("status"),
                        "started_at": run.get("startTime"), "finished_at": run.get("finishTime")},
                "answer": answer, "trigger_payload": trigger_payload, "error": error}

    # --- Studio read endpoints (dumb UI reads these; all real state) -------------
    @app.get("/api/events/status")
    async def events_status(request: Request):
        """What the events layer can do right now — the UI uses this to decide what to show."""
        scope = resolve_principal(headers=request.headers).scope
        grain = os.environ.get("EVENTS_AP_PROJECT_GRAIN", "tenant")
        worker_backend = os.environ.get("EVENTS_WORKER_BACKEND", "cuga")
        return {"ok": True, "enabled": True, "scope": scope,
                # concierge (NL→flow) = react; workers (answer the question) = cuga by default
                "concierge_backend": "react", "worker_backend": worker_backend,
                "backends": ["react", "cuga"], "ap_configured": engine is not None,
                "project_grain": grain,
                # all wired via AP when the engine is configured (inbound channel flows, PUSH
                # watchers, and scheduled/poll flows that deliver via an appended channel send
                # step). Telegram/Discord (AP) + Slack (direct) are round-trip-verified live.
                "features": {"now": True, "cron": engine is not None,
                             "poll": engine is not None, "push": engine is not None,
                             "channels_inbound": engine is not None}}

    @app.get("/api/events/channels")
    async def events_channels(request: Request):
        from .connectors import channels_status
        return {"channels": channels_status()}

    @app.get("/api/events/integrations")
    async def events_integrations(request: Request):
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
        rows = integrations_status(conns, ap_configured=engine is not None,
                                   ap_connect_url=connect_url)
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
                    r["note"] = ("DIRECT backend — CUGA polls Box with BOX_DEV_TOKEN (no AP, no OAuth). "
                                 "Fires via POST /api/events/box/poll; test with live_box_direct_check.py.")
        # .env-TOKEN integrations (github) AUTO-CONNECT on startup — so a token in .env == connected.
        # If the token is set but no AP connection exists yet, it's not a UI bug and not "not
        # connected": auto-connect hasn't succeeded, almost always because AP's piece isn't installed
        # yet on a fresh DB (see `make ap-pieces`). Say that explicitly instead of a bare red status.
        _env_token = {"github": "GITHUB_TOKEN"}
        for r in rows:
            var = _env_token.get(r["name"])
            if var and r["status"] == "not_connected" and os.environ.get(var, "").strip():
                r["status"] = "auto_connect_pending"
                r["note"] = (f"{var} is set — this auto-connects on startup. If it's still pending, "
                             f"AP's {r['name']} piece isn't installed yet (run `make ap-pieces`), then "
                             f"restart. No manual Connect needed once pieces are ready.")
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
            agents.append({"name": s.name, "prompt": getattr(s, "prompt", ""),
                           "backend": getattr(s, "backend", ""),
                           "mcp_servers": list(getattr(s, "mcp_servers", []) or []),
                           "channels": list(getattr(s, "channels", []) or []),
                           "integrations": list(getattr(s, "integrations", []) or []),
                           "access": list(getattr(s, "access", []) or []),
                           "restricted": bool(getattr(s, "access", []) or []),
                           "examples": [u for u in by_agent.get(s.name, []) if u][:3],
                           "can_use": usable})
        agents.sort(key=lambda a: a["name"])
        return {"scope": agent_scope, "agents": agents}

    @app.get("/api/events/mcp-servers")
    async def events_mcp_servers():
        """The tool servers a builder can attach to an agent (name + one-line hint). Drives the
        Agent-editor form so the UI never hardcodes the catalog."""
        from . import mcp_catalog
        return {"servers": [{"name": n, "hint": mcp_catalog.HINTS.get(n, "")}
                            for n in mcp_catalog.known_names()]}

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
        integrations = []
        for it in (body.get("integrations") or []):
            app_name = (it.get("app") or "").strip() if isinstance(it, dict) else ""
            own = (it.get("ownership") or "per-user") if isinstance(it, dict) else "per-user"
            if not app_name:
                return None, "each integration needs an 'app'"
            if own not in ("shared", "per-user"):
                return None, "integration ownership must be 'shared' or 'per-user'"
            integrations.append({"app": app_name, "ownership": own})
        access = [str(a) for a in (body.get("access") or [])]
        return AgentSpec(name=name, prompt=body.get("prompt", "") or "", backend=backend,
                         mcp_servers=mcp, channels=channels, integrations=integrations,
                         access=access), None

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
        except Exception as e:  # noqa: BLE001
            return JSONResponse({"ok": False, "error": str(e)}, 500)
        Trace(new_trace_id())("agent.update", name=spec.name, scope=agent_scope)
        return {"ok": True, "name": spec.name, "scope": agent_scope}

    @app.get("/api/events/examples")
    async def events_examples():
        from .catalog import as_list
        return {"examples": as_list()}

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
        if os.environ.get("EVENTS_BOX_BACKEND", "").lower() == "direct":   # box direct = a USER token
            from . import box_direct
            st["box"] = "connected" if box_direct.token() else "not_connected"
        out = []
        def _cred_scope(key: str) -> str:
            # TENANT: OAuth *app* creds + channel bot tokens (one per org). USER: personal tokens/PATs.
            k = key.upper()
            if k.startswith("EVENTS_OAUTH_") or k.endswith("_BOT_TOKEN") or k.endswith("_SIGNING_SECRET") \
                    or k.endswith("_BOT_USERNAME"):
                return "tenant"
            return "user"     # GITHUB_TOKEN, BOX_DEV_TOKEN, …

        for g in setup_guides.as_list():
            creds = [{**c, "present": bool(os.environ.get(c["key"])), "scope": _cred_scope(c["key"])}
                     for c in g.get("creds", [])]
            steps = [s.replace("<EVENTS_PUBLIC_URL>", pub) for s in g.get("steps", [])]
            # how you connect it (drives the Studio's button): oauth consent · paste token · direct · none
            # a guide may declare `connect` explicitly (e.g. Box's default direct-token path); else derive.
            app = g["app"]
            if g.get("connect"):
                connect = g["connect"]
            elif app == "slack":
                connect = "direct"
            elif any(c["key"].startswith("EVENTS_OAUTH_") for c in g.get("creds", [])):
                connect = "oauth"
            elif g.get("creds"):
                connect = "token"
            else:
                connect = "none"
            status = st.get(app, "n/a")
            # the CONNECTION is per-USER for integrations (each user logs in) · TENANT for channels (one bot)
            conn_scope = "user" if g.get("kind") == "integration" else "tenant"
            out.append({**g, "creds": creds, "steps": steps, "connect": connect,
                        "conn_status": status, "connected": status == "connected",
                        "connection_scope": conn_scope,
                        "needs_connection": connect != "none"})
        return {"public_url": pub, "guides": out}

    # --- DIRECT Slack (default backend; no AP) — Slack Events API → /invoke → chat.postMessage -----
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
        # 3) a real human message → answer it (in the background; ack now)
        ev = body.get("event") or {}
        if slack_direct.should_process(ev):
            # thread identity: a threaded reply carries thread_ts; a root message uses its own ts so
            # the bot's reply STARTS a thread. Either way the whole conversation lives in that thread.
            thread_ts = ev.get("thread_ts") or ev.get("ts")
            asyncio.create_task(_slack_answer(ev.get("text", ""), ev.get("channel", ""),
                                              ev.get("user", ""), thread_ts))
        return {"ok": True}

    async def _slack_answer(text: str, channel: str, user: str, thread_ts: str | None = None) -> None:
        """Route a Slack message through /invoke (concierge + metadata footer) and post the reply
        BACK INTO THE THREAD. The thread_id keys the conversation memory per-thread — one Slack
        thread = one topic. The native id (for identity/delivery) stays the channel: the ``#<ts>``
        suffix is stripped by ``channel_native_id``, so only memory is thread-scoped."""
        from . import slack_direct
        import httpx
        tr = Trace(new_trace_id())
        try:
            port = os.environ.get("EVENTS_CUGA_PORT", "8100")
            gw = (os.environ.get("GATEWAY_TOKEN", "") or "").split(" #", 1)[0].strip()
            # gw:slack:<channel>#<thread_ts> → per-thread memory; native id = channel (suffix stripped).
            # source.user = the Slack author id → per-user identity (whose creds/perms) once linked.
            tid = f"gw:slack:{channel}#{thread_ts}" if thread_ts else f"gw:slack:{channel}"
            payload = {"text": text, "agent": "concierge", "deliver": False,
                       "source": {"type": "channel", "name": "slack", "thread_id": tid, "user": user},
                       "event": {"kind": "message", "payload": {"slack_user": user}}}
            async with httpx.AsyncClient(timeout=180) as c:
                r = await c.post(f"http://127.0.0.1:{port}/invoke",
                                 headers={"X-Gateway-Token": gw}, json=payload)
                answer = (r.json() or {}).get("answer") if r.status_code == 200 else None
            if answer:
                res = await slack_direct.send_message(channel, answer, thread_ts=thread_ts)
                tr("slack.reply", channel=channel, thread=thread_ts, ok=res.get("ok"))
            else:
                tr.error("slack", reason="no answer", status=r.status_code)
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
        folder = str(body.get("folder_id") or "0")
        # `since` in the body wins (manual/test poll); otherwise use the SERVER-tracked last-seen for
        # this folder, so a standing scheduled poll fires only on files added since the previous run.
        since = body.get("since")
        server_tracked = since is None
        if server_tracked:
            since = box_direct.load_since(folder)
        agent = body.get("agent") or "resume_judge"
        deliver_to = body.get("deliver_to")            # e.g. a direct channel: "slack"
        deliver_target = body.get("deliver_target")    # the channel-native id (e.g. Slack channel id)
        tr = Trace(new_trace_id())
        try:
            files = await box_direct.new_files_since(folder, since)
        except Exception as e:  # noqa: BLE001
            tr.error("box.poll", folder=folder, err=str(e))
            return JSONResponse({"ok": False, "error": str(e)}, 502)
        tr("box.poll", folder=folder, new=len(files), since=since)
        processed, newest = [], since or ""
        for f in files:
            newest = max(newest, f.get("created_at") or "")
            await _box_dispatch(agent, f, deliver_to, body.get("scope"), deliver_target)
            processed.append({"id": f["id"], "name": f.get("name")})
        if server_tracked and newest and newest != (since or ""):
            box_direct.save_since(folder, newest)      # advance the watermark for the next poll
        return {"ok": True, "folder": folder, "processed": processed, "newest": newest,
                "trace_id": tr.id}

    async def _box_dispatch(agent: str, file: dict, deliver_to, scope, deliver_target=None) -> None:
        """Fire one Box file through /invoke(agent). If deliver_to is a DIRECT channel the reply is
        sent CUGA-side (no AP); otherwise the answer just rides back in the /invoke response.
        ``deliver_target`` is the channel-native id (e.g. the Slack channel) so the answer lands in
        the right place — without it a direct sink has no destination."""
        import httpx
        from . import delivery
        tr = Trace(new_trace_id())
        port = os.environ.get("EVENTS_CUGA_PORT", "8100")
        gw = (os.environ.get("GATEWAY_TOKEN", "") or "").split(" #", 1)[0].strip()
        direct = bool(deliver_to and delivery.is_direct(deliver_to))
        src = ({"type": "channel", "name": deliver_to, "thread_id": f"gw:{deliver_to}:{deliver_target or ''}"}
               if direct else {"type": "integration", "name": "box", "thread_id": f"box:{file['id']}"})
        payload = {"agent": agent, "deliver": bool(direct), "scope": scope or "",
                   "text": (f"A file '{file.get('name')}' landed in Box. Judge fit vs the JD. "
                            "Start your reply with MATCH or SKIP."),
                   "source": src,
                   "event": {"kind": "new_file", "payload": {"file_id": file["id"],
                                                             "name": file.get("name")}}}
        try:
            async with httpx.AsyncClient(timeout=180) as c:
                r = await c.post(f"http://127.0.0.1:{port}/invoke",
                                 headers={"X-Gateway-Token": gw}, json=payload)
            tr("box.dispatch", file=file.get("name"), ok=r.status_code == 200, deliver=deliver_to)
        except Exception as e:  # noqa: BLE001
            tr.error("box.dispatch", file=file.get("name"), err=str(e))

    # --- GENERIC inbound WEBHOOK (direct, no AP) — any system POSTs a payload → agent → deliver -----
    @app.post("/api/events/hook/{name}")
    async def inbound_webhook(name: str, request: Request):
        """A generic inbound webhook: any external system (monitoring, CI, a form, a payment provider)
        POSTs a JSON payload to <EVENTS_PUBLIC_URL>/api/events/hook/<name> and CUGA runs an agent to
        triage it, optionally delivering the result to a channel.

        Query: ?agent=<agent> (default incident_triage) · ?deliver_to=<channel> + ?target=<native id>
        (deliver the triage to e.g. a Slack channel) · ?key=<secret> (required iff EVENTS_WEBHOOK_KEY
        is set). No AP — it's just an HTTP endpoint that reuses the /invoke seam."""
        import hmac as _hmac
        import json as _json
        import httpx
        want = os.environ.get("EVENTS_WEBHOOK_KEY")
        if want and not _hmac.compare_digest(request.query_params.get("key") or "", want):
            return JSONResponse({"ok": False, "error": "bad or missing ?key"}, 401)
        payload = await _safe_json(request)
        agent = request.query_params.get("agent") or "incident_triage"
        deliver_to = request.query_params.get("deliver_to")
        target = request.query_params.get("target")
        tr = Trace(new_trace_id())
        tr("hook", name=name, agent=agent, deliver=deliver_to)
        body_txt = _json.dumps(payload, indent=2)[:4000] if payload else "(empty body)"
        text = (f"An external system POSTed to webhook '{name}'. Triage this payload:\n\n{body_txt}")
        port = os.environ.get("EVENTS_CUGA_PORT", "8100")
        gw = (os.environ.get("GATEWAY_TOKEN", "") or "").split(" #", 1)[0].strip()
        deliver = bool(deliver_to and target)
        src = ({"type": "channel", "name": deliver_to, "thread_id": f"gw:{deliver_to}:{target}"}
               if deliver else {"type": "integration", "name": "webhook", "thread_id": f"hook:{name}"})
        inv = {"agent": agent, "text": text, "deliver": deliver, "source": src,
               "event": {"kind": "message", "payload": payload if isinstance(payload, dict) else {}}}
        try:
            async with httpx.AsyncClient(timeout=180) as c:
                r = await c.post(f"http://127.0.0.1:{port}/invoke",
                                 headers={"X-Gateway-Token": gw}, json=inv)
            j = r.json() if r.status_code == 200 else {}
        except Exception as e:  # noqa: BLE001
            tr.error("hook", err=str(e))
            return JSONResponse({"ok": False, "webhook": name, "error": str(e)}, 502)
        return {"ok": r.status_code == 200, "webhook": name, "agent": agent,
                "answer": j.get("answer"), "delivered": deliver, "trace_id": tr.id}

    # --- DIRECT Discord (default backend; a Gateway WebSocket bot — no AP, no public URL) ----------
    async def _discord_answer(msg: dict) -> None:
        """A Discord Gateway MESSAGE_CREATE → /invoke(concierge) → reply back to the channel.
        thread_id keys memory per channel/thread; source.user = the author (per-user identity)."""
        from . import discord_direct
        import httpx
        tr = Trace(new_trace_id())
        channel_id = str(msg.get("channel_id") or "")
        author = str((msg.get("author") or {}).get("id") or "")
        text = msg.get("content") or ""
        try:
            port = os.environ.get("EVENTS_CUGA_PORT", "8100")
            gw = (os.environ.get("GATEWAY_TOKEN", "") or "").split(" #", 1)[0].strip()
            payload = {"text": text, "agent": "concierge", "deliver": False,
                       "source": {"type": "channel", "name": "discord",
                                  "thread_id": f"gw:discord:{channel_id}", "user": author},
                       "event": {"kind": "message", "payload": {"discord_user": author}}}
            async with httpx.AsyncClient(timeout=180) as c:
                r = await c.post(f"http://127.0.0.1:{port}/invoke",
                                 headers={"X-Gateway-Token": gw}, json=payload)
                answer = (r.json() or {}).get("answer") if r.status_code == 200 else None
            if answer:
                res = await discord_direct.send_message(channel_id, answer)
                tr("discord.reply", channel=channel_id, ok=res.get("ok"))
            else:
                tr.error("discord", reason="no answer", status=r.status_code)
        except Exception as e:  # noqa: BLE001
            tr.error("discord", err=str(e))

    # register the Gateway as a startup background task (direct is the default). The server's
    # lifespan launches app.state.events_background; nothing to arm (the bot connects on boot).
    if os.environ.get("EVENTS_DISCORD_BACKEND", "direct") != "ap":
        from . import discord_direct as _dd
        if _dd.bot_token():
            async def _discord_gateway():
                await _dd.run_gateway(_discord_answer)
            _bg = list(getattr(app.state, "events_background", []) or [])
            _bg.append(_discord_gateway)
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
        p = resolve_principal(headers={})     # the operator principal (EVENTS_USER_ID / defaults)
        grain = os.environ.get("EVENTS_AP_PROJECT_GRAIN", "tenant")
        # token-auth pieces whose secret sits in .env → create the operator's SECRET_TEXT AP
        # connection on startup, so "set in .env" == "connected" (mirrors the Studio's connect).
        # Without this the piece's inbound flow can't publish (AP: ConnectionNotFound) at arm time.
        #   · github   — PAT (integration)
        #   · telegram — bot token; Telegram is ALWAYS the AP backend, so it always needs this
        #   · discord  — bot token, but only when the AP backend is selected (default is the direct
        #                Gateway, which connects the socket on boot and needs no AP connection)
        autoconn = [("github", os.environ.get("GITHUB_TOKEN", "")),
                    ("telegram", os.environ.get("TELEGRAM_BOT_TOKEN", ""))]
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
                        await engine.ensure_secret_connection(ext, oauth.provider(app_name)["piece"],
                                                              tok, project_name=p.ap_project_name(grain))
                    log.info("autoconnect: %s connected from .env (%s)", app_name, ext)
                    break
                except Exception as e:  # noqa: BLE001
                    if attempt == 4:
                        log.warning("autoconnect %s failed after %d attempts: %r",
                                    app_name, attempt, e, exc_info=True)
                    else:
                        await asyncio.sleep(2 * attempt)

    if engine is not None and any(os.environ.get(k) for k in
                                  ("GITHUB_TOKEN", "TELEGRAM_BOT_TOKEN", "DISCORD_BOT_TOKEN")):
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
            return {"ok": True, "app": app, "kind": "token",
                    "message": f"POST your {app} token to /api/events/connect/{app}/token"}
        if not oauth.is_configured(app):
            return JSONResponse({"ok": False, "app": app, "kind": "oauth",
                                 "error": f"OAuth not configured — set EVENTS_OAUTH_{app.upper()}_"
                                          "CLIENT_ID / _CLIENT_SECRET"}, 501)
        state = oauth.encode_state(scope=p.scope, app=app,
                                   agent=request.query_params.get("agent", ""),
                                   ownership=request.query_params.get("ownership", "per-user"),
                                   ret=request.query_params.get("return", ""))
        return RedirectResponse(oauth.authorize_url(app, state), status_code=302)

    @app.get("/api/events/connect/{app}/callback")
    async def connect_callback(app: str, request: Request):
        """OAuth redirect target: exchange the code + create the user's AP connection."""
        from . import oauth, credentials
        code = request.query_params.get("code")
        st = oauth.decode_state(request.query_params.get("state", ""))
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
                    ext, prov["piece"], code,
                    client_id=os.environ.get(f"EVENTS_OAUTH_{app.upper()}_CLIENT_ID", ""),
                    client_secret=os.environ.get(f"EVENTS_OAUTH_{app.upper()}_CLIENT_SECRET", ""),
                    scope=" ".join(prov.get("scopes", [])),
                    redirect_url=oauth.redirect_uri(app),
                    project_name=p.ap_project_name(grain))
        except Exception as e:  # noqa: BLE001
            Trace(new_trace_id()).error("connect", app=app, err=str(e))
            return HTMLResponse(f"<h3>Connect failed</h3><p>{str(e)[:300]}</p>", 500)
        ret = st.get("ret")
        if ret:
            return RedirectResponse(ret, status_code=302)
        return HTMLResponse(f"<h3>✅ {app} connected</h3><p>You can return to your chat.</p>")

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
            return JSONResponse({"ok": False, "app": app, "error": (
                f"'{app}' is an OAuth connector — AP can't build a connection from a pasted token. "
                f"Use the OAuth login: GET /api/events/connect/{app} (consent → callback).")}, 400)
        p = _principal_from((body or {}).get("scope"), request.headers)
        # ownership: 'tenant' (shared across the tenant) | 'per_user' (each user's own). Default per-user.
        own = "shared" if (body or {}).get("ownership") in ("tenant", "shared") else "per-user"
        ext = credentials.connection_external_id(app, own, p)
        if engine is None:
            return JSONResponse({"ok": False, "error": "AP not configured"}, 501)
        grain = os.environ.get("EVENTS_AP_PROJECT_GRAIN", "tenant")
        try:
            await engine.ensure_secret_connection(ext, prov["piece"], token,
                                                  project_name=p.ap_project_name(grain))
        except Exception as e:  # noqa: BLE001
            import traceback
            Trace(new_trace_id()).error("connect_token", app=app, err=repr(e), tb=traceback.format_exc())
            return JSONResponse({"ok": False, "error": repr(e) or type(e).__name__}, 500)
        return {"ok": True, "app": app, "connection": ext}

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
        return {"scope": p.scope, "user_id": p.user_id, "email": email, "roles": roles,
                "linked_channels": links, "connections": conns}

    @app.post("/api/events/link/{channel}")
    async def link_channel(channel: str, request: Request):
        """Issue a link token FROM the authenticated profile; the user sends it to the bot to
        bind their channel-native id → this account (decision 0007)."""
        if identity is None:
            return JSONResponse({"ok": False, "error": "identity map not configured"}, 501)
        p = _principal_from((await _safe_json(request)).get("scope")
                            or request.query_params.get("scope"), request.headers)
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
        if users is None:
            return {"users": []}
        p = _principal_from(request.query_params.get("scope"), request.headers)
        if not _is_admin(p):
            return JSONResponse({"ok": False, "error": "admin only"}, 403)
        return {"users": [{"user_id": u.user_id, "email": u.email, "roles": u.roles}
                          for u in users.list(p.tenant_id)]}

    @app.post("/api/events/admin/users")
    async def admin_add_user(request: Request):
        if users is None:
            return JSONResponse({"ok": False, "error": "user store not configured"}, 501)
        body = await _safe_json(request)
        p = _principal_from(body.get("scope") or request.query_params.get("scope"), request.headers)
        if not _is_admin(p):
            return JSONResponse({"ok": False, "error": "admin only"}, 403)
        uid = body.get("user_id")
        if not uid:
            return JSONResponse({"ok": False, "error": "missing user_id"}, 400)
        u = users.add(uid, email=body.get("email", ""), roles=body.get("roles") or ["user"],
                      password=body.get("password"), tenant=p.tenant_id)
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
            return {"ok": True, "channel": "discord", "backend": "direct",
                    "note": "Direct Gateway backend — the bot connects on server start; nothing to "
                            "arm. Ensure MESSAGE CONTENT INTENT is on (Developer Portal → Bot → "
                            "Privileged Gateway Intents). Set EVENTS_DISCORD_BACKEND=ap for the "
                            "(polling) AP path."}
        # Slack DIRECT backend (default): nothing to arm in AP — the CUGA endpoint is always live.
        if channel == "slack" and os.environ.get("EVENTS_SLACK_BACKEND", "direct") != "ap":
            from . import slack_direct
            pub = os.environ.get("EVENTS_PUBLIC_URL", "").rstrip("/")
            if not slack_direct.bot_token():
                return JSONResponse({"ok": False, "error": "set SLACK_BOT_TOKEN"}, 400)
            return {"ok": True, "channel": "slack", "backend": "direct",
                    "events_url": f"{pub}/api/events/slack/events" if pub else
                                  "<EVENTS_PUBLIC_URL>/api/events/slack/events",
                    "signature_verification": "on" if slack_direct.signing_secret() else
                                              "OFF (set SLACK_SIGNING_SECRET)",
                    "note": "Set this events_url as your Slack app's Event Subscriptions Request URL "
                            "and subscribe the bot event 'message.channels'. No AP flow needed."}
        if engine is None:
            return JSONResponse({"ok": False, "error": "AP not configured"}, 501)
        grain = os.environ.get("EVENTS_AP_PROJECT_GRAIN", "tenant")
        from . import credentials
        conn = credentials.connection_external_id(channel, "per-user", p)  # the bot connection
        # channel-specific trigger inputs (e.g. Discord polls ONE channel → pass {"channel": "<id>"})
        trigger_input = {k: v for k, v in body.items() if k not in ("scope",)}
        try:
            flow_id = await engine.create_inbound_flow(
                channel=channel, agent="concierge", connection=conn,
                project_name=p.ap_project_name(grain), scope=p.scope,
                trigger_input=trigger_input)
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

    async def _safe_json(request):
        try:
            return await request.json()
        except Exception:  # noqa: BLE001
            return {}

    def _is_admin(p) -> bool:
        if users is None:
            return True                 # no user store → open (dev)
        u = users.get(p.user_id, p.tenant_id)
        return bool(u and u.has_role("admin"))

    def _is_builder(p) -> bool:
        """Builders (and admins) may create/edit agents — that's design-time work (ADR-0005)."""
        if users is None:
            return True                 # no user store → open (dev)
        u = users.get(p.user_id, p.tenant_id)
        return bool(u and (u.has_role("builder") or u.has_role("admin")))
