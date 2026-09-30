"""tool_search cascade + deferred bind set (fc-deferred-tool-search §3.1-3.8)."""

from __future__ import annotations

from typing import List
from unittest.mock import MagicMock

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import StructuredTool

from cuga.backend.cuga_graph.nodes.cuga_lite.bind_tools import deferred
from cuga.backend.cuga_graph.nodes.cuga_lite.bind_tools.deferred import (
    DeferredToolSurface,
    bind_names,
    discovered_in_history,
    loaded_within_budget,
    normalize_tool_search_mode,
    tool_search_active,
)
from cuga.backend.cuga_graph.nodes.cuga_lite.helpers import tool_search as ts
from cuga.backend.cuga_graph.nodes.cuga_lite.helpers.tool_search import (
    TOOL_SEARCH_NAME,
    ToolSearchConfig,
    ToolSearchIndex,
    ToolSearchResult,
    prepare_tool_search_surface,
    render_tool_search_section,
    search,
)
from cuga.backend.cuga_graph.nodes.cuga_lite.providers.registry import create_tool_from_api_dict
from cuga.backend.cuga_graph.state.agent_state import union_ordered

pytestmark = pytest.mark.unit


def _tool(name: str, app: str, description: str = "", params: List[dict] = ()):
    return create_tool_from_api_dict(name, {"description": description, "parameters": list(params)}, app)


HOCKEY = [
    _tool("hockey_get_player_coach_ids", "hockey", "Coach ids a player has had."),
    _tool("hockey_get_coaches_who_were_players", "hockey", "Coaches who previously played in the league."),
    _tool(
        "hockey_list_games",
        "hockey",
        "Games in a season.",
        [{"name": "season", "type": "integer", "required": True}],
    ),
]
MOVIES = [
    _tool("movies_list_customers", "movies", "All customers with their rentals."),
    _tool("movies_get_customer", "movies", "One customer by id."),
]
CATALOGUE = HOCKEY + MOVIES


@pytest.fixture(autouse=True)
def _no_model_stages(monkeypatch):
    """Embedding / LLM stages are opt-in per test; default: unavailable."""

    async def unavailable(*args, **kwargs):
        return None

    monkeypatch.setattr(ts, "_run_strategy", unavailable)


def _index():
    return ToolSearchIndex(CATALOGUE, apps=[])


# ── cascade ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_select_loads_exact_tools():
    result = await search(_index(), "select:movies_get_customer,hockey_list_games")
    assert result.stage == "select"
    assert result.discovered == ["movies_get_customer", "hockey_list_games"]
    assert "Not found" not in result.text


@pytest.mark.asyncio
async def test_select_with_guessed_names_falls_back_to_keyword_search():
    """M3 hockey: the model guessed ``select:team_stats`` / ``select:get_players_by_age``."""
    result = await search(_index(), "select:movies_get_customer,get_customers_list")
    assert result.stage == "select+keyword"
    assert result.discovered[0] == "movies_get_customer", "exact names stay first"
    assert "movies_list_customers" in result.discovered
    assert "Not found: get_customers_list. Loaded the closest matches instead." in result.text


@pytest.mark.asyncio
async def test_select_with_nothing_close_says_so():
    result = await search(_index(), "select:zebra_feeding_schedule")
    assert result.discovered == [] and "Not found: zebra_feeding_schedule." in result.text


@pytest.mark.asyncio
async def test_exact_name_query_is_a_select():
    result = await search(_index(), "hockey_list_games")
    assert (result.stage, result.discovered) == ("exact", ["hockey_list_games"])


@pytest.mark.asyncio
async def test_results_render_as_signatures_capped_at_max_results():
    result = await search(_index(), "+hockey", max_results=2)
    assert len(result.discovered) == 2
    assert result.text.startswith("Loaded 2 tools (callable on your next step):")
    assert all(name in result.text for name in result.discovered)


@pytest.mark.asyncio
async def test_signature_carries_the_parameter_types():
    result = await search(_index(), "select:hockey_list_games")
    assert "hockey_list_games(season: int)" in result.text


@pytest.mark.asyncio
async def test_app_name_restricts_the_pool():
    result = await search(_index(), "customer", app_name="hockey")
    assert not set(result.discovered) & {t.name for t in MOVIES}


