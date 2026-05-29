# Declarative Configuration for Event-Driven CUGA

> **First time here?** Read [design_doc.md](design_doc.md) for the runtime
> mechanics. This doc adds a *new way to write registry rows* on top of the
> existing event-driven design — no runtime change.

A YAML/JSON file format for defining standing intents, agents, and pub sinks at
deploy time. GitOps for agentic loops.

Parent: [configuration_modes.md](configuration_modes.md)

---

## TL;DR

```yaml
# rules.yaml
version: 1

subscriptions:
  - id: monday-hn-digest
    trigger:    { kind: timed, cron: "0 9 * * 1" }
    target:     { agent: digest_agent }
    prompt:     "Fetch top 10 HN posts about LLM tooling and email a digest."
    outcomes:
      - { emit_to: email_digest }

agents:
  - name: digest_agent
    prompt_template: prompts/hn-digest.md
    tools: [hn_api, email]

pub_sinks:
  - name: email_digest
    kind: email
    config: { to: anu@example.com, subject: "Monday HN Digest" }
```

```bash
cuga apply rules.yaml
# → reads, validates, upserts rows into the registry; arms triggers
```

That's the whole shape. Everything below fills in the details.

---

## Design goals

1. **Git-friendly.** A `rules.yaml` is the source of truth; the registry is a
   replica. PRs review behavioral changes.
2. **Schema-validated.** Every file passes JSON Schema before any row gets
   touched. No half-applied state.
3. **Idempotent.** Re-applying the same file is a no-op. Applying a changed
   file is a diff-and-upsert.
4. **Self-contained.** Subscriptions, agents, and sinks live in one file (or
   a folder of files). No "side files" needed at apply time.
5. **Two-way.** `cuga export` dumps the current registry as YAML; you can
   round-trip from chat-driven to file-driven and back.

---

## Top-level shape

```yaml
version: 1                        # schema version
metadata:                         # optional
  app: cuga
  tenant: acme
  owner: ops@acme.com
  description: |
    Production rules for the acme tenant.

subscriptions: [...]              # the "when X, do Y" rules
agents: [...]                     # named CUGA agents these subscriptions target
pub_sinks: [...]                  # outbound destinations
hooks: [...]                      # see skill_hooks.md — declarative hooks live here too
```

All four sections are optional but a file with only `subscriptions` and no
referenced `agents`/`sinks` will fail validation with helpful errors:
*"subscription `monday-hn-digest` targets agent `digest_agent` which is not
defined; declare it in this file or remove the reference."*

---

## `subscriptions` — the standing intents

Every subscription is `trigger × target × outcome`. The triple covers every
events.md utterance.

### Schema

```yaml
- id: <stable kebab-case identifier>          # required; UPSERT key
  description: <one-line human note>          # optional
  enabled: true                               # default true

  trigger:                                    # required
    kind: timed | push | pull | hook          # required
    # kind-specific fields below

  target:                                     # required
    agent: <agent_name>                       # required — must exist in `agents:`
    thread_strategy: per_event | persistent   # default per_event for triggers,
                                              # persistent for hooks
    thread_template: "{trigger.id}:{ts}"      # optional, when per_event

  prompt: |                                   # required
    The instruction the agent receives when the trigger fires.
    Templated with {trigger.payload.*} and {trigger.context.*}.

  outcomes:                                   # optional — declarative shape
    - emit_to: <sink_name>                    # one of three:
      condition: <jq-like expression>         #   pub_sink
    - reply_to: trigger                       #   reply via the trigger's gateway
    - send_to: <agent_name>                   #   swarm: send to another agent

  metadata:                                   # optional opaque dict the agent
    cost_center: q2-leads                     # can read at runtime
```

### Trigger schemas (kind-specific)

