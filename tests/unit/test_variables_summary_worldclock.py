"""Variables-summary `Created:` timestamps must never reach the model (issue #705).

created_at is stamped from the HOST wall clock; in frozen-world-clock benchmark runs
(AppWorld, #544) rendering it into prompts gave the model two conflicting "today"s and
it sometimes trusted the wrong one. Per review of PR #708 the line is removed from the
prompt UNCONDITIONALLY (the model never needs it — ordering is conveyed by position),
while the created_at FIELD is kept for persistence and run forensics.
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
@pytest.mark.parametrize(
    ("benchmark", "current_date"),
    [
        ("default", None),  # production
        ("bpo", None),  # benchmark without a world clock
        ("appworld", WORLD_DATE),  # frozen world clock
    ],
)
def test_created_never_rendered(manager, tracker_date, monkeypatch, benchmark, current_date):
    monkeypatch.setattr(settings.advanced_features, "benchmark", benchmark, raising=False)
    tracker_date.current_date = current_date
    summary = manager.get_variables_summary()
    assert "probe_var" in summary
    assert "- Created: " not in summary
    # the rest of the block is intact
    assert "- Value Preview: " in summary
    assert "- Description: probe" in summary


@pytest.mark.unit
def test_created_at_field_kept_for_persistence(manager):
    meta = manager.variables["probe_var"]
    assert meta.created_at is not None
    assert "created_at" in meta.to_dict(include_value=False)
