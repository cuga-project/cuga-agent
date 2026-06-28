"""Hard session-isolation guarantees for the knowledge backend.

This test exists because of a user-reported "I uploaded a doc in session A,
opened session B, uploaded a different doc, asked a question in B, and got
the answer from A's doc" bug. The trace evidence didn't actually show a
cross-session leak (every chunk in the trace was tagged to a single file
in the new session's collection), but the user's question is the right one
to pin down with tests anyway: **two sessions with distinct thread_ids
MUST NOT see each other's documents at any layer**.

We exercise every layer that could possibly leak:
  1. Collection-name resolution (auth.resolve_collection)
  2. Metadata list_documents
  3. The route GET /api/knowledge/documents?scope=session
  4. The route POST /api/knowledge/search?scope=session

If a future change accidentally couples sessions (e.g., a shared cache
keyed by something other than thread_id, a query that forgets to filter,
a session_provider that creates collections from user_id instead of
thread_id), one of these tests will fail loudly.
"""

from __future__ import annotations

import re
from types import SimpleNamespace

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from cuga.backend.knowledge.auth import (
    KnowledgeIdentity,
    require_internal_or_auth,
    resolve_collection,
)
from cuga.backend.knowledge.routes import knowledge_router


_THREAD_A = "thread-aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa"
_THREAD_B = "thread-bbbbbbbb-bbbb-4bbb-bbbb-bbbbbbbbbbbb"


# -------- resolve_collection: distinct thread_ids → distinct collections --------


def test_resolve_collection_produces_distinct_names_for_distinct_threads():
    """The MOST important invariant. If this fails, nothing else matters."""
    a = KnowledgeIdentity(
        user_id=None,
        tenant_id=None,
        agent_id="cuga-default",
        thread_id=_THREAD_A,
        auth_mode="external",
    )
    b = KnowledgeIdentity(
        user_id=None,
        tenant_id=None,
        agent_id="cuga-default",
        thread_id=_THREAD_B,
        auth_mode="external",
    )
    app = FastAPI()
    # _scope_enabled_for_request needs an engine with session_level_enabled=True
    _eng = SimpleNamespace(
        _config=SimpleNamespace(
            enabled=True,
            session_level_enabled=True,
            agent_level_enabled=True,
            max_files_per_request=5,
        )
    )
    app.state.app_state = SimpleNamespace(knowledge_engine=_eng, knowledge_provider=None)
    # Mock request object — resolve_collection only reads app.state.app_state
    req_a = SimpleNamespace(app=app)
    req_b = SimpleNamespace(app=app)
    coll_a = resolve_collection(a, "session", req_a)
    coll_b = resolve_collection(b, "session", req_b)
    assert coll_a != coll_b, (
        f"Distinct thread_ids must produce distinct collections, got {coll_a!r} == {coll_b!r}"
    )
    assert coll_a.startswith("kb_sess_")
    assert coll_b.startswith("kb_sess_")


def test_resolve_collection_session_requires_thread_id():
    """Without a thread_id, session scope must 400 — not fall back to any
    shared/default collection."""
    from fastapi import HTTPException

    identity = KnowledgeIdentity(
        user_id=None,
        tenant_id=None,
        agent_id="cuga-default",
        thread_id=None,
        auth_mode="external",
    )
    app = FastAPI()
    # _scope_enabled_for_request needs an engine with session_level_enabled=True
    _eng = SimpleNamespace(
        _config=SimpleNamespace(
            enabled=True,
            session_level_enabled=True,
            agent_level_enabled=True,
            max_files_per_request=5,
        )
    )
    app.state.app_state = SimpleNamespace(knowledge_engine=_eng, knowledge_provider=None)
    req = SimpleNamespace(app=app)
    try:
        resolve_collection(identity, "session", req)
        assert False, "Expected HTTPException(400) — session scope without thread_id must not resolve"
    except HTTPException as e:
        assert e.status_code == 400


def test_thread_id_sanitization_does_not_collapse_distinct_uuids():
    """A future change to ``_sanitize`` (in auth.py) that's too aggressive
    could collapse two distinct UUIDs to the same collection. Lock the
    invariant: any two real UUIDs map to distinct collection names."""
    # Real UUIDs from a recent live trace + handcrafted near-collision candidates.
    uuids = [
        "6f3911f1-1c7c-4d07-b0ee-c0752ade92cf",
        "6f3911f1-1c7c-4d07-b0ee-c0752ade92d0",  # differs by 1 nibble
        "00000000-0000-4000-8000-000000000000",
        "00000000_0000_4000_8000_000000000000",  # what sanitize would produce — must NOT collide with the dash form
    ]
    sanitized = set()
    for u in uuids:
        ident = KnowledgeIdentity(
            user_id=None,
            tenant_id=None,
            agent_id="cuga-default",
            thread_id=u,
            auth_mode="external",
        )
        app = FastAPI()
        _eng = SimpleNamespace(
            _config=SimpleNamespace(
                enabled=True,
                session_level_enabled=True,
                agent_level_enabled=True,
                max_files_per_request=5,
            )
        )
        app.state.app_state = SimpleNamespace(knowledge_engine=_eng, knowledge_provider=None)
        coll = resolve_collection(ident, "session", SimpleNamespace(app=app))
        sanitized.add(coll)
    # Last two SHOULD collide (because `-` and `_` both map to `_`); the first
    # two MUST NOT collide. So we expect 3 distinct collection names.
    assert len(sanitized) == 3, f"Sanitization collision detected: {sanitized}"


