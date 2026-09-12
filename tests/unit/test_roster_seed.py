"""The roster YAML is an IMPORT FORMAT, not a second runtime path.

WHAT THIS PINS
--------------
There used to be two sources for "a supervisor and its sub-agents": a roster YAML read by ``/run``,
and the config store read by the Manage UI. Same builder underneath, but two sources — so the two
could disagree, and the roster's agents were invisible in a UI that lists every other agent.

Now ``seed_roster`` imports the YAML into the store at startup and everything reads the store. These
tests hold that contract:

  * seeding produces the same record shape the UI writes, so a seeded agent is an ordinary agent
  * seeding twice is a no-op — it must not add a config version on every container restart
  * an agent NAMED IN THE YAML is owned by the YAML: re-seeding restores it after an edit
  * an agent added through the UI is NOT named in the YAML, so seeding never touches it
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
    special_instructions: |
      You answer crypto/stock price questions.
    mcp_servers:
      - name: cuga_finance
  - name: weatherbot
    special_instructions: You answer current-weather questions.
    mcp_servers:
      - name: cuga_web
      - name: cuga_geo
"""


@pytest.fixture
def roster_file(tmp_path):
    p = tmp_path / "supervisor_agents.yaml"
    p.write_text(ROSTER)
    return str(p)


@pytest.fixture(autouse=True)
def clean_store():
    """Each test starts from an empty config DB — seeding is about what lands in the store."""
    config_store.reset_config_db()
    yield
    config_store.reset_config_db()


@pytest.mark.asyncio
async def test_seeds_sub_agents_and_supervisor(roster_file):
    count, tally = await roster_seed.seed_roster(roster_file)

    assert count == 3, "2 sub-agents + the supervisor"
    assert tally["created"] == 3

    sup, _ = await config_store.load_config(None, "cuga")
    assert sup["agent"]["kind"] == "supervisor"
    assert sup["supervisor"]["subAgents"] == [
        {"kind": "internal", "ref": "pricebot"},
        {"kind": "internal", "ref": "weatherbot"},
    ]


@pytest.mark.asyncio
async def test_sub_agent_record_matches_what_the_ui_writes(roster_file):
    """`build_agents_from_stored_subagents` reads agent/tools/special_instructions — a seeded record
    has to carry exactly those, or the sub-agent loads with no tools and no instructions."""
    await roster_seed.seed_roster(roster_file)

    cfg, _ = await config_store.load_config(None, "weatherbot")

    assert cfg["agent"]["kind"] == "single"
    assert cfg["agent"]["name"] == "weatherbot"
    assert cfg["special_instructions"].startswith("You answer current-weather")
    # mcp_servers become registry-app entries under `tools`, which is the shape the UI stores
    assert cfg["tools"] == [{"name": "cuga_web"}, {"name": "cuga_geo"}]


@pytest.mark.asyncio
async def test_seeding_twice_writes_nothing(roster_file):
    """THE IDEMPOTENCE GUARD. save_config bumps to max(version)+1, so a naive re-seed would add a
    version on every container restart until the table is mostly duplicates."""
    await roster_seed.seed_roster(roster_file)
    versions_before = await config_store.list_versions("pricebot")

    count, tally = await roster_seed.seed_roster(roster_file)

    assert tally["unchanged"] == 3 and tally["created"] == 0 and tally["updated"] == 0
    assert len(await config_store.list_versions("pricebot")) == len(versions_before)


@pytest.mark.asyncio
async def test_yaml_owns_the_agents_it_names(roster_file, tmp_path):
    """Edit the file, restart, the change takes — and without inventing a new version each time."""
    await roster_seed.seed_roster(roster_file)
    before = await config_store.list_versions("pricebot")

    (tmp_path / "supervisor_agents.yaml").write_text(ROSTER.replace("crypto/stock price", "ONLY crypto"))
    _, tally = await roster_seed.seed_roster(roster_file)

    cfg, _ = await config_store.load_config(None, "pricebot")
    assert "ONLY crypto" in cfg["special_instructions"]
    assert tally["updated"] >= 1
    assert len(await config_store.list_versions("pricebot")) == len(before), "rewrote in place"


