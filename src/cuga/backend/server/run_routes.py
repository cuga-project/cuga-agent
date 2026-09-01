"""``POST /run`` and ``GET /run/agents`` — CUGA's machine seam, plus the supervisor roster.

Extracted from ``server/main.py`` rather than grown inside it, following the same shape as
``a2a.runner.build_a2a_router_for_settings``: this module owns the routes and their state, and
``main.py`` keeps one ``include_router`` call.

``/run`` is the non-streaming form of ``/stream``: one request, one JSON answer.

**Without a roster** it drives the SAME ``event_stream`` generator in-process, so knowledge,
history, citations, policies and HITL behave identically — only the output adapter differs.

**With a roster loaded it is NOT the same graph**, and the difference is worth stating plainly
rather than discovering:

  * it calls ``supervisor.invoke``, which does not take attachments, a resume payload, or HITL —
    those are silently absent, not deferred;
  * ``?agent=`` is ADVISORY. There is no public API on ``CugaSupervisor`` to invoke a named
    sub-agent, so the name is expressed as an instruction in the prompt and the model is free to
    ignore it. A webhook pinned to a specialist may be answered by a different one.

Routing to a named sub-agent properly needs a real API on the supervisor; until that exists, do not
read ``?agent=`` as a guarantee.

``event_stream`` and the default user id are INJECTED by :func:`build_run_router` rather than
imported from ``main``, which would be a cycle (``main`` imports this module to mount it).
"""

from __future__ import annotations

import hmac
import json
import os
import uuid
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from loguru import logger

from cuga.config import settings
from cuga.backend.server import events_bridge

router = APIRouter()

# Filled in by build_run_router(). Module-level rather than closed over so the handlers below stay
# readable at the top level instead of nested inside a 400-line factory.
_EVENT_STREAM = None
_DEFAULT_USER_ID = "default_user"


def run_api_enabled() -> bool:
    """Should ``/run`` and ``/run/agents`` be mounted at all?

    Mirrors A2A's ``if settings.a2a.enabled``. These routes execute an agent and require a shared
    secret, so with nothing configured every call 401s — mounting them then only advertises an
    endpoint nobody can use. Vanilla CUGA therefore gets a 404 and no extra surface.

    Enabled by any of:
      * ``CUGA_RUN_TOKEN`` / ``GATEWAY_TOKEN`` — a deployment that means to use the machine seam
      * ``CUGA_SUPERVISOR_ROSTER``            — this server is a preloaded supervisor
      * ``CUGA_RUN_ALLOW_UNAUTHENTICATED``    — the explicit development opt-out
    """
    return bool(_run_token() or _supervisor_roster_path() or _run_dev_unauthenticated())


def build_run_router(*, event_stream, default_user_id: str) -> APIRouter:
    """Wire the dependencies and hand back the router for ``app.include_router``."""
    global _EVENT_STREAM, _DEFAULT_USER_ID
    _EVENT_STREAM = event_stream
    _DEFAULT_USER_ID = default_user_id
    warn_if_run_is_unauthenticated()
    return router


# ── /run — the non-streaming form of /stream ───────────────────────────────────────────────────
# WHY: /stream is built for the web UI — it holds a connection open and emits every intermediate
# step as SSE. A service-to-service caller (the eventing layer, when it runs out-of-process) wants
# the opposite: one request, one JSON answer.
#
# NO ROSTER: it drives the SAME ``event_stream`` generator /stream does — an in-process call, NOT an
# HTTP call to /stream — so knowledge/attachments, conversation history, citations, policies and
# HITL all behave identically by construction. Only the OUTPUT adapter differs: intermediate frames
# are dropped, the terminal Answer is returned as the response body.
#
# WITH A ROSTER it is a DIFFERENT path: supervisor.invoke, which takes no attachments, no resume and
# no HITL, and where ?agent= is advisory (see the note in run_sync). Same endpoint, different
# guarantees — worth knowing before treating the two as interchangeable.
_RUN_ANSWER_NAMES = {"Answer", "final_answer"}
_RUN_ERROR_NAMES = {"Error", "error", "Stopped"}


