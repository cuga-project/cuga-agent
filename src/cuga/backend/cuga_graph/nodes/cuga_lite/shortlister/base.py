"""Core types for pluggable tool shortlisting.

A *shortlister* reduces a candidate tool set down to the ones worth showing the
model. CugaLite does this at two call sites — runtime discovery (``find_tools``)
and the bind-time provider cap — and both route through
:class:`ShortlisterStrategy` so the ranker can be swapped by configuration.

See ``docs/design/pluggable-shortlister.md``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, ClassVar, List, Optional, Protocol

from langchain_core.tools import StructuredTool


@dataclass
class ShortlistCandidate:
    """One ranked tool.

    ``score`` is strategy-defined (LLM relevance, cosine similarity, …) and is
    used only for ordering — never compared across strategies.
    """

    name: str
    score: float = 0.0
    reasoning: str = ""


@dataclass
class ShortlistRequest:
    """Everything a strategy needs to rank ``tools`` against ``query``.

    ``query`` and ``task_context`` are kept **separate** rather than pre-joined:
    the LLM strategy re-composes them into the exact prompt string it has always
    sent, while the embedding strategy weights them independently (a long task
    context would otherwise dominate a short per-step query and make every step
    of a task retrieve the same tools).

    ``top_k=None`` means "strategy decides how many" — the ``find_tools``
    semantic, where the result count varies with the query.
    """

    query: str
    tools: List[StructuredTool]
    apps: List[Any]
    task_context: Optional[str] = None
    top_k: Optional[int] = None
    max_results: Optional[int] = None
    llm: Optional[Any] = None
    run_config: Any = None
    instructions: Optional[str] = None


@dataclass
class ShortlistResult:
    """Ranked candidates plus any annotation to surface to the agent.

    ``notes`` carries strategy-produced messages through to the rendered
    markdown — currently the "filtered out N unrecognized tool names" footer the
    LLM strategy emits after name validation (#546/#549).
    """

    candidates: List[ShortlistCandidate] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)


class ShortlisterUnavailableError(RuntimeError):
    """Strategy cannot run right now (model not loaded, dependency missing).

    Deliberately distinct from a genuine ranking failure: the factory catches
    this and degrades to ``fallback_strategy`` instead of failing the run. A
    strategy that is merely *bad* at ranking must not raise this.
    """


class ShortlisterStrategy(Protocol):
    """Swap-in ranker. Implement this to provide a custom shortlister.

    Register it via ``[shortlister] strategy = "my.pkg.MyShortlister"`` or
    ``CugaAgent(shortlister=Shortlister(instance=MyShortlister()))``.

    Returns candidates best-first. Names that match no tool in the request are
    dropped by the caller, so a strategy need not filter them itself.
    """

    name: ClassVar[str]

    async def shortlist(self, request: ShortlistRequest) -> ShortlistResult: ...