```yaml
# TIMED — pure clock
trigger:
  kind: timed
  cron: "0 9 * * 1"                # 5-field cron, OR…
  interval: 6h                     # "30s" / "5m" / "2h" / "1d", OR…
  delay_until: "2026-06-01T09:00Z" # one-shot
  expires_at: "2026-12-31T23:59Z"  # optional auto-cancel

# PUSH — external system delivers
trigger:
  kind: push
  source: email | slack | webhook | imap | github | stripe | calendly | ...
  filter:                          # source-specific filter
    to: support@acme.com           # for email
    channel: "#support"            # for slack
    event: pull_request.opened     # for github webhook
  secret_ref: env:GITHUB_WEBHOOK_SECRET   # for HMAC verification

# PULL — CUGA-driven check
trigger:
  kind: pull
  interval: 6h
  source:
    type: http_get
    url: https://openai.com/changelog
  diff:
    strategy: content_hash         # also: jq_path, threshold
    # for threshold:
    # path: ".mrr"
    # change: ">5%"

# HOOK — internal, see skill_hooks.md
trigger:
  kind: hook
  on: post_skill
  skill: linear.create_ticket
  filter: { result.success: true }
```

### Example: support email triage

```yaml
- id: support-email-triage
  description: Classify inbound support emails as bug vs sales
  trigger:
    kind: push
    source: email
    filter: { to: support@acme.com }
  target:
    agent: triage_agent
  prompt: |
    A new customer email landed at support@acme.com.

    From:    {trigger.payload.from}
    Subject: {trigger.payload.subject}
    Body:
    {trigger.payload.text}

    Classify this as 'bug' or 'sales'.
    - If bug: file a Linear ticket and reply with the ticket URL.
    - If sales: post to #sales and reply confirming.
  outcomes:
    - emit_to: linear          # for bug branch
    - emit_to: sales_slack     # for sales branch
    - reply_to: trigger        # in both branches
```

The `outcomes` block is **declarative documentation** that helps:

- **Validation:** every sink referenced must exist in `pub_sinks:`.
- **Linting:** unused sinks get flagged.
- **Dashboards:** the explorer UI can show "this standing intent emits to: Linear, Slack".
- **The agent itself:** it gets injected into the prompt as "you should emit to one of these targets" — keeps the agent honest about its job.

It is **not** enforcement — the agent can still emit to other places if its
reasoning calls for it. The outcomes block is a hint, not a contract.

---

## `agents` — named CUGA agent definitions

```yaml
agents:
  - name: triage_agent              # stable identifier
    description: Support email triage
    prompt_template: prompts/triage.md  # path to system prompt file
    tools:                          # MCP tools the agent can call
      - linear
      - slack
      - email_reply
    policies:                       # optional behavior controls
      max_steps: 6
      timeout_seconds: 60
      require_human_approval_for:
        - slack.post                # delegate to human before emitting
    inbox:
      kind: memory | sqlite | kafka # default: inherit from cuga settings
      max_depth: 1000               # backpressure threshold
```

---

## `pub_sinks` — outbound destinations

```yaml
pub_sinks:
  - name: linear
    kind: linear
    config:
      team: support
      project: triage
    credentials_ref: env:LINEAR_API_KEY

  - name: sales_slack
    kind: slack_channel
    config:
      channel: "#sales"
      mention_on_post: true
    credentials_ref: secret:slack-bot-token   # secret manager ref

  - name: email_digest
    kind: email
    config:
      to: anu@example.com
      from: cuga@acme.com
      subject_template: "[Digest] {date}"
```

### Sink kinds

The core set: `slack_channel`, `slack_dm`, `webhook`, `email`, `linear`,
`jira`, `pagerduty`, `topic` (for agent → agent pub/sub, see swarm).

New kinds are pluggable: drop a Python class implementing the
`PubSinkAdapter` protocol, register its kind name in settings, and it's
usable from YAML.

---

## CLI

