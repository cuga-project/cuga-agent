# Reuse map — reuse vs add vs AP

Grounded in a read of `cuga-agent-july` (2026-07-01). Principle: **do not duplicate CUGA
APIs; do not build a connection/OAuth framework — AP owns that.**

## Legend
- ♻️ **REUSE** an existing CUGA API/subsystem (cited below)
- ➕ **ADD** — genuinely new, doesn't exist in CUGA
- ⚡ **AP** — Activepieces owns it; CUGA at most reads/proxies

## The map
| Capability | Verdict | Where |
|---|---|---|
| Create / update / list an **agent** | ♻️ REUSE | `POST /api/manage/config/draft` + publish → `config_store.save_config()` (`agent_configs` table, versioned, multi-`agent_id`) |
| Attach **MCP servers / tools** to an agent | ♻️ REUSE | `tools[]` in the config → `managed_mcp.tools_to_registry_yaml()` → `.cuga/managed_mcp_servers.yaml` |
| **Run** an agent (produce an answer) | ♻️ REUSE | the `/stream` runtime / `event_stream()` (LangGraph); `/invoke` collects it into one response |
| **Secrets / credentials** (CUGA-side) | ♻️ REUSE | `/api/secrets` → `secrets_store.py` (**Fernet-encrypted at rest**, or Vault backend); refs `db://` / `vault://` |
| **thread_id / conversation context** | ♻️ REUSE | `X-Thread-ID` header + conversation-thread store + LangGraph memory; the `/invoke` envelope carries `thread_id` straight through |
| **Auth** on new endpoints | ♻️ REUSE | `require_auth` / `require_chat_access` / `require_manage_access` dependencies |
| **`POST /invoke`** (AP callback seam; run agent on a normalized payload, return/deliver) | ➕ ADD | new router; `X-Gateway-Token` (machine-to-machine) |
| **`POST /api/concierge`** (NL → reuse/create worker + arm trigger) | ➕ ADD | new router; reuses the `/stream` SSE machinery |
| **concierge meta-tools** (`list_capabilities`, `answer_now`, `find_or_create_flow`) | ➕ ADD | router over PRE-BUILT agents (decision 0005 — no agent creation); `find_or_create_flow` reuses/creates an AP flow. *(Earlier `provision_agent`/`run_now`/`create_subscription` are superseded.)* |
| **subscription index** (which AP flows we built → agent, deliver-to; for listing/reuse) | ➕ ADD | one thin table (not CUGA's config store) |
| **Connections** to apps (Gmail/Box/GitHub/Slack/Telegram) | ⚡ AP | AP connection store; OAuth authorized in AP's connect UI (can't be minted headlessly) |
| **Integration credentials** (OAuth tokens, refresh) | ⚡ AP | AP encrypts + refreshes connections — the thing CUGA lacks |
| **Triggers** (cron / webhook / poll / app-event / run-once) | ⚡ AP | AP trigger pieces → call back `POST /invoke` |
| **Delivery** (send Telegram/Slack/email) | ⚡ AP | AP connector send-steps |
| **Run history / observability** | ⚡ AP | AP run history is the one pane (per Decision) |

## What this deletes from the earlier plan
- ❌ **`/api/fleet/agents`** — reuse `/api/manage/*` instead.
- ❌ **`/api/integrations/{kind}/connect`** (per-integration endpoints) — AP owns connections;
  at most one generic `GET /api/integrations` that *reads AP's connection list*, and a
  "connect" that returns the **AP connect URL**.
- ❌ a heavy `fleet.db` — shrinks to a **thin subscription index**; agents live in CUGA's
  `agent_configs`, runs come from AP.

## Net new surface on CUGA
Just **two endpoints** (`/invoke`, `/api/concierge`) + the concierge agent + a subscription
index table. Everything else is reuse or AP.

## What CUGA is MISSING that we must supply (via AP, not CUGA)
CUGA has **no** Activepieces/OAuth/connection concept today (confirmed by grep). That gap is
intentional in this design — AP fills it. CUGA's job stays "define + run agents, securely."
