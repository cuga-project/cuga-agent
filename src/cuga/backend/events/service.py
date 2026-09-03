"""The eventing layer as its OWN service.

Today the events routes mount onto CUGA's FastAPI app, so one process is "CUGA + eventing". That
works, but it forks CUGA's ``main.py`` (every upstream release fights the mount), pins the whole
thing to a single replica (the scheduler and channel loops are process-wide singletons), and puts
every channel bot token in CUGA's image.

This module runs the SAME routes as a standalone app that calls CUGA over HTTP (``/run``, the
non-streaming sibling of ``/stream``). Nothing about the wire contract changes: ``/invoke`` and
``/api/events/*`` are byte-for-byte what they were, so every existing harness and every armed
subscription keeps working — which is the whole point.

    Combined (today, local dev):   one process,  events mounted on CUGA
    Split   (this module):         two services, eventing → CUGA /run

Run it with::

    uv run python -m cuga.backend.events.service          # or: make run-events

Config:
  CUGA_URL              base URL of the CUGA service        (default http://127.0.0.1:7860)
  CUGA_RUN_TOKEN        shared secret for the /run hop      (falls back to GATEWAY_TOKEN)
  GATEWAY_TOKEN         guards this service's own /invoke
  EVENTS_DB             durable store path                  (default ~/.cuga/events.db)
  EVENTS_SERVICE_PORT   listen port                         (default 8100)
"""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager

log = logging.getLogger("events.service")


def _db_path() -> str:
    """Same resolution as the mounted path in CUGA's main.py — durable by default."""
    p = (os.environ.get("EVENTS_DB", "") or "").strip()
    if not p:
        p = os.path.join(os.path.expanduser("~"), ".cuga", "events.db")
    if p != ":memory:":
        try:
            os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
        except OSError as e:
            log.warning("cannot create %s (%s) — falling back to :memory:", p, e)
            return ":memory:"
    return p


def _load_env() -> None:
    """Load the repo ``.env`` exactly as CUGA's own server does.

    Mounted on CUGA, the events routes inherit an environment ``cuga.config`` already populated at
    import time. Standalone, nothing had done that — so a locally-run service came up with an empty
    GATEWAY_TOKEN (its ``/run`` calls to CUGA came back 401, and the roster read silently fell back
    to a stale local row) and no channel tokens at all. Real environment variables WIN
    (``override=False``): on Code Engine the config arrives as env from the secret, and a .env that
    isn't in the image must never be able to shadow it.
    """
    try:
        from dotenv import find_dotenv, load_dotenv
    except ImportError:  # dotenv is a CUGA dep; absence just means "env only"
        return
    forced = os.environ.get("ENV_FILE")
    path = forced or find_dotenv(filename=".env", usecwd=True) or find_dotenv(filename=".env", usecwd=False)
    if path and os.path.isfile(path):
        load_dotenv(path, override=bool(forced))
        log.info("events service: loaded env from %s", path)
    _apply_defaults()


def _apply_defaults() -> None:
    """The events layer's own defaults. These used to live in ``cuga start … --events``; when that
    flag was deleted with combined mode they went with it, and the service inherited none of them.

    ``EVENTS_USER_ID`` is the one that bites: with it unset every unlinked channel message resolves
    to user ``local``, while the Studio and the harnesses browse as ``admin`` — so a flow armed from
    Slack was stored under one scope and listed under another. Armed, fired, delivered, and
    completely invisible in the Flows tab. ``setdefault`` throughout, so .env and real env still win.
    """
    os.environ.setdefault("EVENTS_USER_ID", "admin")  # Studio + channels share one scope
    os.environ.setdefault("EVENTS_WORKER_BACKEND", "http")
    os.environ.setdefault("EVENTS_SEED_AGENTS", "1")


