# Roadmap

Where the work is headed. Phase definitions and current status are in [PHASES.md](PHASES.md); this is
the sequenced "what's next." We are in **P3 (Run) = MVP, ~75%**.

## Not yet fully vetted — the honest TODO (2026-07-15)

The single-agent/supervisor refactor shipped and is live-proven, but these carry known caveats:

1. **Polling triggers never machine-fired.** Gmail's 4 triggers (and Box on the AP backend) are
   Activepieces POLLING triggers — arm-verified only; a genuine fire needs a real email/upload.
   Structural to AP, but the *proof* remains manual (checklist §Gmail/Box).
2. **Credentials — the weak link is `AP_PASSWORD`.** Tokens live encrypted in AP; CUGA-held
   secrets can resolve via `vault://…` (the secret seam) — but in practice `.env` is plaintext and
   AP_PASSWORD guards a publicly-tunneled AP console. Vault it before any real deployment
   (see GAPS.md §security). Box's direct dev-token expires ~60 min — a rotation story is needed.
3. **Per-user identity through supervisor delegates.** The supervisor keys its own memory per
   conversation, but `delegation.py` (upstream) invokes each sub-agent on a FIXED thread
   (`supervisor_conversational_<name>`) — sub-agent memory is shared across users. Fine for
   stateless specialists; wrong for per-user credentialed work. Needs upstream thread plumbing.
4. **Delegation latency + cost.** Every wake-up now includes a supervisor inference (measured:
   ~3–10s overhead; 14/14 accuracy). Watch `make test-delegation` when the roster grows; consider
   domain supervisors if accuracy degrades past ~40 sub-agents.
5. **`test-suite-now` / `test-matrix` / `test-fire` are fleet-era.** They assert per-agent
   invocation by name; in the single-agent world they need rework to assert through `cuga`
   (test-live, suite-flows, delegation bench, and the GitHub triggers harness are already
   supervisor-native and green).
6. **Per-delegate tool scoping is name-based.** The canonical loader now scopes tools via
   registry app names; per-tool includes (get_include_by_app) don't apply to delegates yet.
7. **Webhook/Telegram excluded from the NL fast path** (deliberate) — they arm via the LLM path;
   revisit once armed-webhook use cases firm up.

## Finish P3 (MVP) — the near work

1. **NL→flow rigor** *(the strategic priority)*. Make an English sentence → the right flow
   *measurable*, not just demoable:
   - ✅ a typed **FlowSpec** (`events/flowspec.py`) + a deterministic **pre-router** in front of
     the concierge: a high-confidence utterance arms without the LLM, a missing required slot
     becomes a question, and the next message fills it (**ask-till-legit**); the registry
     validation gate disposes of every proposal before anything is built;
   - ✅ a **labeled benchmark** (47 cases, utterance → expected FlowSpec) scored in CI, gated on
     **zero wrong-at-high** (TESTING.md);
   - ⬜ the LLM seam scored the same way — structured FlowSpec output with the schema generated
     from the registry — plus a **model bake-off** for the concierge (accuracy / latency / cost);
   - ⬜ then **branching/ROUTER flows** (the builder is linear today).
2. **Webhook — OUT** — deliver an answer to any HTTP endpoint (optional HMAC). Unlocks flow→flow
   chaining — a capability, not just another connector.
3. **Email delivery sink** — "…email me the brief" delivers to an inbox, not a chat.

## P4 · Sprint — breadth (no new architecture)

WhatsApp · Email-as-a-channel · Google Calendar · Drive/Sheets · one work tool (Notion/Jira/Linear) ·
RSS/feeds. Each is a new connector on the existing seam.

## P5 · Fly — cloud & scale

Multi-tenant cloud: real isolation, IdP/OIDC, managed infra, stable URLs, a **secrets vault**
(replace `.env`), observability, horizontal scale.

## Backlog (beyond Fly)

Twilio SMS · Stripe · Salesforce/HubSpot · PagerDuty/Datadog · Dropbox/S3 · MS Teams · Signal.

## Recently shipped

All 4 channels live · Gmail/GitHub/Box PUSH proven end-to-end · generic webhook-IN · the Box download
step · GitHub OAuth (replacing the broken PAT path) · debug fire (`/subscriptions/{id}/run`) ·
`?flow=1` returns the armed flow · credential rotation · the `test-fire` harness + consolidated report.

## Cron/poll — status (2026-07-15)
- FIXED: fired prompt is now single-shot. The cadence stripper is an **LLM rewrite at ARM time**
  (`_single_shot_task` — one call per flow, never per tick), with the `_strip_cadence` regex as a
  guarded fallback (LLM disabled via `EVENTS_CADENCE_LLM=0`, unreachable, timed out, or its answer
  still leaks cadence words). Either way the "ONE run, do NOT loop" framing wraps the result.
  Proven: 16/17 corpus utterances get clean LLM rewrites; on 5 adversarial paraphrases the regex
  leaks 5/5 and the LLM strips 5/5 ("keep tabs on TSLA through the day" → "Check TSLA and buzz me
  if it drops 2%"). Before the fix, "every 5 minutes"/"monitor" in the prompt made the agent try
  to loop+sleep and hit the execution timeout. Verified live (poll tick answers in ~8s, no loop).
- PUSH gets the counterpart fix (2026-07-16): no loop bug there, but live probes showed the agent
  re-fetching/confabulating the event (wrong PR summarized) and meta-answers ("I've sent you the
  message") that would be delivered verbatim. `envelope.worker_input()` now prepends deterministic
  one-shot framing to every integration fire: the [event] payload is authoritative, handle THIS
  event once, the reply IS the deliverable. Fire-time seam → covers already-armed flows. Both
  probes verified fixed live; disk run-logs now record the full worker input (framing + payload).
- OPEN (bug 2): "notify ONLY when it changes" needs cross-tick STATE. `__mode:"poll"` marker is
  SET in flows.py but NEVER CONSUMED — no state store, no prior-value passed to the agent. So each
  poll tick is stateless: the agent has no baseline and can't reliably suppress unchanged reports.
  FIX NEEDED: a per-subscription state store; on each tick pass the last stored value into the
  prompt, have the agent return the current value, store it. Design Q: how the agent returns the
  value for storage (structured field vs parse). Until built, "watch X and tell me when it
  changes" reports every tick (or says "no prior data"), not only-on-change.
