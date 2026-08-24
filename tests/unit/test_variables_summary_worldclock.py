"""Variables-summary `Created:` timestamps must not leak the host wall clock into
frozen-world-clock benchmark runs (issue #705).

The gate mirrors the sandbox clock freeze: benchmark mode AND tracker.current_date set.
Three states matter:
  - production (benchmark == "default")        -> Created line rendered (host time)
  - benchmark without a world clock (bpo/m3)   -> Created line rendered (host time)
  - benchmark with a world clock (appworld)    -> Created line suppressed
"""

import pytest

from cuga.backend.activity_tracker.tracker import ActivityTracker
from cuga.backend.cuga_graph.state.agent_state import VariablesManager
from cuga.config import settings

WORLD_DATE = "2023-05-19T12:00:00"


@pytest.fixture()
def manager():
    m = VariablesManager()
    m.reset()
    m.add_variable({"a": 1}, name="probe_var", description="probe")
    yield m
    m.reset()


@pytest.fixture()
def tracker_date():
    tracker = ActivityTracker()
    prev = tracker.current_date
    yield tracker
    tracker.current_date = prev


@pytest.mark.unit
def test_created_rendered_by_default(manager, tracker_date, monkeypatch):
    monkeypatch.setattr(settings.advanced_features, "benchmark", "default", raising=False)
    tracker_date.current_date = WORLD_DATE  # even with a stray date, default mode keeps host time
    summary = manager.get_variables_summary()
    assert "probe_var" in summary
    assert "- Created: " in summary


@pytest.mark.unit
def test_created_rendered_in_benchmark_without_world_clock(manager, tracker_date, monkeypatch):
    monkeypatch.setattr(settings.advanced_features, "benchmark", "bpo", raising=False)
    tracker_date.current_date = None
    summary = manager.get_variables_summary()
    assert "probe_var" in summary
    assert "- Created: " in summary


@pytest.mark.unit
def test_created_suppressed_under_frozen_world_clock(manager, tracker_date, monkeypatch):
    monkeypatch.setattr(settings.advanced_features, "benchmark", "appworld", raising=False)
    tracker_date.current_date = WORLD_DATE
    summary = manager.get_variables_summary()
    assert "probe_var" in summary
    assert "- Created: " not in summary
    # the rest of the block is intact
    assert "- Value Preview: " in summary
    assert "- Description: probe" in summary
