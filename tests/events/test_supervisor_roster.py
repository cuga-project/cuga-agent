"""Offline gates for the SUPERVISOR ROSTER (supervisor_agents.yaml — the canonical source of
truth for sub-agents; the events docs (plans/SUPERVISOR_REFACTOR.md)).

The supervisor routes on the sub-agent NAME (its prompt has no other description to go on). These
gates keep the roster honest without an LLM in the loop:

  * the YAML parses and has the supervisor block + a non-trivial roster
  * no roster smuggles trigger hints back into a prompt (they never reached the router; see the
    test for the evidence)
  * no agent declares a trigger that does not exist in the registry — asserted against the
    STRUCTURED integrations[].triggers, which is the source consumed by code
"""

from __future__ import annotations

import glob
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src", "cuga", "backend", "events"))

import seed  # noqa: E402
import triggers  # noqa: E402

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
ROSTER = os.path.join(ROOT, "events", "examples", "rosters", "default.yaml")


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
    # covered); the 27-agent example moved to events/examples/rosters/supervisor_agents_full.yaml. This floor only
    # catches an accidentally truncated or half-written file.
    assert len(agents) >= 5, f"roster suspiciously small: {len(agents)}"
    assert all(a.get("name") and a.get("special_instructions") for a in agents)


def test_no_roster_smuggles_trigger_hints_back_into_the_prompts():
    """Trigger ownership belongs in structured data, never in prose inside a prompt.

    The rosters used to append "HANDLES TRIGGERS: github/new_pr (…), …" to every sub-agent's
    special_instructions, on the stated theory that the supervisor routed on them. It does not.
    The routing prompt renders each sub-agent as ``{{ agent['description'] }}``
    (cuga_supervisor/prompts/supervisor_lite_prompt.jinja2) and ``description`` comes from
    ``getattr(agent, "description", f"Internal agent: {name}")`` — and ``CugaAgent`` defines no
    ``description``, so every entry falls back to the agent's own name. The hints reached only the
    sub-agent's OWN prompt, i.e. after routing had already picked it, while costing ~50% of the
    roster's prompt text (~447 tokens on the shipped 8-agent roster).

    They were removed. This test stops them coming back by hand or by generator.
    """
    for path in [ROSTER] + sorted(glob.glob(os.path.join(ROOT, "events", "examples", "rosters", "*.yaml"))):
        text = open(path).read()
        assert "HANDLES" not in text, (
            f"{os.path.basename(path)} reintroduces HANDLES prose. Trigger ownership goes in the "
            f"structured integrations[].triggers on the AgentSpec (events/seed.py), not the prompt."
        )


def test_no_agent_declares_a_trigger_that_does_not_exist():
    """The invariant worth keeping, moved to the structured source.

    ``AgentSpec.integrations[].triggers`` is machine-readable and IS consumed (the connect gate
    reads it). A stale entry there — left behind by a trigger rename — is a real defect, unlike a
    stale line of prose. This is the structured replacement for the old
    ``test_every_claimed_trigger_exists_in_the_registry``.

    NB: the reverse direction (every registry trigger has an owner) is deliberately NOT asserted.
    It is currently false — 9 triggers (google_calendar ×3, pinterest ×3, rss, youtube,
    discord/new_reaction) have no structured owner — and the old prose test only passed because the
    HANDLES lines had been hand-expanded far beyond what any agent actually declares. Asserting a
    coverage number nobody maintains is how the prose drifted in the first place.
    """
    known = {t.key for t in triggers.rows()}
    apps = {t.app for t in triggers.rows()}
    stale = []
    for spec in seed.default_agents():
        for integ in spec.integrations or []:
            app = integ.get("app", "")
            for event in integ.get("triggers") or []:
                if app in apps and (app, event) not in known:
                    stale.append((spec.name, app, event))
    assert not stale, f"agents declaring triggers that no longer exist: {stale}"
