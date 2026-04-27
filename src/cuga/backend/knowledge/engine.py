"""In-process knowledge engine using LangChain vector stores + Docling.

Replaces OpenRAG with zero external services. All document parsing, embedding,
vector storage, and search happen in-process.
"""

from __future__ import annotations

import asyncio
import collections
import functools
import ipaddress
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
from urllib.parse import urljoin, urlparse

from pydantic import ConfigDict

from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings
from langchain_core.indexing import InMemoryRecordManager
from langchain_docling import DoclingLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter

from cuga.backend.knowledge.config import KnowledgeConfig, knowledge_vector_backend_for_settings
from cuga.backend.knowledge.interprocess_lock import acquire_exclusive_nonblocking, release_exclusive
from cuga.backend.knowledge.metadata import create_knowledge_metadata
from cuga.backend.storage.facade import get_storage_connection_params
from cuga.backend.knowledge.vector_store_base import VectorStoreAdapter

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


def _page_from_docling_dl_meta(dl_meta: Any) -> int | None:
    """Infer PDF page from Docling chunk metadata (``doc_items`` → ``prov`` → ``page_no``).

    See https://docling-project.github.io/docling/concepts/chunking/ — chunk metadata
    lists contributing document items; each item carries provenance with ``page_no``.
    When a chunk spans multiple pages, we use the minimum page number (chunk start).
    """
    if not isinstance(dl_meta, dict):
        return None
    doc_items = dl_meta.get("doc_items")
    if not isinstance(doc_items, list):
        return None
    pages: list[int] = []
    for item in doc_items:
        if not isinstance(item, dict):
            continue
        prov = item.get("prov")
        if not isinstance(prov, list):
            continue
        for p in prov:
            if not isinstance(p, dict):
                continue
            pn = p.get("page_no")
            if isinstance(pn, int):
                pages.append(pn)
    if not pages:
        return None
    return min(pages)


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


class _FastEmbedEmbeddings(Embeddings):
    """LangChain Embeddings adapter around fastembed.TextEmbedding."""

    def __init__(self, model_name: str):
        from fastembed import TextEmbedding
        import os

        # fastembed defaults cache_dir to ~/fastembed_cache which resolves to
        # /tmp/fastembed_cache when HOME=/tmp (container). Explicitly pass the
        # build-time cache so it finds the pre-extracted model dir immediately,
        # before ever reaching the local_files_only check.
        cache_dir = os.environ.get("FASTEMBED_CACHE_PATH")
        local_files_only = os.environ.get("HF_HUB_OFFLINE", "0") == "1"
        self._model = TextEmbedding(model_name, cache_dir=cache_dir, local_files_only=local_files_only)

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [v.tolist() for v in self._model.embed(texts)]

    def embed_query(self, text: str) -> list[float]:
        return next(self._model.embed([text])).tolist()


def _fastembed_docling_seq_limit(model_name: str) -> int:
    """Upper input length (tokens) for the configured fastembed ONNX model."""
    m = (model_name or "").lower()
    if "m-long" in m or "embed-m-long" in m:
        return 2048
    return 512


@functools.lru_cache(maxsize=1)
def _fastembed_docling_tokenizer_cls():
    """HybridChunker tokenizer using fastembed's Rust tokenizer (avoids HF MiniLM download)."""
    from docling_core.transforms.chunker.tokenizer.base import BaseTokenizer

    class _FastEmbedDoclingTokenizer(BaseTokenizer):
        model_config = ConfigDict(arbitrary_types_allowed=True)

        text_embedding: Any
        max_tokens: int

        def _rust_tokenizer(self):
            inner = self.text_embedding.model
            if inner.tokenizer is None:
                inner.load_onnx_model()
            return inner.tokenizer

        def count_tokens(self, text: str) -> int:
            enc = self._rust_tokenizer().encode(text)
            return int(sum(enc.attention_mask))

        def get_max_tokens(self) -> int:
            return self.max_tokens

        def get_tokenizer(self) -> Any:
            return self._rust_tokenizer()

    return _FastEmbedDoclingTokenizer


