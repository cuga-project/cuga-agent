from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

from cuga.backend.memory_graph import MemoryEdge, MemoryGraph, MemoryNode


GRAPH_SERIALIZATION_FORMAT_VERSION = 1
MEMORY_GRAPHS_INDEX_FORMAT_VERSION = 1
DEFAULT_MEMORY_GRAPHS_FILE = Path("memory_graphs.json")


class GraphSerializationError(ValueError):
    """Raised when a serialized graph file cannot be reconstructed safely."""


def compute_prompt_hash(raw_prompt: str) -> str:
    """Return the stable SHA-256 identifier for a raw prompt.

    The prompt is stripped only at its outer boundaries before hashing. Internal
    whitespace and formatting are preserved so materially different prompts do
    not accidentally share the same identifier.
    """
    if not isinstance(raw_prompt, str):
        raise TypeError(
            "compute_prompt_hash expects a string, "
            f"got {type(raw_prompt).__name__}"
        )

    normalized = raw_prompt.strip()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _normalize_graph_type(graph_type: str) -> str:
    if not isinstance(graph_type, str):
        raise TypeError(
            "graph_type must be a string, "
            f"got {type(graph_type).__name__}"
        )

    normalized = graph_type.strip()
    if not normalized:
        raise ValueError("graph_type must not be empty.")

    return normalized


def serialize_graph(graph: MemoryGraph) -> dict[str, Any]:
    """Serialize a ``MemoryGraph`` into a JSON-compatible dictionary.

    The payload deliberately stores only persistent graph state: nodes and
    edges. ``MemoryGraph``'s internal incoming/outgoing indexes are derived state
    and are reconstructed automatically by ``MemoryGraph.add_edge`` when the
    graph is loaded.

    Pydantic's ``mode='json'`` is used so enums and datetimes are converted to
    their JSON representations while preserving every field defined on
    ``MemoryNode`` and ``MemoryEdge``.
    """
    if not isinstance(graph, MemoryGraph):
        raise TypeError(
            "serialize_graph expects a MemoryGraph, "
            f"got {type(graph).__name__}"
        )

    return {
        "format_version": GRAPH_SERIALIZATION_FORMAT_VERSION,
        "graph": {
            "nodes": [
                graph.nodes[node_id].model_dump(mode="json")
                for node_id in sorted(graph.nodes)
            ],
            "edges": [
                graph.edges[edge_id].model_dump(mode="json")
                for edge_id in sorted(graph.edges)
            ],
        },
    }


def _memory_graphs_index_path(
    memory_graphs_file: str | Path,
) -> Path:
    """Return a stable absolute path for the memory-graphs index file."""
    return Path(memory_graphs_file).expanduser().resolve(strict=False)


def _graph_path_for_index(
    graph_file: str | Path,
    memory_graphs_file: str | Path,
) -> str:
    """Store a graph path relative to the directory containing the index.

    Relative paths keep the cache portable when the whole cache directory is
    moved. ``..`` segments are allowed when the graph lives outside the index
    directory.
    """
    graph_path = Path(graph_file).expanduser().resolve(strict=False)
    index_path = _memory_graphs_index_path(memory_graphs_file)
    relative_path = os.path.relpath(graph_path, start=index_path.parent)
    return Path(relative_path).as_posix()


def _resolve_graph_path(
    graph_file: str | Path,
    memory_graphs_file: str | Path,
) -> Path:
    """Resolve an indexed graph path relative to the index file directory."""
    path = Path(graph_file).expanduser()
    if path.is_absolute():
        return path.resolve(strict=False)

    index_path = _memory_graphs_index_path(memory_graphs_file)
    return (index_path.parent / path).resolve(strict=False)


def _load_memory_graphs_index(
    memory_graphs_file: str | Path,
) -> dict[str, Any]:
    """Load the graph index, returning an empty index when it does not exist."""
    path = _memory_graphs_index_path(memory_graphs_file)

    if not path.exists():
        return {
            "format_version": MEMORY_GRAPHS_INDEX_FORMAT_VERSION,
            "graphs": [],
        }

    try:
        with path.open("r", encoding="utf-8") as file:
            payload = json.load(file)
    except json.JSONDecodeError as exc:
        raise GraphSerializationError(
            f"Memory-graph index is not valid JSON: {path}"
        ) from exc

    if not isinstance(payload, dict):
        raise GraphSerializationError(
            "Memory-graph index payload must be a JSON object."
        )

    format_version = payload.get("format_version")
    if format_version != MEMORY_GRAPHS_INDEX_FORMAT_VERSION:
        raise GraphSerializationError(
            "Unsupported memory-graph index format version: "
            f"{format_version!r}. Expected "
            f"{MEMORY_GRAPHS_INDEX_FORMAT_VERSION}."
        )

    graphs = payload.get("graphs")
    if not isinstance(graphs, list):
        raise GraphSerializationError(
            "Memory-graph index field 'graphs' must be a list."
        )

    return payload


