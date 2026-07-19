"""APEngine — creates real Activepieces flows over REST (Phase 2: CRON/POLL).

Ported/trimmed from event-agent-ap ``automation/activepieces.py``. For a standing trigger we
build an AP flow:  [ Schedule trigger ]  →  [ HTTP → POST {invoke_url} ]. The HTTP step calls
back into the events ``/invoke`` receiver with the normalized envelope; the receiver runs the
worker and (deliver=true) delivers. Piece versions + auth are read live from AP.

Auth: AP_EMAIL/AP_PASSWORD (community edition → JWT) or AP_API_KEY (cloud). Config via env
(AP_BASE_URL, HOST_CALLBACK_URL, GATEWAY_TOKEN). PUSH/branching flows are Phase 3.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time

import httpx

log = logging.getLogger("cuga.events.ap")

SCHEDULE_PIECE = "@activepieces/piece-schedule"
HTTP_PIECE = "@activepieces/piece-http"


class APError(RuntimeError):
    pass


class APEngine:
    kind = "activepieces"
    # AP's JWT lives much longer than this; a short TTL keeps us resilient to AP restarts while
    # still collapsing a burst of operations onto ONE sign-in.
    TOKEN_TTL_SECS = 10 * 60

    def __init__(self) -> None:
        try:
            from .secret_seam import secret as _secret
        except ImportError:  # flat load (tests put the events dir on sys.path)
            from secret_seam import secret as _secret
        self.base = os.environ["AP_BASE_URL"].rstrip("/")
        # secrets go through the events secret seam: the .env value may be plaintext (dev) or a
        # vault://…-style reference (prod). AP_PASSWORD is the one GAPS.md says to vault — it
        # guards an internet-tunneled AP admin console.
        self.api_key = _secret("AP_API_KEY")
        self.email = os.environ.get("AP_EMAIL", "")
        self.password = _secret("AP_PASSWORD")
        self.project_id = os.environ.get("AP_PROJECT_ID", "")
        self.invoke_url = (os.environ.get("HOST_CALLBACK_URL")
                           or os.environ.get("EA_INVOKE_URL")
                           or "http://host.docker.internal:8000/invoke")
        self.gateway_token = os.environ.get("GATEWAY_TOKEN", "")
        self._token = ""
        self._token_exp = 0.0                       # cached-JWT expiry (see _auth)
        self._auth_lock = asyncio.Lock()
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

    async def _auth(self, c: httpx.AsyncClient, *, force: bool = False) -> dict:
        """AP auth headers, with the JWT CACHED and refreshed under a lock.

        This used to sign in on EVERY operation. Arming one flow is a dozen AP calls, so a burst
        (e.g. 14 trigger arms) became well over a hundred sign-ins — AP slowed/refused, the
        connection check then threw, and the concierge's gate reported "connect your credentials"
        for an account that WAS connected. Cache + refresh removes the storm at the source; the
        lock stops a thundering herd of concurrent refreshes.
        """
        if self.api_key:
            return {"Authorization": f"Bearer {self.api_key}"}
        if not (self.email and self.password):
            raise APError("AP needs AP_API_KEY or AP_EMAIL+AP_PASSWORD")
        now = time.time()
        if not force and self._token and now < self._token_exp:
            return {"Authorization": f"Bearer {self._token}"}
        async with self._auth_lock:
            # another coroutine may have refreshed while we waited for the lock
            if not force and self._token and time.time() < self._token_exp:
                return {"Authorization": f"Bearer {self._token}"}
            await self._sign_in(c)
            self._token_exp = time.time() + self.TOKEN_TTL_SECS
        return {"Authorization": f"Bearer {self._token}"}

    async def _auth_retry(self, c: httpx.AsyncClient, resp) -> dict | None:
        """A cached token can be invalidated by an AP restart. On a 401/403, sign in once more and
        let the caller retry — otherwise a stale cache would look like a broken credential."""
        if resp is not None and resp.status_code in (401, 403):
            return await self._auth(c, force=True)
        return None

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

    def _http_action(self, body: dict, ver: str, url: str | None = None,
                     display: str = "Invoke CUGA") -> dict:
        headers = {"X-Gateway-Token": self.gateway_token} if self.gateway_token else {}
        inp = {"url": url or self.invoke_url, "body": {"data": body}, "method": "POST",
               "headers": headers, "timeout": "", "authType": "NONE", "body_type": "json",
               "use_proxy": False, "authFields": {}, "failureMode": "continue_none",
               "queryParams": {}, "proxy_settings": {}, "followRedirects": False,
               "response_is_binary": False}
        settings = {"propertySettings": self._prop_settings(inp), "pieceName": HTTP_PIECE,
                    "pieceVersion": ver, "actionName": "send_request", "input": inp}
        return {"type": "ADD_ACTION",
                "request": {"parentStep": "trigger",
                            "action": {"name": "step_1", "skip": False, "type": "PIECE",
                                       "valid": True, "displayName": display,
                                       "settings": settings}}}

    def _action_op(self, *, piece: str, ap_action: str, inp: dict, ver: str, parent: str,
                   name: str, connection: str = "", display: str = "") -> dict:
        """A post-agent ACTION step (design §3.2), added after the /invoke step. ``connection`` wires
        the acting app's AP connection as ``auth`` (a gmail send needs the user's gmail connection,
        exactly like a trigger). ``inp`` is the resolved native-template input from
        ``actions.render_params``."""
        inp = dict(inp)
        if connection:
            inp["auth"] = f"{{{{connections['{connection}']}}}}"
        # PLAIN prop settings — NOT _prop_settings, which forces a `body` field to a JSON schema
        # (correct for the HTTP piece, wrong for a gmail action whose `body` is text). custom_api_call
        # needs its `body` as JSON, so honor an explicit body_type=json.
        ps = {k: {"type": "MANUAL"} for k in inp}
        if ap_action == "custom_api_call" and inp.get("body_type") == "json" and "body" in ps:
            ps["body"] = {"type": "MANUAL", "schema": {
                "data": {"type": "JSON", "required": True, "displayName": "JSON Body"}}}
        settings = {"propertySettings": ps, "pieceName": piece,
                    "pieceVersion": ver, "actionName": ap_action, "input": inp}
        return {"type": "ADD_ACTION",
                "request": {"parentStep": parent,
                            "action": {"name": name, "skip": False, "type": "PIECE", "valid": True,
                                       "displayName": display or ap_action, "settings": settings}}}

    async def create_box_poll_flow(self, *, name: str, agent: str, folder_id: str,
                                   deliver_to: str | None, deliver_target: str | None = None,
                                   interval_seconds: int, scope: str = "",
                                   project_name: str | None = None) -> str:
        """A STANDING DIRECT-Box watcher — AP-free source, no Box OAuth. AP's schedule piece is just
        the clock: schedule → HTTP POST /api/events/box/poll, which polls Box with the dev token and
        fires <agent> on each new file (the server tracks the per-folder watermark). Returns flow id."""
        box_url = self.invoke_url.replace("/invoke", "/api/events/box/poll")
        body = {"folder_id": str(folder_id), "agent": agent, "deliver_to": deliver_to,
                "deliver_target": deliver_target, "scope": scope}
        async with httpx.AsyncClient(timeout=30) as c:
            hdrs = await self._auth(c)
            pid = (await self.ensure_project(c, hdrs, project_name)) if project_name else self.project_id
            if not pid:
                raise APError("AP project unresolved for box-poll flow")
            flow_id = await self._new_flow(c, hdrs, name, pid, scope)
            sched_ver = await self._piece_version(c, SCHEDULE_PIECE)
            http_ver = await self._piece_version(c, HTTP_PIECE)
            await self._post_op(c, flow_id,
                                self._trigger_op(None, interval_seconds, sched_ver), hdrs)
            await self._post_op(c, flow_id,
                                self._http_action(body, http_ver, url=box_url, display="Poll Box"), hdrs)
            await self._post_op(c, flow_id,
                                {"type": "LOCK_AND_PUBLISH", "request": {"status": "ENABLED"}}, hdrs)
        log.info("created AP box-poll flow=%s name=%s folder=%s every=%ss", flow_id, name,
                 folder_id, interval_seconds)
        return flow_id

    async def _assert_steps_valid(self, c: httpx.AsyncClient, flow_id: str, hdrs: dict) -> None:
        """Fetch the flow and RAISE if any step is invalid — deleting the throwaway flow first. This
        is the arm-time validity gate: AP marks a step ``valid: false`` when its input doesn't satisfy
        the piece (a missing prop, a bad dropdown value). Such a flow publishes but never fires, so we
        must never report it as ARMED. Reusable — the generator's --check mode calls the same idea."""
        d = (await c.get(f"{self.base}/api/v1/flows/{flow_id}", headers=hdrs)).json()
        bad, s = [], (d.get("version") or {}).get("trigger")
        while s:
            if s.get("valid") is False:
                st = s.get("settings", {})
                bad.append(st.get("actionName") or st.get("triggerName") or s.get("name"))
            s = s.get("nextAction")
        if bad:
            try:
                await c.delete(f"{self.base}/api/v1/flows/{flow_id}", headers=hdrs)
            except Exception:  # noqa: BLE001
                pass
            raise APError("Activepieces marked these step(s) invalid — the flow would never fire: "
                          + ", ".join(bad) + ". Not arming.")

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
                                   deliver_direct_target: str | None = None,
                                   subscription_id: str | None = None,
                                   expires_at: float | None = None) -> str:
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
            body = self._invoke_body(agent, thread_id, prompt, deliver and not has_send,
                                     scope, source=direct_source)
            if subscription_id:
                body["subscription_id"] = subscription_id
            if expires_at:
                # bounded run: /invoke's expiry gate deletes the flow on its first tick past this
                body["expires_at"] = expires_at
            await self._post_op(c, flow_id, self._http_action(body, http_ver), hdrs)
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
                               name: str | None = None, actions: list | None = None,
                               deliver_channel: str | None = None, deliver_target: str | None = None,
                               deliver_connection: str | None = None) -> str:
        """Arm an integration PUSH flow: <source>·<event> ▸ /invoke(agent) ▸ [actions…]. Powers the
        Box resume watcher and now the post-agent ACTION path. ``connection`` = the trigger app's AP
        connection externalId (wired as auth on the trigger — REQUIRED to publish). ``source_input``
        supplies trigger-specific inputs (github ``repository``, box a ``folder``).

        ``actions`` is a SEQUENTIAL list of resolved action steps to run after the agent answers:
        each ``{app, ap_action, params, connection, display}`` (build with ``actions.render_params``).
        When actions are present the /invoke step does NOT deliver (deliver=False) — the action step
        IS the delivery. Branched action flows are built offline (flows.build_push_flow) and armed
        via the same op mechanism in a follow-up; the concierge only passes sequential actions here."""
        from . import flows
        # Registry-resolved: (source, event) selects the SPECIFIC piece trigger. The old lookup
        # keyed on source alone and IGNORED `event` for known apps — gmail/new_attachment armed the
        # new-email trigger, and a second trigger on the same app could never exist.
        row = flows.resolve_trigger(source, event)
        if row is not None and row.backend == "direct":
            raise ValueError(f"{source}/{row.event} is a DIRECT trigger (CUGA receives it) — "
                             "it is not armable as an Activepieces flow")
        if row is not None:
            piece_key, trig = row.piece, row.ap_trigger
        else:                                    # legacy channel/webhook/rss rows
            piece_key, trig = flows._LEGACY_SOURCE_TRIGGER[source]
        piece = flows.PIECE.get(piece_key, f"@activepieces/piece-{piece_key}")
        # The default flow name must include the EVENT: _new_flow deletes any existing flow with
        # the same name, so the old `push-{source}-{agent}` naming meant arming a SECOND trigger on
        # the same app silently destroyed the first one's flow.
        default_name = f"push-{source}-{(event or 'default').replace('_', '-')}-{agent}"
        async with httpx.AsyncClient(timeout=30) as c:
            hdrs = await self._auth(c)
            pid = (await self.ensure_project(c, hdrs, project_name)) if project_name else self.project_id
            flow_id = await self._new_flow(c, hdrs, name or default_name, pid, scope)
            tver = await self._piece_version(c, piece)
            hver = await self._piece_version(c, flows.PIECE["http"])
            tinp = dict(source_input or {})
            if connection:                       # wire the integration's connection as trigger auth
                tinp["auth"] = f"{{{{connections['{connection}']}}}}"
            await self._post_op(c, flow_id, self._piece_trigger_op(piece, trig, tinp, tver), hdrs)
            # Pass the trigger's meaningful fields into the /invoke payload so the worker has real
            # content to reason over (not just a filename). Field paths follow the piece's OWN
            # sample output, per (source, event) from the registry (see triggers.py).
            # ROBUSTNESS (Tier 0): ALWAYS also forward the FULL raw trigger output as `_raw`. If the
            # curated per-piece paths are wrong or missing (a new/unmapped piece), the worker still
            # gets the whole payload — envelope.worker_input falls back to `_raw` when the curated
            # fields come back empty. Retires the "flow ran green but the agent got nothing" class.
            payload = {**flows.push_payload(source, event), "_raw": "{{trigger}}"}
            # RETURN-TO-CALLER — report the agent's answer back to the ORIGIN channel (where the flow
            # was armed), alongside the action running in the acting app.
            #   • DIRECT channels (Slack/Discord): deliver=True → /invoke sends via the direct adapter
            #     to the gw:<channel>:<native> origin in thread_id. No AP send step.
            #   • AP-backed channels (Telegram): deliver=False + an AP send step appended AFTER the
            #     action steps (AP owns the send). Set via deliver_channel/deliver_target.
            # gmail ≠ the chat channel, so action + report is never a double-send. Armed from web →
            # neither fires (thread_id not gw:*, no deliver_channel).
            has_send = bool(deliver_channel in flows.CHANNELS and deliver_target)
            body = {"agent": agent, "text": prompt, "deliver": not has_send, "scope": scope,
                    "source": {"type": "integration", "name": source, "thread_id": thread_id},
                    "event": {"kind": event, "payload": payload}}
            await self._post_op(c, flow_id, self._http_action(body, hver), hdrs)
            # post-agent ACTION steps — sequential chain after step_1 (the /invoke)
            parent, idx = "step_1", 2
            for act in (actions or []):
                a_piece = flows.PIECE.get(act["app"], f"@activepieces/piece-{act['app']}")
                a_ver = await self._piece_version(c, a_piece)
                await self._post_op(c, flow_id, self._action_op(
                    piece=a_piece, ap_action=act["ap_action"], inp=act.get("params", {}),
                    ver=a_ver, parent=parent, name=f"step_{idx}",
                    connection=act.get("connection", ""), display=act.get("display", "")), hdrs)
                parent, idx = f"step_{idx}", idx + 1
            if has_send:                              # AP-channel report, AFTER the action(s)
                piece = flows.PIECE[flows.CHANNELS[deliver_channel]["piece"]]
                sver = await self._piece_version(c, piece)
                await self._post_op(c, flow_id, self._channel_send_op(
                    deliver_channel, deliver_target, deliver_connection or "", sver,
                    parent=parent, name=f"step_{idx}"), hdrs)
            # ANTI-SILENT-FAILURE GATE: before publishing, ask AP whether every step is valid. An
            # invalid ACTION step publishes ENABLED and then NEVER fires — the worst kind of silent
            # failure (the user is told "ARMED" for a flow that can't run). Refuse: delete + raise so
            # the concierge reports the real problem instead of a false success.
            await self._assert_steps_valid(c, flow_id, hdrs)
            await self._post_op(c, flow_id,
                                {"type": "LOCK_AND_PUBLISH", "request": {"status": "ENABLED"}}, hdrs)
        log.info("armed push flow source=%s/%s agent=%s flow=%s", source, event, agent, flow_id)
        return flow_id

    # ---- connections (credentials) --------------------------------------
    async def _connections(self, c: httpx.AsyncClient, hdrs: dict, project_id: str) -> list:
        """The project's AP connections. RAISES on a non-200 instead of returning [].

        Returning an empty list on an error made "AP is unhappy" indistinguishable from "this user
        has connected nothing" — which is precisely how a working credential got reported as
        CONNECT NEEDED. A 401/403 (a cached JWT invalidated by an AP restart) is retried once with
        a fresh sign-in before giving up."""
        r = await c.get(f"{self.base}/api/v1/app-connections", headers=hdrs,
                        params={"projectId": project_id, "limit": 100})
        if r.status_code in (401, 403):
            fresh = await self._auth(c, force=True)
            r = await c.get(f"{self.base}/api/v1/app-connections", headers=fresh,
                            params={"projectId": project_id, "limit": 100})
        if r.status_code != 200:
            raise APError(f"AP list-connections failed: HTTP {r.status_code} {r.text[:160]}")
        return r.json().get("data", [])

    async def connection_exists(self, external_id: str, project_name: str | None = None) -> bool:
        """True if an AP connection with this externalId exists (per-user connect check)."""
        async with httpx.AsyncClient(timeout=15) as c:
            hdrs = await self._auth(c)
            pid = (await self.ensure_project(c, hdrs, project_name)) if project_name else self.project_id
            return any(x.get("externalId") == external_id for x in await self._connections(c, hdrs, pid))

    async def ensure_secret_connection(self, external_id: str, piece: str, token: str,
                                       project_name: str | None = None,
                                       update: bool = True) -> str:
        """Create **or update** a SECRET_TEXT connection holding ``token`` — the token/PAT path
        (Telegram/GitHub/Discord). OAuth apps (Gmail/Box) are authorized in AP's connect UI instead.

        This used to return early when a connection with ``external_id`` already existed, which meant
        a rotated credential silently did nothing: you pasted a fresh PAT, got ``{"ok": true}``, and
        Activepieces kept using the dead one. The symptom was a flow that armed cleanly and then
        failed at run time with ``401 Bad credentials`` — nowhere near the paste that caused it.

        AP's ``POST /api/v1/app-connections`` upserts on ``externalId``. We re-POST rather than trust
        that, and if AP rejects the overwrite (older builds 409 on a duplicate) we delete the stale
        connection and create it again, which is what a human would do in the console.

        Pass ``update=False`` for the boot-time auto-connect path, where an existing connection the
        user authorized by hand should NOT be clobbered by whatever is in ``.env``.
        """
        async with httpx.AsyncClient(timeout=20) as c:
            hdrs = await self._auth(c)
            pid = (await self.ensure_project(c, hdrs, project_name)) if project_name else self.project_id
            existing = [x for x in await self._connections(c, hdrs, pid)
                        if x.get("externalId") == external_id]
            if existing and not update:
                return external_id

            body = {"externalId": external_id, "displayName": external_id, "pieceName": piece,
                    "projectId": pid, "type": "SECRET_TEXT",
                    "value": {"type": "SECRET_TEXT", "secret_text": token}}
            r = await c.post(f"{self.base}/api/v1/app-connections", headers=hdrs, json=body)
            if r.status_code < 300:
                if existing:
                    log.info("AP connection %s updated (token rotated)", external_id)
                return external_id

            # Overwrite refused. Only worth the destructive fallback when one already exists —
            # otherwise this is a genuine create failure and the caller should see it.
            if not existing:
                raise APError(f"AP create connection '{external_id}' failed: "
                              f"HTTP {r.status_code} {r.text[:200]}")
            log.warning("AP refused to overwrite %s (HTTP %s); deleting and recreating",
                        external_id, r.status_code)
            await c.delete(f"{self.base}/api/v1/app-connections/{existing[0]['id']}", headers=hdrs)
            r2 = await c.post(f"{self.base}/api/v1/app-connections", headers=hdrs, json=body)
            if r2.status_code >= 300:
                raise APError(f"AP update connection '{external_id}' failed after delete: "
                              f"HTTP {r2.status_code} {r2.text[:200]}")
            log.info("AP connection %s recreated (token rotated)", external_id)
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
            # Do NOT return early on an existing connection. Consent means "use THIS authorization",
            # so a re-consent must replace what is stored — including replacing a wrong-TYPE row (a
            # SECRET_TEXT PAT left behind by an older build), which the piece cannot use and which
            # would otherwise make the whole OAuth round trip a silent no-op.
            existing = [x for x in await self._connections(c, hdrs, pid)
                        if x.get("externalId") == external_id]
            value = {"type": "OAUTH2", "client_id": client_id, "client_secret": client_secret,
                     "code": code, "redirect_url": redirect_url, "scope": scope,
                     "grant_type": "authorization_code", "authorization_method": authorization_method,
                     "props": {}}
            body = {"externalId": external_id, "displayName": external_id, "pieceName": piece,
                    "projectId": pid, "type": "OAUTH2", "value": value}
            r = await c.post(f"{self.base}/api/v1/app-connections", headers=hdrs, json=body)
            if r.status_code < 300:
                return external_id
            if not existing:
                raise APError(f"AP create OAUTH2 connection '{external_id}' failed: "
                              f"HTTP {r.status_code} {r.text[:200]}")
            # An authorization `code` is single-use, so we cannot retry the exchange after a failed
            # overwrite — but AP consumed nothing on a rejected upsert. Drop the stale row and retry.
            log.warning("AP refused to overwrite %s (HTTP %s); deleting and recreating",
                        external_id, r.status_code)
            await c.delete(f"{self.base}/api/v1/app-connections/{existing[0]['id']}", headers=hdrs)
            r2 = await c.post(f"{self.base}/api/v1/app-connections", headers=hdrs, json=body)
            if r2.status_code >= 300:
                raise APError(f"AP update OAUTH2 connection '{external_id}' failed after delete: "
                              f"HTTP {r2.status_code} {r2.text[:200]}")
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

    async def trigger_flow(self, flow_id: str, payload: dict | None = None,
                           headers: dict | None = None) -> tuple[bool, str]:
        """Fire an ENABLED flow immediately, out of band of its own trigger. **Debug use only.**

        Activepieces exposes ``POST /api/v1/webhooks/<flowId>`` for *every* flow, whatever its
        trigger — a schedule flow fires from it just as a webhook flow would. The run executes with
        the flow's own AP connections, so no credential is passed here and none is needed: AP resolves
        them internally, exactly as it does on a real tick.

        Two things this canNOT do, both because a standing flow's ``/invoke`` body is frozen at arm
        time (see ``_invoke_body``):
          * ``payload`` reaches the trigger's output but nothing downstream reads it, so you cannot
            parameterise the run;
          * the answer does not come back here. Read it from the run log.

        NB this endpoint takes **no authentication** on AP's side. Anyone who can reach AP and knows a
        flow id can fire it. Keep AP off the public tunnel, and put the auth on CUGA's endpoint.
        """
        try:
            async with httpx.AsyncClient(timeout=30) as c:
                # `headers` lets the caller reproduce the PROVIDER's delivery headers (e.g. GitHub's
                # X-GitHub-Event). One repo webhook carries many event types, so a piece trigger
                # disambiguates on that header — omit it and the piece emits nothing at all.
                r = await c.post(f"{self.base}/api/v1/webhooks/{flow_id}", json=payload or {},
                                 headers=headers or None)
            ok = r.status_code in (200, 204)
            return ok, (f"HTTP {r.status_code}" + (f" {r.text[:120]}" if r.text else ""))
        except Exception as e:  # noqa: BLE001
            log.warning("AP trigger flow %s failed: %s", flow_id, e)
            return False, str(e)
