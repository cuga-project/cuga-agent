"""Check that installed packages meet the security floors in pyproject.toml.

Run it with the Python of the environment to check:

    .venv/bin/python scripts/check_runtime_floors.py path/to/pyproject.toml authlib certifi ...

For each package named on the command line, it collects the lower bounds (``>=``, ``>``,
``==``) from ``[tool.uv].constraint-dependencies`` and ``[project].dependencies``, and checks
the installed version against them. Upper bounds are ignored: this checks security floors only.

The floors in ``[tool.uv].constraint-dependencies`` apply only when uv resolves the lockfile.
They are not in the wheel metadata, so ``pip install`` over an older environment can keep a
vulnerable version. A package that fails here needs its floor in ``[project].dependencies`` too.

Exit status: 0 when every named package is installed and meets its floors, 1 otherwise.
"""

from __future__ import annotations

import sys
from importlib.metadata import PackageNotFoundError, version

try:
    import tomllib
except ModuleNotFoundError:  # Python < 3.11
    sys.exit("error: check_runtime_floors.py needs Python 3.11 or later (tomllib)")

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name
from packaging.version import Version

_LOWER_BOUND_OPERATORS = {">=", ">", "=="}


def collect_floors(pyproject_path: str) -> dict[str, list[tuple[str, str, str]]]:
    """Map each package to its lower bounds as ``(operator, version, source)``."""
    with open(pyproject_path, "rb") as f:
        data = tomllib.load(f)
    sources = {
        "[tool.uv].constraint-dependencies": data.get("tool", {})
        .get("uv", {})
        .get("constraint-dependencies", []),
        "[project].dependencies": data.get("project", {}).get("dependencies", []),
    }
    floors: dict[str, list[tuple[str, str, str]]] = {}
    for source, lines in sources.items():
        for line in lines:
            req = Requirement(line)
            if req.marker is not None and not req.marker.evaluate():
                continue
            for spec in req.specifier:
                if spec.operator in _LOWER_BOUND_OPERATORS:
                    floors.setdefault(canonicalize_name(req.name), []).append(
                        (spec.operator, spec.version, source)
                    )
    return floors


def meets(installed: Version, operator: str, bound: str) -> bool:
    if operator == ">=":
        return installed >= Version(bound)
    if operator == ">":
        return installed > Version(bound)
    return installed >= Version(bound)  # "==": treat the pinned version as the floor


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print(__doc__, file=sys.stderr)
        return 2
    floors = collect_floors(argv[1])
    failed = False
    for name in argv[2:]:
        key = canonicalize_name(name)
        bounds = floors.get(key)
        if not bounds:
            print(f"FAIL {name}: no floor in pyproject.toml")
            failed = True
            continue
        try:
            installed = Version(version(name))
        except PackageNotFoundError:
            print(f"FAIL {name}: not installed")
            failed = True
            continue
        for operator, bound, source in bounds:
            ok = meets(installed, operator, bound)
            failed |= not ok
            print(f"{'ok  ' if ok else 'FAIL'} {name} {installed} (needs {operator}{bound}, {source})")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
