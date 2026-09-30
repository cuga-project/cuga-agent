"""Provider-safe tool names on the native boundary (Bedrock rejects names > 64 chars)."""

from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from cuga.backend.cuga_graph.nodes.cuga_lite.bind_tools.deferred import DeferredToolSurface, bind_names
from cuga.backend.cuga_graph.nodes.cuga_lite.bind_tools.minify import minified_tools
from cuga.backend.cuga_graph.nodes.cuga_lite.bind_tools.wire_names import MAX_WIRE_NAME_LENGTH, wire_name
from cuga.backend.cuga_graph.nodes.cuga_lite.helpers.tool_search import ToolSearchIndex, search
from cuga.backend.cuga_graph.nodes.cuga_lite.providers.registry import create_tool_from_api_dict

pytestmark = pytest.mark.unit

LONG = "amazon_move_product_from_cart_to_wish_list_cart_to_wish_list_product_id_post"  # 77 chars (AppWorld)
LONG2 = "amazon_move_product_from_cart_to_wish_list_cart_to_wish_list_product_id_put"


def _tool(name):
    return create_tool_from_api_dict(
        name,
        {
            "description": "Move a product from cart to wish list.",
            "parameters": [{"name": "product_id", "type": "integer", "required": True}],
        },
        "amazon",
    )


def test_wire_name_is_short_unique_and_stable():
    assert wire_name("short_name") == "short_name"
    w1, w2 = wire_name(LONG), wire_name(LONG2)
    assert len(w1) <= MAX_WIRE_NAME_LENGTH and len(w2) <= MAX_WIRE_NAME_LENGTH
    assert w1 != w2, "same prefix, different tools → different wire names"
    assert wire_name(LONG) == w1


def test_minified_specs_use_wire_names():
    (spec,) = minified_tools([_tool(LONG)])
    assert spec["function"]["name"] == wire_name(LONG)


def test_surface_maps_wire_back_to_real_and_lru_sees_wire_calls():
    surface = DeferredToolSurface(core=[], pinned=[], deferred=[LONG], tools_by_name={LONG: _tool(LONG)})
    assert surface.to_real(wire_name(LONG)) == LONG and surface.to_real(LONG) == LONG
    history = [
        HumanMessage(content="q"),
        AIMessage(content="", tool_calls=[{"name": wire_name(LONG), "args": {"product_id": 1}, "id": "c1"}]),
        ToolMessage(content="ok", tool_call_id="c1", name=LONG),
    ]
    names, _ = bind_names(surface, history)
    assert names == [LONG]


@pytest.mark.asyncio
async def test_search_shows_and_accepts_wire_names(monkeypatch):
    from cuga.backend.cuga_graph.nodes.cuga_lite.helpers import tool_search as ts

    async def unavailable(*a, **k):
        return None

    monkeypatch.setattr(ts, "_run_strategy", unavailable)
    index = ToolSearchIndex([_tool(LONG)])
    shown = await search(index, "move product wish list")
    assert wire_name(LONG) in shown.text and LONG not in shown.text
    assert shown.discovered == [LONG], "internally tracked by the registered name"
    selected = await search(index, f"select:{wire_name(LONG)}")
    assert selected.discovered == [LONG]


@pytest.mark.asyncio
async def test_tool_exec_dispatches_a_wire_name_to_the_registered_tool(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    from cuga.backend.cuga_graph.nodes.cuga_lite.adapter.tool_exec_node import create_tool_exec_node
    from cuga.backend.cuga_graph.nodes.cuga_lite.bind_tools.deferred import META_KEY
    from cuga.config import settings

    for key in ("max_tool_calls_per_block", "max_tool_calls_per_run", "max_tool_calls_per_thread"):
        monkeypatch.setattr(settings.advanced_features, key, 0, raising=False)
    ran = []

    async def move(**kwargs):
        ran.append(kwargs)
        return "moved"

    tool = _tool(LONG)
    surface = DeferredToolSurface(core=[], pinned=[], deferred=[LONG], tools_by_name={LONG: tool})

    class _Adapter:
        messages_key = "chat_messages"
        metadata_key = "cuga_lite_metadata"
        execute_node_name = "sandbox"
        sender_name = "CugaLite"
        _model = None

        def __init__(self):
            self._tools_context = {LONG: move}
            self._tools_context_ref = {META_KEY: surface, "_lc_bind_tools_structured_by_name": {LONG: tool}}
            self._tracker = MagicMock()

        def get_messages(self, state):
            return list(state.chat_messages)

        def resolve_max_steps(self, state, override):
            return 50

    last = AIMessage(
        content="", tool_calls=[{"name": wire_name(LONG), "args": {"product_id": 7}, "id": "c1"}]
    )
    state = SimpleNamespace(
        chat_messages=[HumanMessage(content="q"), last],
        step_count=0,
        tool_calls=[],
        tool_calls_used_run=0,
        tool_calls_used_thread=0,
        discovered_tools=[],
    )
    result = await create_tool_exec_node(_Adapter())(state, config={"configurable": {}})
    (reply,) = [m for m in result.update["chat_messages"] if isinstance(m, ToolMessage)]
    assert ran == [{"product_id": 7}]
    assert reply.status == "success" and reply.name == LONG and reply.tool_call_id == "c1"
