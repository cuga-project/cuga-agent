# 2026-07-09 — Credentials, the connect-gate bug, branching status, setup docs

Session handoff. Companion to [DECISIONS_2026-07-08.md](DECISIONS_2026-07-08.md). Everything below is
grounded in `src/cuga/backend/events/`.

> **State when this was written:** podman machine stopped, so AP (:8081) and the events server
> (:8100) were both down. Nothing here was verified against a running system — it is code reading
> plus persisted state (`events.db`). Re-verify §2 once the stack is up.

---

## 1. How credentials flow CUGA → Activepieces

**The agent never sees a credential — the design holds.** AP flow steps reference connections as
`{{connections['<externalId>']}}` (`ap_engine.py:251-253`), resolved inside AP's own worker at
execution time. The `/invoke` envelope carries only text + payload (`ap_engine.py:115-125`).

**Tokens live in AP's `app-connections` store**, encrypted at rest with `AP_ENCRYPTION_KEY` in AP's
Postgres. CUGA relays and forgets. For OAuth apps (Gmail/Box) CUGA passes only the authorization
*code*; AP does the exchange and refresh (`ap_engine.py:476-504`). For token apps (GitHub PAT,
Telegram/Discord bot) it creates a `SECRET_TEXT` connection (`ap_engine.py:458-474`).

### The exposure — three compounding facts

1. CUGA authenticates to AP with an **admin email + password in plaintext `.env`**, re-signing on
   every operation (`ap_engine.py:53-72`). That password is the master key to every connection AP
   holds.
2. **The CUGA↔AP hop is plaintext HTTP** (`AP_BASE_URL=http://localhost:8081`). The sign-in password
   and every raw PAT cross it unencrypted (`ap_engine.py:55`, `:467`). Loopback, so not on the public
   wire — but not TLS.
3. **AP is exposed publicly.** `scripts/ap_up.sh:46-58` starts a cloudflared tunnel and sets
   `AP_FRONTEND_URL` to a public `trycloudflare.com` URL, needed so Telegram's webhook can reach it.
   The only thing in front of AP's UI and API is that admin password.

Blast radius of `AP_PASSWORD` = every credential in the system, reachable from the internet.

### A vault already exists — and the events layer ignores it

`src/cuga/backend/secrets/` has HashiCorp Vault (incl. k8s auth), AWS Secrets Manager, and
Fernet-encrypted DB secrets behind a `vault://` / `aws://` / `db://` resolver. It is wired to **LLM
API keys only**. Every events credential is a bare `os.environ` read —
`grep resolve_secret src/cuga/backend/events/` returns nothing. `oauth.py:95-96` already carries the
TODO. IBM Secrets Manager is not present.

### Recommendation

Making AP read tokens *from* a vault is **not** the high-value move — AP already encrypts what it
stores. The exposed secret is the one that **unlocks** AP. Put **`AP_PASSWORD` and
`AP_ENCRYPTION_KEY`** behind the existing `secret_resolver` at `ap_engine.py:35-36`. Small change
against machinery we already own; closes the actual hole. Separately, `AP_PASSWORD` should be a
strong generated value — it guards an internet-reachable admin console.

**Exception to "AP holds it":** direct-backend creds (`SLACK_BOT_TOKEN`, `SLACK_SIGNING_SECRET`,
`DISCORD_BOT_TOKEN`, `BOX_DEV_TOKEN`) live only in plaintext `.env` and CUGA process memory. They
still never enter a prompt. `.env` and `.ap.env` are both correctly gitignored.

---

## 2. The connect-gate false negative (why GitHub "wasn't connected")

**Root cause — a bare `except` conflates "AP is down" with "the user never connected."**
`concierge.py:150-154`:

```python
try:
    exists = await engine.connection_exists(ext, project_name=p.ap_project_name(grain))
except Exception:  # noqa: BLE001
    exists = False
```

`connection_exists` (`ap_engine.py:451-456`) is a **live HTTP call to AP** — sign in, list
connections. AP down, unreachable, slow, or missing the piece → throws → swallowed → the user is told
to connect credentials they already connected. "AP unreachable" and "never connected" produce a
byte-identical message.

`CONNECT NEEDED — connect your github` has **three causes**, in check order:

1. **No PAT connected yet** — the only cause the message actually names.
2. **AP is down or unreachable.** Check first. Note this stack is **podman, not docker** — `docker ps`
   silently returns nothing and reads as "no containers." Use `podman ps` and
   `curl -s localhost:8081/api/v1/flags`.
3. **`GITHUB_TOKEN` is in `.env` but auto-connect never landed it in AP.** On a fresh AP database the
   `@activepieces/piece-github` piece isn't installed, so `_autoconnect_env_tokens`
   (`app.py:801-825`) fails silently after four retries. Run `make ap-pieces`, restart. Confirm with
   `curl -s localhost:8100/api/events/integrations` — github reading `auto_connect_pending` is exactly
   this. The code predicts it at `app.py:387-389`.

**Ruled out: project-grain mismatch.** The gate (`concierge.py:151`) and auto-connect (`app.py:793`)
both read `EVENTS_AP_PROJECT_GRAIN` with the same default, so they query the same AP project.

### Two contributing faults

**The `identity` table is empty** (`events.db`, 0 rows). GitHub creds key on `(tenant, user)` as
`ea::<tenant>::<user>::github`. A Slack sender whose Slack id was never account-linked falls back via
`app.py:68-77` to the operator principal `default/default/local`. So a PAT connected through the
**Studio** (keyed to the web-session user) is a *different key* than Slack looks up. A PAT set in
`.env` auto-connects under `local` and matches. That asymmetry is why the `.env` path works and the
Studio path doesn't, for Slack.

