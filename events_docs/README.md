# Event-driven agents on CUGA

Turn CUGA from a *request/response* agent into an *event-driven* one: agents that watch inboxes,
folders, repos and schedules, converse on chat channels, and deliver answers back — all behind
`EVENTS_ENABLED`, with vanilla CUGA untouched when it's off.

**The one idea:** `/invoke` is the single seam. Every trigger, channel, and integration normalises its
event into one envelope and POSTs it there. Learn that endpoint and the rest follows.

## The docs kept in-repo (deliberately minimal)

| Doc | What it is |
|---|---|
| **[features.md](features.md)** | what the events layer covers — channels, triggers, control plane, what's gated |
| **[SETUP.md](SETUP.md)** + [setup/](setup/) | fresh machine → running stack, and a per-connector setup guide for each channel/integration (test-coupled: every trigger is documented here) |
| **[api/](api/)** | the API reference ([api.html](api/api.html)), the try-it spec ([api_spec.html](api/api_spec.html)), and the examples board ([examples.html](api/examples.html)) — all generated and test-coupled to the code |

That's it. Prose that drifts (decks, roadmaps, status boards, ADR narrative, snapshots) is **not**
kept here — it lived too far from the code to stay true. The source of truth is the code under
`src/cuga/backend/events/`; when a doc and the code disagree, the code wins and the doc is a bug.

## Testing

There is no prose test-guide (it drifted). Use the code:
- **Offline suite:** `make test` (or `pytest tests/events -q`) — fast, no network.
- **Live harnesses:** `tests/events/live_*.py` and the `make test-live` / `test-suite-now` targets.
- The API reference and per-connector setup guides are **enforced by contract tests** in
  `tests/events/test_events_api_contract.py` — add a route or trigger without documenting it and the
  build fails.

## Archived (moved out of the repo)

Point-in-time material — the ADRs, decks, roadmaps, status boards, snapshot dashboards, rendered
diagrams, and the (gated-off) action-half cluster — was moved to `~/Documents/GitHub/events_docs/`
to keep the repo lean and drift-free. None of it is required to build, run, or test the events layer.

## Conventions

- **Generated, don't hand-edit:** `api/api_spec.html` (`scripts/gen_api_spec.py`), the examples
  board's data (`scripts/gen_examples.py`). Offline tests fail the build if these drift from the code.
