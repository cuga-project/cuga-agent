"""RAG profile loading is confined to the VALID_PROFILES allowlist.

``load_profile`` is reachable with attacker-influenced input: ``KnowledgeConfig.
from_settings`` passes ``search.rag_profile`` straight from published config, and
``awareness`` forwards the same value. Before the allowlist it joined that string
into a path (CodeQL alerts #68/#69, ``py/path-injection``).
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
    """A traversal payload must be refused before any file is touched."""
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
    """A plain unknown name is a ValueError, not a FileNotFoundError."""
    with pytest.raises(ValueError) as excinfo:
        load_profile("not_a_profile")
    assert "not_a_profile" in str(excinfo.value)


@pytest.mark.unit
def test_absolute_path_outside_profiles_dir_is_not_read(tmp_path: Path) -> None:
    """Even a readable file outside the profiles dir cannot be loaded."""
    outsider = tmp_path / "outsider.toml"
    outsider.write_text('[search]\ndefault_limit = 99\n', encoding="utf-8")

    with pytest.raises(ValueError):
        load_profile(str(outsider.with_suffix("")))
