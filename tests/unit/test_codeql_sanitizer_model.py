"""Every function the code scanning model file names must exist.

The model file tells code scanning which functions return values that are safe for
file paths or SQL. If one of those functions is renamed or moved, its entry matches
nothing and the alerts it stopped come back, with no error anywhere. This test fails
first instead.
"""

import importlib
import re
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.unit

_MODEL_FILE = (
    Path(__file__).resolve().parents[2]
    / ".github/codeql/extensions/workspace-path-sanitizers/sanitizers.model.yml"
)
_MEMBER_RE = re.compile(r"^Member\[(\w+)\]\.ReturnValue$")


def _entries() -> list[list[str]]:
    data = yaml.safe_load(_MODEL_FILE.read_text())
    return [row for ext in data["extensions"] for row in ext["data"]]


@pytest.mark.parametrize("entry", _entries(), ids=lambda e: f"{e[0]}{e[1]}")
def test_model_entry_names_an_existing_function(entry: list[str]) -> None:
    module_name, access_path, _kind = entry
    # Without the trailing "!" the entry names instances of the module, which never match.
    assert module_name.endswith("!"), f"{module_name!r} needs a trailing '!'"
    match = _MEMBER_RE.fullmatch(access_path)
    assert match, f"unexpected access path {access_path!r}"

    module = importlib.import_module(module_name[:-1])

    assert callable(getattr(module, match.group(1), None)), (
        f"{module_name[:-1]}.{match.group(1)} does not exist; update {_MODEL_FILE.name}"
    )
