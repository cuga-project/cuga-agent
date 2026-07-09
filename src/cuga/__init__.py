"""
CUGA: The Configurable Generalist Agent

CUGA is a state-of-the-art generalist agent designed for enterprise needs,
combining best-of-breed agentic patterns with structured planning and smart
variable management.

Quick Start:
    ```python
    from cuga import CugaAgent
    from langchain_core.tools import tool

    @tool
    def get_weather(city: str) -> str:
        '''Get weather for a city'''
        return f"Weather in {city}: Sunny"

    agent = CugaAgent(tools=[get_weather])
    result = await agent.invoke("What's the weather in NYC?")
    print(result.answer)
    ```

For more information, visit: https://cuga.dev
"""

from typing import TYPE_CHECKING

__version__ = "0.2.20"

# Public API - these will be lazy-loaded via __getattr__
__all__ = [
    "CugaAgent",
    "CugaSupervisor",
    "run_agent",
    "InvokeResult",
    "tracked_tool",
    "KnowledgeClient",
    "KnowledgeEngine",
    "KnowledgeConfig",
    # Client-adaptation SDK surface
    "CLIENT_ADAPTATION_MAX_CHARS",
    "CLIENT_GLOSSARY_MAX_ENTRIES",
    "ClientAdaptationError",
    "client_adaptation_hash",
    "client_glossary_hash",
    "expand_query_with_glossary",
]

# For type checkers (IDE autocomplete) - does not run at runtime
if TYPE_CHECKING:
    from cuga.sdk import CugaAgent, CugaSupervisor, run_agent, InvokeResult
    from cuga.backend.cuga_graph.nodes.cuga_lite.tracking.tracker import tracked_tool
    from cuga.backend.knowledge import KnowledgeClient, KnowledgeEngine
    from cuga.backend.knowledge.config import (
        CLIENT_ADAPTATION_MAX_CHARS,
        CLIENT_GLOSSARY_MAX_ENTRIES,
        ClientAdaptationError,
        KnowledgeConfig,
        client_adaptation_hash,
        client_glossary_hash,
    )
    from cuga.backend.knowledge.query_expansion import expand_query_with_glossary


# Lazy-loading implementation using __getattr__
# This defers all imports until first use, dramatically reducing startup time
_import_map = {
    # SDK exports
    "CugaAgent": ("cuga.sdk", "CugaAgent"),
    "CugaSupervisor": ("cuga.sdk", "CugaSupervisor"),
    "run_agent": ("cuga.sdk", "run_agent"),
    "InvokeResult": ("cuga.sdk", "InvokeResult"),
    # Tracking
    "tracked_tool": (
        "cuga.backend.cuga_graph.nodes.cuga_lite.tracking.tracker",
        "tracked_tool",
    ),
    # Knowledge exports
    "KnowledgeClient": ("cuga.backend.knowledge", "KnowledgeClient"),
    "KnowledgeEngine": ("cuga.backend.knowledge", "KnowledgeEngine"),
    "KnowledgeConfig": ("cuga.backend.knowledge.config", "KnowledgeConfig"),
    "CLIENT_ADAPTATION_MAX_CHARS": (
        "cuga.backend.knowledge.config",
        "CLIENT_ADAPTATION_MAX_CHARS",
    ),
    "CLIENT_GLOSSARY_MAX_ENTRIES": (
        "cuga.backend.knowledge.config",
        "CLIENT_GLOSSARY_MAX_ENTRIES",
    ),
    "ClientAdaptationError": (
        "cuga.backend.knowledge.config",
        "ClientAdaptationError",
    ),
    "client_adaptation_hash": (
        "cuga.backend.knowledge.config",
        "client_adaptation_hash",
    ),
    "client_glossary_hash": ("cuga.backend.knowledge.config", "client_glossary_hash"),
    "expand_query_with_glossary": (
        "cuga.backend.knowledge.query_expansion",
        "expand_query_with_glossary",
    ),
}


def __getattr__(name: str):
    """
    Lazy-load exports on first access.

    This significantly reduces startup time by deferring expensive imports
    until they're actually needed.
    """
    if name in _import_map:
        module_name, attr_name = _import_map[name]
        try:
            # Import the module
            import importlib

            module = importlib.import_module(module_name)
            # Get the attribute
            attr = getattr(module, attr_name)
            # Cache it in the module namespace for faster subsequent access
            globals()[name] = attr
            return attr
        except (ImportError, AttributeError) as e:
            raise AttributeError(
                f"Cannot import '{name}' from '{module_name}': {e}"
            ) from e

    raise AttributeError(f"module 'cuga' has no attribute '{name}'")


def __dir__():
    """Support for dir(cuga) and IDE autocompletion."""
    return list(__all__)
