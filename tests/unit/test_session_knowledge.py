"""Tests for session-level knowledge: provider, deep-merge, MCP tools."""

import json
from pathlib import Path


from cuga.backend.knowledge.session_provider import (
    AgentKnowledgeState,
    PersistentSessionProvider,
    SessionKnowledgeState,
    SessionProvider,
    _deep_merge,
)


# ---------------------------------------------------------------------------
# _deep_merge
# ---------------------------------------------------------------------------


class TestDeepMerge:
    def test_flat_merge(self):
        base = {"a": 1, "b": 2}
        _deep_merge(base, {"b": 3, "c": 4})
        assert base == {"a": 1, "b": 3, "c": 4}

    def test_nested_merge(self):
        base = {"x": {"a": 1, "b": 2}, "y": 10}
        _deep_merge(base, {"x": {"b": 99, "c": 3}})
        assert base == {"x": {"a": 1, "b": 99, "c": 3}, "y": 10}

    def test_overwrite_non_dict_with_dict(self):
        base = {"a": "string"}
        _deep_merge(base, {"a": {"nested": True}})
        assert base == {"a": {"nested": True}}

    def test_overwrite_dict_with_non_dict(self):
        base = {"a": {"nested": True}}
        _deep_merge(base, {"a": 42})
        assert base == {"a": 42}

    def test_empty_patch(self):
        base = {"a": 1}
        _deep_merge(base, {})
        assert base == {"a": 1}


# ---------------------------------------------------------------------------
# SessionProvider (in-memory)
# ---------------------------------------------------------------------------


class TestSessionProvider:
    def test_get_or_create_session(self):
        sp = SessionProvider()
        state = sp.get_or_create_session("t1")
        assert state.thread_id == "t1"
        assert state.filenames == []
        assert state.overrides == {}
        # Second call returns same state
        assert sp.get_or_create_session("t1") is state

    def test_save_and_get(self):
        sp = SessionProvider()
        state = SessionKnowledgeState(thread_id="t2", filter_id="f1", filenames=["a.pdf"])
        sp.save_session("t2", state)
        assert sp.get_session("t2") is state

    def test_delete(self):
        sp = SessionProvider()
        sp.get_or_create_session("t3")
        sp.delete_session("t3")
        assert sp.get_session("t3") is None

    def test_patch_overrides_creates_session(self):
        sp = SessionProvider()
        state = sp.patch_session_overrides("t4", {"mode": "full"})
        assert state.overrides == {"mode": "full"}

    def test_patch_overrides_deep_merges(self):
        sp = SessionProvider()
        sp.patch_session_overrides("t5", {"a": {"x": 1}})
        sp.patch_session_overrides("t5", {"a": {"y": 2}})
        state = sp.get_session("t5")
        assert state.overrides == {"a": {"x": 1, "y": 2}}

    def test_agent_get_or_create(self):
        sp = SessionProvider()
        agent = sp.get_or_create_agent("cuga-default", "3")
        assert agent.agent_id == "cuga-default"
        assert agent.config_version == "3"
        assert agent.key == "cuga-default:3"
        assert agent.prefix == "agent_cuga-default_3/"

    def test_session_ownership_enforced(self):
        sp = SessionProvider()
        sp.get_or_create_session("t1", user_id="alice", tenant_id="acme")
        assert sp.check_session_access("t1", "alice", "acme") is True
        assert sp.check_session_access("t1", "bob", "acme") is False
        assert sp.check_session_access("t1", "alice", "other") is False

    def test_session_ownership_new_session_allows_any(self):
        sp = SessionProvider()
        # Non-existent session — should allow access (will be created)
        assert sp.check_session_access("new", "anyone", "any") is True

    def test_patch_creates_owned_session(self):
        """patch_session_overrides must propagate user_id/tenant_id to new sessions."""
        sp = SessionProvider()
        sp.patch_session_overrides("t1", {"x": 1}, user_id="alice", tenant_id="acme")
        state = sp.get_session("t1")
        assert state.user_id == "alice"
        assert state.tenant_id == "acme"
        # Another user should be blocked
        assert sp.check_session_access("t1", "bob", "acme") is False


# ---------------------------------------------------------------------------
# Regression: sequential nested PATCHes preserve siblings
# ---------------------------------------------------------------------------


class TestSequentialNestedPatches:
    def test_sequential_nested_patches_preserve_siblings(self):
        """Two sequential PATCHes on different document_awareness_* keys must both survive."""
        sp = SessionProvider()
        thread_id = "regression-thread-1"

        # PATCH 1: set document_awareness_mode
        sp.patch_session_overrides(thread_id, {"document_awareness_mode": "full"})

        # PATCH 2: set document_awareness_limit (different key)
        sp.patch_session_overrides(thread_id, {"document_awareness_limit": 50})

        # Both must survive
        state = sp.get_session(thread_id)
        assert state.overrides["document_awareness_mode"] == "full"
        assert state.overrides["document_awareness_limit"] == 50

    def test_nested_dict_patches_preserve_siblings(self):
        """PATCHes to different keys within a nested dict preserve both."""
        sp = SessionProvider()
        sp.patch_session_overrides("t", {"config": {"key_a": 1}})
        sp.patch_session_overrides("t", {"config": {"key_b": 2}})
        state = sp.get_session("t")
        assert state.overrides["config"] == {"key_a": 1, "key_b": 2}

    def test_three_sequential_patches(self):
        sp = SessionProvider()
        sp.patch_session_overrides("t", {"a": 1})
        sp.patch_session_overrides("t", {"b": 2})
        sp.patch_session_overrides("t", {"c": 3})
        state = sp.get_session("t")
        assert state.overrides == {"a": 1, "b": 2, "c": 3}


