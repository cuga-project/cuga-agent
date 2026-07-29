# Triggers + Actions — TODO backlog

Living checklist for the action half. Companion to `TRIGGERS_ACTIONS_DESIGN.md` (design + build
status). Status as of 2026-07-18: Gmail actions work live via chat from any channel; the items below
are what's left. `[ ]` open · `[~]` partial · `[x]` done-recently (kept for context).

## Direct-trigger → action (Option A) — DONE (2026-07-20)

- [x] **slack/discord/telegram → gmail action.** A direct trigger owns no AP flow, so an AP-only action
      runs via a reusable EXECUTOR flow (`catch_webhook ▸ gmail/send_email`, `ap_engine.ensure_action_executor`)
      that CUGA fires after the agent answers (`direct_events.run_action_plan`). AP keeps the creds;
      the plan is built at arm time (validity gate applies) + stashed on `subscription.config`. Linear
      + N-way branches (Python-evaluated). Declines loudly if the executor can't be built. Offline-verified
      (`test_events_direct_actions.py` + gate tests). **Live fire pending.** Full status + gaps:
      [`../todos_actions/direct_actions.md`](../todos_actions/direct_actions.md).
      Open: box-direct→action (separate poll path), telegram return-to-caller, same-app direct sends
      (dormant until slack/discord actions registered), live verification.

## Hardening pass — DONE (2026-07-19) — a, b, c, + LLM verifier

- [x] **LLM verifier (dual-path assurance).** Deterministic code BUILDS the action plan; an
      independent LLM verifies it matches the request (`_verify_action_plan`). A confident MISMATCH →
      ask, don't arm; MATCH / model-unavailable → proceed (FAIL-OPEN). Divergences logged
      (`EVENTS_VERIFY_LOG`) to feed the benchmark. Off with `EVENTS_VERIFY_ACTIONS=0` (offline tests
      set this). Three independent gates now guard an arm: deterministic build · AP validity gate ·
      LLM intent-match. Live-verified: a correct plan MATCHes + arms.

## Hardening pass — DONE (2026-07-19) — a, b, c

- [x] **(a) Arm-time validity gate (no silent failures).** `ap_engine._assert_steps_valid` fetches the
      flow before publish; if AP marks ANY step invalid, it deletes + raises — the concierge reports the
      real problem instead of a false "ARMED". Retro-catches the whole "arms but never fires" class.
- [x] **(b) Validity-probe folded into the generator.** `gen_actions.py <piece> --check` arms a
      throwaway flow per action and prints VALID/INVALID (emitting all props, templating the message
      id). Adding a piece is now "run --check, it tells you what's armable" — no manual archaeology.
      (gmail: send/reply/draft/get/search VALID, custom_api_call INVALID.)
