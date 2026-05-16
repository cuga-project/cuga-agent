"""Unknown slash-command resolver.

When a user types a slash command that does not match any registered
command (e.g. ``/sumarize``), this module suggests the most likely intended
commands using embedding cosine similarity. It is a *suggester*, never an
auto-corrector: it returns ranked candidates and lets the caller decide
what to do with them.

Design
------
``CommandResolver`` is a pure, dependency-injected unit:

* ``embed_fn`` — an async callable turning a string into a vector.
* ``store`` — an :class:`EmbeddingStoreBackend` used as the persistent
  vector cache (commands are written there during :meth:`index`).

Step 3 of :meth:`resolve` needs the indexed command vectors back in order
to rank them. ``LocalEmbeddingStore.list`` only selects the id + metadata
columns — it does **not** return the ``embedding`` column — so we cannot
recover vectors from ``store.list``. Therefore :meth:`index` also keeps an
in-memory copy of the ``(name, embedding, metadata)`` tuples it embedded,
and :meth:`resolve` ranks from that copy. This keeps ranking independent of
the backend's projection quirks and avoids a second round-trip; the store
write is retained so the vectors are still cached/persisted for other
consumers.

The module has no FastAPI, settings, or global-state dependencies.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable, Dict, List, Sequence, Tuple

import numpy as np

from cuga.backend.slash_commands.types import CommandRef
from cuga.backend.storage.embedding.base import EmbeddingStoreBackend


@dataclass(frozen=True)
class CommandSuggestion:
    """A single ranked suggestion for an unknown slash command."""

    name: str
    kind: str
    description: str
    score: float


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    """Cosine similarity between two vectors; 0.0 for any zero-norm input."""
    va = np.asarray(a, dtype=float)
    vb = np.asarray(b, dtype=float)
    na = float(np.linalg.norm(va))
    nb = float(np.linalg.norm(vb))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return float(np.dot(va, vb) / (na * nb))


class CommandResolver:
    """Suggests likely-intended commands for an unknown slash command."""

    def __init__(
        self,
        store: EmbeddingStoreBackend,
        embed_fn: Callable[[str], Awaitable[List[float]]],
    ) -> None:
        self._store = store
        self._embed_fn = embed_fn
        # In-memory copy captured during index(): name -> (embedding, metadata).
        # See module docstring for why store.list cannot replace this.
        self._index: Dict[str, Tuple[List[float], Dict[str, str]]] = {}

    async def index(self, commands: Sequence[CommandRef]) -> None:
        """Embed each command and write it to the store + in-memory index."""
        # Drop stale entries from previous indexings so commands removed
        # from disk don't keep surfacing in suggestions.
        self._index.clear()
        for cmd in commands:
            embedding = await self._embed_fn(f"{cmd.name}: {cmd.description}")
            metadata = {
                "name": cmd.name,
                "kind": cmd.kind,
                "description": cmd.description,
            }
            await self._store.add(id=cmd.name, embedding=embedding, metadata=metadata)
            self._index[cmd.name] = (embedding, metadata)

    async def resolve(
        self,
        raw_name: str,
        *,
        limit: int = 3,
        threshold: float = 0.0,
    ) -> List[CommandSuggestion]:
        """Return up to ``limit`` ranked suggestions for ``raw_name``.

        Steps:
          1. Exact-match short-circuit (case-insensitive, stripped) -> score 1.0.
          2. Embed ``raw_name``.
          3. Rank all indexed commands by cosine similarity to the query.
          4. Drop the input itself, drop anything below ``threshold``,
             return the top ``limit``.
        """
        normalized = raw_name.strip()

        if not self._index:
            return []

        # 1. Exact-match short-circuit.
        lowered = normalized.lower()
        for name, (_embedding, metadata) in self._index.items():
            if name.lower() == lowered:
                return [
                    CommandSuggestion(
                        name=metadata["name"],
                        kind=metadata["kind"],
                        description=metadata["description"],
                        score=1.0,
                    )
                ]

        # 2. Embed the query.
        query_embedding = await self._embed_fn(normalized)

        # 3. Cosine-rank every indexed command.
        scored: List[CommandSuggestion] = []
        for name, (embedding, metadata) in self._index.items():
            # Never return the input itself. Compare case-insensitively to
            # match the exact-match short-circuit above.
            if name.lower() == lowered:
                continue
            score = _cosine(query_embedding, embedding)
            if score < threshold:
                continue
            scored.append(
                CommandSuggestion(
                    name=metadata["name"],
                    kind=metadata["kind"],
                    description=metadata["description"],
                    score=score,
                )
            )

        # 4. Rank descending, return top ``limit``.
        scored.sort(key=lambda s: s.score, reverse=True)
        return scored[:limit]
