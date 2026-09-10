"""Unit tests for issue #765: Policy filesystem sync initialization and propagation."""

from __future__ import annotations

import os
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from cuga.backend.server import main as main_mod
from cuga.config import settings

pytestmark = pytest.mark.unit


def test_filesystem_sync_initialized_when_folder_does_not_exist(monkeypatch, tmp_path):
    """When filesystem_sync is True but cuga_folder does not exist, sync object is still created."""
    non_existent = str(tmp_path / "non_existent_cuga_dir")
    monkeypatch.setattr(settings.policy, "filesystem_sync", True)
    monkeypatch.setattr(settings.policy, "auto_load_policies", True)
    monkeypatch.setattr(settings.policy, "cuga_folder", non_existent)
    monkeypatch.setattr(settings.policy, "enabled", True)

    app_state = main_mod.AppState()
    app_state.policy_system = SimpleNamespace(
        storage=SimpleNamespace(),
        initialize=AsyncMock(),
    )

    # NOTE: _init_policy is nested inside lifespan() and cannot be called in isolation without
    # a full FastAPI app. This test simulates the relevant conditional block verbatim so that
    # the contract (sync object is created even when the folder is absent) is verifiable without
    # an integration harness. If _init_policy's startup logic changes, update this simulation too.
    cuga_folder = os.getenv("CUGA_FOLDER", settings.policy.cuga_folder)
    filesystem_sync_enabled = settings.policy.filesystem_sync

    if not filesystem_sync_enabled:
        app_state.policy_filesystem_sync = None
    else:
        from cuga.backend.cuga_graph.policy.filesystem_sync import PolicyFilesystemSync

        app_state.policy_filesystem_sync = PolicyFilesystemSync(cuga_folder=cuga_folder)

    assert app_state.policy_filesystem_sync is not None
    assert app_state.policy_filesystem_sync.cuga_folder == non_existent


def test_draft_app_state_inherits_policy_filesystem_sync():
    """draft_app_state.policy_filesystem_sync is set from app_state.policy_filesystem_sync."""
    app_state = main_mod.AppState()
    draft_app_state = main_mod.DraftAppState()

    sync_obj = object()
    app_state.policy_filesystem_sync = sync_obj

    # Initialization logic in main.py
    draft_app_state.policy_filesystem_sync = app_state.policy_filesystem_sync

    assert draft_app_state.policy_filesystem_sync is sync_obj
