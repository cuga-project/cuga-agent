# 0007 — Identity, profiles & permissions (making channel isolation real)

## Context
Isolation by `Principal → scope` is correct **once you have a principal**. The gap: a message from
Telegram/Slack/Discord arrives with a **channel-native id** under a **shared bot**, not a CUGA
user. We need to resolve native id → principal, and gate which agents a user may use.

## Decision
**The user profile is the identity anchor.** Tenant → Users → each user's Profile → the channels
they've linked + the integrations they've connected. Three surfaces, three levels of choice:

| Surface | Who | Sets |
|---|---|---|
| **Admin** | tenant owner | creates the tenant, adds users, sets up **tenant-level** channel connections (the bot) + registers OAuth apps |
| **Builder** | agent author | which channels/integrations each agent may use + **who may use the agent** |
| **Profile** | end user | **links** their channels + **connects** their own integrations |

### Identity resolution (native id → principal)
- **Web** → CUGA session (OIDC `sub`, or local user store).
- **Slack** → workspace-scoped; Slack user's **email** → match a user → link.
- **Telegram / Discord** → no corporate identity → **account linking** initiated **from the
  authenticated profile** (so the binding is trustworthy): profile issues a one-time token; the
  user sends it to the bot (Telegram deep-link `?start=<token>`, Discord code); the inbound flow
  posts it to `/invoke`, which binds `(tenant, channel, native_id) → user`.
- Stored in an **identity map**: `(tenant, channel, native_id) → user_id`. Once linked, every
  channel resolves to the same principal → one set of connections across web + channels.
- Unlinked user → the concierge replies "link your account: <url>" (or a limited anonymous mode).

### Connections hang off the profile (not per agent)
A per-user connection is `ea::<tenant>::<user>::<app>` (agent-agnostic), so a user connects Gmail
**once** in their profile and every agent they're allowed to use reuses it.

### Permissions (two kinds)
1. **May you talk to this agent?** → a per-agent access rule (`AgentSpec.access = roles/users`),
   checked in the router (`list_capabilities` filters; `answer_now`/`find_or_create_flow` deny).
2. **May you act on this data?** → enforced automatically by connection ownership (per-user = only
   your data). No extra check needed.

### Users
A light **local user store** (admin adds users; optional password) **and** reuse CUGA's OIDC for
SSO deployments. Single-tenant self-host (BofA) → tenant fixed; the problem reduces to
user-resolution + agent-permissions.

### AP is the connector plane
Channel **connections** (bot tokens) + **inbound** (channel trigger → `/invoke`) + **delivery**
(send steps) all live in **AP**; CUGA does identity resolution + routing + linking. Integrations
same (AP holds tokens; [0006](0006-auth-connection-model.md)).

## Consequences
- The one new backend concept is the **identity map** + a **resolve step** at the gateway; the
  rest reuses `Principal`/scope, per-user connections, and the router.
- Grain still follows credentials ([0005](0005-runtime-router-over-prebuilt-agents.md)/[0006](0006-auth-connection-model.md)):
  once the principal is real, per-user flows/connections are real.
- Build order: identity+users+permissions foundation → channels via AP + linking → Profile/Admin UI.
