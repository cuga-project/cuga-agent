"""``resolve_checkpoint_db_path`` (issue #774).

Each test builds a private ``Dynaconf`` from a temp ``settings.toml`` and passes
its ``storage`` section, the same Box type the app passes as ``settings.storage``.
``DBS_DIR`` is redirected to ``tmp_path`` so nothing touches the real dbs dir.
"""

from pathlib import Path

import pytest
from dynaconf import Dynaconf

from cuga.backend.storage.checkpoint.paths import resolve_checkpoint_db_path

pytestmark = pytest.mark.unit


@pytest.fixture
def dbs_dir(tmp_path, monkeypatch):
    import cuga.config as cuga_config

    d = tmp_path / "dbs"
    monkeypatch.setattr(cuga_config, "DBS_DIR", str(d))
    return d


def _storage(tmp_path, storage_toml: str = ""):
    settings_file = tmp_path / "settings.toml"
    settings_file.write_text(f'[storage]\nmode = "local"\n{storage_toml}\n')
    return Dynaconf(
        settings_files=[str(settings_file)],
        envvar_prefix="CUGA_TEST_CHECKPOINT_PATHS",
    ).storage


@pytest.mark.parametrize(
    ("configured", "expected"),
    [
        ("", "{dbs}/checkpoints.db"),
        ("   ", "{dbs}/checkpoints.db"),
        ("state/cp.db", "{dbs}/state/cp.db"),
        ("{tmp}/elsewhere/cp.db", "{tmp}/elsewhere/cp.db"),
        ("~/cp.db", "{home}/cp.db"),
    ],
    ids=["unset", "blank", "relative-to-dbs-dir", "absolute", "home"],
)
def test_resolves_the_path_and_creates_its_parent(tmp_path, dbs_dir, monkeypatch, configured, expected):
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    fmt = {"dbs": dbs_dir, "tmp": tmp_path, "home": home}

    path = resolve_checkpoint_db_path(
        _storage(tmp_path, f'checkpoint_db_path = "{configured.format(**fmt)}"')
    )

    assert path == Path(expected.format(**fmt))
    assert path.parent.is_dir()