@pytest.mark.asyncio
async def test_ui_added_agents_are_left_alone(roster_file):
    """An agent the YAML never names has an id seeding does not touch. This is the half of the
    contract that lets someone add a sub-agent in the UI on a deployment that also ships a roster."""
    await config_store.save_config(
        {"agent": {"name": "my-custom-bot", "kind": "single"}, "tools": [{"name": "cuga_text"}]},
        agent_id="my-custom-bot",
    )

    await roster_seed.seed_roster(roster_file)

    cfg, _ = await config_store.load_config(None, "my-custom-bot")
    assert cfg is not None, "seeding deleted a UI-created agent"
    assert cfg["tools"] == [{"name": "cuga_text"}]


@pytest.mark.asyncio
async def test_underscored_names_survive_verbatim(tmp_path):
    """THE RENAME GUARD, found by running a real server rather than by a test.

    Slugifying the id (as the UI's create-agent does) turned `code_auditor` into `code-auditor`.
    Two things broke at once: the agent was silently RENAMED — and the name is its identity, the
    concierge routes on it and live subscriptions are armed against it — and the id no longer
    matched `agent.name`, so /run/agents returned a blank description for it, i.e. unroutable.
    """
    p = tmp_path / "r.yaml"
    p.write_text(
        "agents:\n"
        "  - name: code_auditor\n"
        "    special_instructions: You audit code.\n"
        "    mcp_servers: [{name: cuga_code}]\n"
    )

    await roster_seed.seed_roster(str(p))

    cfg, _ = await config_store.load_config(None, "code_auditor")
    assert cfg is not None, "stored under a rewritten id — the agent was renamed"
    assert cfg["agent"]["name"] == "code_auditor"

    sup, _ = await config_store.load_config(None, "cuga")
    ref = sup["supervisor"]["subAgents"][0]["ref"]
    assert ref == "code_auditor"
    # the ref IS the agent's name downstream, so a mismatch here is what blanked the description
    assert ref == cfg["agent"]["name"]


@pytest.mark.asyncio
async def test_description_key_is_accepted_too(tmp_path):
    """The YAML reader this replaces took `description` or `special_instructions`."""
    p = tmp_path / "r.yaml"
    p.write_text("agents:\n  - name: bot\n    description: It does a thing.\n")

    await roster_seed.seed_roster(str(p))

    cfg, _ = await config_store.load_config(None, "bot")
    assert cfg["special_instructions"] == "It does a thing."


@pytest.mark.asyncio
async def test_a_ui_added_sub_agent_keeps_its_membership_across_a_reseed(roster_file):
    """THE RESTART GUARD, and the whole point of "use the YAML, or use the UI".

    Seeding rewrites the supervisor from the file. Doing that verbatim dropped UI-added sub-agents
    from `subAgents` on every boot — the agent's own record survived (the YAML never names its id),
    but it was orphaned: in the registry, out of the supervisor, reachable by nobody. Caught by
    restarting a real server against Postgres, not by a unit test.
    """
    await roster_seed.seed_roster(roster_file)

    # what the Studio does: publish an agent, then add it to the supervisor
    await config_store.save_config(
        {"agent": {"name": "my-custom-bot", "kind": "single"}, "tools": [{"name": "cuga_text"}]},
        agent_id="my-custom-bot",
    )
    sup, _ = await config_store.load_config(None, "cuga")
    sup["supervisor"]["subAgents"].append({"kind": "internal", "ref": "my-custom-bot"})
    await config_store.save_config(sup, agent_id="cuga")

    await roster_seed.seed_roster(roster_file)  # the restart

    sup, _ = await config_store.load_config(None, "cuga")
    refs = [s["ref"] for s in sup["supervisor"]["subAgents"]]
    assert refs == ["pricebot", "weatherbot", "my-custom-bot"], (
        "YAML entries keep file order and the UI addition is appended"
    )