@pytest.mark.asyncio
async def test_unknown_app_name_searches_everything_and_says_so():
    result = await search(_index(), "customer", app_name="movie")
    assert "Unknown app 'movie'" in result.text and "Did you mean: movies" in result.text
    assert "movies_get_customer" in result.discovered


@pytest.mark.asyncio
async def test_app_named_in_the_query_narrows_the_pool():
    result = await search(_index(), "hockey customer")
    assert all(n.startswith("hockey_") for n in result.discovered)


@pytest.mark.asyncio
async def test_no_match_lists_apps_and_the_select_form():
    result = await search(_index(), "zebra")
    assert result.discovered == []
    assert "No matching tools for 'zebra'" in result.text and "select:<name>" in result.text
    assert "hockey" in result.text and "movies" in result.text


@pytest.mark.asyncio
async def test_confident_keyword_hit_never_calls_a_model(monkeypatch):
    calls = []

    async def spy(strategy, *args, **kwargs):
        calls.append(strategy)
        return None

    monkeypatch.setattr(ts, "_run_strategy", spy)
    result = await search(
        _index(), "movies customer rentals", config=ToolSearchConfig(min_score=10, margin_ratio=0.2)
    )
    assert result.stage == "keyword"
    assert calls == []


@pytest.mark.asyncio
async def test_low_confidence_escalates_to_embedding_then_llm(monkeypatch):
    calls = []

    async def fake(strategy, query, pool, apps, **kwargs):
        calls.append(strategy)
        return None if strategy == "embedding" else ["hockey_get_coaches_who_were_players"]

    monkeypatch.setattr(ts, "_run_strategy", fake)
    config = ToolSearchConfig(min_score=1000)  # nothing is ever confident
    result = await search(_index(), "hockey coaches players", config=config, llm=object())
    assert calls == ["embedding", "llm"]
    assert result.stage == "llm"
    assert result.discovered[0] == "hockey_get_coaches_who_were_players", "the LLM's pick leads"
    # ... but never replaces the list (M3 hockey #10): keyword siblings fill the slots.
    assert "hockey_get_player_coach_ids" in result.discovered and len(result.discovered) > 1


@pytest.mark.asyncio
async def test_embedding_that_agrees_with_keyword_suppresses_the_llm(monkeypatch):
    calls = []

    async def fake(strategy, query, pool, apps, **kwargs):
        calls.append(strategy)
        # keyword's top pick (player_coach_ids) is in the embedding top-k → agreement
        return (
            ["hockey_get_coaches_who_were_players", "hockey_get_player_coach_ids"]
            if strategy == "embedding"
            else ["x"]
        )

    monkeypatch.setattr(ts, "_run_strategy", fake)
    result = await search(
        _index(), "hockey coaches players", config=ToolSearchConfig(min_score=1000), llm=object()
    )
    assert calls == ["embedding"]
    assert result.stage == "embedding"
    assert set(result.discovered[:2]) == {
        "hockey_get_coaches_who_were_players",
        "hockey_get_player_coach_ids",
    }


@pytest.mark.asyncio
async def test_llm_fallback_never_stays_deterministic(monkeypatch):
    calls = []

    async def fake(strategy, *args, **kwargs):
        calls.append(strategy)
        return None

    monkeypatch.setattr(ts, "_run_strategy", fake)
    config = ToolSearchConfig(min_score=1000, llm_fallback="never")
    result = await search(_index(), "hockey coaches players", config=config, llm=object())
    assert calls == ["embedding"] and result.stage == "keyword" and result.discovered


@pytest.mark.asyncio
async def test_llm_pool_is_capped(monkeypatch):
    seen = {}

    async def fake(strategy, query, pool, apps, **kwargs):
        seen[strategy] = len(pool)
        return None

    monkeypatch.setattr(ts, "_run_strategy", fake)
    big = [_tool(f"crm_customer_{i}", "crm", "customer") for i in range(200)]
    await search(
        ToolSearchIndex(big), "crm customer", config=ToolSearchConfig(min_score=10_000), llm=object()
    )
    assert seen["llm"] <= ts.MAX_LLM_POOL