- [x] **(c) NL→Flow benchmark.** `tests/events/nl_to_flow_bench.jsonl` (labeled) +
      `test_nl_to_flow_bench.py` scores router_mode / push_trigger / gate_outcome / action accuracy and
      **CORRECT_AT_ARM** (armed flows that were right — the anti-silent-failure metric). Currently
      100% across the board; asserts CORRECT_AT_ARM==100%. It **caught a real silent failure** ("label
      it" silently armed a plain watcher) → now declined via `actions.unsupported_action`.
- [x] **Unsupported-verb decline.** "label/forward/star/… it" with no registry action → honest
      decline, never a silent plain watcher.

## Gmail golden pass — DONE (2026-07-19)

- [x] **send_email valid live** (AP needs every prop present — `render_params` emits typed empties).
- [x] **Multi-action** ("email me AND reply to the sender") — valid 2-action chain, span-deduped.
- [x] **subject / cc from NL** on send_email.
- [x] **Return-to-caller** — direct channels (Slack, proven) + AP-channel send step (Telegram).
- [x] **Recipient handling** — explicit address / "the sender" / asks when it can't infer.
- [x] **NL on-ramp** — every path (fast/slash/LLM) reaches the gate; multi-action aware.

Still open for Gmail:

- [ ] **archive / mark-read / delete (custom_api_call).** The raw Gmail-API step does NOT validate as
      an armable AP step (tried every prop/propSetting shape). Currently GATED with an honest message.
      Needs: figure out AP's custom_api_call validity (or a piece that exposes native modify/trash),
      then wire archive/mark-read (non-destructive) + delete (destructive → run-time approval).
- [ ] **Run-time approval pause** — needed before delete can ship (design §3.4b (b)).
- [x] **Recipient ask-till-legit parking** — DONE. "email me" asks who; the original utterance is
      parked (`_pending_recipient`) and the next message (an address) completes it. Live-verified +
      offline park assertion.
- [ ] **Nicer confirmation text** — channel gets the raw answer prefixed "⚡ flow fired"; a
      "✅ replied to X" line needs the agent to emit a channel note vs the draft body.
- [ ] **Attachments / reply-all / bcc from NL** — params exist, not wired to NL.

## P0 — close the gaps we hit live

- [x] **★ Report back to the originating caller after an action fires (DIRECT channels).** DONE:
      `create_push_flow` now sets `deliver=True` even with an action — `/invoke` delivers the agent's
      answer to the ORIGIN channel encoded in `thread_id` (`gw:<channel>:<native>`) via the direct
      adapter, while the action step runs in the acting app (different sinks, no double-send). Origin
      folded back into the action-flow dedup identity. Verified live: armed from Slack →
      `deliver=true` + slack origin + `create_draft_reply` step; `delivery.send_direct('slack',…)`
      posts for real. **Remaining:** (a) AP-backed channels (Telegram) need an AP send step, not the
      direct adapter; (b) nicer confirmation text ("✅ Drafted a reply to X") vs the raw answer —
      needs the agent to emit a channel line + a draft body (structured), today the channel gets the
      answer prefixed with "⚡ flow fired · gmail/new_email".
- [ ] **Box-direct + action.** Box in DIRECT mode can't carry an AP action step (schedule→poll, no AP
      flow). Options: (a) wire the `/api/events/box/poll` path to run a post-agent gmail action /
      deliver-via-gmail; or (b) document box-AP mode as the supported route. Today it returns an
      honest "switch to AP mode" message. Decide + implement.
- [ ] **Multi-action sequential in the concierge.** `build_action_tail` already chains a list and
      `create_push_flow(actions=[…])` arms N steps — the concierge only extracts/sends ONE. Extend
      `extract_action` → `extract_actions` (list) + loop. Enables "summarize, email me, AND post to
      Slack" and "email me + reply on the PR".
- [ ] **Live cross-piece proof.** Connect GitHub, verify "PR opens → email me at X" arms `github/new_pr
      ▸ agent ▸ gmail/send_email` end-to-end (code is ready; not live-verified).
- [ ] **Real-fire leg (Gmail).** Send an email to the connected inbox, confirm a DRAFT reply actually
      appears (the one leg needing a human-sent email). Add the result to the checklist.

## P1 — LIVE BRANCHING — DONE (2026-07-20)

`"when an email arrives, if it mentions urgent reply to the sender, otherwise draft a reply"` → a
VALID + ENABLED ROUTER flow (`gmail/new_email ▸ /invoke ▸ ROUTER⟨reply / draft⟩`), verified live.

- [x] **AP shapes cracked:** ROUTER op needs `settings.executionType="EXECUTE_FIRST_MATCH"`; branch
      children add with `stepLocationRelativeToParent="INSIDE_BRANCH"` + `branchIndex`.
- [x] **`ap_engine.create_branched_push_flow`** — arms trigger ▸ /invoke ▸ ROUTER + per-branch actions,
      through the validity gate.
- [x] **`actions.extract_branches`** — "if <cond> A, otherwise B" → branches; content conditions ("if
      it mentions X") point at the trigger BODY (`{{trigger.message.text}}`), not the agent answer.
- [x] **Concierge wired** + verifier checks the branch plan + benchmark case + unit tests.

- [x] **Multi-branch (N-way)** — DONE 2026-07-20. "if urgent reply, if invoice email me, otherwise
      draft" → a valid 3-branch ROUTER flow, verified live. (Also fixed: branch-condition words like
      "mentions" polluting trigger resolution — `classify` now strips the `if…` region too.)

