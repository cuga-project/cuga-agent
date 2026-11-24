# CI test discovery

This file holds the detail for the rule "CI discovers tests by directory" in [AGENTS.md](../../AGENTS.md). The rules are in `AGENTS.md`; this file lists which tests run in which job.

CI shards in `.github/workflows/tests.yml` (and the matching commands in `external-fork-tests.yml`) select **directories and markers**, not individual `test_*.py` files. A new test is in CI when its directory is one of the shard roots below; adding a file path to YAML is the wrong change.

## Which job runs a test

| New test lives in | CI job | Notes |
|---|---|---|
| `tests/unit/` or `tests/integration/` | `unit-b` | Whole tree is collected; excluded only by marker (`manual`, `pgvector`, `load`, `e2e`, `stability`) |
| One of the 7 colocated dirs listed in the `unit-a` step | `unit-a` | **Not** a `src/**/tests/` glob — an explicit list, one pytest process per suite (singleton isolation) |
| `src/cuga/backend/cuga_graph/policy/tests/` | `policy-a` / `policy-b` | `test_e2e_*.py` vs the rest of that directory |
| `src/cuga/sdk_core/tests/` | `sdk` | CRUD, mocks, supervisor policy invoke tests, and a small live invoke smoke (`-m "not e2e"`). Live-LLM duplicates (summarization, extra invoke variants, JSON utterance matching) are `@pytest.mark.e2e` — same opt-out as `unit-b`. |
| `tests/system/` or `src/system_tests/load/tests/` | `extras` | |
| `@pytest.mark.load` mocked load | `load` | |
| `@pytest.mark.pgvector` | `pgvector-integration` | Needs the pgvector service job |
| `@pytest.mark.stability` | stability workflow | Not the unit-tests aggregator |

## Known gap: directories that run in no job

`unit-a`'s directory list does not cover every colocated suite. These directories currently run in **no** job:

- `backend/evolve/tests`
- `demo_tools/crm/tests`
- `tools_env/code_sandbox/tests`
- `cuga_lite/executors/filesystem/tests`
- `tests/e2e/`

Adding a test there means it runs locally but never in CI — extend the `unit-a` list (a directory, not a file) if you need coverage.
