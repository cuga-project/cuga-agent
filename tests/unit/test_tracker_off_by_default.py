"""The activity tracker is off by default, and while it is off it keeps nothing in memory.

``cuga start`` used to force ``DYNACONF_ADVANCED_FEATURES__TRACKER_ENABLED=true`` for every
service it launches (demo, demo_skills, manager, travel_agent), so each step rewrote the whole
trajectory file and ``ActivityTracker.steps`` grew until the pod ran out of memory.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from cuga.backend.activity_tracker.tracker import ActivityTracker, Step
from cuga.config import settings

pytestmark = pytest.mark.unit

_TRACKER_ENV = "DYNACONF_ADVANCED_FEATURES__TRACKER_ENABLED"


@pytest.fixture
def tracker():
    t = ActivityTracker()
    saved = (list(t.steps), list(t.prompts), list(t.images), t.step_count)
    enabled = settings.advanced_features.tracker_enabled
    t.steps, t.prompts, t.images, t.step_count = [], [], [], 0
    try:
        yield t
    finally:
        t.steps, t.prompts, t.images, t.step_count = saved
        settings.update({"ADVANCED_FEATURES": {"TRACKER_ENABLED": enabled}}, merge=True)


def _set_tracker(value: bool) -> None:
    settings.update({"ADVANCED_FEATURES": {"TRACKER_ENABLED": value}}, merge=True)


def test_default_setting_is_off():
    import tomllib

    import cuga

    settings_toml = Path(cuga.__file__).parent / "settings.toml"
    with open(settings_toml, "rb") as f:
        assert tomllib.load(f)["advanced_features"]["tracker_enabled"] is False


def test_importing_the_cli_does_not_force_the_tracker_on():
    # A developer's own .env may legitimately set the variable to false (litellm loads it on
    # import), so only the old forced value "true" is a failure.
    env = {k: v for k, v in os.environ.items() if k != _TRACKER_ENV}
    code = (
        "import os, cuga.cli.main\n"
        f"assert os.environ.get({_TRACKER_ENV!r}) != 'true', 'cuga.cli.main forced the tracker on'\n"
    )
    result = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr[-2000:]


def test_off_collect_step_keeps_no_steps_or_prompts(tracker):
    _set_tracker(False)
    with patch.object(ActivityTracker, "to_file") as to_file:
        for i in range(1000):
            tracker.collect_prompt(role="human", value="p" * 100)
            tracker.collect_step(Step(name="step", data=f'{{"i": {i}}}'))

    assert tracker.steps == []
    assert tracker.prompts == []
    assert tracker.step_count == 1000
    to_file.assert_not_called()


def test_off_collect_image_keeps_only_the_latest(tracker):
    _set_tracker(False)
    for i in range(50):
        tracker.collect_image(f"data:image/png;base64,{i}")

    assert tracker.images == ["data:image/png;base64,49"]


def test_on_collect_step_still_records_everything(tracker):
    _set_tracker(True)
    with patch.object(ActivityTracker, "to_file") as to_file:
        tracker.collect_prompt(role="human", value="hello")
        tracker.collect_image("abc")
        tracker.collect_image("def")
        tracker.collect_step(Step(name="step", data='{"a": 1}'))
        tracker.collect_step(Step(name="step", data='{"a": 2}'))

    assert [s.name for s in tracker.steps] == ["step", "step"]
    assert [p.value for p in tracker.steps[0].prompts] == ["hello"]
    assert len(tracker.images) == 2
    assert tracker.step_count == 2
    assert to_file.call_count == 2


def test_reset_clears_the_step_count(tracker):
    _set_tracker(False)
    tracker.collect_step(Step(name="step", data="{}"))
    tracker.reset(intent="x")

    assert tracker.step_count == 0
