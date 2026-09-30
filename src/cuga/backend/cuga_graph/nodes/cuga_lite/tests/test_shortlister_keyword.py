"""Keyword ranker — Claude Code ``searchToolsWithKeywords`` port (fc-deferred-tool-search §3.4)."""

from __future__ import annotations

import pytest

from cuga.backend.cuga_graph.nodes.cuga_lite.providers.registry import create_tool_from_api_dict
from cuga.backend.cuga_graph.nodes.cuga_lite.shortlister.base import ShortlistRequest
from cuga.backend.cuga_graph.nodes.cuga_lite.shortlister.factory import (
    clear_instance_cache,
    resolve_shortlister,
)
from cuga.backend.cuga_graph.nodes.cuga_lite.shortlister.keyword import (
    KeywordShortlister,
    fold,
    parse_query,
    rank,
    split_name,
)
from cuga.backend.cuga_graph.nodes.cuga_lite.shortlister.plan import ShortlisterPlan

pytestmark = pytest.mark.unit


def _tool(name: str, app: str, description: str = "", operation_id: str = None):
    return create_tool_from_api_dict(
        name, {"description": description, "parameters": [], "operation_id": operation_id}, app
    )


CATALOGUE = [
    _tool("hockey_get_player_coach_ids", "hockey", "Coach ids a player has had."),
    _tool("hockey_get_coaches_who_were_players", "hockey", "Coaches who previously played in the league."),
    _tool("hockey_list_games", "hockey", "Games in a season, newest first."),
    _tool("movies_list_customers", "movies", "All customers with their rentals."),
    _tool("movies_get_customer", "movies", "One customer by id."),
    _tool("spotify_play_song", "spotify", "Play a song by id."),
]


def _names(query, **kw):
    return [name for name, _ in rank(query, CATALOGUE, **kw)]


@pytest.mark.parametrize(
    ("word", "folded"),
    [
        ("coaches", "coach"),
        ("categories", "category"),
        ("games", "game"),
        ("status", "status"),
        ("class", "class"),
        ("bus", "bus"),
    ],
)
def test_fold(word, folded):
    assert fold(word) == folded


def test_split_name_handles_snake_camel_and_separators():
    assert split_name("getCoachesWhoPlayed") == ["get", "coach", "who", "played"]
    assert split_name("hockey_get-coaches.list") == ["hockey", "get", "coach", "list"]


def test_parse_query_required_optional_and_stop_words():
    assert parse_query("+hockey find the coaches for me") == (["hockey"], ["coach"])


def test_exact_name_fast_path_returns_only_that_tool():
    assert rank("movies_get_customer", CATALOGUE) == [("movies_get_customer", 10_000)]


def test_app_name_dominates_then_operation_parts_rank_within_it():
    assert set(_names("hockey coaches players")[:2]) == {
        "hockey_get_coaches_who_were_players",
        "hockey_get_player_coach_ids",
    }
    assert all(n.startswith("hockey_") for n in _names("hockey coaches players"))


def test_length_normalization_fixes_the_known_hockey_miss():
    """The documented find_tools miss (tool-discovery-subagent.md): natural phrasing ranked
    the plausible-but-wrong sibling ``get_player_coach_ids`` first. Claude Code's additive
    score alone still does (36 vs 34); name-coverage normalization puts the right tool first."""
    (top, _), (second, _) = rank("hockey coaches players", CATALOGUE)[:2]
    assert top == "hockey_get_coaches_who_were_players"
    assert second == "hockey_get_player_coach_ids"


def test_coverage_prefers_the_sibling_whose_name_the_query_covers():
    short = _tool("retail_get_count_territories", "retail", "Count territories.")
    long = _tool(
        "retail_get_territory_count_by_employee_name", "retail", "Count territories for an employee name."
    )
    assert [n for n, _ in rank("count of territories", [long, short])][0] == "retail_get_count_territories"


def test_required_terms_filter_before_scoring():
    assert set(_names("+movies customer")) == {"movies_get_customer", "movies_list_customers"}
    # Required terms also score (as in Claude Code), so the filter alone decides membership.
    assert _names("+spotify customer") == ["spotify_play_song"]
    assert _names("+spotify +customer") == []


def test_description_only_match_still_ranks():
    assert _names("league") == ["hockey_get_coaches_who_were_players"]


def test_search_hint_outranks_description():
    hinted = _tool("crm_find_accounts", "crm", "Look up accounts.")
    hinted.func._search_hint = "customer lookup"
    plain = _tool("crm_list_notes", "crm", "Notes about a customer.")
    assert [n for n, _ in rank("customer", [plain, hinted])] == ["crm_find_accounts", "crm_list_notes"]


def test_operation_id_contributes_name_parts():
    tool = _tool("bank_op_17", "bank", "", operation_id="transferFunds")
    assert [n for n, _ in rank("transfer", [tool])] == ["bank_op_17"]


def test_zero_scores_are_dropped_and_results_capped_at_five_by_default():
    many = [_tool(f"crm_customer_{i}", "crm") for i in range(12)]
    assert len(rank("customer", many)) == 5
    assert rank("zebra", many) == []
    assert len(rank("customer", many, max_results=None)) == 12


def test_ties_keep_catalogue_order():
    tools = [_tool("crm_customer_beta", "crm"), _tool("crm_customer_alpha", "crm")]
    assert [n for n, _ in rank("customer", tools)] == ["crm_customer_beta", "crm_customer_alpha"]


def test_stop_word_only_query_matches_nothing():
    assert rank("find me the tools", CATALOGUE) == []


@pytest.mark.asyncio
async def test_strategy_honours_max_results():
    result = await KeywordShortlister().shortlist(
        ShortlistRequest(query="hockey", tools=CATALOGUE, apps=[], max_results=2)
    )
    assert len(result.candidates) == 2
    assert all(c.score > 0 and c.reasoning.startswith("keyword score") for c in result.candidates)


def test_keyword_is_a_builtin_strategy():
    clear_instance_cache()
    strategy = resolve_shortlister(ShortlisterPlan(strategy="keyword"))
    assert isinstance(strategy, KeywordShortlister)


def test_registry_summary_becomes_the_search_hint():
    tool = create_tool_from_api_dict(
        "hockey_op_9",
        {"description": "Long OpenAPI description.", "summary": "Coaches who were players", "parameters": []},
        "hockey",
    )
    other = _tool("hockey_list_games", "hockey", "Games.")
    assert [n for n, _ in rank("former player coach", [other, tool])][0] == "hockey_op_9"
