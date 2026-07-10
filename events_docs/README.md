# Event-driven agents on CUGA

Turn CUGA from a *request/response* agent into an *event-driven* one: agents that watch inboxes,
folders, repos and schedules, converse on chat channels, and deliver answers back — all behind
`EVENTS_ENABLED`, with vanilla CUGA untouched when it's off.

**The one idea:** `/invoke` is the single seam. Every trigger, channel, and integration normalises its
event into one envelope and POSTs it there. Learn that endpoint and the rest follows.

## The docs, in order

| # | Doc | What it is |
|---|---|---|
| 1 | **[ARCHITECTURE.md](ARCHITECTURE.md)** + [architecture/](architecture/) | how it works — the system diagram + a sequence diagram per flow shape |
| 2 | **[TESTING.md](TESTING.md)** | the offline gate, the live harnesses, and the consolidated report |
| 3 | **[PHASES.md](PHASES.md)** | Crawl · Walk · Run · Sprint · Fly — what each phase is, and where we are (P3 ~75%) |
| 4 | **[decisions/](decisions/)** + **[GAPS.md](GAPS.md)** | the ADRs (why), and the honest known-gaps + sharp-edges list |
| 5 | **[ROADMAP.md](ROADMAP.md)** | the sequenced "what's next" |
| 6 | **[SETUP.md](SETUP.md)** + [setup/](setup/) | fresh machine → running stack; per-connector setup guides |
| 7 | **[api/](api/)** | the API reference ([api.html](api/api.html)), the try-it spec ([api_spec.html](api/api_spec.html)), and the examples board ([examples.html](api/examples.html)) |

## New here? Read in this order

1. This page, then **[ARCHITECTURE.md](ARCHITECTURE.md)** for the model + diagrams.
2. **[SETUP.md](SETUP.md)** to get a stack running.
3. **[TESTING.md](TESTING.md)** to prove it works (`make test`, then `make test-report`).
4. **[PHASES.md](PHASES.md)** / **[ROADMAP.md](ROADMAP.md)** for where the project stands and heads.

## Conventions

- **Generated, don't hand-edit:** `api/api_spec.html` (`scripts/gen_api_spec.py`), the diagrams
  (`architecture/gen_diagrams.py`), `results/index.html` (`scripts/run_all_tests.py`). Offline tests
  fail the build if the generated artifacts drift from the code.
- **Source of truth is the code** under `src/cuga/backend/events/`. When a doc and the code disagree,
  the code wins and the doc is a bug.