Branching follow-ups (open):
- [ ] **Trigger-field numeric conditions live** ("if >300 lines") — predicate model supports it;
      NL parser covers content + from-address so far.
- [ ] **Nested branches** — a branch that itself branches (AP supports nested routers; not parsed).
- [ ] **Step-0 probe.** Arm a 2-level nested-router flow in live AP; confirm the runtime executes it.
      Result decides: nested-in-one-flow vs auto-chained flows.
- [ ] **Answer-token contract injection.** When a branch tests the answer, inject "begin your reply
      with X/Y" into the agent prompt (the resume-watcher pattern), automatically.
- [ ] **Ask-till-legit for action recipients.** Park the "who should I email?" question so the user's
      next message (an address) completes it — today it just asks, no follow-up parking.

## P1 — broaden beyond Gmail (generic, mostly DATA)

- [ ] **GitHub action rows.** `gen_actions.py github` → verify → commit (create_issue, create_comment,
      …) + phrases. Enables "when X, comment on the PR".
- [ ] **Box action rows** (upload/move/comment) — for box-AP mode.
- [ ] **Slack/Discord/Telegram as ACTIONS** (not just delivery) — e.g. "post to #urgent" as a routed
      action step, distinct from the send sink.
- [ ] **Per-app sender templates.** `send_email` "reply to sender" only resolves gmail's `from`. Add a
      generic `sender_field` per trigger so any app with a real email sender works.
- [ ] **Regenerate + verify all pieces**; add one checklist row + example per new action.

## P1 — tool-first path (design D3)

- [ ] **Equip one agent with a real action tool** (e.g. mailbot + an MCP gmail-send) and prove
      `resolve_action`'s tool branch live (agent acts in-run, no AP step). Currently AP-fallback only.

## P2 — approval + safety

- [ ] **Run-time approval, live.** `approval_step` is built but never inserted live (no destructive
      Gmail action ships). Add a destructive action (e.g. via `custom_api_call` archive) OR a
      non-Gmail destructive action, and wire the gate to insert the approval step + verify the pause.
- [ ] **Approval delivery to origin channel.** Confirm the approve/reject prompt lands in the channel
      the request came from (web/Slack/Telegram) and the answer resumes the flow.
- [ ] **Verb-alignment audit.** Expand the false-positive test set (e.g. "forward", "cc", trigger
      words that look like actions) so extraction stays narrow.

## P2 — Gmail native gaps (custom_api_call)

- [ ] **Decide on archive/label/delete.** Not native Gmail-piece actions — only `custom_api_call`.
      Either add curated `custom_api_call`-backed rows (with `destructive=True` + approval) or keep
      out of scope. Document the decision.

## P2 — tests / CI

- [ ] **Live matrix script.** One `live_actions_matrix.py` running the checklist §B–H end-to-end
      (arm + inspect the flow's action step) against a live server, like `live_github_triggers.py`.
- [ ] **Dedup live test.** Confirm same-action reuse + different-action = new flow on the live server.
- [ ] **Cross-piece live test** (once GitHub connected) in the suite.
- [ ] **Wire `make test-live`** to include the action e2e.

## P3 — docs / UX polish

- [x] Design doc, `actions/` diagrams, `setup_action.md`, `checklist_actions.html`, ACTIONS label in
      Studio + board, `feedback_action_examples_label` memory rule.
- [ ] **Update `checklist_actions.html`** as items flip from v2→live (branching, multi-action).
- [ ] **Studio: surface the action** on an armed subscription (the runs/flows view shows pieces; make
      the action step legible).
- [ ] **Add a "actions" filter chip** to the examples board (currently a label, not a filter).
- [ ] **Regenerate `actions/` diagrams** if the branching/multi-action flow shapes change.

## P3 — housekeeping

- [ ] **PR prep.** Squash/organize the action-half commits; ensure `main` untouched; run ruff + full
      offline suite; fill the PR body from the design doc's build-status table.
- [ ] **`ap_pieces.py` NEEDED.** Add `@activepieces/piece-approval` if run-time approval ships.
- [ ] **Remove/parameterize** the hardcoded `push-gmail-new-email-cuga` test flows left by harnesses.
