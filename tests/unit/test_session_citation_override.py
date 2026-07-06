import pytest

from cuga.backend.knowledge.session_provider import SessionProvider
from cuga.backend.knowledge.routes import _apply_session_settings_patch


def test_patch_applies_only_allowed_keys():
    provider = SessionProvider()
    state = _apply_session_settings_patch(
        provider, "t-1", {"citations_enabled": False, "evil": 1}, user_id="u", tenant_id=""
    )
    assert state.overrides == {"citations_enabled": False}


def test_patch_coerces_truthy_strings():
    provider = SessionProvider()
    state = _apply_session_settings_patch(
        provider, "t-1", {"citations_enabled": "true"}, user_id="u", tenant_id=""
    )
    assert state.overrides["citations_enabled"] is True


def test_empty_patch_rejected():
    provider = SessionProvider()
    with pytest.raises(ValueError):
        _apply_session_settings_patch(provider, "t-1", {"unknown": 1}, user_id="u", tenant_id="")
