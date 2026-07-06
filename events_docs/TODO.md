# Roadmap — what's next

What's *built* is in [README.md](README.md) (status table); what's *deferred with a plan* is in
[KNOWN_GAPS.md](KNOWN_GAPS.md). This file is the forward-looking work, grouped by when you'd need it.

## Before a real (multi-tenant) deployment
- [ ] **Authenticate the `/invoke` scope.** Today the seam trusts a caller-asserted `scope` behind
      the gateway token. Add signed scope claims / an upstream IdP, and wire `current_user.sub` →
      `Principal` at the mount (currently header/env-based). *(Top security item — see KNOWN_GAPS §C.)*
- [ ] **Sign the OAuth `state`** (nonce + HMAC) before exposing OAuth publicly.
- [ ] **Turn on persistent stores.** `EVENTS_DB=<Postgres>` so agents + subscriptions survive
      restarts and are shared across replicas; wire a Postgres LangGraph checkpointer for memory.
- [ ] **Run Activepieces CE on Postgres** (not the single-container sqlite build).
- [ ] **AP project = tenant needs AP Enterprise** (CE = 1 project); else run `grain=shared`.

## Integrations — close the loop
- [ ] **Full PUSH fire e2e** for Gmail + GitHub (Box is proven): connect + a real event
      (open a PR / send an email) → verify the run fires and delivers.
- [ ] **Watcher passes file *content*, not just the name** — add a Box/Gmail download step before
      `/invoke` so `resume_judge` reads the actual document.
- [ ] **Worker-side per-user token** — a CUGA worker reading *your* Gmail via MCP should use *your*
      per-user connection, not a shared one.
- [ ] **OAuth provider config from AP piece metadata** — source auth/token URLs from AP so `oauth.py`
      hardcodes no provider specifics (the `PROVIDERS` table becomes a fallback).

## Platform / scale
- [ ] **`config_store` / `secrets_store` per-call `tenant_id`/`instance_id`** → first-class
      CUGA-native tenant isolation for the `cuga` worker backend.
- [ ] **Unify the two flow-builder paths** (`flows.py` dry-run JSON vs `ap_engine.py` live REST) so
      they can't drift.
- [ ] **Runs / history pane** in the Studio (AP run history surfaced in-app).

## Recently shipped (for context)
Direct Slack/Discord backends · direct-channel delivery · channel author identity + account-linking ·
the generic inbound webhook · the Studio **agent editor** (add/edit) + live Connected/Reconnect
status. See git history and the ADRs for detail.
