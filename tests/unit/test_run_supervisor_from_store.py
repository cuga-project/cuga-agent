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
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from cuga.backend.server import config_store, run_routes

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    config_store.reset_config_db()
    run_routes._supervisor_shutting_down = False
    run_routes._supervisor_drained.set()
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
async def test_renamed_member_keeps_stable_roster_identity_and_description():
    await _store_supervisor(["pricebot"])
    cfg, _ = await config_store.load_config(None, "pricebot")
    cfg["agent"]["name"] = "Price Specialist"
    await config_store.save_config(cfg, agent_id="pricebot")

    details = await run_routes._roster_details(["pricebot"])

    assert details["pricebot"]["description"] == "You are pricebot."


@pytest.mark.asyncio
async def test_child_invalidation_retries_inflight_build_and_preserves_active_lease(monkeypatch):
    await _store_supervisor(["weatherbot"])
    started, release = asyncio.Event(), asyncio.Event()
    snapshots = []
    owners = []

    async def build(subs, **kwargs):
        cfg, _ = await config_store.load_config(None, "weatherbot")
        snapshots.append(cfg["special_instructions"])
        if len(snapshots) == 1:
            started.set()
            await release.wait()
        return await _fake_agents(subs)

    class Owner:
        def __init__(self, **kwargs):
            self.closed = False
            owners.append(self)

        async def aclose(self):
            self.closed = True

    monkeypatch.setattr("cuga.supervisor_utils.supervisor_config.build_agents_from_stored_subagents", build)
    monkeypatch.setattr("cuga.sdk.CugaSupervisor", Owner)
    pending = asyncio.create_task(run_routes._get_supervisor(retain=True))
    await started.wait()
    cfg, _ = await config_store.load_config(None, "weatherbot")
    cfg["special_instructions"] = "updated child"
    await config_store.save_config(cfg, agent_id="weatherbot")
    await run_routes.invalidate_cached_supervisor()
    release.set()
    current = await pending

    assert snapshots == ["You are weatherbot.", "updated child"]
    assert owners[0].closed
    assert current is owners[1]
    assert current._cuga_roster[0]["description"] == "updated child"
    await run_routes.invalidate_cached_supervisor()
    assert not current.closed
    assert current._cuga_roster[0]["name"] == "weatherbot"
    await run_routes._release_supervisor(current)
    assert current.closed


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "extra,status",
    [
        ({"agent": "deleted-agent"}, 404),
        ({"use_draft": True}, 422),
        ({"attachments": [{"name": "file"}]}, 422),
        (
            {
                "action_response": {
                    "action_id": "approve",
                    "confirmed": True,
                    "response_type": "confirmation",
                    "timestamp": "2026-10-08T00:00:00Z",
                    "user_id": "test",
                }
            },
            422,
        ),
    ],
)
async def test_unsupported_or_missing_target_never_invokes_different_config(monkeypatch, extra, status):
    from fastapi import FastAPI
    import httpx

    owner = SimpleNamespace(invoke=AsyncMock())
    release = AsyncMock()
    monkeypatch.setattr(run_routes, "_run_auth_failure", AsyncMock(return_value=None))
    monkeypatch.setattr(run_routes, "_authenticated_user_id", AsyncMock(return_value=None))
    monkeypatch.setattr(run_routes, "_ASSERT_THREAD_ACCESS", AsyncMock())
    monkeypatch.setattr(run_routes, "_get_supervisor", AsyncMock(return_value=owner))
    monkeypatch.setattr(run_routes, "_release_supervisor", release)
    monkeypatch.setattr(run_routes.events_bridge, "forwards_to_events", lambda *_: False)
    run_routes._supervisor_roster["__current__"] = [{"name": "weatherbot"}]
    app = FastAPI()
    app.add_api_route("/run", run_routes.run_sync, methods=["POST"])
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/run", json={"query": "test", **extra})

    assert response.status_code == status
    owner.invoke.assert_not_awaited()
    release.assert_awaited_once_with(owner)


@pytest.mark.asyncio
async def test_agent_listing_uses_owner_snapshot_after_invalidation(monkeypatch):
    owner = SimpleNamespace(_cuga_roster=[{"name": "weatherbot", "description": "Weather specialist"}])
    monkeypatch.setattr(run_routes, "_run_auth_failure", AsyncMock(return_value=None))

    async def get_then_invalidate():
        run_routes._supervisor_roster.clear()
        return owner

    monkeypatch.setattr(run_routes, "_get_supervisor", get_then_invalidate)

    response = await run_routes.run_agents(SimpleNamespace())

    assert [entry["name"] for entry in response["agents"]] == ["cuga", "weatherbot"]


