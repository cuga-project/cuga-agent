"""CugaLite native bind_tools resolution (mode=tools by tool name)."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.tools import StructuredTool

from cuga.backend.cuga_graph.nodes.cuga_lite.helpers.bind_tools import resolve_model_with_bind_tools
from cuga.backend.cuga_graph.nodes.cuga_lite.model_runtime_profile import resolve_bind_tools_fields


def _stub_tool(name: str) -> StructuredTool:
    def _fn() -> str:
        """Stub."""
        return "ok"

    return StructuredTool.from_function(_fn, name=name)


def test_resolve_bind_tools_fields_tool_names_fallback_chain():
    mode, apps, tool_names, inc = resolve_bind_tools_fields(
        {},
        "",
        settings_mode_fn=lambda: "tools",
        settings_apps_fn=list,
        settings_tool_names_fn=lambda: ["load_skill"],
        settings_include_fn=lambda: False,
    )
    assert mode == "tools"
    assert apps == []
    assert tool_names == ["load_skill"]
    assert inc is False


@pytest.mark.asyncio
async def test_bind_tools_tools_mode_orders_and_filters_names():
    load_skill = _stub_tool("load_skill")
    other = _stub_tool("other_tool")
    provider = AsyncMock()
    provider.get_all_tools = AsyncMock(return_value=[other, load_skill])
    model = MagicMock()

    await resolve_model_with_bind_tools(
        model,
        configurable={
            "cuga_lite_bind_tools_mode": "tools",
            "cuga_lite_bind_tools_tool_names": ["load_skill", "missing_one"],
        },
        tools_context_ref={},
        tool_provider=provider,
    )

    model.bind_tools.assert_called_once()
    (bound,), _kwargs = model.bind_tools.call_args
    assert [t.name for t in bound] == ["load_skill"]


@pytest.mark.asyncio
async def test_bind_tools_apps_and_tools_unions_ordered_and_skips_overlap():
    from_app = _stub_tool("read_text_file")
    extra = _stub_tool("load_skill")
    provider = AsyncMock()

    async def mock_get_tools(app_name: str):
        if app_name == "filesystem":
            return [from_app]
        return []

    provider.get_tools = AsyncMock(side_effect=mock_get_tools)
    provider.get_all_tools = AsyncMock(return_value=[from_app, extra])

    model = MagicMock()

    await resolve_model_with_bind_tools(
        model,
        configurable={
            "cuga_lite_bind_tools_mode": "apps_and_tools",
            "cuga_lite_bind_tools_apps": ["filesystem"],
            "cuga_lite_bind_tools_tool_names": ["load_skill", "read_text_file"],
        },
        tools_context_ref={},
        tool_provider=provider,
    )

    model.bind_tools.assert_called_once()
    (bound,), _kwargs = model.bind_tools.call_args
    assert [t.name for t in bound] == ["read_text_file", "load_skill"]


@pytest.mark.asyncio
async def test_bind_tools_overlay_includes_shell_tools_not_on_registry():
    """OpenSandbox registers ``run_command`` on the prepare overlay only, not MCP get_all_tools."""
    run_cmd = _stub_tool("run_command")
    provider = AsyncMock()
    provider.get_all_tools = AsyncMock(return_value=[])
    model = MagicMock()
    overlay_ref = {"_lc_bind_tools_overlay_structured_tools": [run_cmd]}

    await resolve_model_with_bind_tools(
        model,
        configurable={
            "cuga_lite_bind_tools_mode": "tools",
            "cuga_lite_bind_tools_tool_names": ["run_command"],
        },
        tools_context_ref=overlay_ref,
        tool_provider=provider,
    )

    model.bind_tools.assert_called_once()
    (bound,), _kwargs = model.bind_tools.call_args
    assert [t.name for t in bound] == ["run_command"]


@pytest.mark.asyncio
async def test_bind_tools_mode_all_shortlists_when_over_cap():
    """When mode=all candidate count > max_count, run the LLM shortlister and bind top-K."""
    tools = [_stub_tool(f"tool_{i:03d}") for i in range(10)]
    provider = AsyncMock()
    provider.get_all_tools = AsyncMock(return_value=tools)
    provider.get_apps = AsyncMock(return_value=[])
    model = MagicMock()

    async def fake_shortlist(
        query, all_tools, all_apps, llm=None, top_k=4, instructions=None, run_config=None
    ):
        return [t.name for t in all_tools[: min(top_k, 3)]]

    with (
        patch(
            "cuga.backend.cuga_graph.nodes.cuga_lite.helpers.bind_tools.bind_tools_max_count_from_settings",
            return_value=3,
        ),
        patch(
            "cuga.backend.cuga_graph.nodes.cuga_lite.bind_tools.cap.PromptUtils.shortlist_tool_names",
            side_effect=fake_shortlist,
        ),
    ):
        await resolve_model_with_bind_tools(
            model,
            configurable={"cuga_lite_bind_tools_mode": "all"},
            tools_context_ref={},
            tool_provider=provider,
            query="find me a hockey scorer",
        )

    model.bind_tools.assert_called_once()
    (bound,), _kwargs = model.bind_tools.call_args
    assert [t.name for t in bound] == ["tool_000", "tool_001", "tool_002"]


@pytest.mark.asyncio
async def test_bind_tools_mode_all_raises_when_over_cap_without_query():
    """Failing loudly is required to avoid silently corrupting benchmark results."""
    tools = [_stub_tool(f"tool_{i:03d}") for i in range(10)]
    provider = AsyncMock()
    provider.get_all_tools = AsyncMock(return_value=tools)
    provider.get_apps = AsyncMock(return_value=[])
    model = MagicMock()

    with patch(
        "cuga.backend.cuga_graph.nodes.cuga_lite.helpers.bind_tools.bind_tools_max_count_from_settings",
        return_value=3,
    ):
        with pytest.raises(RuntimeError, match="provider-safe cap"):
            await resolve_model_with_bind_tools(
                model,
                configurable={"cuga_lite_bind_tools_mode": "all"},
                tools_context_ref={},
                tool_provider=provider,
                query=None,
            )
    model.bind_tools.assert_not_called()


@pytest.mark.asyncio
async def test_bind_tools_mode_all_no_cap_when_under_threshold():
    """Under the cap, no shortlister is invoked and all candidates are bound."""
    tools = [_stub_tool(f"tool_{i}") for i in range(3)]
    provider = AsyncMock()
    provider.get_all_tools = AsyncMock(return_value=tools)
    provider.get_apps = AsyncMock(return_value=[])
    model = MagicMock()

    shortlist_calls = MagicMock()

    with (
        patch(
            "cuga.backend.cuga_graph.nodes.cuga_lite.helpers.bind_tools.bind_tools_max_count_from_settings",
            return_value=128,
        ),
        patch(
            "cuga.backend.cuga_graph.nodes.cuga_lite.bind_tools.cap.PromptUtils.shortlist_tool_names",
            side_effect=shortlist_calls,
        ),
    ):
        await resolve_model_with_bind_tools(
            model,
            configurable={"cuga_lite_bind_tools_mode": "all"},
            tools_context_ref={},
            tool_provider=provider,
            query="anything",
        )

    shortlist_calls.assert_not_called()
    model.bind_tools.assert_called_once()
    (bound,), _kwargs = model.bind_tools.call_args
    assert sorted(t.name for t in bound) == ["tool_0", "tool_1", "tool_2"]


@pytest.mark.asyncio
async def test_bind_tools_mode_all_disabled_cap_binds_everything():
    """max_count <= 0 disables the cap entirely (WatsonX/permissive backends)."""
    tools = [_stub_tool(f"tool_{i}") for i in range(50)]
    provider = AsyncMock()
    provider.get_all_tools = AsyncMock(return_value=tools)
    provider.get_apps = AsyncMock(return_value=[])
    model = MagicMock()

    with patch(
        "cuga.backend.cuga_graph.nodes.cuga_lite.helpers.bind_tools.bind_tools_max_count_from_settings",
        return_value=0,
    ):
        await resolve_model_with_bind_tools(
            model,
            configurable={"cuga_lite_bind_tools_mode": "all"},
            tools_context_ref={},
            tool_provider=provider,
            query=None,
        )

    model.bind_tools.assert_called_once()
    (bound,), _kwargs = model.bind_tools.call_args
    assert len(bound) == 50


@pytest.mark.asyncio
async def test_bind_tools_cap_does_not_pad_by_default():
    """By default, only the shortlister's ranked tools are bound — cuga_lite is a code-agent
    and binding many native tools regresses code-emission (see commit context)."""
    tools = [_stub_tool(f"tool_{i:03d}") for i in range(10)]
    provider = AsyncMock()
    provider.get_all_tools = AsyncMock(return_value=tools)
    provider.get_apps = AsyncMock(return_value=[])
    model = MagicMock()

    async def stingy_shortlist(
        query, all_tools, all_apps, llm=None, top_k=4, instructions=None, run_config=None
    ):
        return ["tool_007"]

    with (
        patch(
            "cuga.backend.cuga_graph.nodes.cuga_lite.helpers.bind_tools.bind_tools_max_count_from_settings",
            return_value=5,
        ),
        patch(
            "cuga.backend.cuga_graph.nodes.cuga_lite.bind_tools.cap.bind_tools_pad_to_cap_from_settings",
            return_value=False,
        ),
        patch(
            "cuga.backend.cuga_graph.nodes.cuga_lite.bind_tools.cap.PromptUtils.shortlist_tool_names",
            side_effect=stingy_shortlist,
        ),
    ):
        await resolve_model_with_bind_tools(
            model,
            configurable={"cuga_lite_bind_tools_mode": "all"},
            tools_context_ref={},
            tool_provider=provider,
            query="anything",
        )

    model.bind_tools.assert_called_once()
    (bound,), _kwargs = model.bind_tools.call_args
    assert [t.name for t in bound] == ["tool_007"]


@pytest.mark.asyncio
async def test_bind_tools_cap_pads_when_opt_in():
    """When pad_to_cap=True, the shortlist is filled with the remaining tools up to target_k."""
    tools = [_stub_tool(f"tool_{i:03d}") for i in range(10)]
    provider = AsyncMock()
    provider.get_all_tools = AsyncMock(return_value=tools)
    provider.get_apps = AsyncMock(return_value=[])
    model = MagicMock()

    async def stingy_shortlist(
        query, all_tools, all_apps, llm=None, top_k=4, instructions=None, run_config=None
    ):
        return ["tool_007"]

    with (
        patch(
            "cuga.backend.cuga_graph.nodes.cuga_lite.helpers.bind_tools.bind_tools_max_count_from_settings",
            return_value=5,
        ),
        patch(
            "cuga.backend.cuga_graph.nodes.cuga_lite.bind_tools.cap.bind_tools_pad_to_cap_from_settings",
            return_value=True,
        ),
        patch(
            "cuga.backend.cuga_graph.nodes.cuga_lite.bind_tools.cap.PromptUtils.shortlist_tool_names",
            side_effect=stingy_shortlist,
        ),
    ):
        await resolve_model_with_bind_tools(
            model,
            configurable={"cuga_lite_bind_tools_mode": "all"},
            tools_context_ref={},
            tool_provider=provider,
            query="anything",
        )

    model.bind_tools.assert_called_once()
    (bound,), _kwargs = model.bind_tools.call_args
    names = [t.name for t in bound]
    assert len(names) == 5
    assert names[0] == "tool_007"
    assert names[1:] == ["tool_000", "tool_001", "tool_002", "tool_003"]


@pytest.mark.asyncio
async def test_bind_tools_cap_not_violated_when_at_boundary_with_find_tools():
    """Boundary case from coderabbit: len(bound) == max_count and include_find_tools=True.

    Without the effective-count check, the under-cap fast path would append find_tools and
    return max_count+1 tools — provider rejects.
    """
    tools = [_stub_tool(f"tool_{i:03d}") for i in range(5)]
    find_tools_tool = _stub_tool("find_tools")
    provider = AsyncMock()
    provider.get_all_tools = AsyncMock(return_value=tools)
    provider.get_apps = AsyncMock(return_value=[])
    model = MagicMock()

    captured_top_k = {}

    async def fake_shortlist(
        query, all_tools, all_apps, llm=None, top_k=4, instructions=None, run_config=None
    ):
        captured_top_k["value"] = top_k
        return [t.name for t in all_tools[:top_k]]

    with (
        patch(
            "cuga.backend.cuga_graph.nodes.cuga_lite.helpers.bind_tools.bind_tools_max_count_from_settings",
            return_value=5,
        ),
        patch(
            "cuga.backend.cuga_graph.nodes.cuga_lite.bind_tools.cap.PromptUtils.shortlist_tool_names",
            side_effect=fake_shortlist,
        ),
    ):
        await resolve_model_with_bind_tools(
            model,
            configurable={
                "cuga_lite_bind_tools_mode": "all",
                "cuga_lite_bind_tools_include_find_tools": True,
            },
            tools_context_ref={"_lc_bind_tools_find_tools": find_tools_tool},
            tool_provider=provider,
            query="hockey",
        )

    model.bind_tools.assert_called_once()
    (bound,), _kwargs = model.bind_tools.call_args
    assert len(bound) == 5, f"cap violated: bound has {len(bound)} tools (max=5)"
    assert captured_top_k["value"] == 4, "expected 1 slot reserved for find_tools"
    assert bound[-1].name == "find_tools"


@pytest.mark.asyncio
async def test_bind_tools_cap_guarantees_find_tools_when_in_overlay_bound():
    """If `find_tools` is in `bound` via the overlay and `include_find_tools=True`, it must
    survive shortlisting — the LLM ranker is allowed to drop any tool from its ranking input,
    so we pull find_tools out of the ranking pool and reserve a cap slot for it.

    Regression test for coderabbit comment on #203 (`include_find_tools` contract).
    """
    tools = [_stub_tool(f"tool_{i:03d}") for i in range(5)]
    find_tools_tool = _stub_tool("find_tools")
    provider = AsyncMock()
    provider.get_all_tools = AsyncMock(return_value=tools + [find_tools_tool])
    provider.get_apps = AsyncMock(return_value=[])
    model = MagicMock()

    captured = {}

    async def fake_shortlist(
        query, all_tools, all_apps, llm=None, top_k=4, instructions=None, run_config=None
    ):
        captured["top_k"] = top_k
        captured["pool_names"] = [t.name for t in all_tools]
        # Adversarial: rank find_tools out if it appears in the input. With the fix it shouldn't.
        return [t.name for t in all_tools[:top_k]]

    with (
        patch(
            "cuga.backend.cuga_graph.nodes.cuga_lite.helpers.bind_tools.bind_tools_max_count_from_settings",
            return_value=4,
        ),
        patch(
            "cuga.backend.cuga_graph.nodes.cuga_lite.bind_tools.cap.PromptUtils.shortlist_tool_names",
            side_effect=fake_shortlist,
        ),
    ):
        await resolve_model_with_bind_tools(
            model,
            configurable={
                "cuga_lite_bind_tools_mode": "all",
                "cuga_lite_bind_tools_include_find_tools": True,
            },
            tools_context_ref={"_lc_bind_tools_find_tools": find_tools_tool},
            tool_provider=provider,
            query="hockey",
        )

    model.bind_tools.assert_called_once()
    (bound,), _kwargs = model.bind_tools.call_args
    assert len(bound) == 4, f"cap violated: bound has {len(bound)} (cap=4)"
    # Reserve 1 slot for find_tools, so the shortlister sees top_k = cap - 1.
    assert captured["top_k"] == 3
    # find_tools must not be exposed to the ranker (it can't be evicted that way).
    assert "find_tools" not in captured["pool_names"]
    # find_tools must end up bound regardless of how the ranker votes.
    assert "find_tools" in {t.name for t in bound}
    assert bound[-1].name == "find_tools"


@pytest.mark.asyncio
async def test_bind_tools_include_find_tools_false_strips_overlay_find_tools():
    """Overlay can inject `find_tools` into `bound` regardless of `include_find_tools`.

    When the user sets `include_find_tools=False`, the overlay-injected tool must be stripped
    so it can't consume a cap slot or be ranked. Regression test for coderabbit on #203.
    """
    tools = [_stub_tool(f"tool_{i:03d}") for i in range(5)]
    find_tools_tool = _stub_tool("find_tools")
    provider = AsyncMock()
    # Overlay path puts find_tools into bound directly.
    provider.get_all_tools = AsyncMock(return_value=tools + [find_tools_tool])
    provider.get_apps = AsyncMock(return_value=[])
    model = MagicMock()

    captured = {}

    async def fake_shortlist(
        query, all_tools, all_apps, llm=None, top_k=4, instructions=None, run_config=None
    ):
        captured["pool_names"] = [t.name for t in all_tools]
        return [t.name for t in all_tools[:top_k]]

    with (
        patch(
            "cuga.backend.cuga_graph.nodes.cuga_lite.helpers.bind_tools.bind_tools_max_count_from_settings",
            return_value=3,
        ),
        patch(
            "cuga.backend.cuga_graph.nodes.cuga_lite.bind_tools.cap.PromptUtils.shortlist_tool_names",
            side_effect=fake_shortlist,
        ),
    ):
        await resolve_model_with_bind_tools(
            model,
            configurable={
                "cuga_lite_bind_tools_mode": "all",
                # NB: include_find_tools is False (default in configurable here)
            },
            tools_context_ref={"_lc_bind_tools_find_tools": find_tools_tool},
            tool_provider=provider,
            query="hockey",
        )

    model.bind_tools.assert_called_once()
    (bound,), _kwargs = model.bind_tools.call_args
    bound_names = {t.name for t in bound}
    assert "find_tools" not in bound_names, (
        f"find_tools leaked through overlay despite include_find_tools=False: {bound_names}"
    )
    # The shortlister must not have seen find_tools either.
    assert "find_tools" not in captured["pool_names"]


@pytest.mark.asyncio
async def test_bind_tools_cap_raises_when_shortlist_names_dont_match_pool():
    """LLM-hallucinated shortlist names (non-empty list, zero matches in pool) must fail
    loudly. Without the guard we'd silently pad or bind just find_tools, recreating the
    silent degradation the cap path exists to prevent. Coderabbit on #203."""
    tools = [_stub_tool(f"tool_{i:03d}") for i in range(10)]
    provider = AsyncMock()
    provider.get_all_tools = AsyncMock(return_value=tools)
    provider.get_apps = AsyncMock(return_value=[])
    model = MagicMock()

    async def hallucinating_shortlist(
        query, all_tools, all_apps, llm=None, top_k=4, instructions=None, run_config=None
    ):
        return ["nonexistent_tool_a", "nonexistent_tool_b"]

    with (
        patch(
            "cuga.backend.cuga_graph.nodes.cuga_lite.helpers.bind_tools.bind_tools_max_count_from_settings",
            return_value=3,
        ),
        patch(
            "cuga.backend.cuga_graph.nodes.cuga_lite.bind_tools.cap.PromptUtils.shortlist_tool_names",
            side_effect=hallucinating_shortlist,
        ),
    ):
        with pytest.raises(RuntimeError, match="hallucinated"):
            await resolve_model_with_bind_tools(
                model,
                configurable={"cuga_lite_bind_tools_mode": "all"},
                tools_context_ref={},
                tool_provider=provider,
                query="hockey",
            )
    model.bind_tools.assert_not_called()


