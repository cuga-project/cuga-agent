# tests/unit/test_registry_trajectory_recording.py
"""Normal trajectory recording still works through the registry endpoint.

``collect_step_external`` now refuses paths outside the tracker's base directory.
The refusal is covered by test_tracker_external_path_containment.py; this covers
the other half, on the real caller: a legitimate ``trajectory_path`` still
records the call.

The path is built by ``get_current_trajectory_path`` and URL-quoted, exactly as
``sandbox.py`` builds the ``/functions/call`` URL, and the request goes through
the real route so the query parameter is decoded the way it is in production.
Only the MCP tool behind the endpoint is stubbed.
"""

from __future__ import annotations

import contextlib
import json
from pathlib import Path
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient

from cuga.backend.activity_tracker.tracker import ActivityTracker
from cuga.backend.tools_env.registry.registry import api_registry_server as srv
from cuga.config import settings

pytestmark = pytest.mark.unit


class _StubResult:
    text = '{"contacts": ["ada"]}'


class _StubRegistry:
    async def show_apis_for_app(self, app_name):
        return {"list_contacts": {"secure": False, "path": "/contacts"}}

    async def call_function(self, **kwargs):
        return [_StubResult()]


class _StubMcpManager:
    auth_config: dict = {}


@pytest.fixture
def registry_client(tmp_path: Path):
    """The real app with MCP startup skipped, and every global it touches restored.

    ActivityTracker is a singleton and TRACKER_ENABLED is a process-global setting,
    so both are saved and restored, as are the module globals and the app lifespan.
    """
    tracker = ActivityTracker()
    original_base_dir = tracker.get_base_dir()
    original_enabled = settings.advanced_features.tracker_enabled
    original_steps = list(tracker.steps)
    original_prompts = list(tracker.prompts)
    # registry/mcp_manager are only bound by the lifespan, so they may not exist yet.
    original_registry = getattr(srv, "registry", None)
    original_manager = getattr(srv, "mcp_manager", None)
    original_db_mode = srv.database_mode
    original_lifespan = srv.app.router.lifespan_context

    @contextlib.asynccontextmanager
    async def _skip_mcp_startup(app):
        srv.registry = _StubRegistry()
        srv.mcp_manager = _StubMcpManager()
        srv.database_mode = False
        yield

    srv.app.router.lifespan_context = _skip_mcp_startup
    tracker.set_base_dir(str(tmp_path))
    tracker.experiment_folder = "experiment"
    tracker.task_id = "task-1"
    (tmp_path / "experiment").mkdir()

    try:
        with TestClient(srv.app) as client:
            yield client, tracker
    finally:
        srv.app.router.lifespan_context = original_lifespan
        srv.registry = original_registry
        srv.mcp_manager = original_manager
        srv.database_mode = original_db_mode
        tracker.set_base_dir(original_base_dir)
        tracker.steps = original_steps
        tracker.prompts = original_prompts
        settings.update({"ADVANCED_FEATURES": {"TRACKER_ENABLED": original_enabled}}, merge=True)


def test_registry_call_records_trajectory_for_a_legitimate_path(registry_client):
    client, tracker = registry_client

    trajectory_path = tracker.get_current_trajectory_path()
    assert trajectory_path, "the tracker must produce a path to record to"

    response = client.post(
        f"/functions/call?trajectory_path={quote(trajectory_path)}",
        json={"app_name": "crm", "function_name": "list_contacts", "args": {}},
    )

    assert response.status_code == 200
    assert response.json() == {"contacts": ["ada"]}

    recorded = json.loads(Path(trajectory_path).read_text())
    assert [step["name"] for step in recorded["steps"]] == ["api_call", "api_response"], (
        "a trajectory_path inside the tracker's base directory must still be recorded"
    )
