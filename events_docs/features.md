# Event-driven agents — features

What the events layer covers, grouped by concern. Everything here is behind `EVENTS_ENABLED`
(default off → vanilla CUGA). "No AP" = works with zero Activepieces; "AP" = needs Activepieces as
the connector/token custodian for that SaaS.

## Channels — converse with an agent (all direct, no AP)
| Channel | Transport | Public URL? |
|---|---|---|
| Web chat | built-in | no |
| Slack | Events API webhook (inbound) | yes |
| Discord | Gateway websocket (outbound) | no |
| Telegram | long-poll `getUpdates` (outbound) | no |

The AP webhook path for a channel is opt-in per channel via `EVENTS_<CHANNEL>_BACKEND=ap`.

## Triggers — what an agent watches
**Native (no AP), fired by the in-process scheduler / direct streams:**
- **cron** — a fixed clock schedule ("every weekday at 8am").
- **poll** — re-check on an interval and report; **stateful delta** decides *whether* to report:
  - Tier 0 `always` (plain cron) · Tier 1 `threshold` (numeric move ≥ X%) · Tier 1 `identity`
    (new items, dedup by key) · Tier 2 `fuzzy` (agent judges "materially changed").
- **webhook** — any external system POSTs an event in.
- **direct channel watchers** — a reaction / new message / new member on Slack/Discord/Telegram.
- **Box-direct** — a token-poll watcher on a Box folder.

**Via Activepieces (SaaS push):** Gmail · GitHub · Box (OAuth) · Google Calendar · Pinterest · RSS ·
YouTube — AP holds the per-user OAuth token and pushes the event to CUGA.

## Control plane — turning language into a flow
- **NL→Flow concierge** — an LLM router that arms cron/poll/push flows from plain English, and
  answers immediate questions directly.
- **Slash commands** — `/automate` (router picks the mode) plus explicit `/watch /schedule /cron
  /poll /push`; deterministic, no LLM. `/start` + `/link` bind a channel account to a user.
- **Dry-run** — preview exactly what an utterance *would* arm, with zero side effects.

## Delivery & identity
- **Direct-channel delivery** — the answer goes back to where the watcher was armed (no AP send step).
- **Per-user identity** — channel accounts link to profiles; runs are scoped per user/tenant.
- **Credential model** — AP holds SaaS tokens encrypted; the agent never sees them. Tool credentials
  that CUGA owns are managed by CUGA.

## Agents
- **One addressable agent, `cuga`** — a supervisor that routes to its own specialists internally
  (sub-agents defined in `supervisor_agents.yaml`, `EVENTS_SUPERVISOR=1`). The concierge never picks
  a specialist.

## UI & operations
- **Event Studio** (`/studio`) — dashboard, subscriptions, and runs; shown only when events is on.
- **Runs / subscriptions APIs** — filterable by mode / backend (native vs AP) / status / agent.
- **Capability report** — on startup, states exactly what's live (channels, scheduler, AP or not).

## Triggers-only (a deliberate boundary)
- **No connector actions in the events layer.** It arms triggers and delivers the agent's answer;
  it does not run connector *actions* (e.g. Gmail reply/draft). Anything the agent should *do* it
  does through its own tools — that keeps credentials and side-effects inside the agent, not the
  event plumbing.
- **Legacy backends** — the AP-schedule path (`EVENTS_SCHEDULER=ap`) and AP channel backends
  (`EVENTS_<CHANNEL>_BACKEND=ap`) remain as escape hatches; native/direct are the defaults.

## Deploy
- Runs as one FastAPI process. AP-free single-instance is the simplest deploy; horizontal scale
  (multi-replica scheduler coordination, externalized state) is future work.
