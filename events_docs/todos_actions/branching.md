# Branching — status, gaps & TODOs

Focused view of the NL→branched-flow work. Master list: [`../plans/ACTIONS_TODO.md`](../plans/ACTIONS_TODO.md).
Gmail actions: [`gmail.md`](gmail.md). Last updated 2026-07-20.

Legend: ✅ done+live · 🟡 partial/offline · 🔒 blocked on you · ⬜ open

---

## ✅ Done & live-verified

| Capability | Notes |
|---|---|
| ✅ 2-way branching | `"if it mentions urgent reply to the sender, otherwise draft a reply"` → valid + ENABLED AP ROUTER flow (`gmail/new_email ▸ /invoke ▸ ROUTER⟨reply / draft⟩`), verified live |
| ✅ N-way branching | `"if urgent reply, if invoice email me, otherwise draft"` → valid 3-branch ROUTER, verified live |
| ✅ Content conditions | `"if it mentions X"` points at the **trigger body** (`{{trigger.message.text}}`), not the agent answer |
| ✅ From-address conditions | `"if it's from boss@x.com"` resolves against the trigger sender field |
| ✅ Fallback semantics | requires exactly one trailing `otherwise/else` clause; ≥2 branches enforced |
| ✅ AP shapes cracked | ROUTER op needs `settings.executionType="EXECUTE_FIRST_MATCH"`; children add via `stepLocationRelativeToParent="INSIDE_BRANCH"` + `branchIndex` |
| ✅ Classifier hardening | branch-condition words (e.g. "mentions") no longer pollute trigger resolution — `classify.py` strips the `if…` region before matching |
| ✅ Verifier covers branches | the LLM intent-verifier checks the branch plan incl. per-branch recipients |
| ✅ Tests | `extract_branches` unit tests + `test_branched_flow_arms` gate test + a benchmark case |

## ⬜ Open (buildable, no blocker)

- ⬜ **Numeric / comparison conditions from NL** — `"if the PR is >300 lines"`, `"if the amount is over $500"`.
  The predicate/ROUTER model already supports operators (GT/LT/EQUALS via `_OP_MAP` in `flows.py`); the
  **NL parser (`extract_branches`) only extracts text-contains + from-address**. Need to parse
  `>`/`<`/`over`/`under`/`at least` + a number + a trigger field, and map to the numeric operator.
- ⬜ **Answer-based conditions** — `"if the agent thinks it's spam, …"`. A branch that tests the AGENT'S
  verdict (not the raw trigger text) needs the resume-watcher pattern: auto-inject a token contract
  ("begin your reply with URGENT / NORMAL") into the agent prompt, then branch on
  `{{step_1.body.answer}}` starting-with that token. Half-designed, not wired.
- ⬜ **Nicer per-branch confirmation** — the arm reply lists branch tags; a "when urgent → reply · else →
  draft" natural-language summary would read better.

## 🟡 Needs a live probe before building

- 🟡 **Nested branches** (a branch that itself branches). AP *claims* to support nested routers, but we've
  only ever **arm-verified** a flat router — never confirmed the runtime executes a 2-level router.
  **Step-0: arm a hand-built 2-level nested-router flow in live AP and fire it.** The result decides the
  design: nested-in-one-flow vs. auto-chaining several flat flows. Don't parse nested NL until this is known.

## 🔒 The scariest gap — correctness, not validity

- 🔒 **Live-fire branch-correctness harness.** Everything above proves a branched flow **arms valid**. We
  have **never proven AP routes to the *right* branch at runtime** — "valid" ≠ "correct". A flow can arm
  perfectly and still send every email down the `otherwise` branch if a condition template is subtly wrong.
  Need: a harness that fires a synthetic trigger event matching each branch in turn and asserts the
  expected action ran (which step executed). Gmail triggers are poll-only (`fire="manual"`), so this is
  easiest to prove first on a **synth-fireable trigger** (github) driving a branched action, then
  generalize. **This is the one gap that would let us claim branching "truly works" end-to-end.**

## Direct triggers + branching

A branched flow on an **AP-push trigger** (gmail/github/box-AP) uses an AP **ROUTER**. A **direct**
trigger (slack/discord/telegram) has no AP flow to hold a ROUTER — so as of **2026-07-20**
`"when a slack message arrives, if urgent, email me, else draft"` is handled by the **direct-action
executor (Option A)** instead: the branch condition is evaluated **in Python** (`direct_events._eval_condition`,
EXECUTE_FIRST_MATCH) and the winning branch's action fires via its executor flow. So direct-trigger
branching works, just via a different mechanism than the AP ROUTER. Details:
[`direct_actions.md`](direct_actions.md). box-direct branching is still open.

## Suggested next order
1. **Live-fire correctness harness** (github trigger → branched action) — turns "arms valid" into "routes right".
2. **Numeric conditions** — small, high-value, model already supports it.
3. **Nested-router live probe** — decides whether nested branching is even worth parsing.
4. **Answer-based conditions** — after the token-contract injection is designed.
