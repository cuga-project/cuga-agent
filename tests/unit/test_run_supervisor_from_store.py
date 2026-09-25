"""``/run`` builds its supervisor from the STORE, so the UI and the roster cannot disagree.

This is the other half of the roster convergence (see test_roster_seed.py). ``_get_supervisor``
used to parse ``CUGA_SUPERVISOR_ROSTER`` directly; it now reads the seeded records, which is what
lets a UI-added sub-agent appear in ``/run/agents`` and what makes a UI edit take effect without a
restart.

The cache is the subtle part. It used to be keyed on the roster's FILE PATH, which never changes —
correct when the roster was an immutable file, silently wrong once the UI can edit it, because
``/run`` would serve the first supervisor it ever built for the life of the process. It is now keyed
on the stored config version.
"""

from __future__ import annotations

import asyncio

import pytest

from cuga.backend.server import config_store, run_routes

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    config_store.reset_config_db()
    run_routes._supervisor_cache.clear()
    run_routes._supervisor_active_users.clear()
    run_routes._supervisor_retired.clear()
    run_routes._supervisor_roster.clear()
    # The store is the source of truth now; no env var is needed to be a supervisor.
    monkeypatch.delenv("CUGA_SUPERVISOR_ROSTER", raising=False)
    yield
    config_store.reset_config_db()
    run_routes._supervisor_cache.clear()
    run_routes._supervisor_active_users.clear()
    run_routes._supervisor_retired.clear()
    run_routes._supervisor_roster.clear()


async def _store_supervisor(refs):
    for ref in refs:
        await config_store.save_config(
            {
                "agent": {"name": ref, "kind": "single", "description": f"the {ref} specialist"},
                "tools": [{"name": "cuga_web"}],
                "special_instructions": f"You are {ref}.",
            },
            agent_id=ref,
        )
    await config_store.save_config(
        {
            "agent": {"name": "cuga", "kind": "supervisor"},
            "supervisor": {"subAgents": [{"kind": "internal", "ref": r} for r in refs]},
        },
        agent_id="cuga",
    )


@pytest.mark.asyncio
async def test_no_stored_supervisor_means_not_a_supervisor():
    """Vanilla CUGA: nothing seeded, nothing composed. Must be None, not an empty supervisor."""
    assert await run_routes._get_supervisor() is None


@pytest.mark.asyncio
async def test_a_single_kind_agent_is_not_a_supervisor():
    """`cuga` existing is not enough — it has to be kind=supervisor. Guards against a roster entry
    named `cuga` turning the server into a supervisor over nothing."""
    await config_store.save_config({"agent": {"name": "cuga", "kind": "single"}}, agent_id="cuga")

    assert await run_routes._get_supervisor() is None


@pytest.mark.asyncio
async def test_details_come_from_the_stored_config():
    """/run/agents answers "what is loaded here?" — the concierge routes on these descriptions, so a
    blank one makes a specialist unroutable."""
    await _store_supervisor(["pricebot"])

    details = await run_routes._roster_details(["pricebot"])

    assert details["pricebot"]["description"] == "You are pricebot."
    assert details["pricebot"]["mcp_servers"] == ["cuga_web"]


@pytest.mark.asyncio
async def test_details_skip_a_dangling_ref():
    """A subAgent pointing at a deleted agent must not break the listing."""
    details = await run_routes._roster_details(["ghost"])

    assert details == {}


@pytest.mark.asyncio
async def test_close_graph_owners_closes_each_instance_once():
    closed = []

    class _Graph:
        async def aclose(self):
            closed.append(self)

    first = _Graph()
    second = _Graph()

    await run_routes.close_graph_owners(first, None, first, second)

    assert closed == [first, second]


@pytest.mark.asyncio
async def test_close_cached_supervisors_closes_each_instance_once():
    closed = []

    class _FakeSup:
        async def aclose(self):
            closed.append(self)

    first = _FakeSup()
    second = _FakeSup()
    run_routes._supervisor_cache.update({"v1": first, "v2": second, "alias": first})
    run_routes._supervisor_roster.update({"v1": [], "__current__": []})

    await run_routes.close_cached_supervisors()

    assert closed == [first, second]
    assert run_routes._supervisor_cache == {}
    assert run_routes._supervisor_roster == {}