def create_app():
    """Build the standalone eventing app: the same routes, a worker that calls CUGA over HTTP.

    Deliberately does NOT load .env — that belongs to ``main()``, the process entrypoint. Tests
    construct this app directly and must not inherit live bot tokens from the developer's .env.
    """
    from fastapi import FastAPI

    from .agent_store import AgentStore
    from .app import register_events_routes
    from .concierge import Concierge
    from .identity import IdentityMap
    from .llm import default_model_factory
    from .oauth import OAuthAppStore
    from .runtime import make_runtime
    from .subscriptions import SubscriptionStore
    from .users import UserStore

    db = _db_path()
    # REDACTED. This logged the DSN verbatim: harmless for a SQLite path, but for the managed
    # PostgreSQL it is postgres://user:PASSWORD@host/db — so every boot wrote live database
    # credentials in plaintext to the platform log, readable by anyone with log access to the
    # project and retained long after a rotation. db._redact keeps the shape, drops the secret.
    from .db import _redact as _redact_dsn

    log.info("events service: store = %s", _redact_dsn(db))

    # DURABILITY. The container filesystem is ephemeral: when the platform replaces the instance,
    # the new pod gets an empty disk and every armed flow is gone with no restart recorded. Restore
    # the durable snapshot BEFORE any store opens the file, or the stores create fresh empty tables
    # and the restore has nothing to put back. See db_persist.py for why the live DB stays local.
    from . import db_persist

    db_persist.restore(db)

    # WHERE /invoke LIVES. Every trigger source (native scheduler, Slack/Discord/Telegram loops,
    # box poll, webhooks) fires by POSTing an envelope to `http://127.0.0.1:$EVENTS_CUGA_PORT/invoke`.
    # Mounted on CUGA, that port IS CUGA's — same process, so the name reads fine. Split out, /invoke
    # belongs to THIS service, and leaving the default pointed the scheduler at CUGA's port: ticks
    # were POSTed to the other process, which has a different store and had never heard of the
    # subscription — flows armed and then silently never fired.
    # Resolve CUGA's address FIRST (it may be expressed via that same var), then repoint.
    # CUGA's address comes from CUGA_URL ONLY — deliberately not from EVENTS_CUGA_PORT, which we
    # are about to repoint at ourselves. Deriving one from the other made create_app() unsafe to
    # call twice in a process: the first call set EVENTS_CUGA_PORT=<self>, the second read it back
    # as "where CUGA is", saw its own port, and tripped the self-call guard below.
    port = int(os.environ.get("EVENTS_SERVICE_PORT", "8100"))
    cuga_url = (os.environ.get("CUGA_URL") or "http://127.0.0.1:7860").rstrip("/")
    if cuga_url.rstrip("/").endswith(f":{port}"):
        raise RuntimeError(
            f"CUGA_URL ({cuga_url}) points at this service's own port — the worker would call "
            f"itself in a loop. Set CUGA_URL to the CUGA service."
        )
    os.environ["EVENTS_CUGA_PORT"] = str(port)  # loopback /invoke = this service
    log.info("events service: /invoke on :%d · CUGA at %s", port, cuga_url)

    store = SubscriptionStore(db)
    agents = AgentStore(db)
    users = UserStore(db)
    identity = IdentityMap(db)
    oauth_store = OAuthAppStore(db)

    # BOOTSTRAP THE FIRST ADMIN, or the deployment deadlocks.
    #
    # `users` is always constructed, so `_is_builder`/`_is_admin` always consult it — and on a fresh
    # deployment it is EMPTY, which means nobody holds a role. Creating an agent in the Studio is
    # builder-only (403) and creating the user who could is admin-only (403), so there was no path
    # in: `seed_default_users` existed but was never called from anywhere.
    #
    # Identity only, no password: roles are what the gates read, and the local-login path is
    # separate. This grants nothing that the header-based principal model does not already grant —
    # see decisions/0009, which is where wiring real auth is tracked.
    _admins = [
        u.strip() for u in (os.environ.get("EVENTS_ADMIN_USERS", "admin") or "").split(",") if u.strip()
    ]
    for _uid in _admins:
        try:
            if users.get(_uid, "default") is None:
                users.add(_uid, roles=["admin", "builder", "user"], tenant="default")
                log.info("events service: bootstrapped admin/builder identity %r", _uid)
        except Exception as e:  # noqa: BLE001 — never block startup on the user store
            log.warning("events service: could not bootstrap admin %r: %s", _uid, e)

    # THE split: the worker crosses the wire. Everything else — triggers, scheduler, channels,
    # concierge, delivery — stays right here, which is why the direct integrations are unaffected.
    runtime = make_runtime(
        "http", agent_store=agents, cuga_url=cuga_url, cuga_token=os.environ.get("CUGA_RUN_TOKEN", "")
    )
    concierge = Concierge(
        runtime, store=store, engine=_engine(), model_factory=default_model_factory, users=users
    )

    @asynccontextmanager
    async def lifespan(app):
        # The background loops (Telegram long-poll, Discord gateway, native scheduler, direct
        # channel pollers) are registered by register_events_routes into app.state and started
        # here. In the mounted setup this lives in CUGA's lifespan — losing that launcher once
        # already cost us a silent outage, so the standalone service owns it explicitly.
        tasks = []
        for factory in getattr(app.state, "events_background", []) or []:
            tasks.append(asyncio.create_task(factory()))
        # Snapshot the store to durable storage whenever it changes. Started here rather than
        # registered as an events_background factory so it runs even if the events routes register
        # nothing — losing armed flows must not depend on which channels happen to be configured.
        if db_persist.backup_path():
            tasks.append(asyncio.create_task(db_persist.run_snapshot_loop(db)))
        if tasks:
            log.info("events service: launched %d background task(s)", len(tasks))
        try:
            yield
        finally:
            for t in tasks:
                t.cancel()

    app = FastAPI(title="CUGA eventing", lifespan=lifespan)
    # The Studio UI is served by CUGA (it owns the SPA) but calls THIS service for /api/events/*,
    # /api/concierge and /invoke. That is cross-origin whenever the two run on different ports, so
    # the browser needs CORS. EVENTS_CORS_ORIGINS (comma-separated) is the explicit setting; the CE
    # deploy script sets it to the CUGA app's URL.
    _origins = [
        o.strip().rstrip("/")
        for o in (os.environ.get("EVENTS_CORS_ORIGINS", "") or "").split(",")
        if o.strip()
    ]
    if not _origins:
        # UNSET USED TO MEAN "combined mode, same origin, no CORS needed". That has been wrong ever
        # since combined mode was removed: `make up-noap` puts CUGA on :7860 and this service on
        # :8100, which a browser treats as two origins. The preflight OPTIONS then got a bare 405
        # with no Access-Control-* headers, the browser cancelled the request, and the Studio's
        # concierge box printed NOTHING — no error, no reply, because the fetch never completed.
        # It only worked on Code Engine, which is the one place that sets the variable.
        #
        # So derive the default from CUGA_URL, which this service already must know in order to
        # call /run at all. Both host spellings are listed because the SPA is reachable at either
        # and the browser sends whichever the human typed.
        _cuga = (os.environ.get("CUGA_URL", "") or "http://127.0.0.1:7860").rstrip("/")
        _origins = [_cuga]
        for a, b in (("127.0.0.1", "localhost"), ("localhost", "127.0.0.1")):
            if a in _cuga:
                _origins.append(_cuga.replace(a, b))
        log.info("events service: CORS origins defaulted from CUGA_URL (%s)", ", ".join(_origins))
    if _origins:
        from fastapi.middleware.cors import CORSMiddleware

        app.add_middleware(
            CORSMiddleware,
            allow_origins=_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )
        log.info("events service: CORS enabled for %s", ", ".join(_origins))
    gw = (os.environ.get("GATEWAY_TOKEN", "") or "").split(" #", 1)[0].strip()
    register_events_routes(
        app,
        runtime=runtime,
        store=store,
        concierge=concierge,
        engine=_engine(),
        users=users,
        identity=identity,
        oauth_store=oauth_store,
        gateway_token=gw,
    )
    app.state.ev_concierge = concierge

    @app.get("/health")
    async def health():
        return {"ok": True, "service": "events", "cuga": os.environ.get("CUGA_URL", "")}

    return app


def _engine():
    """The Activepieces engine when configured, else None (the no-AP route stays first-class)."""
    if not (os.environ.get("AP_BASE_URL") or "").strip():
        return None
    try:
        from .ap_engine import APEngine

        return APEngine()
    except Exception as e:  # noqa: BLE001 — AP is optional; never block boot on it
        log.warning("Activepieces configured but unavailable (%s) — continuing without it", e)
        return None


def main() -> None:
    import uvicorn

    logging.basicConfig(level=os.environ.get("EVENTS_LOG_LEVEL", "INFO"))
    _load_env()  # the process entrypoint owns config, not create_app()
    # httpx logs every request URL at INFO — and channel APIs carry the BOT TOKEN in the path
    # (api.telegram.org/bot<token>/getUpdates), so INFO httpx logging writes live credentials into
    # the log. Quiet it unless someone explicitly asks for it.
    if os.environ.get("EVENTS_LOG_HTTPX") != "1":
        logging.getLogger("httpx").setLevel(logging.WARNING)
    port = int(os.environ.get("EVENTS_SERVICE_PORT", "8100"))
    uvicorn.run(create_app(), host=os.environ.get("EVENTS_HOST", "0.0.0.0"), port=port)


if __name__ == "__main__":
    main()
