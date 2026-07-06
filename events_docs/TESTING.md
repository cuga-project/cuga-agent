# Testing — Phase 1 & 2

How to test the events layer: the offline suite (fast, runs anywhere), the live e2e harnesses
(need creds + a running server + AP), and the Studio UI click-through. Cross-links:
[README.md](README.md) · [SETUP.md](SETUP.md).

---

## 1. Quick test

```bash
# OFFLINE — 60 green, no network, run on every change
.venv/bin/python -m pytest tests/events -q

# PREFLIGHT — credential doctor: does every integration work from .env alone? (never fails)
python3 tests/events/preflight.py     # watsonx · AP · Telegram · Discord · Slack · Box · MCP
```

The offline suite is **60 passing**:

| File | Count | Covers |
|---|---|---|
| `tests/events/test_events_core.py` | 14 | envelope · MCP catalog · flow builders (cron/poll/push/router) · subscription index · classifier · reason→build planner · StubRuntime memory · 12-utterance oracle |
| `tests/events/test_events_dimensions.py` | 27 | connectors · catalog · credentials (shared/per-user) · seed agents · flow dedup · OAuth registry · isolation · runtime selection + fallback gating · users · identity map · per-agent permissions · delivery backend + direct delivery |
| `tests/events/test_events_studio_api.py` | 19 | Studio API contract (status/channels/integrations/examples/subscriptions) · **agent create/update + mcp-servers** · channel direct-vs-AP backend · slack/box direct · poll · webhook triage + key gate |

`preflight.py` reads `.env` and reports which integrations are actually reachable — it is a
diagnostic, so it always exits cleanly (it never fails the run).

---

## 2. Coverage matrix

Two tiers: **offline** (stdlib / venv, no network — the three `test_events_*.py` files above) and
**live** (needs the CUGA venv, watsonx, AP on `:8081`, sometimes the registry — the `live_*.py`
harnesses). Offline files: `test_events_core.py`, `test_events_dimensions.py`,
`test_events_studio_api.py`. Live harnesses: `live_*.py`.

| Dimension | Offline test | Live harness |
|---|---|---|
| **Channel: web** | `test_events_studio_api.py` (channels endpoint) | Studio Concierge tab / `live_integrations_e2e.py` (NOW) |
| **Channel: telegram** | `test_events_dimensions.py` (identity/link, connectors) | `live_integrations_e2e.py`, `live_credentials_check.py` (token connect) |
| **Channel: slack** | `test_events_studio_api.py` (slack direct), `test_events_dimensions.py` (thread scoping, author identity) | `live_slack_check.py` |
| **Channel: discord** | `test_events_dimensions.py` (connectors) | `live_discord_check.py` |
| **Integration: gmail** | `test_events_dimensions.py` (integrations status, seed) | `live_gmail_e2e.py`, `live_integrations_e2e.py` (PUSH) |
| **Integration: box** | `test_events_studio_api.py` (box direct, poll) | `live_box_e2e.py`, `live_box_direct_check.py`, `live_integrations_e2e.py` (PUSH) |
| **Integration: github** | `test_events_dimensions.py` (seed `pr_reviewer`, full-AP wiring) | `live_github_e2e.py`, `live_integrations_e2e.py` (PUSH) |
| **Trigger: now** | `test_events_core.py` (planner/oracle) | `live_integrations_e2e.py` (NOW), `live_phase2_watchers.py` |
| **Trigger: cron** | `test_events_core.py` + `test_events_dimensions.py` (flow builders per-mode) | `live_integrations_e2e.py` (CRON), `live_phase2_watchers.py` (real AP CRON e2e, ~3 min) |
| **Trigger: poll** | `test_events_studio_api.py` (poll) + `test_events_dimensions.py` (flow builders) | `live_integrations_e2e.py` (POLL), `live_box_direct_check.py` |
| **Trigger: push** | `test_events_core.py` (push builder) + `test_events_dimensions.py` | `live_integrations_e2e.py` (PUSH box/github/gmail), `live_box_e2e.py` |
| **Trigger: webhook** | `test_events_studio_api.py` (webhook triage + key gate) | `live_integrations_e2e.py` (WEBHOOK) |