@pytest.mark.asyncio
async def test_cache_key_follows_the_stored_version(monkeypatch):
    """THE STALENESS GUARD. Publishing a new supervisor config must invalidate the cache; a
    path-keyed cache would have served the old supervisor until the process restarted."""
    await _store_supervisor(["pricebot"])
    _, v1 = await config_store.load_config(None, "cuga")

    built = []
    closed = []

    class _FakeSup:
        def __init__(self, **kw):
            assert kw["interactive"] is False
            self.agent_names = sorted((kw.get("agents") or {}).keys())
            built.append(self.agent_names)

        async def aclose(self):
            closed.append(self.agent_names)

    monkeypatch.setattr(
        "cuga.supervisor_utils.supervisor_config.build_agents_from_stored_subagents",
        lambda subs, **kw: _fake_agents(subs),
    )
    import cuga.sdk as _sdk

    monkeypatch.setattr(_sdk, "CugaSupervisor", _FakeSup)

    await run_routes._get_supervisor()
    await run_routes._get_supervisor()
    assert len(built) == 1, "second call should have hit the cache"

    # A UI edit: add a sub-agent and publish. New version → new cache key → rebuild.
    await _store_supervisor(["pricebot", "weatherbot"])
    _, v2 = await config_store.load_config(None, "cuga")
    assert v2 != v1

    await run_routes._get_supervisor()
    assert len(built) == 2, "a published edit must invalidate the cache"
    assert built[1] == ["pricebot", "weatherbot"]
    assert closed == [["pricebot"]], "replaced supervisors must release pending ACP delegations"


@pytest.mark.asyncio
async def test_replacement_defers_close_until_active_run_releases(monkeypatch):
    await _store_supervisor(["pricebot"])
    closed = []

    class _FakeSup:
        def __init__(self, **kwargs):
            self.version = len(closed)

        async def aclose(self):
            closed.append(self)

    monkeypatch.setattr(
        "cuga.supervisor_utils.supervisor_config.build_agents_from_stored_subagents",
        lambda subs, **kwargs: _fake_agents(subs),
    )
    import cuga.sdk as _sdk

    monkeypatch.setattr(_sdk, "CugaSupervisor", _FakeSup)
    active = await run_routes._get_supervisor(retain=True)
    await _store_supervisor(["pricebot", "weatherbot"])
    replacement = await run_routes._get_supervisor()

    assert replacement is not active
    assert closed == []
    await run_routes._release_supervisor(active)
    assert closed == [active]


@pytest.mark.asyncio
async def test_concurrent_cache_misses_publish_one_live_supervisor(monkeypatch):
    await _store_supervisor(["pricebot"])
    release_build = asyncio.Event()
    both_building = asyncio.Event()
    build_calls = 0
    instances = []

    async def blocked_agents(subs, **kwargs):
        nonlocal build_calls
        build_calls += 1
        if build_calls == 2:
            both_building.set()
        await release_build.wait()
        return await _fake_agents(subs)

    class _FakeSup:
        def __init__(self, **kwargs):
            self.closed = False
            instances.append(self)

        async def aclose(self):
            self.closed = True

    monkeypatch.setattr(
        "cuga.supervisor_utils.supervisor_config.build_agents_from_stored_subagents",
        blocked_agents,
    )
    import cuga.sdk as _sdk

    monkeypatch.setattr(_sdk, "CugaSupervisor", _FakeSup)
    first = asyncio.create_task(run_routes._get_supervisor())
    second = asyncio.create_task(run_routes._get_supervisor())
    try:
        await asyncio.wait_for(both_building.wait(), timeout=0.2)
    except TimeoutError:
        pass
    release_build.set()
    first_result, second_result = await asyncio.gather(first, second)

    assert first_result is second_result
    assert not first_result.closed
    assert len(instances) == 1


async def _fake_agents(subs):
    return {s["ref"]: object() for s in subs if s.get("ref")}
