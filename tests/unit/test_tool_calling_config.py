"""ToolCalling -> configurable serialization (issue #471 P1)."""

from cuga import ToolCalling
from cuga.backend.cuga_graph.nodes.cuga_lite.tool_calling import tool_calling_to_configurable


def test_default_and_code_mode_serialize_to_empty():
    assert tool_calling_to_configurable(None) == {}
    assert tool_calling_to_configurable(ToolCalling()) == {}
    assert tool_calling_to_configurable(ToolCalling(mode="code")) == {}


def test_native_all_tools():
    cfg = tool_calling_to_configurable(ToolCalling(mode="native"))
    assert cfg == {
        "cuga_lite_tool_invocation_mode": "native",
        "cuga_lite_bind_tools_mode": "all",
    }


def test_native_specific_tools():
    cfg = tool_calling_to_configurable(ToolCalling(mode="native", native_tools=["send_email"]))
    assert cfg["cuga_lite_bind_tools_mode"] == "tools"
    assert cfg["cuga_lite_bind_tools_tool_names"] == ["send_email"]
    assert cfg["cuga_lite_tool_invocation_mode"] == "native"


def test_hybrid_apps_with_cap_and_find_tools():
    cfg = tool_calling_to_configurable(
        ToolCalling(mode="hybrid", apps=["crm"], include_find_tools=True, max_bound_tools=32)
    )
    assert cfg["cuga_lite_tool_invocation_mode"] == "hybrid"
    assert cfg["cuga_lite_bind_tools_mode"] == "apps"
    assert cfg["cuga_lite_bind_tools_apps"] == ["crm"]
    assert cfg["cuga_lite_bind_tools_include_find_tools"] is True
    assert cfg["cuga_lite_bind_tools_max_count"] == 32


def test_native_tools_take_precedence_over_apps():
    cfg = tool_calling_to_configurable(ToolCalling(mode="native", native_tools=["a"], apps=["crm"]))
    assert cfg["cuga_lite_bind_tools_mode"] == "tools"


def test_tool_choice_serialized():
    cfg = tool_calling_to_configurable(ToolCalling(mode="native", tool_choice="required"))
    assert cfg["cuga_lite_tool_choice"] == "required"


def test_tool_choice_omitted_when_none():
    cfg = tool_calling_to_configurable(ToolCalling(mode="native"))
    assert "cuga_lite_tool_choice" not in cfg
