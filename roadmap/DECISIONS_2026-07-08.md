# Session Decisions & Learnings — 2026-07-08

A working record of a deep-dive session on **NL → Flow** and the **event-driven runtime**
(`src/cuga/backend/events/`). Focus: how arming actually works across channels, the Box
content gap, the AP-as-sink architecture decision, and a concrete build plan for a **stateful
poll**. Companion to [LEARNINGS.md](LEARNINGS.md) and [summary.html](summary.html).

> Legend: ✅ verified in code · 🟡 partial / emulated · ⛔ not built · 🔨 decided-to-build ·
> ❓ open decision.

---

## 1. Mental models that clicked

- **The arm is channel-agnostic.** Whether you type from Slack or Telegram, the middle —
  concierge decides → `find_or_create_flow` → builds the AP flow — is byte-for-byte the same.
  **Only the two *ends* differ**: how your words reach the concierge (inbound transport) and
  how the answer returns (delivery transport).
- **`thread_id` is the courier.** A flow's `thread_id` (`<scope>::gw:<channel>:<native>`)
  carries the *return address* across the hours-long gap between arming and firing. A Gmail
  push flow whose `source` is `gmail` still knows to deliver to `#C123` because the Slack
  origin is frozen into its `thread_id`. `channel_origin()` digs it back out at fire time.
- **AP is stateless plumbing with one verb: "POST this frozen envelope to `/invoke`."**
  Everything smart — which agent, what to read, where to send — is decided by CUGA at arm
  time (frozen into the envelope) or done by CUGA at fire time.
- **Every trigger normalizes to one shape**: `trigger → POST /invoke → (deliver)`. Channels,
  cron, push all share it. `/invoke` is the single seam.

## 2. How the pieces divide (verified)

| Concern | Owner | Code |
| --- | --- | --- |
| Reasoning / the answer | **CUGA** agent graph | `runtime.run` via `/invoke` |
| NL → which automation | **CUGA** concierge (LLM router) | `concierge.py` |
| The connectors (Gmail, Box, GitHub, Slack…) | **AP pieces** | `flows.py` `PIECE` / `SOURCE_TRIGGER` |
| OAuth tokens + refresh | **AP** | `ap_engine.ensure_oauth_connection` |
| The clock (cron/poll) + app-event triggers | **AP** | `ap_engine.create_schedule_flow` / `create_push_flow` |

Nuance: **channels → direct, integrations → AP.** Slack/Discord bypass AP inbound *and*
outbound (`slack_direct`, `discord_direct`) because AP's Slack piece emitted empty payloads
and couldn't take a pre-issued bot token. Telegram stays on AP both ways.

## 3. The channel traces we walked

- **Slack (direct):** inbound via CUGA endpoint `/api/events/slack/events`; delivery via
  `delivery.send_direct` → `chat.postMessage`. The `thread_id` mechanism is sufficient. ✅
- **Telegram (AP):** inbound via a **standing AP inbound flow**
  (`telegram·new_message → /invoke → telegram·send`); delivery via an **AP send step**.
- **The Telegram push gap (found in code):** `create_push_flow` appends **no send step** and
  `is_direct("telegram")` is `False`, so a **push flow armed from Telegram has no wired
  delivery back to Telegram** — it falls to the capture sink. Slack only works because it's a
  *direct* channel. The push delivery variables are *computed* in `concierge.py:270-277` but
  the push branch **never passes them** to `create_push_flow` (only the cron/poll branch uses
  them). 🟡

## 4. The Box content gap — diagnosed

- A **trigger** (AP `new_file` or our `box_direct` poller) only reports **metadata** —
  `id, name, type, created_at`. Nobody downloads the file. The payload the agent sees is
  `{name, id}` (`flows.py:50`). So "check if it's a resume / matches the JD" fails: the agent
  sees `resume_jane.pdf`, not its words. ✅ (documented gap)
- **It is not an AP limitation and not a Box limitation — it's a wiring choice.** We hold a
  valid Box token and each file's `id`; content is one `GET /files/{id}/content` away.
- **The real wrinkle: PDFs need text extraction**, not raw bytes. Two clean ways: extract
  locally (pypdf) or use **Box's text-representation API** (`[extracted_text]`).
- **Three places the download could live:** (a) CUGA pre-fetch step [best — works for any
  agent], (b) AP download action [clunky for binaries/PDF], (c) an agent Box-read tool
  [flexible, but re-opens "agent holds a credential"].

## 5. DECISION — AP owns trigger + all sinks (Route A)

**Guiding rule chosen this session:** *anything to do with channels/integrations is handed to
AP.* Concretely:

