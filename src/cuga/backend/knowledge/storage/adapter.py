"""Knowledge vector store on top of LocalEmbeddingStore / ProdEmbeddingStore."""

from __future__ import annotations

import asyncio
import json
import re
import sqlite3
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings

from cuga.backend.knowledge.storage.schema import knowledge_embedding_schema
from cuga.backend.knowledge.vector_store_base import VectorStoreAdapter
from cuga.config import get_service_instance_id, get_tenant_id
from loguru import logger

# Compiled once per process — `re.sub` against an inline pattern works
# through Python's small (~512-entry) cache, but the lexical query path
# is hot enough that an explicit module-level binding removes the
# cache lookup per query. The character class keeps:
#   - \w  (Unicode-aware: handles Hebrew/CJK/accented Latin)
#   - whitespace
#   - identifier connectors users routinely put in queries:
#       .  version strings ("v1.2.3"), file paths ("src/foo.py")
#       -  SKUs, ticket IDs ("ABC-123", "Q1-2024")
#       @  email addresses ("user@example.com")
#       +  version build tags, phone numbers
# Everything else (FTS5-reserved syntax: quotes, parens, operators)
# becomes whitespace and gets split out at the tokenisation step.
_FTS_STRIP_RE: re.Pattern[str] = re.compile(r"[^\w\s.@\-+]+", re.UNICODE)

# Strict whitelist for collection names. The engine sanitises upstream
# (any non-[a-zA-Z0-9_] char becomes _) but the adapter is a public
# class and nothing in its constructor enforces that invariant. Catching
# a bad name at __init__ converts a corrupted DDL statement (data
# corruption / SQL-injection footgun) into a clear ValueError at
# construction time. The 63-char cap matches Postgres identifier limits
# and SQLite's practical sweet spot.
_COLLECTION_NAME_RE: re.Pattern[str] = re.compile(r"^[A-Za-z0-9_]{1,63}$")


