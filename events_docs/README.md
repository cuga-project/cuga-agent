# Event-driven agents on CUGA

Turn CUGA from a *request/response* agent into an *event-driven* one: agents that watch inboxes,
folders, repos and schedules, converse on chat channels, and deliver answers back — without
rebuilding CUGA's reasoning. The whole layer is **opt-in** behind `EVENTS_ENABLED`; with the flag
off, CUGA is byte-for-byte unchanged.

> **The model in one breath.** A **builder** defines **agents** (a skill/prompt + MCP tools + the
> connectors it may use). End users chat to them from **channels** (web / Telegram / Slack /
> Discord) and set up **triggers** in natural language. A **concierge** router decides what to do —
> answer now, arm a standing **flow** (cron / poll / push), or decline. **Activepieces (AP)** owns
> the connection + trigger + clock for integrations; **direct backends** own the chat channels.
> Isolation is by `Principal → scope`; a shared chat bot tells users apart by message author.

![Architecture — channels direct/AP, integrations on AP, all converging on the /invoke seam](architecture.png)

*Yellow = people & apps · blue = Activepieces (integration triggers + the clock) · green = the CUGA
server · cyan = direct backends. Every trigger converges on the one `/invoke` seam. Diagram source:
[architecture.mmd](architecture.mmd).*

---

## 0. Quick start — the `make` shortcuts

With base CUGA installed and `.env` filled ([SETUP.md](SETUP.md) is the full runbook), the whole
day-to-day loop is a root [`Makefile`](../Makefile). Run `make` (no target) to list everything:

```bash
make env-check      # verify .env has the required keys (offline)
make doctor         # live credential doctor — ping each configured service
make up             # start Activepieces + CUGA + registry + tunnels (AP first)
make channels       # connect + arm every inbound chat channel with a token in .env
make status         # what's running + the tunnel URLs
open http://localhost:8100/studio
make test           # ~60 offline checks, all green
make stop           # stop everything, keep data (make nuke also wipes the DBs)
make nuke           # stop AND wipe AP volumes + events.db (full reset)
```

Everything below is the detail behind those commands. New here? Read §1–§2 for the model, then live
in `make`.

---

## 1. What this is — channels, integrations, triggers

Two kinds of connector (see [DESIGN.md](DESIGN.md)):

- **Channels** — you *converse* with a human: `web`, `telegram`, `slack`, `discord`.
- **Integrations** — an agent *watches / acts on* an app: `gmail`, `box`, `github` (+ `outlook` planned).

Four+one **trigger** shapes, all normalized onto one `/invoke` envelope:

| Trigger | Meaning | Clock / source |
|---|---|---|
| **NOW** | answer immediately | the inbound chat message |
| **CRON** | on a schedule | AP timer |
| **POLL** | check every N minutes | AP poll (or the direct Box poller) |
| **PUSH** | when the app fires | AP piece trigger (new-email / new-PR / new-file) |
| **WEBHOOK** | an external system POSTs to us | direct HTTP (`/api/events/hook/<name>`, no AP) |

---

## 2. Status — what's completed

Everything below is wired end-to-end and covered by tests. **Offline suite: 60 passing**
(14 core + 27 dimensions + 19 Studio API). Backend column = how the connector talks to the world.

### Channels
| Channel | Backend | Two-way | Verified |
|---|---|---|---|
| Web | built-in (`/api/concierge`) | ✅ | offline + live |
| Telegram | **Activepieces** (webhook piece) | ✅ | live |
| Slack | **direct** (Events API, signed) | ✅ | live (`live_slack_check.py`) |
| Discord | **direct** (Gateway WebSocket, instant) | ✅ | live (`live_discord_check.py`) |

### Integrations (all via Activepieces)
| Integration | Auth | Trigger | Agent | Verified |
|---|---|---|---|---|
| GitHub | PAT (token) | new-PR (push) | `pr_reviewer` | live (`live_github_e2e.py`, real PR) |
| Box | OAuth (default) / direct-poll (opt-in) | new-file | `resume_judge` | live (`live_box_e2e.py`, real upload) |
| Gmail | OAuth (consent + refresh) | new-email / send | `mailbot` | connection live (`live_gmail_e2e.py`) |

### Triggers
NOW ✅ · CRON ✅ (e.g. hourly *GitHub-trending → Slack*) · POLL ✅ · PUSH ✅ · WEBHOOK ✅.

**Known gaps & honest limitations:** see [KNOWN_GAPS.md](KNOWN_GAPS.md). The headline ones: the
`/invoke` seam trusts a caller-supplied `scope` behind the gateway token (real deployments need an
upstream auth layer); OAuth `state` is unsigned; the quick-tunnel URL is ephemeral. All documented
there with the rationale.

---

## 3. Quick test (60 seconds, no server, no credentials)

```bash
# from the repo root
make test           # = pytest tests/events -q  → 60 offline checks, all green
make doctor         # = preflight.py: which live creds are present in .env
```

`preflight.py` never fails the build — it just reports which external services you *could* test live.

---

## 4. How to set it up

**[SETUP.md](SETUP.md) is the single end-to-end runbook** — from setting up base CUGA (repo, venv,
deps, an LLM key) through Activepieces and the events services, with an at-a-glance step table. Then
the per-connector guide for whatever you're wiring — each ends with a **Verify** step and the exact
test to run:

