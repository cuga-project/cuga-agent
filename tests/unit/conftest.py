"""Isolate the local sqlite config/relational store from the developer's real DBS_DIR.

Several tests here call reset_config_db() / save_config() / save_draft(), which write to
DBS_DIR/cuga.db — the SAME on-disk file a locally running `cuga start manager` server uses
(src/cuga/backend/storage/facade.py's _local_db_path()). Running these tests while a dev server
is up deletes/corrupts that shared file — this happened twice during the #101 supervisor work.
Redirect every test in this directory to a throwaway temp directory instead.
"""

import pytest


@pytest.fixture(autouse=True)
def _isolated_local_db_dir(monkeypatch, tmp_path):
    import cuga.backend.storage.facade as storage_facade
    import cuga.config as cuga_config

    # Both bindings must be patched: config_store.py's reset_config_db() re-imports DBS_DIR
    # from cuga.config at call time, but the actual DB path used for reads/writes is resolved
    # via facade.py's own module-level import of DBS_DIR (bound at facade.py's first import).
    monkeypatch.setattr(cuga_config, "DBS_DIR", str(tmp_path))
    monkeypatch.setattr(storage_facade, "DBS_DIR", str(tmp_path))
