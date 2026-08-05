"""The Activepieces engine must be fully constructed before anything calls it.

WHY THIS FILE EXISTS: `reachable()` had been pasted into the MIDDLE of `__init__`, so the
constructor ended at that method's `return` and every attribute below it — `_auth_lock`,
`_token_exp`, the caches, `project_grain` — was never assigned. Nothing failed at construction
time; it failed much later, on the first AP call, as
`'APEngine' object has no attribute '_auth_lock'`, and only on the paths that reach AP. With AP
off (the current default) the whole class looked fine.

These tests need no AP server: they assert the SHAPE of the object, which is exactly the property
a truncated `__init__` breaks.
"""
import inspect

import pytest

from cuga.backend.events.ap_engine import APEngine

# Everything __init__ promises. A method landing mid-constructor silently drops the tail of this
# list, so assert the whole set rather than the one attribute that happened to blow up.
REQUIRED_ATTRS = [
    "base", "api_key", "email", "password", "project_id", "invoke_url", "gateway_token",
    "_token", "_token_exp", "_auth_lock", "_piece_cache", "_project_cache", "_degraded",
    "project_grain",
]


@pytest.fixture
def engine(monkeypatch):
    monkeypatch.setenv("AP_BASE_URL", "http://ap.test:8081")
    return APEngine()


def test_init_assigns_every_attribute_it_promises(engine):
    missing = [a for a in REQUIRED_ATTRS if not hasattr(engine, a)]
    assert not missing, (
        f"APEngine.__init__ never assigned {missing} — most likely a method was pasted into the "
        f"middle of the constructor, ending it early")


def test_init_body_contains_no_return(engine):
    """The failure mode directly: a `return` inside __init__ means the tail never runs."""
    src = inspect.getsource(APEngine.__init__)
    assert "return" not in src, "APEngine.__init__ contains a return — the tail will not execute"


def test_reachable_is_its_own_method(engine):
    assert inspect.iscoroutinefunction(APEngine.reachable)
    assert "flags" in inspect.getsource(APEngine.reachable)


def test_base_url_is_normalised(monkeypatch):
    monkeypatch.setenv("AP_BASE_URL", "http://ap.test:8081/")
    assert APEngine().base == "http://ap.test:8081"


def test_project_grain_defaults_to_tenant_and_is_overridable(monkeypatch):
    monkeypatch.setenv("AP_BASE_URL", "http://ap.test:8081")
    assert APEngine().project_grain == "tenant"
    monkeypatch.setenv("EVENTS_AP_PROJECT_GRAIN", "shared")
    assert APEngine().project_grain == "shared"
