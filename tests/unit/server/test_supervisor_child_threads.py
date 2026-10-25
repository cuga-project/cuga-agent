"""Supervisor child threads on the shared saver (#774)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.unit


async def test_conversation_scoped_subagent_gets_shared_saver():
    """build_agents_from_list passes the shared saver to conversation-scoped agents."""
    sentinel = object()
    captured_kwargs: list[dict] = []

    class FakeCugaAgent:
        def __init__(self, **kwargs):
            captured_kwargs.append(dict(kwargs))
            self._feature_overrides = {}
            self._memory_scope = None

    with patch("cuga.supervisor_utils.supervisor_config.CugaAgent", FakeCugaAgent, create=True):
        # The import is done lazily inside the function body with `from cuga.sdk import CugaAgent`.
        # Patch that source so the lazy import picks up our fake.
        with patch("cuga.sdk.CugaAgent", FakeCugaAgent):
            from cuga.supervisor_utils.supervisor_config import build_agents_from_list

            await build_agents_from_list(
                [
                    {
                        "name": "agent1",
                        "memory_scope": "conversation",
                        "tools": [],
                        "apps": [],
                        "mcp_servers": [],
                    }
                ],
                checkpointer=sentinel,
            )

    assert len(captured_kwargs) == 1
    assert captured_kwargs[0].get("checkpointer") is sentinel


async def test_call_scoped_subagent_does_not_get_shared_saver():
    """Call-scoped agents are NOT given the shared saver."""
    sentinel = object()
    captured_kwargs: list[dict] = []

    class FakeCugaAgent:
        def __init__(self, **kwargs):
            captured_kwargs.append(dict(kwargs))
            self._feature_overrides = {}
            self._memory_scope = None

    with patch("cuga.sdk.CugaAgent", FakeCugaAgent):
        from cuga.supervisor_utils.supervisor_config import build_agents_from_list

        await build_agents_from_list(
            [
                {
                    "name": "agent1",
                    "memory_scope": "call",
                    "tools": [],
                    "apps": [],
                    "mcp_servers": [],
                }
            ],
            checkpointer=sentinel,
        )

    assert len(captured_kwargs) == 1
    assert captured_kwargs[0].get("checkpointer") is None


async def test_no_checkpointer_never_passed_when_caller_omits_it():
    """When checkpointer=None (default), no saver is passed regardless of scope."""
    captured_kwargs: list[dict] = []

    class FakeCugaAgent:
        def __init__(self, **kwargs):
            captured_kwargs.append(dict(kwargs))
            self._feature_overrides = {}
            self._memory_scope = None

    with patch("cuga.sdk.CugaAgent", FakeCugaAgent):
        from cuga.supervisor_utils.supervisor_config import build_agents_from_list

        await build_agents_from_list(
            [
                {
                    "name": "agent1",
                    "memory_scope": "conversation",
                    "tools": [],
                    "apps": [],
                    "mcp_servers": [],
                }
            ],
            # no checkpointer keyword — default None
        )

    assert len(captured_kwargs) == 1
    assert captured_kwargs[0].get("checkpointer") is None


async def test_delete_child_checkpoints_deletes_conversation_scoped():
    """Deleting a conversation also deletes conversation-scoped child checkpoints."""
    from cuga.backend.server.main import _delete_child_checkpoints

    saver = MagicMock()
    saver.adelete_thread = AsyncMock()
    saver.aget_tuple = AsyncMock(return_value=None)

    config = {"supervisor": {"subAgents": [{"kind": "internal", "ref": "search-agent"}]}}
    # Sub-agent has no explicit scope → normalises to "conversation" (the default)
    sub_config: dict = {}

    with patch("cuga.backend.server.config_store.load_config", new_callable=AsyncMock) as mock_load:
        mock_load.side_effect = [
            (config, 1),  # main agent config
            (sub_config, 1),  # sub-agent config
        ]
        with patch("cuga.config.get_tenant_id", return_value="t1"):
            await _delete_child_checkpoints(
                saver=saver,
                agent_id="supervisor-agent",
                conversation_id="thread-123",
                user_id="user-456",
            )

    assert saver.adelete_thread.await_count == 1
    called_key = saver.adelete_thread.await_args[0][0]
    assert called_key.startswith("sup_child_")


async def test_delete_child_checkpoints_skips_call_scoped():
    """Call-scoped sub-agents are NOT deleted when the parent conversation is removed."""
    from cuga.backend.server.main import _delete_child_checkpoints

    saver = MagicMock()
    saver.adelete_thread = AsyncMock()
    saver.aget_tuple = AsyncMock(return_value=None)

    config = {"supervisor": {"subAgents": [{"kind": "internal", "ref": "ephemeral-agent"}]}}
    sub_config = {
        "feature_flags": {"sub_agent_memory_scope": "call"},
    }

    with patch("cuga.backend.server.config_store.load_config", new_callable=AsyncMock) as mock_load:
        mock_load.side_effect = [
            (config, 1),
            (sub_config, 1),
        ]
        with patch("cuga.config.get_tenant_id", return_value="t1"):
            await _delete_child_checkpoints(
                saver=saver,
                agent_id="supervisor-agent",
                conversation_id="thread-123",
                user_id="user-456",
            )

    saver.adelete_thread.assert_not_awaited()


async def test_delete_child_checkpoints_skips_non_internal_entries():
    """External (A2A/ACP) sub-agent entries are silently skipped."""
    from cuga.backend.server.main import _delete_child_checkpoints

    saver = MagicMock()
    saver.adelete_thread = AsyncMock()
    saver.aget_tuple = AsyncMock(return_value=None)

    config = {
        "supervisor": {
            "subAgents": [
                {"kind": "a2a", "name": "ext", "endpoint": "http://example.com"},
            ]
        }
    }

    with patch("cuga.backend.server.config_store.load_config", new_callable=AsyncMock) as mock_load:
        mock_load.return_value = (config, 1)
        with patch("cuga.config.get_tenant_id", return_value="t1"):
            await _delete_child_checkpoints(
                saver=saver,
                agent_id="supervisor-agent",
                conversation_id="thread-123",
                user_id="user-456",
            )

    saver.adelete_thread.assert_not_awaited()


async def test_delete_child_checkpoints_tolerates_missing_agent_config():
    """If the parent agent config is absent, the function returns without error."""
    from cuga.backend.server.main import _delete_child_checkpoints

    saver = MagicMock()
    saver.adelete_thread = AsyncMock()
    saver.aget_tuple = AsyncMock(return_value=None)

    with patch("cuga.backend.server.config_store.load_config", new_callable=AsyncMock) as mock_load:
        mock_load.return_value = (None, None)
        with patch("cuga.config.get_tenant_id", return_value="t1"):
            await _delete_child_checkpoints(
                saver=saver,
                agent_id="no-such-agent",
                conversation_id="thread-123",
                user_id="user-456",
            )

    saver.adelete_thread.assert_not_awaited()


async def test_delete_child_checkpoints_propagates_saver_errors():
    """A saver failure must propagate so the delete endpoint returns 500 and stays retriable."""
    import pytest
    from cuga.backend.server.main import _delete_child_checkpoints

    saver = MagicMock()
    saver.adelete_thread = AsyncMock(side_effect=RuntimeError("saver unavailable"))
    saver.aget_tuple = AsyncMock(return_value=None)

    config = {"supervisor": {"subAgents": [{"kind": "internal", "ref": "agent-a"}]}}
    sub_config: dict = {}

    with patch("cuga.backend.server.config_store.load_config", new_callable=AsyncMock) as mock_load:
        mock_load.side_effect = [
            (config, 1),
            (sub_config, 1),
        ]
        with patch("cuga.config.get_tenant_id", return_value="t1"):
            with pytest.raises(RuntimeError, match="saver unavailable"):
                await _delete_child_checkpoints(
                    saver=saver,
                    agent_id="supervisor-agent",
                    conversation_id="thread-123",
                    user_id="user-456",
                )


async def test_delete_child_checkpoints_propagates_parent_config_lookup_errors():
    """A failed parent config lookup must not look like "no children"."""
    from cuga.backend.server.main import _delete_child_checkpoints

    saver = MagicMock()
    saver.adelete_thread = AsyncMock()
    saver.aget_tuple = AsyncMock(return_value=None)

    with patch("cuga.backend.server.config_store.load_config", new_callable=AsyncMock) as mock_load:
        mock_load.side_effect = RuntimeError("config store down")
        with pytest.raises(RuntimeError, match="config store down"):
            await _delete_child_checkpoints(
                saver=saver,
                agent_id="supervisor-agent",
                conversation_id="thread-123",
                user_id="user-456",
            )

    saver.adelete_thread.assert_not_awaited()


async def test_delete_child_checkpoints_propagates_child_config_lookup_errors():
    """A failed sub-agent config lookup must not skip that child's checkpoint."""
    from cuga.backend.server.main import _delete_child_checkpoints

    saver = MagicMock()
    saver.adelete_thread = AsyncMock()
    saver.aget_tuple = AsyncMock(return_value=None)

    config = {"supervisor": {"subAgents": [{"kind": "internal", "ref": "agent-a"}]}}
    with patch("cuga.backend.server.config_store.load_config", new_callable=AsyncMock) as mock_load:
        mock_load.side_effect = [(config, 1), RuntimeError("config store down")]
        with pytest.raises(RuntimeError, match="config store down"):
            await _delete_child_checkpoints(
                saver=saver,
                agent_id="supervisor-agent",
                conversation_id="thread-123",
                user_id="user-456",
            )

    saver.adelete_thread.assert_not_awaited()


