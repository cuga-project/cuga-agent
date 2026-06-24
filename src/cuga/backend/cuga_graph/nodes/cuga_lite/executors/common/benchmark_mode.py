from cuga.config import settings


def is_benchmark_mode() -> bool:
    """Check if benchmark mode is enabled (non-default benchmark setting).

    Returns:
        True if benchmark mode is enabled, False otherwise
    """
    return settings.advanced_features.benchmark != "default"


def is_skills_relaxed_execution() -> bool:
    """Skills mode uses shell/filesystem tools; skip inline-code sandbox checks."""
    return bool(getattr(getattr(settings, "skills", None), "enabled", False))


def is_relaxed_execution() -> bool:
    """Benchmark or skills mode: no SecurityValidator / restricted-import gates."""
    return is_benchmark_mode() or is_skills_relaxed_execution()
