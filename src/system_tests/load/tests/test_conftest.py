"""Unit tests for load test pytest configuration."""

from system_tests.load.conftest import resolve_load_test_users


def test_resolve_load_test_users_uses_default(monkeypatch):
    monkeypatch.delenv("CUGA_LOAD_TEST_USERS", raising=False)
    assert resolve_load_test_users(5) == 5


def test_resolve_load_test_users_reads_env(monkeypatch):
    monkeypatch.setenv("CUGA_LOAD_TEST_USERS", "12")
    assert resolve_load_test_users(5) == 12
