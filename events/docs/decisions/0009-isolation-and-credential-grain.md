# 0009 — Isolation, identity, and credential grain

**Status:** accepted · Related: `0003-credentials-ownership.md`, `0007` (identity map)

Supersedes two earlier positions reached while working this through, both recorded here because the
reasoning matters: (a) *single shared scope* — rejected, it makes flow ownership meaningless;
(b) *mandatory CUGA login for every channel sender* — rejected, it is nonsense for a public
WhatsApp Business number and it conflates two different problems.

## Context

Target integrations: **channels (Slack · Discord · Telegram · web · WhatsApp), GitHub, Box.**
Gmail/Outlook deferred. That scope makes every credential *app-level*, which changes what enforces
authorization — and forces the question of who a channel sender actually is.

## The insight this rests on

**A channel is an authentication authority.** WhatsApp verified that person controls that phone
number; Slack authenticated them into the workspace; Telegram and Discord verified their accounts.
A channel-native id is therefore a **real principal, authenticated by a different authority than
CUGA** — not an anonymous placeholder.

That separates two concerns that were being conflated:

| Concern | What it actually needs |
|---|---|
| Alice must not see Bob's flows | a channel-verified identity — **no CUGA login** |
| May this person read `/HR`? | a CUGA identity **and** a privileged agent |

**Isolation never needed a login. Authorization did.**

## Decision

**1. Credentials are per-TENANT and static.** No per-user secrets, no writable secret store, no
refresh loop, no locking.

| | Stored (static) | Minted at runtime |
|---|---|---|
| Slack / Discord / Telegram | bot token (+ Slack signing secret) | — |
| GitHub | App id + RSA private key + webhook secret | JWT (10 min) → installation token (1 h) |
| Box | CCG `client_id` + `client_secret` + subject | access token (1 h), **no refresh token** |

App auth mints short-lived tokens from a static secret, so nothing rotating is stored. The existing
**read-only** resolver (`vault://`, `aws://`, `db://`, `env://` via `secret_seam`) suffices — only
`VaultBackend` has `set()`, and we deliberately avoid needing a write path.

**2. Isolation is ALWAYS ON, for everyone.** Every sender gets a distinct principal derived from
their channel-verified native id (`whatsapp:+4477…`, `slack:U123`). Not optional, not a mode. Alice
cannot see, pause or delete Bob's flows, whether or not either has a CUGA account.

Nothing new is needed downstream: `_owned_sub` already gates pause/resume/delete on
`sub.tenant == caller scope`, and the list endpoint already passes `store.list(scope=…)`. Those
checks are real; they are merely trivially satisfied today because every caller resolves to
`"local"`. Giving senders distinct principals is what makes them bite.

**3. Linking is an UPGRADE, not a gate.** Linking a channel account to a CUGA login merges the
channel principal into the CUGA user and unlocks **privileged agents**. An unlinked sender is a
first-class user of bounded agents — they can chat, and they can arm flows, scoped to themselves.

**4. Privileged vs bounded is a property of the AGENT, not the channel.** An agent holding
app-level integrations (Box, GitHub) reads with the service account's permissions, so it requires a
linked CUGA identity. An agent with only public tools requires nothing. Deploy them as *different
agents* — `helpdesk` in WhatsApp/#general, `assistant` in DMs — rather than one agent that behaves
differently by context. Default **privileged**: an agent must be explicitly bounded to be exposed
anonymously, and marking one bounded while it still holds privileged integrations must be refused,
not merely warned about.

## THE PREREQUISITE: inbound must be un-spoofable

Channel-verified identity is worth exactly as much as the proof that the message came from the
channel. If a webhook accepts `from: +44-alice` without verification, anyone can *be* Alice and
every isolation guarantee above is void.

Current channels are sound, for two different reasons:

- **Telegram** — long-poll (`getUpdates`). Outbound; there is no inbound endpoint to forge.
- **Discord** — Gateway WebSocket. Outbound; same.
- **Slack** — inbound webhook, but `slack_direct.verify_signature` enforces the signing secret.

**WhatsApp is inbound and therefore MUST verify Meta's `X-Hub-Signature-256` before any of its
identities may be trusted.** That is the real prerequisite for the WhatsApp scenario — not a login.

## Known hazard: recycled phone numbers

Mobile numbers are reassigned. A new owner of a recycled number inherits the previous owner's
principal, and with it their flows and conversation history. Slack/Discord/Telegram ids do not have
this property; **WhatsApp does**, and it is the one place where a channel-verified id decays.

Mitigations: expire idle flows, and re-confirm identity after a long silence before delivering
anything personal. Do not let a standing flow keep firing to a number that has been quiet for
months.

## Consequences

- Ships with no token subsystem and no mandatory login — the cheap path, and now also the correct
  one.
- Isolation is unconditional, so "flows are shared" is never true.
- The security boundary for privileged data is **agent classification + linking**, and for
  everything else it is **the channel's own authentication**. Both must hold; neither alone is
  enough.
- `perms.py` states the old invariant — *"per-user connections are keyed to the principal, so a
  user can only ever act on their own data."* App-level credentials make that false, and decision 4
  is what replaces it. Update that docstring when agent classification lands.
- Adding Gmail/Outlook for **per-user mailboxes** is where per-user rotating credentials become
  unavoidable. Build that store once, for both, at that point.

## Implementation notes

Already built: `_owned_sub`, scope-filtered listing, `resolve_channel`, `IdentityMap`
(`issue_token`/`redeem_token`, 15-min single-use), CUGA OIDC.

Gaps:

1. **Core never forwards the authenticated user.** `events_bridge` *relays* `X-User-Id` if the
   inbound request carries it — a browser never does. Inject `current_user.sub` instead.
2. **Unlinked channel senders fall through to `"local"`.** At the `channel.unlinked` branch, derive
   the principal from the channel-verified native id instead.
3. **Merge on link.** When a channel principal links, re-key its subscriptions to the CUGA scope or
   they orphan. `IdentityMap.link()` is the place.
4. **Link tokens are plaintext** — `link_token.token` is the primary key, so a DB read yields live
   bearer tokens. Store `sha256(token)`; raise `token_urlsafe(8)` (64 bits) to 16; rate-limit
   redemption.
5. **Do NOT add a per-user `scope=` filter to `direct_events.match`.** A watcher must fire on events
   caused by *other* people — Alice arms "watch #eng for :bug:", Bob reacts, Alice's watcher fires
   and delivers to Alice. Isolation at fire time is per-TENANT, not per-user; `sub.tenant` holds the
   full `tenant/instance/user` string, so a tenant filter needs a prefix match or its own column.

Credential storage is unchanged by any of this: provider secrets stay in the vault-backed resolver;
link tokens are **hashed, not vaulted** — they are verifiers, never read back.