@pytest.mark.asyncio
async def test_supervisor_uses_editor_instructions_and_configured_model(monkeypatch):
    await _store_supervisor(["weatherbot"])
    config, _ = await config_store.load_config(None, "cuga")
    config.update(special_instructions="edited instructions", llm={"model": "parent-model"})
    config["supervisor"]["special_instructions"] = "old nested instructions"
    await config_store.save_config(config, agent_id="cuga")
    monkeypatch.setattr(
        "cuga.supervisor_utils.supervisor_config.build_agents_from_stored_subagents",
        lambda subs, **kw: _fake_agents(subs),
    )
    from unittest.mock import Mock

    model = object()
    factory = Mock(return_value=SimpleNamespace())
    create_model = Mock(return_value=model)
    monkeypatch.setattr("cuga.backend.llm.models.create_llm_from_config", create_model)
    monkeypatch.setattr("cuga.sdk.CugaSupervisor", factory)

    await run_routes._get_supervisor()

    assert factory.call_args.kwargs["special_instructions"] == "edited instructions"
    assert factory.call_args.kwargs["model"] is model
    create_model.assert_called_once_with({"model": "parent-model"})


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
    first_started = asyncio.Event()
    build_calls = 0
    instances = []

    async def blocked_agents(subs, **kwargs):
        nonlocal build_calls
        build_calls += 1
        first_started.set()
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
    await first_started.wait()  # first build is running; second is waiting on the lock
    release_build.set()
    first_result, second_result = await asyncio.gather(first, second)

    assert first_result is second_result
    assert not first_result.closed
    assert len(instances) == 1


@pytest.mark.asyncio
async def test_stale_reader_cannot_replace_newer_published_supervisor(monkeypatch):
    await _store_supervisor(["pricebot"])
    original_load = config_store.load_config
    stale_read_ready = asyncio.Event()
    release_stale_read = asyncio.Event()
    first_read = True

    async def delayed_first_load(version=None, agent_id="cuga-default"):
        nonlocal first_read
        result = await original_load(version, agent_id)
        if agent_id == "cuga" and version is None and first_read:
            first_read = False
            stale_read_ready.set()
            await release_stale_read.wait()
        return result

    built = []

    class _FakeSup:
        def __init__(self, **kwargs):
            self.agent_names = sorted(kwargs["agents"])
            self.closed = False
            built.append(self)

        async def aclose(self):
            self.closed = True

    monkeypatch.setattr(config_store, "load_config", delayed_first_load)
    monkeypatch.setattr(
        "cuga.supervisor_utils.supervisor_config.build_agents_from_stored_subagents",
        lambda subs, **kwargs: _fake_agents(subs),
    )
    import cuga.sdk as _sdk

    monkeypatch.setattr(_sdk, "CugaSupervisor", _FakeSup)
    stale_reader = asyncio.create_task(run_routes._get_supervisor())
    await stale_read_ready.wait()
    await _store_supervisor(["pricebot", "weatherbot"])
    newer = await run_routes._get_supervisor()
    release_stale_read.set()
    stale_result = await stale_reader

    assert stale_result is newer
    assert newer.agent_names == ["pricebot", "weatherbot"]
    assert not newer.closed
    assert list(run_routes._supervisor_cache.values()) == [newer]


@pytest.mark.asyncio
async def test_cancel_after_construction_closes_unpublished_candidate_without_lease(monkeypatch):
    await _store_supervisor(["pricebot"])
    constructed = asyncio.Event()
    release_details = asyncio.Event()
    instances = []

    class _FakeSup:
        def __init__(self, **kwargs):
            self.closed = False
            instances.append(self)
            constructed.set()

        async def aclose(self):
            self.closed = True

    async def blocked_details(_refs):
        await release_details.wait()
        return {}

    monkeypatch.setattr(
        "cuga.supervisor_utils.supervisor_config.build_agents_from_stored_subagents",
        lambda subs, **kwargs: _fake_agents(subs),
    )
    monkeypatch.setattr(run_routes, "_roster_details", blocked_details)
    import cuga.sdk as _sdk

    monkeypatch.setattr(_sdk, "CugaSupervisor", _FakeSup)
    request = asyncio.create_task(run_routes._get_supervisor(retain=True))
    await constructed.wait()
    request.cancel()
    with pytest.raises(asyncio.CancelledError):
        await request

    assert len(instances) == 1
    assert instances[0].closed
    assert run_routes._supervisor_cache == {}
    assert run_routes._supervisor_active_users == {}


