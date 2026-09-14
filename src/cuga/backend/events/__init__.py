"""The event-driven layer for CUGA — its OWN SERVICE (``cuga.backend.events.service``).

A concierge turns natural language into standing flows; every trigger (channel message, app
event, cron, run-once) re-enters through ``POST /invoke``, and the worker call goes out to CUGA's
``POST /run`` over HTTP. This package is never mounted onto CUGA's app — that "combined" mode was
removed, along with the ``EVENTS_ENABLED`` flag that gated it. See ``the events docs (ARCHITECTURE.md)``.

Import policy: this ``__init__`` stays import-light on purpose. The pure modules
(``envelope``, ``mcp_catalog``, ``trace``, ``flows``, ``subscriptions``, ``classify``)
are stdlib-only and independently importable/testable. Heavy adapters
(``runtime.ReactRuntime``) import langgraph lazily inside their methods, so importing this
package never drags in the full runtime.
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

# NB: there is no on/off flag any more. Running this package IS enabling it — it is a separate
# service, so "off" simply means not starting it.