**GitHub has no direct fallback.** Box gets a bypass (`concierge.py:147-148`); there is no
`github_direct.py` and no `GITHUB_TOKEN` short-circuit in the gate. So the gate blocks even when the
PAT in `.env` would work fine against the GitHub API.

### ❓ Open decision

Split that `except` so AP-unreachable surfaces as its own error rather than accusing the user. A few
lines; changes runtime behavior. **Not done — needs sign-off.** Docs were updated instead (§4).

---

## 3. NL→Flow branching — decided, designed, not built

**Decided** (DECISIONS_2026-07-08 §5, "Route A"): AP owns the trigger, all OAuth/connections, the
routing branch, and all sends. CUGA owns reasoning only.

**Not built.** The router primitive exists only in the *template* layer — `flows.py` `router_step`
(152), `build_push_flow(branches=…)` (242), `build_resume_watcher_flow` (264-275). The **live** engine
can't reach it: `ap_engine.create_push_flow` (`ap_engine.py:407-443`) builds a strictly linear
`trigger → http → publish`, takes no `branches` argument, emits no ROUTER op. Its docstring says
"PUSH/branching flows are Phase 3" (line 9).

**Tier 0 is done** — every push flow forwards `"_raw": "{{trigger}}"` (`ap_engine.py:435`) and
`worker_input()` falls back to it when curated fields come back empty (`envelope.py:103-109`). That's
the fix for the Gmail bug where the piece nested everything under `message`. **Tier 1** (read the
piece's output schema at arm time, derive the map once, LLM proposes → validator gates) is the marked
next target, unbuilt.

**Next steps** (~1-2 days, roadmap_next.html item #2):

1. Add a ROUTER `ADD_ACTION` op to `ap_engine.py`, branching on `{{step_1.body.answer}}` with
   `TEXT_STARTS_WITH` + a FALLBACK branch. Exact JSON already at `flows.py:157-171`.
2. Per-branch send ops — reuse `_channel_send_op` (`ap_engine.py:242`); add Gmail `send_email`.
3. Thread `branches` through `create_push_flow` and the concierge arm call (`concierge.py:349`).

**Two wrinkles to settle before building, not after:**

- The agent's single answer string doubles as **both** the routing signal **and** the delivered
  message, so `MATCH:` leaks into Slack unless the agent returns structured `{route, message}`.
- The branch is **static, 2-way, fixed at arm time, prefix-matched**. "An utterance can have multiple
  parts" may mean N-way or dynamic routing — which the decision doc explicitly scopes out as "would
  need a rebuild." **Decide which we actually want before spending the two days.**
- ⚠ `flows.py:170-171`'s router-with-children op addressing has **never been tested against the live
  AP REST API** (DECISIONS_2026-07-08 line 91). Verify first; it could invalidate the estimate.

---

## 4. Setup docs

**The entry point to hand a new person:** [../events_docs/setup/README.md](../events_docs/setup/README.md)
— all 7 connectors, channels-vs-integrations, the 2 shared prereqs, and a one-shot verifier
(`tests/events/preflight.py`).

| Connector | Guide | Backend |
|---|---|---|
| Telegram | `events_docs/setup/TELEGRAM.md` | AP webhook |
| Discord | `events_docs/setup/DISCORD.md` | direct Gateway |
| Slack | `events_docs/setup/SLACK.md` | direct |
| GitHub | `events_docs/setup/GITHUB.md` | AP, PAT |
| Box | `events_docs/setup/BOX.md` | AP OAuth, direct opt-in |
| Gmail | `events_docs/setup/GMAIL.md` | AP OAuth |
| Webhook | `events_docs/setup/WEBHOOK.md` | direct |

Umbrella runbook: `events_docs/SETUP.md`. Env vars live in `.env.events.example` — the root
`.env.example` has **zero** connector vars; don't start anyone there.

### Three defects fixed (all actively misleading)

1. **The Studio wizard contradicted the code on Discord.** `setup_guides.py:44-54`, which renders the
   in-product Setup tab, described Discord as `"AP (polling, ~5 min)"` with a Connect call and an arm
   step. The code default has been direct Gateway (instant, no public URL, nothing to arm) since
   `delivery.py:26`. The markdown was right; the UI users actually see was wrong.
2. **GMAIL.md claimed token expiry can't happen** — *"it shouldn't: AP refreshes it."* But Google's
   **Testing** publishing mode invalidates the refresh token after **7 days** regardless of refreshing
   (documented only in `SETUP.md:33`). A "worked all week, then broke" trap.
3. **GITHUB.md listed one of the three causes** of `CONNECT NEEDED`. Now lists all three plus the
   Slack identity-key mismatch from §2.

### Remaining doc gaps (not fixed)

- No per-connector preflight for GitHub / Gmail / Webhook — only the combined
  `tests/events/live_integrations_e2e.py`.
- Box's AP-OAuth default path is prose, not numbered steps; its watcher passes file *name* not
  *content* (the file-download exception in DECISIONS_2026-07-08).
- Expiry traps to warn about: `BOX_DEV_TOKEN` ~60 min; Gmail refresh token 7 days in Testing mode;
  `make nuke`/`make fresh` wipes AP volumes and loses all connections (`make stop && make up` keeps
  them).

---

## Resume checklist

1. `podman machine start && make up` — nothing above was verified live.
2. `curl -s localhost:8100/api/events/integrations` → is github `auto_connect_pending`? If so,
   `make ap-pieces` + restart, and §2 cause #3 is confirmed.
3. Answer the two §3 wrinkles (structured `{route, message}`; 2-way vs N-way) before the router build.
4. Decide the §2 open question (split the `except`).
5. Consider the §1 vault change — `AP_PASSWORD` + `AP_ENCRYPTION_KEY` through `secret_resolver`.
