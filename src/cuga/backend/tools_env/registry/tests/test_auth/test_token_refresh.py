"""Age-based token refresh in BaseAuthManager (no JWT decoding).

AppWorld tokens have a random 10–30 min TTL; we re-fetch once a stored token
reaches REFRESH_AFTER_SECONDS (9 min) so the auto-injected file_system token
never goes stale mid-task. These checks pin that behaviour without sleeping by
back-dating the recorded fetch time.
"""

import pytest

from cuga.backend.tools_env.registry.registry.authentication import base_auth_manager
from cuga.backend.tools_env.registry.registry.authentication.base_auth_manager import (
    REFRESH_AFTER_SECONDS,
    BaseAuthManager,
)

pytestmark = pytest.mark.unit


class FakeAuthManager(BaseAuthManager):
    def __init__(self, fail=False):
        super().__init__()
        self.logins = 0
        self.fail = fail

    def _get_credentials(self, app_name):
        return None if app_name == "unknown" else "pw"

    def _fetch_token(self, app_name, creds):
        if self.fail:
            raise RuntimeError("login failed")
        self.logins += 1
        return {"access_token": f"{app_name}-token-{self.logins}"}


def _age(mgr, app, seconds):
    """Backdate the token's recorded fetch time by `seconds`."""
    mgr._token_times[app] -= seconds


def test_fresh_token_is_reused():
    m = FakeAuthManager()
    assert m.get_access_token("gmail") == "gmail-token-1"
    assert m.get_access_token("gmail") == "gmail-token-1"  # reused
    assert m.logins == 1


def test_stale_token_is_refreshed():
    m = FakeAuthManager()
    m.get_access_token("gmail")
    _age(m, "gmail", REFRESH_AFTER_SECONDS + 1)
    assert m.get_access_token("gmail") == "gmail-token-2"  # re-fetched
    assert m.logins == 2


def test_refresh_failure_falls_back_to_stale_token():
    m = FakeAuthManager()
    m.get_access_token("gmail")
    _age(m, "gmail", REFRESH_AFTER_SECONDS + 1)
    m.fail = True
    # Refresh fails, but a stale token may still be valid — keep it rather than error.
    assert m.get_access_token("gmail") == "gmail-token-1"


def test_no_credentials_returns_none_without_error():
    m = FakeAuthManager()
    assert m.get_access_token("unknown") is None


def test_get_stored_tokens_refreshes_stale_entries():
    m = FakeAuthManager()
    m.get_access_token("gmail")
    m.get_access_token("file_system")
    _age(m, "file_system", REFRESH_AFTER_SECONDS + 1)  # only file_system aged out
    tokens = m.get_stored_tokens()
    assert tokens["gmail"] == "gmail-token-1"  # untouched
    assert tokens["file_system"] == "file_system-token-3"  # refreshed


def test_direct_store_records_time(monkeypatch=None):
    # A token written via _store (as the /auth/token sniff path now does) is not
    # immediately stale.
    m = FakeAuthManager()
    m._store("gmail", "sniffed")
    assert not m._is_stale("gmail")
    assert m.get_access_token("gmail") == "sniffed"  # no re-login
    assert m.logins == 0


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok  {name}")
    print(f"(refresh age = {REFRESH_AFTER_SECONDS}s)")
    assert base_auth_manager.REFRESH_AFTER_SECONDS == 540