async def test_delete_child_checkpoints_covers_yaml_roster_children(tmp_path, monkeypatch):
    """The default agent's YAML supervisor children share the saver, so they are deleted too."""
    from cuga.backend.cuga_graph.nodes.cuga_supervisor.child_checkpoint import child_checkpoint_id
    from cuga.backend.server import main as main_mod
    from cuga.config import settings

    roster = tmp_path / "roster.yaml"
    roster.write_text(
        "agents:\n"
        "  - name: yaml-conversation\n"
        "  - name: yaml-explicit\n"
        "    memory_scope: conversation\n"
        "  - name: yaml-call\n"
        "    memory_scope: call\n"
    )
    monkeypatch.setattr(settings.supervisor, "config_path", str(roster))
    monkeypatch.setattr(settings.supervisor, "sub_agent_memory_scope", "conversation", raising=False)

    saver = MagicMock()
    saver.adelete_thread = AsyncMock()
    saver.aget_tuple = AsyncMock(return_value=None)
    default_id = main_mod.app_state.agent_id

    with patch("cuga.backend.server.config_store.load_config", new_callable=AsyncMock) as mock_load:
        mock_load.return_value = (None, None)
        with patch("cuga.config.get_tenant_id", return_value="t1"):
            await main_mod._delete_child_checkpoints(
                saver=saver,
                agent_id=default_id,
                conversation_id="thread-123",
                user_id="user-456",
            )

    deleted = {call.args[0] for call in saver.adelete_thread.await_args_list}
    expected = {
        child_checkpoint_id(
            tenant_id="t1",
            user_id="user-456",
            supervisor_id=default_id,
            parent_thread_id="thread-123",
            sub_agent_id=name,
        )
        for name in ("yaml-conversation", "yaml-explicit")
    }
    assert deleted == expected  # the call-scoped child has no durable checkpoint


