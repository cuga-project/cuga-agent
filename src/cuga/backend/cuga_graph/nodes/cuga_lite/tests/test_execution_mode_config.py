"""Configuration surfaces for ``cuga_lite_execution_mode`` / ``cuga_lite_step_discipline``.

One assertion per control plane — settings.toml, validators, ``configurable``,
the per-model runtime profile, the SDK constructor / ``invoke`` / ``stream`` —
so a knob wired into only some surfaces cannot ship (the usual "my config does
nothing" bug). Plus the graph wiring: the ``tool_exec`` node is present on the
CugaLite graph regardless of mode, and absent when a graph does not opt in.
"""

from __future__ import annotations

import inspect
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from cuga.backend.cuga_graph.nodes.cuga_lite import model_runtime_profile as mrp
from cuga.backend.cuga_graph.nodes.cuga_lite.model_runtime_profile import (
    EXECUTION_MODE_CODEACT,
    EXECUTION_MODE_FUNCTION_CALLING,
    STEP_DISCIPLINE_OFF,
    STEP_DISCIPLINE_ONE_TOOL_PER_STEP,
    normalize_execution_mode,
    normalize_step_discipline,
    resolve_execution_mode,
    resolve_fc_prompt_fragments,
    resolve_step_discipline,
)

pytestmark = pytest.mark.unit


# ── 1. settings.toml + validators: the shipped default is the old behaviour ──


def test_settings_toml_defaults_to_codeact_and_no_discipline():
    from cuga.config import settings

    assert settings.advanced_features.cuga_lite_execution_mode == "codeact"
    assert settings.advanced_features.cuga_lite_step_discipline == "off"
    assert list(settings.advanced_features.cuga_lite_fc_prompt_fragments) == []


def test_validators_supply_the_same_defaults_without_the_keys():
    """A settings.toml without the keys must resolve identically, not AttributeError."""
    from cuga.config import validators

    declared = {v.names[0]: v.default for v in validators if v.names}
    assert declared["advanced_features.cuga_lite_execution_mode"] == "codeact"
    assert declared["advanced_features.cuga_lite_step_discipline"] == "off"
    assert declared["advanced_features.cuga_lite_fc_prompt_fragments"] == []


def test_real_settings_resolve_to_the_defaults():
    assert resolve_execution_mode({}) == EXECUTION_MODE_CODEACT
    assert resolve_step_discipline({}) == STEP_DISCIPLINE_OFF
    assert resolve_fc_prompt_fragments({}) == []


# ── 2. spellings: forgiving in, canonical out, never raising ─────────────────


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("codeact", EXECUTION_MODE_CODEACT),
        ("CodeAct", EXECUTION_MODE_CODEACT),
        ("code_act", EXECUTION_MODE_CODEACT),
        ("function_calling", EXECUTION_MODE_FUNCTION_CALLING),
        (" Function-Calling ", EXECUTION_MODE_FUNCTION_CALLING),
        ("fc", EXECUTION_MODE_FUNCTION_CALLING),
    ],
)
def test_execution_mode_aliases(raw, expected):
    assert normalize_execution_mode(raw) == expected


def test_execution_mode_accepts_only_the_documented_spellings():
    """Two canonical values plus ``fc``; anything else is a typo and falls back loudly."""
    for typo in ("native", "tool_calling", "functioncalling", "code", "sandbox", "1", "true"):
        assert normalize_execution_mode(typo) == EXECUTION_MODE_CODEACT, typo


def test_unknown_execution_mode_falls_back_to_codeact_without_raising():
    assert normalize_execution_mode("banana") == EXECUTION_MODE_CODEACT
    assert normalize_execution_mode(None) == EXECUTION_MODE_CODEACT
    assert normalize_execution_mode(42) == EXECUTION_MODE_CODEACT