Cross-cutting live harnesses (not per-channel): `live_isolation_check.py` (two tenants → two AP
projects), `live_identity_check.py` (two users isolated · per-agent permissions · channel link
handshake), `live_credentials_check.py` (shared vs per-user creds), `live_statefulness_check.py`
(agent + memory survive a replica restart), `live_phase2_watchers.py` (full AP CRON round-trip).

`live_integrations_e2e.py` is the canonical all-modes harness; the per-integration
`live_box_e2e.py` / `live_github_e2e.py` / `live_gmail_e2e.py` and the AP-free direct checks
(`live_box_direct_check.py`, `live_slack_check.py`, `live_discord_check.py`) are the true
end-to-end ones.

> **Older harnesses being consolidated:** `live_server_e2e_check.py`, `live_concierge_check.py`,
> and `live_stage2_channels.py` still default to port `7860` / the `/api/concierge` path from an
> earlier iteration and are being folded into `live_integrations_e2e.py`. Prefer the harnesses in
> the matrix above; the older ones remain factual but may need `EVENTS_SERVER_URL` overrides.

---

## 3. Live e2e recipe

### Prerequisites
- **Server** on `:8100` with the events layer on: `EVENTS_ENABLED=1 EVENTS_WORKER_BACKEND=cuga
  EVENTS_SEED_AGENTS=1`. `scripts/events_up.sh` starts registry + tunnels + the CUGA server.
- **Activepieces** on `AP_BASE_URL` (`:8081`) for the AP-backed legs (CRON/POLL/PUSH, telegram/discord).
- **`GATEWAY_TOKEN`** in `.env` (the `X-Gateway-Token` on `/invoke`).
- **watsonx creds** (`WATSONX_APIKEY` / `WATSONX_URL` / `WATSONX_PROJECT_ID`) — the LLM the workers use.
- For a full PUSH leg, connect the integration first (see [setup/](setup/)): Box/Gmail via
  `GET /api/events/connect/{app}` (OAuth), GitHub via `POST /api/events/connect/github/token`.

All commands read `.env` for creds. `EVENTS_SERVER_URL` defaults to `http://localhost:8100`.

### All four trigger modes + full-AP integrations
```bash
EVENTS_SERVER_URL=http://localhost:8100 .venv/bin/python tests/events/live_integrations_e2e.py
```
**PASS:** NOW returns a real price; CRON and POLL each create a real AP flow (verified via
`subscriptions.ap_flow_id`); PUSH box/github/gmail either arm an AP flow (if connected) or return
the correct CONNECT-NEEDED; WEBHOOK triages and delivers.

### Box — upload a real file, watcher detects + judges it
```bash
BOX_FOLDER_ID=0 EVENTS_SERVER_URL=http://localhost:8100 GATEWAY_TOKEN=<..> \
  .venv/bin/python tests/events/live_box_e2e.py
```
**PASS:** uploads a real résumé-like file to Box, the new file is detected, `resume_judge` returns
a verdict, then the file is deleted. (Box dev tokens expire ~60 min — a stale token prints a clear
"regenerate" message.)

### Box direct (AP-free poll)
```bash
BOX_DEV_TOKEN=<fresh> BOX_FOLDER_ID=<folder> EVENTS_SERVER_URL=http://localhost:8100 \
  .venv/bin/python tests/events/live_box_direct_check.py
```
**PASS:** whoami validates the token, folder items list, `new_files_since` baseline is correct, and
`POST /api/events/box/poll` fires the watcher per new file (direct-channel delivery, no AP).

### GitHub — review a real PR (read-only)
```bash
EVENTS_SERVER_URL=http://localhost:8100 GATEWAY_TOKEN=<..> \
  .venv/bin/python tests/events/live_github_e2e.py
```
**PASS:** fetches a real open PR + its diff from a public repo, feeds it to `pr_reviewer` via
`/invoke`, and gets back a real summary + risk assessment. Zero side effects. Optional
`E2E_PR="owner/repo#123"` to pin a PR.