- **AP owns:** the trigger, all OAuth/connections, routing (the branch), **and all sends**
  (Slack #work, Gmail x@…). 🔨
- **CUGA owns:** reasoning only (classify, match a JD, compose the message text).
- **The agent carries no integration tools and no credentials.** It returns a labeled verdict
  (`MATCH: …` / `SKIP: …`) + the message text; AP routes on the prefix and delivers.

This matches the project's founding rule ("don't build an OAuth/connection framework — AP owns
that") and rejects the earlier fallback of an agent that self-delivers via its own tools
(Route B) — Route B was only ever the "works today with no build" option.

**What Route A costs to build:** port the branch primitive that already exists in the
spec layer (`flows.py` `router_step` + `send_step`, and `build_resume_watcher_flow` =
`Box·NewFile → judge → Router⟨MATCH→… / else→…⟩`) into the **live REST engine**
(`ap_engine.py`), which today has **no ROUTER op** — only linear `trigger → http → publish`.
Add: (1) a ROUTER `ADD_ACTION` op branching on `{{step_1.body.answer}}`, (2) per-branch send
ops (reuse `_channel_send_op` for Slack, add a Gmail `send_email` action). ⚠️ Verify the
router-with-children op addressing against the live AP build first.

**The exception — file-content download.** AP hands *references*, not extracted text, so this
is the one spot outside "AP owns everything." Handle **on a need basis**: either the agent
gets a read tool, or we insert a download-and-extract step (CUGA-side `download_text`). Decided
per-integration, not globally. 🔨

**Two UX wrinkles of AP-as-sink to remember:**
1. The agent's single answer string doubles as *routing signal* and *delivered message*
   (`send` uses `{{step_1.body.answer}}` whole) — so `MATCH:` would leak into Slack unless the
   agent returns a structured `{route, message}` and the ops read named fields.
2. The branch is **static** — fixed at arm time, matched by text prefix. Great for a known
   2-way split; "N destinations / dynamic routing" would need a rebuild (that's when Route B's
   agent-side reasoning scales better). This is the "reason vs build" fork.

## 6. Poll needs a real state primitive — the build we scoped

**Framing agreed:** `now / push / cron` are the *easy* ones (integration-native). **`poll` is
harder — it needs state.**

**Why it's the weak link today (verified):** a "poll" flow is **identical to a cron flow**
(same `create_schedule_flow`, same `schedule → /invoke → deliver`) with **one appended prompt
sentence** — *"Only report if it changed since last time; else say nothing changed"*
(`concierge.py:379`). There is:
- **no `get_state`/`set_state`** — `flows.py:211/215` mentions them and sets a `__mode="poll"`
  marker, but `ap_engine` never reads it and no state store exists. Design intent, not built. ⛔
- **wrong semantics** — the prompt compares to the *last tick*; "up 10%" means vs a *baseline*.
  Nowhere stores a baseline. 🟡
- **no no-op suppression in the plumbing** — `catalog.py:86` claims "a no-change tick delivers
  nothing", but `/invoke` delivers whatever the agent returns → "nothing changed" spam. ⛔
- the deterministic classifier even **mis-reads the phrasing** — "if IBM stocks **go up**"
  doesn't match `_POLL` (needs "it … goes"); via `/automate` it falls to NOW → forced PUSH →
  "no agent wired for that source." Only the LLM path has a shot. 🟡

**The design (mirrors `box_direct`, which already proves the pattern — AP schedule = dumb
clock, a CUGA endpoint holds per-subscription state and fires on transition):**

Split the poll into three roles so no single LLM call is sensor + comparator + memory:
- **Sensor** → returns a *number* (an AP price piece if one exists; else the agent, which
  holds the price tool). *We confirmed: no AP stock piece → CUGA/agent is the sensor
  (Variant 2).*
- **Comparator + state** → deterministic CUGA code. Baseline math is not an LLM job.
- **Notifier** → runs only on a crossing → no-op suppression for free.

**Control flow (locked):** `AP schedule → POST /api/events/poll → (the endpoint) calls the
agent for the number → compares → maybe delivers`. AP calls the *endpoint*, never the agent;
the agent never calls `/poll`.

**Five touch-points, each a near-copy of a Box counterpart:**
1. `poll_state.py` (new) — per-subscription KV `{baseline, last, alerted}` (copy
   `box_direct.load_since/save_since`).
2. `ap_engine.create_value_poll_flow` (new) — `schedule → POST /api/events/poll` (copy
   `create_box_poll_flow`).
3. `POST /api/events/poll` (new) — the comparator+state+deliver endpoint (copy `/box/poll`).
4. `find_or_create_flow` poll branch — capture `threshold_pct/direction/metric` + capture the
   **arm-time baseline**, arm via `create_value_poll_flow`.
5. concierge prompt/tool — extract the threshold slots; **default the cadence** with a
   one-line confirm instead of erroring on a missing interval.

**Nothing existing changes.** `/invoke` is untouched and still serves NOW/channels/cron/push;
only the **poll kind** re-points from `schedule→/invoke` to `schedule→/poll`. `/poll` reuses
`runtime.run` (sensor) and `delivery.send_direct` (deliver) — a thin stateful wrapper, exactly
like `/box/poll` was added without touching `/invoke`.

**Effort:** ~1 focused day; the comparator is pure and offline-testable.

**❓ Two open semantic decisions (needed before coding):**
1. **Baseline** = the price at the moment of arming? (default: yes) vs today's open / prev close.
2. **After it fires once** → stop, or keep watching for the next +10%? (default: stop/latch.)

## 7. Why poll is the high-leverage fix

The `poll_state` KV + "deliver only on a state transition" is the **same primitive** behind the
hard workflows: digest ("collect PRs, release at 6pm"), escalation ("if no ack in 15 min"),
negative-follow-up ("if they *didn't* reply in 3 days"). Poll-threshold is the smallest, cleanest
place to build durable per-subscription state; the harder ones inherit it.

## 8. Status against the requirement spec

See [spec_status.html](spec_status.html) — `events.md` rendered with a per-capability
assessment (done / partial / not-built) and a review column to correct. Headline: **now / push
/ cron + the core integrations (Box, Gmail, GitHub) and 4 channels are done; poll-with-state,
file-content, generic pub, WhatsApp/Calendar, audio, and swarm are the open work.**
