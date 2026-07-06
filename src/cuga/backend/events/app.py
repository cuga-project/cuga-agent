"""FastAPI wiring for the events layer — mounted on CUGA's app, behind ``EVENTS_ENABLED``.

Two new endpoints (DESIGN §5):
  - ``POST /invoke``         — the seam AP calls back through (X-Gateway-Token).
  - ``POST /api/concierge``  — NL → decide; ``?dry_run=1`` builds the flow without publishing.

``register_events_routes(app, runtime=..., store=...)`` is called from CUGA's ``main.py``
lifespan ONLY when the flag is on, so vanilla CUGA is untouched when off. fastapi is a CUGA
dependency; the dependency-light core doesn't import this module.
"""

from __future__ import annotations

import os

from fastapi import Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse

from . import concierge_plan
from .envelope import Envelope
from .principal import resolve as resolve_principal
from .trace import Trace, new_trace_id


def register_events_routes(app, *, runtime, store=None, concierge=None, engine=None,
                           users=None, identity=None, oauth_store=None,
                           gateway_token: str | None = None) -> None:
    """Attach /invoke, /api/concierge and the read-only Studio endpoints to a FastAPI ``app``.

    ``engine`` is the (optional) AP engine — passed so the Integrations endpoint can report
    **real** connection status. The read endpoints exist so the Studio UI stays dumb: it renders
    exactly what the backend can do, no client-side business logic.
    """
    token = gateway_token if gateway_token is not None else os.environ.get("GATEWAY_TOKEN", "")

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
        #   1. DIRECT channel sink (e.g. Slack): the fired flow's source is the channel, so CUGA
        #      sends the answer itself via the channel's direct adapter (no AP connection needed).
        #   2. capture sink: POST to EA_CAPTURE_URL when set (assertable real-HTTP target for e2e/web).
        if env.deliver:
            from . import delivery
            from .principal import channel_native_id
            direct_done = False
            if env.source.type == "channel" and delivery.is_direct(env.source.name) \
                    and isinstance(answer, str):
                target = channel_native_id(env.source) or ""
                if target:
                    ok, why = await delivery.send_direct(env.source.name, target, answer)
                    tr("deliver", via="direct", channel=env.source.name, ok=ok, reason=why)
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
        try:
            reply = await concierge.run(thread_id, text, principal)
            tr("concierge", ok=True, scope=principal.scope)
        except Exception as e:  # noqa: BLE001
            tr.error("error", err=str(e))
            return JSONResponse({"ok": False, "error": str(e), "trace_id": tr.id}, 500)
        return {"ok": True, "reply": reply, "scope": principal.scope, "trace_id": tr.id}

    @app.get("/api/events/subscriptions")
    async def list_subscriptions(request: Request):
        # only THIS principal's subscriptions (isolation)
        scope = resolve_principal(headers=request.headers).scope
        return {"scope": scope, "subscriptions": store.as_dicts(scope=scope) if store else []}

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
        return {"integrations": integrations_status(
            conns, ap_configured=engine is not None, ap_connect_url=connect_url)}

    @app.get("/api/events/agents")
    async def events_agents(request: Request):
        """The pre-built worker fleet (geobot, pricebot, …) the concierge routes among. Dumb read:
        the UI renders the specs; visibility follows per-agent access (perms.can_use)."""
        from . import perms
        p = _principal_from(request.query_params.get("scope"), request.headers)
        roles = ["user"]
        if users is not None:
            u = users.get(p.user_id, p.tenant_id)
            if u:
                roles = u.roles
        agent_scope = "/".join(p.scope.split("/")[:2]) or p.scope
        specs = runtime.list_agents(scope=agent_scope) if runtime is not None else []
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
                           "can_use": usable})
        agents.sort(key=lambda a: a["name"])
        return {"scope": agent_scope, "agents": agents}

    @app.get("/api/events/examples")
    async def events_examples():
        from .catalog import as_list
        return {"examples": as_list()}

    @app.get("/api/events/setup-guides")
    async def events_setup_guides():
        """Per-connector 'how to connect' guides for the Studio (creds, ownership, steps). Shows the
        live EVENTS_PUBLIC_URL and whether each required credential is present, so the UI can render
        an accurate, actionable setup guide."""
        from . import setup_guides
        pub = os.environ.get("EVENTS_PUBLIC_URL", "").rstrip("/") or "<EVENTS_PUBLIC_URL>"
        out = []
        for g in setup_guides.as_list():
            creds = [{**c, "present": bool(os.environ.get(c["key"]))} for c in g.get("creds", [])]
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
            out.append({**g, "creds": creds, "steps": steps, "connect": connect})
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
        """Poll a Box folder for NEW files and fire the watcher agent on each — the AP-free Box path
        (sidesteps AP's OAuth wall + the paid-app webhook). Gateway-token protected so a schedule or
        cron can drive it. Body: {folder_id, since?, agent?, deliver_to?, scope?}. Returns the new
        files processed + the newest created_at (the caller stores it as the next `since` baseline)."""
        if token and request.headers.get("X-Gateway-Token") != token:
            return JSONResponse({"ok": False, "error": "bad or missing X-Gateway-Token"}, 401)
        from . import box_direct
        body = await _safe_json(request)
        folder = str(body.get("folder_id") or "0")
        since = body.get("since")
        agent = body.get("agent") or "resume_judge"
        deliver_to = body.get("deliver_to")            # e.g. a direct channel: "slack"
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
            await _box_dispatch(agent, f, deliver_to, body.get("scope"))
            processed.append({"id": f["id"], "name": f.get("name")})
        return {"ok": True, "folder": folder, "processed": processed, "newest": newest,
                "trace_id": tr.id}

    async def _box_dispatch(agent: str, file: dict, deliver_to, scope) -> None:
        """Fire one Box file through /invoke(agent). If deliver_to is a DIRECT channel the reply is
        sent CUGA-side (no AP); otherwise the answer just rides back in the /invoke response."""
        import httpx
        from . import delivery
        tr = Trace(new_trace_id())
        port = os.environ.get("EVENTS_CUGA_PORT", "8100")
        gw = (os.environ.get("GATEWAY_TOKEN", "") or "").split(" #", 1)[0].strip()
        direct = bool(deliver_to and delivery.is_direct(deliver_to))
        src = ({"type": "channel", "name": deliver_to, "thread_id": f"gw:{deliver_to}:"}
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
