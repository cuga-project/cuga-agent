"""Fixtures for SDK core tests.

Also isolates the local sqlite config/relational store from the developer's real
DBS_DIR. Some tests here (e.g. TestBuildAgentsFromStoredSubAgents) call
reset_config_db() / save_config(), which write to DBS_DIR/cuga.db — the SAME
on-disk file a locally running `cuga start manager` server uses. Redirect every
test in this directory to a throwaway temp directory instead.
"""

import functools
import importlib.util
from pathlib import Path

import pytest
import pytest_asyncio

from cuga.backend.llm.models import LLMManager

# The repo-root ``tests/fixtures/async_only_saver.py`` is the single copy of
# AsyncOnlySaver. The `sdk` CI job collects only this directory under
# --import-mode=importlib, so the repo root is not on sys.path and
# ``import tests.fixtures...`` fails here; load the module from its file instead.
_ASYNC_ONLY_SAVER_PATH = Path(__file__).resolve().parents[4] / "tests" / "fixtures" / "async_only_saver.py"


@functools.lru_cache(maxsize=1)
def _load_async_only_saver_module():
    spec = importlib.util.spec_from_file_location("_cuga_test_async_only_saver", _ASYNC_ONLY_SAVER_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def async_only_saver_cls():
    """The ``AsyncOnlySaver`` class (sync saver API raises and records ``sync_calls``)."""
    return _load_async_only_saver_module().AsyncOnlySaver


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
