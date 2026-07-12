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

## Live — start here
Prereqs: `make up` (CUGA on `:8100`, AP on `:8081`) + `make doctor` (creds). Then:

```bash
make test-live                 # 4 channels + 4 flow modes, ~1-2 min, self-cleaning
#   ↳ scope it:  make test-live ARGS="--only channels"   or   ARGS="--only flows"
make test-suite-now            # EVERY seeded agent, invoked directly (~13 min)
make test-suite-flows          # cron + poll + push (~5 min)
make test-matrix               # EVERY trigger mode × EVERY channel sink × EVERY integration (~3-5 min)
```

`make sync` is **not** needed — the live harnesses are pure stdlib. Don't run bare `make test-suite`:
its 1500 s budget is shorter than NOW + channels + flows, so the tail silently skips. Use the phase
targets above, or `SUITE_BUDGET_SECS=2400 make test-suite`.

`live_suite.py` (`make test-suite`) is the **behavioural** harness: every agent answers a NOW question
(asserted against `meta.mcp`, so an agent can't pass by answering from the model's memory instead of
calling its tools), then every flow mode arms. It reports **four** outcomes, not two —
`PASS` · `FAIL` · `XFAIL` (a logged gap, reason printed) · `XPASS` (a gap just closed → go delete the
expectation). Only `FAIL` reddens the bar. The agent catalog, with each utterance and what it is
expected to do, is [AGENT_NOW_CATALOG.md](AGENT_NOW_CATALOG.md).

`live_matrix.py` (`make test-matrix`) arms **every cell of the grid** — each mode and each integration
against each of the four sinks — by POSTing the exact envelope that channel's transport posts, because
`find_or_create_flow` derives the sink from the caller's `thread_id`. A cell is not pass/fail; it
records `✓ armed · ≡ reused (dedup) · ? needs-input · ⚠ connect-needed · ! claims-existing-but-none ·
✗ error · – skip`. Only `✗` fails the run: needs-input and connect-needed are *correct* behaviours.
Optional `GITHUB_TEST_REPO=owner/repo` arms the github row (it creates a **real repo webhook**, which
cleanup removes); `BOX_FOLDER_ID` stops the box watcher asking which folder.

`live_e2e.py` is the **canonical live harness**: it sends a real message on every channel, validates
the answer, arms every flow mode, verifies the Activepieces flow exists, then **deletes only the
subscriptions it created**. Missing creds are `SKIP`, not `FAIL`. Three things it does that a naive
harness gets wrong:

- **It probes AP directly before trusting a `CONNECT NEEDED` reply.** The connect gate reports
  "connect your credentials" when AP is merely unreachable, so without this probe every PUSH leg
  false-passes when AP is down. See `events_docs/GAPS.md` §2.
- **It distinguishes a *new* subscription from a *reused* one.** `find_or_create_flow` de-duplicates
  on `dedup_key`, so re-arming the same intent legitimately creates nothing. Matching on mode alone
  (as the older harness does) lets a leftover flow from a previous run mask a broken arm.
- **It matches integration watchers by `source_connector`, not by mode.** With
  `EVENTS_BOX_BACKEND=direct`, a Box *push* request correctly arms `mode=POLL`
  (`box-poll-resume_judge`) because the direct backend polls Box's API.

How real is each channel leg — `web` and `slack` are full round trips (Slack posts a real message and
sends a byte-identical Events API callback; the reply is read back from `conversations.replies`).
`discord` and `telegram` are partial: a bot cannot send itself a message, so the transport hop
(Gateway socket / Telegram→AP webhook) is simulated by driving the exact `/invoke` envelope it posts,
while the **outbound** send is real and verified against the platform. The harness prints which is which.

## Live — one canonical harness per surface

| Surface | Harness | Proves |
|---|---|---|
| **Channels + all trigger modes** | **`live_e2e.py`** ⭐ | what `make test-live` runs — see above |
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