```bash
cuga validate rules.yaml             # JSON-Schema check, no DB write
cuga diff rules.yaml                 # show what would change
cuga apply rules.yaml                # validate + upsert + arm triggers
cuga apply rules.yaml --dry-run      # validate + diff, no writes
cuga apply rules/*.yaml              # merge multiple files
cuga export > current.yaml           # dump current state
cuga delete --by-file rules.yaml     # delete rows that came from this file
```

Apply is **transactional**: either every row in the file lands, or none of
them do. Half-applied state never exists.

---

## Provenance: who created each row

Every row in `subscriptions` / `agents` / `pub_sinks` carries a `source`
column:

| Source value | Meaning |
|---|---|
| `interactive:user@example.com` | Created via supervisor chat |
| `declarative:path/to/rules.yaml#sub-id` | Created via `cuga apply` |
| `hook:linear.create_ticket` | Auto-created by a hook subscription |

This matters for:

- **`cuga apply --prune`** can safely delete rows that disappeared from the
  YAML — but ONLY rows whose `source` matches `declarative:<this-file>`. It
  never touches user-added interactive rules.
- **Audit & debugging:** when something fires unexpectedly, you can trace it.
- **Migration:** `cuga adopt --interactive-to-yaml` rewrites
  `source=interactive:*` rows out as YAML you can paste into git.

---

## Reload behavior

When `cuga apply` runs:

1. **Parse + validate.** JSON Schema → semantic checks (do referenced
   agents/sinks exist? do trigger configs match their kind?).
2. **Diff against current state.** Compute the set of:
   - New rows to INSERT
   - Existing rows to UPDATE (changed since last apply)
   - Existing rows to leave alone (unchanged)
   - Existing rows to DELETE (only if `--prune` AND `source` matches this file)
3. **Apply transactionally.** Single SQL transaction; rollback on any error.
4. **Re-arm triggers.** Restart cron schedules, reload webhook routing, etc.
   This is `apscheduler.remove_job + add_job` for timed triggers; no
   downtime for unrelated subscriptions.
5. **Report.** Print a summary: `+3 created, ~2 updated, -1 deleted`.

---

## How this works with the routing agent

The CUGA routing agent's intelligent setup-time routing (the `delegate_to_*`
mechanism that picks `target_agent`) is **bypassed** for declarative rules,
because the human writing the YAML already specified `target.agent` directly.

This is the right tradeoff:

- **Interactive mode:** the user says natural language. The routing agent's
  LLM figures out which specialist agent should handle it. One LLM call per
  rule, but that's fine because there's a human waiting.

- **Declarative mode:** the YAML author *already knows* which agent should
  handle the standing intent. Forcing the routing agent to re-decide on apply would be
  redundant and slow. The YAML's `target.agent` is taken as truth.

Open-ended declarative rules (`target.agent: routing_agent`) still work —
the routing agent receives the event at runtime and dispatches per-event,
just like today's loops.

---

## Example: a full real-world file

