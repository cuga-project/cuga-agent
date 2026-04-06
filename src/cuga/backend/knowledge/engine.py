"""In-process knowledge engine using LangChain vector stores + Docling.

Replaces OpenRAG with zero external services. All document parsing, embedding,
vector storage, and search happen in-process.
"""

from __future__ import annotations

import asyncio
import collections
import fcntl
import ipaddress
import logging
from loguru import logger as loguru_logger
import re
import shutil
import socket
import threading
import time
import uuid
from dataclasses import dataclass, fields as dc_fields
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings
from langchain_core.indexing import InMemoryRecordManager, index
from langchain_docling import DoclingLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter

from cuga.backend.knowledge.config import KnowledgeConfig
from cuga.backend.knowledge.metadata import MetadataDB

logger = loguru_logger

BLOCKED_HOSTNAMES = {"localhost", "metadata.google.internal", "169.254.169.254"}
ALLOWED_PORTS = {80, 443, 8080, 8443}
_VS_CACHE_MAX = 64  # max cached vector store connections


def _iter_exception_messages(exc: BaseException) -> list[str]:
    """Collect exception messages across cause/context chains."""
    messages: list[str] = []
    seen: set[int] = set()
    stack: list[BaseException] = [exc]

    while stack:
        current = stack.pop()
        current_id = id(current)
        if current_id in seen:
            continue
        seen.add(current_id)

        message = str(current).strip()
        if message:
            messages.append(message)

        if current.__cause__ is not None:
            stack.append(current.__cause__)
        if current.__context__ is not None:
            stack.append(current.__context__)

    return messages


def _translate_document_load_error(file_path: Path, exc: BaseException) -> Exception:
    """Map low-level parser errors to actionable ingestion failures."""
    if file_path.suffix.lower() == ".pdf":
        lowered = " | ".join(_iter_exception_messages(exc)).lower()
        if any(token in lowered for token in ("incorrect password", "password error", "encrypted")):
            return ValueError(
                f"PDF is password-protected and cannot be indexed without a password: {file_path.name}"
            )

    if isinstance(exc, Exception):
        return exc
    return RuntimeError(str(exc))




# --- Data classes ---

@dataclass
class SearchResult:
    text: str
    filename: str
    page: int | None
    score: float


@dataclass
class DocInfo:
    filename: str
    chunk_count: int
    status: str
    ingested_at: str
    preview: str = ""


# --- Errors ---

class ReindexBusyError(Exception):
    """Raised when reindex cannot start because uploads are pending."""

    def __init__(self, pending_count: int):
        self.pending_count = pending_count
        super().__init__(f"Cannot reindex: {pending_count} upload(s) in progress")


class ReindexInProgressError(Exception):
    """Raised when upload is attempted during reindex."""
    pass


# --- Prepared update result ---

@dataclass
class PreparedKnowledgeUpdate:
    """Result of prepare_knowledge_update. Passed to commit without re-validation."""

    validated: KnowledgeConfig
    embedding_changed: bool
    chunking_changed: bool
    metric_changed: bool
    reindex_recommended: bool
    new_embeddings: Embeddings | None
    new_embedding_dim: int | None


# --- Embedding factory ---

def create_embeddings(provider: str, model: str, use_gpu: bool = True) -> Embeddings:
    """Create embeddings instance based on provider and model.

    For local embeddings, uses fastembed (lightweight, no torch/sentence-transformers needed).
    """
    if provider in ("huggingface", "fastembed", "local"):
        from langchain_community.embeddings import FastEmbedEmbeddings
        model = model or "sentence-transformers/all-MiniLM-L6-v2"
        return FastEmbedEmbeddings(model_name=model)
    elif provider == "openai":
        from langchain_openai import OpenAIEmbeddings
        model = model or "text-embedding-3-small"
        return OpenAIEmbeddings(model=model)
    elif provider == "ollama":
        from langchain_community.embeddings import OllamaEmbeddings
        model = model or "nomic-embed-text"
        return OllamaEmbeddings(model=model)
    else:
        raise ValueError(f"Unknown embedding provider: {provider}")




def _get_embedding_dim(embeddings: Embeddings) -> int:
    """Get embedding dimension by embedding a test string."""
    test_vec = embeddings.embed_query("test")
    return len(test_vec)


# --- Engine ---

