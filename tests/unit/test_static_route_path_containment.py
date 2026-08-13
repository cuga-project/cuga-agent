# tests/unit/test_static_route_path_containment.py
"""Containment regression tests for the static file routes (GHSA-55pr-c85h-3p9q).

``serve_flows`` and ``serve_react`` join the request path onto a static
directory. Before the fix they returned the file after only an
``exists``/``isfile`` check, so a path that walked upward out of the static
directory was served. Both now resolve the join and refuse anything landing
outside their static root.

Two layers are covered, because each can hide the bug on its own:

* the handlers called directly, which no HTTP client can normalize away;
* the same handlers behind a router, reached with a percent-encoded URL so
  neither httpx nor Starlette collapses the dot-segments before routing.

Every traversal test first asserts that the *unfixed* join would have found a
real file, so the test cannot pass merely because nothing was there to serve.
The handlers are registered on a throwaway ``FastAPI()`` rather than the real
``app`` — the same lightweight ``TestClient`` pattern as
test_session_settings_routes.py — so routing is exercised without app startup.
"""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
import pytest

import cuga.backend.server.main as _main
from cuga.backend.server.main import serve_flows, serve_react

pytestmark = pytest.mark.unit

CANARY = "cuga-test-canary-this-must-never-be-served"
TRAVERSAL = "../../outside/canary.txt"


@pytest.fixture
def static_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A static dir with a canary file two levels above it.

    Both static roots point at the same directory so one fixture serves both
    handlers. ``index.html`` is present so the SPA fallback is reachable.
    """
    root = tmp_path / "static" / "dist"
    root.mkdir(parents=True)
    (root / "index.html").write_text("<!doctype html><title>app</title>")
    (root / "app.js").write_text("console.log('app');")

    outside = tmp_path / "static" / "dist" / ".." / ".." / "outside"
    outside_resolved = tmp_path / "outside"
    outside_resolved.mkdir(parents=True, exist_ok=True)
    assert Path(os.path.normpath(outside)) == outside_resolved
    (outside_resolved / "canary.txt").write_text(CANARY)

    monkeypatch.setattr(_main.app_state, "STATIC_DIR_FLOWS", str(root), raising=False)
    monkeypatch.setattr(_main.app_state, "STATIC_DIR_HTML", str(root), raising=False)
    return root


@pytest.fixture
def client(static_root: Path) -> TestClient:
    """The real handlers on a bare app — routing without the full app's startup."""
    app = FastAPI()
    app.get("/flows/{full_path:path}")(serve_flows)
    app.get("/{full_path:path}")(serve_react)
    return TestClient(app)


def assert_traversal_would_have_been_served(static_root: Path) -> None:
    """Guard against a vacuous pass: the pre-fix join must find a real file."""
    pre_fix_path = os.path.join(str(static_root), TRAVERSAL)
    assert os.path.exists(pre_fix_path) and os.path.isfile(pre_fix_path), (
        "test is vacuous: the traversal target does not exist, so the unfixed "
        "handler would have 404'd for the wrong reason"
    )
    assert Path(pre_fix_path).read_text() == CANARY


# ---------------------------------------------------------------------------
# Handlers called directly — no client in the loop
# ---------------------------------------------------------------------------


async def test_serve_flows_rejects_traversal(static_root: Path):
    assert_traversal_would_have_been_served(static_root)

    with pytest.raises(HTTPException) as exc:
        await serve_flows(TRAVERSAL, request=None)
    assert exc.value.status_code == 404


async def test_serve_react_rejects_traversal(static_root: Path):
    assert_traversal_would_have_been_served(static_root)

    with pytest.raises(HTTPException) as exc:
        await serve_react(TRAVERSAL, request=None)
    assert exc.value.status_code == 404


async def test_serve_react_rejects_traversal_behind_manage_prefix(static_root: Path):
    """The ``manage/`` prefix is stripped before the join; containment still applies."""
    with pytest.raises(HTTPException) as exc:
        await serve_react(f"manage/{TRAVERSAL}", request=None)
    assert exc.value.status_code == 404


async def test_serve_flows_rejects_absolute_path(static_root: Path, tmp_path: Path):
    """``os.path.join`` discards the static root when the tail is absolute."""
    absolute = str(tmp_path / "outside" / "canary.txt")
    assert os.path.isfile(os.path.join(str(static_root), absolute))

    with pytest.raises(HTTPException) as exc:
        await serve_flows(absolute, request=None)
    assert exc.value.status_code == 404


# ---------------------------------------------------------------------------
# Through the router, with the dot-segments percent-encoded
# ---------------------------------------------------------------------------


def test_flows_route_rejects_encoded_traversal(client: TestClient, static_root: Path):
    assert_traversal_would_have_been_served(static_root)

    resp = client.get("/flows/..%2f..%2foutside%2fcanary.txt")
    assert resp.status_code == 404
    assert CANARY not in resp.text


def test_react_route_rejects_encoded_traversal(client: TestClient, static_root: Path):
    assert_traversal_would_have_been_served(static_root)

    resp = client.get("/..%2f..%2foutside%2fcanary.txt")
    assert resp.status_code == 404
    assert CANARY not in resp.text


def test_encoded_traversal_reaches_the_handler_undecoded(client: TestClient):
    """The encoded URL must survive httpx and Starlette as a real traversal.

    Without this, the two tests above could pass because the request never
    carried dot-segments by the time it reached the route.
    """
    seen: list[str] = []

    app = FastAPI()

    @app.get("/flows/{full_path:path}")
    async def _capture(full_path: str):
        seen.append(full_path)
        return {"ok": True}

    TestClient(app).get("/flows/..%2f..%2foutside%2fcanary.txt")
    assert seen == ["../../outside/canary.txt"]


# ---------------------------------------------------------------------------
# Legitimate requests are unaffected
# ---------------------------------------------------------------------------


def test_flows_route_still_serves_a_real_file(client: TestClient):
    resp = client.get("/flows/app.js")
    assert resp.status_code == 200
    assert "console.log" in resp.text


def test_react_route_still_serves_a_real_asset(client: TestClient):
    resp = client.get("/app.js")
    assert resp.status_code == 200
    assert "console.log" in resp.text


def test_react_route_still_falls_back_to_index_for_deep_links(client: TestClient):
    """An unknown path with no traversal keeps the SPA fallback."""
    resp = client.get("/some/client/side/route")
    assert resp.status_code == 200
    assert "<title>app</title>" in resp.text


def test_react_route_still_strips_the_manage_prefix(client: TestClient):
    resp = client.get("/manage/app.js")
    assert resp.status_code == 200
    assert "console.log" in resp.text
