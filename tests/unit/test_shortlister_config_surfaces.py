"""One assertion per configuration surface.

A setting wired into only some surfaces is the usual reason "my config does
nothing" bugs happen. Each test here covers one control plane end to end.
"""

from unittest.mock import patch

import pytest

pytestmark = pytest.mark.unit


# --- 1. settings.toml -------------------------------------------------------


def test_settings_toml_ships_the_shortlister_section():
    from cuga.config import settings

    assert settings.shortlister.strategy == "llm"
    assert settings.shortlister.threshold == 128
    assert settings.shortlister.top_k == 128
    assert settings.shortlister.max_results == 10
    assert settings.shortlister.embedding_model == "BAAI/bge-small-en-v1.5"
    assert settings.shortlister.embedding_provider == "local"


# --- 2 & 3. validators supply defaults without the section ------------------


def test_validators_cover_every_plan_field():
    """A settings.toml missing [shortlister] must not AttributeError at runtime."""
    from cuga.config import validators
    from cuga.backend.cuga_graph.nodes.cuga_lite.shortlister.plan import ALL_FIELDS

    declared = {
        v.names[0].split(".", 1)[1] for v in validators if v.names and v.names[0].startswith("shortlister.")
    }
    assert set(ALL_FIELDS) <= declared, f"missing validators for {set(ALL_FIELDS) - declared}"


def test_missing_shortlisting_tool_threshold_validator_regression():
    """prepare_node reads this without a getattr guard (see design doc §7.1)."""
    from cuga.config import settings

    assert settings.advanced_features.shortlisting_tool_threshold == 35


# --- 4. configurable (per-invoke) ------------------------------------------


def test_configurable_overrides_settings():
    from cuga.config import settings
    from cuga.backend.cuga_graph.nodes.cuga_lite.shortlister import ShortlisterRouter

    plan = ShortlisterRouter.resolve(
        settings,
        configurable={"shortlister_strategy": "hybrid", "shortlister_max_results": 3},
    )
    assert plan.strategy == "hybrid"
    assert plan.max_results == 3


@pytest.mark.asyncio
async def test_configurable_reaches_find_tools_through_run_config():
    """The full path: RunnableConfig -> prompt_utils -> router -> strategy."""
    from langchain_core.tools import StructuredTool
    from cuga.backend.cuga_graph.nodes.cuga_lite.prompt_utils import PromptUtils

    def fn(**kwargs):
        return "x"

    fn.__name__ = "sample_tool"
    tools = [StructuredTool.from_function(func=fn, name="sample_tool", description="d")]

    captured = {}

    async def _capture(self, request):
        from cuga.backend.cuga_graph.nodes.cuga_lite.shortlister import ShortlistResult

        captured["max_results"] = request.max_results
        captured["top_k"] = request.top_k
        return ShortlistResult()

    with patch(
        "cuga.backend.cuga_graph.nodes.cuga_lite.shortlister.embedding.EmbeddingShortlister.shortlist",
        _capture,
    ):
        await PromptUtils.find_tools(
            query="q",
            all_tools=tools,
            all_apps=[],
            run_config={
                "configurable": {
                    "shortlister_strategy": "embedding",
                    "shortlister_threshold": 0,
                    "shortlister_max_results": 5,
                    "shortlister_top_k": 7,
                }
            },
        )

    assert captured["max_results"] == 5
    assert captured["top_k"] == 7


def test_malformed_run_config_is_tolerated():
    from cuga.backend.cuga_graph.nodes.cuga_lite.prompt_utils import _configurable_of

    assert _configurable_of(None) == {}
    assert _configurable_of({}) == {}
    assert _configurable_of({"configurable": None}) == {}
    assert _configurable_of("nonsense") == {}
    assert _configurable_of({"configurable": {"a": 1}}) == {"a": 1}


# --- 5. SDK -----------------------------------------------------------------


def test_cuga_agent_accepts_a_shortlister_argument():
    import inspect
    from cuga.sdk import CugaAgent

    for method in (CugaAgent.__init__, CugaAgent.invoke, CugaAgent.stream):
        assert "shortlister" in inspect.signature(method).parameters, method.__name__


def test_apply_shortlister_writes_configurable_keys():
    from cuga.sdk import CugaAgent
    from cuga.backend.cuga_graph.nodes.cuga_lite.shortlister import Shortlister

    agent = CugaAgent.__new__(CugaAgent)
    agent._shortlister = Shortlister(strategy="hybrid", top_k=32)
    run_config = {"configurable": {}}
    CugaAgent._apply_shortlister(agent, run_config)

    assert run_config["configurable"]["shortlister_strategy"] == "hybrid"
    assert run_config["configurable"]["shortlister_top_k"] == 32


def test_per_invoke_shortlister_overrides_the_constructor_default():
    from cuga.sdk import CugaAgent
    from cuga.backend.cuga_graph.nodes.cuga_lite.shortlister import Shortlister

    agent = CugaAgent.__new__(CugaAgent)
    agent._shortlister = Shortlister(strategy="hybrid")
    run_config = {"configurable": {}}
    CugaAgent._apply_shortlister(agent, run_config, Shortlister(strategy="embedding"))

    assert run_config["configurable"]["shortlister_strategy"] == "embedding"


def test_explicit_raw_keys_are_never_clobbered():
    from cuga.sdk import CugaAgent
    from cuga.backend.cuga_graph.nodes.cuga_lite.shortlister import Shortlister

    agent = CugaAgent.__new__(CugaAgent)
    agent._shortlister = Shortlister(strategy="hybrid")
    run_config = {"configurable": {"shortlister_strategy": "llm"}}
    CugaAgent._apply_shortlister(agent, run_config)

    assert run_config["configurable"]["shortlister_strategy"] == "llm"


def test_apply_shortlister_is_a_noop_when_unconfigured():
    """The default path must stay untouched."""
    from cuga.sdk import CugaAgent

    agent = CugaAgent.__new__(CugaAgent)
    agent._shortlister = None
    run_config = {"configurable": {}}
    CugaAgent._apply_shortlister(agent, run_config)

    assert run_config == {"configurable": {}}


def test_apply_shortlister_never_raises_on_bad_input():
    from cuga.sdk import CugaAgent

    agent = CugaAgent.__new__(CugaAgent)
    agent._shortlister = object()  # not a Shortlister
    run_config = {"configurable": {}}
    CugaAgent._apply_shortlister(agent, run_config)  # must not raise


# --- 6. public export -------------------------------------------------------


def test_shortlister_is_importable_from_the_package_root():
    from cuga import Shortlister, ShortlisterStrategy

    assert Shortlister(strategy="hybrid").strategy == "hybrid"
    assert ShortlisterStrategy is not None


def test_shortlister_is_in_dunder_all():
    import cuga

    assert "Shortlister" in cuga.__all__
    assert "ShortlisterStrategy" in cuga.__all__