@pytest.mark.parametrize(
    "raw, expected",
    [
        (None, STEP_DISCIPLINE_OFF),
        ("off", STEP_DISCIPLINE_OFF),
        (False, STEP_DISCIPLINE_OFF),
        ("bogus", STEP_DISCIPLINE_OFF),
        ("1", STEP_DISCIPLINE_OFF),
        ("stepwise", STEP_DISCIPLINE_OFF),
        ("one_tool_per_step", STEP_DISCIPLINE_ONE_TOOL_PER_STEP),
        ("One_Tool_Per_Step", STEP_DISCIPLINE_ONE_TOOL_PER_STEP),
        ("one-tool-per-step", STEP_DISCIPLINE_ONE_TOOL_PER_STEP),
        (True, STEP_DISCIPLINE_ONE_TOOL_PER_STEP),
    ],
)
def test_step_discipline_aliases(raw, expected):
    assert normalize_step_discipline(raw) == expected


def test_fragments_are_normalised_deduped_and_unknown_dropped():
    cfg = {"cuga_lite_fc_prompt_fragments": ["evidence_first", "Evidence_First", "not_a_fragment"]}
    assert resolve_fc_prompt_fragments(cfg) == ["evidence_first"]
    assert resolve_fc_prompt_fragments({"cuga_lite_fc_prompt_fragments": "evidence_first"}) == [
        "evidence_first"
    ]


# ── 3. precedence: configurable > per-model profile > settings ───────────────


def test_configurable_beats_settings():
    assert (
        resolve_execution_mode({"cuga_lite_execution_mode": "fc"}, settings_mode_fn=lambda: "codeact")
        == EXECUTION_MODE_FUNCTION_CALLING
    )
    assert (
        resolve_step_discipline({"cuga_lite_step_discipline": "off"}, settings_fn=lambda: "one_tool_per_step")
        == STEP_DISCIPLINE_OFF
    )


def test_settings_apply_when_configurable_is_silent_or_blank():
    assert (
        resolve_execution_mode({}, settings_mode_fn=lambda: "function_calling")
        == EXECUTION_MODE_FUNCTION_CALLING
    )
    # An empty string is "unset", not "codeact" — the next layer decides.
    assert (
        resolve_execution_mode({"cuga_lite_execution_mode": ""}, settings_mode_fn=lambda: "function_calling")
        == EXECUTION_MODE_FUNCTION_CALLING
    )


def test_model_profile_sits_between_configurable_and_settings(monkeypatch):
    monkeypatch.setattr(
        mrp,
        "runtime_defaults_for_model",
        lambda name: (
            {"cuga_lite_execution_mode": "fc", "cuga_lite_step_discipline": "one_tool_per_step"}
            if name == "profiled-model"
            else {}
        ),
    )
    # profile beats settings
    assert resolve_execution_mode({}, "profiled-model", settings_mode_fn=lambda: "codeact") == (
        EXECUTION_MODE_FUNCTION_CALLING
    )
    assert resolve_step_discipline({}, "profiled-model", settings_fn=lambda: "off") == (
        STEP_DISCIPLINE_ONE_TOOL_PER_STEP
    )
    # configurable beats profile
    assert (
        resolve_execution_mode(
            {"cuga_lite_execution_mode": "codeact"}, "profiled-model", settings_mode_fn=lambda: "fc"
        )
        == EXECUTION_MODE_CODEACT
    )
    # an unprofiled model falls through to settings
    assert (
        resolve_execution_mode({}, "other-model", settings_mode_fn=lambda: "codeact")
        == EXECUTION_MODE_CODEACT
    )


# ── 4. SDK: constructor default, per-call override, raw keys win ─────────────


def test_cuga_agent_accepts_execution_mode_and_step_discipline_everywhere():
    from cuga.sdk import CugaAgent

    for method in (CugaAgent.__init__, CugaAgent.invoke, CugaAgent.stream):
        params = inspect.signature(method).parameters
        assert "execution_mode" in params, method.__name__
        assert "step_discipline" in params, method.__name__
        assert params["execution_mode"].default is None
        assert params["step_discipline"].default is None


