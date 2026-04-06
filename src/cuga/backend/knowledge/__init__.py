from cuga.backend.knowledge.engine import KnowledgeEngine
from cuga.backend.knowledge.client import KnowledgeClient
from cuga.backend.knowledge.config import KnowledgeConfig

__all__ = ["KnowledgeEngine", "KnowledgeClient", "KnowledgeConfig"]


def __getattr__(name: str):
    if name == "OpenRAGClient":
        import warnings

        warnings.warn(
            "OpenRAGClient is deprecated. Use KnowledgeClient instead. See SDK migration guide in the docs.",
            DeprecationWarning,
            stacklevel=2,
        )
        return KnowledgeClient
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