# ---------------------------------------------------------------------------
# TTL expiry
# ---------------------------------------------------------------------------


class TestCollectExpiredSessions:
    def test_expired_sessions_returned(self):
        sp = SessionProvider()
        # Create a session with a very old timestamp
        state = sp.get_or_create_session("old")
        state.created_at = "2020-01-01T00:00:00+00:00"
        sp.save_session("old", state)
        # Create a recent session
        sp.get_or_create_session("new")

        expired = sp.collect_expired_sessions(max_age_seconds=1)
        thread_ids = [s.thread_id for s in expired]
        assert "old" in thread_ids
        assert "new" not in thread_ids

    def test_no_expired_sessions(self):
        sp = SessionProvider()
        sp.get_or_create_session("recent")
        expired = sp.collect_expired_sessions(max_age_seconds=999999)
        assert len(expired) == 0

    def test_missing_created_at_skipped(self):
        sp = SessionProvider()
        state = SessionKnowledgeState(thread_id="no-ts", created_at="")
        sp.save_session("no-ts", state)
        expired = sp.collect_expired_sessions(max_age_seconds=1)
        assert len(expired) == 0


# ---------------------------------------------------------------------------
# Prefix helpers
# ---------------------------------------------------------------------------


class TestPrefixHelpers:
    def test_session_prefix_normal(self):
        from cuga.backend.knowledge.session_provider import session_prefix

        result = session_prefix("abcdef1234567890extra")
        assert result == "sess_abcdef1234567890/"

    def test_session_prefix_short_id_padded(self):
        from cuga.backend.knowledge.session_provider import session_prefix

        result = session_prefix("abc")
        assert result == "sess_abc0000000000000/"
        assert len("abc0000000000000") == 16

    def test_agent_prefix(self):
        from cuga.backend.knowledge.session_provider import agent_prefix

        result = agent_prefix("cuga-default", "3")
        assert result == "agent_cuga-default_3/"


# ---------------------------------------------------------------------------
# PersistentSessionProvider
# ---------------------------------------------------------------------------


class TestPersistentSessionProvider:
    def test_write_through_persists(self, tmp_path: Path):
        path = tmp_path / "state.json"
        sp = PersistentSessionProvider(path)
        sp.patch_session_overrides("t1", {"mode": "full"})
        # File should exist and contain the state
        assert path.exists()
        data = json.loads(path.read_text())
        assert "t1" in data["sessions"]
        assert data["sessions"]["t1"]["overrides"]["mode"] == "full"

    def test_patch_forwards_ownership(self, tmp_path: Path):
        """PersistentSessionProvider.patch_session_overrides must forward user_id/tenant_id."""
        path = tmp_path / "state.json"
        sp = PersistentSessionProvider(path)
        sp.patch_session_overrides("t1", {"x": 1}, user_id="alice", tenant_id="acme")
        state = sp.get_session("t1")
        assert state.user_id == "alice"
        assert state.tenant_id == "acme"
        # Verify persisted to disk too
        data = json.loads(path.read_text())
        assert data["sessions"]["t1"]["user_id"] == "alice"
        assert data["sessions"]["t1"]["tenant_id"] == "acme"

    def test_load_on_init(self, tmp_path: Path):
        path = tmp_path / "state.json"
        # Write initial state
        sp1 = PersistentSessionProvider(path)
        sp1.patch_session_overrides("t1", {"x": 1})
        sp1.save_agent(AgentKnowledgeState(agent_id="a", config_version="1", filenames=["f.pdf"]))

        # Create new provider from same file
        sp2 = PersistentSessionProvider(path)
        assert sp2.get_session("t1").overrides == {"x": 1}
        assert sp2.get_agent("a:1").filenames == ["f.pdf"]

    def test_no_double_write(self, tmp_path: Path):
        """Provider's save() writes to disk. Routes should not write again."""
        path = tmp_path / "state.json"
        sp = PersistentSessionProvider(path)

        # Only call provider methods — no direct path.write_text()
        sp.patch_session_overrides("t1", {"a": 1})
        sp.patch_session_overrides("t1", {"b": 2})

        data = json.loads(path.read_text())
        assert data["sessions"]["t1"]["overrides"] == {"a": 1, "b": 2}

    def test_delete_persists(self, tmp_path: Path):
        path = tmp_path / "state.json"
        sp = PersistentSessionProvider(path)
        sp.get_or_create_session("t1")
        sp.delete_session("t1")
        data = json.loads(path.read_text())
        assert "t1" not in data["sessions"]
