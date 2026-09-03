# 0009 — Isolation and credential grain (channels · GitHub · Box)

**Status:** accepted for v1 · **Supersedes nothing** · Related: `0003-credentials-ownership.md`, `0007` (identity map)

## Context

The target integration set is **channels (Slack · Discord · Telegram · web), GitHub, and Box**.
Gmail/Outlook are explicitly deferred. That scope choice makes every credential *app-level*, which
in turn changes what does and does not enforce authorization.

## Decision

**1. All credentials are per-TENANT and static.** No per-user secrets, no writable secret store, no
refresh loop, no locking.

| | Stored (static) | Minted at runtime |
|---|---|---|
| Slack | `SLACK_BOT_TOKEN` + `SLACK_SIGNING_SECRET` | — |
| Discord | `DISCORD_BOT_TOKEN` | — |
| Telegram | `TELEGRAM_BOT_TOKEN` | — |
| GitHub | App ID + RSA private key + webhook secret | JWT (10 min) → installation token (1 h) |
| Box | CCG `client_id` + `client_secret` + subject | access token (1 h), **no refresh token** |

Bot tokens do not expire. GitHub and Box mint short-lived tokens on demand from a static secret
("app auth"), so nothing rotating is ever stored. The existing **read-only** resolver
(`vault://`, `aws://`, `db://`, `env://` via `secret_seam`) is sufficient — note only
`VaultBackend` has a `set()`, so a rotating store would need new write paths. We avoid needing one.

**2. Identity is PER-USER, and linking is mandatory.** Flows belong to the person who armed them.
`Principal.user_id` must resolve to a real authenticated CUGA user; `"local"` is a bug, not a
setting.

- **Web**: `user_id` comes from CUGA's authenticated session (`current_user.sub`, OIDC).
- **Channels** (Slack · Discord · Telegram · WhatsApp): the sender must **link** their channel
  account to their CUGA login once, via the existing `IdentityMap` link-token flow.
- **Unlinked senders are refused** — not silently downgraded to a shared principal. The refusal is
  a helpful prompt ("log in to CUGA and send me this code"), not an error.

Rejected alternative: deriving a pseudo-user from the channel-native id (`slack:U123`) for unlinked
senders. It gives isolation for free and needs no login, but it creates a second identity per human
per channel, orphans flows when they later link, and — decisively — leaves an unauthenticated
stranger able to run agents against app-level credentials. Isolation without authentication is
bookkeeping, not security.

**Refuse arming AND chat for unlinked senders.** Because credentials are app-level (decision 1), a
single unlinked question ("summarise the newest file in /HR") already reads service-account data.
Gating only the standing flows would leave the larger hole open.

**3. `scope` stays plumbed end to end** — poll body → `/invoke` → subscriptions → agent store. It
already is. Turning isolation on later is enabling a filter, not building one.

## What single-scope does NOT break: replying to the right person

Worth stating explicitly, because it is the first thing people assume is broken. **Delivery is
already per-person and does not depend on `scope`.** At arm time the concierge captures the
caller's channel-NATIVE id from the thread and stores it on the subscription row
(`deliver_target` / `deliver_direct_target`). Ten people messaging one WhatsApp Business number
produce ten rows with ten different targets; a fire replies to the number that armed it, even
though every one of them resolves to `user_id="local"`. The reply target is captured from the
ORIGIN, never derived from the principal.

The line is therefore:

| | Needs identity? |
|---|---|
| reply to where it came from · conversation threading | **no** — native id on the row |
| reply to a DIFFERENT channel ("arm from WhatsApp, post to Slack") | **yes** |
| "email me" / "me" as a person rather than a chat | **yes** |
| flow ownership + visibility · data access | **yes** |

So a shared bot with same-channel delivery works correctly today. It is cross-channel delivery,
"me", and authorization that need the identity map.

## THE CONDITION THIS BREAKS UNDER — read this before widening the channel

`perms.py` documents the invariant the authorization model rests on:

> The other permission axis — 'may you act on this data?' — needs no check here: **per-user
> connections are keyed to the principal, so a user can only ever act on their own data.**

**App-level credentials make that sentence false.** With a Box service account or a GitHub App,
every user acts with the *app's* access, not their own. The credential stops being the permission
check, and nothing replaces it: `perms.can_use` gates only *which agent* you may talk to
(`AgentSpec.access`, empty = everyone by default), and there is **no allowlist on who may message a
bot at all**.

Two individually-reasonable decisions — app-level credentials (cheap) and single scope (simple) —
compose into: **anyone who can reach the bot can read anything the service account can read.**