@pytest.mark.asyncio
async def test_cancellation_during_stale_close_keeps_new_cache_and_finishes_retirement(monkeypatch):
    await _store_supervisor(["pricebot"])
    close_started = asyncio.Event()
    finish_close = asyncio.Event()
    instances = []

    class _FakeSup:
        def __init__(self, **kwargs):
            self.closed = False
            self.block_close = bool(instances)
            instances.append(self)

        async def aclose(self):
            if not self.block_close:
                close_started.set()
                await finish_close.wait()
            self.closed = True

    monkeypatch.setattr(
        "cuga.supervisor_utils.supervisor_config.build_agents_from_stored_subagents",
        lambda subs, **kwargs: _fake_agents(subs),
    )
    import cuga.sdk as _sdk

    monkeypatch.setattr(_sdk, "CugaSupervisor", _FakeSup)
    old = await run_routes._get_supervisor()
    old.block_close = False
    await _store_supervisor(["pricebot", "weatherbot"])
    replacement_request = asyncio.create_task(run_routes._get_supervisor())
    await close_started.wait()
    replacement_request.cancel()
    finish_close.set()
    with pytest.raises(asyncio.CancelledError):
        await replacement_request
    await asyncio.sleep(0)

    assert old.closed
    assert len(run_routes._supervisor_cache) == 1
    replacement = next(iter(run_routes._supervisor_cache.values()))
    assert replacement is not old
    assert not replacement.closed


@pytest.mark.asyncio
async def test_cancel_immediately_after_publication_rolls_back_lease(monkeypatch):
    await _store_supervisor(["pricebot"])
    close_started = asyncio.Event()
    finish_close = asyncio.Event()

    class _FakeSup:
        def __init__(self, **kwargs):
            pass

        async def aclose(self):
            close_started.set()
            await finish_close.wait()

    monkeypatch.setattr(
        "cuga.supervisor_utils.supervisor_config.build_agents_from_stored_subagents",
        lambda subs, **kwargs: _fake_agents(subs),
    )
    import cuga.sdk as _sdk

    monkeypatch.setattr(_sdk, "CugaSupervisor", _FakeSup)
    await run_routes._get_supervisor()
    await _store_supervisor(["pricebot", "weatherbot"])
    request = asyncio.create_task(run_routes._get_supervisor(retain=True))
    await close_started.wait()
    request.cancel()
    finish_close.set()
    with pytest.raises(asyncio.CancelledError):
        await request

    assert run_routes._supervisor_active_users == {}
    assert len(run_routes._supervisor_cache) == 1


@pytest.mark.asyncio
async def test_shutdown_waits_for_active_lease_before_closing_owner(monkeypatch):
    await _store_supervisor(["pricebot"])
    close_started = asyncio.Event()
    finish_close = asyncio.Event()
    closed = asyncio.Event()

    class _FakeSup:
        def __init__(self, **kwargs):
            pass

        async def aclose(self):
            close_started.set()
            await finish_close.wait()
            closed.set()

    monkeypatch.setattr(
        "cuga.supervisor_utils.supervisor_config.build_agents_from_stored_subagents",
        lambda subs, **kwargs: _fake_agents(subs),
    )
    import cuga.sdk as _sdk

    monkeypatch.setattr(_sdk, "CugaSupervisor", _FakeSup)
    active = await run_routes._get_supervisor(retain=True)
    shutdown = asyncio.create_task(run_routes.close_cached_supervisors())
    await asyncio.sleep(0)

    assert not closed.is_set()
    assert not shutdown.done()
    release = asyncio.create_task(run_routes._release_supervisor(active))
    await close_started.wait()
    await asyncio.sleep(0)
    assert not shutdown.done()
    finish_close.set()
    await release
    await shutdown
    assert closed.is_set()
    assert run_routes._supervisor_active_users == {}


async def _fake_agents(subs):
    return {s["ref"]: object() for s in subs if s.get("ref")}