def test_confidence_rule():
    config = ToolSearchConfig(min_score=10, margin_ratio=0.2)
    assert ts._is_confident([("a", 20), ("b", 16)], 1, config), "lead of exactly 20%"
    assert not ts._is_confident([("a", 20), ("b", 17)], 1, config), "cut-off result too close"
    assert not ts._is_confident([("a", 200), ("b", 170)], 1, config), "relative: big raw gap is not enough"
    assert not ts._is_confident([("a", 8)], 5, config), "below min score"
    assert ts._is_confident([("a", 12)], 5, config), "nothing cut → margin against 0"


# ── deferred bind set ────────────────────────────────────────────────────────


def _surface(**kw):
    search_tool = StructuredTool.from_function(func=lambda query: "", name=TOOL_SEARCH_NAME, description="d")
    by_name = {t.name: t for t in CATALOGUE}
    by_name[TOOL_SEARCH_NAME] = search_tool
    defaults = dict(
        core=[TOOL_SEARCH_NAME],
        pinned=["movies_get_customer"],
        deferred=[t.name for t in CATALOGUE if t.name != "movies_get_customer"],
        tools_by_name=by_name,
    )
    defaults.update(kw)
    return DeferredToolSurface(**defaults)


def _search_message(names, call_id="s1"):
    return ToolMessage(
        content="Loaded",
        tool_call_id=call_id,
        name=TOOL_SEARCH_NAME,
        artifact=ToolSearchResult(text="", discovered=list(names)).artifact(),
    )


def test_initial_bind_is_core_and_pinned_only():
    names, evicted = bind_names(_surface(), [HumanMessage(content="hi")])
    assert names == [TOOL_SEARCH_NAME, "movies_get_customer"] and evicted == []


def test_discovered_tools_are_bound_in_discovery_order():
    history = [
        HumanMessage(content="hi"),
        _search_message(["hockey_list_games", "hockey_get_player_coach_ids"]),
        _search_message(["movies_list_customers"], "s2"),
    ]
    names, _ = bind_names(_surface(), history)
    assert names == [
        TOOL_SEARCH_NAME,
        "movies_get_customer",
        "hockey_list_games",
        "hockey_get_player_coach_ids",
        "movies_list_customers",
    ]


def test_a_successful_direct_call_promotes_the_tool_and_a_failed_one_does_not():
    history = [
        ToolMessage(content="ok", tool_call_id="a", name="hockey_list_games"),
        ToolMessage(content="bad", tool_call_id="b", name="movies_list_customers", status="error"),
    ]
    assert discovered_in_history(history, _surface().deferred) == ["hockey_list_games"]


def test_state_carry_survives_when_history_was_summarized_away():
    names, _ = bind_names(
        _surface(), [HumanMessage(content="summary")], state_discovered=["hockey_list_games"]
    )
    assert "hockey_list_games" in names


def test_seed_is_bound_from_the_first_call():
    names, _ = bind_names(_surface(seed=["hockey_list_games"]), [])
    assert "hockey_list_games" in names


def test_count_cap_evicts_least_recently_used_but_keeps_order():
    surface = _surface(max_loaded=2)
    history = [
        _search_message(["hockey_list_games", "hockey_get_player_coach_ids", "movies_list_customers"]),
        AIMessage(content="", tool_calls=[{"name": "hockey_list_games", "args": {}, "id": "c1"}]),
    ]
    loaded, evicted = loaded_within_budget(
        surface, ["hockey_list_games", "hockey_get_player_coach_ids", "movies_list_customers"], history
    )
    # hockey_list_games was used most recently; of the never-used two, the newer discovery wins.
    assert loaded == ["hockey_list_games", "movies_list_customers"]
    assert evicted == ["hockey_get_player_coach_ids"]


def test_token_budget_evicts_but_always_keeps_one():
    surface = _surface(token_budget=1)
    loaded, evicted = loaded_within_budget(surface, ["hockey_list_games", "movies_list_customers"], [])
    assert len(loaded) == 1 and len(evicted) == 1


def test_core_and_pinned_are_never_evicted():
    surface = _surface(max_loaded=0)
    names, _ = bind_names(surface, [_search_message(["hockey_list_games"])])
    assert names == [TOOL_SEARCH_NAME, "movies_get_customer"]


