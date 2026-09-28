"""Seed-once: the DB is the runtime source of truth; the roster YAML is a first-boot SEED.

WHY THIS EXISTS
---------------
The roster YAML is baked into the container image and is git-managed — a running microservice
cannot write back to it. So it cannot be the runtime source of truth for anything an operator does
in the Manage UX. It must be a *seed* for an empty DB; after that, the DB wins.

The bug this closes: `seed_roster` re-imported the roster on EVERY boot, so deleting a roster agent
in the UX was undone on the next restart/redeploy (a missing row looks identical to "never seeded",
so it was re-created). Now `seed_roster` stamps a fingerprint of the roster on the supervisor and,
on later boots, SKIPS re-seeding while that fingerprint is unchanged — so UI deletes/edits stick.
A DELIBERATE roster change (new fingerprint, shipped via redeploy) still re-applies.

These are the behaviours that guarantee it.
"""

from __future__ import annotations

import pytest

from cuga.backend.server import config_store
from cuga.supervisor_utils import roster_seed

pytestmark = pytest.mark.unit

ROSTER = """
supervisor:
  name: cuga
  special_instructions: route to the right specialist
agents:
  - name: pricebot
    special_instructions: You answer crypto/stock price questions.
    mcp_servers:
      - name: cuga_finance
  - name: geobot
    special_instructions: You answer geography questions.
    mcp_servers:
      - name: cuga_geo
"""


@pytest.fixture
def roster_file(tmp_path):
    p = tmp_path / "roster.yaml"
    p.write_text(ROSTER)
    return str(p)


@pytest.fixture(autouse=True)
def clean_store():
    config_store.reset_config_db()
    yield
    config_store.reset_config_db()


@pytest.mark.asyncio
async def test_a_ui_delete_is_NOT_undone_by_the_next_seed(roster_file):
    """THE FIX. Delete a roster agent (as the UX does), then re-seed the SAME roster (a restart /
    redeploy). Before seed-once this re-created the agent; now the unchanged fingerprint makes the
    re-seed a no-op, so the delete sticks."""
    await roster_seed.seed_roster(roster_file)
    assert (await config_store.load_config(None, "geobot"))[0] is not None

    # the operator deletes geobot in the Manage UX → its rows are removed from the store
    await config_store.delete_all_configs("geobot")
    assert (await config_store.load_config(None, "geobot"))[0] is None

    # restart / redeploy: the SAME image ships the SAME roster
    count, tally = await roster_seed.seed_roster(roster_file)

    after, _ = await config_store.load_config(None, "geobot")
    assert after is None, "seed-once failed: the delete was undone on re-seed"
    assert tally["created"] == 0, "re-seed created something despite an unchanged roster"


@pytest.mark.asyncio
async def test_a_changed_roster_DOES_re_apply(roster_file, tmp_path):
    """The escape hatch. A deliberate roster change (new fingerprint, shipped by redeploy) must
    re-apply — otherwise you could never push a roster update to an existing deployment. Here the
    change re-introduces a previously-deleted agent, proving the fingerprint gate reopened."""
    await roster_seed.seed_roster(roster_file)
    await config_store.delete_all_configs("geobot")

    # the checked-in roster changes (an intentional edit to geobot's instructions)
    (tmp_path / "roster.yaml").write_text(ROSTER.replace("geography questions", "ONLY geography"))
    _, tally = await roster_seed.seed_roster(roster_file)

    after, _ = await config_store.load_config(None, "geobot")
    assert after is not None, "a changed roster did not re-apply"
    assert "ONLY geography" in after["special_instructions"]
    assert tally["created"] + tally["updated"] >= 1


@pytest.mark.asyncio
async def test_a_human_edit_wins_even_when_the_roster_is_re_applied(roster_file, tmp_path):
    """Seed-once must not weaken the per-agent human-edit protection: when the roster DOES change and
    re-applies, an agent a human edited in Manage is still left alone."""
    await roster_seed.seed_roster(roster_file)

    # human edits pricebot in Manage
    cfg, _ = await config_store.load_config(None, "pricebot")
    cfg["special_instructions"] = "EDITED BY A HUMAN"
    await config_store.save_config(cfg, agent_id="pricebot")

    # an UNRELATED roster change forces a re-apply (fingerprint differs)
    (tmp_path / "roster.yaml").write_text(ROSTER.replace("geography questions", "ONLY geography"))
    _, tally = await roster_seed.seed_roster(roster_file)

    after, _ = await config_store.load_config(None, "pricebot")
    assert after["special_instructions"] == "EDITED BY A HUMAN", "re-apply clobbered a human edit"
    assert tally["skipped"] >= 1


@pytest.mark.asyncio
async def test_first_boot_still_seeds_from_the_yaml(roster_file):
    """Seed-once must not break the first import: an empty DB gets the full roster."""
    count, tally = await roster_seed.seed_roster(roster_file)
    assert count == 3  # pricebot + geobot + supervisor
    assert tally["created"] == 3
    assert (await config_store.load_config(None, "pricebot"))[0] is not None
    assert (await config_store.load_config(None, "geobot"))[0] is not None
