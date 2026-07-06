"""Seed pre-built agents — what a BUILDER creates at design time.

In the refined model the concierge NEVER creates agents; a builder does (skill + MCP tools +
policies + the channels/integrations the agent may use). Until the builder UI lands, this module
seeds a demo fleet so the runtime router (answer-now / reuse-or-create-flow / decline) has agents
to work with — and so tests/UI have a realistic starting point.

Each agent declares:
  - ``mcp_servers``   — its tools (from mcp_catalog; loaded via the CUGA registry).
  - ``channels``      — where it can converse (web / telegram / …).
  - ``integrations``  — apps it can watch/act on, each with credential OWNERSHIP:
        'shared'   → the builder connects a service account once.
        'per-user' → each chatting user logs in with their own (OAuth/token) at first use.

Enable with ``EVENTS_SEED_AGENTS=1`` (main.py seeds on startup). Idempotent (upsert by name).
"""

from __future__ import annotations

import logging

try:
    from .runtime import AgentSpec, DEFAULT_SCOPE
except ImportError:  # flat load (offline tests)
    from runtime import AgentSpec, DEFAULT_SCOPE

log = logging.getLogger("cuga.events.seed")


def _worker_backend() -> str:
    import os
    return os.environ.get("EVENTS_WORKER_BACKEND", "cuga")


def default_agents(backend: str | None = None) -> list[AgentSpec]:
    """The demo fleet. Names are GENERAL/reusable (pricebot handles any price, not just BTC)."""
    b = backend or _worker_backend()
    web = ["web", "telegram"]
    return [
        AgentSpec(name="pricebot", backend=b, mcp_servers=["cuga-finance"], channels=web,
                  prompt="You answer crypto/stock price questions concisely using your tools."),
        AgentSpec(name="geobot", backend=b, mcp_servers=["cuga-knowledge", "cuga-geo"], channels=web,
                  prompt="You answer country/geography questions — capital, population, region — by looking "
                         "them up on Wikipedia (cuga-knowledge: search_wikipedia / get_article_summary). "
                         "You can also geocode a place and find nearby hikes/attractions (cuga-geo)."),
        AgentSpec(name="weatherbot", backend=b, mcp_servers=["cuga-web"], channels=web,
                  prompt="You answer current-weather questions for a city."),
        AgentSpec(name="papers", backend=b, mcp_servers=["cuga-knowledge"], channels=web,
                  prompt="You find and summarize recent arXiv papers on a topic."),
        AgentSpec(name="market_briefer", backend=b, mcp_servers=["cuga-finance", "cuga-web"],
                  channels=web, access=["builder", "admin"],   # restricted → demonstrates perms
                  prompt="You produce a short market brief on request."),
        # agents that ACT ON integrations — these drive the per-user login (OAuth) story
        AgentSpec(name="mailbot", backend=b, mcp_servers=["cuga-text"], channels=web,
                  integrations=[{"app": "gmail", "ownership": "per-user"}],
                  prompt="You summarize and triage the user's Gmail. Uses their own Gmail login."),
        AgentSpec(name="resume_judge", backend=b, mcp_servers=["cuga-text"], channels=web,
                  integrations=[{"app": "box", "ownership": "per-user"},
                                {"app": "gmail", "ownership": "per-user"}],
                  prompt="When a resume lands in Box, judge fit vs the JD and email the result."),
        AgentSpec(name="support_digest", backend=b, mcp_servers=["cuga-web"],
                  channels=["web", "slack"],
                  integrations=[{"app": "slack", "ownership": "shared"}],
                  prompt="You post an overnight support digest to a shared Slack channel."),
    ]


def seed_default_agents(runtime, scope: str = DEFAULT_SCOPE, backend: str | None = None) -> list[str]:
    """Upsert the demo fleet into the runtime's store for ``scope``. Returns the names.

    NOTE: agents are per-``scope`` in the AgentStore. So that BOTH alice and bob (distinct
    scopes) can use the fleet, seed under each user's scope — or use tenant-shared agents. For
    the demo we seed the DEFAULT scope + each seeded user's scope (see main.py)."""
    names = []
    for spec in default_agents(backend):
        runtime.upsert_agent(spec, scope=scope)
        names.append(spec.name)
    log.info("seeded %d agents into scope=%s: %s", len(names), scope, ", ".join(names))
    return names


def seed_default_users(user_store, tenant: str = "default") -> list[str]:
    """A demo org: an admin/builder + two plain users (for two-user isolation tests)."""
    user_store.add("admin", email="admin@acme.test", roles=["admin", "builder", "user"],
                   password="admin", tenant=tenant)
    user_store.add("alice", email="alice@acme.test", roles=["user"], password="alice", tenant=tenant)
    user_store.add("bob", email="bob@acme.test", roles=["user"], password="bob", tenant=tenant)
    log.info("seeded users into tenant=%s: admin, alice, bob", tenant)
    return ["admin", "alice", "bob"]