# -------- HTTP route isolation --------


class _IsolatingFakeEngine:
    """In-memory backend that simulates correct per-collection isolation.

    The vector store / metadata store implementations isolate by collection
    name; this fake mimics that contract so we can exercise the route layer.
    A test that fails here means the ROUTE leaked the collection across
    scopes — e.g. ignored the thread_id-derived collection name.
    """

    def __init__(self):
        self._config = SimpleNamespace(
            enabled=True,
            session_level_enabled=True,
            agent_level_enabled=True,
            max_files_per_request=5,
            default_limit=10,
            default_score_threshold=0.0,
        )
        # collection → list[doc dict]
        self._docs: dict[str, list[dict]] = {}
        # collection → list[(text, filename)]
        self._chunks: dict[str, list[tuple[str, str]]] = {}

    def add_doc(self, collection: str, filename: str, chunks: list[str]):
        self._docs.setdefault(collection, []).append(
            {
                "filename": filename,
                "chunk_count": len(chunks),
                "status": "indexed",
                "ingested_at": "2026-06-02T12:00:00+00:00",
                "preview": chunks[0] if chunks else "",
            }
        )
        self._chunks.setdefault(collection, []).extend((text, filename) for text in chunks)

    async def list_documents(self, collection: str):
        rows = self._docs.get(collection, [])
        # Match the engine's DocInfo shape
        return [SimpleNamespace(**r) for r in rows]

    async def search(
        self,
        collection: str,
        query: str,
        limit: int = 10,
        score_threshold: float = 0.0,
        scope: str = "",
    ):
        # Returns SearchResult-shaped objects; in tests we only care that
        # ONLY collection-local chunks come back. The route stamps results
        # with their ``source`` from the caller's scope, so include it here.
        return [
            SimpleNamespace(text=text, filename=fname, page=1, score=0.5, scope=scope, section_path="")
            for text, fname in self._chunks.get(collection, [])[:limit]
        ]

    async def search_with_stats(
        self,
        *,
        collection: str,
        query: str,
        limit: int = 10,
        score_threshold: float = 0.0,
        scope: str = "",
    ):
        # After Step 5, the route's single-scope path calls
        # ``search_with_stats`` (returns ``(results, _JunkFilterStats)``)
        # so the wire envelope can carry per-scope filter accounting.
        # The isolation tests don't care about stats — just that the
        # ONLY chunks returned are the caller's. Pass through to the
        # existing ``search`` impl and synthesize a zero-stats object.
        from cuga.backend.knowledge.engine import _JunkFilterStats

        results = await self.search(collection, query, limit, score_threshold, scope)
        return results, _JunkFilterStats(candidates=len(results))

    async def get_task(self, task_id: str):
        return None


def _client_with_engine(engine):
    app = FastAPI()
    app.include_router(knowledge_router)

    def _make_override(thread_id):
        async def _override(request: Request) -> KnowledgeIdentity:
            # Production identity comes from headers; mimic that here so
            # the route's normal X-Thread-ID flow is exercised.
            return KnowledgeIdentity(
                user_id=None,
                tenant_id=None,
                agent_id="cuga-default",
                thread_id=request.headers.get("X-Thread-ID"),
                auth_mode="external",
            )

        return _override

    app.dependency_overrides[require_internal_or_auth] = _make_override(None)
    app.state.app_state = SimpleNamespace(
        knowledge_engine=engine,
        knowledge_provider=None,
    )
    return TestClient(app), app


def _sess_collection(thread_id: str) -> str:
    return f"kb_sess_{re.sub(r'[^a-zA-Z0-9_]', '_', thread_id)}"


def test_list_documents_scope_session_returns_only_callers_thread():
    """The smoking-gun scenario: session A has Doc_A, session B has Doc_B,
    a GET with thread_id_A must NEVER see Doc_B."""
    engine = _IsolatingFakeEngine()
    engine.add_doc(_sess_collection(_THREAD_A), "Doc_A.pdf", ["alpha chunk from A"])
    engine.add_doc(_sess_collection(_THREAD_B), "Doc_B.pdf", ["beta chunk from B"])
    client, _ = _client_with_engine(engine)

    res_a = client.get(
        "/api/knowledge/documents?scope=session",
        headers={"X-Agent-ID": "cuga-default", "X-Thread-ID": _THREAD_A},
    )
    assert res_a.status_code == 200
    docs_a = res_a.json()["documents"]
    assert len(docs_a) == 1
    assert docs_a[0]["filename"] == "Doc_A.pdf"
    assert all("Doc_B" not in d["filename"] for d in docs_a)

    res_b = client.get(
        "/api/knowledge/documents?scope=session",
        headers={"X-Agent-ID": "cuga-default", "X-Thread-ID": _THREAD_B},
    )
    assert res_b.status_code == 200
    docs_b = res_b.json()["documents"]
    assert len(docs_b) == 1
    assert docs_b[0]["filename"] == "Doc_B.pdf"


