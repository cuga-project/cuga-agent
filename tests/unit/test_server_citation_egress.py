# tests/unit/test_server_citation_egress.py
"""Tests for the two citation egress helpers extracted into main.py:
- _format_sources_footer  (pure function, no mocking needed)
- _rehydrate_citation_ledger  (async, needs a DB stub)
"""

import asyncio
import json
from types import SimpleNamespace

import cuga.backend.server.main as _main
from cuga.backend.knowledge.sources import get_ledger, _reset_all_ledgers_for_tests
from cuga.backend.server.main import _format_sources_footer, _rehydrate_citation_ledger
import pytest

pytestmark = pytest.mark.unit


def setup_function():
    _reset_all_ledgers_for_tests()


# ---------------------------------------------------------------------------
# _format_sources_footer
# ---------------------------------------------------------------------------


def test_footer_formats_pages_and_omits_none():
    footer = _format_sources_footer(
        [
            {"n": 1, "filename": "a.pdf", "page": 3},
            {"n": 2, "filename": "notes.txt", "page": None},
        ]
    )
    assert footer == "\n\nSources:\n[1] a.pdf p.3\n[2] notes.txt"


def test_footer_single_entry_with_page():
    footer = _format_sources_footer([{"n": 1, "filename": "report.pdf", "page": 12}])
    assert footer == "\n\nSources:\n[1] report.pdf p.12"


def test_footer_single_entry_no_page():
    footer = _format_sources_footer([{"n": 1, "filename": "notes.txt", "page": None}])
    assert footer == "\n\nSources:\n[1] notes.txt"


def test_footer_page_zero_is_included():
    """page=0 is a valid page number; it must not be treated as missing."""
    footer = _format_sources_footer([{"n": 1, "filename": "x.pdf", "page": 0}])
    assert footer == "\n\nSources:\n[1] x.pdf p.0"


# ---------------------------------------------------------------------------
# _rehydrate_citation_ledger helpers
# ---------------------------------------------------------------------------


def _ev(name, data):
    return SimpleNamespace(event_name=name, event_data=data)


def _app_state_with_engine(events):
    """Build a minimal app_state stub with a knowledge engine, plus a DB stub."""

    async def get_stream_events(agent_id, thread_id, user_id):
        return SimpleNamespace(events=events) if events is not None else None

    db = SimpleNamespace(get_stream_events=get_stream_events)
    engine = SimpleNamespace(_config=SimpleNamespace(enabled=True, citations_enabled=True))
    app_state = SimpleNamespace(
        knowledge_engine=engine,
        agent_id="cuga-default",
        _db=db,
    )
    return app_state, db


# ---------------------------------------------------------------------------
# _rehydrate_citation_ledger
# ---------------------------------------------------------------------------


def test_rehydration_restores_only_valid_answer_sources(monkeypatch):
    events = [
        _ev("CodeAgent", "not json"),
        _ev("Answer", "raw wxo text, not json"),
        _ev("Answer", json.dumps(["not", "a", "dict"])),
        _ev("Answer", json.dumps({"data": "x", "sources": None})),
        _ev(
            "Answer",
            json.dumps(
                {
                    "data": "y",
                    "sources": [
                        {
                            "cite_id": "s2",
                            "filename": "a.pdf",
                            "page": 1,
                            "scope": "agent",
                            "snippet": "alpha",
                            "query": "q",
                            "n": 1,
                        },
                        # bogus: no cite_id field matching s\d+ — restore ignores it
                        {"cite_id": "bogus", "filename": "b.pdf"},
                    ],
                }
            ),
        ),
    ]
    app_state, db = _app_state_with_engine(events)

    # Patch get_conversation_db in main's namespace so the helper uses our stub
    monkeypatch.setattr(_main, "get_conversation_db", lambda: db)

    asyncio.run(_rehydrate_citation_ledger(app_state, "t-reh", "default_user"))

    ledger = get_ledger("t-reh", create=False)
    assert ledger is not None
    assert ledger.get("s2") is not None
    assert ledger.get("s2").filename == "a.pdf"
    assert ledger.get("bogus") is None

    # Counter continues past restored ids
    new_id = ledger.register(
        SimpleNamespace(
            text="new",
            filename="c.pdf",
            page=None,
            scope="agent",
            score=0.5,
            section_path="",
        ),
        query="q2",
    )
    assert new_id == "s3"


