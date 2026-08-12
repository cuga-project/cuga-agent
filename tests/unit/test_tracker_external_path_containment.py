# tests/unit/test_tracker_external_path_containment.py
"""Containment regression for the external-trajectory tracker write.

``ActivityTracker.collect_step_external`` writes ``full_path`` after only
checking that its parent directory exists. On the registry server, ``full_path``
is the ``trajectory_path`` query parameter of ``POST /functions/call``
(api_registry_server.py:305, :332), which carries no authentication, so a
request could name any writable path and have the tracker overwrite it with
trajectory JSON that embeds the request body.

This corresponds to code-scanning alerts #11, #12, #13, #30, dismissed as
"false positive" on 2026-02-26 with no recorded reason. Reproduced end to end
before the fix: a single unauthenticated request overwrote a file outside any
trajectory directory.

The fix confines the write to the tracker's ``_base_dir``, where every
legitimate path is built by ``get_current_trajectory_path``. These tests assert
both halves: a path outside the base directory is refused and leaves the target
untouched, and a path inside it is still written.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from cuga.backend.activity_tracker.tracker import ActivityTracker, Step
from cuga.config import settings

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _enable_tracker():
    """Mirror api_registry_server.py:331, which force-enables the tracker whenever a
    trajectory_path is supplied, so the tracker_enabled guard is not the thing under test."""
    settings.update({"ADVANCED_FEATURES": {"TRACKER_ENABLED": True}}, merge=True)


def _tracker_with_base(base: Path) -> ActivityTracker:
    tracker = ActivityTracker()
    tracker.set_base_dir(str(base))
    return tracker


def test_external_step_refuses_path_outside_base_dir(tmp_path: Path):
    base = tmp_path / "trajectories"
    base.mkdir()

    victim = tmp_path / "victim.json"
    victim.write_text('{"do_not":"overwrite"}')
    assert victim.parent.exists()  # the only guard the old code applied

    tracker = _tracker_with_base(base)
    tracker.collect_step_external(
        Step(name="api_call", data="attacker-controlled"),
        full_path=str(victim),
    )

    assert victim.read_text() == '{"do_not":"overwrite"}', (
        "tracker overwrote a file outside its base directory — arbitrary write "
        "via the trajectory_path parameter is unmitigated"
    )


def test_external_step_refuses_traversal_out_of_base_dir(tmp_path: Path):
    base = tmp_path / "trajectories"
    base.mkdir()
    victim = tmp_path / "victim.json"
    victim.write_text('{"do_not":"overwrite"}')

    traversal = os.path.join(str(base), "..", "victim.json")
    assert os.path.exists(traversal)  # non-vacuous: the join does resolve to the victim

    tracker = _tracker_with_base(base)
    tracker.collect_step_external(Step(name="api_call", data="x"), full_path=traversal)

    assert victim.read_text() == '{"do_not":"overwrite"}'


def test_external_step_still_writes_inside_base_dir(tmp_path: Path):
    base = tmp_path / "trajectories"
    base.mkdir()
    legit = base / "experiment" / "task-1.json"
    legit.parent.mkdir(parents=True)

    tracker = _tracker_with_base(base)
    tracker.collect_step_external(Step(name="api_call", data="ok"), full_path=str(legit))

    assert legit.exists(), "a path inside the base directory must still be written"
    assert "steps" in legit.read_text()
