# Architecture

The event-driven layer turns CUGA from a request/response agent into one that reacts to the world —
a message in Slack, a new email, a cron tick, a repo webhook, a file in Box. It is **additive**:
everything lives behind `EVENTS_ENABLED`, and vanilla CUGA is byte-for-byte unchanged when it is off.
The whole layer is self-contained under `src/cuga/backend/events/` (one flat package; the only core
touch points are two guarded hooks in the server startup).

## The one idea

**`/invoke` is the single seam.** Every trigger, channel, and integration normalises its event into
one envelope — `{agent, source, event}` — and POSTs it to `/invoke`. Upstream is just different ways
of producing that envelope; downstream is the worker fleet and delivery. Learn this one endpoint and
the whole system follows.

![system](architecture/system.png)

Two facts that trip everyone up:

- **Activepieces calls back over `HOST_CALLBACK_URL` (podman-internal DNS), not the public tunnel.**
  The public cloudflared tunnel is *inbound only* — it exists so GitHub/Slack webhooks and OAuth
  callbacks can reach CUGA. That tunnel is **ephemeral**: when it dies, every flow fails with
  `INTERNAL_ERROR` on AP's payload callback. Fix: `make ap`. (See [GAPS.md](GAPS.md).)
- **Delivery is one of two paths**, chosen by `delivery.is_direct(channel)`: CUGA's own direct
  adapter (Slack, Discord, Box) or an Activepieces send-step (`{{step_1.body.answer}}`). The sink is
  parsed from the `thread_id` origin — which is why a Gmail-sourced flow can deliver to Slack.

## The pieces

| Component | Role |
|---|---|
| **`/invoke`** | the seam. Envelope in → agent runs → answer out. `X-Gateway-Token` auth. `meta.mcp` = the tools that actually ran. |
| **Concierge** | the NL→flow router (`POST /api/concierge`). Classifies an utterance, picks a pre-built agent, reuses-or-creates the AP flow. It **never creates agents** — it routes among the fleet. |
| **Worker fleet** | 18 pre-built agents (`seed.py`), each = prompt + MCP tools + access rules. On the `cuga` backend, each materialises its own `DynamicAgentGraph`. |
| **MCP tool servers** | the agents' hands: `cuga-finance · geo · web · knowledge · code · text`. Attached per agent by name (see [MCP notes](#mcp-tools)). |
| **Activepieces (AP)** | owns triggers **and all credentials** (the agent holds none). Schedule/gmail/github/telegram pieces. Connections are OAUTH2 or SECRET_TEXT. |
| **Delivery** | direct adapter or AP send-step; sink from the `thread_id` origin. |
| **Studio UI** | a *dumb* React console — it renders exactly what the read endpoints report, no client-side business logic. |

## What is reused vs added vs delegated

The layer is deliberately thin. It **reuses** CUGA's graph/agent machinery (a worker *is* a
`DynamicAgentGraph`), **adds** only the events package + two guarded startup hooks, and **delegates**
all triggers and credentials to Activepieces. It builds **no** connection/OAuth framework of its own —
AP owns that. That is why merging it is non-breaking.

## Backends — two per concern

- **Worker backend** (`EVENTS_WORKER_BACKEND`, default `cuga`): how an agent runs. `cuga` = a full
  CUGA graph per agent; `react` = a lighter loop. The concierge itself is `react`.
- **Channel backend**: how a chat channel connects. **Slack + Discord = direct** (no AP — Slack via
  its Events API, Discord via a Gateway WebSocket bot). **Telegram = AP** (polling trigger + send
  step). Web is a plain `/api/concierge` call. See [ADR-0008](decisions/0008-direct-backends-for-channels.md).
- **Box backend** (`EVENTS_BOX_BACKEND`): `direct` (CUGA polls Box with a token and **downloads file
  content** server-side, inlining text / base64-ing binary into the prompt) vs the AP push trigger.

## Sequence diagrams — one per flow shape

The nine shapes below are genuinely distinct in code; every other trigger/channel/integration
combination is a variant of one of them.

| Diagram | Shows | Distinct because |
|---|---|---|
| ![now](architecture/seq-01-now.png) | **NOW** — one-shot question | no AP, no flow; the answer is the HTTP response |
| ![concierge](architecture/seq-02-concierge.png) | **Concierge** — sentence → armed flow | the NL→flow door; `?flow=1` returns the flow |
| ![cron/poll](architecture/seq-03-cron-poll.png) | **CRON / POLL** — scheduled fire | AP owns the trigger; callback is internal |
| ![push github](architecture/seq-04-push-github.png) | **PUSH · GitHub** — webhook trigger | inbound via tunnel; OAuth conn + `admin:repo_hook` |
| ![push gmail](architecture/seq-05-push-gmail.png) | **PUSH · Gmail** — polling trigger | AP polls; can't be fired out of band |
| ![push box](architecture/seq-06-push-box.png) | **PUSH · Box** — direct poll + download | no AP; CUGA fetches file *content* server-side |
| ![channel slack](architecture/seq-07-channel-slack.png) | **Channel · Slack** — direct, signed | no AP; signature-verified; reply in-thread |
| ![channel telegram](architecture/seq-08-channel-telegram.png) | **Channel · Telegram** — AP backend | polling trigger + AP send (Discord = direct WS bot) |
| ![webhook](architecture/seq-09-webhook.png) | **Generic webhook** — any system → agent | no AP, no piece; `?key=` guards it |

SVG sources + the generator live in [architecture/](architecture/); regenerate with
`python events_docs/architecture/gen_diagrams.py`.

## Invariants (the non-negotiables)

- **Additive.** Behind `EVENTS_ENABLED`; vanilla CUGA unaffected when off.
- **Multi-agent preserved.** A worker agent *is* a CUGA graph; `thread_id` keys per-thread memory.
- **AP owns credentials.** The agent never sees a token. This is the security boundary.
- **Reuse before create.** The concierge de-dupes on `dedup_key = agent·source·cadence·sink·owner`.
- **The UI is dumb.** It renders backend state; all logic is server-side.

## MCP tools

Workers reach the world through MCP servers registered in `mcp_catalog.py` and bridged in
`_cuga_bridge.py`. An agent lists servers by name (`mcp_servers=["cuga-finance"]`); the runtime
attaches them. One gotcha: MCP tool names hyphenate on the wire and underscore in code
(`cuga-web` → `cuga_web`), handled by the bridge. The Studio's agent editor reads the live catalog
from `GET /api/events/mcp-servers`, so it never hardcodes the list.

## Studio UI (the dumb console)

The Studio reads only these endpoints and renders them verbatim: `status`, `channels`,
`integrations`, `agents`, `mcp-servers`, `subscriptions` (+ `/flow`, pause/resume/delete), `runs`,
`examples`, `setup-guides`, `me`, `connections`, `admin/*`. Writes it performs: `POST /api/concierge`
(chat), agent create/edit, and `connect/{app}` (OAuth window) vs `connect/{app}/token` (paste). The
connect button branches on the integration's `auth` field — `oauth` opens a consent window, anything
else prompts for a token. See the full API in [api/](api/).

## Further reading

Design rationale and trade-offs: the [ADRs](decisions/). Phase plan: [PHASES.md](PHASES.md). Known
limitations and sharp edges: [GAPS.md](GAPS.md). Testing: [TESTING.md](TESTING.md).
