# Integration setup guides

One focused, step-by-step guide per integration — what to procure, how to wire it, and the exact
command to **verify** it works. Pick your integration:

**Channels** (converse with an agent):

| Guide | Backend | Inbound | What it's for |
|---|---|---|---|
| **[TELEGRAM.md](TELEGRAM.md)** | Activepieces (webhook) | instant | 1:1 chat with an agent |
| **[DISCORD.md](DISCORD.md)** | **direct (default)** · AP polling behind a flag | instant (Gateway) | channel chat with an agent |
| **[SLACK.md](SLACK.md)** | **direct (default)** · AP behind a flag | instant | channel chat with an agent |

**Integrations** (an app the agent watches / acts on — all run on **Activepieces**):

| Guide | Auth | Trigger | What it's for |
|---|---|---|---|
| **[BOX.md](BOX.md)** | AP OAuth (default) · direct token behind a flag | `new_file` | file/résumé watcher (PUSH) |
| **[GITHUB.md](GITHUB.md)** | AP token (PAT) | `new_pull_request` / `new_issue` | PR/issue watcher (PUSH) |
| **[GMAIL.md](GMAIL.md)** | AP OAuth (consent + refresh) | `new_email` | inbox watcher (PUSH) + email delivery sink |
| **[WEBHOOK.md](WEBHOOK.md)** | none (direct) | HTTP POST | generic inbound webhook → triage → deliver |

**Channels vs integrations.** A *channel* (Telegram/Discord/Slack) is a place you **converse with**
an agent — two-way. An *integration* (Box/GitHub/Gmail) is an app the agent **watches or acts on**;
integrations run on AP (OAuth/token + the piece trigger). Full integration e2e:
`tests/events/live_integrations_e2e.py`.

**Two backends per connector.** Some connectors run **direct** (CUGA talks to the vendor API with a
token — no Activepieces) and some via **AP** (AP holds the OAuth token and runs the trigger/send).
The default is chosen per connector; override with `EVENTS_<NAME>_BACKEND=direct|ap`. Direct exists
because AP's OAuth2 connection refuses a pre-obtained token (it insists on doing the code-exchange
itself) and, for Slack, its app-event trigger silently dropped events. See
[../SETUP.md](../SETUP.md) for the "AP owns it" boundary and the parked Slack bug.

## Before any integration — the two shared prerequisites

1. **A running events server** on `:8100` (behind `EVENTS_ENABLED=1`) with the tool registry up.
   See [../SETUP.md](../SETUP.md). Quick check: `curl -s localhost:8100/api/events/status`.
2. **A public HTTPS URL** (`EVENTS_PUBLIC_URL`) for the connectors that receive webhooks over the
   internet (Telegram, Slack-direct). `events_up.sh` provides it **automatically** — a **stable
   ngrok** domain when `EVENTS_NGROK_DOMAIN` is set (recommended: configure Slack/Gmail once), else a
   cloudflared quick-tunnel it auto-detects and wires into the server (no manual `.env` edit). Full
   story: [../PUBLIC_URL.md](../PUBLIC_URL.md). **Discord (direct Gateway — outbound WS) and Box
   (polling) need no public URL.**

## Verify them all at once

```bash
EVENTS_SERVER_URL=http://localhost:8100 .venv/bin/python tests/events/preflight.py
```
Prints ✅/❌ per integration (watsonx · Activepieces · Telegram · Discord · Slack · Box · MCP) straight
from your `.env`. Run it first — it catches a bad token before you chase a round-trip.

> Secrets live in `.env` (gitignored). Never paste token values into chat or commits. When you add a
> secret, **restart the server** so it picks up the new value (the launcher loads `.env` at startup).