@pytest.mark.asyncio
async def test_a_ui_added_a2a_sub_agent_also_survives(roster_file):
    """`a2a` entries have no `ref` — matching on ref alone would silently drop them."""
    await roster_seed.seed_roster(roster_file)
    sup, _ = await config_store.load_config(None, "cuga")
    sup["supervisor"]["subAgents"].append(
        {"kind": "a2a", "name": "remote-helper", "endpoint": "https://example.invalid/a2a"}
    )
    await config_store.save_config(sup, agent_id="cuga")

    await roster_seed.seed_roster(roster_file)

    sup, _ = await config_store.load_config(None, "cuga")
    assert any(s.get("name") == "remote-helper" for s in sup["supervisor"]["subAgents"])


@pytest.mark.asyncio
async def test_dropping_an_agent_from_the_yaml_removes_it(roster_file, tmp_path):
    """The other half of ownership: the file controls its OWN entries, including removal."""
    await roster_seed.seed_roster(roster_file)

    (tmp_path / "supervisor_agents.yaml").write_text(
        ROSTER.split("  - name: weatherbot")[0]  # drop weatherbot
    )
    await roster_seed.seed_roster(roster_file)

    sup, _ = await config_store.load_config(None, "cuga")
    assert [s["ref"] for s in sup["supervisor"]["subAgents"]] == ["pricebot"]


@pytest.mark.asyncio
async def test_no_roster_configured_is_a_no_op():
    assert await roster_seed.seed_roster("") == (0, {})


@pytest.mark.asyncio
async def test_a_missing_file_does_not_raise(tmp_path):
    """Startup calls this. A bad path must degrade to 'no supervisor', not stop the server."""
    count, tally = await roster_seed.seed_roster(str(tmp_path / "nope.yaml"))

    assert count == 0 and tally == {}


@pytest.mark.asyncio
async def test_a_sub_agent_may_not_squat_on_the_supervisor_id(tmp_path):
    """A roster entry literally named `cuga` would otherwise overwrite the supervisor record with a
    `kind: single` one, and /run would stop seeing a supervisor at all."""
    p = tmp_path / "r.yaml"
    p.write_text("agents:\n  - name: cuga\n    mcp_servers: [{name: cuga_web}]\n  - name: pricebot\n")

    await roster_seed.seed_roster(str(p))

    sup, _ = await config_store.load_config(None, "cuga")
    assert sup["agent"]["kind"] == "supervisor"
    assert sup["supervisor"]["subAgents"] == [{"kind": "internal", "ref": "pricebot"}]


@pytest.mark.asyncio
async def test_an_edit_made_in_manage_survives_a_restart(roster_file):
    """SAMI'S POINT F. Seeding overwrote whenever `existing != config` — which is precisely the
    shape of "a human changed it". So every restart silently reverted the Manage UI to the YAML and
    the edit looked like it had never been saved.

    Each seeded record now carries a hash of what the roster wrote. A stored config that no longer
    matches that hash was edited by somebody, and seeding defers to them.
    """
    await roster_seed.seed_roster(roster_file)

    # a human edits pricebot in Manage
    cfg, _ = await config_store.load_config(None, "pricebot")
    cfg["special_instructions"] = "EDITED BY A HUMAN IN MANAGE"
    await config_store.save_config(cfg, agent_id="pricebot")

    _, tally = await roster_seed.seed_roster(roster_file)  # restart

    after, _ = await config_store.load_config(None, "pricebot")
    assert after["special_instructions"] == "EDITED BY A HUMAN IN MANAGE", "the restart clobbered it"
    assert tally["skipped"] >= 1


@pytest.mark.asyncio
async def test_an_untouched_agent_still_self_heals_from_the_file(roster_file, tmp_path):
    """The other half — deferring to humans must not stop the roster being the source of truth for
    agents nobody has touched. A container replace has to restore them."""
    await roster_seed.seed_roster(roster_file)

    # nobody edits anything; the FILE changes
    (tmp_path / "supervisor_agents.yaml").write_text(ROSTER.replace("crypto/stock price", "ONLY crypto"))
    _, tally = await roster_seed.seed_roster(roster_file)

    cfg, _ = await config_store.load_config(None, "pricebot")
    assert "ONLY crypto" in cfg["special_instructions"]
    assert tally["updated"] >= 1 and tally["skipped"] == 0
