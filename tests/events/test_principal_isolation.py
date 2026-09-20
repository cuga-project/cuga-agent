"""Unlinked channel senders must be ISOLATED by their native id, never collapsed onto one shared
identity. This is ADR 0009 ("a channel is an auth authority; isolation is always on from the native
id, linking is an upgrade") and closes the cross-user leak where every unlinked sender would share
one identity's memory, resolved credentials, and subscriptions.

The regression these pin: `unlinked_principal` returning the DEFAULT principal (or None with the
caller falling back to it) — which would make Alice and Bob run as the same `default/default/admin`.
"""

from __future__ import annotations

import pytest

from cuga.backend.events.principal import Principal, unlinked_principal

pytestmark = pytest.mark.unit


def test_two_senders_get_distinct_scopes():
    alice = unlinked_principal("whatsapp", "15551110000")
    bob = unlinked_principal("whatsapp", "15552220000")
    assert alice is not None and bob is not None
    assert alice.scope != bob.scope, "different senders must not share a scope"


def test_scope_is_not_the_shared_default():
    p = unlinked_principal("whatsapp", "15551110000")
    default = Principal()  # tenant/instance/user_id = default/default/local
    assert p.scope != default.scope
    assert p.user_id not in ("admin", "local", ""), "must not collapse onto a shared/default user"
    assert p.user_id.startswith("ch_"), "unlinked channel identities are namespaced"


def test_same_sender_is_stable():
    a = unlinked_principal("slack", "U123")
    b = unlinked_principal("slack", "U123")
    assert a is not None and a.scope == b.scope, "same sender → same scope across messages"


def test_channel_namespacing_separates_same_id_on_different_channels():
    wa = unlinked_principal("whatsapp", "123")
    tg = unlinked_principal("telegram", "123")
    assert wa is not None and tg is not None and wa.scope != tg.scope


def test_no_native_id_returns_none_so_caller_keeps_its_default():
    assert unlinked_principal("whatsapp", "") is None


def test_scope_is_filesystem_and_path_safe():
    # a sender id with awkward characters must not leak "/" (the scope separator) or spaces
    p = unlinked_principal("slack", "weird id/with:stuff")
    assert p is not None
    assert "/" not in p.user_id and " " not in p.user_id