def create_embeddings(config: "KnowledgeConfig") -> Embeddings:
    """Create an Embeddings instance for the configured provider.

    Providers:
        fastembed   — lightweight local embeddings (default, installed with cuga)
        huggingface — HuggingFace sentence-transformers (optional: pip install sentence-transformers)
        openai      — OpenAI API (requires api_key)
        ollama      — local Ollama server
    """
    import os

    provider = config.embedding_provider
    model = config.embedding_model

    if provider == "fastembed":
        return _FastEmbedEmbeddings(model or "BAAI/bge-small-en-v1.5")

    if provider == "huggingface":
        model = model or "BAAI/bge-small-en-v1.5"
        try:
            from langchain_huggingface import HuggingFaceEmbeddings
        except ImportError:
            raise ImportError(
                "HuggingFace embedding provider requires sentence-transformers. "
                "Install with: pip install sentence-transformers langchain-huggingface"
            )
        return HuggingFaceEmbeddings(model_name=model)

    if provider == "openai":
        from langchain_openai import OpenAIEmbeddings

        api_key = config.embedding_api_key or os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise ValueError(
                "OpenAI embedding provider requires an API key. "
                "Set knowledge.embeddings.api_key in settings or OPENAI_API_KEY env var."
            )
        kwargs: dict[str, Any] = {"model": model or "text-embedding-3-small", "api_key": api_key}
        if config.embedding_base_url:
            kwargs["base_url"] = config.embedding_base_url
        return OpenAIEmbeddings(**kwargs)

    if provider == "ollama":
        from langchain_ollama import OllamaEmbeddings

        base_url = config.embedding_base_url or "http://localhost:11434"
        return OllamaEmbeddings(model=model or "nomic-embed-text", base_url=base_url)

    raise ValueError(
        f"Unknown embedding provider: {provider}. Supported: fastembed, huggingface, openai, ollama"
    )


def _get_embedding_dim(embeddings: Embeddings) -> int:
    """Get embedding dimension by embedding a test string."""
    test_vec = embeddings.embed_query("test")
    return len(test_vec)


# --- Engine ---


