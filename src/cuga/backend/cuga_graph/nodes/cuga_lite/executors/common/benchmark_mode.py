import contextvars

from cuga.config import settings

_skills_relaxed: contextvars.ContextVar[bool | None] = contextvars.ContextVar(
    "skills_relaxed_execution", default=None
)


def set_skills_relaxed_execution(enabled: bool) -> contextvars.Token:
    return _skills_relaxed.set(enabled)


def reset_skills_relaxed_execution(token: contextvars.Token) -> None:
    _skills_relaxed.reset(token)


def is_benchmark_mode() -> bool:
    """Check if benchmark mode is enabled (non-default benchmark setting).

    Returns:
        True if benchmark mode is enabled, False otherwise
    """
    return settings.advanced_features.benchmark != "default"


def is_skills_relaxed_execution() -> bool:
    """Per-run skills flag only — set via set_skills_relaxed_execution during code execution."""
    value = _skills_relaxed.get()
    return bool(value) if value is not None else False


def is_relaxed_execution() -> bool:
    """Benchmark or skills mode: no SecurityValidator / restricted-import gates."""
    return is_benchmark_mode() or is_skills_relaxed_execution()
