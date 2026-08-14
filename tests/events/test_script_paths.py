"""Every script that derives the repo root must actually land on the repo root.

WHY THIS EXISTS
---------------
Moving `scripts/` to `events/scripts/` (and `events_docs/` to `events/docs/`) put six files one
directory deeper. Each of them walks up a FIXED number of levels to find the repo root:

    cd "$(dirname "$0")/.."                  # bash
    pathlib.Path(__file__).resolve().parents[2]   # python

A hard-coded climb is correct only for the depth it was written at. After the move every one of them
resolved to `events/` instead of the repo root, and the failure mode is the worst kind: `events_up.sh`
exited 2 with an EMPTY log, `make reload` printed nothing, and both services just never came up.
Nothing in CI noticed, because CI runs pytest and never executes a shell script.

The check is trivial — climb, then look for `pyproject.toml` — but it is the only thing standing
between a future `git mv` and another silent-exit debugging session.
"""

from __future__ import annotations

import pathlib
import re

import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]
MARKER = "pyproject.toml"  # exists only at the repo root

# (path, number of levels it climbs). Kept explicit rather than parsed so that moving a file
# without updating its climb fails HERE, naming the file, instead of somewhere downstream.
PY_ROOTS = [
    ("events/docs/setup/scripts/_common.py", 4),
    ("events/scripts/run_all_tests.py", 2),
    ("events/scripts/gen_supervisor_roster.py", 2),
    ("events/scripts/gen_examples.py", 2),
    ("events/scripts/gen_ledger.py", 2),
    ("events/scripts/gen_slides.py", 2),
]


@pytest.mark.parametrize("rel,climbs", PY_ROOTS, ids=[p for p, _ in PY_ROOTS])
def test_python_repo_root_resolves(rel: str, climbs: int):
    """The declared climb lands on the repo root, and the file still declares that climb."""
    src = REPO / rel
    assert src.exists(), f"{rel} moved or was deleted — update PY_ROOTS"

    root = src.resolve().parents[climbs]
    assert (root / MARKER).exists(), f"{rel} climbs {climbs} to {root}, which is not the repo root"

    # The file must actually use the depth we just proved correct.
    text = src.read_text()
    declared = re.findall(r"parents\[(\d+)\]|(\.parent(?:\.parent)+)", text)
    depths = {int(a) for a, _ in declared if a} | {b.count(".parent") for _, b in declared if b}
    assert climbs in depths, f"{rel} no longer climbs {climbs} levels (found {sorted(depths)})"


def _bash_scripts():
    return sorted((REPO / "events" / "scripts").glob("*.sh"))


def test_there_are_bash_scripts_to_check():
    """Guards against the glob silently going empty after another move."""
    assert _bash_scripts(), "no shell scripts found under events/scripts/"


@pytest.mark.parametrize("script", _bash_scripts(), ids=lambda p: p.name)
def test_bash_repo_root_resolves(script: pathlib.Path):
    """`cd "$(dirname "$0")/../.."` must reach the repo root. A script with no climb is fine —
    it simply never depends on where it was launched from."""
    m = re.search(r'cd "\$\(dirname "\$0"\)((?:/\.\.)+)"', script.read_text())
    if not m:
        pytest.skip(f"{script.name} does not derive a repo root")

    climbs = m.group(1).count("/..")
    root = script.resolve().parents[climbs]
    assert (root / MARKER).exists(), (
        f"{script.name} climbs {climbs} to {root}, which is not the repo root — "
        f"it will fail with an empty log and a nonzero exit"
    )
