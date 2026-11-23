"""Knowledge dense-search benchmark for one code tree: main, PR 1213, or the PoC.

Usage: python bench.py <src_dir> <arm> <dsn> <collection> [populate_chunks]
  arm: main | pr | poc  (main and pr call adapter.search from worker threads, as the engine's
       asyncio.to_thread does; poc awaits adapter.asearch on one event loop, as the engine does now)
Embeddings are a fast deterministic stub, so timings are the storage path only (no model).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import random
import statistics
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

SRC, ARM, DSN, COLLECTION = sys.argv[1:5]
POPULATE = int(sys.argv[5]) if len(sys.argv) > 5 else 0
sys.path.insert(0, SRC)
os.environ["DYNACONF_SERVICE__TENANT_ID"] = "bench"
os.environ["DYNACONF_SERVICE__INSTANCE_ID"] = "bench"

from loguru import logger  # noqa: E402

logger.remove()
logger.add(sys.stderr, level="ERROR")

import asyncpg  # noqa: E402
from langchain_core.documents import Document  # noqa: E402

from cuga.backend.knowledge.storage.adapter import StorageBackedKnowledgeVectorStore  # noqa: E402

DIM = 384
WORDS = "retrieval augmented generation pipeline index document chunk vector search query answer source page table figure model".split()


def _vec(text: str) -> list[float]:
    rnd = random.Random(int(hashlib.sha256(text.encode()).hexdigest()[:16], 16))
    v = [rnd.uniform(-1, 1) for _ in range(DIM)]
    n = sum(x * x for x in v) ** 0.5
    return [x / n for x in v]


class Embeddings:
    def embed_query(self, text: str) -> list[float]:
        return _vec(text)

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [_vec(t) for t in texts]


def docs(n: int, source: str, seed: int) -> list[Document]:
    rnd = random.Random(seed)
    return [
        Document(
            page_content=" ".join(rnd.choice(WORDS) + str(rnd.randint(0, 999)) for _ in range(300)),
            metadata={"source": source, "filename": source.rsplit("/", 1)[-1], "page": i},
        )
        for i in range(n)
    ]


def adapter(collection: str) -> StorageBackedKnowledgeVectorStore:
    return StorageBackedKnowledgeVectorStore(collection, Embeddings(), postgres_url=DSN)


def ms(values: list[float]) -> dict:
    s = sorted(values)
    return {
        "p50": round(statistics.median(s) * 1000, 1),
        "p95": round(s[int(len(s) * 0.95) - 1] * 1000, 1),
        "max": round(s[-1] * 1000, 1),
    }


async def peak_connections(stop: threading.Event, out: list[int]) -> None:
    conn = await asyncpg.connect(DSN)
    try:
        while not stop.is_set():
            out.append(
                await conn.fetchval(
                    "SELECT count(*) - 1 FROM pg_stat_activity WHERE datname = current_database() "
                    "AND backend_type = 'client backend'"
                )
            )
            await asyncio.sleep(0.005)
    finally:
        await conn.close()


def watch_connections():
    stop, out = threading.Event(), []
    t = threading.Thread(target=lambda: asyncio.run(peak_connections(stop, out)), daemon=True)
    t.start()

    def finish() -> int:
        stop.set()
        t.join()
        return max(out) if out else 0

    return finish


def main() -> None:
    kb = adapter(COLLECTION)
    if POPULATE:
        for i in range(0, POPULATE, 500):
            kb.add_documents(docs(min(500, POPULATE - i), f"/bench/file{i}.pdf", seed=i))
        print(json.dumps({"populated": POPULATE}))
        return

    queries = [f"question {i}" for i in range(60)]
    upload_kb = adapter(f"{COLLECTION}_up_{ARM}")
    result: dict = {"arm": ARM}

    if ARM in ("main", "pr"):
        worker = ThreadPoolExecutor(max_workers=24)
        run = lambda q: worker.submit(kb.search, q, 5).result()  # noqa: E731
        run("warmup")
        lat = []
        for q in queries:
            t0 = time.perf_counter()
            assert len(run(q)) == 5
            lat.append(time.perf_counter() - t0)
        result["sequential_ms"] = ms(lat)

        finish = watch_connections()
        rates = []
        for r in range(5):
            t0 = time.perf_counter()
            list(worker.map(lambda q: kb.search(q, 5), [f"c{r}-{i}" for i in range(20)]))
            rates.append(20 / (time.perf_counter() - t0))
        result["concurrent_20_per_s"] = round(statistics.median(rates), 1)
        result["peak_connections"] = finish()

        up = threading.Thread(target=upload_kb.add_documents, args=(docs(1000, "/bench/up.pdf", seed=7),))
        up.start()
        lat = []
        while up.is_alive():
            t0 = time.perf_counter()
            run(f"during {len(lat)}")
            lat.append(time.perf_counter() - t0)
        up.join()
        result["during_upload_ms"] = ms(lat)
        result["during_upload_n"] = len(lat)
    else:
        from cuga.backend.storage.embedding.prod import close_loop_pools

        async def go() -> None:
            await kb.asearch("warmup", 5)
            lat = []
            for q in queries:
                t0 = time.perf_counter()
                assert len(await kb.asearch(q, 5)) == 5
                lat.append(time.perf_counter() - t0)
            result["sequential_ms"] = ms(lat)

            finish = watch_connections()
            rates = []
            for r in range(5):
                t0 = time.perf_counter()
                await asyncio.gather(*(kb.asearch(f"c{r}-{i}", 5) for i in range(20)))
                rates.append(20 / (time.perf_counter() - t0))
            result["concurrent_20_per_s"] = round(statistics.median(rates), 1)
            result["peak_connections"] = finish()

            up = threading.Thread(target=upload_kb.add_documents, args=(docs(1000, "/bench/up.pdf", seed=7),))
            up.start()
            lat = []
            while up.is_alive():
                t0 = time.perf_counter()
                await kb.asearch(f"during {len(lat)}", 5)
                lat.append(time.perf_counter() - t0)
            up.join()
            result["during_upload_ms"] = ms(lat)
            result["during_upload_n"] = len(lat)
            await close_loop_pools()

        asyncio.run(go())

    upload_kb.drop()
    print(json.dumps(result))


if __name__ == "__main__":
    main()