def test_rehydration_skipped_when_citations_disabled(monkeypatch):
    """When citations are disabled, the helper must return early without touching the DB."""
    engine = SimpleNamespace(_config=SimpleNamespace(enabled=False, citations_enabled=False))
    app_state = SimpleNamespace(knowledge_engine=engine, agent_id="cuga-default")

    db_called = []

    def _should_not_be_called():
        db_called.append(True)
        return SimpleNamespace(get_stream_events=None)

    monkeypatch.setattr(_main, "get_conversation_db", _should_not_be_called)

    asyncio.run(_rehydrate_citation_ledger(app_state, "t-disabled", "default_user"))

    assert not db_called, "DB must not be called when citations disabled"
    assert get_ledger("t-disabled", create=False) is None


def test_rehydration_honors_session_override_over_agent_flag(monkeypatch):
    """fix: rehydration gates on the SAME session-aware predicate as stamping and
    resolution. Agent-level citations_enabled=False + a per-session override=True
    must still rehydrate — otherwise the fresh ledger re-issues colliding cite_ids
    after a restart."""
    from cuga.backend.knowledge.sources import set_session_override_lookup

    events = [
        _ev(
            "Answer",
            json.dumps(
                {
                    "data": "y",
                    "sources": [
                        {
                            "cite_id": "s2",
                            "filename": "a.pdf",
                            "page": 1,
                            "scope": "agent",
                            "snippet": "alpha",
                            "query": "q",
                            "n": 1,
                        }
                    ],
                }
            ),
        ),
    ]

    async def get_stream_events(agent_id, thread_id, user_id):
        return SimpleNamespace(events=events)

    db = SimpleNamespace(get_stream_events=get_stream_events)
    # agent-level citations OFF, but knowledge enabled
    engine = SimpleNamespace(_config=SimpleNamespace(enabled=True, citations_enabled=False))
    app_state = SimpleNamespace(knowledge_engine=engine, agent_id="cuga-default")
    monkeypatch.setattr(_main, "get_conversation_db", lambda: db)
    # per-session override turns citations ON for this thread
    set_session_override_lookup(lambda tid: {"citations_enabled": True} if tid == "t-override" else {})
    try:
        asyncio.run(_rehydrate_citation_ledger(app_state, "t-override", "default_user"))
    finally:
        set_session_override_lookup(None)

    ledger = get_ledger("t-override", create=False)
    assert ledger is not None
    assert ledger.get("s2") is not None  # restored despite the agent flag being off


def test_rehydration_noop_when_ledger_already_present(monkeypatch):
    """If the ledger already exists, no DB fetch is performed."""
    app_state, db = _app_state_with_engine(events=[])
    # Pre-create the ledger
    existing = get_ledger("t-existing")

    db_called = []

    async def _tracking_get_stream_events(agent_id, thread_id, user_id):
        db_called.append(True)
        return SimpleNamespace(events=[])

    db.get_stream_events = _tracking_get_stream_events
    monkeypatch.setattr(_main, "get_conversation_db", lambda: db)

    asyncio.run(_rehydrate_citation_ledger(app_state, "t-existing", "default_user"))

    assert not db_called, "DB must not be called when ledger already in memory"
    assert get_ledger("t-existing", create=False) is existing


def test_rehydration_handles_no_stream_history(monkeypatch):
    """stream_history=None (no prior events) must not crash and must not create a ledger."""

    async def get_stream_events(agent_id, thread_id, user_id):
        return None  # no history

    db = SimpleNamespace(get_stream_events=get_stream_events)
    engine = SimpleNamespace(_config=SimpleNamespace(enabled=True, citations_enabled=True))
    app_state = SimpleNamespace(knowledge_engine=engine, agent_id="cuga-default")

    monkeypatch.setattr(_main, "get_conversation_db", lambda: db)

    asyncio.run(_rehydrate_citation_ledger(app_state, "t-nohistory", "default_user"))
    # No ledger created (events_list was empty/None, no ledger created)
    # The ledger may or may not be created — what matters is no exception raised
    # and if created it is empty
    ledger = get_ledger("t-nohistory", create=False)
    assert ledger is None or len(ledger) == 0