async def test_delete_child_checkpoints_ignores_yaml_roster_for_other_agents(tmp_path, monkeypatch):
    """Only the default agent loads the YAML roster; other agents' children come from the store."""
    from cuga.backend.server import main as main_mod
    from cuga.config import settings

    roster = tmp_path / "roster.yaml"
    roster.write_text("agents:\n  - name: yaml-child\n")
    monkeypatch.setattr(settings.supervisor, "config_path", str(roster))

    saver = MagicMock()
    saver.adelete_thread = AsyncMock()
    saver.aget_tuple = AsyncMock(return_value=None)

    with patch("cuga.backend.server.config_store.load_config", new_callable=AsyncMock) as mock_load:
        mock_load.return_value = (None, None)
        await main_mod._delete_child_checkpoints(
            saver=saver,
            agent_id="some-other-supervisor",
            conversation_id="thread-123",
            user_id="user-456",
        )

    saver.adelete_thread.assert_not_awaited()


async def test_delete_child_checkpoints_deletes_recorded_ids_after_roster_removal():
    """Child IDs recorded in service_scope are deleted even when absent from the current roster.

    This covers the case where a child was removed or renamed since
    the last delegation.
    """
    from cuga.backend.server.main import _delete_child_checkpoints

    recorded_id = "sup_child_aabbccdd" + "0" * 56  # synthetic but structurally valid

    fake_checkpoint = MagicMock()
    fake_checkpoint.checkpoint = {
        "channel_values": {
            "service_scope": {"child_checkpoint_ids": [recorded_id]},
        }
    }

    saver = MagicMock()
    saver.adelete_thread = AsyncMock()
    saver.aget_tuple = AsyncMock(return_value=fake_checkpoint)

    with patch("cuga.backend.server.config_store.load_config", new_callable=AsyncMock) as mock_load:
        # Current roster has no children (they were removed).
        mock_load.return_value = (None, None)
        with patch("cuga.config.get_tenant_id", return_value="t1"):
            await _delete_child_checkpoints(
                saver=saver,
                agent_id="supervisor-agent",
                conversation_id="thread-123",
                user_id="user-456",
            )

    deleted = {call.args[0] for call in saver.adelete_thread.await_args_list}
    assert recorded_id in deleted


