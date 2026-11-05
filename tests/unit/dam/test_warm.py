"""DAM's runner warms its checkpointer and its own imports with CUGA's."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.anyio]


class _Agent:
    instances: list["_Agent"] = []

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.initialize_calls = 0
        self.graph = SimpleNamespace()
        type(self).instances.append(self)

    async def initialize(self) -> None:
        self.initialize_calls += 1

    async def aclose(self) -> None:
        return None


@pytest.mark.unit
async def test_warm_opens_the_checkpointer_before_the_default_agent(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    pytest.importorskip("langgraph.checkpoint.sqlite.aio")
    import cuga.sdk
    import cuga.backend.server.acp.runner as core_runner
    from deployment.dam.cuga_dam.runner import DamRunner

    preloaded: list[tuple[str, ...]] = []
    _Agent.instances.clear()
    monkeypatch.setenv("CUGA_FOLDER", str(tmp_path))
    monkeypatch.setattr(cuga.sdk, "CugaAgent", _Agent)
    monkeypatch.setattr(core_runner, "_preload", lambda extra: preloaded.append(extra))
    runner = DamRunner()

    await runner.warm()

    assert preloaded == [DamRunner.preload_modules]
    assert "langgraph.checkpoint.sqlite.aio" in DamRunner.preload_modules
    assert (tmp_path / "acp-checkpoints.sqlite3").exists()
    # The default agent was built after the checkpointer, so it carries it.
    assert _Agent.instances[0].kwargs["checkpointer"] is runner._checkpointer
    assert _Agent.instances[0].initialize_calls == 1
    await runner.shutdown()