@pytest.mark.asyncio
async def test_bind_tools_cap_clamps_shortlist_when_llm_returns_too_many():
    """If the shortlister (e.g. a non-compliant custom impl or future refactor) returns more
    valid names than ``top_k``, the call site must still enforce ``target_k`` so the bound
    list never exceeds the provider-safe cap. Regression test for coderabbit on #203."""
    tools = [_stub_tool(f"tool_{i:03d}") for i in range(20)]
    provider = AsyncMock()
    provider.get_all_tools = AsyncMock(return_value=tools)
    provider.get_apps = AsyncMock(return_value=[])
    model = MagicMock()

    async def overlong_shortlist(
        query, all_tools, all_apps, llm=None, top_k=4, instructions=None, run_config=None
    ):
        # Deliberately ignore top_k — return every pool name.
        return [t.name for t in all_tools]

    with (
        patch(
            "cuga.backend.cuga_graph.nodes.cuga_lite.helpers.bind_tools.bind_tools_max_count_from_settings",
            return_value=4,
        ),
        patch(
            "cuga.backend.cuga_graph.nodes.cuga_lite.bind_tools.cap.PromptUtils.shortlist_tool_names",
            side_effect=overlong_shortlist,
        ),
    ):
        await resolve_model_with_bind_tools(
            model,
            configurable={"cuga_lite_bind_tools_mode": "all"},
            tools_context_ref={},
            tool_provider=provider,
            query="hockey",
        )

    model.bind_tools.assert_called_once()
    (bound,), _kwargs = model.bind_tools.call_args
    assert len(bound) == 4, f"cap violated: bound has {len(bound)} (max=4)"
    # First four ranked names — earlier names win via the in-order break.
    assert [t.name for t in bound] == ["tool_000", "tool_001", "tool_002", "tool_003"]


