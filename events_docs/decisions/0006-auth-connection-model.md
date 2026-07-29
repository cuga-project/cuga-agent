# 0006 — Auth & connection model: enable (builder) vs log in (user)

## Context
"Set up an integration" conflates three things: the **capability** (may this agent use Gmail?),
the **credential** (whose token?), and **identity** (who is chatting?). Untangling them makes the
auth flow clear — and answers "the builder enables it, but the user logs in with their own
account."

## Decision
Three layers:

1. **Capability (builder, design time).** The builder enables integrations/channels per agent +
   ownership (`shared` | `per-user`). A declaration, not a credential. Stored on the agent config
   (`AgentSpec.integrations = [{app, ownership}]`, `AgentSpec.channels`).
2. **Connection (login).** The actual token, held + refreshed by **Activepieces**, keyed by
   externalId: `shared → ea::<tenant>::agent::<agent>::<app>` · `per-user → ea::<tenant>::<user>::<app>`.
   - **shared** — the builder authorizes once (service account).
   - **per-user** — each chatting user logs in with their own account, **just-in-time** on first
     use (the concierge relays a connect link).
3. **Identity vs credential.** **Channels** identify the user (one shared bot; user = chat id).
   **Integrations** act *as* the user → that's where per-user login matters.

**Deployment: self-host CE. Mechanism: CUGA hosts connect; AP holds the token.**
- **OAuth apps** (gmail/box/github): CUGA hosts `/api/events/connect/<app>` (redirect to
  consent) + `/callback` (exchange the code, create an AP OAUTH2 connection — AP refreshes it).
  *(Amended by [0008](0008-direct-backends-for-channels.md): **Slack** is NOT an AP OAuth
  connection — its bot token runs a direct backend, because AP's OAuth2 rejects a Slack bot token.
  "outlook" was speculative and is not a shipped integration.)*
  The platform registers an OAuth app per provider once
  (`EVENTS_OAUTH_<APP>_CLIENT_ID/_CLIENT_SECRET`, redirect `…/connect/<app>/callback`).
- **Token apps** (telegram / discord bot tokens): `POST /api/events/connect/<app>/token` → AP
  `SECRET_TEXT` connection. No redirect. **GitHub is NOT here** — its AP piece accepts only OAUTH2, so
  it goes through the OAuth flow above; `connect/github/token` **rejects** a pasted PAT with a `400`
  (github is registered `kind:"oauth"`, so the endpoint won't build a SECRET_TEXT connection).
- Connect UX by channel: **web** → popup; **telegram/discord** → a tappable link.

**Flow grain follows credentials.** A flow using only shared connectors is **per-tenant** (one
flow, dedup tenant-wide); any per-user connector makes it **per-user** (an AP trigger binds one
account, so N users' Gmail = N flows — by physics, not choice). The dedup key's owner-scope
encodes this automatically ([0005](0005-runtime-router-over-prebuilt-agents.md)).

## Verified
- Token path **live against real AP**: `POST /connect/telegram/token` (real bot token) →
  `ea::default::local::telegram` connection created; `GET /api/events/connections` lists it.
- Connect endpoints degrade cleanly: unconfigured OAuth → "set EVENTS_OAUTH_GMAIL_CLIENT_ID/…".
- OAuth registry + authorize-URL + state + redirect_uri unit-tested offline.

## Consequences / open
- Real **Gmail/Box OAuth** needs the platform's OAuth app (client id/secret) — the builder/admin
  registers it once with the provider; then per-user login works end to end.
- ⚠️ AP's OAUTH2 connection `value` schema shifts between versions — `ensure_oauth_connection`
  is marked VERIFY; adjust keys against the target AP build on first real OAuth connect.
- Worker-side use of a per-user token (a CUGA worker reading *your* Gmail via MCP) reuses the
  same connection; wiring the worker's tool to the per-user connection is the Phase-3 follow-up.