That is tolerable only while the channel is closed. It breaks the moment:

1. **A channel is reachable by people outside the trust boundary** (WhatsApp/Telegram by phone
   number, a public Slack Connect channel, a Discord invite). They resolve to `local` like everyone
   else and inherit full service-account access. A WhatsApp **Business** number is the sharpest
   case: many senders, one bot, and identity is only a phone number — replies still route
   correctly (see above), but every sender shares one authorization and one flow list.
2. **The service account can see anything not everyone may see** — HR, finance, private repos. CUGA
   then *is* the escalation path: the only gate is being able to type `/automate`.
3. **Two teams share one deployment** — shared flows become leakage rather than a feature.

## Implementing mandatory linking — what exists, what is missing

**Already built (isolation is not the work):**

- `_owned_sub` gates pause/resume/delete on `sub.tenant == caller scope`; the list endpoint passes
  `store.list(scope=…)`. Ownership enforcement is real — it is only trivially satisfied today
  because every caller resolves to `local`.
- `/invoke` already calls `principal.resolve_channel(channel, native_id, identity_map)` and uses
  the linked principal's scope when it resolves; it logs `channel.unlinked` otherwise.
- `IdentityMap` has `issue_token` / `redeem_token` (15-min TTL, single-use) and `link` / `resolve`.
- CUGA has OIDC and `current_user.sub`.

**The four gaps:**

1. **Core never forwards the authenticated user.** `events_bridge` *relays* `X-User-Id` if the
   inbound request carries it — and a browser never does. It must be **injected** from
   `current_user.sub` (plus the tenant claim) when core forwards to the events service. Without
   this the web side authenticates and then discards the identity.
2. **No link UX.** Needs an authenticated endpoint to issue a code, and a redeem branch on the
   inbound channel path (Telegram deep-link `?start=<token>`, `/link <code>` elsewhere).
3. **The gate itself.** At the `channel.unlinked` branch, stop falling through to header
   resolution; return the link prompt instead.
4. **Link tokens are stored in plaintext** — `link_token.token` is the primary key, so a database
   read yields live bearer tokens. Store `sha256(token)` and look up by hash. Also raise
   `secrets.token_urlsafe(8)` (64 bits) to 16, and rate-limit redemption.

**Where credentials live under this decision — unchanged.** Linking introduces **no new long-lived
secret**, so the vault story does not move:

| | Sensitivity | Storage |
|---|---|---|
| provider secrets (bot tokens, GitHub App key, Box client secret) | long-lived, high | `secret_seam` → `vault://` / `aws://` (already) |
| OIDC client secret | long-lived, high | same resolver |
| link tokens | 15-min single-use bearer | **hash at rest** — a verifier, not a secret to retrieve |
| identity mappings (`native_id → user_id`) | not secret | plain table |
| per-user OAuth tokens | — | none; deferred with Gmail/Outlook |

## Mitigations, in the order they buy the most

1. **Scope the service account down** to exactly the folders/repos the whole audience may see.
   Cheapest, and it holds even if everything else is wrong.
2. **Keep bot channels closed** — invite-only workspaces; do not expose Telegram/WhatsApp bots to
   arbitrary senders while credentials are app-level.
3. **Wire auth → `user_id`** (now decision 2, mandatory). NOTE: do **not** add a per-user `scope=`
   filter to `direct_events.match`. A watcher must fire on events caused by OTHER people — Alice
   arms "watch #eng for :bug:", Bob reacts, Alice's watcher fires and delivers to Alice. Filtering
   there by the event author's scope would break that. Isolation at fire time is per-TENANT, not
   per-user, and `sub.tenant` currently stores the full `tenant/instance/user` string, so a tenant
   filter needs a prefix match or its own column.
4. **Box `box_subject_type=user`** with an authenticated `(tenant, "box", cuga_user_id) → box_user_id`
   mapping. Restores real per-user permissions *without* per-user secrets, because the subject
   varies while the client secret does not — Box then enforces access, and a user cannot watch a
   folder they cannot read. Requires Box enterprise + admin authorization.

## Consequences

- v1 ships with no token subsystem and no identity work — the cheap path, deliberately.
- The security posture depends on **the service account's scope** and **who can reach the bot**,
  not on CUGA's own checks. That must be stated wherever the deployment is documented.
- Adding Gmail/Outlook for *per-user mailboxes* is the point where per-user rotating credentials
  become unavoidable; build the store once, for both, at that time.
