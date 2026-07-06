"""The `cuga-*` MCP server catalog — auto-registration by name.

Ported from event-agent-ap so the concierge can wire the well-known cuga-apps MCP
servers just by naming them (``cuga-finance``, ``cuga-knowledge``, …). Scale-to-zero
on Code Engine → warm them before demos/tests. Stdlib-only.
"""

from __future__ import annotations

# The cuga-apps MCP servers hosted on IBM Code Engine.
CUGA_APPS = ("web", "knowledge", "geo", "finance", "code", "local", "text")

_CODE_ENGINE = ("https://cuga-apps-mcp-{app}"
                ".1gxwxi8kos9y.us-east.codeengine.appdomain.cloud/mcp")

# One-line hint per server for the concierge's list_capabilities.
HINTS = {
    "cuga-finance": "get_stock_quote / get_crypto_price",
    "cuga-knowledge": "search_arxiv (recent papers)",
    "cuga-geo": "country capital / population / region",
    "cuga-web": "web search / browse / weather / wiki",
    "cuga-text": "summarize / translate / text utilities",
    "cuga-code": "explain / analyze code",
    "cuga-local": "local/system operations",
}


def known_names() -> list[str]:
    """All well-known cuga-* server names."""
    return [f"cuga-{a}" for a in CUGA_APPS]


def known_mcp_url(name: str) -> str | None:
    """URL for a well-known cuga-* server name, else None."""
    if name.startswith("cuga-") and name[5:] in CUGA_APPS:
        return _CODE_ENGINE.format(app=name[5:])
    return None


def to_client_config(name: str, transport: str = "streamable_http") -> dict | None:
    """A MultiServerMCPClient-style config entry for a known server, else None.

        {"cuga-finance": {"url": "...", "transport": "streamable_http"}}
    """
    url = known_mcp_url(name)
    if url is None:
        return None
    return {"url": url, "transport": transport}


def resolve(names: list[str]) -> dict:
    """Turn a list of server names into a MultiServerMCPClient config dict (known ones only)."""
    out: dict = {}
    for n in names or []:
        cfg = to_client_config(n)
        if cfg is not None:
            out[n] = cfg
    return out
