"""Session ownership store: collection index, atomic writes, fail-closed load.

The ownership check must see every record that maps to a collection, because
lookalike thread_ids ("a-b", "a_b") share one. These tests pin that the index
keeps that guarantee through every write path, that a check no longer scans
the whole store, and that the file on disk can neither be left half-written by
a crash nor be silently replaced by an empty store when it cannot be read.
"""

import json
import uuid

import pytest

from cuga.backend.knowledge import session_provider as sp
from cuga.backend.knowledge.session_provider import (
    PersistentSessionProvider,
    SessionKnowledgeState,
    SessionProvider,
)

pytestmark = pytest.mark.unit


def test_lookalike_thread_ids_stay_bound_through_every_write_path():
    store = SessionProvider()
    assert store.claim_session("a-b", "alice")
    assert not store.claim_session("a_b", "bob")  # same collection, other owner
    assert store.claim_session("a_b", "alice")  # same owner may use either spelling

    store.clear_session("a-b")  # clearing knowledge keeps ownership
    assert not store.claim_session("a_b", "bob")

    store.delete_session("a-b")  # "a_b" is still owned by alice
    assert not store.claim_session("a-b", "bob")
    store.delete_session("a_b")
    assert store.claim_session("a-b", "bob")  # nothing left, so free to claim


def test_check_looks_up_one_collection_instead_of_scanning_the_store(monkeypatch):
    store = SessionProvider()
    for i in range(2000):
        store.claim_session(str(uuid.uuid4()), f"user{i % 50}")
    store.claim_session("target-thread", "alice")

    calls = []
    real = sp.session_collection_name
    monkeypatch.setattr(sp, "session_collection_name", lambda t: calls.append(t) or real(t))
    assert store.check_session_access("target-thread", "alice")
    assert not store.check_session_access("target_thread", "bob")
    assert len(calls) <= 2, f"check computed {len(calls)} collection names; it should not scan"


def test_direct_writes_to_the_store_rebuild_the_index():
    store = SessionProvider()
    store.claim_session("a-b", "alice")
    store._sessions["c-d"] = SessionKnowledgeState(thread_id="c-d", user_id="carol")
    assert not store.check_session_access("c_d", "bob")


def test_owners_and_lookalikes_survive_a_reload(tmp_path):
    path = tmp_path / "session_knowledge.json"
    store = PersistentSessionProvider(path)
    store.claim_session("a-b", "alice")

    reloaded = PersistentSessionProvider(path)
    assert reloaded.get_session("a-b").user_id == "alice"
    assert not reloaded.claim_session("a_b", "bob")


@pytest.mark.parametrize("content", ['{"sessions": {"a-b": {"user_id": "al', "not json", "[]"])
def test_unreadable_store_refuses_to_start_empty(tmp_path, content):
    path = tmp_path / "session_knowledge.json"
    path.write_text(content)
    with pytest.raises(RuntimeError, match="unreadable"):
        PersistentSessionProvider(path)
    assert path.read_text() == content  # left in place for the operator


def test_missing_store_starts_empty(tmp_path):
    store = PersistentSessionProvider(tmp_path / "absent.json")
    assert store.list_sessions() == {}


def test_failed_write_leaves_the_previous_file_intact(tmp_path, monkeypatch):
    path = tmp_path / "session_knowledge.json"
    store = PersistentSessionProvider(path)
    store.claim_session("a-b", "alice")
    before = path.read_text()

    def crash_mid_write(payload, f, **kwargs):
        f.write('{"sessions": {')
        raise OSError("disk full")

    monkeypatch.setattr(sp.json, "dump", crash_mid_write)
    store.claim_session("c-d", "carol")  # persist fails; logged, not raised

    assert path.read_text() == before
    assert json.loads(path.read_text())["sessions"]["a-b"]["user_id"] == "alice"
    assert [p.name for p in tmp_path.iterdir()] == [path.name]  # no temp file left behind