def test_bind_deferred_binds_minified_dicts(monkeypatch):
    model = MagicMock()
    deferred.bind_deferred(model, _surface(), [TOOL_SEARCH_NAME, "hockey_list_games"])
    (specs,), _ = model.bind_tools.call_args
    assert [s["function"]["name"] for s in specs] == [TOOL_SEARCH_NAME, "hockey_list_games"]
    assert all('"title"' not in str(s) for s in specs)


@pytest.mark.parametrize(
    ("raw", "mode"),
    [("on", "on"), ("OFF", "off"), ("auto", "auto"), (True, "on"), (False, "off"), ("?", "auto")],
)
def test_normalize_mode(raw, mode):
    assert normalize_tool_search_mode(raw) == mode


def test_activation_rule():
    assert tool_search_active("auto", 36, 35) and not tool_search_active("auto", 35, 35)
    assert tool_search_active("on", 1, 35) and not tool_search_active("on", 0, 35)
    assert not tool_search_active("off", 1000, 35)


def test_union_ordered_reducer_is_append_only():
    assert union_ordered(["a", "b"], ["b", "c"]) == ["a", "b", "c"]
    assert union_ordered(["a"], []) == ["a"], "a rebuilt state with the default cannot unload tools"
    assert union_ordered(None, None) == []


# ── prepare-time surface + prompt ────────────────────────────────────────────


def _prepare(**overrides):
    tools_context = {t.name: (lambda **kw: None) for t in CATALOGUE}
    todos = StructuredTool.from_function(func=lambda: "", name="create_update_todos", description="d")
    tools_context["create_update_todos"] = lambda **kw: None
    find_tools = StructuredTool.from_function(
        func=lambda query, app_name: "", name="find_tools", description="d"
    )
    tools_context["find_tools"] = lambda **kw: None
    kwargs = dict(
        tools_for_execution=CATALOGUE,
        tools_for_prompt=[find_tools, todos],
        tools_context=tools_context,
        apps=[],
        configurable={"cuga_lite_tool_search": "on"},
        model_name="",
        llm=None,
        task_context="which hockey games were played",
        threshold=35,
    )
    kwargs.update(overrides)
    return prepare_tool_search_surface(**kwargs), tools_context


def test_prepare_builds_tiers_and_registers_tool_search():
    (surface, index), tools_context = _prepare()
    assert surface.core == [TOOL_SEARCH_NAME, "create_update_todos"], "find_tools is superseded"
    assert set(surface.deferred) == {t.name for t in CATALOGUE}
    assert callable(tools_context[TOOL_SEARCH_NAME])
    assert set(index.by_name) == set(surface.deferred)


def test_prepare_pins_configured_apps():
    (surface, _), _ = _prepare(
        configurable={"cuga_lite_tool_search": "on", "cuga_lite_bind_tools_apps": ["movies"]}
    )
    assert set(surface.pinned) == {t.name for t in MOVIES}
    assert not set(surface.deferred) & set(surface.pinned)


def test_prepare_is_inactive_below_threshold_in_auto_mode():
    prepared, tools_context = _prepare(configurable={"cuga_lite_tool_search": "auto"})
    assert prepared is None and TOOL_SEARCH_NAME not in tools_context


def test_prepare_seed_uses_the_keyword_stage_only():
    (surface, _), _ = _prepare(
        configurable={"cuga_lite_tool_search": "on", "cuga_lite_tool_search_seed_k": 2}
    )
    assert surface.seed and surface.seed[0] == "hockey_list_games"


def test_prompt_section_counts_per_app_and_lists_names_below_the_limit():
    index = _index()
    text = render_tool_search_section(index, loaded=["hockey_list_games"], list_names_max=150)
    assert "- hockey (1/3)" in text and "- movies (0/2)" in text
    assert "movies_get_customer" in text
    assert "movies_get_customer" not in render_tool_search_section(index, list_names_max=2)


# ── auto trigger by context share (§3.2) ─────────────────────────────────────


def test_auto_activates_on_schema_share_of_the_context_window():
    assert tool_search_active("auto", 5, 35, schema_tokens=13_000, context_tokens=128_000, context_pct=10)
    assert not tool_search_active("auto", 5, 35, schema_tokens=12_000, context_tokens=128_000, context_pct=10)
    assert not tool_search_active("auto", 5, 35, schema_tokens=99_999, context_tokens=128_000, context_pct=0)
    assert not tool_search_active("off", 5, 35, schema_tokens=99_999, context_tokens=128_000, context_pct=10)


