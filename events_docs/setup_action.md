# Setup — the Action half

This EXTENDS [`SETUP.md`](SETUP.md); it does not replace it. Get the base stack up the normal way
(`make up` / `cuga start demo --events`, connect your integrations) — then this page covers only
what the **action half** adds: running a connector **action** (Gmail send / reply / draft) as a step
*after* the agent answers.

Design + build status: [`plans/TRIGGERS_ACTIONS_DESIGN.md`](plans/TRIGGERS_ACTIONS_DESIGN.md) ·
diagrams: [`actions/`](actions/) · acceptance: [`checklist_actions.html`](checklist_actions.html).

---

## What the action half needs (beyond the base stack)

Everything in `SETUP.md` still applies. The action half adds **one hard requirement** and no new
infrastructure:

1. **The acting app must be connected as a per-user OAuth connection.** Actions run *as you*. For the
   Gmail pilot that means a per-user Gmail connection in Activepieces — the SAME connection the Gmail
   *trigger* uses, wired as `auth` on the *action* step too. Connect it exactly as in
   [`setup/GMAIL.md`](setup/GMAIL.md):

   ```
   open $PUBLIC/api/events/connect/gmail        # approve in the browser (testing-mode → Advanced → Allow)
   curl -s http://localhost:7860/api/events/integrations   # gmail should show connected
   ```

   A **cross-app** flow (e.g. a GitHub trigger that emails you) needs BOTH apps connected — the
   trigger app (github) *and* the acting app (gmail). The action resolves its **own** app's
   connection, independent of the trigger.

2. **The server must run this branch.** The action vocabulary lives in the concierge prompt and the
   `actions.py` registry. If your server predates the action half, restart it (`make up`). Verify:

   ```
   curl -s -H 'x-user-id: admin' http://localhost:7860/api/events/examples | grep act-gmail-reply
   ```

That's it — no new tunnels, containers, tokens, or pieces. The Gmail piece you already installed
(`make ap-pieces`) carries the action.

---

## How it works (one paragraph)

The concierge's `find_or_create_flow` gained two optional args — `action='<app>/<name>'` and
`action_to` (a send recipient). After the existing **trigger gate**, a symmetric **action gate**
validates the action against `actions.py`, checks **verb-alignment** (the utterance verb must match
the action — this is what stops "send" compiling to "delete"), fills its params (message id from the
trigger, body from the agent's answer, recipient from you), and appends an AP **action step** to the
flow. `ap_engine.create_push_flow(actions=…)` arms it live. The agent never sees a token — AP
resolves the action's connection in its own sandbox at fire time. See the diagrams in
[`actions/`](actions/).

---

## Adding a NEW piece's actions (the whole developer flow)

The contract: **new piece = DATA, not code.** No renderer/flow/resolver/router/approval changes.

1. **Draft rows from the live AP catalog** (reads `GET /api/v1/pieces/<piece>`):

   ```
   uv run python scripts/gen_actions.py github        # or: box, slack, @activepieces/piece-…
   ```

2. **Verify + paste** the rows you want into `src/cuga/backend/events/actions.py`. Set each param's
   `source` hint (`answer` | `trigger` | `static` | `user`) and mark any `destructive=True`. The
   generator flags read-only actions (usually a TOOL, not a post-agent action) and warns when a
   capability is only reachable via `custom_api_call` (e.g. Gmail has no native archive/label/delete —
   do **not** invent registry rows for those).

3. **Add / annotate an agent** that handles it (roster `supervisor_agents.yaml`) — same as a new
   trigger (`SETUP.md → Adding a sub-agent`).

4. **Add examples** to `src/cuga/backend/events/catalog.py`, then regenerate the boards:

   ```
   uv run python scripts/gen_examples.py && uv run python scripts/gen_slides.py
   ```

5. **Add tests** — a registry/gate case in `tests/events/test_events_actions.py` /
   `test_events_action_gate.py`, and a row in [`checklist_actions.html`](checklist_actions.html).

No step above touches `flows.action_step`, `resolve_action`, `router_step`, or the concierge gate.
If a new piece ever forces a change there, the abstraction is wrong — file it.

---

## Approval (two-tier) — what to know for setup

- **Arm-time** (always): the ARMED confirmation **names the action**; a destructive action can't arm
  on ambiguous confidence. Nothing to configure.
- **Run-time** (only for `destructive=True` or opt-in): an approval step is compiled into the flow
  (AP's approval piece). The Gmail pilot ships **no** destructive action, so this never fires here.
  If you add a destructive action later, ensure `@activepieces/piece-approval` is installed
  (`make ap-pieces` covers the pilot set; add it to `scripts/ap_pieces.py` NEEDED if you rely on it).

---

## Testing

| Level | Command | Needs |
|---|---|---|
| Offline (registry, gate, branches, dedup) | `uv run python -m pytest tests/events/test_events_actions.py tests/events/test_events_action_gate.py -q` | nothing |
| Full offline suite (no regressions) | `uv run python -m pytest tests/events/ -q -k 'not live'` | nothing |
| Live arming + action-step present | `EVENTS_SERVER_URL=http://localhost:7860 uv run python tests/events/live_gmail_action_e2e.py` | server up + **Gmail connected** |
| Manual acceptance | open [`checklist_actions.html`](checklist_actions.html) | server up + Gmail connected |

**The real-fire leg** (an actual email → a real draft/reply) needs an email SENT to the connected
inbox; the harness prints how to finish it. As with Gmail triggers, the poll fires within ~5 min.

---

## Gmail action coverage (what works today)

| Ask | Action | Status |
|---|---|---|
| "reply to the sender" | `gmail/reply_to_email` | ✅ live |
| "draft a reply" | `gmail/create_draft_reply` | ✅ live |
| "email me / email X a summary" | `gmail/send_email` (subject + cc from NL) | ✅ live |
| "email me AND reply" (multi-action) | send + reply chained | ✅ live |
| result reported back to the arming channel | Slack/Discord (direct) + Telegram (AP send step) | ✅ live |
| "archive it" / "mark it read" / "delete it" | custom_api_call (Gmail REST) | ⛔ **gated** — the raw-call step doesn't validate as an armable AP step yet; the concierge declines honestly and steers to reply/draft/send. Delete also needs run-time approval. |

## Gotchas

- **"email me" asks for an address.** The concierge can't infer *your* address from "me" — it asks.
  Give an explicit address, or say "reply to the sender" (which uses the firing email's `from`). This
  is deliberate: never silently mail the wrong place.
- **Gmail has no native archive/label/delete.** Only `send_email` / `reply_to_email` /
  `create_draft_reply` (+ read/search + `custom_api_call`). The pilot is native-only by design (D1).
- **Reply/draft need a Gmail trigger.** They key off the firing message id (`{{trigger.message.id}}`);
  they aren't meaningful downstream of a non-Gmail trigger.
- **Delivery vs action.** A flow that ACTS doesn't also deliver to a chat channel — the action *is*
  the output. The chat sink is dropped from the flow (and from its dedup identity).
