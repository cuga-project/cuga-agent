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
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

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
            from .principal import channel_native_id, resolve_channel
            nid = channel_native_id(env.source)
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
            from .principal import channel_native_id
            txt = (env.text or "").strip()
            for pfx in ("/start ", "/link "):
                if txt.startswith(pfx):
                    nid = channel_native_id(env.source)
                    uid = identity.redeem_token(txt[len(pfx):].strip(), nid) if nid else None
                    tr("channel.link", channel=env.source.name, native=nid, ok=bool(uid))
                    return {"ok": True, "linked": bool(uid), "trace_id": tr.id,
                            "answer": ("Your account is linked — you can chat now."
                                       if uid else "That link code is invalid or expired.")}
        # agents are tenant-shared (agent_scope = first 2 scope segments); run-state is per-user
        agent_scope = "/".join(scope.split("/")[:2]) or scope
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
                tr("concierge", ok=True, scope=scope, ms=int((time.time() - t0) * 1000))
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
                tr("worker.done", agent=agent, ok=True, ms=int((time.time() - t0) * 1000))
            except Exception as e:  # noqa: BLE001
                tr.error("error", agent=agent, err=str(e))
                return JSONResponse({"ok": False, "error": str(e)}, 500)
        # Delivery: channel/schedule flows now deliver via an AP send step (deliver=False here,
        # AP owns the outbound). deliver=True is the web/self-delivery path: POST the answer to
        # EA_CAPTURE_URL when set (an assertable, real-HTTP delivery target for e2e/web).
        if env.deliver:
            cap = os.environ.get("EA_CAPTURE_URL")
            if cap:
                try:
                    import httpx
                    async with httpx.AsyncClient(timeout=10) as hc:
                        await hc.post(cap, json={"agent": agent, "answer": answer,
                                                 "thread_id": env.thread_id, "trace_id": tr.id})
                    tr("deliver", via="capture", ok=True)
                except Exception as e:  # noqa: BLE001
                    tr.error("deliver", via="capture", err=str(e))
        return {"ok": True, "agent": agent, "answer": answer, "trace_id": tr.id}

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
                # step). Telegram is the only channel round-trip-verified live so far.
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
            tokens = await oauth.exchange_code(app, code)
            ext = credentials.connection_external_id(app, "per-user", p)
            if engine is not None:
                prov = oauth.provider(app)
                grain = os.environ.get("EVENTS_AP_PROJECT_GRAIN", "tenant")
                await engine.ensure_oauth_connection(
                    ext, prov["piece"], tokens,
                    client_id=os.environ.get(f"EVENTS_OAUTH_{app.upper()}_CLIENT_ID", ""),
                    client_secret=os.environ.get(f"EVENTS_OAUTH_{app.upper()}_CLIENT_SECRET", ""),
                    token_url=prov["token"], scope=" ".join(prov.get("scopes", [])),
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
        """Paste a raw credential → an AP connection.
          - token apps (GitHub PAT, Telegram bot) → a SECRET_TEXT connection.
          - OAuth apps (Box/Gmail/…) with a pasted **access/developer token** → an OAUTH2
            connection carrying just that access_token (no refresh). This is the **Box Developer
            Token** path: it sidesteps the redirect-URI config a free Box account can't save, but
            the token **expires (~60 min) and can't refresh** — regenerate to re-demo.
        """
        from . import oauth, credentials
        body = await request.json()
        token = (body or {}).get("token", "")
        prov = oauth.provider(app)
        if prov is None:
            return JSONResponse({"ok": False, "error": f"unknown app '{app}'"}, 404)
        if not token:
            return JSONResponse({"ok": False, "error": "missing 'token'"}, 400)
        p = _principal_from((body or {}).get("scope"), request.headers)
        ext = credentials.connection_external_id(app, "per-user", p)
        if engine is None:
            return JSONResponse({"ok": False, "error": "AP not configured"}, 501)
        grain = os.environ.get("EVENTS_AP_PROJECT_GRAIN", "tenant")
        dev = oauth.connect_kind(app) != "token"      # OAuth app + pasted token = dev/access token
        try:
            if not dev:
                await engine.ensure_secret_connection(ext, prov["piece"], token,
                                                      project_name=p.ap_project_name(grain))
            else:
                await engine.ensure_oauth_connection(
                    ext, prov["piece"], {"access_token": token},
                    client_id=os.environ.get(f"EVENTS_OAUTH_{app.upper()}_CLIENT_ID", ""),
                    client_secret=os.environ.get(f"EVENTS_OAUTH_{app.upper()}_CLIENT_SECRET", ""),
                    token_url=prov.get("token", ""), scope=" ".join(prov.get("scopes", [])),
                    redirect_url=oauth.redirect_uri(app), project_name=p.ap_project_name(grain))
        except Exception as e:  # noqa: BLE001
            import traceback
            Trace(new_trace_id()).error("connect_token", app=app, err=repr(e), tb=traceback.format_exc())
            return JSONResponse({"ok": False, "error": repr(e) or type(e).__name__}, 500)
        if dev:
            return {"ok": True, "app": app, "connection": ext,
                    "note": "access-token connection (dev token: ~60 min, no refresh — regenerate to renew)"}
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
        """Admin: arm a channel INBOUND flow in AP (channel·new_message → /invoke(concierge) →
        send). One per channel; every message routes to the concierge. AP owns the channel."""
        if engine is None:
            return JSONResponse({"ok": False, "error": "AP not configured"}, 501)
        body = await _safe_json(request)
        p = _principal_from(body.get("scope") or request.query_params.get("scope"), request.headers)
        if not _is_admin(p):
            return JSONResponse({"ok": False, "error": "admin only"}, 403)
        grain = os.environ.get("EVENTS_AP_PROJECT_GRAIN", "tenant")
        from . import credentials
        conn = credentials.connection_external_id(channel, "per-user", p)  # the bot connection
        try:
            flow_id = await engine.create_inbound_flow(
                channel=channel, agent="concierge", connection=conn,
                project_name=p.ap_project_name(grain), scope=p.scope)
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
