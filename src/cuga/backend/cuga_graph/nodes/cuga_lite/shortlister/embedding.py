"""Cosine-similarity shortlister — ranks tools without calling an LLM.

Compares one vector built from the user's question against one vector per tool
(name + description + parameter names + return field names; see ``doc.py``).

Three properties worth knowing before changing anything here:

* **It is a recall device, not a precision device.** ``get_contacts`` (list) and
  ``get_contact`` (by id) differ by one character and embed almost identically.
  Use it to cut a large catalogue down for an LLM to finish (``hybrid``), or
  where only "don't drop the needed tool" matters (the bind-time cap). Issue
  #150 documented exactly this failure class for the LLM shortlister.
* **A query must never wait on a model download.** Mirrors the contract in
  ``knowledge/reranker.py``: if the backend is not ready we start a background
  load and raise :class:`ShortlisterUnavailableError` so the caller degrades to
  the LLM strategy for that one call.
* **Query and task context are blended as vectors, not strings.** Concatenating
  them lets a long first message drown a short per-step query, which would make
  every step of a multi-step task retrieve the same tools.

Local (``provider = "local"``, the default) runs entirely offline via fastembed,
reusing the same session knowledge and policy already load — no second ONNX
session, and nothing extra for airgapped preload to fetch. ``provider =
"openai"`` is available when a deployment explicitly wants it, and trades those
properties for a hosted model. This section deliberately does not inherit
``[storage.embedding]``, which a deployment may have pointed at OpenAI.
"""

from __future__ import annotations

import asyncio
import os
import threading
import time
from typing import Any, ClassVar, Dict, List, Optional, Sequence, Tuple

import numpy as np
from loguru import logger

from cuga.backend.cuga_graph.nodes.cuga_lite.shortlister.base import (
    ShortlistCandidate,
    ShortlistRequest,
    ShortlistResult,
    ShortlisterUnavailableError,
)
from cuga.backend.cuga_graph.nodes.cuga_lite.shortlister.doc import (
    app_name_for_tool,
    tool_document,
    tool_fingerprint,
)

#: Ready backends, keyed by ``provider:model``. Weights load once per process.
_MODELS: Dict[str, Any] = {}
_LOADING: set = set()
_RETRY_AFTER: Dict[str, float] = {}
_LOCK = threading.Lock()

#: Normalized document vectors, keyed by content fingerprint.
_VECTORS: Dict[str, np.ndarray] = {}

#: After a failed load (offline / not cached / missing API key) wait this long
#: before retrying, so a broken deploy does not hammer the network or spam logs.
_RETRY_COOLDOWN_S = 30.0

#: Never return nothing: an empty result is a dead end for the agent and the
#: bind-time cap raises on it.
_MIN_FALLBACK_RESULTS = 3

DEFAULT_PROVIDER = "local"


def backend_key(provider: str, model_name: str) -> str:
    return f"{provider or DEFAULT_PROVIDER}:{model_name}"


def _is_asymmetric(model_name: str) -> bool:
    """Whether the model wants distinct query/passage encodings.

    ``BAAI/bge-*`` (our default) is trained with a query-side instruction
    prefix, so queries and documents are encoded differently. MiniLM-class
    models are symmetric, and applying bge's prefix to one *degrades* results.
    Default to symmetric so an unknown model behaves sanely rather than oddly.
    """
    lowered = (model_name or "").lower()
    return "bge" in lowered and "reranker" not in lowered


class _LocalBackend:
    """fastembed, on-device. No network at query time, nothing billed.

    Uses the process-wide session from ``embedding_service`` so the shortlister
    shares weights with knowledge and policy rather than loading a second copy.
    """

    def __init__(self, model_name: str) -> None:
        from cuga.backend.storage.embedding.embedding_service import get_shared_text_embedding

        self._model_name = model_name
        self._asymmetric = _is_asymmetric(model_name)
        # Shared with knowledge and policy: one ONNX session per model for the
        # whole process, and one entry in the airgapped preload list.
        self._model = get_shared_text_embedding(model_name)

    def _embed_sync(self, texts: Sequence[str], *, as_query: bool) -> np.ndarray:
        if self._asymmetric:
            method = self._model.query_embed if as_query else self._model.passage_embed
        else:
            method = self._model.embed
        return np.array([np.asarray(v, dtype=np.float32) for v in method(list(texts))], dtype=np.float32)

    async def aembed(self, texts: Sequence[str], *, as_query: bool) -> np.ndarray:
        # fastembed is synchronous and CPU-bound; keep it off the event loop.
        return await asyncio.to_thread(self._embed_sync, texts, as_query=as_query)


