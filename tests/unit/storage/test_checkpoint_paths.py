"""``resolve_checkpoint_db_path`` (issue #774, W01.3).

Each test builds a private ``Dynaconf`` from a temp ``settings.toml`` and passes
its ``storage`` section, the same Box type the app passes as ``settings.storage``.
``DBS_DIR`` is redirected to ``tmp_path`` so nothing touches the real dbs dir.
"""

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


def test_default_is_checkpoints_db_in_dbs_dir(tmp_path, dbs_dir):
    path = resolve_checkpoint_db_path(_storage(tmp_path, 'checkpoint_db_path = ""'))

    assert path == dbs_dir / "checkpoints.db"
    assert dbs_dir.is_dir()


def test_missing_key_falls_back_to_default(tmp_path, dbs_dir):
    assert resolve_checkpoint_db_path(_storage(tmp_path)) == dbs_dir / "checkpoints.db"


def test_blank_path_falls_back_to_default(tmp_path, dbs_dir):
    path = resolve_checkpoint_db_path(_storage(tmp_path, 'checkpoint_db_path = "   "'))

    assert path == dbs_dir / "checkpoints.db"


def test_explicit_absolute_path_is_used_and_parent_created(tmp_path, dbs_dir):
    target = tmp_path / "elsewhere" / "nested" / "cp.db"

    path = resolve_checkpoint_db_path(_storage(tmp_path, f'checkpoint_db_path = "{target.as_posix()}"'))

    assert path == target
    assert target.parent.is_dir()
    assert not target.exists()
    assert not dbs_dir.exists()


def test_relative_path_resolves_against_dbs_dir(tmp_path, dbs_dir, monkeypatch):
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    monkeypatch.chdir(cwd)

    path = resolve_checkpoint_db_path(_storage(tmp_path, 'checkpoint_db_path = "sub/cp.db"'))

    assert path == dbs_dir / "sub" / "cp.db"
    assert (dbs_dir / "sub").is_dir()
    assert not (cwd / "sub").exists()


def test_existing_parent_dir_is_fine(tmp_path, dbs_dir):
    dbs_dir.mkdir()

    assert resolve_checkpoint_db_path(_storage(tmp_path)) == dbs_dir / "checkpoints.db"
