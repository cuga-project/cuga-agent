"""Per-request run metadata — who answered and with what.

The worker (HttpRuntime.run → CUGA POST /run) records the agent, its MCP servers, and the
tools it actually called into a contextvar; the ``/invoke`` handler reads it after the run to build
a one-line footer on the reply (and a structured ``meta`` block in the API response). Contextvar,
so it flows through the single awaited call chain (/invoke → runtime.run → run_graph, or via the
concierge's answer_now) without changing the AgentRuntime interface.
"""

from __future__ import annotations

import contextvars

_meta: contextvars.ContextVar[dict | None] = contextvars.ContextVar("run_meta", default=None)


def start() -> None:
    """Begin capturing for this request (call before running the worker)."""
    _meta.set({"agent": None, "backend": None, "mcp": [], "tools": []})


def add(**kw) -> None:
    m = _meta.get()
    if m is None:
        m = {"agent": None, "backend": None, "mcp": [], "tools": []}
        _meta.set(m)
    for k, v in kw.items():
        if k == "tools" and v:
            seen = m.get("tools") or []
            for t in v:
                if t and t not in seen:
                    seen.append(t)
            m["tools"] = seen
        else:
            m[k] = v


def get() -> dict | None:
    return _meta.get()


def footer(meta: dict | None, *, ms: int | None = None) -> str:
    """A compact one-line footer for a chat reply, e.g.
    ``— pricebot · cuga_finance.get_crypto_price · 2.3s``. Empty if there's nothing to show."""
    if not meta or not meta.get("agent"):
        return ""
    bits = [meta["agent"]]
    tools = meta.get("tools") or []
    if tools:  # exact tool calls (when captured)
        bits.append(", ".join(tools[:4]) + ("…" if len(tools) > 4 else ""))
    elif meta.get("mcp"):  # else the tool namespace (which MCP servers)
        bits.append("via " + ", ".join(meta["mcp"]))
    if ms is not None:
        bits.append(f"{ms / 1000:.1f}s")
    return "— " + " · ".join(bits)
