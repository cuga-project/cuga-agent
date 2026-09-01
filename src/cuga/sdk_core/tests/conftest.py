"""Fixtures for SDK core tests.

Also isolates the local sqlite config/relational store from the developer's real
DBS_DIR. Some tests here (e.g. TestBuildAgentsFromStoredSubAgents) call
reset_config_db() / save_config(), which write to DBS_DIR/cuga.db — the SAME
on-disk file a locally running `cuga start manager` server uses. Redirect every
test in this directory to a throwaway temp directory instead.
"""

import pytest
import pytest_asyncio

from cuga.backend.llm.models import LLMManager


@pytest.fixture(autouse=True)
def _isolated_local_db_dir(monkeypatch, tmp_path):
    import cuga.backend.storage.facade as storage_facade
    import cuga.config as cuga_config

    # Both bindings must be patched: config_store.py's reset_config_db() re-imports DBS_DIR
    # from cuga.config at call time, but the actual DB path used for reads/writes is resolved
    # via facade.py's own module-level import of DBS_DIR (bound at facade.py's first import).
    monkeypatch.setattr(cuga_config, "DBS_DIR", str(tmp_path))
    monkeypatch.setattr(storage_facade, "DBS_DIR", str(tmp_path))


@pytest_asyncio.fixture(autouse=True)
async def _rebind_llm_async_clients():
    # ChatWatsonx caches an httpx.AsyncClient bound to the pytest event loop.
    # Rebind that client to the current loop instead of dropping the whole
    # LLMManager cache (which re-auths to IAM on every test). See #523.
    mgr = LLMManager()
    mgr.rebind_async_clients_to_running_loop()
    yield
    # Close on this loop before pytest tears it down so the next test does not leak.
    await mgr.aclose_watsonx_async_clients()