class KnowledgeEngine:
    """In-process knowledge engine. No external services needed.

    All Milvus operations are serialized through a dedicated thread via _milvus_lock
    to ensure thread safety with Milvus Lite.
    """

    def __init__(self, config: KnowledgeConfig):
        config.validate()
        self._config = config
        # Legacy — kept for backward compatibility with tests
        self._milvus_uri = str(config.persist_dir / "knowledge.db")
        self._files_dir = config.persist_dir / "files"
        self._metadata = MetadataDB(config.persist_dir / "metadata.db")

        # Ensure directories exist
        config.persist_dir.mkdir(parents=True, exist_ok=True)
        self._files_dir.mkdir(parents=True, exist_ok=True)

        # Single-writer lock (flock — race-free)
        self._lock_file = open(config.persist_dir / ".lock", "w")
        try:
            fcntl.flock(self._lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self._lock_file.close()
            raise RuntimeError(
                "Knowledge engine already running in another process. "
                "Start with --workers 1"
            )

        # Default embeddings (lazy — initialized on first use to speed up startup)
        self._default_embeddings = None
        self._default_embedding_dim = None

        # Vector store LRU cache (bounded)
        self._vector_stores: collections.OrderedDict[str, Milvus] = collections.OrderedDict()

        # Record managers for dedup (InMemoryRecordManager per collection)
        self._record_managers: dict[str, InMemoryRecordManager] = {}

        # Milvus serialization lock (all Milvus ops go through this)
        self._milvus_lock = threading.Lock()

        # Per-collection async ingest locks
        self._collection_locks: dict[str, asyncio.Lock] = {}

        # Active tasks for cancellation
        self._active_tasks: dict[str, asyncio.Event] = {}

        # Reindex coordination flags (in-memory, single-process only — flock ensures this)
        self._reindex_in_progress: set[str] = set()
        self._reindex_deferred: set[str] = set()

        # Background tasks
        self._shutdown_event = asyncio.Event()
        self._background_tasks: list[asyncio.Task] = []

        # Crash recovery
        recovered = self._metadata.recover_stale_tasks()
        if recovered:
            logger.info(f"Recovered {recovered} stale task(s) from previous crash")

        # Reconcile stale deletes
        self._reconcile_deletes()

        # Purge old tasks
        purged = self._metadata.purge_old_tasks(max_age_days=7)
        if purged:
            logger.debug(f"Purged {purged} old task(s)")

        logger.info(
            f"Knowledge engine started: "
            f"vector_store={config.vector_store}, "
            f"embedding={config.embedding_provider}/{config.embedding_model or 'auto'}, "
            f"use_gpu={config.use_gpu}, "
            f"metric={config.metric_type}, "
            f"persist_dir={config.persist_dir}"
        )

    def start_background_tasks(self, loop: asyncio.AbstractEventLoop | None = None) -> None:
        """Start background maintenance tasks. Call after event loop is running."""
        async def _maintenance_loop():
            while not self._shutdown_event.is_set():
                try:
                    await asyncio.sleep(3600)  # every hour
                    if self._shutdown_event.is_set():
                        break
                    self._reconcile_deletes()
                    self._metadata.purge_old_tasks(max_age_days=7)
                    self._cleanup_expired_sessions()
                    logger.debug("Background maintenance completed")
                except asyncio.CancelledError:
                    break
                except Exception as e:
                    logger.error(f"Background maintenance error: {e}")

        task = asyncio.ensure_future(_maintenance_loop())
        self._background_tasks.append(task)

    def shutdown(self) -> None:
        """Release resources."""
        self._shutdown_event.set()
        for task in self._background_tasks:
            task.cancel()
        try:
            fcntl.flock(self._lock_file, fcntl.LOCK_UN)
            self._lock_file.close()
        except Exception:
            pass
        logger.info("Knowledge engine stopped")

    # --- Embeddings (lazy init) ---

    def _ensure_embeddings(self) -> None:
        """Initialize embeddings on first use (not at engine startup)."""
        if self._default_embeddings is None:
            provider = self._config.embedding_provider
            model = self._config.embedding_model
            self._default_embeddings = create_embeddings(provider, model, use_gpu=self._config.use_gpu)
            self._default_embedding_dim = _get_embedding_dim(self._default_embeddings)
            logger.info(
                f"Embeddings initialized: provider={provider}, "
                f"model={model}, dim={self._default_embedding_dim}"
            )

    async def warmup(self) -> dict[str, Any]:
        """Preload heavyweight resources so callers can gate on readiness."""
        await asyncio.to_thread(self._ensure_embeddings)
        return {
            "embedding_provider": self._config.embedding_provider,
            "embedding_model": self._config.embedding_model or "auto",
            "embeddings_initialized": self._default_embeddings is not None,
        }

    # --- Vector store (LRU cache, bounded) ---

    def _get_vector_store(self, collection: str):
        """Get or create a vector store adapter for the collection."""
        if collection in self._vector_stores:
            self._vector_stores.move_to_end(collection)
            return self._vector_stores[collection]

        # Evict oldest if at capacity
        while len(self._vector_stores) >= _VS_CACHE_MAX:
            evicted_name, _ = self._vector_stores.popitem(last=False)
            logger.debug(f"Evicted vector store cache: {evicted_name}")

        from cuga.backend.knowledge.vector_store import create_vector_store

        embeddings = self._get_embeddings_for_collection(collection)
        adapter = create_vector_store(
            backend=self._config.vector_store,
            collection=collection,
            embeddings=embeddings,
            persist_dir=self._config.persist_dir,
            metric_type=self._config.metric_type,
            pgvector_connection_string=self._config.pgvector_connection_string,
        )
        self._vector_stores[collection] = adapter
        return adapter

    def _get_record_manager(self, collection: str) -> InMemoryRecordManager:
        if collection not in self._record_managers:
            rm = InMemoryRecordManager(namespace=collection)
            rm.create_schema()
            self._record_managers[collection] = rm
        return self._record_managers[collection]

    def _get_embeddings_for_collection(self, collection: str) -> Embeddings:
        """Get embeddings for a collection, always using the pinned provider/model."""
        self._ensure_embeddings()
        cfg = self._metadata.get_collection_config(collection)
        if cfg:
            # Always use the pinned config — even if dims happen to match the default,
            # provider/model may differ (e.g. OpenAI vs HuggingFace both at 384 dims)
            pinned_provider = cfg["embedding_provider"]
            pinned_model = cfg["embedding_model"]
            if (pinned_provider == self._config.embedding_provider
                    and pinned_model == self._config.embedding_model):
                return self._default_embeddings
            return create_embeddings(pinned_provider, pinned_model)
        return self._default_embeddings

    def _ensure_collection_config(self, collection: str) -> None:
        """Pin embedding config for a new collection."""
        self._ensure_embeddings()
        if not self._metadata.get_collection_config(collection):
            provider = self._config.embedding_provider
            model = self._config.embedding_model
            self._metadata.set_collection_config(
                collection, provider, model, self._default_embedding_dim
            )
            logger.info(f"Created collection {collection} (dim={self._default_embedding_dim})")

    def _get_collection_lock(self, collection: str) -> asyncio.Lock:
        if collection not in self._collection_locks:
            self._collection_locks[collection] = asyncio.Lock()
        return self._collection_locks[collection]

    # --- Ingest ---

    def _sanitize_and_validate(self, collection: str, file_path: Path,
                               replace_duplicates: bool,
                               original_filename: str | None = None) -> str:
        """Validate file and return sanitized filename. Raises on error."""
        filename = _sanitize_filename(original_filename or file_path.name)
        collection = _sanitize_collection(collection)

        if collection in self._reindex_in_progress:
            raise ReindexInProgressError()

        pending = [t for t in self._metadata.list_tasks(collection)
                   if t["status"] in ("pending", "running")]
        if len(pending) >= self._config.max_pending_tasks:
            raise IngestionQueueFullError(self._config.max_pending_tasks)

        if not replace_duplicates and self._metadata.document_exists(collection, filename):
            raise DocumentExistsError(filename)

        if not file_path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")
        file_size = file_path.stat().st_size
        max_bytes = self._config.max_upload_size_mb * 1024 * 1024
        if file_size > max_bytes:
            raise FileTooLargeError(file_size, max_bytes)

        return filename

    def _create_task_entry(self, collection: str, filename: str) -> dict[str, Any]:
        """Create a task entry. Refuses if reindex is in progress."""
        coll = _sanitize_collection(collection)
        if coll in self._reindex_in_progress:
            raise ReindexInProgressError()
        return self._create_task_entry_internal(coll, filename)

    def _create_reindex_task_entry(self, collection: str, filename: str) -> dict[str, Any]:
        """Create a task entry for reindex (bypasses reindex guard)."""
        return self._create_task_entry_internal(_sanitize_collection(collection), filename)

    def _create_task_entry_internal(self, collection: str, filename: str) -> dict[str, Any]:
        """Internal: create task entry without guard checks."""
        task_id = f"task_{uuid.uuid4().hex[:12]}"
        file_tasks = {filename: {"filename": filename, "status": "pending"}}
        return self._metadata.create_task(task_id, collection, 1, file_tasks)

    async def _run_ingest(self, collection: str, file_path: Path, filename: str,
                          task_id: str, replace_duplicates: bool,
                          skip_file_copy: bool = False) -> None:
        """Run ingestion for a single file in a background thread.

        Serialized per-collection via asyncio.Lock so Milvus Lite never sees
        concurrent access (its embedded gRPC server crashes under parallel writes).
        Docling parsing still runs in a thread to avoid blocking the event loop.
        """
        cancel_event = asyncio.Event()
        self._active_tasks[task_id] = cancel_event

        coll = _sanitize_collection(collection)

        # Serialize per-collection: Milvus Lite cannot handle concurrent connections.
        async with self._get_collection_lock(coll):
            # Pre-initialize vector store on the async thread (has event loop).
            # Milvus() constructor creates AsyncMilvusClient which requires a running
            # event loop — doing this here avoids a 75-retry timeout from asyncio.to_thread.
            self._ensure_collection_config(coll)
            if coll not in self._vector_stores:
                self._get_vector_store(coll)

            await asyncio.to_thread(
                self._ingest_sync, coll, file_path,
                filename, task_id, replace_duplicates, cancel_event, skip_file_copy,
            )

    async def ingest(self, collection: str, file_path: Path,
                     replace_duplicates: bool = True,
                     original_filename: str | None = None) -> dict[str, Any]:
        """Ingest a document file into a collection. Validates, creates task, runs ingestion."""
        collection = _sanitize_collection(collection)
        filename = self._sanitize_and_validate(collection, file_path, replace_duplicates, original_filename)
        task_info = self._create_task_entry(collection, filename)
        await self._run_ingest(collection, file_path, filename, task_info["task_id"], replace_duplicates)
        return self._metadata.get_task(task_info["task_id"])

    def _ingest_sync(self, collection: str, file_path: Path, filename: str,
                     task_id: str, replace_duplicates: bool,
                     cancel_event: asyncio.Event,
                     skip_file_copy: bool = False) -> None:
        """Synchronous ingestion worker. Runs in thread via asyncio.to_thread."""
        start = time.monotonic()
        try:
            self._metadata.update_task(task_id, status="running")
            self._metadata.update_task(
                task_id,
                file_tasks={filename: {"filename": filename, "status": "processing"}},
            )
            logger.info(f"Task {task_id}: pending -> running for {filename} in {collection}")

            if cancel_event.is_set():
                self._metadata.update_task(
                    task_id, status="cancelled",
                    file_tasks={filename: {"filename": filename, "status": "skipped"}},
                )
                return

            # Collection config and vector store already initialized in _run_ingest
            # (on the async thread where event loop is available for Milvus init)

            # Copy original file to storage (skip during reindex — file already in place)
            if not skip_file_copy:
                dest_dir = self._files_dir / collection
                dest_dir.mkdir(parents=True, exist_ok=True)
                shutil.copy2(file_path, dest_dir / filename)

            # Load + chunk
            docs = self._load_document(file_path)
            if not docs:
                raise ValueError(f"No content extracted from {filename}")

            # Enforce chunk limit
            if len(docs) > self._config.max_chunks_per_document:
                docs = docs[: self._config.max_chunks_per_document]
                logger.warning(f"Truncated {filename} to {self._config.max_chunks_per_document} chunks")

            # Normalize metadata — keep only fields we need, strip Docling extras
            # (dl_meta, headings, bounding_box etc. cause schema conflicts across formats)
            source_id = f"{collection}/{filename}"
            for doc in docs:
                page = doc.metadata.get("page", doc.metadata.get("dl_meta", {}).get("page", None) if isinstance(doc.metadata.get("dl_meta"), dict) else None)
                if page is not None:
                    try:
                        page = int(page)
                    except (TypeError, ValueError):
                        page = None
                meta = {
                    "source": source_id,
                    "filename": filename,
                }
                # Only include page when it has a value — Milvus can't infer
                # schema type from None on first insert (collection creation).
                if page is not None:
                    meta["page"] = page
                doc.metadata = meta
                # Validate metadata types for Milvus compatibility
                for key, val in doc.metadata.items():
                    if val is not None and not isinstance(val, (str, int, float, bool)):
                        logger.warning(f"Coercing metadata {key}={type(val).__name__} to str for {filename}")
                        doc.metadata[key] = str(val)

            if docs:
                logger.debug(f"Sample metadata for {filename}: {docs[0].metadata}")

            logger.info(
                f"Inserting {len(docs)} chunks into {self._config.vector_store} "
                f"collection {collection} for {filename}"
            )
            with self._milvus_lock:
                result = self._insert_documents(
                    collection, docs, source_id, filename, replace_duplicates
                )
            logger.info(
                f"{self._config.vector_store} insert complete for {filename}: "
                f"added={result.get('num_added', 0)}, skipped={result.get('num_skipped', 0)}"
            )

            duration = time.monotonic() - start
            chunk_count = result.get("num_added", 0) + result.get("num_updated", 0)
            # Build a short preview from the first chunk(s) for knowledge awareness
            _PREVIEW_MAX_CHARS = 500
            preview_parts: list[str] = []
            preview_len = 0
            for d in docs:
                text = d.page_content.strip()
                if not text:
                    continue
                remaining = _PREVIEW_MAX_CHARS - preview_len
                if remaining <= 0:
                    break
                preview_parts.append(text[:remaining])
                preview_len += len(preview_parts[-1])
            preview = " ".join(preview_parts).replace("\n", " ").strip()
            if len(preview) > _PREVIEW_MAX_CHARS:
                preview = preview[:_PREVIEW_MAX_CHARS].rsplit(" ", 1)[0] + "..."
            self._metadata.add_document(collection, filename, chunk_count or len(docs), preview=preview)
            self._metadata.update_task(
                task_id, status="completed", processed_files=1, successful_files=1,
                file_tasks={filename: {
                    "filename": filename, "status": "indexed",
                    "duration_seconds": round(duration, 2),
                }},
            )
            logger.info(f"Ingested {filename} -> {len(docs)} chunks in {collection} "
                        f"(added={result.get('num_added', 0)}, skipped={result.get('num_skipped', 0)})")

        except Exception as e:
            duration = time.monotonic() - start
            logger.error(f"Failed to ingest {filename}: {e}")
            self._metadata.update_task(
                task_id, status="failed", processed_files=1, failed_files=1,
                file_tasks={filename: {
                    "filename": filename, "status": "failed",
                    "error": str(e), "duration_seconds": round(duration, 2),
                }},
            )
        finally:
            self._active_tasks.pop(task_id, None)

    def _insert_documents(self, collection: str, docs: list, source_id: str,
                          filename: str, replace_duplicates: bool,
                          retry: bool = True) -> dict:
        """Insert documents into the vector store via adapter."""
        try:
            adapter = self._get_vector_store(collection)
            doc_exists = self._metadata.document_exists(collection, filename)

            if replace_duplicates and doc_exists:
                try:
                    adapter.delete_by_source(source_id)
                except Exception as e:
                    logger.debug(f"Pre-delete for {source_id}: {e}")
                rm = self._record_managers.get(collection)
                if rm:
                    try:
                        rm.delete_keys([source_id])
                    except Exception:
                        pass
                # Batch insert
                _BATCH = 50
                total_added = 0
                for i in range(0, len(docs), _BATCH):
                    result = adapter.add_documents(docs[i:i + _BATCH])
                    total_added += result.get("num_added", 0)
                return {"num_added": total_added, "num_skipped": 0}
            elif not doc_exists:
                return adapter.add_documents(docs)
            else:
                return {"num_added": 0, "num_skipped": len(docs)}
        except Exception as e:
            if retry and ("DataNotMatch" in str(e) or "schema" in str(e).lower()):
                # Schema mismatch — drop and recreate collection
                logger.warning(f"Schema mismatch in {collection}, dropping and recreating: {e}")
                self._vector_stores.pop(collection, None)
                self._record_managers.pop(collection, None)
                try:
                    adapter = self._get_vector_store(collection)
                    adapter.drop()
                except Exception:
                    pass
                self._metadata.delete_collection_metadata(collection)
                # Retry once with fresh collection
                return self._insert_documents(
                    collection, docs, source_id, filename, replace_duplicates, retry=False
                )
            raise

    async def ingest_url(self, collection: str, url: str) -> dict[str, Any]:
        """Ingest a document from URL."""
        self._validate_url(url)
        collection = _sanitize_collection(collection)

        import httpx
        import tempfile

        async with httpx.AsyncClient(
            follow_redirects=True, max_redirects=5,
            timeout=30.0, trust_env=False,
        ) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            max_bytes = self._config.max_url_download_size_mb * 1024 * 1024
            if len(resp.content) > max_bytes:
                raise FileTooLargeError(len(resp.content), max_bytes)

        parsed = urlparse(url)
        filename = _sanitize_filename(Path(parsed.path).name or "downloaded_page.html")

        # Write to temp file — kept alive until ingest completes (ingest is awaited)
        with tempfile.NamedTemporaryFile(suffix=f"_{filename}", delete=False) as tmp:
            tmp.write(resp.content)
            tmp_path = Path(tmp.name)

        try:
            return await self.ingest(collection, tmp_path, replace_duplicates=True)
        finally:
            tmp_path.unlink(missing_ok=True)

    # --- Delete (5-step compensating flow per plan) ---

    async def delete_document(self, collection: str, filename: str) -> None:
        """Delete a document. Idempotent compensating flow across stores."""
        collection = _sanitize_collection(collection)
        filename = _sanitize_filename(filename)

        # Step 1: Mark as deleting (single-store transaction)
        if not self._metadata.mark_deleting(collection, filename):
            raise DocumentNotFoundError(filename)

        async with self._get_collection_lock(collection):
            await asyncio.to_thread(self._delete_document_sync, collection, filename)

    def _delete_document_sync(self, collection: str, filename: str) -> None:
        """Synchronous 5-step delete. All steps idempotent."""
        source_id = f"{collection}/{filename}"
        try:
            # Step 2: Delete from vector store
            with self._milvus_lock:
                try:
                    adapter = self._get_vector_store(collection)
                    adapter.delete_by_source(source_id)
                except Exception as e:
                    logger.debug(f"{self._config.vector_store} delete for {source_id}: {e}")

            # Step 3: Clear record manager state (prevents stale dedup skip)
            rm = self._record_managers.get(collection)
            if rm:
                try:
                    rm.delete_keys([source_id])
                except Exception as e:
                    logger.debug(f"RecordManager delete for {source_id}: {e}")

            # Step 4: Delete from file storage
            file_path = self._files_dir / collection / filename
            file_path.unlink(missing_ok=True)

            # Step 5: Delete from metadata
            self._metadata.remove_document(collection, filename)
            logger.info(f"Deleted {filename} from {collection}")
        except Exception as e:
            logger.error(f"Delete incomplete for {filename} in {collection}: {e}")
            # Stays in "deleting" state — reconciliation will retry

    def _reconcile_deletes(self) -> None:
        """Retry stale deletes (runs on startup and hourly)."""
        stale = self._metadata.get_deleting_documents()
        for doc in stale:
            logger.info(f"Reconciling stale delete: {doc['filename']} in {doc['collection']}")
            self._delete_document_sync(doc["collection"], doc["filename"])

    # --- Session cleanup ---

    def _cleanup_expired_sessions(self, max_age_days: int = 7) -> None:
        """Drop session collections older than max_age_days."""
        # Use metadata DB to list session collections (backend-agnostic)
        all_configs = self._metadata.list_all_collection_configs()
        for col_name in all_configs:
            if not col_name.startswith("kb_sess_"):
                continue
            cfg = self._metadata.get_collection_config(col_name)
            if not cfg:
                continue
            from datetime import datetime, timezone, timedelta
            try:
                created = datetime.fromisoformat(cfg["created_at"])
                if datetime.now(timezone.utc) - created > timedelta(days=max_age_days):
                    self.drop_collection(col_name)
                    logger.info(f"Cleaned up expired session collection: {col_name}")
            except Exception as e:
                logger.debug(f"Could not check age for {col_name}: {e}")

    # --- Search ---

    async def search(self, collection: str, query: str,
                     limit: int = 10, score_threshold: float = 0.0) -> list[SearchResult]:
        """Search documents in a collection."""
        collection = _sanitize_collection(collection)
        limit = max(1, min(limit, 100))
        score_threshold = max(0.0, min(score_threshold, 1.0))

        def _search_sync():
            with self._milvus_lock:
                adapter = self._get_vector_store(collection)
                # Scores are already normalized to [0, 1] by the adapter
                # (uses LangChain's built-in relevance score normalization)
                scored = adapter.search(query, k=limit)
                if scored:
                    logger.debug(
                        f"Search '{query[:30]}' on {collection}: "
                        f"top_score={scored[0][1]:.4f}, count={len(scored)}, "
                        f"backend={self._config.vector_store}"
                    )
                return scored

        scored_results = await asyncio.to_thread(_search_sync)

        results = []
        seen_texts: set[str] = set()
        for doc, score in scored_results:
            if score >= score_threshold:
                text = doc.page_content
                if text in seen_texts:
                    continue
                seen_texts.add(text)
                results.append(SearchResult(
                    text=text,
                    filename=doc.metadata.get("filename", "unknown"),
                    page=doc.metadata.get("page", None),
                    score=round(score, 4),
                ))

        if results:
            logger.debug(f"Search '{query[:30]}' on {collection}: top_score={results[0].score}, count={len(results)}")
        return results

    # --- List ---

    async def list_documents(self, collection: str) -> list[DocInfo]:
        """List documents in a collection (hides 'deleting' status)."""
        collection = _sanitize_collection(collection)
        rows = self._metadata.list_documents(collection)
        return [DocInfo(**r) for r in rows]

    def get_document_file_path(self, collection: str, filename: str) -> Path:
        """Return the stored original file path for a document."""
        collection = _sanitize_collection(collection)
        filename = _sanitize_filename(filename)
        file_path = self._files_dir / collection / filename
        if not file_path.exists():
            raise DocumentNotFoundError(filename)
        return file_path

    # --- Tasks ---

    async def get_tasks(self, collection: str | None = None) -> list[dict[str, Any]]:
        return self._metadata.list_tasks(collection)

    async def get_task(self, task_id: str) -> dict[str, Any] | None:
        return self._metadata.get_task(task_id)

    async def cancel_task(self, task_id: str) -> dict[str, Any] | None:
        task = self._metadata.get_task(task_id)
        if not task:
            return None
        if task["status"] in ("completed", "failed", "cancelled"):
            return task

        cancel_event = self._active_tasks.get(task_id)
        if cancel_event:
            cancel_event.set()

        if task["status"] == "pending":
            file_tasks = task["file_tasks"]
            for ft in file_tasks.values():
                if ft["status"] == "pending":
                    ft["status"] = "skipped"
            self._metadata.update_task(task_id, status="cancelled", file_tasks=file_tasks)
            logger.debug(f"Task {task_id}: cancelled (was pending)")

        return self._metadata.get_task(task_id)

    # --- Knowledge config update (prepare / commit) ---

    def prepare_knowledge_update(self, knowledge_cfg: dict) -> PreparedKnowledgeUpdate:
        """Validate, coerce, preflight. No mutation. Raises ValueError/TypeError on bad input.

        All external calls (embedding creation, dimension check) happen here.
        If the incoming dict contains a ``rag_profile``, its parameters are
        expanded into the dict before coercion so the existing change-detection
        logic works unchanged.
        """
        from cuga.backend.knowledge.config import load_profile, VALID_PROFILES

        profile_name = knowledge_cfg.get("rag_profile")
        if profile_name and profile_name in VALID_PROFILES:
            try:
                profile_data = load_profile(profile_name)
                search = profile_data.get("search", {})
                chunking = profile_data.get("chunking", {})
                # Profile values are defaults; explicit keys in knowledge_cfg win
                expanded = {
                    "max_search_attempts": search.get("max_search_attempts"),
                    "default_limit": search.get("default_limit"),
                    "default_score_threshold": search.get("default_score_threshold"),
                    "chunk_size": chunking.get("chunk_size"),
                    "chunk_overlap": chunking.get("chunk_overlap"),
                    "rag_profile": profile_name,
                }
                # Remove None values and merge (profile as base, explicit overrides win)
                expanded = {k: v for k, v in expanded.items() if v is not None}
                knowledge_cfg = {**expanded, **knowledge_cfg}
            except FileNotFoundError:
                logger.warning("Profile %s not found, ignoring", profile_name)

        validated = KnowledgeConfig.coerce_and_validate(knowledge_cfg, base=self._config)

        embedding_changed = (
            validated.embedding_provider != self._config.embedding_provider
            or validated.embedding_model != self._config.embedding_model
        )
        chunking_changed = (
            validated.chunk_size != self._config.chunk_size
            or validated.chunk_overlap != self._config.chunk_overlap
        )
        metric_changed = validated.metric_type != self._config.metric_type
        reindex_recommended = embedding_changed or chunking_changed or metric_changed

        new_embeddings = None
        new_dim = None
        if embedding_changed:
            new_embeddings = create_embeddings(
                validated.embedding_provider, validated.embedding_model,
                use_gpu=validated.use_gpu,
            )
            new_dim = _get_embedding_dim(new_embeddings)

        return PreparedKnowledgeUpdate(
            validated=validated,
            embedding_changed=embedding_changed,
            chunking_changed=chunking_changed,
            metric_changed=metric_changed,
            reindex_recommended=reindex_recommended,
            new_embeddings=new_embeddings,
            new_embedding_dim=new_dim,
        )

    def commit_knowledge_update(self, prepared: PreparedKnowledgeUpdate) -> dict[str, Any]:
        """Commit a prepared update. Pure in-memory mutation, no external calls."""
        old_use_gpu = self._config.use_gpu

        for f in dc_fields(KnowledgeConfig):
            if f.name != "persist_dir":
                setattr(self._config, f.name, getattr(prepared.validated, f.name))

        if prepared.new_embeddings:
            self._default_embeddings = prepared.new_embeddings
            self._default_embedding_dim = prepared.new_embedding_dim
            with self._milvus_lock:
                self._vector_stores.clear()
                self._record_managers.clear()
        elif old_use_gpu != self._config.use_gpu and self._config.embedding_provider == "huggingface":
            # GPU preference changed for local embeddings — recreate on new device
            # No reindex needed (same vectors, different compute device)
            self._default_embeddings = create_embeddings(
                self._config.embedding_provider, self._config.embedding_model,
                use_gpu=self._config.use_gpu,
            )
            logger.info(f"Embeddings device changed: use_gpu={self._config.use_gpu}")

        return {
            "embedding_changed": prepared.embedding_changed,
            "chunking_changed": prepared.chunking_changed,
            "reindex_recommended": prepared.reindex_recommended,
        }

    def apply_knowledge_config(self, knowledge_cfg: dict) -> dict[str, Any]:
        """Convenience: prepare + commit in one call. Used by update_settings() compat."""
        prepared = self.prepare_knowledge_update(knowledge_cfg)
        return self.commit_knowledge_update(prepared)

    # --- Settings ---

    def get_settings(self) -> dict[str, Any]:
        from cuga.backend.knowledge.config import list_profiles

        return {
            "knowledge": {
                "enabled": self._config.enabled,
                "agent_level_enabled": self._config.agent_level_enabled,
                "session_level_enabled": self._config.session_level_enabled,
                "rag_profile": self._config.rag_profile,
                "embedding_provider": self._config.embedding_provider,
                "embedding_model": self._config.embedding_model,
                "use_gpu": self._config.use_gpu,
                "chunk_size": self._config.chunk_size,
                "chunk_overlap": self._config.chunk_overlap,
                "metric_type": self._config.metric_type,
                "max_pending_tasks": self._config.max_pending_tasks,
                "max_upload_size_mb": self._config.max_upload_size_mb,
                "max_url_download_size_mb": self._config.max_url_download_size_mb,
                "max_files_per_request": self._config.max_files_per_request,
                "max_chunks_per_document": self._config.max_chunks_per_document,
            },
            "rag_profiles": {
                name: {
                    "name": data.get("profile", {}).get("name", name),
                    "description": data.get("profile", {}).get("description", ""),
                    "search": data.get("search", {}),
                    "chunking": data.get("chunking", {}),
                }
                for name, data in list_profiles().items()
            },
        }

    def update_settings(self, **kwargs) -> dict[str, Any]:
        """Deprecated: use apply_knowledge_config() instead."""
        logger.warning("update_settings() is deprecated; use apply_knowledge_config()")
        self.apply_knowledge_config(kwargs)
        return self.get_settings()

    def health(self, collection: str | None = None) -> dict[str, Any]:
        h: dict[str, Any] = {
            "status": "healthy",
            "engine": f"langchain-{self._config.vector_store}",
            "settings": self.get_settings()["knowledge"],
            "embeddings_initialized": self._default_embeddings is not None,
            "reindex_in_progress": list(self._reindex_in_progress),
            "stale": False,
            "reindex_deferred": False,
        }
        if collection:
            # Hash-suffixed collections (kb_agent_X_{12-char-hash}) are created with
            # the exact settings that produce that hash — they cannot be stale.
            # Only check staleness for legacy hash-less collections.
            import re as _re
            _has_hash = bool(_re.search(r"_[0-9a-f]{12}$", collection))
            if not _has_hash:
                pinned = self._metadata.get_collection_config(collection)
                if pinned and (
                    pinned.get("embedding_provider") != self._config.embedding_provider
                    or pinned.get("embedding_model") != self._config.embedding_model
                ):
                    h["stale"] = True
            if collection in self._reindex_deferred:
                h["reindex_deferred"] = True
        return h

    # --- Collection lifecycle ---

    def drop_collection(self, collection: str) -> None:
        """Drop a collection and all its data."""
        collection = _sanitize_collection(collection)

        with self._milvus_lock:
            adapter = self._vector_stores.pop(collection, None)
            self._record_managers.pop(collection, None)
            if adapter:
                try:
                    adapter.drop()
                except Exception as e:
                    logger.debug(f"Drop collection {collection}: {e}")

        # Delete file storage
        files_dir = self._files_dir / collection
        if files_dir.exists():
            shutil.rmtree(files_dir)

        # Delete metadata
        self._metadata.delete_collection_metadata(collection)
        logger.info(f"Dropped collection {collection}")

    def drop_collection_vectors(self, collection: str) -> None:
        """Drop vectors and metadata but preserve source files for re-indexing."""
        collection = _sanitize_collection(collection)
        with self._milvus_lock:
            adapter = self._vector_stores.pop(collection, None)
            self._record_managers.pop(collection, None)
            if adapter:
                try:
                    adapter.drop()
                except Exception as e:
                    logger.debug(f"Drop collection vectors {collection}: {e}")
            else:
                # No cached adapter — create one just to drop
                try:
                    from cuga.backend.knowledge.vector_store import create_vector_store
                    embeddings = self._get_embeddings_for_collection(collection)
                    temp = create_vector_store(
                        self._config.vector_store, collection, embeddings,
                        self._config.persist_dir, self._config.metric_type,
                        self._config.pgvector_connection_string,
                    )
                    temp.drop()
                except Exception as e:
                    logger.debug(f"Drop uncached collection {collection}: {e}")
        self._metadata.delete_collection_metadata(collection)
        logger.info(f"Dropped collection vectors {collection} (files preserved)")

    async def copy_source_files(self, source_collection: str, target_collection: str) -> int:
        """Copy source files from one collection to another.

        Returns the number of files copied. Does not re-ingest — call reindex()
        on the target collection after copying.
        """
        import shutil

        source_collection = _sanitize_collection(source_collection)
        target_collection = _sanitize_collection(target_collection)
        src_dir = self._files_dir / source_collection
        dst_dir = self._files_dir / target_collection

        if not src_dir.exists():
            return 0

        dst_dir.mkdir(parents=True, exist_ok=True)
        count = 0
        for f in src_dir.iterdir():
            if f.is_file():
                shutil.copy2(str(f), str(dst_dir / f.name))
                count += 1
        logger.info("Copied %d source files from %s to %s", count, source_collection, target_collection)
        return count

    async def reindex(self, collection: str) -> dict[str, Any]:
        """Drop collection vectors and re-ingest all files with current settings.

        Creates per-file tasks. Returns immediately; ingestion runs in background.
        Raises ReindexBusyError if uploads are in progress.
        Sets _reindex_in_progress flag to block new uploads during reindex.
        """
        collection = _sanitize_collection(collection)
        files_dir = self._files_dir / collection
        if not files_dir.exists():
            return {"status": "no_documents", "count": 0}

        file_list = [f for f in files_dir.iterdir() if f.is_file()]
        if not file_list:
            return {"status": "no_documents", "count": 0}

        # All phases wrapped: flag is ALWAYS cleared on any failure path.
        task_ids: list[str] = []
        try:
            # Phase 1: Atomic check + flag + drop (under collection lock).
            lock = self._get_collection_lock(collection)
            async with lock:
                pending = [t for t in self._metadata.list_tasks(collection)
                           if t["status"] in ("pending", "running")]
                if pending:
                    raise ReindexBusyError(len(pending))
                self._reindex_in_progress.add(collection)
                self.drop_collection_vectors(collection)

            # Phase 2: Create per-file tasks AFTER drop (so they aren't deleted).
            for file_path in file_list:
                task_info = self._create_reindex_task_entry(collection, file_path.name)
                task_ids.append(task_info["task_id"])

            # Phase 3: Sequential background worker. Clears flags on completion.
            async def _reindex_worker():
                try:
                    for fp, tid in zip(file_list, task_ids):
                        await self._run_ingest(
                            collection, fp, fp.name, tid,
                            replace_duplicates=True, skip_file_copy=True,
                        )
                finally:
                    self._reindex_in_progress.discard(collection)
                    self._reindex_deferred.discard(collection)

            asyncio.create_task(_reindex_worker())
        except ReindexBusyError:
            raise  # Don't clear flag (was never set for this collection)
        except Exception:
            self._reindex_in_progress.discard(collection)
            for tid in task_ids:
                try:
                    self._metadata.update_task(tid, status="failed", file_tasks={})
                except Exception:
                    pass
            raise

        return {"status": "started", "count": len(file_list), "task_ids": task_ids}

    # --- Document loading ---

    _DOCLING_FORMATS = {
        ".pdf", ".docx", ".pptx", ".xlsx", ".html", ".htm",
        ".md", ".csv", ".asciidoc", ".adoc", ".tex", ".latex",
        ".png", ".jpg", ".jpeg", ".tiff", ".bmp", ".webp",
    }

    def _get_effective_chunk_settings(self) -> tuple[int, int]:
        """Get chunk_size and chunk_overlap from _config (source of truth after publish)."""
        return self._config.chunk_size, self._config.chunk_overlap

    def _build_docling_chunker(self, chunk_size: int):
        """Build a HybridChunker that respects our chunk_size config.

        HybridChunker combines hierarchical (heading-aware) splitting with
        token-based size limits.  Key features:
        - ``max_tokens`` enforces our configured chunk size
        - ``merge_peers=True`` merges small sibling chunks for density
        - ``repeat_table_header=True`` repeats table/form headers in every
          chunk so field labels are preserved alongside their values
        """
        try:
            from docling_core.transforms.chunker import HybridChunker

            return HybridChunker(max_tokens=chunk_size)
        except Exception as e:
            logger.warning(f"HybridChunker init failed, falling back to default: {e}")
            return None

    def _load_document(self, file_path: Path) -> list[Document]:
        """Load a document using Docling for supported formats, fallback for plain text."""
        suffix = file_path.suffix.lower()
        logger.info(f"Loading document: {file_path.name} (suffix={suffix}, size={file_path.stat().st_size} bytes)")

        chunk_size, chunk_overlap = self._get_effective_chunk_settings()

        if suffix in self._DOCLING_FORMATS:
            try:
                from langchain_docling.loader import ExportType

                chunker = self._build_docling_chunker(chunk_size)
                loader_kwargs: dict = {
                    "file_path": str(file_path),
                    "export_type": ExportType.DOC_CHUNKS,
                }
                if chunker is not None:
                    loader_kwargs["chunker"] = chunker
                loader = DoclingLoader(**loader_kwargs)
                docs = loader.load()
            except Exception as e:
                translated = _translate_document_load_error(file_path, e)
                logger.error(
                    f"Docling failed to parse {file_path.name}: "
                    f"{type(translated).__name__}: {translated}"
                )
                raise translated from e
        elif suffix in (".txt", ".text", ".log", ".json", ".xml", ".yaml", ".yml",
                        ".toml", ".ini", ".cfg", ".conf"):
            text = file_path.read_text(errors="replace")
            splitter = RecursiveCharacterTextSplitter(
                chunk_size=chunk_size,
                chunk_overlap=chunk_overlap,
            )
            chunks = splitter.split_text(text)
            docs = [
                Document(page_content=chunk, metadata={"page": i + 1})
                for i, chunk in enumerate(chunks)
            ]
        else:
            try:
                from langchain_docling.loader import ExportType

                chunker = self._build_docling_chunker(chunk_size)
                loader_kwargs = {
                    "file_path": str(file_path),
                    "export_type": ExportType.DOC_CHUNKS,
                }
                if chunker is not None:
                    loader_kwargs["chunker"] = chunker
                loader = DoclingLoader(**loader_kwargs)
                docs = loader.load()
            except Exception:
                raise ValueError(f"Unsupported file format: {suffix}")

        logger.info(f"Loaded {len(docs)} raw chunks from {file_path.name}")

        # Post-process: re-split any oversized chunks from Docling
        # This ensures stored chunk_size/chunk_overlap settings are respected
        # even for Docling-parsed formats.
        if docs and any(len(d.page_content) > chunk_size * 2 for d in docs):
            splitter = RecursiveCharacterTextSplitter(
                chunk_size=chunk_size,
                chunk_overlap=chunk_overlap,
            )
            docs = splitter.split_documents(docs)
            logger.info(f"Re-split into {len(docs)} chunks (chunk_size={chunk_size})")

        return docs

    # --- URL validation ---

    def _validate_url(self, url: str) -> None:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            raise ValueError("Only http/https URLs allowed")
        if parsed.hostname in BLOCKED_HOSTNAMES:
            raise ValueError("Blocked hostname")
        if "@" in (parsed.netloc or ""):
            raise ValueError("URL credentials not allowed")
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        if port not in ALLOWED_PORTS:
            raise ValueError(f"Port {port} not allowed")
        for family, _, _, _, sockaddr in socket.getaddrinfo(parsed.hostname, None):
            addr = ipaddress.ip_address(sockaddr[0])
            if any([addr.is_private, addr.is_loopback, addr.is_link_local,
                    addr.is_reserved, addr.is_multicast, addr.is_unspecified]):
                raise ValueError("Private/internal/reserved URLs not allowed")


# --- Helpers ---

def _sanitize_collection(name: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_]", "_", name)


def _sanitize_filename(name: str) -> str:
    if ".." in name:
        raise ValueError("Invalid filename: path traversal detected")
    # Strip path separators and control chars, but preserve Unicode (Hebrew, CJK, etc.)
    name = name.replace("/", "_").replace("\\", "_").replace("\x00", "")
    # Remove only control characters and problematic filesystem chars
    return re.sub(r'[\x00-\x1f<>:"|?*]', "_", name)


# --- Exceptions ---

class IngestionQueueFullError(Exception):
    def __init__(self, max_pending: int):
        self.max_pending = max_pending
        super().__init__(f"Ingestion queue full (max {max_pending} pending tasks)")


class DocumentExistsError(Exception):
    def __init__(self, filename: str):
        self.filename = filename
        super().__init__(f"File already indexed: {filename}")


class DocumentNotFoundError(Exception):
    def __init__(self, filename: str):
        self.filename = filename
        super().__init__(f"Document not found: {filename}")


class FileTooLargeError(Exception):
    def __init__(self, size: int, max_size: int):
        self.size = size
        self.max_size = max_size
        super().__init__(f"File too large: {size} bytes (max {max_size})")