def test_search_scope_session_returns_only_callers_thread_chunks():
    """The bug the user reported in plain terms: 'searching in session B
    returned content from session A'. Make it impossible at the route layer."""
    engine = _IsolatingFakeEngine()
    engine.add_doc(
        _sess_collection(_THREAD_A),
        "Doc_A.pdf",
        [
            "secret content unique to session A about UNMT",
        ],
    )
    engine.add_doc(
        _sess_collection(_THREAD_B),
        "Doc_B.pdf",
        [
            "completely different content for session B about car licenses",
        ],
    )
    client, _ = _client_with_engine(engine)

    # Search from session B should never return Doc_A's content
    res_b = client.post(
        "/api/knowledge/search",
        headers={"X-Agent-ID": "cuga-default", "X-Thread-ID": _THREAD_B},
        json={"query": "anything", "scope": "session"},
    )
    assert res_b.status_code == 200
    results_b = res_b.json()["results"]
    for hit in results_b:
        assert hit["filename"] != "Doc_A.pdf", (
            f"Session B search returned Doc_A — CROSS-SESSION LEAK: {hit!r}"
        )
        assert "UNMT" not in hit["text"], f"Session B search returned A's content — LEAK: {hit!r}"

    # Conversely, search from session A should never return Doc_B's content
    res_a = client.post(
        "/api/knowledge/search",
        headers={"X-Agent-ID": "cuga-default", "X-Thread-ID": _THREAD_A},
        json={"query": "anything", "scope": "session"},
    )
    assert res_a.status_code == 200
    for hit in res_a.json()["results"]:
        assert hit["filename"] != "Doc_B.pdf"
        assert "car licenses" not in hit["text"]


def test_session_scope_without_thread_id_400s_in_route():
    """Defense in depth: even if a future caller accidentally drops the
    X-Thread-ID header, the route must 400 — never fall back to a
    'default' / 'shared' session collection."""
    engine = _IsolatingFakeEngine()
    client, _ = _client_with_engine(engine)

    res = client.get(
        "/api/knowledge/documents?scope=session",
        headers={"X-Agent-ID": "cuga-default"},  # NO X-Thread-ID
    )
    assert res.status_code == 400, (
        f"Session scope without thread_id MUST 400, got {res.status_code} with body {res.text}"
    )

    res2 = client.post(
        "/api/knowledge/search",
        headers={"X-Agent-ID": "cuga-default"},  # NO X-Thread-ID
        json={"query": "x", "scope": "session"},
    )
    assert res2.status_code == 400


def test_session_thread_id_whitespace_only_treated_as_missing_in_list():
    """A thread_id of ``"   "`` (whitespace) must not resolve to a valid
    collection — it would sanitize to ``___`` and create a phantom session
    everyone shares."""
    engine = _IsolatingFakeEngine()
    client, _ = _client_with_engine(engine)
    res = client.get(
        "/api/knowledge/documents?scope=session",
        headers={"X-Agent-ID": "cuga-default", "X-Thread-ID": "   "},
    )
    assert res.status_code == 400, (
        "Whitespace thread_id must be treated as missing; otherwise multiple "
        "callers who all forget to set X-Thread-ID end up sharing kb_sess____"
    )


def test_session_thread_id_whitespace_only_treated_as_missing_in_search():
    """Same whitespace guard must hold for the search route. Originally
    only the list route checked this explicitly; ``resolve_collection``
    used plain truthiness (``if not identity.thread_id``) which let ``"   "``
    through and would have created a shared phantom collection. The fix
    moved the strip-aware check into ``resolve_collection`` itself so
    every caller (search, upload, list, delete) gets the same protection."""
    engine = _IsolatingFakeEngine()
    client, _ = _client_with_engine(engine)
    res = client.post(
        "/api/knowledge/search",
        headers={"X-Agent-ID": "cuga-default", "X-Thread-ID": "  \t \n "},
        json={"query": "x", "scope": "session"},
    )
    assert res.status_code == 400


def test_session_thread_id_whitespace_only_treated_as_missing_in_upload():
    """Upload route too — the most dangerous case, because a misformed
    thread_id here would create the phantom collection that subsequent
    searches then read."""
    engine = _IsolatingFakeEngine()
    # The upload route needs more engine surface than the IsolatingFakeEngine
    # provides; we only care that the 400 fires before any ingest work.
    engine._sanitize_and_validate = lambda *a, **k: "x.pdf"  # type: ignore[attr-defined]
    client, _ = _client_with_engine(engine)
    res = client.post(
        "/api/knowledge/documents",
        files={"files": ("x.pdf", b"%PDF-1.7", "application/pdf")},
        data={"scope": "session"},
        headers={"X-Agent-ID": "cuga-default", "X-Thread-ID": "  "},
    )
    assert res.status_code == 400
