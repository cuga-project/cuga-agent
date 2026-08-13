"""CUGA core must not depend on the eventing layer. Enforced, not asserted in prose.

The whole premise of this branch is that CUGA ships and runs whether or not the eventing service
exists. Two docs stated "a test fails if that regresses" — and for a while that was simply untrue,
which is worse than having no claim at all. This file makes it true.

The properties, in the order they would break:

  1. `cuga.backend.server.main` / `cuga.cli.main` import nothing from `cuga.backend.events`.
     A single convenience import is all it takes to make CUGA un-deployable without the layer.
  2. The events package's only import from CUGA core is `resolve_secret`. That is the one seam we
     accepted; anything else means the "layer on top" story has quietly become a fork.
  3. Core's coupling to the running service is ONE environment variable. Unset it and the door is
     closed at the first line of `_forwards_to_events`.

Parsed with `ast`, not imported: importing `server.main` pulls in the whole graph, which is slow and
has side effects (it also broke 17 unrelated tests once when a lockstep test tried it).
"""

import ast
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2] / "src" / "cuga"

CORE_FILES = [
    ROOT / "backend" / "server" / "main.py",
    ROOT / "cli" / "main.py",
    ROOT / "supervisor_utils" / "supervisor_config.py",
]


def _imported_modules(path: pathlib.Path):
    """Every module named by an import in this file, including inside functions."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            out.add(node.module)
    return out


@pytest.mark.parametrize("path", CORE_FILES, ids=lambda p: p.name)
def test_core_does_not_import_the_events_package(path):
    assert path.exists(), f"{path} moved — update this guard"
    offenders = sorted(m for m in _imported_modules(path) if m.startswith("cuga.backend.events"))
    assert not offenders, (
        f"{path.relative_to(ROOT.parent.parent)} imports {offenders}. CUGA core must run without "
        f"the eventing layer installed; talk to it over HTTP (see _forward_slash_to_events) instead."
    )


def test_events_package_imports_only_resolve_secret_from_core():
    """One seam, named. A second one is a design change, not a refactor."""
    allowed_names = {"resolve_secret"}
    found = {}
    for py in (ROOT / "backend" / "events").glob("*.py"):
        tree = ast.parse(py.read_text(encoding="utf-8"), filename=str(py))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("cuga."):
                if node.module.startswith("cuga.backend.events"):
                    continue  # intra-package, fine
                for a in node.names:
                    if a.name not in allowed_names:
                        found.setdefault(py.name, []).append(f"{node.module}.{a.name}")
    assert not found, (
        f"events/ imports more than {sorted(allowed_names)} from CUGA core: {found}. "
        f"Each extra import makes the events layer harder to run beside a different CUGA build."
    )


def test_the_door_is_closed_when_events_is_not_configured(monkeypatch):
    """The one variable. Unset → a slash verb is just text, exactly like upstream CUGA.

    Reads the guard out of the source rather than importing the server, for the reason in the
    module docstring.
    """
    src = (ROOT / "backend" / "server" / "main.py").read_text(encoding="utf-8")
    fn = src.split("def _forwards_to_events(")[1].split("\ndef ")[0]
    first_real_line = [
        ln.strip() for ln in fn.splitlines() if ln.strip() and not ln.strip().startswith(("#", '"'))
    ][1]
    assert "_events_api_url()" in first_real_line and "not" in first_real_line, (
        "the FIRST thing _forwards_to_events does must be the EVENTS_API_URL check — otherwise "
        f"vanilla CUGA starts behaving differently. Got: {first_real_line!r}"
    )


def test_core_never_reads_the_events_database():
    """EVENTS_DB belongs to the eventing service alone; core has its own conversation store."""
    for path in CORE_FILES:
        assert "EVENTS_DB" not in path.read_text(encoding="utf-8"), (
            f"{path.name} references EVENTS_DB. The events database is not CUGA's — sharing it "
            f"would couple the two deployments at the data layer."
        )
