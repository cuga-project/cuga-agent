"""Regression test: concurrent draft PATCHes to different sections must
not clobber each other's changes.

Bug recap: a user clicked Use on the Watsonx env-preset (autosave fired
PATCH /draft/knowledge), then typed in a different field whose autosave
fires PATCH /draft/tools. The two handlers' load-modify-write in
``_load_and_patch_draft`` interleaved:

    PATCH knowledge: load(draft_v0)   → set knowledge=watsonx → save(v0+watsonx)
    PATCH tools:     load(draft_v0)   → set tools=new         → save(v0+tools) ← knowledge LOST

Result: engine on watsonx (the apply succeeded), DB still on fastembed
(the second save overwrote the knowledge update). User-visible symptom:
"I switched to Watsonx but reindex still used fastembed."

The fix is a per-agent asyncio.Lock that serializes the LMW so the
second reader sees the first writer's changes.
"""

from __future__ import annotations

import asyncio


def test_concurrent_cross_section_patches_preserve_all_sections(monkeypatch):
    """All three concurrent PATCHes survive — the lock prevents one
    section's write from clobbering another section's update."""
    from cuga.backend.server import config_store as cs
    from cuga.backend.server import manage_routes

    state: dict = {}

    async def fake_load(agent_id: str = "cuga-default") -> dict:
        # Widen the read window to deterministically expose any LMW race.
        await asyncio.sleep(0.01)
        return dict(state)

    async def fake_save(cfg: dict, agent_id: str = "cuga-default") -> None:
        state.clear()
        state.update(cfg)

    monkeypatch.setattr(cs, "load_draft", fake_load)
    monkeypatch.setattr(cs, "save_draft", fake_save)
    # Reset per-agent locks so the test doesn't share state across runs.
    manage_routes._AGENT_DRAFT_LOCKS.clear()

    async def run() -> None:
        state["knowledge"] = {"embedding_provider": "fastembed"}
        state["tools"] = []
        await asyncio.gather(
            manage_routes._load_and_patch_draft(
                "agentA",
                "knowledge",
                {"embedding_provider": "litellm", "embedding_model": "watsonx/x"},
            ),
            manage_routes._load_and_patch_draft("agentA", "tools", [{"name": "mcp_x"}]),
            manage_routes._load_and_patch_draft("agentA", "llm", {"model": "groq/y"}),
        )

    asyncio.run(run())

    assert state["knowledge"]["embedding_provider"] == "litellm", (
        f"knowledge clobbered: {state.get('knowledge')!r}"
    )
    assert state["knowledge"]["embedding_model"] == "watsonx/x"
    assert state["tools"] == [{"name": "mcp_x"}]
    assert state["llm"]["model"] == "groq/y"


def test_lmw_lock_holds_under_repeated_contention(monkeypatch):
    """50 rounds of concurrent two-section PATCHes — no flakiness."""
    from cuga.backend.server import config_store as cs
    from cuga.backend.server import manage_routes

    state: dict = {}

    async def fake_load(agent_id: str = "cuga-default") -> dict:
        await asyncio.sleep(0.005)
        return dict(state)

    async def fake_save(cfg: dict, agent_id: str = "cuga-default") -> None:
        state.clear()
        state.update(cfg)

    monkeypatch.setattr(cs, "load_draft", fake_load)
    monkeypatch.setattr(cs, "save_draft", fake_save)
    manage_routes._AGENT_DRAFT_LOCKS.clear()

    async def one_round(i: int) -> None:
        state.clear()
        state["knowledge"] = {"embedding_provider": "fastembed"}
        await asyncio.gather(
            manage_routes._load_and_patch_draft("agentA", "knowledge", {"embedding_provider": "litellm"}),
            manage_routes._load_and_patch_draft("agentA", "tools", [{"iter": i}]),
        )
        assert state["knowledge"]["embedding_provider"] == "litellm", f"iter {i}: knowledge lost"
        assert state["tools"] == [{"iter": i}], f"iter {i}: tools lost"

    async def run() -> None:
        for i in range(50):
            await one_round(i)

    asyncio.run(run())


def test_per_agent_locks_do_not_serialize_different_agents(monkeypatch):
    """Two different agents must NOT block each other — separate locks.

    Without per-agent isolation, a slow PATCH on agentB would hold up
    fast PATCHes on agentA. The lock-per-agent design avoids this.
    """
    from cuga.backend.server import config_store as cs
    from cuga.backend.server import manage_routes

    state: dict = {}

    async def fake_load(agent_id: str = "cuga-default") -> dict:
        return dict(state.get(agent_id, {}))

    async def fake_save(cfg: dict, agent_id: str = "cuga-default") -> None:
        state[agent_id] = dict(cfg)

    monkeypatch.setattr(cs, "load_draft", fake_load)
    monkeypatch.setattr(cs, "save_draft", fake_save)
    manage_routes._AGENT_DRAFT_LOCKS.clear()

    async def run() -> None:
        await asyncio.gather(
            manage_routes._load_and_patch_draft("agentA", "knowledge", {"v": 1}),
            manage_routes._load_and_patch_draft("agentB", "knowledge", {"v": 2}),
        )

    asyncio.run(run())

    # Both agents got their writes; per-agent locks gave them parallel paths.
    assert state["agentA"]["knowledge"] == {"v": 1}
    assert state["agentB"]["knowledge"] == {"v": 2}
    # And the lock dict tracked both as distinct.
    assert "agentA" in manage_routes._AGENT_DRAFT_LOCKS
    assert "agentB" in manage_routes._AGENT_DRAFT_LOCKS
    assert manage_routes._AGENT_DRAFT_LOCKS["agentA"] is not manage_routes._AGENT_DRAFT_LOCKS["agentB"]
