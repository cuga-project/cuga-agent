"""Pluggable tool shortlisting for CugaLite.

See ``docs/design/pluggable-shortlister.md``. Default (``strategy = "llm"``)
reproduces the pre-existing behavior exactly.
"""

from cuga.backend.cuga_graph.nodes.cuga_lite.shortlister.base import (
    ShortlistCandidate,
    ShortlistRequest,
    ShortlistResult,
    ShortlisterStrategy,
    ShortlisterUnavailableError,
)
from cuga.backend.cuga_graph.nodes.cuga_lite.shortlister.config import (
    Shortlister,
    shortlister_to_configurable,
)
from cuga.backend.cuga_graph.nodes.cuga_lite.shortlister.factory import (
    clear_instance_cache,
    resolve_shortlister,
    run_shortlister,
    warm_tool_vectors,
)
from cuga.backend.cuga_graph.nodes.cuga_lite.shortlister.plan import (
    BUILTIN_STRATEGIES,
    ShortlisterPlan,
    ShortlisterRouter,
)
from cuga.backend.cuga_graph.nodes.cuga_lite.shortlister.render import render_tools_markdown

__all__ = [
    "BUILTIN_STRATEGIES",
    "Shortlister",
    "ShortlistCandidate",
    "ShortlistRequest",
    "ShortlistResult",
    "ShortlisterPlan",
    "ShortlisterRouter",
    "ShortlisterStrategy",
    "ShortlisterUnavailableError",
    "clear_instance_cache",
    "render_tools_markdown",
    "resolve_shortlister",
    "run_shortlister",
    "warm_tool_vectors",
    "shortlister_to_configurable",
]
