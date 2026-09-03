"""The `cuga_*` MCP server catalog — auto-registration by name.

Ported from event-agent-ap so the concierge can wire the well-known cuga-apps MCP
servers just by naming them (``cuga_finance``, ``cuga_knowledge``, …). Scale-to-zero
on Code Engine → warm them before demos/tests. Stdlib-only.

UNDERSCORES, NOT HYPHENS, and this is the one naming rule worth stating. These names are
registry app names, and CUGA composes a tool identifier as ``<app>_<tool>`` — so a hyphen
would produce ``cuga-finance_get_crypto_price``, which the code-execution agent parses as
subtraction. This catalog used to spell them ``cuga-finance``, which forced a translation
step in the supervisor loader; that step is gone because the names now simply agree.
The Code Engine HOSTNAMES keep their hyphens (``cuga-apps-mcp-finance``) — they are DNS,
not identifiers, and _CODE_ENGINE below builds them from the bare suffix.
"""

from __future__ import annotations

# The cuga-apps MCP servers hosted on IBM Code Engine.
CUGA_APPS = ("web", "knowledge", "geo", "finance", "code", "local", "text")

_CODE_ENGINE = "https://cuga-apps-mcp-{app}.1gxwxi8kos9y.us-east.codeengine.appdomain.cloud/mcp"

# One-line hint per server for the concierge's list_capabilities.
HINTS = {
    "cuga_finance": "get_stock_quote / get_crypto_price",
    "cuga_knowledge": "search_arxiv (recent papers)",
    "cuga_geo": "country capital / population / region",
    "cuga_web": "web search / browse / weather / wiki",
    "cuga_text": "summarize / translate / text utilities",
    "cuga_code": "explain / analyze code",
    "cuga_local": "local/system operations",
}


def known_names() -> list[str]:
    """All well-known cuga_* server names."""
    return [f"cuga_{a}" for a in CUGA_APPS]


def known_mcp_url(name: str) -> str | None:
    """URL for a well-known cuga_* server name, else None.

    The hyphenated spelling this catalog used to emit is still accepted, so an agent
    provisioned before the rename keeps resolving instead of silently losing its tools.
    Nothing writes it any more.
    """
    prefix = name[:5]
    if prefix in ("cuga_", "cuga-") and name[5:] in CUGA_APPS:
        return _CODE_ENGINE.format(app=name[5:])
    return None


def to_client_config(name: str, transport: str = "streamable_http") -> dict | None:
    """A MultiServerMCPClient-style config entry for a known server, else None.

    {"cuga_finance": {"url": "...", "transport": "streamable_http"}}
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
