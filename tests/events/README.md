# events tests — what to run to prove each thing

Two tiers: **offline** (no server, no creds — the safety net) and **live** (real server + real
APIs). Full recipe + the coverage matrix: [../../events_docs/TESTING.md](../../events_docs/TESTING.md).

## Offline — run this first (60 green, ~0.5s)
```bash
.venv/bin/python -m pytest tests/events -q
```
| File | Covers |
|---|---|
| `test_events_core.py` (14) | envelope · classify · flow builders · subscriptions · concierge dry-run · eval oracle |
| `test_events_dimensions.py` (27) | connectors · credentials · isolation · per-mode flows · slack/discord direct · delivery selection · seed agents |
| `test_events_studio_api.py` (19) | the Studio API contract (TestClient): status/channels/integrations/agents CRUD/setup-guides · direct-channel delivery · box poll · webhook + key gate |

`preflight.py` is the **credential doctor** — reports which live creds are present in `.env`; never fails.

## Live — one canonical harness per surface
Prereqs: CUGA on `:8100`, AP on `:8081`, `GATEWAY_TOKEN`, watsonx creds. Then:

| Surface | Harness | Proves |
|---|---|---|
| **All trigger modes** | `live_integrations_e2e.py` | NOW/CRON/POLL/PUSH across Box/GitHub/Gmail + webhook |
| **GitHub** | `live_github_e2e.py` | real open PR → `pr_reviewer` reviews the real diff |
| **Box** | `live_box_e2e.py` | real upload → poll → `resume_judge` → cleanup |
| **Box (direct poll)** | `live_box_direct_check.py` | the AP-free direct poller path |
| **Gmail** | `live_gmail_e2e.py` | per-user OAuth connection + arms a real inbox-watcher flow |
| **Slack** (direct) | `live_slack_check.py` | token + delivery leg + arm the direct Events-API backend |
| **Discord** (direct) | `live_discord_check.py` | gateway + arm; Message-Content-Intent |
| **Telegram** (AP) | `live_telegram_check.py` | token (getMe) + delivery leg + arm via AP |

Cross-cutting (unique coverage): `live_isolation_check.py` (AP project isolation),
`live_identity_check.py` (two-user perms + channel linking), `live_credentials_check.py`
(shared vs per-user connections), `live_statefulness_check.py` (survives restart),
`live_phase2_watchers.py` (the only real timed CRON round-trip).

Utilities: `preflight.py` (credential doctor), `ap_nuke.py` (destructive AP cleanup of EA-tagged flows).

> **Legacy note:** `live_server_e2e_check.py`, `live_concierge_check.py`, and `live_stage2_channels.py`
> predate the canonical `*_e2e.py` set and default to port `7860` / `/api/concierge`. They still hold
> some unique router/dedup assertions; confirm the server contract before relying on them. New work
> should target the canonical harnesses above.
