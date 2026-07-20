# Gmail actions — status, gaps & TODOs

Gmail-focused view of the action work. Master list: [`../plans/ACTIONS_TODO.md`](../plans/ACTIONS_TODO.md).
Design: [`../plans/TRIGGERS_ACTIONS_DESIGN.md`](../plans/TRIGGERS_ACTIONS_DESIGN.md). Verify:
[`../checklist_actions.html`](../checklist_actions.html). Last updated 2026-07-19.

Legend: ✅ done+live · 🟡 partial/offline · ⛔ gated (honest decline) · 🔒 blocked on you · ⬜ open

---

## ✅ Enabled & live-verified

| Capability | Notes |
|---|---|
| ✅ `reply_to_email` | reply to the sender; keys off the firing message id |
| ✅ `create_draft_reply` | draft a reply (no send) |
| ✅ `send_email` | email me / email X; **subject + cc from NL**; all-props render (AP validity rule) |
| ✅ Multi-action | "email me a summary **and** reply to the sender" → one valid 2-action flow |
| ✅ **Cross-piece GitHub → Gmail** | "when a PR opens on o/r, email me a risk summary at X" → `github/new_pr ▸ agent ▸ gmail/send_email`, VALID + ENABLED (verified live 2026-07-19) |
| ✅ Return-to-caller | armed from Slack/Discord → answer posts back to that channel (proven); Telegram → AP send step |
| ✅ Recipient ask-till-legit **parking** | "email me" → asks who; next message (an address) completes it |
| ✅ Triggers | new_email, new_labeled_email, new_attachment, new_gmail_label |
| ✅ Safety gates | arm-time AP **validity gate** · **LLM verifier** (intent-match) · unsupported-verb decline · verb-alignment · same-app guard |
| ✅ Generator `--check` | `gen_actions.py gmail --check` reports per-action VALID/INVALID |
| ✅ Benchmark | `make bench` — CORRECT_AT_ARM 100% |

## ⛔ Gated (declines honestly — no silent failure)

| Ask | Why gated | TODO to enable |
|---|---|---|
| ⛔ `archive it` / `mark it read` | custom_api_call won't validate as an armable AP step | ⬜ resolve AP custom_api_call validity, OR a piece with native modify |
| ⛔ `delete it` / `trash it` | same + destructive | ⬜ above **and** a run-time approval pause |
| ⛔ `label it X` / `forward it` / `star it` | no native Gmail action at all | ⬜ custom_api_call (label needs a label-id lookup) |

## 🟡 Partial / offline-only

| Item | State | TODO |
|---|---|---|
| ✅ Branching (2-way + **N-way**) | LIVE — "if urgent reply, if invoice email me, else draft" → valid ROUTER flow, verified | nested + numeric conditions open |
| 🟡 Telegram return-to-caller | wired (AP send step) | ⬜ live-prove (Slack/Discord proven) |

## ⬜ Open (buildable, no blocker)

- ⬜ **Nicer confirmation text** — channel gets the raw answer prefixed "⚡ flow fired"; want "✅ replied to alice@… — <summary>" (agent emits a channel line separate from the reply body).
- ⬜ **Attachments / reply-all / bcc from NL** — params exist on `send_email`/`reply_to_email`, not wired to language.
- ⬜ **Verifier-mismatch parking** — a flagged mismatch asks; parking it so the next message resolves it (like recipient parking).
- ⬜ **Live fire-correctness for Gmail** — synthetic-fire harness that asserts the drafted/sent content, not just that the flow armed.

## 🔒 Blocked on you (external)

- 🔒 **Real email → real draft/reply** — send an email to the connected inbox to prove the final leg.
- ✅ ~~Cross-piece GitHub → Gmail~~ — DONE (GitHub connected + verified live). While proving it, the
  **verifier caught a real bug**: an email address like `me@gmail.com` in the utterance matched the
  gmail *trigger* phrase, mis-resolving "PR opens … email me at …@gmail.com" to `gmail/new_email`.
  Fixed: `classify.py` masks email addresses before trigger matching; regression case added to the
  benchmark. Good example of the 3-gate design working.
- 🔒 **Box → Gmail action** — your Box is in DIRECT mode (token poll) which can't carry an AP action step; needs Box in AP mode, or a box-direct-poll-carries-action build.

---

## Trigger → Action combinations (what actually works)

The **action** is always **Gmail** today (only gmail actions are registered). The **trigger** can only
carry an action if it's an **AP-push** trigger. So:

| Combination | Works? | Why |
|---|---|---|
| gmail → gmail (reply/draft/send) | ✅ | AP push + gmail action |
| github → gmail | ✅ | AP push + gmail action (verified) |
| box (AP mode) → gmail | ✅ | needs Box OAuth |
| slack / discord / telegram → gmail | ✅ **via executor (Option A, 2026-07-20)** | direct trigger owns no AP flow, so the gmail action runs via a reusable `catch_webhook ▸ gmail/send_email` executor CUGA fires after the agent answers. Offline-verified; **live fire pending**. See [`direct_actions.md`](direct_actions.md) |
| box (direct mode) → gmail | ❌ **declines loudly** | box-direct arms via a separate poll-flow path not yet wired to the executor; still declines. slack/discord/telegram now work |
| gmail → github (create issue) | ✅ **(2026-07-20)** | `github/create_issue` registered + validates live in AP (needs owner/repo) |
| github → github (comment on PR) | ✅ **(2026-07-20)** | `github/create_comment` — same-app; issue_number from the firing PR |
| gmail → slack (post as an action) | ❌ | slack actions not registered — but **delivery to Slack works** (return-to-caller / deliver_to) |

**Two gaps this reveals (both general, not Gmail-specific):**
- ⬜ **Non-gmail actions** — register github/slack/box action rows (`gen_actions.py <piece> --check` +
  verify). Unlocks gmail→github, gmail→slack-post, etc.
- ✅ **Direct-trigger → action — BUILT (Option A, 2026-07-20).** slack/discord/telegram → gmail now work
  via a reusable **action-executor** AP webhook flow (`catch_webhook ▸ gmail/send_email`) that CUGA POSTs
  to after the direct-trigger agent runs — AP keeps the creds, direct branching runs in CUGA (Python).
  Offline-verified; **live fire pending**. box-direct still open. Details + gaps:
  [`direct_actions.md`](direct_actions.md). Full branching status: [`branching.md`](branching.md).

## Suggested next order
1. Live **branching** (ROUTER now unblocked) — the flagship general gap.
2. **Nicer confirmation text** + **verifier-mismatch parking** — small polish.
3. **Live fire-correctness harness** — turns "armed" into "actually did the right thing".
4. (When you connect GitHub / flip Box) — prove the cross-piece paths live.
