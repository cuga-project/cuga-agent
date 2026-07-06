# CUGA Studio — the events UI (additive, dumb, in-sync)

The Studio is added **into CUGA's existing React frontend** (Carbon Design + react-router), not a
new app. It is **configuration & visibility only** — it carries **no business logic**. Every
decision (reuse-or-create a worker, classify NOW/CRON/PUSH/POLL, arm a flow, resolve connection
status) happens **server-side**; the UI just renders what the backend reports and posts user text
to the concierge. So the UI can never drift from what the backend can actually do.

## Where it lives
| Piece | File |
|---|---|
| Studio shell + tabs | `src/frontend_workspaces/frontend/src/StudioPage.tsx` |
| Dumb concierge chat | `src/frontend_workspaces/frontend/src/ConciergeChat.tsx` |
| Styles | `src/frontend_workspaces/frontend/src/StudioPage.css` |
| API client (events fns) | `src/frontend_workspaces/frontend/src/api.ts` (`getEvents*`, `postConcierge`) |
| Route `/studio` (role-gated) | `src/frontend_workspaces/frontend/src/App.tsx` |
| "Studio" nav + CTA (gated) | `src/frontend_workspaces/frontend/src/ManageDashboard.tsx` |

**Untouched when the flag is off.** The Studio entry only appears if `GET /api/events/status`
returns ok (i.e. `EVENTS_ENABLED=1`). Vanilla CUGA renders exactly as before; the route is there
but shows a "Studio is off" note if reached directly.

## The tabs (each is a dumb fetch → render)
| Tab | Reads | Shows |
|---|---|---|
| **Concierge** | `POST /api/concierge` (+ `?dry_run=1` via the **Preview** toggle) | chat; live reply, or the plan JSON in Preview mode |
| **Channels** | `GET /api/events/channels` | web/telegram/discord/slack + real status (token present?) |
| **Integrations** | `GET /api/events/integrations` + `/connect/*` | gmail/box/github + real status (AP connection **or** a direct-backend token) → a green **Connected** tag with a **Reconnect** button when connected, else **Connect**: OAuth apps open the login popup, token apps prompt for a token |
| **Flows** | `GET /api/events/subscriptions` | armed watchers with CRON/POLL badges, backend, delivery |
| **Examples** | `GET /api/events/examples` | click-to-load catalog tagged by **outcome** (answer-now / flow / connect / decline) → drops the utterance into Concierge |
| **Profile** | `GET /api/events/me` + `/link/*` + `/connect/*` | the user's identity, roles, **linked channels** (Link buttons: Telegram/Discord), and connected integrations |
| **Agents** | `GET /api/events/agents` · `POST`/`PUT /api/events/agents` · `GET /api/events/mcp-servers` | the pre-built worker fleet the concierge routes among + each agent's tools/channels/integrations/access. An **Add agent** button + per-card **Edit** open a form (name · backend · skill/prompt · tools · channels · integrations+ownership · access) — builder/admin only; saves upsert the agent |
| **Admin** | `GET/POST /api/events/admin/users` + `/oauth-apps` | tenant users + roles (add user); **OAuth apps** — enter each provider's client id/secret in the UI (stored server-side, overrides `.env`); arm channel inbound flows |

Status is **derived from real state**, never faked: channels from the presence of the bot token
that enables delivery; integrations from live AP connections (AP owns creds). PUSH and channel
inbound are wired end-to-end; **channel inbound is live round-trip verified: Telegram (AP), Discord
(AP), Slack (direct, default)** (2026-07-06). Slack/Box default to a *direct* backend (no AP); Box
direct poll is e2e verified. OAuth connect-in-place is built (token path proven against real AP;
OAuth path degrades cleanly). A **Setup** tab renders `GET /api/events/setup-guides` (per-connector
steps + credential ownership). The `/api/events/status` `features` flags are conservative and lag the
wiring.

## The endpoints behind it (all additive, behind `EVENTS_ENABLED`)
Added to `src/cuga/backend/events/app.py` (`register_events_routes(..., engine=…)`):
```
GET /api/events/status        → {enabled, scope, backends, ap_configured, project_grain, features}
GET /api/events/channels      → {channels:[{name,label,status,live,note}]}
GET /api/events/integrations  → {integrations:[{name,label,auth,status,connect_url,live,note}]}
GET /api/events/examples      → {examples:[{id,title,utterance,mode,agent,mcp,live,note}]}
GET /api/events/agents        → {scope, agents:[{name,prompt,backend,mcp_servers,channels,integrations,access,can_use}]}
POST /api/events/agents       → create/upsert an agent (builder/admin)   ·   PUT /api/events/agents/<name> → update
GET /api/events/mcp-servers   → {servers:[{name,hint}]}   (drives the agent-editor tool picker)
GET /api/events/subscriptions → {scope, subscriptions:[…]}   (already existed)
POST /api/concierge           → live route / ?dry_run=1 preview   (already existed)
GET  /api/events/connect/<app>          → OAuth: 302 to consent · token: instructions
GET  /api/events/connect/<app>/callback → OAuth code exchange → create the user's AP connection
POST /api/events/connect/<app>/token    → token apps (GitHub PAT / Telegram bot)
GET  /api/events/connections            → the caller's own connected integrations
```
Connect model: **CUGA hosts the login; AP holds the token** (decision 0006). Per-user OAuth apps
need `EVENTS_OAUTH_<APP>_CLIENT_ID/_SECRET`; token apps work out of the box.
Descriptors + status logic: `connectors.py` (channels/integrations) and `catalog.py` (examples)
— the single source of truth, server-side.

## Isolation
Every read endpoint resolves the caller's `Principal` from headers → `scope`, and filters by it
(subscriptions by tenant; integrations by the principal's AP project). The UI shows only the
caller's own state. See [decisions/0002-tenancy-and-isolation.md](decisions/0002-tenancy-and-isolation.md).

## Build & serve
The frontend is a **pre-built webpack bundle** served by FastAPI's catch-all from
`src/cuga/frontend/dist`. Editing any `*.tsx` does **nothing** until you rebuild + republish and
restart the server. Use the script (it installs deps, builds, and publishes to the served dir):
```bash
scripts/frontend_build.sh    # pnpm install + build + publish → src/cuga/frontend/dist
# then restart the CUGA server, and open the Studio at:
#   http://localhost:8100/studio     (served by the CUGA server on :8100)
```
`api.ts`'s `getApiBaseUrl()` returns `window.location.origin` (same-origin), so the bundle talks
to whatever host/port served it — no hardcoded port.

> Note: this repo ships a **pre-built** `src/cuga/frontend/dist` (no `node_modules` checked in).
> The Studio's `.tsx`/`.ts` sources are committed and compile as part of `scripts/frontend_build.sh`
> (a pnpm monorepo; the script enables pnpm via corepack if missing). **Rebuild after any `.tsx`
> change**, then restart the server.

See [TESTING.md](TESTING.md) → *Studio UI walkthrough* for the click-through.
