# Triggers + Actions — TODO backlog

Living checklist for the action half. Companion to `TRIGGERS_ACTIONS_DESIGN.md` (design + build
status). Status as of 2026-07-18: Gmail actions work live via chat from any channel; the items below
are what's left. `[ ]` open · `[~]` partial · `[x]` done-recently (kept for context).

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

## P1 — branching (the biggest v2 piece)

- [ ] **LLM branch vocabulary.** Teach the concierge to emit `branches` (if/else) — a new tool arg +
      prompt section, mirroring the ACTION VOCABULARY.
- [ ] **`ap_engine` ROUTER ops.** Arm branched flows live (AP ROUTER node + children), not just
      sequential `ADD_ACTION`. Offline builder (`router_step`, Option-B predicates) already done.
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
