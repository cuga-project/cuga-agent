"""Event-driven concierge layer for CUGA (opt-in via ``EVENTS_ENABLED``).

A concierge turns natural language into worker agents + Activepieces flows; every
trigger (channel message, app event, cron, run-once) re-enters through ``POST /invoke``.
See ``events_docs/DESIGN.md``.

Import policy: this ``__init__`` stays import-light on purpose. The pure modules
(``envelope``, ``mcp_catalog``, ``trace``, ``flows``, ``subscriptions``, ``classify``)
are stdlib-only and independently importable/testable. Heavy adapters
(``runtime.ReactRuntime`` / ``runtime.CugaRuntime``) import langgraph/cuga lazily
inside their methods, so importing this package never drags in the full runtime.
"""

__all__ = [
    "envelope",
    "mcp_catalog",
    "trace",
    "flows",
    "subscriptions",
    "classify",
    "runtime",
]

EVENTS_FLAG = "EVENTS_ENABLED"


def enabled() -> bool:
    """True when the events layer is switched on. Off by default → CUGA unchanged."""
    import os

    return os.environ.get(EVENTS_FLAG, "").lower() in ("1", "true", "yes", "on")