```yaml
version: 1
metadata:
  app: cuga
  tenant: acme
  owner: ops@acme.com

subscriptions:

  # ─── Timed: weekly digest ───────────────────────────
  - id: monday-hn-digest
    trigger:    { kind: timed, cron: "0 9 * * 1" }
    target:     { agent: digest_agent }
    prompt:     "Fetch top 10 HN posts about LLM tooling, summarize, email."
    outcomes:   [{ emit_to: email_digest }]

  # ─── Push: customer support ─────────────────────────
  - id: support-email-triage
    trigger:
      kind: push
      source: email
      filter: { to: support@acme.com }
    target:     { agent: triage_agent }
    prompt:     "Classify as bug/sales; file ticket or ping #sales; reply."
    outcomes:
      - emit_to: linear
      - emit_to: sales_slack
      - reply_to: trigger

  # ─── Pull: external system without webhooks ─────────
  - id: openai-changelog-watch
    trigger:
      kind: pull
      interval: 6h
      source: { type: http_get, url: https://openai.com/changelog }
      diff:   { strategy: content_hash }
    target:     { agent: alerter }
    prompt:     "If a new model entry exists, summarize and DM the user."
    outcomes:   [{ emit_to: anu_dm }]

  # ─── Push + state-diff: Stripe MRR ──────────────────
  - id: stripe-mrr-drop
    trigger:
      kind: push
      source: webhook
      filter: { event_type: "invoice.payment_succeeded" }
      secret_ref: env:STRIPE_WEBHOOK_SECRET
    target:     { agent: finance_agent }
    prompt:     "Compute MRR. If down >5% WoW, draft churn analysis."
    outcomes:   [{ emit_to: finance_slack }, { emit_to: email_exec }]

agents:
  - name: digest_agent
    prompt_template: prompts/hn-digest.md
    tools: [hn_api, email]

  - name: triage_agent
    prompt_template: prompts/support-triage.md
    tools: [linear, slack, email_reply]
    policies:
      max_steps: 8
      timeout_seconds: 90

  - name: alerter
    prompt_template: prompts/alerter.md
    tools: [slack_dm]

  - name: finance_agent
    prompt_template: prompts/finance-analysis.md
    tools: [stripe_read, sheets, email, slack]

pub_sinks:
  - name: email_digest
    kind: email
    config: { to: anu@example.com, subject_template: "[Digest] {date}" }
    credentials_ref: env:SMTP_PASSWORD

  - name: linear
    kind: linear
    config: { team: support, project: triage }
    credentials_ref: env:LINEAR_API_KEY

  - name: sales_slack
    kind: slack_channel
    config: { channel: "#sales", mention_on_post: true }
    credentials_ref: secret:slack-bot-token

  - name: anu_dm
    kind: slack_dm
    config: { user: U012345 }
    credentials_ref: secret:slack-bot-token

  - name: finance_slack
    kind: slack_channel
    config: { channel: "#finance-alerts" }
    credentials_ref: secret:slack-bot-token

  - name: email_exec
    kind: email
    config: { to: ceo@acme.com, subject_template: "[Finance] MRR drop alert" }
    credentials_ref: env:SMTP_PASSWORD
```

That's 4 subscriptions × 4 agents × 6 sinks = a full small-tenant config in
about 80 lines. Same file, applied across staging and production, gives
identical behavior.

---

## JSON alternative

Same shape, just JSON for systems that emit it (Terraform, Pulumi, an LLM
that's not good at YAML):

```json
{
  "version": 1,
  "subscriptions": [
    {
      "id": "monday-hn-digest",
      "trigger": { "kind": "timed", "cron": "0 9 * * 1" },
      "target":  { "agent": "digest_agent" },
      "prompt":  "Fetch top 10 HN posts...",
      "outcomes": [{ "emit_to": "email_digest" }]
    }
  ],
  "agents": [{ "name": "digest_agent", "tools": ["hn_api","email"] }],
  "pub_sinks": [{ "name": "email_digest", "kind": "email",
                  "config": { "to": "anu@example.com" } }]
}
```

YAML is the recommended human form; JSON is the recommended emitted-by-tools
form. Both go through the same validator and applier.

---

## Status & open questions

This is a proposal. Some things to nail down before implementation:

- **Schema versioning:** when we add fields, do we bump `version: 1` → `2`,
  or use `$schema` URLs? Probably the latter for forward compat.
- **Secrets:** the `credentials_ref: env:FOO` and `secret:foo` syntax needs
  a small resolver layer that knows about env vars and the deployment's
  secret manager (Vault, AWS Secrets Manager, etc.).
- **Multi-file merging:** if `rules/*.yaml` produces overlapping `id`s, do
  we error or last-wins? Recommend error.
- **Hot-reload:** should we watch the file and re-apply on change? For
  development yes (with a `cuga apply --watch` flag); for production, no —
  apply is explicit.

None of these are blockers. Suggest implementing in M3-ish — once Phase 2
exists, declarative rules are valuable from day one of any new tenant.