async def test_delete_child_checkpoints_deletes_recorded_ids_after_scope_change():
    """Recorded IDs are still deleted even when the child is now call-scoped in the current roster.

    A child switched from conversation to call scope disappears from the roster-derived
    ID set but must still be cleaned up via the recorded list.
    """
    from cuga.backend.server.main import _delete_child_checkpoints

    recorded_id = "sup_child_eeff0011" + "0" * 56  # synthetic but structurally valid

    fake_checkpoint = MagicMock()
    fake_checkpoint.checkpoint = {
        "channel_values": {
            "service_scope": {"child_checkpoint_ids": [recorded_id]},
        }
    }

    saver = MagicMock()
    saver.adelete_thread = AsyncMock()
    saver.aget_tuple = AsyncMock(return_value=fake_checkpoint)

    # Current roster shows the child as call-scoped (would not be deleted by roster logic alone).
    config = {"supervisor": {"subAgents": [{"kind": "internal", "ref": "former-child"}]}}
    sub_config = {"feature_flags": {"sub_agent_memory_scope": "call"}}

    with patch("cuga.backend.server.config_store.load_config", new_callable=AsyncMock) as mock_load:
        mock_load.side_effect = [(config, 1), (sub_config, 1)]
        with patch("cuga.config.get_tenant_id", return_value="t1"):
            await _delete_child_checkpoints(
                saver=saver,
                agent_id="supervisor-agent",
                conversation_id="thread-123",
                user_id="user-456",
            )

    deleted = {call.args[0] for call in saver.adelete_thread.await_args_list}
    assert recorded_id in deleted