@pytest.mark.asyncio
async def test_bind_tools_cap_clamps_shortlist_with_find_tools_slot():
    """Same clamp must hold when ``include_find_tools=True`` reserves a slot:
    ``target_k = max_count - 1`` is the upper bound on shortlisted entries."""
    tools = [_stub_tool(f"tool_{i:03d}") for i in range(20)]
    find_tools_tool = _stub_tool("find_tools")
    provider = AsyncMock()
    provider.get_all_tools = AsyncMock(return_value=tools)
    provider.get_apps = AsyncMock(return_value=[])
    model = MagicMock()

    async def overlong_shortlist(
        query, all_tools, all_apps, llm=None, top_k=4, instructions=None, run_config=None
    ):
        return [t.name for t in all_tools]

    with (
        patch(
            "cuga.backend.cuga_graph.nodes.cuga_lite.helpers.bind_tools.bind_tools_max_count_from_settings",
            return_value=4,
        ),
        patch(
            "cuga.backend.cuga_graph.nodes.cuga_lite.bind_tools.cap.PromptUtils.shortlist_tool_names",
            side_effect=overlong_shortlist,
        ),
    ):
        await resolve_model_with_bind_tools(
            model,
            configurable={
                "cuga_lite_bind_tools_mode": "all",
                "cuga_lite_bind_tools_include_find_tools": True,
            },
            tools_context_ref={"_lc_bind_tools_find_tools": find_tools_tool},
            tool_provider=provider,
            query="hockey",
        )

    model.bind_tools.assert_called_once()
    (bound,), _kwargs = model.bind_tools.call_args
    assert len(bound) == 4, f"cap violated: bound has {len(bound)} (max=4)"
    # 3 shortlisted + 1 find_tools at the end.
    assert [t.name for t in bound] == ["tool_000", "tool_001", "tool_002", "find_tools"]