# ── CUGA preloaded as a supervisor ─────────────────────────────────────────────────────────────
# CUGA_SUPERVISOR_ROSTER=<path to supervisor_agents.yaml> starts this server AS the supervisor:
# /run builds a CugaSupervisor from the roster once and every call routes through it, so the
# sub-agents are a CUGA-side concern — which is where they belong.
#
# WHY: the roster used to be loaded only by the events layer's SupervisorRuntime. That is fine when
# events and CUGA share a process, but once the worker moves across an HTTP hop the executing side
# is vanilla CUGA — no events layer, no runtime, no roster — so every call ran as CUGA's lone
# default agent. Callers do not (and should not) pass an agent: they address the supervisor, and it
# routes internally. One agent in the file or twenty-seven, the caller is unchanged.
_supervisor_cache: Dict[str, Any] = {}
# name → description for the loaded roster, kept beside the supervisor so /run/agents can answer
# "what is loaded here?" without reaching into CugaSupervisor's privates.
_supervisor_roster: Dict[str, List[Dict[str, str]]] = {}


def _supervisor_roster_path() -> str:
    return (os.environ.get("CUGA_SUPERVISOR_ROSTER", "") or "").split(" #", 1)[0].strip()


def _roster_details(path: str) -> Dict[str, Dict[str, Any]]:
    """name → {description, mcp_servers}, read from the roster YAML itself.

    load_supervisor_config builds CugaAgent instances and does NOT carry the YAML's descriptive
    fields onto them, so the objects can't be asked. Those fields matter: they are how the events
    layer's concierge decides which specialist a message belongs to, and a blank one makes a
    sub-agent effectively unroutable. Same fields the in-process SupervisorRuntime reads, so the
    split and combined topologies describe the roster identically.
    """
    try:
        import yaml as _yaml

        with open(path, "r") as f:
            cfg = _yaml.safe_load(f) or {}
        out: Dict[str, Dict[str, Any]] = {}
        for a in cfg.get("agents") or []:
            if not isinstance(a, dict) or not a.get("name"):
                continue
            out[str(a["name"])] = {
                "description": str(a.get("description") or a.get("special_instructions") or "").strip(),
                "mcp_servers": [
                    m.get("name") if isinstance(m, dict) else str(m) for m in (a.get("mcp_servers") or [])
                ],
            }
        return out
    except Exception as e:  # noqa: BLE001 — a nicety; never fail the load for it
        logger.warning(f"could not read agent details from roster {path!r}: {e}")
        return {}


async def _get_supervisor():
    """The preloaded supervisor, or None when this server isn't running as one."""
    path = _supervisor_roster_path()
    if not path:
        return None
    if path in _supervisor_cache:
        return _supervisor_cache[path]
    from cuga.sdk import CugaSupervisor
    from cuga.supervisor_utils.supervisor_config import load_supervisor_config

    # auto_load_policies=False: everything this supervisor runs is HEADLESS — a scheduled tick, a
    # webhook, a channel message. Nobody is present to answer an approval interrupt, and one would
    # hang the run until the caller times out. Asked for HERE rather than defaulted inside
    # load_supervisor_config, so CugaSupervisor.from_yaml and every other caller keep the upstream
    # behaviour of honouring settings.policy.auto_load_policies. A roster entry can opt back in.
    #
    # scope_tools=True: same principle, same reason it is asked for HERE. A roster sub-agent is a
    # specialist — pricebot should hold cuga-finance, not the whole registry — which keeps the
    # concierge's delegation sharp and the sub-agent's prompt small. Defaulting it on would strip
    # tools from every supervisor built through the SDK, so only this roster opts in.
    cfg = await load_supervisor_config(path, auto_load_policies=False, scope_tools=True)
    sup = CugaSupervisor(
        agents=cfg.agents,
        special_instructions=(cfg.supervisor or {}).get("special_instructions"),
    )
    _supervisor_cache[path] = sup
    _details = _roster_details(path)
    _supervisor_roster[path] = [
        {"name": n, **_details.get(n, {"description": "", "mcp_servers": []})} for n in (cfg.agents or {})
    ]
    logger.info(f"CUGA is running AS a supervisor: {len(cfg.agents)} sub-agent(s) from {path}")
    return sup


def _run_token() -> str:
    """Shared secret for the machine seam. CUGA_RUN_TOKEN wins; GATEWAY_TOKEN is the events
    layer's own token, reused so a split deployment configures ONE secret."""
    return (os.environ.get("CUGA_RUN_TOKEN") or os.environ.get("GATEWAY_TOKEN") or "").strip()


RUN_DEV_UNAUTH_ENV = "CUGA_RUN_ALLOW_UNAUTHENTICATED"


def _run_dev_unauthenticated() -> bool:
    """Is the explicit development opt-out set? Read live so a test can toggle it."""
    return (os.environ.get(RUN_DEV_UNAUTH_ENV, "") or "").split(" #", 1)[0].strip().lower() in (
        "1",
        "true",
        "yes",
    )


