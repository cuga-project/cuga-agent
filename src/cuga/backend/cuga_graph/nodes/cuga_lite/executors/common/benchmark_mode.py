from loguru import logger

from cuga.config import settings

_warned_relaxed = False


def is_benchmark_mode() -> bool:
    """Check if benchmark mode is enabled (non-default benchmark setting).

    Returns:
        True if benchmark mode is enabled, False otherwise
    """
    return settings.advanced_features.benchmark != "default"


def is_relaxed_execution() -> bool:
    """Relaxed execution, only when the operator sets ``execution.enforce_code_security = false``.

    Neither Skills nor a benchmark profile implies it (GHSA-4rj2): both used to
    silently disable every validator and inject real ``os``/``sys`` for the whole run.
    """
    return not settings.execution.enforce_code_security


def warn_if_relaxed() -> None:
    """Emit one loud warning per process when code security is relaxed (GHSA-4rj2 / L-2)."""
    global _warned_relaxed
    if is_relaxed_execution() and not _warned_relaxed:
        _warned_relaxed = True
        logger.warning(
            "SECURITY: execution.enforce_code_security is false. Code security checks are OFF — "
            "model-generated Python runs with real builtins and modules (os, sys, subprocess "
            "reachable). Do not use with untrusted input."
        )
