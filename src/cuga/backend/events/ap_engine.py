"""APEngine — creates real Activepieces flows over REST (Phase 2: CRON/POLL).

Ported/trimmed from event-agent-ap ``automation/activepieces.py``. For a standing trigger we
build an AP flow:  [ Schedule trigger ]  →  [ HTTP → POST {invoke_url} ]. The HTTP step calls
back into the events ``/invoke`` receiver with the normalized envelope; the receiver runs the
worker and (deliver=true) delivers. Piece versions + auth are read live from AP.

Auth: AP_EMAIL/AP_PASSWORD (community edition → JWT) or AP_API_KEY (cloud). Config via env
(AP_BASE_URL, HOST_CALLBACK_URL, GATEWAY_TOKEN). PUSH/branching flows are Phase 3.
"""

from __future__ import annotations

import logging
import os

import httpx

log = logging.getLogger("cuga.events.ap")

SCHEDULE_PIECE = "@activepieces/piece-schedule"
HTTP_PIECE = "@activepieces/piece-http"


class APError(RuntimeError):
    pass


class APEngine:
    kind = "activepieces"

    def __init__(self) -> None:
        self.base = os.environ["AP_BASE_URL"].rstrip("/")
        self.api_key = os.environ.get("AP_API_KEY", "")
        self.email = os.environ.get("AP_EMAIL", "")
        self.password = os.environ.get("AP_PASSWORD", "")
        self.project_id = os.environ.get("AP_PROJECT_ID", "")
        self.invoke_url = (os.environ.get("HOST_CALLBACK_URL")
                           or os.environ.get("EA_INVOKE_URL")
                           or "http://host.docker.internal:8000/invoke")
        self.gateway_token = os.environ.get("GATEWAY_TOKEN", "")
        self._token = ""
        self._piece_cache: dict[str, str] = {}
        self._project_cache: dict[str, str] = {}   # project displayName -> id
        self._degraded = False                      # set if the AP plan refuses extra projects
        # Isolation grain for AP PROJECTS: 'tenant' (default; per-tenant project — the right
        # multi-tenant boundary), 'user' (per-user project — strict), or 'shared' (one project;
        # isolation via flow-name namespacing only). Flow names + connection externalIds are
        # ALWAYS full-scope-prefixed, so finer isolation holds regardless of grain. If the AP
        # plan caps projects (CE = 1), the engine auto-degrades to 'shared' behavior.
        self.project_grain = os.environ.get("EVENTS_AP_PROJECT_GRAIN", "tenant")

    # ---- auth ------------------------------------------------------------
    async def _sign_in(self, c: httpx.AsyncClient) -> None:
        r = await c.post(f"{self.base}/api/v1/authentication/sign-in",
                         json={"email": self.email, "password": self.password})
        if r.status_code >= 300:
            raise APError(f"AP sign-in failed: HTTP {r.status_code} {r.text[:200]}")
        body = r.json()
        self._token = body.get("token", "")
        if not self._token:
            raise APError("AP sign-in returned no token")
        if not self.project_id:
            self.project_id = body.get("projectId", "")

    async def _auth(self, c: httpx.AsyncClient) -> dict:
        if self.api_key:
            return {"Authorization": f"Bearer {self.api_key}"}
        if not (self.email and self.password):
            raise APError("AP needs AP_API_KEY or AP_EMAIL+AP_PASSWORD")
        await self._sign_in(c)   # fresh each op — robust to AP restarts
        return {"Authorization": f"Bearer {self._token}"}

    async def _piece_version(self, c: httpx.AsyncClient, name: str) -> str:
        if name in self._piece_cache:
            return self._piece_cache[name]
        ver = ""
        try:
            r = await c.get(f"{self.base}/api/v1/pieces/{name}")
            if r.status_code == 200:
                ver = r.json().get("version", "") or ""
        except Exception:  # noqa: BLE001
            ver = ""
        if not ver:
            r = await c.get(f"{self.base}/api/v1/pieces")
            if r.status_code == 200:
                ver = next((p.get("version", "") for p in r.json() if p.get("name") == name), "")
        if not ver:
            raise APError(f"AP piece '{name}' has no resolvable version (installed?)")
        self._piece_cache[name] = ver
        return ver

    async def available(self) -> tuple[bool, str]:
        try:
            async with httpx.AsyncClient(timeout=8) as c:
                r = await c.get(f"{self.base}/api/v1/pieces/{SCHEDULE_PIECE}")
                if r.status_code != 200:
                    return False, f"AP {self.base} → HTTP {r.status_code}"
                await self._auth(c)
            return True, f"AP reachable + authenticated at {self.base} (project {self.project_id})"
        except Exception as e:  # noqa: BLE001
            return False, f"AP unavailable at {self.base}: {e}"

    # ---- flow ops --------------------------------------------------------
    @staticmethod
    def _prop_settings(inp: dict, dynamic=()) -> dict:
        # DROPDOWN props fed a {{template}} (e.g. Discord channel_id) must be marked DYNAMIC, or AP
        # validates the value against the (unfetched) dropdown options and flags the step invalid.
        ps = {k: {"type": "DYNAMIC" if k in dynamic else "MANUAL"} for k in inp}
        if "body" in ps:
            ps["body"] = {"type": "MANUAL", "schema": {
                "data": {"type": "JSON", "required": True, "displayName": "JSON Body"}}}
        return ps

    def _invoke_body(self, agent: str, thread_id: str, prompt: str, deliver: bool,
                     scope: str = "", source: dict | None = None) -> dict:
        """The normalized envelope the AP HTTP step POSTs to /invoke. ``scope`` (the
        principal) rides along so the fired run executes in the right tenant namespace.

        ``source`` overrides the default time/cron source — used for a DIRECT-channel sink, where
        the source is set to {type:channel, name:<ch>, thread_id:gw:<ch>:<native>} so ``/invoke``
        delivers the answer itself via the channel's direct adapter (no AP send step)."""
        return {"agent": agent, "text": prompt, "deliver": deliver, "scope": scope,
                "source": source or {"type": "time", "name": "cron", "thread_id": thread_id},
                "event": {"kind": "tick", "payload": {}}}

    def _trigger_op(self, cron: str | None, interval_seconds: int | None, ver: str) -> dict:
        if cron:
            tname, inp, display = "cron_expression", {"cronExpression": cron, "timezone": "UTC"}, \
                f"cron {cron} UTC"
        elif interval_seconds:
            secs = int(interval_seconds)
            if secs < 60 or secs % 60:
                raise APError("AP schedules are minute-granularity; use a multiple of 60s")
            tname, inp, display = "every_x_minutes", {"minutes": secs // 60}, f"every {secs//60}m"
        else:
            raise APError("schedule needs cron or interval_seconds")
        settings = {"propertySettings": self._prop_settings(inp), "pieceName": SCHEDULE_PIECE,
                    "pieceVersion": ver, "triggerName": tname, "input": inp}
        return {"type": "UPDATE_TRIGGER",
                "request": {"name": "trigger", "valid": True, "displayName": display,
                            "type": "PIECE_TRIGGER", "settings": settings}}

    def _http_action(self, body: dict, ver: str) -> dict:
        headers = {"X-Gateway-Token": self.gateway_token} if self.gateway_token else {}
        inp = {"url": self.invoke_url, "body": {"data": body}, "method": "POST",
               "headers": headers, "timeout": "", "authType": "NONE", "body_type": "json",
               "use_proxy": False, "authFields": {}, "failureMode": "continue_none",
               "queryParams": {}, "proxy_settings": {}, "followRedirects": False,
               "response_is_binary": False}
        settings = {"propertySettings": self._prop_settings(inp), "pieceName": HTTP_PIECE,
                    "pieceVersion": ver, "actionName": "send_request", "input": inp}
        return {"type": "ADD_ACTION",
                "request": {"parentStep": "trigger",
                            "action": {"name": "step_1", "skip": False, "type": "PIECE",
                                       "valid": True, "displayName": "Invoke CUGA",
                                       "settings": settings}}}

    async def _post_op(self, c: httpx.AsyncClient, flow_id: str, op: dict, hdrs: dict) -> dict:
        r = await c.post(f"{self.base}/api/v1/flows/{flow_id}", json=op, headers=hdrs)
        if r.status_code >= 300:
            raise APError(f"AP op {op['type']} failed: HTTP {r.status_code} {r.text[:300]}")
        return r.json() if r.text else {}

    async def find_flow_by_name(self, c: httpx.AsyncClient, hdrs: dict, name: str,
                                project_id: str) -> str | None:
        r = await c.get(f"{self.base}/api/v1/flows",
                        params={"projectId": project_id, "limit": 100}, headers=hdrs)
        if r.status_code != 200:
            return None
        for f in r.json().get("data", []):
            if (f.get("version") or {}).get("displayName") == name:
                return f.get("id")
        return None

    async def ensure_project(self, c: httpx.AsyncClient, hdrs: dict, project_name: str) -> str:
        """Find-or-create the AP project for a principal → its id (per-tenant ISOLATION).

        Each principal's flows + connections live in their own AP project, so tenants can't
        see or touch each other's automations."""
        if self._degraded:                       # plan caps projects → everything shares default
            return self.project_id
        if project_name in self._project_cache:
            return self._project_cache[project_name]
        r = await c.get(f"{self.base}/api/v1/projects", headers=hdrs, params={"limit": 100})
        if r.status_code == 200:
            data = r.json()
            items = data.get("data", data) if isinstance(data, dict) else data
            for p in (items or []):
                if p.get("displayName") == project_name:
                    self._project_cache[project_name] = p["id"]
                    return p["id"]
        rc = await c.post(f"{self.base}/api/v1/projects", headers=hdrs,
                          json={"displayName": project_name})
        if rc.status_code == 402 or "FEATURE_DISABLED" in rc.text:
            # AP plan caps team projects → degrade to the default project with scope-prefixed
            # flow names (still isolated + filterable per tenant at the app layer).
            self._degraded = True
            self._project_cache[project_name] = self.project_id
            log.warning("AP plan limits projects (%s) — using namespace isolation "
                        "(scope-prefixed flows) in the default project for '%s'",
                        rc.status_code, project_name)
            return self.project_id
        if rc.status_code >= 300:
            raise APError(f"AP create project '{project_name}' failed: HTTP {rc.status_code} "
                          f"{rc.text[:200]}")
        pid = rc.json()["id"]
        self._project_cache[project_name] = pid
        log.info("created AP project '%s' → %s (per-tenant project isolation)", project_name, pid)
        return pid

    # ---- public API ------------------------------------------------------
    def _channel_send_op(self, channel: str, target_ref: str, connection: str,
                         sver: str, parent: str = "step_1", name: str = "step_2") -> dict:
        """A channel OUTBOUND send op (piece send-action). ``target_ref`` is where the reply goes —
        a literal native id (schedule/push: fixed at arm time) or a trigger template (inbound)."""
        from . import flows
        d = flows.CHANNELS[channel]
        piece = flows.PIECE[d["piece"]]
        send_inp = {d["target_arg"]: str(target_ref), d["text_arg"]: "{{step_1.body.answer}}",
                    **d.get("const", {})}
        if connection:
            send_inp["auth"] = f"{{{{connections['{connection}']}}}}"
        return self._piece_action_op(parent, name, piece, d["send_action"], send_inp, sver,
                                     f"{channel} · send", dynamic=d.get("dynamic_props", []))

    async def create_schedule_flow(self, *, name: str, agent: str, thread_id: str, prompt: str,
                                   cron: str | None = None, interval_seconds: int | None = None,
                                   deliver: bool = True, replace: bool = True,
                                   project_name: str | None = None, scope: str = "",
                                   deliver_channel: str | None = None,
                                   deliver_target: str | None = None,
                                   deliver_connection: str | None = None,
                                   deliver_direct_channel: str | None = None,
                                   deliver_direct_target: str | None = None) -> str:
        """Create + ENABLE a schedule→/invoke flow. Returns the AP flow id.

        ``project_name`` (a Principal.ap_project_name()) isolates the flow into that principal's
        own AP project; without it, the engine's default project is used."""
        async with httpx.AsyncClient(timeout=30) as c:
            hdrs = await self._auth(c)
            if project_name:                        # grain=tenant/user → dedicated project
                project_id = await self.ensure_project(c, hdrs, project_name)   # (degrades on cap)
            else:                                   # grain=shared → default project + prefixing
                project_id = self.project_id
            if not project_id:
                raise APError("AP project unresolved (no project_name and no default project)")
            # scope-prefix the flow name so tenants are isolated even when they share a project
            # (the fallback when the AP plan caps projects).
            flow_name = f"{scope}::{name}" if scope else name
            if replace:
                existing = await self.find_flow_by_name(c, hdrs, flow_name, project_id)
                if existing:
                    await c.delete(f"{self.base}/api/v1/flows/{existing}", headers=hdrs)
            sched_ver = await self._piece_version(c, SCHEDULE_PIECE)
            http_ver = await self._piece_version(c, HTTP_PIECE)
            r = await c.post(f"{self.base}/api/v1/flows", headers=hdrs,
                             json={"displayName": flow_name, "projectId": project_id})
            if r.status_code >= 300:
                raise APError(f"AP create flow failed: HTTP {r.status_code} {r.text[:300]}")
            flow_id = r.json()["id"]
            # deliver to a CHANNEL → AP owns the outbound: cron → /invoke(deliver=False) → channel
            # send. Without a channel target we keep deliver=True (web/capture-sink delivery).
            # A DIRECT-channel sink (e.g. Slack default) appends NO AP send step — instead the
            # invoke body's source is the channel, so /invoke sends the answer via its direct
            # adapter (deliver=True). This is how a scheduled flow reaches an out-of-AP channel.
            from . import flows
            has_send = bool(deliver_channel in flows.CHANNELS and deliver_target)
            direct_source = None
            if deliver_direct_channel and deliver_direct_target and not has_send:
                direct_source = {"type": "channel", "name": deliver_direct_channel,
                                 "thread_id": f"gw:{deliver_direct_channel}:{deliver_direct_target}"}
            await self._post_op(c, flow_id, self._trigger_op(cron, interval_seconds, sched_ver), hdrs)
            await self._post_op(c, flow_id,
                                self._http_action(self._invoke_body(agent, thread_id, prompt,
                                                                    deliver and not has_send,
                                                                    scope, source=direct_source),
                                                  http_ver), hdrs)
            if has_send:
                piece = flows.PIECE[flows.CHANNELS[deliver_channel]["piece"]]
                sver = await self._piece_version(c, piece)
                await self._post_op(c, flow_id, self._channel_send_op(
                    deliver_channel, deliver_target, deliver_connection or "", sver), hdrs)
            await self._post_op(c, flow_id,
                                {"type": "LOCK_AND_PUBLISH", "request": {"status": "ENABLED"}}, hdrs)
        log.info("created AP schedule flow=%s name=%s (sched=%s http=%s send=%s)",
                 flow_id, name, sched_ver, http_ver,
                 deliver_channel if has_send else (f"direct:{deliver_direct_channel}"
                                                   if direct_source else "none"))
        return flow_id

    # ---- inbound (channel) + push (integration) flows -------------------
    # These arm the CHANNEL/INTEGRATION plane in AP: a piece trigger → HTTP /invoke → send/router.
    # All channel/integration specifics come from flows.CHANNELS / flows.SOURCE_TRIGGER (config),
    # so there is NO channel/integration API in CUGA — AP does the receiving + sending.
    # ⚠️ VERIFY the piece trigger/action op shapes against your AP build on first live arm.
    def _piece_trigger_op(self, piece: str, trigger: str, inp: dict, ver: str, dynamic=()) -> dict:
        settings = {"propertySettings": self._prop_settings(inp, dynamic), "pieceName": piece,
                    "pieceVersion": ver, "triggerName": trigger, "input": inp}
        return {"type": "UPDATE_TRIGGER",
                "request": {"name": "trigger", "valid": True, "displayName": f"{piece} · {trigger}",
                            "type": "PIECE_TRIGGER", "settings": settings}}

    def _piece_action_op(self, parent: str, name: str, piece: str, action: str, inp: dict,
                         ver: str, display: str, dynamic=()) -> dict:
        settings = {"propertySettings": self._prop_settings(inp, dynamic), "pieceName": piece,
                    "pieceVersion": ver, "actionName": action, "input": inp}
        return {"type": "ADD_ACTION",
                "request": {"parentStep": parent,
                            "action": {"name": name, "skip": False, "type": "PIECE", "valid": True,
                                       "displayName": display, "settings": settings}}}

    async def _new_flow(self, c, hdrs, name: str, project_id: str, scope: str) -> str:
        flow_name = f"{scope}::{name}" if scope else name
        existing = await self.find_flow_by_name(c, hdrs, flow_name, project_id)
        if existing:
            await c.delete(f"{self.base}/api/v1/flows/{existing}", headers=hdrs)
        r = await c.post(f"{self.base}/api/v1/flows", headers=hdrs,
                         json={"displayName": flow_name, "projectId": project_id})
        if r.status_code >= 300:
            raise APError(f"AP create flow failed: HTTP {r.status_code} {r.text[:200]}")
        return r.json()["id"]

    async def create_inbound_flow(self, *, channel: str, agent: str = "concierge",
                                  connection: str = "", project_name: str | None = None,
                                  scope: str = "", trigger_input: dict | None = None) -> str:
        """Arm a channel INBOUND flow: <channel>·trigger ▸ /invoke ▸ <channel>·send. Every
        message from the channel reaches the concierge; the reply goes back out via AP.
        ``connection`` = the bot's AP connection externalId (wired as auth on trigger + send).
        ``trigger_input`` supplies channel-specific trigger config declared in ``trigger_args``
        (e.g. Discord's polling trigger needs the ``channel`` id to watch)."""
        from . import flows
        d = flows.CHANNELS.get(channel)
        if not d:
            raise APError(f"unknown channel '{channel}'")
        auth = f"{{{{connections['{connection}']}}}}" if connection else None
        async with httpx.AsyncClient(timeout=30) as c:
            hdrs = await self._auth(c)
            pid = (await self.ensure_project(c, hdrs, project_name)) if project_name else self.project_id
            flow_id = await self._new_flow(c, hdrs, f"inbound-{channel}", pid, scope)
            piece = flows.PIECE[d["piece"]]
            tver = await self._piece_version(c, piece)
            hver = await self._piece_version(c, flows.PIECE["http"])
            # trigger (auth = the bot connection; + any required trigger inputs, e.g. discord channel)
            tinp = {"auth": auth} if auth else {}
            for k in d.get("trigger_args", []):
                if not trigger_input or trigger_input.get(k) in (None, ""):
                    raise APError(f"channel '{channel}' trigger needs '{k}' — pass it when arming")
                tinp[k] = trigger_input[k]
            tinp.update(d.get("trigger_const", {}))     # fixed trigger inputs (e.g. slack ignoreBots)
            await self._post_op(c, flow_id, self._piece_trigger_op(
                piece, d["trigger"], tinp, tver, dynamic=d.get("dynamic_props", [])), hdrs)
            # HTTP → /invoke (channel envelope: text + native id ride in the body/thread_id).
            # source.user = the message AUTHOR (a trigger template, e.g. discord {{trigger.author.id}})
            # → per-user identity through a shared bot. Absent ⇒ falls back to the thread-native id.
            src = {"type": "channel", "name": channel,
                   "thread_id": f"gw:{channel}:{d['native_ref']}"}
            if d.get("user_ref"):
                src["user"] = d["user_ref"]
            body = {"agent": agent, "text": d["text_ref"], "deliver": False, "scope": scope,
                    "source": src, "event": {"kind": "message", "payload": {}}}
            await self._post_op(c, flow_id, self._http_action(body, hver), hdrs)
            # send the answer back to the channel (auth = the bot connection). native_ref is a
            # trigger template here (reply to the SAME chat the message came from).
            sver = await self._piece_version(c, piece)
            send_inp = {d["target_arg"]: d["native_ref"], d["text_arg"]: "{{step_1.body.answer}}",
                        **d.get("const", {})}
            if auth:
                send_inp["auth"] = auth
            await self._post_op(c, flow_id, self._piece_action_op(
                "step_1", "step_2", piece, d["send_action"], send_inp, sver,
                f"{channel} · send", dynamic=d.get("dynamic_props", [])), hdrs)
            await self._post_op(c, flow_id,
                                {"type": "LOCK_AND_PUBLISH", "request": {"status": "ENABLED"}}, hdrs)
        log.info("armed inbound flow channel=%s agent=%s flow=%s", channel, agent, flow_id)
        return flow_id

    async def create_push_flow(self, *, source: str, event: str, agent: str, thread_id: str,
                               prompt: str, project_name: str | None = None, scope: str = "",
                               connection: str = "", source_input: dict | None = None,
                               name: str | None = None) -> str:
        """Arm an integration PUSH flow: <source>·<event> ▸ /invoke(agent). Powers the Box resume
        watcher (source='box', event='new_file'). ``connection`` = the integration's AP connection
        externalId (wired as auth on the trigger — REQUIRED for the flow to publish). ``source_input``
        supplies trigger-specific inputs (e.g. github needs a ``repository``, box a ``folder``)."""
        from . import flows
        piece_key, trig = flows.SOURCE_TRIGGER.get(source, (source, event))
        piece = flows.PIECE.get(piece_key, f"@activepieces/piece-{piece_key}")
        async with httpx.AsyncClient(timeout=30) as c:
            hdrs = await self._auth(c)
            pid = (await self.ensure_project(c, hdrs, project_name)) if project_name else self.project_id
            flow_id = await self._new_flow(c, hdrs, name or f"push-{source}-{agent}", pid, scope)
            tver = await self._piece_version(c, piece)
            hver = await self._piece_version(c, flows.PIECE["http"])
            tinp = dict(source_input or {})
            if connection:                       # wire the integration's connection as trigger auth
                tinp["auth"] = f"{{{{connections['{connection}']}}}}"
            await self._post_op(c, flow_id, self._piece_trigger_op(piece, trig, tinp, tver), hdrs)
            # Pass the trigger's meaningful fields into the /invoke payload so the worker has real
            # content to reason over (not just a filename). Field paths are the AP piece's trigger
            # output shape; unknown paths render empty (harmless). See flows.PUSH_PAYLOAD.
            # ROBUSTNESS (Tier 0): ALWAYS also forward the FULL raw trigger output as `_raw`. If the
            # curated per-piece paths are wrong or missing (a new/unmapped piece), the worker still
            # gets the whole payload — envelope.worker_input falls back to `_raw` when the curated
            # fields come back empty. Retires the "flow ran green but the agent got nothing" class.
            payload = {**flows.PUSH_PAYLOAD.get(source, {}), "_raw": "{{trigger}}"}
            body = {"agent": agent, "text": prompt, "deliver": True, "scope": scope,
                    "source": {"type": "integration", "name": source, "thread_id": thread_id},
                    "event": {"kind": event, "payload": payload}}
            await self._post_op(c, flow_id, self._http_action(body, hver), hdrs)
            await self._post_op(c, flow_id,
                                {"type": "LOCK_AND_PUBLISH", "request": {"status": "ENABLED"}}, hdrs)
        log.info("armed push flow source=%s/%s agent=%s flow=%s", source, event, agent, flow_id)
        return flow_id

    # ---- connections (credentials) --------------------------------------
    async def _connections(self, c: httpx.AsyncClient, hdrs: dict, project_id: str) -> list:
        r = await c.get(f"{self.base}/api/v1/app-connections", headers=hdrs,
                        params={"projectId": project_id, "limit": 100})
        return r.json().get("data", []) if r.status_code == 200 else []

    async def connection_exists(self, external_id: str, project_name: str | None = None) -> bool:
        """True if an AP connection with this externalId exists (per-user connect check)."""
        async with httpx.AsyncClient(timeout=15) as c:
            hdrs = await self._auth(c)
            pid = (await self.ensure_project(c, hdrs, project_name)) if project_name else self.project_id
            return any(x.get("externalId") == external_id for x in await self._connections(c, hdrs, pid))

    async def ensure_secret_connection(self, external_id: str, piece: str, token: str,
                                       project_name: str | None = None) -> str:
        """Create (idempotently) a SECRET_TEXT connection holding ``token`` — the token/PAT path
        (Telegram/GitHub). OAuth apps (Gmail/Box) are authorized in AP's connect UI instead."""
        async with httpx.AsyncClient(timeout=20) as c:
            hdrs = await self._auth(c)
            pid = (await self.ensure_project(c, hdrs, project_name)) if project_name else self.project_id
            if any(x.get("externalId") == external_id for x in await self._connections(c, hdrs, pid)):
                return external_id
            r = await c.post(f"{self.base}/api/v1/app-connections", headers=hdrs, json={
                "externalId": external_id, "displayName": external_id, "pieceName": piece,
                "projectId": pid, "type": "SECRET_TEXT",
                "value": {"type": "SECRET_TEXT", "secret_text": token}})
            if r.status_code >= 300:
                raise APError(f"AP create connection '{external_id}' failed: "
                              f"HTTP {r.status_code} {r.text[:200]}")
            return external_id

    async def ensure_oauth_connection(self, external_id: str, piece: str, code: str,
                                      *, client_id: str, client_secret: str, redirect_url: str,
                                      scope: str = "", authorization_method: str = "BODY",
                                      project_name: str | None = None) -> str:
        """Create an AP **OAUTH2** connection by handing AP the authorization ``code`` — AP does the
        token exchange itself and owns the refresh lifecycle.

        IMPORTANT (AP 0.82): the ``UpsertOAuth2Request`` schema REQUIRES ``code`` (+ client_id,
        client_secret, redirect_url, all min-1). AP does NOT accept a pre-obtained access token —
        so a "dev token"/"bot token" cannot be turned into an OAuth2 connection; the OAuth consent
        flow (oauth.py authorize → callback with a code) is the only path. Token-auth pieces
        (telegram/discord/github) use ``ensure_secret_connection`` instead.
        """
        async with httpx.AsyncClient(timeout=20) as c:
            hdrs = await self._auth(c)
            pid = (await self.ensure_project(c, hdrs, project_name)) if project_name else self.project_id
            if any(x.get("externalId") == external_id for x in await self._connections(c, hdrs, pid)):
                return external_id
            value = {"type": "OAUTH2", "client_id": client_id, "client_secret": client_secret,
                     "code": code, "redirect_url": redirect_url, "scope": scope,
                     "grant_type": "authorization_code", "authorization_method": authorization_method,
                     "props": {}}
            r = await c.post(f"{self.base}/api/v1/app-connections", headers=hdrs, json={
                "externalId": external_id, "displayName": external_id, "pieceName": piece,
                "projectId": pid, "type": "OAUTH2", "value": value})
            if r.status_code >= 300:
                raise APError(f"AP create OAUTH2 connection '{external_id}' failed: "
                              f"HTTP {r.status_code} {r.text[:200]}")
            return external_id

    async def list_connections(self, project_name: str | None = None) -> list[dict]:
        async with httpx.AsyncClient(timeout=15) as c:
            hdrs = await self._auth(c)
            pid = (await self.ensure_project(c, hdrs, project_name)) if project_name else self.project_id
            return [{"id": x.get("id"), "externalId": x.get("externalId")}
                    for x in await self._connections(c, hdrs, pid)]

    async def delete_connection(self, external_id: str, project_name: str | None = None) -> None:
        async with httpx.AsyncClient(timeout=15) as c:
            hdrs = await self._auth(c)
            pid = (await self.ensure_project(c, hdrs, project_name)) if project_name else self.project_id
            for x in await self._connections(c, hdrs, pid):
                if x.get("externalId") == external_id:
                    await c.delete(f"{self.base}/api/v1/app-connections/{x['id']}", headers=hdrs)

    async def delete_flow(self, flow_id: str) -> None:
        try:
            async with httpx.AsyncClient(timeout=15) as c:
                hdrs = await self._auth(c)
                await c.delete(f"{self.base}/api/v1/flows/{flow_id}", headers=hdrs)
        except Exception as e:  # noqa: BLE001
            log.warning("AP delete flow %s failed: %s", flow_id, e)

    async def set_flow_status(self, flow_id: str, enabled: bool) -> None:
        """Pause/resume a published flow via AP's CHANGE_STATUS op (ENABLED/DISABLED). Best-effort —
        logs on failure so the caller can still update its own record."""
        try:
            async with httpx.AsyncClient(timeout=15) as c:
                hdrs = await self._auth(c)
                await self._post_op(c, flow_id, {"type": "CHANGE_STATUS",
                    "request": {"status": "ENABLED" if enabled else "DISABLED"}}, hdrs)
        except Exception as e:  # noqa: BLE001
            log.warning("AP set flow %s status=%s failed: %s", flow_id, enabled, e)

    async def get_flow(self, flow_id: str) -> dict | None:
        """Fetch the full AP flow JSON (trigger + steps) for a rich, read-only view in the CUGA UI.
        Returns None if AP is unreachable or the flow is gone."""
        try:
            async with httpx.AsyncClient(timeout=15) as c:
                hdrs = await self._auth(c)
                r = await c.get(f"{self.base}/api/v1/flows/{flow_id}", headers=hdrs)
                return r.json() if r.status_code == 200 and r.text else None
        except Exception as e:  # noqa: BLE001
            log.warning("AP get flow %s failed: %s", flow_id, e)
            return None

    async def list_runs(self, limit: int = 60) -> list[dict]:
        """The execution log: recent AP flow-runs for the project. Each row carries status
        (SUCCEEDED/FAILED/RUNNING), flowId, displayName, and start/finish times. [] on failure."""
        try:
            async with httpx.AsyncClient(timeout=15) as c:
                hdrs = await self._auth(c)
                r = await c.get(f"{self.base}/api/v1/flow-runs", headers=hdrs,
                                params={"projectId": self.project_id, "limit": limit})
                if r.status_code != 200 or not r.text:
                    return []
                data = r.json()
                return data.get("data", data) if isinstance(data, dict) else (data or [])
        except Exception as e:  # noqa: BLE001
            log.warning("AP list runs failed: %s", e)
            return []

    async def get_run(self, run_id: str) -> dict | None:
        """One flow-run's detail incl. per-step outputs — the trigger payload and the CUGA
        ``/invoke`` response that carries the agent's answer. None on failure."""
        try:
            async with httpx.AsyncClient(timeout=15) as c:
                hdrs = await self._auth(c)
                r = await c.get(f"{self.base}/api/v1/flow-runs/{run_id}", headers=hdrs)
                return r.json() if r.status_code == 200 and r.text else None
        except Exception as e:  # noqa: BLE001
            log.warning("AP get run %s failed: %s", run_id, e)
            return None
