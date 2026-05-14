"""CugaLite native bind_tools resolution (mode=tools by tool name)."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain_core.tools import StructuredTool

from cuga.backend.cuga_graph.nodes.cuga_lite.cuga_lite_graph import resolve_model_with_bind_tools
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
