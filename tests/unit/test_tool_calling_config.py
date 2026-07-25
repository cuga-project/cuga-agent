"""ToolCalling -> configurable serialization (issue #471 P1)."""

from cuga import ToolCalling
from cuga.backend.cuga_graph.nodes.cuga_lite.tool_calling import tool_calling_to_configurable


def test_none_serializes_to_empty_but_explicit_code_is_full_off():
    # None = no opt-in -> {} (global setting / model profile still apply).
    assert tool_calling_to_configurable(None) == {}
    # Explicit mode="code" (incl. the default ToolCalling()) is a FULL opt-out:
    # code-act handling + tools unbound, overriding a global native default and
    # any profile (e.g. gpt-oss-20b) that would otherwise bind.
    expected = {"cuga_lite_tool_invocation_mode": "code", "cuga_lite_bind_tools_mode": "none"}
    assert tool_calling_to_configurable(ToolCalling()) == expected
    assert tool_calling_to_configurable(ToolCalling(mode="code")) == expected


def test_code_opt_out_unbinds_a_profile_that_would_bind():
    # end-to-end: ToolCalling(mode="code") must override a model profile that
    # auto-binds (gpt-oss-20b), so an explicit code opt-out is truly code-act.
    from cuga.backend.cuga_graph.nodes.cuga_lite.helpers.bind_tools import (
        _bind_include_find_tools_from_config,
        _bind_tools_apps_from_settings,
        _bind_tools_mode_from_settings,
        _bind_tools_tool_names_from_settings,
    )
    from cuga.backend.cuga_graph.nodes.cuga_lite.model_runtime_profile import resolve_bind_tools_fields

    def _resolve(cfg):
        mode, *_ = resolve_bind_tools_fields(
            cfg,
            "gpt-oss-20b",
            settings_mode_fn=_bind_tools_mode_from_settings,
            settings_apps_fn=_bind_tools_apps_from_settings,
            settings_tool_names_fn=_bind_tools_tool_names_from_settings,
            settings_include_fn=lambda: _bind_include_find_tools_from_config({}),
        )
        return mode

    assert _resolve({}) == "apps"  # profile binds by default
    assert _resolve(tool_calling_to_configurable(ToolCalling(mode="code"))) == "none"  # opt-out unbinds


def test_native_all_tools():
    cfg = tool_calling_to_configurable(ToolCalling(mode="native"))
    assert cfg == {
        "cuga_lite_tool_invocation_mode": "native",
        "cuga_lite_bind_tools_mode": "all",
        # include_find_tools is always pinned so a global/profile can't widen it
        "cuga_lite_bind_tools_include_find_tools": False,
    }


def test_include_find_tools_is_always_pinned_for_native():
    # False must be serialized explicitly (not omitted) so a global setting or
    # model profile that enables find_tools can't widen an explicit selection.
    off = tool_calling_to_configurable(ToolCalling(mode="native", native_tools=["a"]))
    assert off["cuga_lite_bind_tools_include_find_tools"] is False
    on = tool_calling_to_configurable(ToolCalling(mode="native", include_find_tools=True))
    assert on["cuga_lite_bind_tools_include_find_tools"] is True


def test_serialization_fails_closed_to_code_unbind():
    # a broken tc must fall back to fully-off (code + unbind), NEVER {} which
    # would let a global/profile keep native binding enabled.
    class _Boom:
        mode = "native"

        def __getattr__(self, name):
            raise RuntimeError("boom")

    cfg = tool_calling_to_configurable(_Boom())
    assert cfg == {"cuga_lite_tool_invocation_mode": "code", "cuga_lite_bind_tools_mode": "none"}


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


def test_mode_resolver_defaults_to_code():
    from cuga.backend.cuga_graph.nodes.cuga_lite.tool_calling import resolve_tool_invocation_mode

    assert resolve_tool_invocation_mode(None) == "code"
    assert resolve_tool_invocation_mode({}) == "code"
    assert resolve_tool_invocation_mode({"cuga_lite_tool_invocation_mode": "bogus"}) == "code"


def test_mode_resolver_honors_global_setting():
    from unittest.mock import patch

    from cuga.backend.cuga_graph.nodes.cuga_lite.tool_calling import (
        native_tool_calls_enabled,
        resolve_tool_invocation_mode,
    )
    from cuga.config import settings

    # global setting alone turns it on (no configurable needed)
    with patch.object(settings.advanced_features, "cuga_lite_tool_invocation_mode", "native", create=True):
        assert resolve_tool_invocation_mode(None) == "native"
        assert native_tool_calls_enabled({}) is True
        # explicit configurable still wins over the global setting
        assert resolve_tool_invocation_mode({"cuga_lite_tool_invocation_mode": "code"}) == "code"
