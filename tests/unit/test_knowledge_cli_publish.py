"""CLI publish-flow HTTP contract — closes the A5 coverage gap.

Review comment 3's fix (``cuga knowledge adaptation-set --publish`` +
``glossary-set --publish`` now hit POST ``/api/manage/config`` with a
``{"config": <draft>}`` body, not the non-existent
``/api/manage/config/publish``) had ZERO regression tests. The same
class of bug — silent endpoint mismatch — produced the original bug;
not having coverage means the next refactor could regress without CI
catching it.

Approach: mock ``httpx.get`` / ``httpx.post`` via monkeypatch, invoke the
CLI command via typer's CliRunner, assert the URLs + payload shapes.
No live HTTP, no FastAPI server, no Dynaconf coupling.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from typer.testing import CliRunner


class _FakeResponse:
    """Minimal duck-type for the httpx.Response shape the CLI uses."""

    def __init__(self, status_code: int = 200, json_payload: dict[str, Any] | None = None, text: str = ""):
        self.status_code = status_code
        self._json = json_payload or {}
        self.text = text or ""

    def json(self) -> dict[str, Any]:
        return self._json

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}: {self.text}")


def _make_mock_httpx(captured_calls: list, draft_payload: dict | None = None):
    """Build mock httpx methods that capture every call and respond
    with the configured draft payload."""
    draft_payload = draft_payload or {
        "agent": {"name": "demo"},
        "knowledge": {"enabled": True, "client_adaptation_text": "stub"},
    }

    def _get(url: str, **kw):
        captured_calls.append(("GET", url, kw))
        return _FakeResponse(200, draft_payload)

    def _patch(url: str, **kw):
        captured_calls.append(("PATCH", url, kw))
        return _FakeResponse(200, {"ok": True})

    def _post(url: str, **kw):
        captured_calls.append(("POST", url, kw))
        return _FakeResponse(200, {"ok": True})

    return _get, _patch, _post


def _invoke_adaptation_set(tmp_path: Path, monkeypatch, captured_calls: list):
    """Invoke ``cuga knowledge adaptation-set --publish`` against the
    mocked HTTP layer. Returns the CliRunner result."""
    import cuga.cli.main as cli_module
    import cuga.cli.knowledge_cmds as knowledge_cmds

    fake_get, fake_patch, fake_post = _make_mock_httpx(captured_calls)
    monkeypatch.setattr(knowledge_cmds.httpx, "get", fake_get)
    monkeypatch.setattr(knowledge_cmds.httpx, "patch", fake_patch)
    monkeypatch.setattr(knowledge_cmds.httpx, "post", fake_post)
    monkeypatch.setattr(knowledge_cmds, "_cuga_server_base_url", lambda: "http://test.local")

    adapt_file = tmp_path / "adapt.md"
    adapt_file.write_text("# adaptation\nHello world.\n", encoding="utf-8")

    runner = CliRunner()
    return runner.invoke(
        cli_module.knowledge_app,
        ["adaptation-set", str(adapt_file), "--publish", "--agent-id", "test-agent"],
    )


# ---------------------------------------------------------------------------
# Invariant — publish endpoint URL is the right one (closes #3)
# ---------------------------------------------------------------------------


def test_adaptation_set_publish_hits_post_api_manage_config(tmp_path, monkeypatch):
    """The CLI must POST to ``/api/manage/config`` (NOT the
    non-existent ``/api/manage/config/publish`` we shipped before)."""
    captured: list = []
    result = _invoke_adaptation_set(tmp_path, monkeypatch, captured)
    assert result.exit_code == 0, result.stdout
    # The publish flow should issue:
    #   PATCH /api/manage/config/draft/knowledge?agent_id=...
    #   GET   /api/manage/config?draft=1&agent_id=...
    #   POST  /api/manage/config?agent_id=...
    posts = [(verb, url, kw) for verb, url, kw in captured if verb == "POST"]
    assert len(posts) == 1, f"expected exactly 1 POST, got {len(posts)}: {posts}"
    _verb, post_url, _kw = posts[0]
    assert "/api/manage/config" in post_url
    assert "/api/manage/config/publish" not in post_url, (
        f"CLI still hitting the dead /api/manage/config/publish endpoint: {post_url!r}"
    )


def test_adaptation_set_publish_body_contains_draft_config(tmp_path, monkeypatch):
    """The POST body must be ``{\"config\": <draft>}`` — the manage
    endpoint reads ``data.get('config', data)``, and without a config
    section the agent-name validator (``data['agent']['name']``)
    raises 400."""
    captured: list = []
    result = _invoke_adaptation_set(tmp_path, monkeypatch, captured)
    assert result.exit_code == 0, result.stdout
    posts = [(verb, url, kw) for verb, url, kw in captured if verb == "POST"]
    _verb, _url, kw = posts[0]
    body = kw.get("json")
    assert isinstance(body, dict), f"POST body not a dict: {body!r}"
    assert "config" in body, f"POST body missing ``config`` key: {body!r}"
    # The config we POSTed is the draft we GET'd — proves the two
    # HTTP hops are correctly chained.
    assert body["config"]["agent"]["name"] == "demo"


# ---------------------------------------------------------------------------
# Invariant — patch-and-publish call order is preserved
# ---------------------------------------------------------------------------


def test_adaptation_set_calls_in_correct_order(tmp_path, monkeypatch):
    """The flow must be:
      1. PATCH the draft with the new adaptation_text
      2. GET the draft (now updated)
      3. POST the full draft as the new published version
    Out-of-order would publish a stale config or skip the PATCH."""
    captured: list = []
    result = _invoke_adaptation_set(tmp_path, monkeypatch, captured)
    assert result.exit_code == 0, result.stdout
    verbs = [verb for verb, _url, _kw in captured]
    # PATCH must come first; GET-then-POST must follow.
    assert verbs[0] == "PATCH", f"expected PATCH first, got {verbs}"
    assert "GET" in verbs, f"missing GET (draft fetch) — got {verbs}"
    assert verbs[-1] == "POST", f"expected POST last, got {verbs}"
    get_index = verbs.index("GET")
    post_index = verbs.index("POST")
    assert get_index < post_index, f"GET (draft) must precede POST (publish): {verbs}"
