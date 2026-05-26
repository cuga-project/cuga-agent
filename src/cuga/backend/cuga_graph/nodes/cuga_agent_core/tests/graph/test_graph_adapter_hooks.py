"""Phase 1 + Phase 2 — CoreGraphAdapter optional call_model hook extensions.

Pins the optional hooks added to CoreGraphAdapter so that:
1. A minimal concrete subclass that only implements the abstract methods
   gets no-op defaults for all hooks.
2. A subclass that overrides the hooks returns its custom values.
3. The hooks have the correct signatures.

Phase 2 additions: prepare_system_content, normalize_response, get_invoke_config,
on_response_processed, build_metadata_update, get_variables_storage, get_tracker,
and the async classify_auto_continue (model + reasoning args).
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, List, Optional

import pytest
from langchain_core.messages import BaseMessage, HumanMessage

from cuga.backend.cuga_graph.nodes.cuga_agent_core.graph.graph_nodes import CoreGraphAdapter


# ── Minimal concrete adapter (only satisfies abstract methods) ─────────────


class _MinimalAdapter(CoreGraphAdapter):
    messages_key = "chat_messages"

    def get_messages(self, state: Any) -> List[BaseMessage]:
        return []

    def resolve_max_steps(self, state: Any, override: Optional[int]) -> int:
        return override or 50


# ── 1. Default hook values ─────────────────────────────────────────────────


def test_default_get_few_shot_messages_returns_empty_list():
    adapter = _MinimalAdapter()
    state = SimpleNamespace()
    result = adapter.get_few_shot_messages(state)
    assert result == []


def test_default_get_pi_returns_none():
    adapter = _MinimalAdapter()
    state = SimpleNamespace()
    assert adapter.get_pi(state) is None


@pytest.mark.asyncio
async def test_default_classify_auto_continue_returns_false():
    adapter = _MinimalAdapter()
    state = SimpleNamespace()
    assert (
        await adapter.classify_auto_continue(state, model=None, content="some content", reasoning=None)
        is False
    )


@pytest.mark.asyncio
async def test_default_resolve_bind_tools_returns_none():
    adapter = _MinimalAdapter()
    state = SimpleNamespace()
    assert await adapter.resolve_bind_tools(state, active_model=None, configurable={}) is None


def test_default_prepare_system_content_returns_base_prompt():
    adapter = _MinimalAdapter()
    state = SimpleNamespace()
    assert adapter.prepare_system_content(state, {}, "base prompt") == "base prompt"


def test_default_normalize_response_extracts_content_and_reasoning():
    adapter = _MinimalAdapter()
    response = SimpleNamespace(
        content="hello",
        additional_kwargs={"reasoning_content": "thought"},
    )
    content, reasoning = adapter.normalize_response(response)
    assert content == "hello"
    assert reasoning == "thought"


def test_default_normalize_response_none_reasoning():
    adapter = _MinimalAdapter()
    response = SimpleNamespace(content="hi", additional_kwargs={})
    content, reasoning = adapter.normalize_response(response)
    assert content == "hi"
    assert reasoning is None


def test_default_get_invoke_config_returns_empty_dict():
    adapter = _MinimalAdapter()
    assert adapter.get_invoke_config({}) == {}


def test_default_on_response_processed_is_noop():
    adapter = _MinimalAdapter()
    state = SimpleNamespace()
    # Must not raise
    adapter.on_response_processed(state, code=None, content="hi")
    adapter.on_response_processed(state, code="print(1)", content="```python\nprint(1)\n```")


def test_default_build_metadata_update_returns_existing_meta_when_no_playbook():
    adapter = _MinimalAdapter()
    state = SimpleNamespace(cuga_lite_metadata={"some_key": True})
    result = adapter.build_metadata_update(state, playbook_fired=False)
    assert result == {"some_key": True}


def test_default_build_metadata_update_adds_playbook_flag_when_fired():
    adapter = _MinimalAdapter()
    state = SimpleNamespace(cuga_lite_metadata={"some_key": True})
    result = adapter.build_metadata_update(state, playbook_fired=True)
    assert result["playbook_guidance_added"] is True
    assert result["some_key"] is True


def test_default_get_variables_storage_returns_state_attr():
    adapter = _MinimalAdapter()
    storage = {"x": {"value": 1}}
    state = SimpleNamespace(variables_storage=storage)
    assert adapter.get_variables_storage(state) is storage


def test_default_get_tracker_returns_none():
    adapter = _MinimalAdapter()
    assert adapter.get_tracker() is None


@pytest.mark.asyncio
async def test_default_ainvoke_model_delegates_to_bound():
    from unittest.mock import AsyncMock, MagicMock

    adapter = _MinimalAdapter()
    sentinel = object()
    mock_bound = MagicMock()
    mock_bound.ainvoke = AsyncMock(return_value=sentinel)
    result = await adapter.ainvoke_model(mock_bound, ["msg"], {"callbacks": []})
    assert result is sentinel
    mock_bound.ainvoke.assert_awaited_once_with(["msg"], config={"callbacks": []})


# ── 2. Overriding hooks ────────────────────────────────────────────────────


class _FullAdapter(CoreGraphAdapter):
    messages_key = "chat_messages"

    def get_messages(self, state: Any) -> List[BaseMessage]:
        return state.chat_messages or []

    def resolve_max_steps(self, state: Any, override: Optional[int]) -> int:
        return override or 10

    def get_few_shot_messages(self, state: Any) -> List[BaseMessage]:
        return state.few_shot or []

    def get_pi(self, state: Any) -> Optional[str]:
        return getattr(state, "pi", None)

    async def classify_auto_continue(
        self, state: Any, model: Any, content: str, reasoning: Optional[str]
    ) -> bool:
        return "CONTINUE" in content

    async def resolve_bind_tools(self, state: Any, active_model: Any, configurable: dict) -> Any:
        return "bound_model_sentinel"


def test_overridden_get_few_shot_messages():
    adapter = _FullAdapter()
    msg = HumanMessage(content="example")
    state = SimpleNamespace(few_shot=[msg])
    assert adapter.get_few_shot_messages(state) == [msg]


def test_overridden_get_pi():
    adapter = _FullAdapter()
    state = SimpleNamespace(pi="You are a helpful assistant.")
    assert adapter.get_pi(state) == "You are a helpful assistant."


@pytest.mark.asyncio
async def test_overridden_classify_auto_continue_true():
    adapter = _FullAdapter()
    state = SimpleNamespace()
    assert (
        await adapter.classify_auto_continue(state, None, "Please CONTINUE with the next step.", None) is True
    )


@pytest.mark.asyncio
async def test_overridden_classify_auto_continue_false():
    adapter = _FullAdapter()
    state = SimpleNamespace()
    assert await adapter.classify_auto_continue(state, None, "All done.", None) is False


@pytest.mark.asyncio
async def test_overridden_resolve_bind_tools():
    adapter = _FullAdapter()
    state = SimpleNamespace()
    result = await adapter.resolve_bind_tools(state, active_model=None, configurable={})
    assert result == "bound_model_sentinel"


# ── 3. Hook signatures are stable under keyword call ──────────────────────


@pytest.mark.asyncio
async def test_all_hooks_callable_with_keyword_args():
    adapter = _MinimalAdapter()
    state = SimpleNamespace(variables_storage={}, cuga_lite_metadata={})
    response = SimpleNamespace(content="hi", additional_kwargs={})
    # Should not raise
    adapter.get_few_shot_messages(state)
    adapter.get_pi(state)
    await adapter.classify_auto_continue(state, model=None, content="hello", reasoning=None)
    await adapter.resolve_bind_tools(state, active_model=None, configurable={})
    adapter.prepare_system_content(state, {}, "prompt")
    adapter.normalize_response(response)
    adapter.get_invoke_config({})
    adapter.on_response_processed(state, code=None, content="hi")
    adapter.build_metadata_update(state, playbook_fired=False)
    adapter.get_variables_storage(state)
    adapter.get_tracker()
    from unittest.mock import AsyncMock

    mock_bound = AsyncMock()
    await adapter.ainvoke_model(mock_bound, [], {})
