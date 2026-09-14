"""Import a roster YAML into the agent config store, so there is ONE runtime path.

WHY THIS EXISTS
---------------
There were two ways to describe "a supervisor and its sub-agents":

    a roster YAML  → load_supervisor_config()          → used by /run   (events)
    the config DB  → build_agents_from_stored_subagents() → used by /stream (#433)

Both already ended in the same builder, so nothing was duplicated in the *code* — but the two
config SOURCES meant `/run` and the Manage UI could disagree about what the roster is, and the
9 events agents were invisible in a UI that lists every other agent.

This collapses that. The store is the only thing read at runtime; the YAML is an import format,
exactly like the JSON import the Manage UI already has. A deployment still boots from a file it
ships in the image — set ``CUGA_SUPERVISOR_ROSTER`` and the roster is seeded before the first
request — but nothing parses YAML to answer a request.

OWNERSHIP RULE
--------------
An agent NAMED IN THE YAML is owned by the YAML: seeding rewrites it on every boot, so the file
stays the reviewed source of truth and a container replace restores it. An agent added through
the UI has an id the YAML never mentions, so seeding never touches it.

IDEMPOTENCE
-----------
``save_config`` bumps to ``max(version) + 1`` on every call, so naive re-seeding would add a
version on every container restart. We compare against the published config first and use
``update_published_config_at_version`` to rewrite in place, so a restart that changes nothing
writes nothing.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional, Tuple

import yaml
from loguru import logger

# The supervisor's own id in the store. The roster's supervisor block is not a sub-agent, and the
# events layer addresses exactly one agent by name — see run_routes and the concierge.
SUPERVISOR_AGENT_ID = "cuga"


def roster_path() -> str:
    """The configured roster file, or "" when this server is not seeding one.

    Same variable the events deployment already sets, so adopting the store path needs no
    deployment change. ` # trailing comments` are stripped, as every other reader here does.
    """
    return (os.environ.get("CUGA_SUPERVISOR_ROSTER", "") or "").split(" #", 1)[0].strip()


def _agent_id(name: str) -> str:
    """The roster name, used VERBATIM as the store id.

    Deliberately NOT ``agents_routes._slugify``, which rewrites anything outside ``[a-z0-9]`` to a
    hyphen. That would rename half this roster — ``code_auditor`` → ``code-auditor`` — and the name
    is the agent's identity everywhere else: the concierge routes on it, ``?agent=`` pins on it, and
    live subscriptions are already armed against it. Renaming would break them silently.

    It also has to equal the ``ref`` stored in ``supervisor.subAgents``, because that ref is what
    ``build_agents_from_stored_subagents`` uses as the agent's name. When the two diverged, every
    underscore-named agent came back from /run/agents with a blank description — i.e. unroutable.

    ``--`` is the one sequence that cannot survive: ``config_store._parse_agent_id`` splits on it.
    """
    return str(name).strip().replace("--", "-")


def _app_names(entry: Dict[str, Any]) -> List[str]:
    """Registry apps this agent declares, from either `apps:` or `mcp_servers:`.

    Stored configs express apps as ``tools: [{name: ...}]`` — the same shape the Manage UI writes
    and ``build_agents_from_stored_subagents`` reads back.
    """
    names: List[str] = []
    for key in ("apps", "mcp_servers"):
        for item in entry.get(key) or []:
            n = item.get("name") if isinstance(item, dict) else str(item)
            if n:
                names.append(n)
    return names


def _sub_agent_config(entry: Dict[str, Any]) -> Dict[str, Any]:
    """One roster entry → the stored config shape `create_agent` + the config editor produce."""
    name = str(entry.get("name") or "").strip()
    # A roster entry may carry either key; the old YAML reader accepted both, so accept both here
    # or an entry written the other way loses its routing text.
    instructions = (entry.get("special_instructions") or entry.get("description") or "").strip()
    return {
        "agent": {
            "name": name,
            # The roster's instructions double as the description: it is what the concierge reads
            # to pick a specialist, and an agent with a blank one is effectively unroutable.
            "description": instructions.split("\n", 1)[0][:300],
            "kind": "single",
        },
        "tools": [{"name": n} for n in _app_names(entry)],
        "special_instructions": instructions,
    }


def _supervisor_config(roster: Dict[str, Any], sub_ids: List[str]) -> Dict[str, Any]:
    sup = roster.get("supervisor") or {}
    return {
        "agent": {
            "name": sup.get("name") or SUPERVISOR_AGENT_ID,
            "description": (sup.get("special_instructions") or "The CUGA supervisor").split("\n", 1)[0][:300],
            "kind": "supervisor",
        },
        "supervisor": {
            "subAgents": [{"kind": "internal", "ref": ref} for ref in sub_ids],
            "planApproval": False,
            **(
                {"special_instructions": sup["special_instructions"]}
                if sup.get("special_instructions")
                else {}
            ),
        },
    }


async def _upsert(config: Dict[str, Any], agent_id: str) -> str:
    """Write `config` as the published config for `agent_id`, without churning versions.

    Returns "created" | "updated" | "unchanged" — the caller logs a summary, and the tests assert
    that a second identical seed is a no-op.
    """
    from cuga.backend.server.config_store import (
        load_config,
        save_config,
        update_published_config_at_version,
    )

    existing, version = await load_config(None, agent_id)
    if existing is None or not version:
        await save_config(config, agent_id=agent_id)
        return "created"
    if existing == config:
        return "unchanged"
    # `load_config(None, ...)` only ever returns a published (numeric) version, but
    # `update_published_config_at_version` raises on anything else, and that would be a crash at
    # startup rather than a bad roster. Fall back to a normal save instead.
    if str(version).isdigit() and await update_published_config_at_version(config, agent_id, version):
        return "updated"
    # The published version could not be rewritten in place (deleted underneath us, or a store
    # that does not support it). A new version is still correct, just noisier.
    await save_config(config, agent_id=agent_id)
    return "updated"


async def seed_roster(path: Optional[str] = None) -> Tuple[int, Dict[str, int]]:
    """Import the roster at `path` (default: $CUGA_SUPERVISOR_ROSTER) into the config store.

    Returns ``(agents_seeded, {"created": n, "updated": n, "unchanged": n})``. Never raises: a
    malformed or missing roster must not stop the server from booting — it degrades to "no
    supervisor", which is exactly what an unset variable does.
    """
    path = path if path is not None else roster_path()
    if not path:
        return 0, {}

    try:
        with open(path, "r") as f:
            roster = yaml.safe_load(f) or {}
    except Exception as e:  # noqa: BLE001 — a bad file must not break startup
        logger.warning(f"roster seed: could not read {path!r}: {e}")
        return 0, {}

    entries = [a for a in (roster.get("agents") or []) if isinstance(a, dict) and a.get("name")]
    if not entries:
        logger.warning(f"roster seed: {path!r} declares no agents")
        return 0, {}

    tally: Dict[str, int] = {"created": 0, "updated": 0, "unchanged": 0}
    sub_ids: List[str] = []

    for entry in entries:
        agent_id = _agent_id(entry["name"])
        if not agent_id or agent_id == SUPERVISOR_AGENT_ID:
            # The supervisor is written below from the roster's own `supervisor:` block; a
            # sub-agent may not squat on its id.
            continue
        try:
            tally[await _upsert(_sub_agent_config(entry), agent_id)] += 1
            sub_ids.append(agent_id)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"roster seed: {agent_id!r} failed: {e}")

    try:
        tally[await _upsert(_supervisor_config(roster, sub_ids), SUPERVISOR_AGENT_ID)] += 1
    except Exception as e:  # noqa: BLE001
        logger.warning(f"roster seed: supervisor failed: {e}")
        return len(sub_ids), tally

    logger.info(
        f"roster seed: {len(sub_ids)} sub-agent(s) + supervisor from {path} "
        f"({tally['created']} created, {tally['updated']} updated, {tally['unchanged']} unchanged)"
    )
    return len(sub_ids) + 1, tally