def test_prepare_auto_defers_a_few_huge_schemas(monkeypatch):
    from cuga.backend.cuga_graph.nodes.cuga_lite.bind_tools import deferred as deferred_mod

    monkeypatch.setattr(deferred_mod, "context_tokens_for", lambda model_name: 1_000)
    prepared, _ = _prepare(
        configurable={"cuga_lite_tool_search": "auto", "cuga_lite_tool_search_context_pct": 10}
    )
    assert prepared is not None, "5 small tools still exceed 10% of a 1K-token window"

    monkeypatch.setattr(deferred_mod, "context_tokens_for", lambda model_name: 1_000_000)
    prepared, _ = _prepare(
        configurable={"cuga_lite_tool_search": "auto", "cuga_lite_tool_search_context_pct": 10}
    )
    assert prepared is None


@pytest.mark.asyncio
async def test_embedding_that_disagrees_with_keyword_escalates_to_the_llm(monkeypatch):
    calls = []

    async def fake(strategy, query, pool, apps, **kwargs):
        calls.append(strategy)
        if strategy == "embedding":
            return ["hockey_list_games"]  # keyword's top pick is absent → disagreement
        return ["hockey_get_coaches_who_were_players"]

    monkeypatch.setattr(ts, "_run_strategy", fake)
    result = await search(
        _index(),
        "hockey coaches players",
        config=ToolSearchConfig(min_score=1000),
        llm=object(),
        max_results=1,
    )
    assert calls == ["embedding", "llm"]
    assert result.stage == "llm"


@pytest.mark.asyncio
async def test_slow_stages_degrade_to_keyword_instead_of_failing(monkeypatch):
    import asyncio

    async def slow(strategy, *args, **kwargs):
        await asyncio.sleep(5)
        return ["never"]

    monkeypatch.setattr(ts, "_run_strategy", slow)
    config = ToolSearchConfig(min_score=1000, time_budget_s=1.0)
    result = await search(_index(), "hockey coaches players", config=config, llm=object())
    assert result.stage == "keyword" and result.discovered
    assert "never" not in result.discovered


# ── searched but never called a tool (one-shot correction) ───────────────────


def test_search_only_turn_detection():
    from cuga.backend.cuga_graph.nodes.cuga_lite.bind_tools.deferred import search_only_turn

    turn = [HumanMessage(content="q"), _search_message(["hockey_list_games"])]
    assert search_only_turn(turn, 0) == ["hockey_list_games"]
    called = turn + [ToolMessage(content="err", tool_call_id="c", name="hockey_list_games", status="error")]
    assert search_only_turn(called, 0) is None, "a domain call (even a failed one) is never second-guessed"
    assert search_only_turn([HumanMessage(content="hello")], 0) is None, "no search → plain chat"
    empty = [HumanMessage(content="q"), _search_message([])]
    assert search_only_turn(empty, 0) is None, "nothing loaded → nothing to call"
    # Earlier turns do not count.
    two_turns = [
        HumanMessage(content="a"),
        ToolMessage(content="x", tool_call_id="c", name="hockey_list_games"),
    ]
    two_turns += [HumanMessage(content="b"), _search_message(["movies_get_customer"], "s9")]
    assert search_only_turn(two_turns, 2) == ["movies_get_customer"]


# ── listing an app's tools (select:* / bare select) ──────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("query", ["select:*", "select", "select:", "*", ""])
async def test_listing_queries_list_the_apps_tool_names(query):
    result = await search(_index(), query, app_name="hockey")
    assert result.stage == "listing" and result.discovered == [], "listing loads nothing"
    assert "hockey has 3 tools" in result.text
    assert all(t.name in result.text for t in HOCKEY) and "movies_get_customer" not in result.text


@pytest.mark.asyncio
async def test_listing_without_an_app_lists_the_apps():
    result = await search(_index(), "select:*")
    assert "hockey (3)" in result.text and "movies (2)" in result.text and 'query "select:*"' in result.text


def test_prompt_section_teaches_listing_and_discourages_early_refusal():
    text = render_tool_search_section(_index(), list_names_max=0)
    assert "select:* with app_name" in text and "do not tell the user a tool does not exist" in text
