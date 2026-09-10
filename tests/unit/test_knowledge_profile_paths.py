"""Loading a RAG profile only accepts the names listed in VALID_PROFILES.

``load_profile`` can be reached with a name that a user supplied:
``KnowledgeConfig.from_settings`` passes ``search.rag_profile`` straight from
published configuration, and ``awareness`` passes on the same value. Before this
check existed, that name was joined into a file path directly, which is what
CodeQL reported as alerts #68 and #69 under the rule ``py/path-injection``.
"""

from __future__ import annotations

import builtins
from pathlib import Path

import pytest

from cuga.backend.knowledge.config import VALID_PROFILES, load_profile


@pytest.mark.unit
@pytest.mark.parametrize("name", VALID_PROFILES)
def test_every_valid_profile_loads(name: str) -> None:
    profile = load_profile(name)
    assert isinstance(profile, dict)
    # Each shipped profile defines at least the search knobs the config reads back.
    assert "search" in profile


@pytest.mark.unit
@pytest.mark.parametrize(
    "name",
    [
        "../../../etc/passwd",
        "..",
        ".",
        "../standard",
        "nested/standard",
        "standard\\..\\..\\secret",
        "/etc/passwd",
        "",
    ],
)
def test_traversal_attempts_are_rejected(name: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """A name that tries to escape the directory is refused before any file is opened."""
    opened: list[object] = []
    real_open = builtins.open

    def spy_open(file, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003
        opened.append(file)
        return real_open(file, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", spy_open)

    with pytest.raises(ValueError):
        load_profile(name)

    assert opened == []


@pytest.mark.unit
def test_unknown_but_harmless_name_is_rejected() -> None:
    """An ordinary unknown name raises ValueError, not FileNotFoundError."""
    with pytest.raises(ValueError) as excinfo:
        load_profile("not_a_profile")
    assert "not_a_profile" in str(excinfo.value)


@pytest.mark.unit
def test_absolute_path_outside_profiles_dir_is_not_read(tmp_path: Path) -> None:
    """A readable file outside the profiles directory still cannot be loaded."""
    outsider = tmp_path / "outsider.toml"
    outsider.write_text('[search]\ndefault_limit = 99\n', encoding="utf-8")

    with pytest.raises(ValueError):
        load_profile(str(outsider.with_suffix("")))
