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

**2. Identity runs single-scope for v1.** `Principal.user_id` stays `"local"`; this is a deliberate
setting, not an unfixed bug. Consequences, accepted knowingly:

- every armed flow is visible to everyone in the Studio, and anyone can cancel anyone's
- conversation memory is shared per thread key
- every watcher fires for every user's events (`direct_events.match` already ignores scope)
- "my Box folder" means the **service account's** folders, so users must share folders with it

**3. `scope` stays plumbed end to end** — poll body → `/invoke` → subscriptions → agent store. It
already is. Turning isolation on later is enabling a filter, not building one.

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
   else and inherit full service-account access.
2. **The service account can see anything not everyone may see** — HR, finance, private repos. CUGA
   then *is* the escalation path: the only gate is being able to type `/automate`.
3. **Two teams share one deployment** — shared flows become leakage rather than a feature.

## Mitigations, in the order they buy the most

1. **Scope the service account down** to exactly the folders/repos the whole audience may see.
   Cheapest, and it holds even if everything else is wrong.
2. **Keep bot channels closed** — invite-only workspaces; do not expose Telegram/WhatsApp bots to
   arbitrary senders while credentials are app-level.
3. **Wire auth → `user_id`.** Then add `scope=` to `direct_events.match`'s `store.list(...)` (the one
   read path that omits the available isolation filter). Pointless before this — with everyone at
   `local` the filter matches everything.
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
