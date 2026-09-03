"""The `cuga_*` MCP server catalog — auto-registration by name.

A CONVENIENCE, NOT AN ALLOW-LIST. These seven are wired by name so the demos work out of the box;
they are not the set of servers an agent may use. Anything registered in the CUGA registry can be
named by a `backend="cuga"` agent (CUGA resolves it there, and this process never sees it), and a
`backend="react"` agent can carry its own endpoint as ``{"name": …, "url": …}``. This module used
to be enforced as a closed list by the agent-editor validator, which meant nobody could attach
their own MCP server; that check is gone.

Ported from event-agent-ap so the concierge can wire the well-known cuga-apps MCP
servers just by naming them (``cuga_finance``, ``cuga_knowledge``, …). Scale-to-zero
on Code Engine → warm them before demos/tests. Stdlib-only apart from the logger.

UNDERSCORES, NOT HYPHENS, and this is the one naming rule worth stating. These names are
registry app names, and CUGA composes a tool identifier as ``<app>_<tool>`` — so a hyphen
would produce ``cuga-finance_get_crypto_price``, which the code-execution agent parses as
subtraction. This catalog used to spell them ``cuga-finance``, which forced a translation
step in the supervisor loader; that step is gone because the names now simply agree.
The Code Engine HOSTNAMES keep their hyphens (``cuga-apps-mcp-finance``) — they are DNS,
not identifiers, and _CODE_ENGINE below builds them from the bare suffix.
"""

from __future__ import annotations

import logging

_log = logging.getLogger("cuga.events")

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


def migrate_legacy_names(names: list) -> list:
    """Rewrite THIS catalog's own pre-rename spellings: ``cuga-web`` → ``cuga_web``.

    Agents provisioned before the rename carry the hyphenated name in storage. The events-native
    backend still resolves those (``known_mcp_url`` accepts both), but a ``backend="cuga"`` worker
    hands the name to CUGA, which scopes verbatim against registry keys that are underscore names —
    so the agent runs with NO tools and only a log warning. Applied on read, so no migration step
    and no operator action; re-saving the agent persists the new spelling.

    DELIBERATELY BOUNDED to the seven names this project renamed. It is not a hyphen→underscore
    rewrite: an operator's own server registered as ``my-server`` is a legitimate registry key and
    passes through untouched. A blanket rewrite is exactly the bug this replaced — it scoped such
    an agent to a key the registry does not have.
    """
    out = []
    for n in names or []:
        s = str(n)
        out.append(f"cuga_{s[5:]}" if s[:5] == "cuga-" and s[5:] in CUGA_APPS else n)
    return out


def to_client_config(name, transport: str = "streamable_http") -> dict | None:
    """A MultiServerMCPClient-style config entry for a server, else None.

    {"cuga_finance": {"url": "...", "transport": "streamable_http"}}

    ``name`` is either a well-known catalog name or a dict carrying its own endpoint —
    ``{"name": …, "url": …, "transport": …}``. The dict form is how an agent reaches an MCP server
    this catalog has never heard of, which is the whole point: the seven below are a convenience,
    not the set of servers that may exist.
    """
    if isinstance(name, dict):
        url = (name.get("url") or "").strip()
        if not url:
            return None
        return {"url": url, "transport": (name.get("transport") or transport)}
    url = known_mcp_url(name)
    if url is None:
        return None
    return {"url": url, "transport": transport}


def resolve(names: list) -> dict:
    """Turn server names into a MultiServerMCPClient config dict.

    Anything that cannot be resolved to a URL is SKIPPED AND LOGGED. It used to be skipped
    silently, which is the worst of the options: the agent came up with fewer tools than it
    declared, answered "I don't have a tool for that", and nothing anywhere said why.

    Skipping rather than raising is deliberate — one unreachable server should not take down an
    agent that has three others — but it has to be visible. Only the react backend comes through
    here; a backend="cuga" worker never does, because CUGA resolves names against its own registry.
    """
    out: dict = {}
    for n in names or []:
        cfg = to_client_config(n)
        key = n.get("name") if isinstance(n, dict) else n
        if cfg is not None:
            out[key] = cfg
        else:
            _log.warning(
                "mcp_catalog.resolve: no endpoint for %r — this agent will run WITHOUT its tools. "
                "Either it is one of the built-in %s, or pass {'name': ..., 'url': ...} so this "
                "process knows where to reach it.",
                key,
                known_names(),
            )
    return out