def warn_if_run_is_unauthenticated() -> None:
    """Say it out loud at boot. An unauthenticated exec endpoint should never be a quiet default."""
    if not _run_token() and _run_dev_unauthenticated():
        logger.warning(
            "{}=1 — POST /run and /run/agents are UNAUTHENTICATED. Anyone who can reach this port "
            "can execute the agent. Development only; set CUGA_RUN_TOKEN before exposing this server.",
            RUN_DEV_UNAUTH_ENV,
        )


def _run_auth_failure(request: Request) -> Optional[JSONResponse]:
    """Guard the machine seam. Returns the 401 to send, or None to proceed.

    ``/run`` and ``/run/agents`` EXECUTE an agent. The gate used to read
    ``if token and request.headers.get(...) != token`` — so with no token configured, which is
    vanilla CUGA (neither ``CUGA_RUN_TOKEN`` nor ``GATEWAY_TOKEN`` is set by default), the condition
    was false and the check evaporated entirely. Anyone who could reach the port could run the
    agent, including while CUGA authentication was enabled and ``/stream`` was login-gated behind
    ``require_chat_access``. An always-mounted endpoint must not be the one unlocked door.

    So it FAILS CLOSED: a token is required. The single way out is an explicit development flag,
    which is deliberately verbose and logged at startup — a deployment cannot end up unauthenticated
    by simply forgetting to configure something.
    """
    token = _run_token()
    if token:
        supplied = request.headers.get("X-Gateway-Token") or ""
        # compare_digest on `str` raises TypeError for non-ASCII, and this header is attacker
        # supplied — compare bytes so a hostile header is a 401, not a 500.
        if not hmac.compare_digest(supplied.encode("utf-8", "replace"), token.encode("utf-8", "replace")):
            return JSONResponse(
                {"ok": False, "status": "error", "error": "bad or missing X-Gateway-Token"}, 401
            )
        return None
    if _run_dev_unauthenticated():
        return None
    return JSONResponse(
        {
            "ok": False,
            "status": "error",
            "error": (
                "/run requires a shared secret. Generate one with "
                "`python -c \"import secrets; print(secrets.token_urlsafe(32))\"` and set it as "
                "CUGA_RUN_TOKEN (or GATEWAY_TOKEN, which the eventing service already uses) on both "
                f"processes. For local development only, set {RUN_DEV_UNAUTH_ENV}=1."
            ),
        },
        401,
    )


def _run_unpack_answer(payload: Any) -> Dict[str, Any]:
    """The DEFAULT-mode Answer payload is a JSON string carrying the answer plus its sidecars
    (see the Answer emission in event_stream); WXO mode sends bare text. Accept both."""
    if isinstance(payload, dict):
        obj = payload
    else:
        text = payload if isinstance(payload, str) else str(payload or "")
        try:
            obj = json.loads(text)
        except (json.JSONDecodeError, TypeError):
            return {"answer": text, "sources": [], "variables": {}}
        if not isinstance(obj, dict):
            return {"answer": text, "sources": [], "variables": {}}
    return {
        "answer": obj.get("data") if isinstance(obj.get("data"), str) else (obj.get("answer") or ""),
        "sources": obj.get("sources") or [],
        "variables": obj.get("variables") or {},
        "active_policies": obj.get("active_policies") or [],
    }


