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
__all__ = [
    "CugaAgent",
    "CugaSupervisor",
    "run_agent",
    "InvokeResult",
    "PolicyDecision",
    "PolicyDecisionOutcome",
    "PolicyDecisionStage",
    "tracked_tool",
    "KnowledgeClient",
    "KnowledgeEngine",
    "KnowledgeConfig",
    # Tool shortlisting: how a large tool set is shrunk before the model sees it.
    "Shortlister",
    "ShortlisterStrategy",
    # Client-adaptation SDK surface — operators can build / validate / hash
    # adaptation text without going through the HTTP layer.
    "CLIENT_ADAPTATION_MAX_CHARS",
    "CLIENT_GLOSSARY_MAX_ENTRIES",
    "ClientAdaptationError",
    "client_adaptation_hash",
    "client_glossary_hash",
    "expand_query_with_glossary",
]

# For type checkers and IDEs — not executed at runtime.
if TYPE_CHECKING:
    from cuga.sdk import CugaAgent, CugaSupervisor, run_agent, InvokeResult
    from cuga.backend.cuga_graph.nodes.cuga_lite.tracking.tracker import tracked_tool
    from cuga.backend.cuga_graph.nodes.cuga_lite.shortlister import (
        Shortlister,
        ShortlisterStrategy,
    )
    from cuga.backend.knowledge import KnowledgeClient, KnowledgeEngine
    from cuga.backend.knowledge.config import (
        CLIENT_ADAPTATION_MAX_CHARS,
        CLIENT_GLOSSARY_MAX_ENTRIES,
        ClientAdaptationError,
        KnowledgeConfig,
        client_adaptation_hash,
        client_glossary_hash,
    )
    from cuga.backend.cuga_graph.policy.models import (
        PolicyDecision,
        PolicyDecisionOutcome,
        PolicyDecisionStage,
    )
    from cuga.backend.knowledge.query_expansion import expand_query_with_glossary

# Lazy-loading map: attribute name -> (module, attribute).
# All public exports are deferred to first access via __getattr__ (PEP 562).
_import_map: dict = {
    "CugaAgent": ("cuga.sdk", "CugaAgent"),
    "CugaSupervisor": ("cuga.sdk", "CugaSupervisor"),
    "run_agent": ("cuga.sdk", "run_agent"),
    "InvokeResult": ("cuga.sdk", "InvokeResult"),
    "tracked_tool": (
        "cuga.backend.cuga_graph.nodes.cuga_lite.tracking.tracker",
        "tracked_tool",
    ),
    "KnowledgeClient": ("cuga.backend.knowledge", "KnowledgeClient"),
    "KnowledgeEngine": ("cuga.backend.knowledge", "KnowledgeEngine"),
    "KnowledgeConfig": ("cuga.backend.knowledge.config", "KnowledgeConfig"),
    "Shortlister": (
        "cuga.backend.cuga_graph.nodes.cuga_lite.shortlister",
        "Shortlister",
    ),
    "ShortlisterStrategy": (
        "cuga.backend.cuga_graph.nodes.cuga_lite.shortlister",
        "ShortlisterStrategy",
    ),
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
    "PolicyDecision": (
        "cuga.backend.cuga_graph.policy.models",
        "PolicyDecision",
    ),
    "PolicyDecisionOutcome": (
        "cuga.backend.cuga_graph.policy.models",
        "PolicyDecisionOutcome",
    ),
    "PolicyDecisionStage": (
        "cuga.backend.cuga_graph.policy.models",
        "PolicyDecisionStage",
    ),
}


def __getattr__(name: str):
    """Lazy-load public exports on first access (PEP 562).

    Defers all heavy backend imports until actually needed, so
    ``import cuga`` is near-instant and ``from cuga import CugaAgent``
    only pays the full import cost once.
    """
    if name in _import_map:
        import importlib

        module_name, attr_name = _import_map[name]
        module = importlib.import_module(module_name)
        attr = getattr(module, attr_name)
        # Cache in module namespace — subsequent accesses skip __getattr__.
        globals()[name] = attr
        return attr
    raise AttributeError(f"module 'cuga' has no attribute '{name}'")


def __dir__():
    """Support dir(cuga) and IDE autocompletion."""
    return list(__all__)
