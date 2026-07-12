# Architecture

The event-driven layer turns CUGA from a request/response agent into one that reacts to the world —
a message in Slack, a new email, a cron tick, a repo webhook, a file in Box. It is **additive**:
everything lives behind `EVENTS_ENABLED`, and vanilla CUGA is byte-for-byte unchanged when it is off.
The whole layer is self-contained under `src/cuga/backend/events/` (one flat package; the only core
touch points are two guarded hooks in the server startup).

## The two pieces

The whole system is **create a flow** (arm-time) and **fire a flow** (run-time).

- **CREATE** — connect a credential, then turn an English sentence into a standing flow. The concierge
  builds the flow and bakes in a *reference* to the credential, never the secret.
- **FIRE** — a trigger fires, `/invoke` runs the agent, the answer is delivered.

![system](architecture/system.png)

**`/invoke` is the single seam.** Every fire path normalises its event into one envelope —
`{agent, source, event}` — and POSTs it to `/invoke`. Learn this one endpoint and the rest follows.

## Credentials — set up, transferred, resolved

The agent never sees a token. There are three credential models, and the sequence diagrams below make
each explicit:

1. **OAuth via AP** (gmail · github · box-AP) — you consent in the browser; the provider redirects to
   CUGA's callback with an authorization **code**; CUGA hands AP the *code* (not a token); **AP
   exchanges it and stores an `OAUTH2` connection** and owns the refresh lifecycle.
2. **Token via AP** (telegram · discord-AP) — you paste a secret; AP stores it as a `SECRET_TEXT`
   connection.
3. **Direct env token** (box-direct · slack · discord) — the token lives in CUGA's `.env`
   (`BOX_DEV_TOKEN`, `SLACK_BOT_TOKEN`); CUGA's own adapter uses it. Still never handed to the agent.

For (1) and (2), the armed flow references the connection as `{{connections['ea::tenant::user::app']}}`.
**At fire time, AP resolves that reference to the real token inside its own sandbox** and uses it to
poll/receive — the token never crosses to CUGA or the agent. This is the security boundary
([ADR-0001](decisions/0001-ap-as-the-event-engine.md), [ADR-0006](decisions/0006-auth-connection-model.md)).

Two other facts that trip everyone up:

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

## Sequence diagrams

Grouped by the two pieces. Credentials (🔑) are called out in every one.

### CREATE — arm-time

**① Connect a credential** — the OAuth handshake, and where the token ends up (AP, forever).

![connect](architecture/create-1-connect-credential.png)

**② Arm a flow** — the concierge turns a sentence into a flow and bakes in the *connection reference*.

![arm](architecture/create-2-arm-flow.png)

### FIRE — run-time

The eight distinct fire shapes. Every other trigger/channel/integration combination is a variant of
one of these.

| Diagram | Shows | Credential |
|---|---|---|
| ![now](architecture/fire-1-now.png) | **① NOW** — one-shot question, no flow | none (the tool authenticates at the MCP server) |
| ![schedule](architecture/fire-2-schedule.png) | **② CRON / POLL** — scheduled fire | none (a pure schedule; agent uses MCP tools) |
| ![github](architecture/fire-3-push-github.png) | **③ PUSH · GitHub** — webhook trigger | AP resolves the OAuth connection in its sandbox |
| ![gmail](architecture/fire-4-push-gmail.png) | **④ PUSH · Gmail** — polling trigger | AP resolves the OAuth connection (same model) |
| ![box](architecture/fire-5-box-direct.png) | **⑤ PUSH · Box** — direct poll + download | CUGA-held `BOX_DEV_TOKEN` (direct model, no AP) |
| ![slack](architecture/fire-6-channel-slack.png) | **⑥ Channel · Slack** — direct, signed | CUGA-held `SLACK_*` (signing secret + bot token) |
| ![telegram](architecture/fire-7-channel-telegram.png) | **⑦ Channel · Telegram** — AP backend | AP bot-token connection (Discord = direct WS bot) |
| ![webhook](architecture/fire-8-webhook.png) | **⑧ Generic webhook** — any system → agent; the agent is either **pinned** (`?agent=`) or **routed** (`?route=1`, the concierge picks it by capability, like chat) | `EVENTS_WEBHOOK_KEY` (unset = open) |

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
