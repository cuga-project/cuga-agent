# Direct-trigger → action (Option A) — status, gaps & TODOs

How a DIRECT trigger (slack/discord/telegram/box-direct) drives an AP-only action (e.g. gmail).
Built 2026-07-20. Design: [`../plans/CHANNEL_BACKEND_DECISION.md`](../plans/CHANNEL_BACKEND_DECISION.md).
Master list: [`../plans/ACTIONS_TODO.md`](../plans/ACTIONS_TODO.md).

Legend: ✅ done (offline-verified) · 🟡 partial · 🔒 blocked/live-pending · ⬜ open

## How it works (the executor)

A direct trigger owns no AP flow, so we can't hang an action step on it. Instead:

```
slack/discord/telegram message ─▶ CUGA runs the agent ─▶ CUGA POSTs to a reusable executor flow
                                                          (catch_webhook ▸ gmail/send_email)  ─▶ AP runs it
```

- **`actions.executor_input(action)`** — the executor flow's action input: dropdown/checkbox params
  baked as literals, everything else reads `{{trigger.body.<name>}}`.
- **`ap_engine.ensure_action_executor(...)`** — creates/reuses `exec-<app>-<action>` (idempotent by
  name), through the same **validity gate** as every flow (invalid → delete + raise, never a false ARM).
- **`actions.executor_body(action, supplied)`** — the per-fire JSON CUGA POSTs; answer-source params
  carry a `{{answer}}` sentinel.
- **concierge direct path** builds the plan at arm time (so the validity gate runs) and stashes it on
  `subscription.config["action_plan"]`. If it can't build (action app not connected) it **declines
  loudly** — never a silent plain watcher.
- **`direct_events.run_action_plan(engine, sub, answer, payload)`** — after the agent answers: picks the
  branch (EXECUTE_FIRST_MATCH, else fallback), substitutes `{{answer}}`, fires the executor
  (`engine.trigger_flow`). Wired into `dispatch_all`, and action-bearing watchers are routed there.

## ✅ Built & offline-verified

| Capability | Notes |
|---|---|
| ✅ slack → gmail action | `send_email` via executor; verb-align + verifier + validity gate all still apply |
| ✅ discord → gmail action | same path (discord gateway + slack events both pass `engine` to dispatch) |
| ✅ telegram → gmail action | rides the same inline-`/invoke` watcher seam automatically (no extra code) |
| ✅ Linear multi-action | `run_action_plan` runs every step in order |
| ✅ Branched (N-way) | Python-evaluated: content CONTAINS vs message+answer; from-address EQUALS vs sender |
| ✅ Decline-when-unbuildable | executor can't be built → loud decline, nothing armed |
| ✅ Tests | `test_events_direct_actions.py` (helpers + runner) + gate tests (arms-via-executor, declines) |

## Live-fire results (2026-07-20) — arm proven, run blocked

Ran against a live server + Activepieces (Gmail connected). Harness:
[`../../tests/events/live_direct_action_e2e.py`](../../tests/events/live_direct_action_e2e.py).

**✅ Proven live:**
- The full **arm path through the real concierge**: `"when a message posts in #alerts, email me a
  summary at …"` → `ARMED direct watcher (slack/new_channel_message) … then run gmail/send_email via an
  executor flow`.
- **Executor flow creation + the validity gate** — `ensure_action_executor` builds `exec-gmail-send-email`
  and AP judges every step valid (an invalid step would delete + raise).
- The executor **webhook accepts the fire** (HTTP 200).

**Two real bugs found + fixed while live-firing:**
1. **Connect-gate false-negative.** A direct trigger has no AP connection, but the gate demanded the
   SOURCE (slack) be connected → blocked slack→gmail even with Gmail connected. Fixed: a direct trigger
   gates on the ACTION app. Regression test: `test_direct_trigger_gates_on_action_app_not_source`.
2. **`catch_webhook` invalid.** The webhook trigger requires an `authType` prop; the empty input made
   AP mark it invalid and the validity gate refused. Fixed: pass `authType="none"`.

**🔒 Still blocked — the executor RUN errors at the AP platform level.** Firing the executor produces an
AP run with `status=INTERNAL_ERROR`, `stepsCount=0` (fails at init, before the gmail step). Consistent
across fires. No AP worker/sandbox logs were reachable to diagnose; no other flows had recent runs to
compare against on this instance. **So: the direct-action flow ARMS valid and accepts the fire, but the
run does not execute the send on this AP.** Next: get AP worker/engine logs (or test a known-good
webhook flow) to determine if it's AP-instance health vs. the webhook-trigger flow shape.

## 🟡 Partial / known nuances

- 🟡 **Telegram return-to-caller.** The *action* fires, but reporting the agent's answer BACK to the
  telegram chat is uncertain: telegram delivers via AP (not the direct adapter `deliver=True` uses), and
  when a message is consumed only by an action watcher the `/invoke` early-return carries no answer, so
  the telegram inbound send step may post nothing. Slack/Discord return-to-caller works (direct adapter).
  Fix: give the AP-inbound early-return a short confirmation line, or deliver the report inside
  `run_action_plan` for AP-delivery channels.
- 🟡 **Branch conditions are Python-evaluated for direct triggers** (not an AP ROUTER). Content →
  CONTAINS against message+answer; from-address → EQUALS against payload sender. Good for the common
  case; numeric conditions ("> 300") aren't parsed (same gap as the AP-ROUTER branching path).

## ⬜ Open / dormant

- ⬜ **box-direct → action.** NOT wired. box-direct arms via `create_box_poll_flow` (a separate path that
  returns before the direct-action code); it still declines. Wire the poll dispatch to `run_action_plan`,
  or use box in AP mode.
- ⬜ **Same-app direct actions (slack→slack, discord→discord).** The `direct_send` step kind is coded but
  **dormant** — no slack/discord ACTIONS are registered yet (only gmail actions exist). Register them
  (`gen_actions.py slack --check`) and slack→slack / discord→discord work via the bot adapter (no AP,
  no executor) — the *easiest* combination, not blocked.
- ⬜ **Executor cleanup.** `exec-<app>-<action>` flows are created lazily and reused, but never deleted
  when the last watcher using them is removed. Harmless (idempotent, reused), but they accumulate.
- ⬜ **Cross-scope executors.** One executor per (app, action) per AP project/scope — correct, but not
  yet load-tested with many tenants.

## Suggested next order
1. 🔒 **Live-fire once** (slack → gmail) — turn offline-green into proven.
2. 🟡 **Telegram return-to-caller** — small, makes telegram→gmail feel complete.
3. ⬜ **Register slack/discord actions** — unlocks same-app sends (dormant `direct_send`) + gmail→slack.
4. ⬜ **box-direct → action** — wire the poll dispatch, or standardize on box-AP.
