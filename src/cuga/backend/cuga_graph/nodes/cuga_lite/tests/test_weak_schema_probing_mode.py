"""advanced_features.cuga_lite_weak_schema_probing_mode resolution and gating (issue #272 follow-up).

Sami requested the whole weak-schema tool output probing feature be gated behind a categorical
settings.toml mode, defaulting to the pre-PR #417 legacy behavior ("combine_and_execute").
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from cuga.backend.cuga_graph.nodes.cuga_lite.prompt_utils import (
    PromptUtils,
    resolve_weak_schema_probing_mode,
)


def _fake_settings(**advanced_features_kwargs):
    return SimpleNamespace(advanced_features=SimpleNamespace(**advanced_features_kwargs))


def _make_tool(response_schemas):
    func = SimpleNamespace(_response_schemas=response_schemas)
    return SimpleNamespace(name="some_tool", func=func, args_schema=None)


def test_resolve_default_when_key_absent():
    settings_obj = _fake_settings()
    assert resolve_weak_schema_probing_mode(settings_obj) == "combine_and_execute"


def test_resolve_passthrough_combine_and_execute():
    settings_obj = _fake_settings(cuga_lite_weak_schema_probing_mode="combine_and_execute")
    assert resolve_weak_schema_probing_mode(settings_obj) == "combine_and_execute"


def test_resolve_passthrough_get_first_and_execute():
    settings_obj = _fake_settings(cuga_lite_weak_schema_probing_mode="get_first_and_execute")
    assert resolve_weak_schema_probing_mode(settings_obj) == "get_first_and_execute"


def test_resolve_passthrough_truncate_at_first_probe():
    settings_obj = _fake_settings(cuga_lite_weak_schema_probing_mode="truncate_at_first_probe")
    assert resolve_weak_schema_probing_mode(settings_obj) == "truncate_at_first_probe"


def test_resolve_unknown_value_warns_and_falls_back():
    settings_obj = _fake_settings(cuga_lite_weak_schema_probing_mode="bogus_mode")
    with patch("cuga.backend.cuga_graph.nodes.cuga_lite.prompt_utils.logger.warning") as mock_warning:
        result = resolve_weak_schema_probing_mode(settings_obj)
    assert result == "combine_and_execute"
    mock_warning.assert_called_once()


def test_resolve_wrong_case_value_warns_and_falls_back():
    """Case is NOT normalized — a differently-cased value is treated as invalid, per spec."""
    settings_obj = _fake_settings(cuga_lite_weak_schema_probing_mode="Truncate_At_First_Probe")
    with patch("cuga.backend.cuga_graph.nodes.cuga_lite.prompt_utils.logger.warning") as mock_warning:
        result = resolve_weak_schema_probing_mode(settings_obj)
    assert result == "combine_and_execute"
    mock_warning.assert_called_once()


def test_resolve_uses_real_settings_when_no_override_given():
    # Pins the real settings.toml default until explicitly configured otherwise.
    assert resolve_weak_schema_probing_mode() == "combine_and_execute"


def test_is_weak_schema_tool_mode_combine_and_execute_always_false():
    empty_schema_tool = _make_tool({})
    placeholder_tool = _make_tool({"success": {"type": "string"}, "_synthetic_placeholder": True})
    assert PromptUtils.is_weak_schema_tool(empty_schema_tool, mode="combine_and_execute") is False
    assert PromptUtils.is_weak_schema_tool(placeholder_tool, mode="combine_and_execute") is False


def test_is_weak_schema_tool_mode_none_resolves_from_settings():
    tool = _make_tool({})
    with patch(
        "cuga.backend.cuga_graph.nodes.cuga_lite.prompt_utils.resolve_weak_schema_probing_mode",
        return_value="get_first_and_execute",
    ):
        assert PromptUtils.is_weak_schema_tool(tool) is True


def test_get_tool_docs_combine_and_execute_renders_no_probe_directive():
    """Under the legacy default, an undeclared-schema tool renders no directive at all —
    the literal pre-PR #417 rendering."""
    tool = _make_tool({})
    _params_doc, response_doc = PromptUtils.get_tool_docs(tool, mode="combine_and_execute")
    assert response_doc == ""
