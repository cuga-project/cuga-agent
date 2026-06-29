"""Retrieval-time observability sink — append-only JSONL of query traces.

Default behaviour: **fully off**. ``emit()`` is a no-op unless either

  1. the env vars ``KNOWLEDGE_TRACE=1`` AND ``KNOWLEDGE_TRACE_FILE=<path>``
     are both set, OR
  2. a caller has entered the ``capture_trace(path)`` context manager.

When active, every ``emit()`` appends a single JSON line to the configured
sink file. JSON-serialisable Python dicts only — callers should serialise
their own dataclasses to dicts before emitting.

Thread-safe: a single module-level lock serialises writes so concurrent
``KnowledgeClient.search`` calls produce well-formed JSONL.

PII discipline: the sink path defaults to ``None`` and writes nothing to
stdout/stderr; operators opt in explicitly per run.

Used by:

- ``query_expansion.py``          Which aliases fired or were dropped per query
- ``engine.search_with_stats``    Retrieval candidates, fusion ranks
- ``reranker.py``                 Rerank score deltas
"""

from __future__ import annotations

import json
import logging
import os
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

logger = logging.getLogger("cuga.knowledge.trace")

# Schema version. Bump when emit() field set changes meaningfully.
# Downstream readers should refuse to consume records with a higher
# _schema_version than they understand.
TRACE_SCHEMA_VERSION = 1


class _TraceSink:
    """Thread-safe JSONL appender. Module-level singleton (see _sink below).

    The sink has two activation paths — env vars (process-wide, opt-in
    for long-running deployments) and the ``capture_trace`` context
    manager (per-call, mainly for tests + the pipeline runner). Both
    paths share the same file lock so concurrent emits stay correct.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._path: Path | None = None
        self._enabled = False
        # Override path/enabled state used by capture_trace() — takes
        # precedence over env vars while set.
        self._override_path: Path | None = None
        self._override_enabled = False

    def _resolve_active_path(self) -> Path | None:
        """Return the active sink file path, or None if disabled.

        Context manager override beats env-var configuration. This lets
        tests run with capture_trace() even if the developer's shell has
        KNOWLEDGE_TRACE set.
        """
        if self._override_enabled:
            return self._override_path
        if not self._enabled:
            # Lazy env-var check — re-read every call so unit tests can
            # patch os.environ mid-test without a sink reset.
            if os.environ.get("KNOWLEDGE_TRACE") == "1":
                env_path = os.environ.get("KNOWLEDGE_TRACE_FILE")
                if env_path:
                    self._enabled = True
                    self._path = Path(env_path)
                    return self._path
            return None
        return self._path

    def emit(self, record: dict[str, Any]) -> None:
        """Append ``record`` as one JSON line to the sink. No-op if disabled.

        Adds ``_schema_version`` to the record if not already present. Logs
        and swallows IOErrors — a failing trace must never crash the
        production search path.
        """
        path = self._resolve_active_path()
        if path is None:
            return

        record = dict(record)  # defensive copy
        record.setdefault("_schema_version", TRACE_SCHEMA_VERSION)

        try:
            line = json.dumps(record, ensure_ascii=False)
        except (TypeError, ValueError) as exc:
            logger.warning(f"trace serialization failed: {exc}; record dropped")
            return

        with self._lock:
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open("a", encoding="utf-8") as f:
                    f.write(line + "\n")
            except OSError as exc:
                # File-system pressure / permissions — log once, never crash.
                logger.warning(f"trace write failed for {path}: {exc}")

    def _enter_override(self, path: Path) -> None:
        self._override_path = path
        self._override_enabled = True

    def _exit_override(self) -> None:
        self._override_path = None
        self._override_enabled = False


# Module-level singleton. Stays cheap when disabled — a single bool check
# per call.
_sink = _TraceSink()


def emit(record: dict[str, Any]) -> None:
    """Public emit. Adds ``_schema_version`` if absent. Disabled by default.

    Callers that want to be cheap on the hot path can ``if is_enabled():``
    first to avoid building a large record dict only to drop it.
    """
    _sink.emit(record)


def is_enabled() -> bool:
    """True iff the sink is currently configured to write somewhere.

    Useful for callers that compose expensive trace records — call this
    first and skip the record build when off.
    """
    return _sink._resolve_active_path() is not None


@contextmanager
def capture_trace(path: Path | str) -> Iterator[Path]:
    """Within the block, emit() appends to ``path``. Restores prior state on exit.

    Used by the pipeline's stage 04/05 to scope traces to a single run
    without setting env vars. Tests use this to verify emit behaviour.

    ``path`` is created (with parent dirs) on first emit, not on enter —
    a block that never emits leaves no file behind.
    """
    p = Path(path)
    _sink._enter_override(p)
    try:
        yield p
    finally:
        _sink._exit_override()


def reset_for_tests() -> None:
    """Reset module state. ONLY for use in pytest fixtures.

    Env-var-driven enable state is re-read on next emit() so the only
    state that needs clearing is the override path."""
    _sink._exit_override()
    _sink._enabled = False
    _sink._path = None
