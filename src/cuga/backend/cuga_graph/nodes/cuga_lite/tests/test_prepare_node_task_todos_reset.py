"""Regression test for issue #314 — task_todos reset on fresh conversation.

Verifies that prepare_tools_and_apps:
1. Clears the closure-scoped adapter._task_todos_ref in place when a
   conversation starts (chat_messages <= 1)
2. Propagates the reset through the returned Command.update so LangGraph
   persists state.task_todos = None — the failure mode flagged in the
   review of PR #315 where the in-place state mutation was a no-op
3. Does NOT touch either field on a continuing conversation (HITL resume
   or follow-up turn)
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage


def _build_mock_adapter(stale_ref: list[dict]) -> MagicMock:
    adapter = MagicMock()
    adapter._task_todos_ref = list(stale_ref)
    adapter._tools_context = {}
    adapter._instructions = ""
    adapter._special_instructions = None
    adapter._static_prompt = None
    adapter._thread_id = "test-thread"
    adapter._model = MagicMock()
    adapter.set_metadata = MagicMock()

    # Tool provider: no tools, no apps (lets the node finish without I/O)
    adapter._base_tool_provider = MagicMock()
    adapter._base_tool_provider.get_all_tools = AsyncMock(return_value=[])
    adapter._base_tool_provider.get_apps = AsyncMock(return_value=[])
    adapter._base_tool_provider.get_tools = AsyncMock(return_value=[])

    # Prompt template renders to empty string (we don't assert on the prompt body)
    rendered = MagicMock()
    rendered.to_string = MagicMock(return_value="")
    adapter._prompt_template = MagicMock()
    adapter._prompt_template.invoke = MagicMock(return_value=rendered)
    return adapter


def _make_state(*, chat_messages: list, task_todos):
    return SimpleNamespace(
        chat_messages=chat_messages,
        task_todos=task_todos,
        sub_task=None,
        sub_task_app=None,
        api_intent_relevant_apps=None,
        cuga_lite_metadata=None,
        thread_id="test-thread",
    )


@pytest.mark.asyncio
async def test_fresh_conversation_clears_ref_and_propagates_through_command_update():
    """Stale ref + stale state.task_todos on a fresh-invoke first turn:
    closure ref is cleared in place AND Command.update carries task_todos=None
    so LangGraph persists the reset (closes the state-fallback leak path that
    the review of PR #315 flagged as still open).
    """
    from cuga.backend.cuga_graph.nodes.cuga_lite.adapter.prepare_node import (
        create_prepare_tools_and_apps_node,
    )

    stale_ref = [{"text": "stale Gmail plan", "status": "pending"}]
    stale_state = [{"text": "stale checkpointed plan", "status": "in_progress"}]

    adapter = _build_mock_adapter(stale_ref)
    state = _make_state(
        chat_messages=[HumanMessage(content="new HR task")],
        task_todos=list(stale_state),
    )

    # Override only the prepare-node-level config via configurable; let the
    # real settings module handle deeper layers (filesystem / shell backends
    # validated by pydantic).
    configurable = {
        "enable_todos": False,
        "shortlisting_tool_threshold": 35,
    }
    with patch(
        "cuga.backend.cuga_graph.nodes.cuga_lite.adapter.prepare_node.settings.policy.enabled",
        new=False,
    ):
        node = create_prepare_tools_and_apps_node(adapter, lc_bind_tools_meta={})
        result = await node(state, config={"configurable": configurable})

    # 1. Closure-scoped ref cleared in place
    assert adapter._task_todos_ref == [], (
        f"_task_todos_ref should be cleared on fresh conversation; got {adapter._task_todos_ref!r}"
    )
    # 2. Command.update carries the reset so LangGraph persists state.task_todos = None
    assert isinstance(result.update, dict), "prepare_tools_and_apps must return Command with an update dict"
    assert "task_todos" in result.update, (
        "Command.update must include task_todos to propagate the reset through "
        "LangGraph state; the in-place state.task_todos = None mutation is a "
        "no-op (see PR #315 review)"
    )
    assert result.update["task_todos"] is None


@pytest.mark.asyncio
async def test_continuing_conversation_preserves_ref_and_omits_task_todos_in_update():
    """When chat_messages > 1 (multi-turn / HITL resume):
    closure ref is preserved (in-flight plan still visible to call_model)
    AND Command.update does NOT contain task_todos (no spurious reset).
    """
    from cuga.backend.cuga_graph.nodes.cuga_lite.adapter.prepare_node import (
        create_prepare_tools_and_apps_node,
    )

    in_flight_ref = [{"text": "fetch unread email", "status": "in_progress"}]

    adapter = _build_mock_adapter(in_flight_ref)
    state = _make_state(
        chat_messages=[
            HumanMessage(content="original task"),
            AIMessage(content="assistant turn 1"),
            HumanMessage(content="continue"),
        ],
        task_todos=list(in_flight_ref),
    )

    configurable = {
        "enable_todos": False,
        "shortlisting_tool_threshold": 35,
    }
    with patch(
        "cuga.backend.cuga_graph.nodes.cuga_lite.adapter.prepare_node.settings.policy.enabled",
        new=False,
    ):
        node = create_prepare_tools_and_apps_node(adapter, lc_bind_tools_meta={})
        result = await node(state, config={"configurable": configurable})

    # 1. In-flight ref preserved
    assert adapter._task_todos_ref == in_flight_ref, (
        f"_task_todos_ref must NOT be cleared on a continuing turn; got {adapter._task_todos_ref!r}"
    )
    # 2. No spurious task_todos reset in the update
    assert "task_todos" not in result.update, (
        "Command.update should not include task_todos on a continuing turn (would erase the in-flight plan)"
    )
