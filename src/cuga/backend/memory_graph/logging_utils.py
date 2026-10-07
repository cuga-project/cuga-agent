from __future__ import annotations

import os


def memory_graph_trace_enabled() -> bool:
    """Return True only when verbose pair/node-level memory-graph tracing is requested."""
    return os.getenv("CUGA_MEMORY_GRAPH_TRACE", "").strip().casefold() in {
        "1",
        "true",
        "yes",
        "on",
    }
