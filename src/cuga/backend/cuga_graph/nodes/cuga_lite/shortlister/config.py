"""Public SDK config object for shortlisting.

Mirrors the ``ToolCalling`` shape: a dataclass the caller builds, serialized
into ``run_config["configurable"]`` by the SDK. Every field defaults to ``None``
meaning "inherit" — so ``Shortlister(strategy="hybrid")`` is a complete,
sensible configuration and the remaining knobs keep their settings values.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

from cuga.backend.cuga_graph.nodes.cuga_lite.shortlister.plan import (
    CONFIGURABLE_PREFIX,
    ALL_FIELDS,
)


@dataclass
class Shortlister:
    """How CugaLite should shrink a large tool set.

    Example:
        ```python
        from cuga import CugaAgent, Shortlister

        agent = CugaAgent(tools=[...], shortlister=Shortlister(strategy="hybrid"))
        await agent.invoke("...", shortlister=Shortlister(top_k=32))
        ```
    """

    #: ``"llm"`` (default), ``"embedding"``, ``"hybrid"``, or a dotted class path.
    strategy: Optional[str] = None
    #: A pre-built strategy object; wins over ``strategy``.
    instance: Optional[Any] = None
    #: Engage the cosine stage only above this many candidates.
    threshold: Optional[int] = None
    #: How many candidates the cosine stage keeps.
    top_k: Optional[int] = None
    #: Max tools rendered to the agent by ``find_tools``.
    max_results: Optional[int] = None
    #: Cosine floor. Low by design — this is a recall filter.
    min_score: Optional[float] = None
    #: Step-query vs task-context blend, 0..1.
    query_weight: Optional[float] = None
    fallback_strategy: Optional[str] = None
    embedding_model: Optional[str] = None
    embedding_provider: Optional[str] = None


def shortlister_to_configurable(shortlister: Optional[Shortlister]) -> Dict[str, Any]:
    """Serialize to ``shortlister_*`` configurable keys.

    Returns ``{}`` when there is nothing to say, so callers can treat an absent
    or empty config as a no-op.
    """
    if shortlister is None:
        return {}
    out: Dict[str, Any] = {}
    for field_name in ALL_FIELDS:
        value = getattr(shortlister, field_name, None)
        if value is not None:
            out[f"{CONFIGURABLE_PREFIX}{field_name}"] = value
    if shortlister.instance is not None:
        out["shortlister_instance"] = shortlister.instance
    return out
