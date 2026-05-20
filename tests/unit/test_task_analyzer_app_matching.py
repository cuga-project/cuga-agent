import pytest

from cuga.config import settings
from cuga.backend.cuga_graph.nodes.task_decomposition_planning.analyze_task import TaskAnalyzer
from cuga.backend.cuga_graph.state.agent_state import AgentState
from cuga.backend.tools_env.registry.utils.types import AppDefinition


def _apps():
    return [
        AppDefinition(name="venmo", description="Payments app", url="https://venmo.com"),
        AppDefinition(name="spotify", description="Music app", url="https://spotify.com"),
        AppDefinition(name="calendar", description="Calendar app", url="https://calendar.example.com"),
    ]


def test_resolve_relevant_apps_case_insensitive_exact_match():
    resolved = TaskAnalyzer.resolve_relevant_apps(["VenMo"], _apps())
    assert resolved == ["venmo"]


def test_resolve_relevant_apps_trims_whitespace():
    resolved = TaskAnalyzer.resolve_relevant_apps(["  venmo  "], _apps())
    assert resolved == ["venmo"]


def test_resolve_relevant_apps_ignores_empty_and_non_string_entries():
    resolved = TaskAnalyzer.resolve_relevant_apps(["", "   ", None], _apps())  # type: ignore[list-item]
    assert resolved == []


def test_resolve_relevant_apps_deduplicates_equivalent_matches():
    resolved = TaskAnalyzer.resolve_relevant_apps(["venmo", "Venmo", "venom"], _apps())
    assert resolved == ["venmo"]


def test_resolve_relevant_apps_drops_similarity_below_cutoff():
    resolved = TaskAnalyzer.resolve_relevant_apps(["paypal"], _apps())
    assert resolved == []


def test_resolve_relevant_apps_respects_length_delta_guard():
    resolved = TaskAnalyzer.resolve_relevant_apps(["venmooooooooo"], _apps())
    assert resolved == []


def test_resolve_relevant_apps_mixed_forced_list_keeps_only_valid_and_correctable():
    resolved = TaskAnalyzer.resolve_relevant_apps(["venom", "not_a_real_app"], _apps())
    assert resolved == ["venmo"]


def test_resolve_relevant_apps_skips_ambiguous_top_fuzzy_matches():
    apps = [
        AppDefinition(name="venomy", description="App A", url="https://a.example.com"),
        AppDefinition(name="venomz", description="App B", url="https://b.example.com"),
    ]
    resolved = TaskAnalyzer.resolve_relevant_apps(["venomq"], apps)
    assert resolved == []


def test_resolve_relevant_apps_corrects_when_clear_top_fuzzy_winner_exists():
    apps = [
        AppDefinition(name="spotify", description="Music app", url="https://spotify.com"),
        AppDefinition(name="calendar", description="Calendar app", url="https://calendar.example.com"),
    ]
    resolved = TaskAnalyzer.resolve_relevant_apps(["sptify"], apps)
    assert resolved == ["spotify"]


@pytest.mark.asyncio
async def test_match_apps_corrects_simple_typo_from_forced_apps(monkeypatch):
    apps = _apps()

    async def _fake_get_apps():
        return apps

    monkeypatch.setattr(
        "cuga.backend.cuga_graph.nodes.task_decomposition_planning.analyze_task.get_apps", _fake_get_apps
    )
    monkeypatch.setattr(settings.features, "forced_apps", ["venom"])

    state = AgentState(input="Request money on Venmo", url="", elements="")
    matched_apps, app_match = await TaskAnalyzer.match_apps(agent=None, state=state, mode="api")

    assert app_match.relevant_apps == ["venmo"]
    assert matched_apps is not None
    assert [a.name for a in matched_apps] == ["venmo"]


@pytest.mark.asyncio
async def test_match_apps_drops_unknown_forced_apps(monkeypatch):
    apps = _apps()

    async def _fake_get_apps():
        return apps

    monkeypatch.setattr(
        "cuga.backend.cuga_graph.nodes.task_decomposition_planning.analyze_task.get_apps", _fake_get_apps
    )
    monkeypatch.setattr(settings.features, "forced_apps", ["not_a_real_app"])

    state = AgentState(input="Do something", url="", elements="")
    matched_apps, app_match = await TaskAnalyzer.match_apps(agent=None, state=state, mode="api")

    assert app_match.relevant_apps == []
    assert matched_apps == []


@pytest.mark.asyncio
async def test_match_apps_hybrid_mode_appends_web_app(monkeypatch):
    apps = _apps()

    async def _fake_get_apps():
        return apps

    monkeypatch.setattr(
        "cuga.backend.cuga_graph.nodes.task_decomposition_planning.analyze_task.get_apps", _fake_get_apps
    )
    monkeypatch.setattr(settings.features, "forced_apps", ["venom"])

    state = AgentState(input="Request money on Venmo", url="", elements="")
    matched_apps, app_match = await TaskAnalyzer.match_apps(
        agent=None,
        state=state,
        mode="hybrid",
        web_app_name="web_portal",
        web_description="User-facing web portal",
    )

    assert app_match.relevant_apps == ["venmo"]
    assert matched_apps is not None
    assert [a.name for a in matched_apps] == ["venmo", "web_portal"]
    assert [a.type for a in matched_apps] == ["api", "web"]


@pytest.mark.asyncio
async def test_match_apps_single_app_fast_path_is_unchanged(monkeypatch):
    apps = [AppDefinition(name="venmo", description="Payments app", url="https://venmo.com")]

    async def _fake_get_apps():
        return apps

    monkeypatch.setattr(
        "cuga.backend.cuga_graph.nodes.task_decomposition_planning.analyze_task.get_apps", _fake_get_apps
    )
    monkeypatch.setattr(settings.features, "forced_apps", ["not_a_real_app"])

    state = AgentState(input="Do something", url="", elements="")
    matched_apps, app_match = await TaskAnalyzer.match_apps(agent=None, state=state, mode="api")

    assert app_match.relevant_apps == ["venmo"]
    assert matched_apps is not None
    assert [a.name for a in matched_apps] == ["venmo"]