[Slack](setup/SLACK.md) · [Discord](setup/DISCORD.md) · [Telegram](setup/TELEGRAM.md) ·
[GitHub](setup/GITHUB.md) · [Box](setup/BOX.md) · [Gmail](setup/GMAIL.md) · [Webhook](setup/WEBHOOK.md)

Credentials live in `.env`, tagged **TENANT** (one per org: bot tokens, OAuth *app* client id/secret)
vs **USER** (per person: PATs, OAuth consent). The Studio reads `.env` and shows *configured ✓* or a
*set-up →* wizard, plus live **● Connected / ○ Not connected** status. See
[decisions/0003-credentials-ownership.md](decisions/0003-credentials-ownership.md).

---

## 5. How to test (for real)

Full recipe in **[TESTING.md](TESTING.md)** — the offline suite, then the live e2e harnesses (one per
integration, each hits a real API), then the Studio UI walkthrough. The canonical live tests:

```bash
EVENTS_SERVER_URL=http://localhost:8100 .venv/bin/python tests/events/live_integrations_e2e.py  # all modes
.venv/bin/python tests/events/live_github_e2e.py     # real open PR → pr_reviewer
.venv/bin/python tests/events/live_box_e2e.py        # real upload → resume_judge → cleanup
.venv/bin/python tests/events/live_gmail_e2e.py      # OAuth connection + arm inbox watcher
```

`tests/events/README.md` maps every channel/integration/trigger → the one harness that proves it.

---

## 6. Try it for real (end-to-end demo)

The bring-up script starts CUGA + AP + a tunnel; then talk to the concierge from a real channel:

```bash
make up                           # CUGA :8100, AP :8081, tunnels; seeds the demo fleet
open http://localhost:8100/studio # the builder/operator console
```

Then, from Slack, message the bot: *"every hour post the top trending GitHub repos here."* The
concierge arms a real AP CRON flow that runs `github_trending` and delivers straight to that Slack
channel (direct backend, no AP send-step). That exact flow is live today.

---

## 7. User isolation — our story

Isolation is keyed on `Principal(tenant, instance, user)` → two scopes
([decisions/0002-tenancy-and-isolation.md](decisions/0002-tenancy-and-isolation.md)):

- **`scope` = tenant/instance/user** (per-user) namespaces LangGraph threads/memory, subscriptions,
  and AP connection externalIds (`ea::<tenant>::<user>::<app>`).
- **`agent_scope` = tenant/instance** (per-tenant) — agent *definitions* are shared across a tenant's
  users, but each user runs them in their own memory namespace.

A **shared chat bot does not collapse users**: every message carries its author (`source.user` =
Slack `ev.user` / Discord author id), and an **account-linking** handshake (`/link <token>`) binds a
channel-native id to a profile ([decisions/0007-identity-profiles-permissions.md](decisions/0007-identity-profiles-permissions.md)).
Per-user integrations require each user to log in with their own OAuth/token.

**The honest limitation:** `/invoke` accepts the caller-asserted `scope` (it's the trusted AP→CUGA
seam, guarded by the gateway token). Hardening this — signed scope claims / an upstream identity
provider — is the top item in [KNOWN_GAPS.md](KNOWN_GAPS.md).

---

## 8. Why Activepieces (and why some things bypass it)

AP earns its keep for **integrations**: it holds & refreshes OAuth tokens, hosts the piece triggers
(new-email / new-PR / new-file), and is the schedule/poll clock. Rebuilding all of that per app is
exactly the work we don't want to own.

But AP is **not** the right tool for token-auth chat channels: its OAuth2 connector demands the
authorization *code* (it does the exchange itself) and refuses a pre-obtained bot token, and its
Slack `new-message` piece emitted empty payloads. So Slack (Events API) and Discord (Gateway) run as
**direct backends** — CUGA owns the socket — which is also *instant* vs AP's poll latency and needs
no public URL for Discord. The full rationale and the direct/AP division is
[decisions/0008-direct-backends-for-channels.md](decisions/0008-direct-backends-for-channels.md).

---

## Map of the docs

| Doc | What it is |
|---|---|
| [DESIGN.md](DESIGN.md) | Architecture: the `AgentRuntime` port, the `/invoke` envelope, the concierge router, invariants. |
| [SETUP.md](SETUP.md) + [setup/](setup/) | One-command bootstrap + per-connector setup (each with its own Verify + test). |
| [TESTING.md](TESTING.md) | The full test recipe (offline suite + live e2e + Studio walkthrough) and the coverage matrix. |
| [OPERATIONS.md](OPERATIONS.md) | Running it day-to-day: the tunnel, ports, and every sharp edge (the warts-and-all journal). |
| [PUBLIC_URL.md](PUBLIC_URL.md) | The public URL (`EVENTS_PUBLIC_URL`) in one page: the two tunnels, auto-wire, `make public-url`, and the Slack/Gmail update checklist. |
| [KNOWN_GAPS.md](KNOWN_GAPS.md) | Design decisions, known gaps, and issues — the reviewer's list. |
| [STUDIO_UI.md](STUDIO_UI.md) | The Studio console (tabs, the "dumb + additive" contract, agent editor). |
| [MCP_SETUP.md](MCP_SETUP.md) | Giving CUGA workers tools via the MCP registry. |
| [decisions/](decisions/) | The ADRs — the source of truth for the model (0001–0008). |
| [ARCHITECTURE.md](ARCHITECTURE.md) | How it relates to core CUGA: reuse map + blast-radius/compatibility. |
