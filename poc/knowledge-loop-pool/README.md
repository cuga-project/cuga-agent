# PoC: knowledge search on the engine's event loop (an alternative to PR 1213)

Branch `poc/knowledge-loop-pool` (no pull request). It starts from `main` (54d7722bb) plus
`e17c9f359` from PR 1213 (the #1208 fix, authored by Ido Levy) as its own commit (`c6e25d902` here).

## The idea

On `main`, every search by meaning runs `asyncio.run` in a worker thread. Each search therefore
creates an asyncpg pool, prepares it, and closes it. That setup costs about 40 ms per search.

- **PR 1213** moves all knowledge Postgres work to one background event loop thread that the
  process starts, with one pool per database on that loop.
- **This PoC** awaits the search on the engine's own event loop, with one pool per
  (event loop, database). The knowledge metadata store already keeps an asyncpg pool on this
  loop, and `engine.aclose()` closes it. The PoC follows the same pattern, so there is no new
  thread and no new lifecycle contract.

## What changed (4 source files, +175 / −42)

| File | Change |
|---|---|
| `storage/embedding/prod.py` | `_new_pool()`: the #1208 per-connection setup, used by both kinds of pool. `loop_pool(url)`: one pool per (loop, database), at most 8 connections. `close_loop_pools()`. `ProdEmbeddingStore(use_loop_pool=True)`: the table is checked once per pool. If the table was dropped, `search` returns nothing, and the next search checks the table again. |
| `knowledge/vector_store_base.py` | `asearch()`: by default, it runs `search` in a worker thread. |
| `knowledge/storage/adapter.py` | `asearch()`: embeds the query in a worker thread, then awaits the store on the caller's loop. `drop()` clears the search store, so a search that is still running finds nothing. |
| `knowledge/engine.py` | The dense leg awaits `adapter.asearch` at its 5 call sites. `aclose()` calls `close_loop_pools()`. |

Not changed: uploads, deletes, drops and keyword search keep the `main` path. The PR also
leaves keyword search as a follow-up.

This design does not need the background loop thread, the per-store connection caps, the patch
of asyncpg's private `_queue`, `recreate_if_missing`, or fork handling.

## Licenses (the project is Apache-2.0)

| Package | License | In `.github/licenses/policy.json` | Used by the PoC |
|---|---|---|---|
| asyncpg | Apache-2.0 | Approved | Yes (already a dependency) |
| pgvector | MIT | Approved | Yes (already a dependency) |
| psycopg, psycopg-binary, psycopg-pool | LGPL-3.0-only | Not approved ("requires review") | No |

The PoC adds no dependency. Its new code is original and falls under the repository's
Apache-2.0 license. I first considered a thread-safe `psycopg_pool.ConnectionPool`, which would
avoid event loops entirely, but dropped it for the license reason above.

## Tests

- **Unit:** 6 new tests in `tests/unit/test_knowledge_search_loop_pool.py`:
  - one pool per loop, shared by all collections;
  - a new pool for each new loop, and each pool is closed;
  - the table is checked once per pool;
  - a dropped table reads as empty and is checked again;
  - a search that is still running during a drop runs no DDL;
  - the policy store still fails on a missing table.

  934 existing knowledge and embedding unit tests pass. One test fails on this Mac only because
  Tesseract and EasyOCR are not installed (an OCR test, unrelated to this change).
- **pgvector:** 3 new tests in `tests/integration/test_knowledge_pgvector_loop_pool.py`:
  - 96 searches across 12 collections use at most 8 connections, and 0 remain after
    `close_loop_pools()`;
  - 16 searchers keep running while the collection is dropped, and none fails;
  - an upload runs while searches run, and every row is found afterwards.

  The full CI pgvector command (`-m pgvector tests/unit tests/integration`) gives
  **58 passed**. It ran on local Postgres 16.15 with pgvector 0.8.7 (CI uses 0.8.0), with
  `--ignore=tests/unit/acp` because the local venv lacks a package that folder imports.

## Benchmark

Setup: one process, 1,000 chunks of 300 words, and stub embeddings, so the timings cover the
storage path only (no embedding model). The pgvector container has 1 CPU and 1 GB. There were
3 rounds alternating `main`, PR 1213 and the PoC; each cell shows the range across the 3 rounds.

| | `main` | PR 1213 | PoC |
|---|---|---|---|
| One search at a time, p50 | 47–67 ms | 6.1–10.2 ms | 6.5–9.3 ms |
| 20 searches at once | 28–50 per s | 113–203 per s | 151–197 per s |
| Peak connections, 20 at once | 20 | 6 | 8 |
| Search during a 1,000-chunk upload, p95 | 76–111 ms | 41–66 ms | 27–57 ms |
| Search during a 1,000-chunk upload, max | 147–196 ms | 288–418 ms | 52–232 ms |
| Searches completed during the upload | 94–104 | 359–499 | 573–637 |

- Search by meaning is equally fast with the PR and with the PoC: 6–10 ms, against 47–67 ms on
  `main`.
- Both bound the number of connections. The PR's 6 is its cap per collection; the PoC's 8 is
  the pool size.
- During an upload, searches wait less with the PoC. With the PR, the upload's keyword-term
  computation (about 116 ms of CPU per 200-chunk batch) runs on the same loop as the searches.
- One `main` run failed during the first trial, and I did not capture its error. The 3 rounds
  in the table all completed.

## Trade-offs against PR 1213

- PoC uploads and deletes still open a pool per call, as on `main`; the PR shares a pool for
  them too. Uploads are rare, and they can move to the loop pool later in the same way.
- The PoC needs a long-lived engine loop and `aclose()` at the end of that loop. The metadata
  store already has this contract today.
- When a table is dropped, a PoC search reads it as empty, and the next search through the same
  store creates it again, as on `main`. With the PR, it stays missing until the next upload.

## Run it

```bash
docker run -d --name cuga-poc-pgvector --cpus 1 --memory 1g -e POSTGRES_USER=cuga -e POSTGRES_PASSWORD=cuga -e POSTGRES_DB=cuga_test -p 55432:5432 pgvector/pgvector:pg16  # pragma: allowlist secret
```

```bash
CUGA_KNOWLEDGE_PGVECTOR_CONNECTION_STRING=postgresql://cuga:cuga@localhost:55432/cuga_test uv run pytest -m "unit or pgvector" tests/unit/test_knowledge_search_loop_pool.py tests/integration/test_knowledge_pgvector_loop_pool.py  # pragma: allowlist secret
```

Benchmark: populate once, then run each arm with its source tree. The `main` and PR trees come
from `git archive origin/main src` and `git archive pr-1213 src`.

```bash
python poc/knowledge-loop-pool/bench.py src poc postgresql://cuga:cuga@localhost:55432/cuga_test kb_bench_1k 1000  # pragma: allowlist secret
```

```bash
python poc/knowledge-loop-pool/bench.py src poc postgresql://cuga:cuga@localhost:55432/cuga_test kb_bench_1k  # pragma: allowlist secret
```