@router.post("/run")
async def run_sync(request: Request):
    """Run one task to completion and return the final answer as a single JSON body.

    Body: ``{query, thread_id?, agent?, user_id?, disable_history?, attachments?, action_response?}``
    Reply: ``{ok, status, answer, thread_id, sources, variables, error}`` where ``status`` is
    ``ok`` | ``error`` | ``interrupt`` (the graph paused for human input).

    Knowledge bases are NOT passed here — they attach out-of-band to the ``thread_id`` (session
    scope) or to the agent, exactly as for /stream, and are picked up by identity at run time.
    """
    # AUTHENTICATE FIRST — before the imports below, not after. Those pull in cuga.config, whose
    # load_dotenv(override=False) refills any variable that is currently ABSENT, so a lazy import
    # could put GATEWAY_TOKEN back mid-request and change the answer the gate gives. An auth check
    # that depends on import side effects is not a check.
    denied = _run_auth_failure(request)
    if denied is not None:
        return denied

    from cuga.backend.cuga_graph.nodes.human_in_the_loop.followup_model import ActionResponse
    from cuga.backend.cuga_graph.utils.agent_loop import StreamEvent

    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        return JSONResponse({"ok": False, "status": "error", "error": "body must be JSON"}, 400)
    if not isinstance(body, dict):
        return JSONResponse({"ok": False, "status": "error", "error": "body must be a JSON object"}, 400)

    query = body.get("query")
    resume_raw = body.get("action_response")
    if not isinstance(query, str) or not query.strip():
        if not resume_raw:
            return JSONResponse(
                {"ok": False, "status": "error", "error": "query is required (or action_response to resume)"},
                422,
            )
        query = None
    resume = None
    if resume_raw:
        try:
            resume = ActionResponse(**resume_raw)
        except Exception:  # noqa: BLE001
            # The LOG keeps the detail; the RESPONSE must not. str(e) on a validation failure
            # carries model internals (and, on any other exception, a stack trace) out to whoever
            # called the endpoint — CodeQL py/stack-trace-exposure.
            logger.exception("/run rejected a malformed action_response")
            return JSONResponse(
                {"ok": False, "status": "error", "error": "action_response is malformed"}, 422
            )

    thread_id = str(body.get("thread_id") or "") or str(uuid.uuid4())
    user_id = str(body.get("user_id") or "") or _DEFAULT_USER_ID
    disable_history = bool(body.get("disable_history", False))
    attachments = body.get("attachments") or None

    # ── CUGA IS THE DOOR ────────────────────────────────────────────────────────────────────────
    # Every channel utterance (Slack/Telegram/Discord/web) arrives HERE, not at the eventing layer.
    # The adapters are pure transport; the decision is CUGA's, and it is the same one rule /stream
    # applies: an explicit slash verb, or a thread with an arming dialogue already open, goes to the
    # eventing service. Everything else is ordinary chat and never touches it.
    #
    # The arming conversation is multi-turn ("which repo?", "yes", "change the prompt to …"), so the
    # open-dialogue check is what keeps the follow-ups routed — a bare "yes" means nothing on its own.
    channel = body.get("channel") if isinstance(body.get("channel"), dict) else None
    if isinstance(query, str) and events_bridge.forwards_to_events(query, thread_id):
        reply = await events_bridge.forward_slash_to_events(
            query, thread_id, request.headers, channel=channel
        )
        return {
            "ok": bool(reply),
            "status": "ok" if reply else "error",
            "answer": reply,
            "thread_id": thread_id,
            "sources": [],
            "variables": {},
            "routed_to": "events",
            "error": None if reply else "eventing layer returned nothing",
        }

    run_agent = None
    if str(body.get("use_draft", "")).lower() in ("1", "true", "yes", "on"):
        draft_state = getattr(request.app.state, "draft_app_state", None)
        if draft_state and getattr(draft_state, "agent", None):
            run_agent = draft_state.agent

    out: Dict[str, Any] = {"answer": "", "sources": [], "variables": {}}
    status, err = "", ""

    # Preloaded-supervisor mode: this server IS the supervisor, so the run goes through it and the
    # sub-agent routing happens inside. Only /run takes this path — /stream and the UI keep their
    # existing agent, so turning the roster on cannot disturb the interactive surface.
    supervisor = None
    try:
        supervisor = await _get_supervisor()
    except Exception:  # noqa: BLE001 — a bad roster must not take the endpoint down
        # The roster PATH and the exception text are both server-side detail: the path exposes the
        # deployment's filesystem layout and str(e) can carry a stack trace. Log both, return
        # neither (CodeQL py/stack-trace-exposure).
        logger.exception("supervisor roster %r failed to load", _supervisor_roster_path())
        return JSONResponse(
            {
                "ok": False,
                "status": "error",
                "answer": "",
                "thread_id": thread_id,
                "error": "the supervisor roster failed to load — see the server log",
            },
            500,
        )
    if supervisor is not None and query:
        # PINNED sub-agent — ADVISORY, not a guarantee. A caller naming a real sub-agent (a webhook
        # with ?agent=incident_triage, a subscription armed against a specialist) gets that name
        # expressed as an INSTRUCTION IN THE PROMPT, because CugaSupervisor exposes no API to invoke
        # a named sub-agent. The model can ignore it, so a run pinned to a specialist may be answered
        # by a different one. Doing this properly needs a routing API on the supervisor; until then
        # do not treat ?agent= as routing. A name absent from the roster is ignored, not an error.
        pinned = str(body.get("agent") or "").split("::")[-1].strip()
        roster_names = {a["name"] for a in (_supervisor_roster.get(_supervisor_roster_path()) or [])}
        if pinned and pinned != "cuga" and pinned in roster_names:
            query = (
                f"Delegate this to the `{pinned}` agent — it is the right specialist. "
                f"Return its answer.\n\n{query}"
            )
        try:
            res = await supervisor.invoke(query, thread_id=thread_id)
            answer = (getattr(res, "answer", None) or getattr(res, "result", None) or "") if res else ""
            return {
                "ok": bool(answer),
                "status": "ok" if answer else "error",
                "answer": answer,
                "thread_id": thread_id,
                "sources": list(getattr(res, "sources", None) or []),
                "variables": dict(getattr(res, "variables", None) or {}),
                "error": None if answer else "supervisor returned an empty answer",
            }
        except Exception:  # noqa: BLE001
            logger.exception("/run supervisor invoke failed")  # detail stays here, not in the reply
            return JSONResponse(
                {
                    "ok": False,
                    "status": "error",
                    "answer": "",
                    "thread_id": thread_id,
                    "error": "the run failed — see the server log",
                },
                500,
            )
    try:
        async for frame in _EVENT_STREAM(
            query,
            api_mode=settings.advanced_features.mode == "api",
            resume=resume,
            thread_id=thread_id,
            agent=run_agent,
            disable_history=disable_history,
            user_id=user_id,
            user_attachments=attachments,
        ):
            try:
                ev = StreamEvent.parse(
                    frame.decode("utf-8") if isinstance(frame, (bytes, bytearray)) else str(frame)
                )
            except Exception:  # noqa: BLE001 — a malformed/foreign frame must not sink the run
                continue
            if ev is None or not ev.name:
                continue
            if ev.name in _RUN_ANSWER_NAMES:
                out = _run_unpack_answer(ev.data)
                status = "ok"
                break
            if ev.name in _RUN_ERROR_NAMES:
                unpacked = _run_unpack_answer(ev.data)
                err = unpacked.get("answer") or f"agent {ev.name}"
                status = "error"
                break
            # every other frame is in-flight progress — the whole point of /run is to drop it
    except Exception:  # noqa: BLE001
        logger.exception("/run failed")  # detail stays here, not in the reply
        return JSONResponse(
            {
                "ok": False,
                "status": "error",
                "answer": "",
                "thread_id": thread_id,
                "error": "the run failed — see the server log",
            },
            500,
        )

    if not status:
        # The stream ended with no terminal frame. That is the HITL shape: the graph paused
        # awaiting an ActionResponse. Report it honestly rather than as a silent empty answer.
        status, err = "interrupt", "agent paused awaiting human input"
    return {
        "ok": status == "ok",
        "status": status,
        "answer": out.get("answer") or "",
        "thread_id": thread_id,
        "sources": out.get("sources") or [],
        "variables": out.get("variables") or {},
        "error": err or None,
    }