class StorageBackedKnowledgeVectorStore(VectorStoreAdapter):
    """Knowledge chunks in sqlite-vec (local) or pgvector (prod) with tenant/instance scope."""

    def __init__(
        self,
        collection: str,
        embeddings: Embeddings,
        *,
        local_db_path: str = "",
        postgres_url: str = "",
        embedding_batch_size: int = 64,
        embedding_concurrency: int = 4,
        embedder_is_network: bool = False,
        vector_insert_batch_size: int = 200,
    ):
        if postgres_url:
            self._use_prod = True
            self._postgres_url = postgres_url
            self._local_db_path = ""
        elif local_db_path:
            self._use_prod = False
            self._local_db_path = local_db_path
            self._postgres_url = ""
        else:
            raise ValueError(
                "Knowledge storage vector store requires local_db_path (storage_local) or postgres_url (storage_prod)"
            )
        # F5 — defense in depth. Engine layer already sanitises
        # collection names (any non-[a-zA-Z0-9_] char becomes _), but
        # the adapter is a public class — any future caller that
        # constructs it without going through the engine could land an
        # arbitrary string in DDL/DML interpolations below. Catching it
        # here turns a corrupted CREATE TABLE statement into a clear
        # ValueError at construction time.
        if not _COLLECTION_NAME_RE.fullmatch(collection):
            raise ValueError(
                f"collection name {collection!r} contains characters not in "
                f"[A-Za-z0-9_]{{1,63}}. The adapter interpolates this into DDL "
                f"so arbitrary names risk SQL injection / DDL corruption."
            )
        self._collection = collection
        self._embeddings = embeddings
        self._embedding_batch_size = max(1, int(embedding_batch_size))
        self._embedding_concurrency = max(1, int(embedding_concurrency))
        self._embedder_is_network = bool(embedder_is_network)
        # ``vector_insert_batch_size`` caps the size of each ``add_many``
        # transaction. Protects prod (pgvector) from issues that surface only
        # at very large batches: a single multi-thousand-row transaction can
        # exceed ``command_timeout``, hold the HNSW write lock for long
        # enough to block readers, and balloon asyncpg's binary-protocol
        # buffer. Locally (sqlite-vec) the value is harmless.
        self._vector_insert_batch_size = max(1, int(vector_insert_batch_size))
        self._store: Any = None
        self._dim: int | None = None

    def _scope(self) -> dict[str, str]:
        return {"tenant_id": get_tenant_id(), "instance_id": get_service_instance_id()}

    def _ensure_store(self) -> None:
        if self._store is not None:
            return
        dim = len(self._embeddings.embed_query("probe"))
        schema = knowledge_embedding_schema(dim)
        if self._use_prod:
            from cuga.backend.storage.embedding.prod import ProdEmbeddingStore

            self._store = ProdEmbeddingStore(self._postgres_url, self._collection, schema)
        else:
            from cuga.backend.storage.embedding.local import LocalEmbeddingStore

            self._store = LocalEmbeddingStore(self._local_db_path, self._collection, schema)
        self._dim = dim
        logger.info(
            "Knowledge storage-backed vector store ready: collection={} dim={} prod={}",
            self._collection,
            dim,
            self._use_prod,
        )

    def _run_embedding_coro(self, coro):
        """Run async embedding store work on a short-lived loop; close prod asyncpg pool afterward.

        ``ProdEmbeddingStore`` pools are tied to the event loop. ``KnowledgeEngine`` invokes
        search/add from worker threads via ``asyncio.run`` / ``asyncio.to_thread``, so each run
        must not reuse a pool from a previous closed loop.
        """

        async def _wrapped():
            try:
                return await coro
            finally:
                if self._use_prod and self._store is not None:
                    from cuga.backend.storage.embedding.prod import ProdEmbeddingStore

                    if isinstance(self._store, ProdEmbeddingStore):
                        await self._store.close_pool()

        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(_wrapped())
        with ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(lambda: asyncio.run(_wrapped())).result()

    def _l2_similarity(self, distance: float) -> float:
        """Map sqlite-vec L2 distance to [0, 1] (higher = nearer)."""
        d = max(0.0, float(distance))
        return min(1.0, 1.0 / (1.0 + d))

    @staticmethod
    def _cosine_similarity(distance: float) -> float:
        d = float(distance)
        return max(0.0, min(1.0, 1.0 - d))

    # ------------------------------------------------------------------
    # BM25 / lexical retrieval (sqlite-vec via FTS5; pgvector deferred)
    # ------------------------------------------------------------------
    #
    # We maintain a parallel FTS5 virtual table per collection so a
    # keyword query like ``"Amit"`` or ``"401k"`` can be ranked by BM25
    # alongside the dense (semantic) search. RRF fusion happens in the
    # engine; the adapter only owns lifecycle + per-leg retrieval.
    #
    # Design choices:
    #  - Mirror columns into FTS so a search hit returns full Document
    #    metadata in one query (no JOIN, no chunk_id → main lookup).
    #  - Lazy create: the FTS table is only created the first time we
    #    insert. Existing pre-upgrade collections work — dense-only —
    #    until they're re-ingested OR the user runs a backfill.
    #  - Graceful degrade: every public method tolerates "FTS not yet
    #    materialised" and returns ``[]`` rather than raising. This is
    #    what lets ``engine.search_with_stats`` always call
    #    ``adapter.search_lexical`` unconditionally.

    def _fts_table(self) -> str:
        return f"{self._collection}__fts"

    def _fts_connect(self):
        """Open a SQLite connection configured for safe concurrent use.

        F4 — every FTS code path goes through this helper to guarantee:

          ``PRAGMA journal_mode = WAL``
            Default DELETE-mode journal takes an exclusive lock that
            blocks every concurrent reader during a write. With WAL,
            readers and writers no longer block each other — critical
            when an in-flight ``add_documents`` overlaps with
            ``search_lexical`` calls (very normal for cuga: user uploads
            a doc, then immediately queries it).
            WAL is a persistent setting at the DB level — re-applying
            it on every connect is cheap (a single PRAGMA) and prevents
            "I forgot to enable WAL once at setup" regressions.

          ``PRAGMA busy_timeout = 5000``
            5 second wait before SQLITE_BUSY is raised. Under WAL the
            only contention left is the brief checkpoint window; 5s is
            an order of magnitude more than that needs.

          ``PRAGMA synchronous = NORMAL``
            WAL's recommended pairing — safer than OFF, faster than
            FULL. We don't store irreplaceable data in FTS (it's
            derived from chunk_text in the main vec0 table), so a
            slight power-loss durability tradeoff is acceptable for the
            ~30% write throughput improvement.
        """
        conn = sqlite3.connect(self._local_db_path)
        try:
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA busy_timeout = 5000")
            conn.execute("PRAGMA synchronous = NORMAL")
        except Exception as exc:  # noqa: BLE001 — never block ingest/search on a PRAGMA failure
            logger.debug(f"PRAGMA application failed (continuing with defaults): {exc}")
        return conn

    @staticmethod
    def _normalize_fts_query(query: str) -> str | None:
        """Sanitise an FTS5 MATCH expression that preserves identifier-like
        tokens AND uses OR (not AND) so a multi-word query doesn't
        require every token to be present.

        Why OR not AND:
          - RRF already enforces co-occurrence at the *fusion* layer
            (chunks ranked by both legs win), so demanding AND inside the
            lexical leg double-penalises. The previous AND-joined version
            wiped out recall on identifier queries by requiring the user
            to type the document's exact tokenisation.
          - BM25 with OR returns chunks containing ANY query term,
            ranked by term-frequency × inverse-document-frequency. The
            top hits are still the chunks where MOST terms appear; the
            ones with one weak match contribute low scores that RRF
            naturally deprioritises.

        Returns ``None`` for zero-token queries so callers can short-
        circuit before opening a connection.
        """

        # Strip FTS5 reserved syntax but KEEP word chars + whitespace +
        # identifier connectors. Uses the module-level compiled pattern.
        safe = _FTS_STRIP_RE.sub(" ", query).strip()
        # Now split on whitespace. Tokens that contain ONLY identifier
        # connectors with no letters/digits ("---", ".", "-_-") are
        # noise; drop them.
        tokens: list[str] = []
        for raw in safe.split():
            stripped = raw.strip("._@-+")
            if stripped and any(ch.isalnum() for ch in raw):
                tokens.append(raw)
        if not tokens:
            return None
        # Quote each token so FTS5 reserved words ("AND", "OR", "NEAR",
        # "NOT") inside the query are matched as literal text instead of
        # parsed as operators. Then join with explicit OR so the lexical
        # leg returns chunks matching ANY term (RRF picks the best).
        return " OR ".join(f'"{t}"' for t in tokens)

    def _ensure_fts_table(self, conn) -> None:
        """Create the FTS5 virtual table if it doesn't already exist.
        Idempotent: ``CREATE VIRTUAL TABLE IF NOT EXISTS`` short-circuits
        on the second call, so checking in every ``add_documents`` is free.

        Tokeniser choice:
          ``unicode61 remove_diacritics 2 tokenchars '._@-+'``
            - ``unicode61`` Unicode-aware tokeniser. ``\\w`` matches Hebrew,
              CJK, accented Latin natively.
            - ``remove_diacritics 2`` folds accents so a query for "cafe"
              matches a doc with "café" (and vice versa).
            - ``tokenchars '._@-+'`` PRESERVES identifier connectors
              inside tokens — must match the set the query sanitiser
              keeps (``_FTS_STRIP_RE``) so "user@example.com" is one
              token on both sides.

          Porter stemmer (English suffix stripping) is deliberately NOT
          included: it's a no-op on non-Latin scripts and on identifier
          tokens like "v1.2.3" / "user_id" it only adds work. Language-
          specific stemming is a Phase-3 follow-up.
        """
        sql = (
            f"CREATE VIRTUAL TABLE IF NOT EXISTS {self._fts_table()} USING fts5("
            "chunk_id UNINDEXED, source UNINDEXED, filename UNINDEXED, "
            "page UNINDEXED, section_path UNINDEXED, chunk_text, "
            "tokenize=\"unicode61 remove_diacritics 2 tokenchars '._@-+'\")"
        )
        conn.execute(sql)

    def _fts_add(self, items: list[tuple[str, dict[str, Any], str]]) -> None:
        """Insert ``[(chunk_id, meta_payload, chunk_text), ...]`` rows.

        Called from ``add_documents`` after the dense insert succeeded.
        Failures here are logged + swallowed: a transient FTS write
        problem must not roll back a successful vector insert (the user
        would lose ingest progress for a recall optimisation).
        """
        if not items or self._use_prod:
            # pgvector hybrid not yet implemented — graceful no-op.
            return
        try:
            conn = self._fts_connect()
            try:
                self._ensure_fts_table(conn)
                conn.executemany(
                    f"INSERT INTO {self._fts_table()} "
                    "(chunk_id, source, filename, page, section_path, chunk_text) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    [
                        (
                            cid,
                            str(meta.get("source", "")),
                            str(meta.get("filename", "")),
                            int(meta.get("page", -1)) if meta.get("page", -1) is not None else -1,
                            str(meta.get("section_path", "") or ""),
                            text,
                        )
                        for cid, meta, text in items
                    ],
                )
                conn.commit()
            finally:
                conn.close()
        except Exception as exc:  # noqa: BLE001 — never fail the ingest
            logger.warning(
                "BM25 FTS write failed for collection=%s items=%d: %s "
                "(dense insert succeeded; lexical leg will return empty until backfill)",
                self._collection,
                len(items),
                exc,
            )

    def _fts_delete_by_source(self, source_id: str) -> None:
        """Delete FTS rows matching a source. Called from
        ``delete_by_source`` BEFORE the dense leg's delete loop so a
        crash between legs leaves orphan dense rows (invisible to RRF)
        rather than orphan FTS rows (visible ghost hits)."""
        if self._use_prod:
            return
        try:
            conn = self._fts_connect()
            try:
                self._ensure_fts_table(conn)
                conn.execute(
                    f"DELETE FROM {self._fts_table()} WHERE source = ?",
                    (source_id,),
                )
                conn.commit()
            finally:
                conn.close()
        except Exception as exc:  # noqa: BLE001 — best-effort
            logger.warning(
                "BM25 FTS delete_by_source failed for collection=%s source=%s: %s",
                self._collection,
                source_id,
                exc,
            )

    def _fts_drop(self) -> None:
        if self._use_prod:
            return
        try:
            conn = self._fts_connect()
            try:
                conn.execute(f"DROP TABLE IF EXISTS {self._fts_table()}")
                conn.commit()
            finally:
                conn.close()
        except Exception as exc:  # noqa: BLE001 — best-effort
            logger.debug(
                "BM25 FTS drop noop for collection=%s: %s",
                self._collection,
                exc,
            )

    def search_lexical(self, query: str, k: int = 10) -> list[tuple[Document, float]]:
        """BM25 lexical retrieval, parallel sibling of :meth:`search`.

        Returns the same shape ``[(Document, score), ...]`` as dense
        search so the engine can fuse them by chunk identity without
        per-leg branching. Scores are normalised to [0, 1] (1 = best
        match) — SQLite FTS5's raw ``bm25()`` returns *negative* numbers
        where larger magnitude = better, so we invert + clip.

        Returns ``[]`` when:
          - the collection's FTS table doesn't exist yet (pre-upgrade
            collection, or pgvector backend),
          - the query has zero usable tokens after sanitization,
          - the underlying FTS query raises (defensive — never let a
            lexical-leg failure kill the dense leg).
        """
        if self._use_prod:
            # Postgres FTS hybrid is a follow-up; for now degrade to
            # dense-only on prod backends.
            return []
        normalized = self._normalize_fts_query(query)
        if not normalized:
            return []
        try:
            conn = self._fts_connect()
            try:
                # If the FTS table was never created (pre-upgrade
                # collection), the SELECT raises an OperationalError.
                # Catch + log + return [] so the dense leg still answers.
                rows = conn.execute(
                    f"SELECT chunk_id, source, filename, page, section_path, "
                    f"chunk_text, bm25({self._fts_table()}) AS rank "
                    f"FROM {self._fts_table()} "
                    f"WHERE {self._fts_table()} MATCH ? "
                    f"ORDER BY rank LIMIT ?",
                    (normalized, max(1, int(k))),
                ).fetchall()
            finally:
                conn.close()
        except sqlite3.OperationalError as exc:
            logger.debug(
                "BM25 FTS not available for collection=%s (query degrades to dense-only): %s",
                self._collection,
                exc,
            )
            return []
        except Exception as exc:  # noqa: BLE001 — defensive
            logger.warning(
                "BM25 FTS search failed for collection=%s query=%r: %s",
                self._collection,
                query[:40],
                exc,
            )
            return []

        out: list[tuple[Document, float]] = []
        for row in rows:
            chunk_id, source, filename, page, section_path, chunk_text, raw_rank = row
            # Map raw BM25 (negative; more-negative = better match) to
            # [0, 1] with "higher = better" semantics so RRF can treat
            # the lexical leg symmetrically with the dense leg.
            #
            # Earlier this used ``1/(1+|raw|)``, which INVERTED the
            # semantics — a more-negative raw_rank (better match) got a
            # SMALLER score. ``|raw| / (1 + |raw|)`` is monotonically
            # increasing in ``|raw|``: raw=-10 → 0.91, raw=-1 → 0.50,
            # raw≈0 → 0. Closes CodeRabbit M3.
            _abs_rank = abs(float(raw_rank))
            score = _abs_rank / (1.0 + _abs_rank)
            metadata: dict[str, Any] = {
                "source": source or "",
                "filename": filename or "",
                "page": page if page not in (-1, None) else None,
            }
            if section_path:
                metadata["section_path"] = section_path
            out.append((Document(page_content=str(chunk_text or ""), metadata=metadata), score))
        return out

    def add_documents(
        self,
        documents: list[Document],
        stage_timings: dict[str, Any] | None = None,
        progress_cb: Any = None,
    ) -> dict[str, int]:
        if not documents:
            return {"num_added": 0, "num_skipped": 0}
        self._ensure_store()
        texts = [d.page_content for d in documents]
        n = len(texts)
        bs = self._embedding_batch_size
        # C3/C4 (issue #183 step 4): sub-batch the embed call. Network
        # providers (openai/ollama) benefit from concurrent gather; local
        # providers (fastembed/huggingface) stay sequential because their
        # ONNX/torch runtimes already use internal threads and adding asyncio
        # gather on top just adds overhead without true parallelism.
        t_embed = time.monotonic()
        starts = list(range(0, n, bs))
        vectors: list[list[float] | None] = [None] * n

        # C5 (issue #183 step 6): emit a progress event after each sub-batch
        # completes. The callback is sync; if the caller wants to hop to an
        # async event loop, that bridge lives in the engine (run_coroutine_
        # threadsafe). Failures here are swallowed because progress is
        # best-effort and must never fail an ingest.
        embed_done = [0]

        def _safe_progress(stage: str, done: int, total: int) -> None:
            if progress_cb is None:
                return
            try:
                progress_cb(stage, done, total)
            except Exception:
                # Best-effort instrumentation; never let it surface to ingest.
                pass

        def _record_sub(start: int, sub: list[list[float]]) -> None:
            end = min(start + bs, n)
            if len(sub) != end - start:
                raise RuntimeError(
                    f"Embedder returned {len(sub)} vectors for {end - start} "
                    f"texts at offset {start}; bailing before any insert."
                )
            for i, emb in enumerate(sub):
                vectors[start + i] = emb
            embed_done[0] += end - start
            _safe_progress("embed", embed_done[0], n)

        if self._embedder_is_network and self._embedding_concurrency > 1 and len(starts) > 1:
            # Network providers: prefer ``aembed_documents``. LangChain's
            # default falls back to run_in_executor over embed_documents, so
            # this is equivalent for embedders without a native async impl,
            # and strictly better for ones that have one (langchain-openai
            # uses httpx natively, etc.). Either way, gather still gives us
            # the concurrent dispatch we want.
            sem = asyncio.Semaphore(self._embedding_concurrency)

            async def _embed_one(start: int) -> None:
                end = min(start + bs, n)
                async with sem:
                    sub = await self._embeddings.aembed_documents(texts[start:end])
                _record_sub(start, sub)

            async def _embed_all() -> None:
                await asyncio.gather(*(_embed_one(s) for s in starts))

            self._run_embedding_coro(_embed_all())
        else:
            # Local providers: embedder runtime already exploits threads.
            for start in starts:
                end = min(start + bs, n)
                _record_sub(start, self._embeddings.embed_documents(texts[start:end]))

        if any(v is None for v in vectors):
            raise RuntimeError(
                "Internal: at least one chunk had no embedding after sub-batched embed; "
                "this indicates a logic bug — failing before insert."
            )
        if stage_timings is not None:
            stage_timings["embed_s"] = round(time.monotonic() - t_embed, 3)
        scope = self._scope()

        # C2 (issue #183 step 4): build (id, embedding, metadata) tuples once
        # and bulk-insert via ``add_many``. Replaces a per-row ``await
        # self._store.add(...)`` loop that paid one commit/round-trip per row.
        items: list[tuple[str, list[float], dict[str, Any]]] = []
        for doc, emb in zip(documents, vectors, strict=True):
            chunk_id = str(uuid.uuid4())
            page_raw = doc.metadata.get("page")
            page_val: int
            if page_raw is None:
                page_val = -1
            else:
                try:
                    page_val = int(page_raw)
                except (TypeError, ValueError):
                    page_val = -1
            # Strip and require non-empty: the rollback path
            # (``_fts_delete_by_source``) deletes by exact source string.
            # If a caller smuggled in ``source=""``, a partial-insert
            # rollback would over-delete every prior chunk in the same
            # scope that also has an empty source — a silent data-loss
            # path. Reject loudly instead. Closes CodeRabbit M4.
            source = str(doc.metadata.get("source", "") or "").strip()
            if not source:
                raise ValueError(
                    "Document metadata.source must be a non-empty string for "
                    "add_documents — rollback and delete_by_source rely on it "
                    "as the primary key for source-scoped cleanup."
                )
            filename = str(doc.metadata.get("filename", "") or "")
            # Preserve section_path (Docling headings collapsed into a flat
            # breadcrumb) — the upstream metadata-normalization step in
            # ``engine._insert_documents_async`` carefully populates this
            # to give the LLM context for caption-only chunks like
            # ``'Table 27: ...'``. Without persisting it here the field
            # silently dies at the storage boundary and the search-side
            # restore (engine.search) gets an empty string.
            section_path = str(doc.metadata.get("section_path", "") or "")
            meta_payload: dict[str, Any] = {
                "source": source,
                "filename": filename,
                "page": page_val,
            }
            if section_path:
                meta_payload["section_path"] = section_path
            meta = {
                "id": chunk_id,
                "tenant_id": scope["tenant_id"],
                "instance_id": scope["instance_id"],
                "source": source,
                "filename": filename,
                "page": page_val,
                "chunk_text": doc.page_content,
                "meta_json": json.dumps(meta_payload, ensure_ascii=False),
            }
            items.append((chunk_id, emb, meta))

        # Sub-batch the bulk insert by ``vector_insert_batch_size`` so each
        # ``add_many`` call is a short transaction. Critical on prod (pgvector)
        # for documents large enough that one giant transaction would: blow
        # past ``command_timeout``, hold the HNSW write lock for many seconds,
        # or overflow asyncpg's binary-protocol buffer. Locally (sqlite-vec)
        # this just splits one ``executemany`` into a few; same total cost.
        insert_bs = self._vector_insert_batch_size
        total_items = len(items)

        # Compensating-delete tracker: each ``add_many`` is its own transaction
        # on pgvector, so a failure in batch N+1 leaves batches 1..N as orphan
        # vectors. Without cleanup the retry path appends duplicates because
        # ``delete_by_source`` only runs when the document is already in the
        # metadata store, and a first-time failure may never have written
        # metadata. Track the source on first successful batch and roll back
        # on any subsequent batch failure. Per Sami's review (Dec 2026).
        # ``source`` is identical for every chunk of one document — items
        # carry it in ``meta["source"]``. Codify the invariant so a future
        # caller that batches chunks from multiple documents can't slip
        # through and produce a half-cleaned rollback. (CodeRabbit
        # 2026-06-09 #3381207466 — single-source precondition.)
        if items:
            _sources = {str(m.get("source", "")) for *_rest, m in items}
            if len(_sources) > 1:
                raise ValueError(
                    "add_documents expects a single ``source`` per call; "
                    f"got {len(_sources)} distinct sources. The orphan-vector "
                    "rollback path only tracks one source — batching multiple "
                    "documents here would leave a partial-failure cleanup "
                    "incomplete."
                )
        source_for_rollback = items[0][2].get("source") if items else None

        async def _add_all() -> int:
            n = 0
            partial = False
            for i in range(0, total_items, insert_bs):
                chunk = items[i : i + insert_bs]
                try:
                    await self._store.add_many(chunk)
                except Exception:
                    # If we'd already committed earlier batches, clean them
                    # up before re-raising so the next ingest attempt
                    # doesn't encounter duplicate vectors on the same
                    # source_id. Inline the delete loop here (mirror of
                    # ``delete_by_source``) so we stay on the current event
                    # loop — the adapter-level helper would call
                    # ``_run_embedding_coro`` which would deadlock against
                    # the loop we're already inside.
                    if partial and source_for_rollback:
                        try:
                            _scope = self._scope()
                            _filt = {**_scope, "source": source_for_rollback}
                            while True:
                                _rows = await self._store.list(_filt, 5000)
                                if not _rows:
                                    break
                                for _row in _rows:
                                    _cid = _row.get("id")
                                    if _cid:
                                        await self._store.delete(
                                            _cid,
                                            tenant_id=_scope["tenant_id"],
                                            instance_id=_scope["instance_id"],
                                        )
                            # FTS rollback is best-effort and synchronous;
                            # FTS write failure here doesn't undo what we
                            # already cleaned up on the dense side. Log the
                            # divergence so incident triage can see when
                            # FTS + dense state drifted (was silently
                            # swallowed — CodeRabbit-flagged 2026-06-09).
                            try:
                                self._fts_delete_by_source(source_for_rollback)
                            except Exception as _fts_err:  # noqa: BLE001
                                logger.warning(
                                    "FTS compensating delete failed for %r "
                                    "after partial-insert rollback: %s — "
                                    "dense + FTS state may have diverged; "
                                    "the next re-ingest of the same source "
                                    "will resync via delete_by_source.",
                                    source_for_rollback,
                                    _fts_err,
                                )
                            logger.warning(
                                "Rolled back %d orphan vectors for %r after "
                                "partial-insert failure at batch offset %d",
                                n,
                                source_for_rollback,
                                i,
                            )
                        except Exception as rb_err:  # noqa: BLE001
                            logger.error(
                                "Compensating rollback for %r failed after "
                                "partial-insert at batch offset %d: %s — "
                                "orphan vectors may persist. The next "
                                "re-ingest of the same source will replace "
                                "them via delete_by_source.",
                                source_for_rollback,
                                i,
                                rb_err,
                            )
                    raise
                n += len(chunk)
                partial = True
                # Progress emit fires inside the worker thread's loop. Safe
                # because ``_safe_progress`` swallows callback errors.
                _safe_progress("insert", n, total_items)
            return n

        _safe_progress("insert_start", 0, total_items)

        t_insert = time.monotonic()
        count = self._run_embedding_coro(_add_all())
        if stage_timings is not None:
            stage_timings["insert_s"] = round(time.monotonic() - t_insert, 3)

        # BM25 sidecar: mirror the same chunks into the FTS5 table so a
        # subsequent ``search_lexical`` can rank by keyword presence.
        # Done AFTER the dense insert succeeded — a failure on this leg
        # is logged + swallowed so the user keeps their ingest progress
        # (recall-side optimisation must never block correctness).
        _fts_items = [
            (
                cid,
                {
                    "source": meta.get("source", ""),
                    "filename": meta.get("filename", ""),
                    "page": meta.get("page", -1),
                    "section_path": json.loads(meta.get("meta_json", "{}") or "{}").get("section_path", ""),
                },
                meta.get("chunk_text", "") or "",
            )
            for cid, _emb, meta in items
        ]
        if stage_timings is not None:
            _t_fts = time.monotonic()
            self._fts_add(_fts_items)
            stage_timings["fts_s"] = round(time.monotonic() - _t_fts, 3)
        else:
            self._fts_add(_fts_items)
        return {"num_added": count, "num_skipped": 0}

    def search(self, query: str, k: int = 10) -> list[tuple[Document, float]]:
        self._ensure_store()
        q_emb = self._embeddings.embed_query(query)
        filt = dict(self._scope())

        async def _go():
            return await self._store.search(q_emb, k, filt)

        rows = self._run_embedding_coro(_go())
        out: list[tuple[Document, float]] = []
        sim_fn = self._cosine_similarity if self._use_prod else self._l2_similarity
        for row in rows:
            if len(row) < 4:
                continue
            _, chunk_text, meta_json, distance = row[0], row[1], row[2], row[3]
            try:
                extra = json.loads(meta_json) if meta_json else {}
            except (json.JSONDecodeError, TypeError):
                extra = {}
            page = extra.get("page", -1)
            if page == -1:
                page = None
            metadata: dict[str, Any] = {
                "source": extra.get("source", ""),
                "filename": extra.get("filename", ""),
                "page": page,
            }
            # Restore section_path when it was preserved at ingest. Empty
            # values are omitted to keep result envelopes lean for non-
            # Docling formats (txt, plain markdown).
            _sp = extra.get("section_path", "")
            if _sp:
                metadata["section_path"] = _sp
            doc = Document(page_content=str(chunk_text or ""), metadata=metadata)
            out.append((doc, sim_fn(distance)))
        return out

    def delete_by_source(self, source_id: str) -> None:
        self._ensure_store()
        scope = self._scope()
        filt: dict[str, Any] = {**scope, "source": source_id}

        # F3 — FTS-first deletion order. A crash between the two legs
        # produces orphan rows on whichever side ran LAST. Orphan FTS
        # rows are user-visible (ghost hits in search_lexical referring
        # to chunk_ids the dense leg no longer materialises, which RRF
        # then ranks as "lexical-only winners"). Orphan dense rows are
        # invisible — RRF gives them lexical_rank=None which contributes
        # 0, and the next ingest of the same source replaces them. So
        # we delete FTS first; the rebuild cost is strictly lower.
        self._fts_delete_by_source(source_id)

        async def _delete_loop():
            while True:
                rows = await self._store.list(filt, 5000)
                if not rows:
                    break
                for row in rows:
                    cid = row.get("id")
                    if not cid:
                        continue
                    await self._store.delete(
                        cid,
                        tenant_id=scope["tenant_id"],
                        instance_id=scope["instance_id"],
                    )

        self._run_embedding_coro(_delete_loop())

    def drop(self) -> None:
        # F2 — drop FTS *after* the main DROP succeeds. The previous
        # FTS-first order left an inconsistent state on partial failure:
        # if the main DROP raised (locked file, missing permissions)
        # the FTS table was already gone, violating the invariant
        # "FTS exists iff dense exists". Re-ordering ensures that on
        # any failure we still have both legs intact.
        if self._use_prod:
            if not self._postgres_url:
                return

            async def _drop_pg():
                from cuga.backend.storage.embedding.prod import ProdEmbeddingStore

                if self._store is not None and isinstance(self._store, ProdEmbeddingStore):
                    await self._store.close_pool()
                import asyncpg

                pool = await asyncpg.create_pool(
                    self._postgres_url,
                    min_size=1,
                    max_size=1,
                    command_timeout=60,
                )
                try:
                    async with pool.acquire() as conn:
                        await conn.execute(f"DROP TABLE IF EXISTS {self._collection}")
                finally:
                    await pool.close()

            self._run_embedding_coro(_drop_pg())
        else:
            if not self._local_db_path:
                return
            import sqlite_vec

            conn = sqlite3.connect(self._local_db_path)
            conn.enable_load_extension(True)
            try:
                sqlite_vec.load(conn)
            finally:
                conn.enable_load_extension(False)
            try:
                conn.execute(f"DROP TABLE IF EXISTS {self._collection}")
                conn.commit()
            finally:
                conn.close()
        # F2 — only drop FTS now, after the main DROP has succeeded.
        # If we reach here both legs are coming down; if the main DROP
        # raised, we've already propagated the exception and skipped this.
        self._fts_drop()
        self._store = None
        self._dim = None