### Gmail — arm a real inbox watcher
```bash
EVENTS_SERVER_URL=http://localhost:8100 .venv/bin/python tests/events/live_gmail_e2e.py
```
**PASS:** confirms the per-user Gmail OAuth connection exists in AP, arms an inbox watcher, and a
real AP flow is created (`subscriptions.ap_flow_id`). The final leg (send an email → flow fires →
`mailbot` summarizes) needs an email sent to the connected account; the harness prints how.

### Slack / Discord direct checks
```bash
EVENTS_SERVER_URL=http://localhost:8100 .venv/bin/python tests/events/live_slack_check.py
EVENTS_SERVER_URL=http://localhost:8100 DISCORD_CHANNEL_ID=<id> \
  .venv/bin/python tests/events/live_discord_check.py
```
**PASS:** each validates the bot token, confirms the bot can post (delivery leg), and verifies the
flow/descriptor shape, then prints the one manual step each platform requires (Slack: OAuth
connection + Events API Request URL; Discord: post a message and wait for the ~5-min poll, Message
Content Intent on).

### Cross-cutting (mostly `.venv-events`, no server needed)
```bash
.venv-events/bin/python tests/events/live_phase2_watchers.py      # AP CRON e2e (~3 min)
.venv-events/bin/python tests/events/live_isolation_check.py      # two tenants → two AP projects
.venv/bin/python        tests/events/live_identity_check.py       # users isolated + perms + link
.venv-events/bin/python tests/events/live_credentials_check.py    # shared vs per-user creds
.venv-events/bin/python tests/events/live_statefulness_check.py   # survive a replica restart
```

Clean AP between runs: `.venv-events/bin/python tests/events/ap_nuke.py --dry` (preview),
`ap_nuke.py` (delete EA-tagged), `ap_nuke.py --all` (nuclear).

---

## 4. Studio UI walkthrough

The Studio is added **into CUGA's existing React frontend** (not a new app) and is **dumb** — every
tab just renders a `GET /api/events/*` and the Concierge tab POSTs your text. So clicking through
the UI *is* testing the backend. Rebuild the bundle after any `.tsx` change:
`scripts/frontend_build.sh`, then `scripts/events_up.sh`.

Open **http://localhost:8100/studio** (or `/manage` → the **Studio** nav / **"Open Event Studio →"**
button — hidden in vanilla CUGA). The header shows
`scope default/default/local · workers cuga · concierge react · AP connected`.

| Tab | Click to prove |
|---|---|
| **Concierge** | *"what is the bitcoin price right now?"* → **Send** → live price (a CUGA worker answered). Flip **Preview** → the plan JSON, no side effects (`?dry_run=1`). *"every 1 minute send me new arXiv papers…"* → arms a real AP flow. |
| **Channels** | web = connected; telegram/discord/slack = connected only when the bot token is set. |
| **Integrations** | gmail/box/github/slack with **live AP-connection** status + a **Connect** button (OAuth apps open the login popup; token apps prompt for a token). |
| **Flows** | **Refresh** → the armed watcher appears with a **CRON**/**POLL** badge, its agent, backend, and delivery target. |
| **Examples** | click **Try it** on "Geography + follow-up memory" → drops the utterance into Concierge → Send, then *"and its population?"* → per-thread memory holds. |
| **Agents** | the pre-built worker fleet the concierge routes among + each agent's tools/channels/integrations. Use **Add agent** to register a new worker; each agent row shows a **Connected / Reconnect** status for its integrations. |
| **Profile** | your identity, roles, linked channels (Link buttons: Telegram/Discord), connected integrations. |
| **Admin** | tenant users + roles; OAuth apps (enter client id/secret in the UI); arm channel inbound flows. |

Full reference: [STUDIO_UI.md](STUDIO_UI.md).
