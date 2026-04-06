"""Knowledge vector store on top of LocalEmbeddingStore / ProdEmbeddingStore."""

from __future__ import annotations

import asyncio
import json
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings

from cuga.backend.knowledge.storage.schema import knowledge_embedding_schema
from cuga.backend.knowledge.vector_store_base import VectorStoreAdapter
from cuga.config import get_service_instance_id, get_tenant_id
from loguru import logger


class StorageBackedKnowledgeVectorStore(VectorStoreAdapter):
    """Knowledge chunks in sqlite-vec (local) or pgvector (prod) with tenant/instance scope."""

    def __init__(
        self,
        collection: str,
        embeddings: Embeddings,
        *,
        local_db_path: str = "",
        postgres_url: str = "",
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
        self._collection = collection
        self._embeddings = embeddings
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

    def add_documents(self, documents: list[Document]) -> dict[str, int]:
        if not documents:
            return {"num_added": 0, "num_skipped": 0}
        self._ensure_store()
        texts = [d.page_content for d in documents]
        vectors = self._embeddings.embed_documents(texts)
        scope = self._scope()

        async def _add_all() -> int:
            n = 0
            for doc, emb in zip(documents, vectors, strict=True):
                chunk_id = str(uuid.uuid4())
                page_val: int
                page_raw = doc.metadata.get("page")
                if page_raw is None:
                    page_val = -1
                else:
                    try:
                        page_val = int(page_raw)
                    except (TypeError, ValueError):
                        page_val = -1
                source = str(doc.metadata.get("source", "") or "")
                filename = str(doc.metadata.get("filename", "") or "")
                meta = {
                    "id": chunk_id,
                    "tenant_id": scope["tenant_id"],
                    "instance_id": scope["instance_id"],
                    "source": source,
                    "filename": filename,
                    "page": page_val,
                    "chunk_text": doc.page_content,
                    "meta_json": json.dumps(
                        {"source": source, "filename": filename, "page": page_val},
                        ensure_ascii=False,
                    ),
                }
                await self._store.add(chunk_id, emb, meta)
                n += 1
            return n

        count = self._run_embedding_coro(_add_all())
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
            doc = Document(
                page_content=str(chunk_text or ""),
                metadata={
                    "source": extra.get("source", ""),
                    "filename": extra.get("filename", ""),
                    "page": page,
                },
            )
            out.append((doc, sim_fn(distance)))
        return out

    def delete_by_source(self, source_id: str) -> None:
        self._ensure_store()
        scope = self._scope()
        filt: dict[str, Any] = {**scope, "source": source_id}

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
            import sqlite3

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
        self._store = None
        self._dim = None