def _write_memory_graphs_index(
    payload: dict[str, Any],
    memory_graphs_file: str | Path,
) -> Path:
    path = _memory_graphs_index_path(memory_graphs_file)
    path.parent.mkdir(parents=True, exist_ok=True)

    with path.open("w", encoding="utf-8") as file:
        json.dump(
            payload,
            file,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        file.write("\n")

    return path


def _register_graph_file(
    *,
    graph_file: str | Path,
    raw_prompt: str,
    graph_type: str,
    memory_graphs_file: str | Path,
) -> None:
    """Register a saved graph under ``(graph_type, prompt_hash)``.

    The stored graph path is relative to the directory containing the index.
    If the same graph type and prompt hash are already present, that entry is
    updated to point at the newly saved graph file instead of adding a duplicate.
    """
    prompt_hash = compute_prompt_hash(raw_prompt)
    normalized_graph_type = _normalize_graph_type(graph_type)
    graph_path = _graph_path_for_index(graph_file, memory_graphs_file)

    index_payload = _load_memory_graphs_index(memory_graphs_file)
    entries = index_payload["graphs"]

    for entry in entries:
        if not isinstance(entry, dict):
            raise GraphSerializationError(
                "Every item in memory-graph index 'graphs' must be an object."
            )

        if (
            entry.get("prompt_hash") == prompt_hash
            and entry.get("graph_type") == normalized_graph_type
        ):
            entry["graph_file"] = graph_path
            entry["graph_type"] = normalized_graph_type
            _write_memory_graphs_index(index_payload, memory_graphs_file)
            return

    entries.append(
        {
            "prompt_hash": prompt_hash,
            "graph_type": normalized_graph_type,
            "graph_file": graph_path,
        }
    )
    _write_memory_graphs_index(index_payload, memory_graphs_file)


def save_graph(
    graph: MemoryGraph,
    file_path: str | Path,
    *,
    raw_prompt: str,
    graph_type: str,
    memory_graphs_file: str | Path = DEFAULT_MEMORY_GRAPHS_FILE,
) -> Path:
    """Serialize ``graph``, save it, and register it in the graph index.

    The index entry is identified by both ``graph_type`` and the SHA-256 hash of
    ``raw_prompt``. If that pair already exists, its ``graph_file`` value is
    updated to point at this newly written graph file.

    The graph path stored in the index is relative to the directory containing
    ``memory_graphs_file`` so later loading does not depend on the process's
    current working directory.
    """
    _normalize_graph_type(graph_type)

    path = Path(file_path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)

    payload = serialize_graph(graph)

    with path.open("w", encoding="utf-8") as file:
        json.dump(
            payload,
            file,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        file.write("\n")

    _register_graph_file(
        graph_file=path,
        raw_prompt=raw_prompt,
        graph_type=graph_type,
        memory_graphs_file=memory_graphs_file,
    )

    return path


def find_graph_file(
    raw_prompt: str,
    *,
    graph_type: str,
    memory_graphs_file: str | Path = DEFAULT_MEMORY_GRAPHS_FILE,
) -> str | None:
    """Return the indexed graph path for ``raw_prompt`` and ``graph_type``.

    Lookup filters entries by both the prompt hash and graph type. The returned
    value is the path string exactly as stored in the index, which is normally
    relative to the directory containing ``memory_graphs_file``.

    Returns:
        The matching indexed graph path, or ``None`` when no matching entry is
        registered. A missing index file is treated as an empty index.
    """
    prompt_hash = compute_prompt_hash(raw_prompt)
    normalized_graph_type = _normalize_graph_type(graph_type)
    index_payload = _load_memory_graphs_index(memory_graphs_file)

    for entry in index_payload["graphs"]:
        if not isinstance(entry, dict):
            raise GraphSerializationError(
                "Every item in memory-graph index 'graphs' must be an object."
            )

        if entry.get("prompt_hash") != prompt_hash:
            continue
        if entry.get("graph_type") != normalized_graph_type:
            continue

        graph_file = entry.get("graph_file")
        if not isinstance(graph_file, str) or not graph_file.strip():
            raise GraphSerializationError(
                "Matching memory-graph index entry has no valid 'graph_file'."
            )

        return graph_file

    return None


def deserialize_graph(payload: dict[str, Any]) -> MemoryGraph:
    """Reconstruct a ``MemoryGraph`` from a serialized graph dictionary."""
    if not isinstance(payload, dict):
        raise GraphSerializationError(
            "Serialized graph payload must be a JSON object."
        )

    format_version = payload.get("format_version")
    if format_version != GRAPH_SERIALIZATION_FORMAT_VERSION:
        raise GraphSerializationError(
            "Unsupported graph serialization format version: "
            f"{format_version!r}. Expected "
            f"{GRAPH_SERIALIZATION_FORMAT_VERSION}."
        )

    graph_payload = payload.get("graph")
    if not isinstance(graph_payload, dict):
        raise GraphSerializationError(
            "Serialized graph payload is missing the 'graph' object."
        )

    raw_nodes = graph_payload.get("nodes")
    raw_edges = graph_payload.get("edges")

    if not isinstance(raw_nodes, list):
        raise GraphSerializationError(
            "Serialized graph field 'graph.nodes' must be a list."
        )
    if not isinstance(raw_edges, list):
        raise GraphSerializationError(
            "Serialized graph field 'graph.edges' must be a list."
        )

    graph = MemoryGraph()

    try:
        for raw_node in raw_nodes:
            node = MemoryNode.model_validate(raw_node)
            graph.add_node(node)

        for raw_edge in raw_edges:
            edge = MemoryEdge.model_validate(raw_edge)
            graph.add_edge(edge)
    except Exception as exc:
        raise GraphSerializationError(
            "Failed to reconstruct MemoryGraph from serialized payload: "
            f"{type(exc).__name__}: {exc}"
        ) from exc

    return graph


def load_graph(file_path: str | Path) -> MemoryGraph:
    """Read a serialized graph JSON file and reconstruct its ``MemoryGraph``."""
    path = Path(file_path).expanduser()

    try:
        with path.open("r", encoding="utf-8") as file:
            payload = json.load(file)
    except json.JSONDecodeError as exc:
        raise GraphSerializationError(
            f"Graph file is not valid JSON: {path}"
        ) from exc

    return deserialize_graph(payload)


def load_graph_for_prompt(
    raw_prompt: str,
    *,
    graph_type: str,
    memory_graphs_file: str | Path = DEFAULT_MEMORY_GRAPHS_FILE,
) -> MemoryGraph | None:
    """Load the cached graph for a prompt/type pair, or return ``None``.

    This is the fail-soft cache-read wrapper intended for agent initialization.
    It covers the complete read pipeline:

    1. hash the raw prompt;
    2. read and validate the memory-graphs index;
    3. find an entry matching both prompt hash and graph type;
    4. resolve its graph path relative to the index file;
    5. verify that the graph file exists and is a regular file;
    6. read, deserialize, and reconstruct the ``MemoryGraph``.

    If any step fails for any reason, the function returns ``None`` so callers
    can treat the situation as a cache miss and rebuild the graph from source.
    """
    try:
        graph_file = find_graph_file(
            raw_prompt,
            graph_type=graph_type,
            memory_graphs_file=memory_graphs_file,
        )
        if graph_file is None:
            return None

        graph_path = _resolve_graph_path(
            graph_file,
            memory_graphs_file,
        )
        if not graph_path.exists() or not graph_path.is_file():
            return None

        return load_graph(graph_path)
    except Exception:
        return None


__all__ = [
    "DEFAULT_MEMORY_GRAPHS_FILE",
    "GRAPH_SERIALIZATION_FORMAT_VERSION",
    "MEMORY_GRAPHS_INDEX_FORMAT_VERSION",
    "GraphSerializationError",
    "compute_prompt_hash",
    "deserialize_graph",
    "find_graph_file",
    "load_graph",
    "load_graph_for_prompt",
    "save_graph",
    "serialize_graph",
]