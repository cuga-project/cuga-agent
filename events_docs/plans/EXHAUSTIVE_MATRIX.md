# Exhaustive verification matrix — arm → FIRE → answer-verified

User directive (2026-07-16): "not just arming … arm the flow, but also fire and see if you're able
to get a response … be as exhaustive as possible … I just don't want a half-baked situation where
arming works but the flow doesn't execute, or executes and gives crap."

## What "verified" means (three gates per case, not one)

1. **ARMED** — the flow really exists (AP flow id / direct subscription), correct trigger + slots.
2. **FIRED** — a real or piece-exact synthetic event traverses the REAL path
   (trigger → AP/gateway → `/invoke` → supervisor → delegate → sink).
3. **ANSWER QUALITY** — the reply contains the case's expected facts (`expect_any`) and none of
   the failure signatures (`forbid`): executor scaffolding ("## New Variables Created",
   "Execution output:"), deliberation leaks ("We have a loop"), refusals ("I'm unable to"),
   loop-attempts ("sleep("), connect-prompts when connected. Synthetic payloads carry planted
   facts (unique markers) so the assertion is deterministic, not vibes.

## The matrix (dimension coverage, not blind cross-product)

27 agents × 33 triggers × 4 channels ≈ 3,500 cells is noise — most cells are meaningless
(pricebot × box/new_folder). The honest target: **every dimension value covered at least once,
every meaningful pairing covered exactly once** ≈ **75–85 cases**:

| Dimension | Coverage |
|---|---|
| **33 triggers** | one armed+fired case each, mapped to its HANDLES agent (pr_reviewer←new_pr, resume_judge←box/new_file, mailbot←new_email, incident_triage←new_issue, …); registry-generated so a new trigger FAILS the build until it has a case |
| **27 agents** | every agent answers at least one NOW case (its signature utterance from catalog.py), routed through the supervisor; the 10 HANDLES agents additionally get their trigger fires |
| **4 channels inbound** | chat probe per channel (web `/stream` + `/api/concierge`, Slack signed event, Discord gateway-shaped `/invoke`, Telegram AP-shaped `/invoke`) + slash-arm per channel (`/automate` — the web one just broke silently; now every channel gets the probe) |
| **4 channels as sinks** | one fired flow delivering per channel, delivery confirmed by reading the channel back (Slack API / Discord API / Telegram getUpdates / web capture) |
| **modes** | cron real tick · poll real tick · bounded (TTL) self-delete · push per trigger · webhook pinned + routed · NOW |

## Fire mechanics per backend (what's synthetic, what's real, what's blocked)

| Surface | Synthetic fire (machine, every run) | Real fire (genuine external event) |
|---|---|---|
| github ×14 | piece-exact webhook payloads → real AP runs (proven 14/14) | repo API on anupamamurthi/pachyderm: star/branch/commit/label/milestone/release/issue automatable NOW; **PR needs PAT Pull-requests:R/W (user, 1 click)**; collaborator/mention need a 2nd account → synth-only, marked |
| gmail ×4 | `/invoke`-seam envelope with hostile payload (proven; leak-gate) | new_email proven organic; labeled/attachment need crafted emails — **user sends 2 emails** or synth-only, marked |
| box ×3 | `/invoke`-seam envelope | **BLOCKED on fresh BOX_DEV_TOKEN (user; 60-min expiry — send it at run start)**; then upload/folder/comment via API (proven pattern) |
| slack ×8 | HMAC-signed Events-API POSTs, byte-identical (proven 4/4 → extend to 8/8) | bot-actable: post/reaction via API where author≠bot required is satisfiable; else human, marked |
| discord ×2 | **gap: no Gateway injector** → build one (feed dispatch dicts to the real handlers) + `/invoke`-seam | new_member needs a real join (user/test account), marked |
| telegram ×1 | `/invoke`-seam with pattern-matching payload | needs one human message to the bot, marked |
| webhook | fully real every run (plain POSTs) | same thing — webhook IS machine-real |
| cron/poll/TTL | real AP schedule ticks (proven); TTL flows self-clean | same thing |

## The harness: `tests/events/live_exhaustive.py` (`make test-exhaustive`)

- **Case table generated** from `triggers.rows()` × `supervisor_agents.yaml` × `catalog.py` — a
  new trigger/agent with no case entry fails the consistency gate (same pattern as the corpus
  cadence gate). Hand-written `expect_any`/`forbid`/`payload` per case.
- **Phases**: preflight (doctor incl. the AP_FRONTEND_URL check; abort early on dead infra) →
  arm-all (reuse dedup) → fire (parallel where independent; pollers armed first, collected last)
  → verify answers (per-case gates + global leak scan of every answer) → deliver-legs → cleanup
  (delete everything the run created; subscriptions before == after) → report + ledger stamps
  (per-trigger cells, not per-surface).
- **Runtime**: ~60–90 min (gmail/box polling waits dominate; everything else minutes). Wired into
  `make test-report ARGS="--exhaustive"` as an opt-in stage.
- **Honest output**: every non-machine-real leg prints as SYNTH or BLOCKED(what unblocks it) —
  never counted as a real-fire pass. The ledger gains per-trigger rows (33) replacing the
  7 per-surface rows, each with synth/real columns.

## Pre-req fixes shipped this session (would have failed the matrix)

- web `/stream` slash short-circuit (plain agent generated loop+sleep code for `/automate`).
- Discord mention-gate (EVENTS_DISCORD_CHAT=mention; watchers still see gated traffic).
- push one-shot framing + payload field caps + cadence LLM rewrite + TTL bounded flows.

## Known gaps the matrix will EXPOSE but not fix (separate work items)

1. **Stateful poll** (bug 2): "only if it changed" reports every tick — poll cases assert only
   single-shot answer quality until the state store exists.
2. **Supervisor deliberation-leak guard** (runtime): the matrix's `forbid` scan catches leaks in
   tests; production needs the guard in `/invoke` before delivery.
3. **AP tunnel flap**: doctor detects it; a stable AP URL (2nd reserved domain) or internal-URL
   split is the durable fix — the matrix aborts early (preflight) instead of failing 30 cases.
4. **gmail send action**: `draft` prop + OAuth send-scope refresh fails — blocks the email sink
   and self-sent trigger emails.

## What is needed from Anu (the full list, once)

1. Fresh **BOX_DEV_TOKEN** at run start (60-min window covers the box legs if they run first).
2. GitHub PAT **Pull requests: Read and write** (unblocks the PR real-fire, still pending).
3. During the run (~5 min of attention, optional but upgrades SYNTH→REAL): one Telegram message
   to @time4fun_bot · one Discord @mention + one plain message (gate check) · 2 emails to
   cugatest@gmail.com (one with attachment, one to be labeled) · one Slack 🐛 reaction.
4. Decide: 2nd test account for github collaborator/mention + discord member-join, or accept
   synth-only for those 3 cells.