@pytest.mark.asyncio
async def test_bind_tools_cap_binds_only_find_tools_when_max_count_is_one():
    """`max_count=1` + `include_find_tools=True` should still succeed by binding only
    find_tools, instead of raising as "cap too small to fit even find_tools"."""
    tools = [_stub_tool(f"tool_{i:03d}") for i in range(5)]
    find_tools_tool = _stub_tool("find_tools")
    provider = AsyncMock()
    provider.get_all_tools = AsyncMock(return_value=tools)
    provider.get_apps = AsyncMock(return_value=[])
    model = MagicMock()

    with patch(
        "cuga.backend.cuga_graph.nodes.cuga_lite.helpers.bind_tools.bind_tools_max_count_from_settings",
        return_value=1,
    ):
        await resolve_model_with_bind_tools(
            model,
            configurable={
                "cuga_lite_bind_tools_mode": "all",
                "cuga_lite_bind_tools_include_find_tools": True,
            },
            tools_context_ref={"_lc_bind_tools_find_tools": find_tools_tool},
            tool_provider=provider,
            query="hockey",
        )

    model.bind_tools.assert_called_once()
    (bound,), _kwargs = model.bind_tools.call_args
    assert [t.name for t in bound] == ["find_tools"]


@pytest.mark.asyncio
async def test_bind_tools_cap_reserves_slot_for_find_tools():
    """When include_find_tools is on, the cap reserves 1 slot for find_tools."""
    tools = [_stub_tool(f"tool_{i:03d}") for i in range(10)]
    find_tools_tool = _stub_tool("find_tools")
    provider = AsyncMock()
    provider.get_all_tools = AsyncMock(return_value=tools)
    provider.get_apps = AsyncMock(return_value=[])
    model = MagicMock()

    captured_top_k = {}

    async def fake_shortlist(
        query, all_tools, all_apps, llm=None, top_k=4, instructions=None, run_config=None
    ):
        captured_top_k["value"] = top_k
        return [t.name for t in all_tools[:top_k]]

    with (
        patch(
            "cuga.backend.cuga_graph.nodes.cuga_lite.helpers.bind_tools.bind_tools_max_count_from_settings",
            return_value=4,
        ),
        patch(
            "cuga.backend.cuga_graph.nodes.cuga_lite.bind_tools.cap.PromptUtils.shortlist_tool_names",
            side_effect=fake_shortlist,
        ),
    ):
        await resolve_model_with_bind_tools(
            model,
            configurable={
                "cuga_lite_bind_tools_mode": "all",
                "cuga_lite_bind_tools_include_find_tools": True,
            },
            tools_context_ref={"_lc_bind_tools_find_tools": find_tools_tool},
            tool_provider=provider,
            query="hockey",
        )

    assert captured_top_k["value"] == 3
    model.bind_tools.assert_called_once()
    (bound,), _kwargs = model.bind_tools.call_args
    assert [t.name for t in bound] == ["tool_000", "tool_001", "tool_002", "find_tools"]