class KnowledgeEngine:
    """In-process knowledge engine (chunking, embeddings, pluggable vector backends)."""

    def __init__(self, config: KnowledgeConfig):
        config.validate()
        self._config = config
        self._files_dir = config.persist_dir / "files"
        _smode, _, _pghost = get_storage_connection_params()
        self._metadata = create_knowledge_metadata(config.persist_dir, mode=_smode, postgres_url=_pghost)

        # Ensure directories exist
        config.persist_dir.mkdir(parents=True, exist_ok=True)
        self._files_dir.mkdir(parents=True, exist_ok=True)

        # Single-writer lock (flock / msvcrt — race-free)
        self._lock_file = open(config.persist_dir / ".lock", "w+b")
        try:
            acquire_exclusive_nonblocking(self._lock_file)
        except OSError:
            self._lock_file.close()
            raise RuntimeError("Knowledge engine already running in another process. Start with --workers 1")

        # Default embeddings (lazy — initialized on first use to speed up startup)
        self._default_embeddings = None
        self._default_embedding_dim = None

        # Vector store LRU cache (bounded)
        self._vector_stores: collections.OrderedDict[str, VectorStoreAdapter] = collections.OrderedDict()

        # Record managers for dedup (InMemoryRecordManager per collection)
        self._record_managers: dict[str, InMemoryRecordManager] = {}

        # Docling converter (lazy, reused across all document loads)
        self._docling_converter = None

        self._vector_store_lock = threading.Lock()

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

        self._metadata_ready = False
        self._metadata_init_lock = asyncio.Lock()

        from cuga.config import settings as _settings

        _vb = knowledge_vector_backend_for_settings(_settings)
        _sm = getattr(getattr(_settings, "storage", None), "mode", "local")
        logger.info(
            f"Knowledge engine started: "
            f"storage.mode={_sm} vector_backend={_vb}, "
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
                    if self._metadata_ready:
                        await self._reconcile_deletes()
                        await self._metadata.purge_old_tasks(max_age_days=7)
                        await self._cleanup_expired_sessions()
                    logger.debug("Background maintenance completed")
                except asyncio.CancelledError:
                    break
                except Exception as e:
                    logger.error(f"Background maintenance error: {e}")

        task = asyncio.ensure_future(_maintenance_loop())
        self._background_tasks.append(task)

    async def _ensure_metadata_ready(self) -> None:
        if self._metadata_ready:
            return
        async with self._metadata_init_lock:
            if self._metadata_ready:
                return
            await self._metadata.ensure_ready()
            recovered = await self._metadata.recover_stale_tasks()
            if recovered:
                logger.info(f"Recovered {recovered} stale task(s) from previous crash")
            await self._reconcile_deletes()
            purged = await self._metadata.purge_old_tasks(max_age_days=7)
            if purged:
                logger.debug(f"Purged {purged} old task(s)")
            self._metadata_ready = True

    async def aclose(self) -> None:
        try:
            await self._metadata.close()
        except Exception as e:
            logger.debug(f"Knowledge metadata close: {e}")

    def shutdown(self) -> None:
        """Release resources."""
        self._shutdown_event.set()
        for task in self._background_tasks:
            task.cancel()
        try:
            release_exclusive(self._lock_file)
            self._lock_file.close()
        except Exception:
            pass
        logger.info("Knowledge engine stopped")

    # --- Embeddings (lazy init) ---

    def _ensure_embeddings(self) -> None:
        """Initialize embeddings on first use (not at engine startup)."""
        if self._default_embeddings is None:
            self._default_embeddings = create_embeddings(self._config)
            self._default_embedding_dim = _get_embedding_dim(self._default_embeddings)
            logger.info(
                f"Embeddings initialized: provider={self._config.embedding_provider}, "
                f"model={self._config.embedding_model or '(default)'}, "
                f"dim={self._default_embedding_dim}"
            )

    async def warmup(self) -> dict[str, Any]:
        """Preload heavyweight resources so callers can gate on readiness."""
        await self._ensure_metadata_ready()
        await asyncio.to_thread(self._ensure_embeddings)
        return {
            "embedding_provider": self._config.embedding_provider,
            "embedding_model": self._config.embedding_model or "auto",
            "embeddings_initialized": self._default_embeddings is not None,
        }

    def _knowledge_vector_backend(self) -> str:
        from cuga.config import settings

        return knowledge_vector_backend_for_settings(settings)

    # --- Vector store (LRU cache, bounded) ---

    def _get_record_manager(self, collection: str) -> InMemoryRecordManager:
        if collection not in self._record_managers:
            rm = InMemoryRecordManager(namespace=collection)
            rm.create_schema()
            self._record_managers[collection] = rm
        return self._record_managers[collection]

    async def _resolve_embeddings_for_collection(self, collection: str) -> Embeddings:
        self._ensure_embeddings()
        cfg = await self._metadata.get_collection_config(collection)
        if cfg:
            pinned_provider = cfg["embedding_provider"]
            pinned_model = cfg["embedding_model"]
            if (
                pinned_provider == self._config.embedding_provider
                and pinned_model == self._config.embedding_model
            ):
                return self._default_embeddings
            from dataclasses import replace

            pinned_cfg = replace(
                self._config, embedding_provider=pinned_provider, embedding_model=pinned_model
            )
            return create_embeddings(pinned_cfg)
        return self._default_embeddings

    def _create_vector_adapter(self, collection: str, embeddings: Embeddings):
        from cuga.backend.knowledge.vector_store import create_vector_store

        return create_vector_store(
            backend=self._knowledge_vector_backend(),
            collection=collection,
            embeddings=embeddings,
            persist_dir=self._config.persist_dir,
            metric_type=self._config.metric_type,
            pgvector_connection_string=self._config.pgvector_connection_string,
        )

    def _vector_cache_put(self, collection: str, adapter: VectorStoreAdapter) -> None:
        while len(self._vector_stores) >= _VS_CACHE_MAX:
            evicted_name, _ = self._vector_stores.popitem(last=False)
            logger.debug(f"Evicted vector store cache: {evicted_name}")
        self._vector_stores[collection] = adapter

    async def _ensure_vector_store_cached(self, collection: str) -> None:
        if collection in self._vector_stores:
            self._vector_stores.move_to_end(collection)
            return
        embeddings = await self._resolve_embeddings_for_collection(collection)

        def _sync_put() -> None:
            with self._vector_store_lock:
                if collection in self._vector_stores:
                    self._vector_stores.move_to_end(collection)
                    return
                adapter = self._create_vector_adapter(collection, embeddings)
                self._vector_cache_put(collection, adapter)

        await asyncio.to_thread(_sync_put)

    async def _ensure_collection_config(self, collection: str) -> None:
        self._ensure_embeddings()
        if not await self._metadata.get_collection_config(collection):
            provider = self._config.embedding_provider
            model = self._config.embedding_model
            await self._metadata.set_collection_config(
                collection, provider, model, self._default_embedding_dim
            )
            logger.info(f"Created collection {collection} (dim={self._default_embedding_dim})")

    def _get_collection_lock(self, collection: str) -> asyncio.Lock:
        if collection not in self._collection_locks:
            self._collection_locks[collection] = asyncio.Lock()
        return self._collection_locks[collection]

    # --- Ingest ---

    async def _sanitize_and_validate(
        self, collection: str, file_path: Path, replace_duplicates: bool, original_filename: str | None = None
    ) -> str:
        """Validate file and return sanitized filename. Raises on error."""
        filename = _sanitize_filename(original_filename or file_path.name)
        collection = _sanitize_collection(collection)

        if collection in self._reindex_in_progress:
            raise ReindexInProgressError()

        pending = [
            t for t in await self._metadata.list_tasks(collection) if t["status"] in ("pending", "running")
        ]
        if len(pending) >= self._config.max_pending_tasks:
            raise IngestionQueueFullError(self._config.max_pending_tasks)

        if not replace_duplicates and await self._metadata.document_exists(collection, filename):
            raise DocumentExistsError(filename)

        if not file_path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")
        file_size = file_path.stat().st_size
        max_bytes = self._config.max_upload_size_mb * 1024 * 1024
        if file_size > max_bytes:
            raise FileTooLargeError(file_size, max_bytes)

        return filename

    async def _create_task_entry(self, collection: str, filename: str) -> dict[str, Any]:
        coll = _sanitize_collection(collection)
        if coll in self._reindex_in_progress:
            raise ReindexInProgressError()
        return await self._create_task_entry_internal(coll, filename)

    async def _create_reindex_task_entry(self, collection: str, filename: str) -> dict[str, Any]:
        return await self._create_task_entry_internal(_sanitize_collection(collection), filename)

    async def _create_task_entry_internal(self, collection: str, filename: str) -> dict[str, Any]:
        task_id = f"task_{uuid.uuid4().hex[:12]}"
        file_tasks = {filename: {"filename": filename, "status": "pending"}}
        return await self._metadata.create_task(task_id, collection, 1, file_tasks)

    async def _run_ingest(
        self,
        collection: str,
        file_path: Path,
        filename: str,
        task_id: str,
        replace_duplicates: bool,
        skip_file_copy: bool = False,
    ) -> None:
        """Run ingestion for a single file in a background thread.

        Serialized per-collection via asyncio.Lock. Docling parsing still runs in a
        thread to avoid blocking the event loop.
        """
        cancel_event = asyncio.Event()
        self._active_tasks[task_id] = cancel_event

        coll = _sanitize_collection(collection)
        await self._ensure_metadata_ready()

        async with self._get_collection_lock(coll):
            await self._ensure_collection_config(coll)
            await self._ensure_vector_store_cached(coll)
            await self._ingest_inner(
                coll,
                file_path,
                filename,
                task_id,
                replace_duplicates,
                cancel_event,
                skip_file_copy,
            )

    async def ingest(
        self,
        collection: str,
        file_path: Path,
        replace_duplicates: bool = True,
        original_filename: str | None = None,
    ) -> dict[str, Any]:
        """Ingest a document file into a collection. Validates, creates task, runs ingestion."""
        await self._ensure_metadata_ready()
        collection = _sanitize_collection(collection)
        filename = await self._sanitize_and_validate(
            collection, file_path, replace_duplicates, original_filename
        )
        task_info = await self._create_task_entry(collection, filename)
        await self._run_ingest(collection, file_path, filename, task_info["task_id"], replace_duplicates)
        return await self._metadata.get_task(task_info["task_id"])

    async def _ingest_inner(
        self,
        collection: str,
        file_path: Path,
        filename: str,
        task_id: str,
        replace_duplicates: bool,
        cancel_event: asyncio.Event,
        skip_file_copy: bool = False,
    ) -> None:
        start = time.monotonic()
        try:
            await self._metadata.update_task(task_id, status="running")
            await self._metadata.update_task(
                task_id,
                file_tasks={filename: {"filename": filename, "status": "processing"}},
            )
            logger.info(f"Task {task_id}: pending -> running for {filename} in {collection}")

            if cancel_event.is_set():
                await self._metadata.update_task(
                    task_id,
                    status="cancelled",
                    file_tasks={filename: {"filename": filename, "status": "skipped"}},
                )
                return

            docs = await asyncio.to_thread(self._load_document, file_path)
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
                page = doc.metadata.get("page")
                dl_meta = doc.metadata.get("dl_meta")
                if page is None and isinstance(dl_meta, dict):
                    page = dl_meta.get("page")
                if page is None and isinstance(dl_meta, dict):
                    page = _page_from_docling_dl_meta(dl_meta)
                if page is not None:
                    try:
                        page = int(page)
                    except (TypeError, ValueError):
                        page = None
                meta = {
                    "source": source_id,
                    "filename": filename,
                }
                # Only include page when set — keeps chunk metadata JSON-friendly
                if page is not None:
                    meta["page"] = page
                doc.metadata = meta
                # Coerce exotic types for vector backends
                for key, val in doc.metadata.items():
                    if val is not None and not isinstance(val, (str, int, float, bool)):
                        logger.warning(f"Coercing metadata {key}={type(val).__name__} to str for {filename}")
                        doc.metadata[key] = str(val)

            if docs:
                logger.debug(f"Sample metadata for {filename}: {docs[0].metadata}")

            logger.info(
                f"Inserting {len(docs)} chunks into {self._knowledge_vector_backend()} "
                f"collection {collection} for {filename}"
            )
            result = await self._insert_documents_async(
                collection, docs, source_id, filename, replace_duplicates
            )
            logger.info(
                f"{self._knowledge_vector_backend()} insert complete for {filename}: "
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
            await self._metadata.add_document(collection, filename, chunk_count or len(docs), preview=preview)

            if not skip_file_copy:
                dest_dir = self._files_dir / collection
                dest_dir.mkdir(parents=True, exist_ok=True)
                dest = dest_dir / filename
                if file_path.resolve() != dest.resolve():
                    try:
                        await asyncio.to_thread(shutil.copy2, str(file_path), str(dest))
                    except Exception:
                        if dest.exists():
                            try:
                                await asyncio.to_thread(dest.unlink)
                            except OSError:
                                pass
                        raise

            await self._metadata.update_task(
                task_id,
                status="completed",
                processed_files=1,
                successful_files=1,
                file_tasks={
                    filename: {
                        "filename": filename,
                        "status": "indexed",
                        "duration_seconds": round(duration, 2),
                    }
                },
            )
            logger.info(
                f"Ingested {filename} -> {len(docs)} chunks in {collection} "
                f"(added={result.get('num_added', 0)}, skipped={result.get('num_skipped', 0)})"
            )

        except Exception as e:
            duration = time.monotonic() - start
            logger.error(f"Failed to ingest {filename}: {e}")
            await self._metadata.update_task(
                task_id,
                status="failed",
                processed_files=1,
                failed_files=1,
                file_tasks={
                    filename: {
                        "filename": filename,
                        "status": "failed",
                        "error": str(e),
                        "duration_seconds": round(duration, 2),
                    }
                },
            )
        finally:
            self._active_tasks.pop(task_id, None)

    async def _insert_documents_async(
        self,
        collection: str,
        docs: list,
        source_id: str,
        filename: str,
        replace_duplicates: bool,
        retry: bool = True,
    ) -> dict:
        try:
            doc_exists = await self._metadata.document_exists(collection, filename)

            def _vector_mutation() -> dict:
                with self._vector_store_lock:
                    adapter = self._vector_stores[collection]
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
                        _BATCH = 50
                        total_added = 0
                        for i in range(0, len(docs), _BATCH):
                            result = adapter.add_documents(docs[i : i + _BATCH])
                            total_added += result.get("num_added", 0)
                        return {"num_added": total_added, "num_skipped": 0}
                    if not doc_exists:
                        return adapter.add_documents(docs)
                    return {"num_added": 0, "num_skipped": len(docs)}

            return await asyncio.to_thread(_vector_mutation)
        except Exception as e:
            if retry and ("DataNotMatch" in str(e) or "schema" in str(e).lower()):
                logger.warning(f"Schema mismatch in {collection}, dropping and recreating: {e}")
                with self._vector_store_lock:
                    self._vector_stores.pop(collection, None)
                self._record_managers.pop(collection, None)
                try:
                    await self._ensure_vector_store_cached(collection)

                    def _drop_vec() -> None:
                        with self._vector_store_lock:
                            ad = self._vector_stores.get(collection)
                            if ad:
                                ad.drop()

                    await asyncio.to_thread(_drop_vec)
                except Exception:
                    pass
                await self._metadata.delete_collection_metadata(collection)
                await self._ensure_vector_store_cached(collection)
                return await self._insert_documents_async(
                    collection, docs, source_id, filename, replace_duplicates, retry=False
                )
            raise

    async def ingest_url(self, collection: str, url: str) -> dict[str, Any]:
        """Ingest a document from URL."""
        self._validate_url(url)
        collection = _sanitize_collection(collection)

        import httpx
        import tempfile

        max_redirects = 5
        current_url = url
        fetch_result: tuple[str, bytes] | None = None
        async with httpx.AsyncClient(
            follow_redirects=False,
            timeout=30.0,
            trust_env=False,
        ) as client:
            redirect_count = 0
            while True:
                async with client.stream("GET", current_url, follow_redirects=False) as resp:
                    if resp.is_redirect:
                        if redirect_count >= max_redirects:
                            raise ValueError(f"Too many redirects (max {max_redirects})")
                        location = resp.headers.get("location")
                        if not location:
                            raise ValueError("Redirect response missing Location header")
                        next_url = urljoin(str(resp.url), location.strip())
                        self._validate_url(next_url)
                        current_url = next_url
                        redirect_count += 1
                        continue
                    resp.raise_for_status()
                    max_bytes = self._config.max_url_download_size_mb * 1024 * 1024
                    total = 0
                    buf = bytearray()
                    async for chunk in resp.aiter_bytes(8192):
                        n = len(chunk)
                        total += n
                        if total > max_bytes:
                            raise FileTooLargeError(total, max_bytes)
                        buf.extend(chunk)
                    fetch_result = (str(resp.url), bytes(buf))
                    break

        if fetch_result is None:
            raise RuntimeError("URL download finished without a response body")
        final_url, downloaded = fetch_result

        parsed = urlparse(final_url)
        filename = _sanitize_filename(Path(parsed.path).name or "downloaded_page.html")

        # Write to temp file — kept alive until ingest completes (ingest is awaited)
        with tempfile.NamedTemporaryFile(suffix=f"_{filename}", delete=False) as tmp:
            tmp.write(downloaded)
            tmp_path = Path(tmp.name)

        try:
            return await self.ingest(collection, tmp_path, replace_duplicates=True)
        finally:
            tmp_path.unlink(missing_ok=True)

    # --- Delete (5-step compensating flow per plan) ---

    async def delete_document(self, collection: str, filename: str) -> None:
        """Delete a document. Idempotent compensating flow across stores."""
        await self._ensure_metadata_ready()
        collection = _sanitize_collection(collection)
        filename = _sanitize_filename(filename)

        if not await self._metadata.mark_deleting(collection, filename):
            raise DocumentNotFoundError(filename)

        async with self._get_collection_lock(collection):
            await self._ensure_vector_store_cached(collection)
            try:
                await asyncio.to_thread(self._delete_vector_and_file, collection, filename)
                await self._metadata.remove_document(collection, filename)
                logger.info(f"Deleted {filename} from {collection}")
            except Exception as e:
                logger.error(f"Delete incomplete for {filename} in {collection}: {e}")

    def _delete_vector_and_file(self, collection: str, filename: str) -> None:
        source_id = f"{collection}/{filename}"
        with self._vector_store_lock:
            try:
                adapter = self._vector_stores.get(collection)
                if adapter:
                    adapter.delete_by_source(source_id)
            except Exception as e:
                logger.debug(f"{self._knowledge_vector_backend()} delete for {source_id}: {e}")

        rm = self._record_managers.get(collection)
        if rm:
            try:
                rm.delete_keys([source_id])
            except Exception as e:
                logger.debug(f"RecordManager delete for {source_id}: {e}")

        file_path = self._files_dir / collection / filename
        file_path.unlink(missing_ok=True)

    async def _finalize_stale_delete(self, collection: str, filename: str) -> None:
        collection = _sanitize_collection(collection)
        filename = _sanitize_filename(filename)
        async with self._get_collection_lock(collection):
            await self._ensure_vector_store_cached(collection)
            try:
                await asyncio.to_thread(self._delete_vector_and_file, collection, filename)
                await self._metadata.remove_document(collection, filename)
                logger.info(f"Reconciled delete: {filename} from {collection}")
            except Exception as e:
                logger.error(f"Reconcile delete incomplete for {filename} in {collection}: {e}")

    async def _reconcile_deletes(self) -> None:
        stale = await self._metadata.get_deleting_documents()
        for doc in stale:
            logger.info(f"Reconciling stale delete: {doc['filename']} in {doc['collection']}")
            await self._finalize_stale_delete(doc["collection"], doc["filename"])

    async def _cleanup_expired_sessions(self, max_age_days: int = 7) -> None:
        all_configs = await self._metadata.list_all_collection_configs()
        for col_name in all_configs:
            if not col_name.startswith("kb_sess_"):
                continue
            cfg = await self._metadata.get_collection_config(col_name)
            if not cfg:
                continue
            from datetime import datetime, timezone, timedelta

            try:
                created = datetime.fromisoformat(cfg["created_at"])
                if datetime.now(timezone.utc) - created > timedelta(days=max_age_days):
                    await self.drop_collection(col_name)
                    logger.info(f"Cleaned up expired session collection: {col_name}")
            except Exception as e:
                logger.debug(f"Could not check age for {col_name}: {e}")

    # --- Search ---

    async def search(
        self, collection: str, query: str, limit: int = 10, score_threshold: float = 0.0
    ) -> list[SearchResult]:
        """Search documents in a collection."""
        await self._ensure_metadata_ready()
        collection = _sanitize_collection(collection)
        limit = max(1, min(limit, 100))
        score_threshold = max(0.0, min(score_threshold, 1.0))

        await self._ensure_vector_store_cached(collection)

        def _search_sync():
            with self._vector_store_lock:
                adapter = self._vector_stores[collection]
                scored = adapter.search(query, k=limit)
                if scored:
                    logger.debug(
                        f"Search '{query[:30]}' on {collection}: "
                        f"top_score={scored[0][1]:.4f}, count={len(scored)}, "
                        f"backend={self._knowledge_vector_backend()}"
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
                results.append(
                    SearchResult(
                        text=text,
                        filename=doc.metadata.get("filename", "unknown"),
                        page=doc.metadata.get("page", None),
                        score=round(score, 4),
                    )
                )

        if results:
            logger.debug(
                f"Search '{query[:30]}' on {collection}: top_score={results[0].score}, count={len(results)}"
            )
        return results

    # --- List ---

    async def list_documents(self, collection: str) -> list[DocInfo]:
        """List documents in a collection (hides 'deleting' status)."""
        await self._ensure_metadata_ready()
        collection = _sanitize_collection(collection)
        rows = await self._metadata.list_documents(collection)
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
        await self._ensure_metadata_ready()
        return await self._metadata.list_tasks(collection)

    async def get_task(self, task_id: str) -> dict[str, Any] | None:
        await self._ensure_metadata_ready()
        return await self._metadata.get_task(task_id)

    async def cancel_task(self, task_id: str) -> dict[str, Any] | None:
        await self._ensure_metadata_ready()
        task = await self._metadata.get_task(task_id)
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
            await self._metadata.update_task(task_id, status="cancelled", file_tasks=file_tasks)
            logger.debug(f"Task {task_id}: cancelled (was pending)")

        return await self._metadata.get_task(task_id)

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
            new_embeddings = create_embeddings(validated)
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
            with self._vector_store_lock:
                self._vector_stores.clear()
                self._record_managers.clear()
        elif old_use_gpu != self._config.use_gpu and self._config.embedding_provider == "fastembed":
            # GPU preference changed — fastembed manages acceleration internally,
            # but recreate to pick up any config change
            self._default_embeddings = create_embeddings(self._config)
            logger.info("Embeddings recreated after config change")

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

    def get_knowledge_config(self) -> dict[str, Any]:
        return self._config.to_dict()

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

    async def health(self, collection: str | None = None) -> dict[str, Any]:
        h: dict[str, Any] = {
            "status": "healthy",
            "engine": f"knowledge-{self._knowledge_vector_backend()}",
            "settings": self.get_settings()["knowledge"],
            "embeddings_initialized": self._default_embeddings is not None,
            "reindex_in_progress": list(self._reindex_in_progress),
            "stale": False,
            "reindex_deferred": False,
        }
        if collection:
            await self._ensure_metadata_ready()
            import re as _re

            _has_hash = bool(_re.search(r"_[0-9a-f]{12}$", collection))
            if not _has_hash:
                pinned = await self._metadata.get_collection_config(collection)
                if pinned and (
                    pinned.get("embedding_provider") != self._config.embedding_provider
                    or pinned.get("embedding_model") != self._config.embedding_model
                ):
                    h["stale"] = True
            if collection in self._reindex_deferred:
                h["reindex_deferred"] = True
        return h

    # --- Collection lifecycle ---

    async def drop_collection(self, collection: str) -> None:
        """Drop a collection and all its data."""
        await self._ensure_metadata_ready()
        collection = _sanitize_collection(collection)

        with self._vector_store_lock:
            adapter = self._vector_stores.pop(collection, None)
            self._record_managers.pop(collection, None)
            if adapter:
                try:
                    adapter.drop()
                except Exception as e:
                    logger.debug(f"Drop collection {collection}: {e}")

        files_dir = self._files_dir / collection
        if files_dir.exists():
            shutil.rmtree(files_dir)

        await self._metadata.delete_collection_metadata(collection)
        logger.info(f"Dropped collection {collection}")

    async def drop_collection_vectors(self, collection: str) -> None:
        """Drop vectors and metadata but preserve source files for re-indexing."""
        await self._ensure_metadata_ready()
        collection = _sanitize_collection(collection)
        embeddings = await self._resolve_embeddings_for_collection(collection)
        with self._vector_store_lock:
            adapter = self._vector_stores.pop(collection, None)
            self._record_managers.pop(collection, None)
            if adapter:
                try:
                    adapter.drop()
                except Exception as e:
                    logger.debug(f"Drop collection vectors {collection}: {e}")
            else:
                try:
                    temp = self._create_vector_adapter(collection, embeddings)
                    temp.drop()
                except Exception as e:
                    logger.debug(f"Drop uncached collection {collection}: {e}")
        await self._metadata.delete_collection_metadata(collection)
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
        await self._ensure_metadata_ready()
        collection = _sanitize_collection(collection)
        files_dir = self._files_dir / collection
        if not files_dir.exists():
            return {"status": "no_documents", "count": 0}

        file_list = [f for f in files_dir.iterdir() if f.is_file()]
        if not file_list:
            return {"status": "no_documents", "count": 0}

        task_ids: list[str] = []
        try:
            lock = self._get_collection_lock(collection)
            async with lock:
                pending = [
                    t
                    for t in await self._metadata.list_tasks(collection)
                    if t["status"] in ("pending", "running")
                ]
                if pending:
                    raise ReindexBusyError(len(pending))
                self._reindex_in_progress.add(collection)
                await self.drop_collection_vectors(collection)

            for file_path in file_list:
                task_info = await self._create_reindex_task_entry(collection, file_path.name)
                task_ids.append(task_info["task_id"])

            # Phase 3: Sequential background worker. Clears flags on completion.
            async def _reindex_worker():
                try:
                    for fp, tid in zip(file_list, task_ids):
                        await self._run_ingest(
                            collection,
                            fp,
                            fp.name,
                            tid,
                            replace_duplicates=True,
                            skip_file_copy=True,
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
                    await self._metadata.update_task(tid, status="failed", file_tasks={})
                except Exception:
                    pass
            raise

        return {"status": "started", "count": len(file_list), "task_ids": task_ids}

    # --- Document loading ---

    _DOCLING_FORMATS = {
        ".pdf",
        ".docx",
        ".pptx",
        ".xlsx",
        ".html",
        ".htm",
        ".md",
        ".csv",
        ".asciidoc",
        ".adoc",
        ".tex",
        ".latex",
        ".png",
        ".jpg",
        ".jpeg",
        ".tiff",
        ".bmp",
        ".webp",
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

        When embeddings use fastembed, the chunker tokenizer uses the same ONNX
        tokenizer (avoids downloading sentence-transformers/all-MiniLM-L6-v2).
        """
        try:
            from docling_core.transforms.chunker import HybridChunker

            if self._config.embedding_provider == "fastembed":
                self._ensure_embeddings()
                emb = self._default_embeddings
                if isinstance(emb, _FastEmbedEmbeddings):
                    seq = _fastembed_docling_seq_limit(self._config.embedding_model or "")
                    cap = min(chunk_size, seq)
                    fe = emb._model
                    tok = _fastembed_docling_tokenizer_cls()(
                        text_embedding=fe,
                        max_tokens=cap,
                    )
                    mn = getattr(fe, "model_name", None)
                    logger.debug(
                        "HybridChunker tokenizer: fastembed ONNX (same model as embeddings) "
                        "(model_name={!r}, chunk_token_limit={}, model_seq_limit={})",
                        mn,
                        cap,
                        seq,
                    )
                    return HybridChunker(tokenizer=tok)
                logger.debug(
                    "HybridChunker tokenizer: expected _FastEmbedEmbeddings for provider=fastembed, "
                    "got {}; using Docling default (HuggingFace MiniLM tokenizer)",
                    type(emb).__name__,
                )
            else:
                logger.debug(
                    "HybridChunker tokenizer: Docling default HuggingFace "
                    "(sentence-transformers/all-MiniLM-L6-v2); embedding_provider={!r}",
                    self._config.embedding_provider,
                )

            return HybridChunker(max_tokens=chunk_size)
        except Exception as e:
            logger.warning(f"HybridChunker init failed, falling back to default: {e}")
            return None

    def _get_docling_converter(self):
        """Get or create a reusable Docling DocumentConverter (loads weights once)."""
        if self._docling_converter is None:
            import os
            from docling.document_converter import DocumentConverter, PdfFormatOption
            from docling.datamodel.base_models import InputFormat
            from docling.datamodel.pipeline_options import PdfPipelineOptions

            from pathlib import Path

            artifacts_path_str = os.environ.get("DOCLING_ARTIFACTS_PATH")
            artifacts_path = Path(artifacts_path_str) if artifacts_path_str else None
            pipeline_options = PdfPipelineOptions(artifacts_path=artifacts_path)
            self._docling_converter = DocumentConverter(
                format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options)}
            )
            logger.info("Docling DocumentConverter initialized (weights loaded)")
        return self._docling_converter

    def _load_document(self, file_path: Path) -> list[Document]:
        """Load a document using Docling for supported formats, fallback for plain text."""
        suffix = file_path.suffix.lower()
        logger.info(
            f"Loading document: {file_path.name} (suffix={suffix}, size={file_path.stat().st_size} bytes)"
        )

        chunk_size, chunk_overlap = self._get_effective_chunk_settings()

        if suffix in self._DOCLING_FORMATS:
            try:
                from langchain_docling.loader import ExportType

                chunker = self._build_docling_chunker(chunk_size)
                loader_kwargs: dict = {
                    "file_path": str(file_path),
                    "export_type": ExportType.DOC_CHUNKS,
                    "converter": self._get_docling_converter(),
                }
                if chunker is not None:
                    loader_kwargs["chunker"] = chunker
                loader = DoclingLoader(**loader_kwargs)
                docs = loader.load()
            except Exception as e:
                translated = _translate_document_load_error(file_path, e)
                logger.error(
                    f"Docling failed to parse {file_path.name}: {type(translated).__name__}: {translated}"
                )
                raise translated from e
        elif suffix in (
            ".txt",
            ".text",
            ".log",
            ".json",
            ".xml",
            ".yaml",
            ".yml",
            ".toml",
            ".ini",
            ".cfg",
            ".conf",
        ):
            text = file_path.read_text(errors="replace")
            splitter = RecursiveCharacterTextSplitter(
                chunk_size=chunk_size,
                chunk_overlap=chunk_overlap,
            )
            chunks = splitter.split_text(text)
            docs = [Document(page_content=chunk, metadata={"page": i + 1}) for i, chunk in enumerate(chunks)]
        else:
            try:
                from langchain_docling.loader import ExportType

                chunker = self._build_docling_chunker(chunk_size)
                loader_kwargs = {
                    "file_path": str(file_path),
                    "export_type": ExportType.DOC_CHUNKS,
                    "converter": self._get_docling_converter(),
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
            if any(
                [
                    addr.is_private,
                    addr.is_loopback,
                    addr.is_link_local,
                    addr.is_reserved,
                    addr.is_multicast,
                    addr.is_unspecified,
                ]
            ):
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
