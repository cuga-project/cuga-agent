# Event-driven agents on CUGA

Turn CUGA from a *request/response* agent into an *event-driven* one: agents that watch inboxes,
folders, repos and schedules, converse on chat channels, and deliver answers back — all in **a
second service beside CUGA**, never bolted into it. Don't deploy it and CUGA is unchanged.

**The one idea: CUGA is the door.** Every message from every channel lands on CUGA's `POST /run`,
and CUGA — not the eventing service — decides whether this one is ordinary chat or an attempt to arm
something. An explicit slash verb (`/automate`, `/watch`, `/schedule`…), or a thread with an arming
dialogue already open, goes to the eventing service. Everything else is ordinary chat and never
touches it.

`/invoke` is still a seam, but a narrower one than it used to be: it is the **fire** seam
(scheduler → `/invoke` → CUGA `/run`) and the inbound-webhook seam. Channels no longer post there —
they post to `/run` like everyone else.

## The docs kept in-repo (deliberately minimal)

| Doc | What it is |
|---|---|
| **[TRY_IT_DEPLOYED.md](TRY_IT_DEPLOYED.md)** | **START HERE** — try the deployed CUGA in a browser. Nothing to install; shareable with anyone. |
| **[ARCHITECTURE.md](ARCHITECTURE.md)** | the door, the building blocks per process, and sequence diagrams for all four flows (chat · arming · fire · webhook) |
| **[AUTOMATION_COOKBOOK.md](AUTOMATION_COOKBOOK.md)** | what to actually type — copy-pasteable asks matched to the 8 sub-agents in the roster |
| **[SETUP.md](SETUP.md)** + [setup/](setup/) | fresh machine → running stack, and a per-connector setup guide for each channel/integration (test-coupled: every trigger is documented here) |
| **[api/](api/)** | the API reference ([api.html](api/api.html)) and the examples board ([examples.html](api/examples.html)) — Studio UI assets, test-coupled to the code: a route with no entry in `api.html` fails the build |
| **[../events/deploy/](../deploy/README.md)** | deploy the events layer to IBM Code Engine (no-AP) and test/operate it from your machine (`make ce-*`, `make test-e2e-ce`) |

That's it. Prose that drifts (decks, roadmaps, status boards, ADR narrative, snapshots) is **not**
kept here — it lived too far from the code to stay true. The source of truth is the code under
`src/cuga/backend/events/`; when a doc and the code disagree, the code wins and the doc is a bug.

## The database (read this before running anything)

`EVENTS_DB` takes a **`postgresql://` URL**, and **local dev runs the same engine as the
deployment**. That is the whole point: local used to be a durable SQLite file while Code Engine ran
SQLite on an ephemeral disk, so the fragile path was the only one nobody exercised — and a pod
replacement silently deleted armed flows with no restart recorded.

```bash
make pg          # local PostgreSQL 16 in a container, prints the DSN  (there is NO native install)
make test-pg     # the store tests against the real engine
make pg-psql     # a psql shell   ·   make pg-stop / make pg-reset
```

Deployed: `events/deploy/4_postgres.sh` provisions a managed instance once and writes the DSN into the
Code Engine secret. SQLite still works (`EVENTS_DB=<path>`) and is what the hermetic offline suite
uses, but it is not what we deploy. Details: [ARCHITECTURE.md](ARCHITECTURE.md) §7.

## Testing

There is no prose test-guide (it drifted). Use the code:
- **Offline suite:** `make test` (or `pytest tests/events -q`) — fast, no network, SQLite.
- **Store tests on real Postgres:** `make test-pg` — the SQL path that actually ships.
- **Local e2e:** `make test-e2e` (no-AP) / `make test-ap` (with AP) — arm **and** fire across channels.
- **Deployed (Code Engine):** `make test-e2e-ce` runs the same channel + native-fire e2e against the
  live app; `make ce-status` / `ce-logs` / `ce-smoke` operate it from local. See
  [../deploy/README.md](../deploy/README.md).
- The API reference and per-connector setup guides are **enforced by contract tests** in
  `tests/events/test_events_api_contract.py` — add a route or trigger without documenting it and the
  build fails.

## Archived (moved out of the repo)

Point-in-time material — the ADRs, decks, roadmaps, status boards, snapshot dashboards, rendered
diagrams, and the (gated-off) action-half cluster — was moved to `~/Documents/GitHub/events/docs/`
to keep the repo lean and drift-free. None of it is required to build, run, or test the events layer.

## Conventions

  An offline test fails the build if it drifts from the code. `api.html` is hand-written, and is
  likewise test-coupled — every route must appear in it.