async def test_record_child_checkpoint_id_appended_to_service_scope():
    """_record_child_checkpoint_id appends a conversation-scoped ID to service_scope."""
    from cuga.backend.cuga_graph.nodes.cuga_supervisor.delegation import _record_child_checkpoint_id

    class FakeState:
        service_scope: dict = {}

    state = FakeState()
    _record_child_checkpoint_id(state, "sup_child_abc123")
    assert state.service_scope["child_checkpoint_ids"] == ["sup_child_abc123"]

    # Calling again with the same ID must not duplicate.
    _record_child_checkpoint_id(state, "sup_child_abc123")
    assert state.service_scope["child_checkpoint_ids"] == ["sup_child_abc123"]

    # A second distinct ID is appended.
    _record_child_checkpoint_id(state, "sup_child_def456")
    assert state.service_scope["child_checkpoint_ids"] == ["sup_child_abc123", "sup_child_def456"]


async def test_recorded_child_survives_checkpoint_and_next_turn_then_cleanup():
    from langgraph.checkpoint.memory import MemorySaver
    from langgraph.graph import END, START, StateGraph

    from cuga.backend.cuga_graph.nodes.cuga_supervisor.cuga_supervisor_state import CugaSupervisorState
    from cuga.backend.cuga_graph.nodes.cuga_supervisor.delegation import _record_child_checkpoint_id
    from cuga.backend.cuga_graph.nodes.cuga_supervisor.nodes.execute_agent_tool import (
        _delegation_state_update,
    )
    from cuga.backend.cuga_graph.state.agent_state import AgentState
    from cuga.backend.server.main import (
        _checkpoint_thread_id,
        _delete_child_checkpoints,
        apply_request_user_context,
    )

    child_id = "sup_child_" + "a" * 64
    saver = MemorySaver()
    builder = StateGraph(CugaSupervisorState)

    async def delegate(state):
        _record_child_checkpoint_id(state, child_id)
        return _delegation_state_update(state)

    builder.add_node("delegate", delegate)
    builder.add_edge(START, "delegate")
    builder.add_edge("delegate", END)
    graph = builder.compile(checkpointer=saver)
    key = {"configurable": {"thread_id": _checkpoint_thread_id("parent", "u", "supervisor-agent")}}
    await graph.ainvoke(CugaSupervisorState(thread_id="parent", user_id="u").model_dump(), key)
    first = await graph.aget_state(key)
    assert first.values["service_scope"]["child_checkpoint_ids"] == [child_id]

    next_turn = AgentState(**first.values)
    apply_request_user_context(next_turn, "u")
    assert next_turn.service_scope["child_checkpoint_ids"] == [child_id]

    async def no_delegation(state):
        return {"service_scope": dict(state.service_scope or {})}

    later = StateGraph(CugaSupervisorState)
    later.add_node("NoDelegation", no_delegation)
    later.add_edge(START, "NoDelegation")
    later.add_edge("NoDelegation", END)
    await later.compile(checkpointer=saver).ainvoke(next_turn.model_dump(), key)

    child = StateGraph(AgentState)
    child.add_node("Answer", lambda _state: {"final_answer": "child"})
    child.add_edge(START, "Answer")
    child.add_edge("Answer", END)
    child_key = {"configurable": {"thread_id": child_id}}
    await child.compile(checkpointer=saver).ainvoke(AgentState().model_dump(), child_key)
    assert await saver.aget_tuple(child_key) is not None

    with patch("cuga.backend.server.config_store.load_config", new_callable=AsyncMock) as load:
        load.return_value = (None, None)  # child was removed from the current roster
        await _delete_child_checkpoints(
            saver=saver,
            agent_id="supervisor-agent",
            conversation_id="parent",
            user_id="u",
        )
    assert await saver.aget_tuple(child_key) is None


async def test_child_cleanup_aborts_when_parent_checkpoint_read_fails():
    from cuga.backend.server.main import _delete_child_checkpoints

    saver = MagicMock()
    saver.aget_tuple = AsyncMock(side_effect=RuntimeError("saver unavailable"))
    saver.adelete_thread = AsyncMock()

    with pytest.raises(RuntimeError, match="saver unavailable"):
        await _delete_child_checkpoints(
            saver=saver,
            agent_id="supervisor-agent",
            conversation_id="parent",
            user_id="u",
        )
    saver.adelete_thread.assert_not_awaited()
