"""Per-agent authorization — 'may this user talk to this agent?' (decision 0007).

An agent's ``access`` list holds allowed **roles** and/or **user_ids**. Empty = everyone (the
default). The other permission axis — 'may you act on this data?' — needs no check here: per-user
connections are keyed to the principal, so a user can only ever act on their own data.
Stdlib-only.
"""

from __future__ import annotations


def can_use(spec, roles: list | None = None, user_id: str | None = None) -> bool:
    """True if a user with these ``roles`` / ``user_id`` may use ``spec``. Empty access = all."""
    access = getattr(spec, "access", None) or []
    if not access:
        return True
    allowed = set(access)
    if user_id and user_id in allowed:
        return True
    return bool(allowed & set(roles or []))


def visible_agents(agents, roles: list | None = None, user_id: str | None = None) -> list:
    """Filter a list of AgentSpecs to the ones this user may use."""
    return [a for a in agents if can_use(a, roles, user_id)]
