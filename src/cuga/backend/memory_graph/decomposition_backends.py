"""Optional local backends for linguistic and DeDisCo decomposition."""

from __future__ import annotations


def mlx_backend():
    """Load the Apple-only DeDisCo backend when classification is needed."""
    try:
        from mlx_lm import generate, load
    except ImportError as exc:
        raise RuntimeError(
            "DeDisCo requires mlx-lm on an Apple Silicon host; install the "
            "memory-graph runtime dependencies or disable DeDisCo."
        ) from exc
    try:
        from mlx_lm import batch_generate
    except ImportError:  # Older mlx-lm; retain the serial fallback.
        batch_generate = None
    return generate, load, batch_generate


def stanza_module():
    """Load Stanza only when graph decomposition needs its pipeline."""
    try:
        import stanza
    except ImportError as exc:
        raise RuntimeError("Stanza is required for memory-graph linguistic decomposition.") from exc
    return stanza