def test_apply_execution_mode_writes_the_configurable_keys():
    from cuga.sdk import CugaAgent

    agent = SimpleNamespace(_execution_mode="function_calling", _step_discipline="one_tool_per_step")
    run_config = {"configurable": {}}
    CugaAgent._apply_execution_mode(agent, run_config)
    assert run_config["configurable"] == {
        "cuga_lite_execution_mode": "function_calling",
        "cuga_lite_step_discipline": "one_tool_per_step",
    }


def test_per_invoke_values_override_the_constructor_default():
    from cuga.sdk import CugaAgent

    agent = SimpleNamespace(_execution_mode="function_calling", _step_discipline=None)
    run_config = {"configurable": {}}
    CugaAgent._apply_execution_mode(
        agent, run_config, execution_mode="codeact", step_discipline="one_tool_per_step"
    )
    assert run_config["configurable"]["cuga_lite_execution_mode"] == "codeact"
    assert run_config["configurable"]["cuga_lite_step_discipline"] == "one_tool_per_step"


def test_explicit_raw_keys_are_never_clobbered():
    from cuga.sdk import CugaAgent

    agent = SimpleNamespace(_execution_mode="function_calling", _step_discipline="one_tool_per_step")
    run_config = {"configurable": {"cuga_lite_execution_mode": "codeact", "cuga_lite_step_discipline": "off"}}
    CugaAgent._apply_execution_mode(agent, run_config, execution_mode="fc")
    assert run_config["configurable"]["cuga_lite_execution_mode"] == "codeact"
    assert run_config["configurable"]["cuga_lite_step_discipline"] == "off"


def test_apply_execution_mode_is_a_noop_when_unconfigured():
    from cuga.sdk import CugaAgent

    agent = SimpleNamespace(_execution_mode=None, _step_discipline=None)
    run_config = {"configurable": {}}
    CugaAgent._apply_execution_mode(agent, run_config)
    assert run_config["configurable"] == {}, "settings.toml must decide when nothing is passed"


# ── 5. graph wiring ──────────────────────────────────────────────────────────


def _dummy_nodes():
    async def prepare(state, config=None):
        return {}

    async def call_model(state, config=None):
        return {}

    async def execute(state, config=None):
        return {}

    async def tool_exec(state, config=None):
        return {}

    return prepare, call_model, execute, tool_exec


def test_build_agent_graph_adds_tool_exec_only_when_given():
    from cuga.backend.cuga_graph.nodes.cuga_agent_core.graph.shared_graph import build_agent_graph
    from cuga.backend.cuga_graph.nodes.cuga_lite.cuga_lite_graph import CugaLiteState

    prepare, call_model, execute, tool_exec = _dummy_nodes()
    adapter = SimpleNamespace(execute_node_name="sandbox")

    without = build_agent_graph(
        adapter=adapter,
        state_class=CugaLiteState,
        prepare_node=prepare,
        call_model_node=call_model,
        execute_node=execute,
    )
    assert "tool_exec" not in without.nodes, "a graph that does not opt in must be unchanged"

    with_node = build_agent_graph(
        adapter=adapter,
        state_class=CugaLiteState,
        prepare_node=prepare,
        call_model_node=call_model,
        execute_node=execute,
        tool_exec_node=tool_exec,
    )
    assert "tool_exec" in with_node.nodes
    assert ("tool_exec", "call_model") not in with_node.edges, "it routes with a Command, no static edge"


def test_cuga_lite_graph_always_wires_tool_exec_so_mode_can_switch_per_invoke():
    from cuga.backend.cuga_graph.nodes.cuga_lite.cuga_lite_graph import create_cuga_lite_graph

    provider = MagicMock()
    provider.get_all_tools = AsyncMock(return_value=[])
    provider.get_apps = AsyncMock(return_value=[])
    provider.get_tools = AsyncMock(return_value=[])
    graph = create_cuga_lite_graph(model=MagicMock(), tool_provider=provider, apps_list=[])

    assert {"prepare", "call_model", "sandbox", "tool_exec"} <= set(graph.nodes)
    assert ("tool_exec", "call_model") not in graph.edges