@router.get("/run/agents")
async def run_agents(request: Request):
    """What this server has loaded — the machine-readable sibling of /run.

    A split deployment puts the eventing layer in its own process, so it has no roster of its own:
    the roster belongs to whoever executes, which is this server. Without this endpoint the events
    side had to guess, and it guessed "one agent" — so a webhook pinned to a real sub-agent
    (``?agent=incident_triage``) was rejected as unknown before it ever reached the supervisor.

    Deliberately NOT /api/agents: that one is the dashboard's, sits behind the manage-access cookie,
    and returns UI card data for the configured agent. This is the machine seam, guarded by the same
    shared secret as /run.
    """
    denied = _run_auth_failure(request)
    if denied is not None:
        return denied
    path = _supervisor_roster_path()
    if not path:
        # Not running as a supervisor: one plain agent, still addressable as "cuga".
        return {
            "ok": True,
            "supervisor": False,
            "roster": "",
            "agents": [{"name": "cuga", "description": "the CUGA agent", "mcp_servers": []}],
        }
    try:
        await _get_supervisor()  # builds + populates _supervisor_roster on first call
    except Exception:  # noqa: BLE001
        # Same rule as /run: the roster path is the deployment's filesystem layout and str(e) can
        # carry a stack trace. Both belong in the log, neither in the response (CodeQL
        # py/stack-trace-exposure).
        logger.exception("roster %r failed to load", path)
        return JSONResponse({"ok": False, "error": "the roster failed to load — see the server log"}, 500)
    subs = list(_supervisor_roster.get(path) or [])
    # "cuga" is the supervisor itself and is always addressable — callers that know nothing about
    # the roster target it and let it route.
    agents = [{"name": "cuga", "description": "the CUGA supervisor", "mcp_servers": []}] + [
        s for s in subs if s.get("name") != "cuga"
    ]
    return {"ok": True, "supervisor": True, "roster": path, "agents": agents}
