# 09 — Identity, profiles & permissions (making channel isolation real)

The builder enables channels/integrations; the **user profile is the identity anchor**. Linking a
channel from the authenticated profile makes the `native_id → principal` binding trustworthy.
Decision: [0007](../decisions/0007-identity-profiles-permissions.md). Code:
[`users.py`](../../src/cuga/backend/events/users.py), [`identity.py`](../../src/cuga/backend/events/identity.py),
[`perms.py`](../../src/cuga/backend/events/perms.py), `principal.resolve_channel`, endpoints in
[`app.py`](../../src/cuga/backend/events/app.py).

## Channel account-linking (Telegram/Discord)
```mermaid
sequenceDiagram
    autonumber
    participant U as Alice (logged in)
    participant PR as Profile (/api/events/me, /link)
    participant BOT as Channel bot (via AP)
    participant INV as POST /invoke
    participant IM as IdentityMap

    U->>PR: Link Telegram
    PR->>IM: issue_token(tenant, alice, telegram)
    IM-->>PR: token
    PR-->>U: "open t.me/Bot?start=<token>"
    U->>BOT: /start <token>   (from telegram id 12345)
    BOT->>INV: channel message {thread: gw:telegram:12345, text: "/start <token>"}
    INV->>IM: redeem_token(token, "12345")
    IM-->>INV: bound → (tenant, telegram, 12345) = alice
    INV-->>U: "Your account is linked."
```

## Runtime resolution + permissions (per message)
```mermaid
sequenceDiagram
    autonumber
    participant BOT as Telegram (via AP)
    participant INV as POST /invoke
    participant IM as IdentityMap
    participant CO as Concierge (router)
    participant US as UserStore

    BOT->>INV: message from telegram:12345
    INV->>IM: resolve(tenant, telegram, 12345)
    IM-->>INV: alice → scope acme/inst/alice
    INV->>CO: route as alice
    CO->>US: roles(alice) = [user]
    Note over CO: list_capabilities filters agents by perms —<br/>alice sees open agents, NOT market_briefer([builder,admin])
    CO-->>BOT: answer / arm flow / "connect" / decline
```

**Verified live (8/8, `live_identity_check.py`):** alice=user vs admin profiles; **alice can't see
the restricted `market_briefer`, admin can**; a Telegram `/start <token>` binds `telegram:12345 →
alice`, and `/me` then shows the linked channel. Offline: `test_events_dimensions` covers
UserStore, IdentityMap + link tokens, perms, agent_scope vs per-user scope, and channel resolve.

**Key split:** agents are **tenant-shared** (`Principal.agent_scope`); threads/subscriptions/
connections are **per-user** (`Principal.scope`). Grain still follows credentials (0006).
