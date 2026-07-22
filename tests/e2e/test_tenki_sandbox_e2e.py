from __future__ import annotations

import importlib.metadata
import json
import os
import time
import uuid

import pytest
from packaging.version import Version

from cuga.backend.cuga_graph.nodes.cuga_agent_core.tools.runtime_tools import (
    build_runtime_tools,
    resolve_runtime_backends,
)
from cuga.backend.cuga_graph.nodes.cuga_lite.executors.code_executor import CodeExecutor
from cuga.backend.cuga_graph.nodes.cuga_lite.executors.tenki import TenkiSandboxExecutor
from cuga.backend.server import workspace_upload
from cuga.backend.server.workspace_sandbox import (
    fetch_sandbox_workspace_tree,
    read_sandbox_workspace_bytes,
    sandbox_text_preview,
)
from cuga.config import settings


@pytest.mark.e2e
@pytest.mark.asyncio
@pytest.mark.parametrize("run_number", [1, 2])
async def test_tenki_runtime_workspace_and_cleanup_e2e(monkeypatch, tmp_path, run_number):
    if not os.getenv("TENKI_API_KEY"):
        pytest.skip("TENKI_API_KEY is required")

    assert Version(importlib.metadata.version("tenki-sandbox")) >= Version("0.4.0")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DYNACONF_ADVANCED_FEATURES__SANDBOX_MODE", "tenki")
    monkeypatch.setenv("DYNACONF_ADVANCED_FEATURES__ENABLE_SHELL_TOOL", "true")
    monkeypatch.setenv("DYNACONF_ADVANCED_FEATURES__ENABLE_FILESYSTEM_TOOLS", "true")
    monkeypatch.setenv("DYNACONF_ADVANCED_FEATURES__TENKI_SANDBOX_MAX_DURATION", "300")
    monkeypatch.setenv("DYNACONF_ADVANCED_FEATURES__TENKI_SANDBOX_IDLE_TIMEOUT_MINUTES", "2")
    settings.reload()

    thread_id = f"tenki-e2e-{run_number}-{uuid.uuid4().hex[:12]}"
    executor = TenkiSandboxExecutor()
    monkeypatch.setattr(CodeExecutor, "_tenki_executor", executor)
    sandbox_id = None

    try:
        backends = resolve_runtime_backends(settings, {})
        bundle = build_runtime_tools(thread_id=thread_id, backends=backends)
        assert backends.shell == "tenki"
        assert backends.filesystem == "sandbox_remote"

        started = time.monotonic()
        output = await bundle.execution_callables["run_command"](
            "echo '{\"value\":42}' > result.json && "
            "python3 -c 'import json; print(json.load(open(\"result.json\"))[\"value\"])'"
        )
        startup_seconds = time.monotonic() - started
        print(f"tenki run {run_number} startup_seconds={startup_seconds:.2f}")
        assert "42" in output
        sandbox_id = executor._sandboxes[thread_id].id

        write_result = await bundle.execution_callables["write_file"](
            "/workspace/note.txt", "hello from cuga"
        )
        assert "File written" in write_result
        assert await bundle.execution_callables["read_file"]("/workspace/note.txt") == "hello from cuga"

        upload = await workspace_upload.upload_workspace_bytes(
            thread_id, "payload.json", json.dumps({"ok": True}).encode()
        )
        assert upload["sandbox_path"] == "/workspace/uploads/payload.json"
        assert await sandbox_text_preview(thread_id, upload["path"]) == '{"ok": true}'
        data, name = await read_sandbox_workspace_bytes(thread_id, "/workspace/result.json")
        assert json.loads(data) == {"value": 42}
        assert name == "result.json"

        tree = await fetch_sandbox_workspace_tree(thread_id)
        assert any(node["name"] == "result.json" for node in tree)
        assert any(node["name"] == "uploads" for node in tree)

        failure = await bundle.execution_callables["run_command"]("echo integration-failure >&2; exit 17")
        assert "integration-failure" in failure
        assert "exit code 17" in failure
    finally:
        await workspace_upload.delete_thread_uploads(thread_id)
        await executor.release_sandbox(thread_id)

    from tenki_sandbox import AsyncClient

    verifier = AsyncClient()
    try:
        active = await verifier.list(tags=["cuga-agent"])
        assert sandbox_id is None or all(sandbox.id != sandbox_id for sandbox in active)
    finally:
        await verifier.close()