class _OpenAIBackend:
    """Hosted embeddings. Opt-in — this makes shortlisting a billed network call.

    ``OpenAIEmbeddings`` already distinguishes documents from queries and
    batches natively, so no asymmetry heuristic is needed here.
    """

    def __init__(self, model_name: str) -> None:
        from langchain_openai import OpenAIEmbeddings

        from cuga.backend.storage.embedding.embedding_service import get_embedding_config

        cfg = get_embedding_config()
        api_key = cfg.get("api_key") or os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise ShortlisterUnavailableError(
                "shortlister.embedding_provider = 'openai' but no API key found "
                "(set OPENAI_API_KEY or storage.embedding.api_key)"
            )
        kwargs: Dict[str, Any] = {"model": model_name or "text-embedding-3-small", "api_key": api_key}
        if cfg.get("base_url"):
            kwargs["base_url"] = str(cfg["base_url"]).rstrip("/")
        self._embeddings = OpenAIEmbeddings(**kwargs)

    async def aembed(self, texts: Sequence[str], *, as_query: bool) -> np.ndarray:
        items = list(texts)
        if as_query:
            vectors = [await self._embeddings.aembed_query(t) for t in items]
        else:
            vectors = await self._embeddings.aembed_documents(items)
        return np.array([np.asarray(v, dtype=np.float32) for v in vectors], dtype=np.float32)


_BACKENDS = {"local": _LocalBackend, "openai": _OpenAIBackend}


def is_ready(provider: str, model_name: str) -> bool:
    """True if the backend is resident and ranking will not block on a load."""
    return backend_key(provider, model_name) in _MODELS


def _build_backend(provider: str, model_name: str) -> Any:
    name = (provider or DEFAULT_PROVIDER).strip().lower()
    builder = _BACKENDS.get(name)
    if builder is None:
        raise ShortlisterUnavailableError(
            f"unknown shortlister.embedding_provider {provider!r}; "
            f"expected one of {', '.join(sorted(_BACKENDS))}"
        )
    return builder(model_name)


def prewarm(provider: str, model_name: str) -> bool:
    """Build the backend synchronously. Returns True on success."""
    key = backend_key(provider, model_name)
    if key in _MODELS:
        return True
    try:
        backend = _build_backend(provider, model_name)
    except Exception as e:
        with _LOCK:
            _RETRY_AFTER[key] = time.monotonic() + _RETRY_COOLDOWN_S
        logger.warning("Shortlister embedding backend {!r} failed to load: {}", key, e)
        return False
    with _LOCK:
        _MODELS[key] = backend
        _RETRY_AFTER.pop(key, None)
    logger.info("Shortlister embedding backend ready: {}", key)
    return True


def ensure_loading(provider: str, model_name: str) -> None:
    """Kick a background load if one is not already running or cooling down."""
    key = backend_key(provider, model_name)
    with _LOCK:
        if key in _MODELS or key in _LOADING:
            return
        retry_at = _RETRY_AFTER.get(key)
        if retry_at is not None and time.monotonic() < retry_at:
            return
        _LOADING.add(key)

    def _load() -> None:
        try:
            prewarm(provider, model_name)
        finally:
            with _LOCK:
                _LOADING.discard(key)

    threading.Thread(target=_load, name=f"shortlister-embed-load:{key}", daemon=True).start()


def reset_caches() -> None:
    """Clear backend and vector caches. Tests only."""
    with _LOCK:
        _MODELS.clear()
        _LOADING.clear()
        _RETRY_AFTER.clear()
        _VECTORS.clear()


def _normalize(vectors: np.ndarray) -> np.ndarray:
    """L2-normalize row-wise; zero rows stay zero (they score 0, never NaN)."""
    matrix = np.atleast_2d(np.asarray(vectors, dtype=np.float32))
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return matrix / norms


