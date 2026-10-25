"""Supervisor child threads on the shared saver — W04.4/W12.6."""

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
