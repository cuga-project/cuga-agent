"""Offline gates for the SUPERVISOR ROSTER (supervisor_agents.yaml — the canonical source of
truth for sub-agents; events_docs/plans/SUPERVISOR_REFACTOR.md).

The supervisor routes on the HANDLES lines inside each sub-agent's special_instructions. These
gates keep that routing surface honest without an LLM in the loop:

  * the YAML parses and has the supervisor block + a non-trivial roster
  * EVERY registry trigger is claimed by at least one sub-agent's HANDLES line — an unclaimed
    trigger means the supervisor has no hint for that event and will guess
  * every HANDLES trigger actually EXISTS in the registry (no stale hints after a trigger rename)
"""

from __future__ import annotations

import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src", "cuga", "backend", "events"))

import triggers  # noqa: E402

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
ROSTER = os.path.join(ROOT, "supervisor_agents.yaml")


def _roster():
    import yaml

    with open(ROSTER) as f:
        return yaml.safe_load(f)


def test_roster_parses_and_has_the_supervisor_block():
    cfg = _roster()
    assert (cfg.get("supervisor") or {}).get("name") == "cuga"
    assert "delegate" in (cfg["supervisor"].get("special_instructions") or "").lower()
    agents = cfg.get("agents") or []
    # The shipped roster is deliberately SMALL (one agent per capability area, every MCP server
    # covered); the 27-agent example moved to rosters/supervisor_agents_full.yaml. This floor only
    # catches an accidentally truncated or half-written file — the real gate is trigger coverage
    # below, which does not care how many agents share the work.
    assert len(agents) >= 5, f"roster suspiciously small: {len(agents)}"
    assert all(a.get("name") and a.get("special_instructions") for a in agents)


def _handles_pairs(cfg) -> set:
    pairs = set()
    for a in cfg.get("agents") or []:
        for m in re.finditer(r"\b([a-z_]+)/([a-z_]+)\b", a.get("special_instructions") or ""):
            pairs.add((m.group(1), m.group(2)))
    return pairs


def test_every_registry_trigger_is_claimed_by_a_sub_agent():
    """An unclaimed trigger = the supervisor has NO routing hint for that event."""
    claimed = _handles_pairs(_roster())
    missing = [t.key for t in triggers.rows() if t.key not in claimed and t.key != ("webhook", "inbound")]
    assert not missing, f"triggers no sub-agent claims (add a HANDLES line): {missing}"


def test_every_claimed_trigger_exists_in_the_registry():
    """A stale HANDLES hint (renamed/removed trigger) misleads the supervisor's routing."""
    known = {t.key for t in triggers.rows()}
    apps = {t.app for t in triggers.rows()}
    stale = [p for p in _handles_pairs(_roster()) if p[0] in apps and p not in known]
    assert not stale, f"HANDLES hints pointing at unknown triggers: {stale}"
