"""Integration credentials — the two ownership models (DESIGN discussion).

An integration on an agent declares HOW its credential is owned:

  - 'shared'   — a service account the BUILDER connects once; every user of the agent uses
                 the same credential. AP connection is scoped to the agent (within the tenant):
                 ``ea::<tenant>::agent::<agent>::<app>``.  (Worker-side shared secrets can also
                 live as a CUGA secret; for AP steps it's an AP connection.)
  - 'per-user' — each chatting user authorizes THEIR OWN credential (OAuth / token) at runtime;
                 the AP connection is scoped to the principal: ``ea::<tenant>::<user>::<app>``.
                 If the user hasn't connected yet, the concierge prompts a just-in-time connect.

This module only computes the connection reference + whether a connect is needed; the actual
AP connection CRUD lives on APEngine. Stdlib-only.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

OWNERSHIP = ("shared", "per-user")


@dataclass
class Integration:
    """An integration bound to an agent (declared at agent creation)."""
    app: str                       # gmail | box | github | telegram | slack | ...
    ownership: str = "per-user"    # 'shared' (service acct) | 'per-user' (each user's own)

    def __post_init__(self):
        if self.ownership not in OWNERSHIP:
            raise ValueError(f"ownership must be one of {OWNERSHIP}, got {self.ownership!r}")


def _san(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9_:.-]", "-", s)


def connection_external_id(app: str, ownership: str, principal, agent: str | None = None) -> str:
    """The AP connection externalId to use for ``app`` under this ownership + principal.

    shared   → per-agent within the tenant (all the agent's users share it).
    per-user → per (tenant, user) — each user their own, distinct credential.
    """
    if ownership == "shared":
        base = f"{principal.tenant_id}::agent::{agent}" if agent else f"{principal.tenant_id}::shared"
        return _san(f"ea::{base}::{app}")
    return principal.connection_external_id(app)     # ea::<tenant>::<user>::<app>


def is_per_user(ownership: str) -> bool:
    return ownership == "per-user"