class EmbeddingShortlister:
    """Ranks tools by cosine similarity between query and tool documents."""

    name: ClassVar[str] = "embedding"

    def __init__(
        self,
        model_name: str,
        *,
        provider: str = DEFAULT_PROVIDER,
        query_weight: float = 0.7,
        min_score: float = 0.15,
    ) -> None:
        self._model_name = model_name
        self._provider = (provider or DEFAULT_PROVIDER).strip().lower()
        self._query_weight = min(max(float(query_weight), 0.0), 1.0)
        self._min_score = float(min_score)

    @property
    def _key(self) -> str:
        return backend_key(self._provider, self._model_name)

    # -- backend access ----------------------------------------------------

    def _require_backend(self) -> Any:
        """Return the ready backend, or start loading and signal unavailability."""
        backend = _MODELS.get(self._key)
        if backend is not None:
            return backend
        ensure_loading(self._provider, self._model_name)
        raise ShortlisterUnavailableError(
            f"embedding backend {self._key!r} is not ready yet; loading in the "
            f"background and serving this call with the fallback strategy"
        )

    # -- vectors -----------------------------------------------------------

    async def _document_matrix(self, backend: Any, tools: List[Any]) -> np.ndarray:
        """Normalized document vectors for ``tools``, embedding only cache misses."""
        from cuga.backend.cuga_graph.nodes.cuga_lite.prompt_utils import PromptUtils

        fingerprints: List[str] = []
        missing_texts: List[str] = []
        missing_indexes: List[int] = []

        for index, tool in enumerate(tools):
            try:
                _, response_doc = PromptUtils.get_tool_docs(tool)
            except Exception:
                response_doc = ""
            document = tool_document(tool, app_name_for_tool(tool), response_doc)
            # Keyed on the backend, not just the model: two providers serving the
            # same model name still produce different vector spaces.
            fingerprint = tool_fingerprint(document, self._key)
            fingerprints.append(fingerprint)
            if fingerprint not in _VECTORS:
                missing_texts.append(document)
                missing_indexes.append(index)

        if missing_texts:
            raw = await backend.aembed(missing_texts, as_query=False)
            normalized = _normalize(raw)
            for position, index in enumerate(missing_indexes):
                _VECTORS[fingerprints[index]] = normalized[position]

        return np.vstack([_VECTORS[f] for f in fingerprints])

    async def _query_vector(self, backend: Any, request: ShortlistRequest) -> Optional[np.ndarray]:
        """Blend the step query and task context as weighted unit vectors.

        String concatenation would let a long task context dominate a short step
        query — the weighting would become an accident of relative length.
        """
        step = (request.query or "").strip()
        context = (request.task_context or "").strip()

        texts = [t for t in (step, context) if t]
        if not texts:
            # Not an unavailability: the backend is fine, there is simply
            # nothing to rank against. Raising here would wrongly trigger the
            # fallback strategy, which would receive the same empty query.
            return None

        normalized = _normalize(await backend.aembed(texts, as_query=True))
        if len(texts) == 1:
            return normalized[0]

        alpha = self._query_weight
        blended = alpha * normalized[0] + (1.0 - alpha) * normalized[1]
        return _normalize(blended)[0]

    # -- ranking -----------------------------------------------------------

    def _select(
        self,
        scores: np.ndarray,
        tools: List[Any],
        request: ShortlistRequest,
    ) -> List[Tuple[int, float]]:
        """Apply ``top_k`` / ``min_score`` / ``max_results``, never returning empty."""
        limit = request.top_k if request.top_k else len(tools)
        if request.max_results:
            limit = min(limit, request.max_results)
        limit = max(1, min(limit, len(tools)))

        order = np.argsort(-scores)[:limit]
        kept = [(int(i), float(scores[i])) for i in order if float(scores[i]) >= self._min_score]

        if not kept:
            # Nothing cleared the floor. Returning nothing is worse than
            # returning the best guesses: the agent has no next move, and the
            # bind-time cap raises on an empty ranking.
            fallback = min(_MIN_FALLBACK_RESULTS, limit, len(tools))
            kept = [(int(i), float(scores[i])) for i in order[:fallback]]
        return kept

    async def warm(self, tools: List[Any]) -> int:
        """Load the session and embed ``tools``, returning the number embedded.

        Server mode calls this at startup and whenever the tool catalogue
        changes, so the first user query does not fall back to the LLM. Only
        cache misses cost anything — vectors are keyed by content hash, so a
        re-warm after adding two tools embeds two documents, not the catalogue.

        Blocking here is correct: it runs at boot, not on a query.
        """
        loaded = await asyncio.to_thread(prewarm, self._provider, self._model_name)
        if not loaded or not tools:
            return 0
        before = len(_VECTORS)
        await self._document_matrix(_MODELS[self._key], tools)
        return len(_VECTORS) - before

    async def shortlist(self, request: ShortlistRequest) -> ShortlistResult:
        if not request.tools or (request.top_k is not None and request.top_k <= 0):
            return ShortlistResult()

        backend = self._require_backend()
        query_vector = await self._query_vector(backend, request)
        if query_vector is None:
            return ShortlistResult()
        document_matrix = await self._document_matrix(backend, request.tools)

        scores = document_matrix @ query_vector
        selected = self._select(scores, request.tools, request)

        return ShortlistResult(
            candidates=[
                ShortlistCandidate(
                    name=request.tools[index].name,
                    score=score,
                    reasoning=f"Cosine similarity {score:.3f} to the query.",
                )
                for index, score in selected
            ]
        )
