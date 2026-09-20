# Events layer — environment variables

A scannable reference for every environment variable the CUGA **events layer** reads, grouped by
purpose. The annotated, copy-paste template is [`.env.events.example`](../../.env.events.example) at
the repo root; this file is the "what is each one, and do I need it?" companion.

**Everything AP-free.** The whole top half runs with **no Activepieces** — channels, the native
cron/poll scheduler, the generic webhook, GitHub-direct, and Box-direct. Activepieces is only for
the SaaS push triggers still on it (Gmail, Outlook, Box-OAuth, Google-Calendar, Pinterest).

`generate a secret:` `python3 -c "import secrets; print(secrets.token_urlsafe(32))"` ·
`generate a Fernet key:` `python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`

## Required — the floor to boot and answer

| Variable | Purpose |
|---|---|
| `AGENT_SETTING_CONFIG` | Model profile, e.g. `settings.watsonx.toml`. Without it CUGA falls back to `settings.openai.toml` and crashes on a missing `OPENAI_API_KEY`. |
| `LLM_PROVIDER` / `LLM_MODEL` | The provider + model (e.g. `watsonx` / `openai/gpt-oss-120b`). |
| `WATSONX_APIKEY` · `WATSONX_URL` · `WATSONX_PROJECT_ID` (or `WATSONX_SPACE_ID`) | watsonx creds. (Or `OPENAI_API_KEY` for the OpenAI profile.) |
| `GATEWAY_TOKEN` | Shared secret for `/invoke` + `/run`. Both fail closed (401) without it. |

## Events core

| Variable | Required? | Purpose |
|---|---|---|
| `EVENTS_DB` | for durability | SQLite path locally, **Postgres DSN** in prod. Unset ⇒ in-memory, lost on restart. |
| `EVENTS_DB_CA_B64` | if DSN is `verify-full` | Base64 CA cert for the managed Postgres. |
| `EVENTS_WEBHOOK_KEY` | for the generic webhook | Gates `POST /api/events/hook/<name>` (`?key=`). Unset ⇒ the webhook **401s every request** (fails closed). |
| `CUGA_SECRET_KEY` | recommended | Fernet key; encrypts the config store + admin-entered OAuth secrets. Unset ⇒ plaintext + a warning. |
| `EVENTS_SCHEDULER` | default `native` | `native` = AP-free in-process cron/poll. `ap` = legacy AP schedule. |

## Channels (each optional — provide the token to enable; all direct/AP-free)

| Channel | Variables | Notes |
|---|---|---|
| **Slack** | `SLACK_BOT_TOKEN`, `SLACK_SIGNING_SECRET` | Direct Events API (signed). `EVENTS_SLACK_BACKEND` unset = direct. |
| **Discord** | `DISCORD_BOT_TOKEN` | Direct Gateway WebSocket. `EVENTS_DISCORD_BACKEND` unset = direct. |
| **Telegram** | `TELEGRAM_BOT_TOKEN`, `EVENTS_TELEGRAM_BOT_USERNAME` | Direct long-poll (no public URL). `EVENTS_TELEGRAM_BACKEND` unset = direct. |
| **WhatsApp** | `WHATSAPP_TOKEN`, `WHATSAPP_PHONE_NUMBER_ID`, `WHATSAPP_APP_SECRET`, `WHATSAPP_VERIFY_TOKEN` | Direct Meta Cloud API (signed webhook). 24-h window, then a template. Optional: `WHATSAPP_TEMPLATE_NAME`, `WHATSAPP_TEMPLATE_LANG`, `WHATSAPP_API_VERSION`. Needs `EVENTS_PUBLIC_URL`. |

> A channel with a bot token that also owns a socket (Telegram long-poll, Discord Gateway) is a
> **single-owner loop** — never run a laptop and a deployment on the same token, or they double-process.

## Direct integrations (triggers, AP-free)

| Integration | Variables | Notes |
|---|---|---|
| **GitHub** (14 triggers) | **Receiving events:** `GITHUB_WEBHOOK_SECRET` only (**required** — verifies `X-Hub-Signature-256`, fails closed). **API read-back (optional, pick ONE):** GitHub App creds `GITHUB_APP_ID` + `GITHUB_APP_PRIVATE_KEY` (+ `GITHUB_APP_INSTALLATION_ID`) = production, **or** `GITHUB_TOKEN` (PAT) = quick/demo (PAT wins if both set). | Always direct (no switch). Point a repo webhook at `<EVENTS_PUBLIC_URL>/api/events/github/events`. Read-back auth is needed only when the agent calls the API (fetch diff, comment) — **pure trigger reaction needs neither**. |
| **Box** | `EVENTS_BOX_BACKEND=direct` **+** either CCG (`BOX_CLIENT_ID`, `BOX_CLIENT_SECRET`, `BOX_ENTERPRISE_ID`, optional `BOX_USER_ID`) **or** `BOX_DEV_TOKEN` | CCG is durable (mints its own tokens); a dev token expires in ~60 min. Optional `BOX_FOLDER_ID`. |

## Deploy knobs (set by the CE scripts; override via env)

`REGION` · `RESOURCE_GROUP_NAME` · `CE_PROJECT_NAME` · `REGISTRY_HOST`/`REGISTRY_NAMESPACE`/`REGISTRY_SECRET_NAME` ·
`IMAGE_REPO`/`IMAGE_REF` · `CORE_APP`/`EVENTS_APP` · `CPU`/`MEMORY`/`EPHEMERAL` · `MIN_SCALE`/`MAX_SCALE` (both 1 — the loops are singletons) ·
`REQUEST_TIMEOUT` · `CE_ROSTER` · `CE_EVENTS_SUPERVISOR` · `MCP_SERVERS_FILE_IN_IMAGE` · `CUGA_CE_ADMIN` (admin gate).
The four backend switches — `EVENTS_TELEGRAM_BACKEND` / `EVENTS_DISCORD_BACKEND` / `EVENTS_SLACK_BACKEND` / `EVENTS_BOX_BACKEND` — default to `direct` in the CE deploy.

## OIDC (core UI login — optional)

`OIDC_CLIENT_ID` · `OIDC_CLIENT_SECRET` · `OIDC_DISCOVERY_URL` · `OIDC_REDIRECT_URI` — logs a human into the Studio UI (served by `cuga-core`). The events service has no session awareness; it authenticates callers with `GATEWAY_TOKEN`.

## Activepieces (only for the SaaS push triggers still on AP)

`AP_BASE_URL` · `AP_EMAIL` · `AP_PASSWORD` · `HOST_CALLBACK_URL` · `EVENTS_AP_PROJECT_GRAIN`, plus the
per-vendor `EVENTS_OAUTH_<APP>_CLIENT_ID/SECRET`. Needed **only** for Gmail / Outlook / Box-OAuth /
Google-Calendar / Pinterest. Leave blank to run fully AP-free. (GitHub is no longer here — it's direct.)

---

### Minimal sets

- **Core CUGA + web chat:** the 4 Required rows.
- **+ events (cron/poll, generic webhook):** add `EVENTS_DB`, `EVENTS_WEBHOOK_KEY`, `CUGA_SECRET_KEY`.
- **+ a channel or direct integration:** add that one block above. Nothing else is required.
