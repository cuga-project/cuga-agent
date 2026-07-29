# 0003 — Credential ownership: shared vs per-user

## Context
"Setting up an integration on an agent" is ambiguous: does the *builder* provide the Gmail
credential (so all users share it), or does each *chatting user* connect their own? Both are
valid and needed — a builder can't hand over 500 employees' personal tokens, and a shared team
inbox shouldn't need per-user connects.

## Decision
Each integration on an agent declares a **`credential_ownership`**:

| | `shared` (service account) | `per-user` |
|---|---|---|
| Whose account | the agent's / team's (one) | each chatting user's own |
| Who authorizes | the **builder**, once, at agent creation | **each user**, at runtime (OAuth/token) |
| Stored in | CUGA **secret** (`vault://…`, worker-side) and/or an AP **connection** scoped to the agent | AP **connection** `ea::<tenant>::<user>::<app>` (AP holds + refreshes) |
| Connection externalId | `ea::<tenant>::agent::<agent>::<app>` | `ea::<tenant>::<user>::<app>` |
| Chatting user does | nothing — inherits the shared cred | **just-in-time connect** on first use |

- **The agent config carries a *binding*, not always the token** — "integration = gmail,
  ownership = shared|per-user". At runtime the events layer resolves it to a concrete connection
  externalId via `credentials.connection_external_id(app, ownership, principal, agent)`.
- **Where creds physically live:** AP steps (triggers/actions) always reference an **AP connection**
  (AP manages OAuth refresh — a reason to lean on AP). Worker-side shared secrets may be a **CUGA
  secret**. Per-user creds are AP connections keyed by principal.
- **Just-in-time connect (per-user):** when a chat needs an app the user hasn't connected, the
  concierge replies "connect your `<app>`" + the AP connect URL, then proceeds. (Same
  capability-envelope pattern as "tell me what to connect.")
- **OAuth vs token:** OAuth apps (Gmail/Box) are authorized in AP's connect UI (can't be minted
  headlessly). Token apps (Telegram, Discord bot tokens) paste a token via the API. GitHub is **OAuth**,
  NOT a pasted PAT — AP's piece-github accepts only OAUTH2 (see ADR-0006). *(Amended by
  [ADR-0008](0008-direct-backends-for-channels.md): **Discord** and **Slack** default to **direct**
  CUGA backends holding a `.env` bot token, not an AP connection; the AP token path stays as an opt-in.)*

## Verified live
`live_credentials_check.py`: `shared` → alice & bob resolve to the **same** connection
(`ea::acme::agent::mailbot::telegram`); `per-user` → **distinct** connections
(`ea::acme::alice::telegram` ≠ `…bob…`); an unconnected user (charlie) → **needs a connect**.

## Consequences
- The builder chooses ownership **per integration** (e.g. BofA "internal support" = shared team
  inbox; "my email assistant" = per-user).
- CUGA today provides only `shared` (agent-level secrets); **per-user is the new capability** the
  events layer + AP add (Phase 3).
- The agent-builder UI needs a "shared vs per-user" toggle per integration (TODO / UX).
