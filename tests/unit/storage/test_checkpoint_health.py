"""``/health`` and ``/health/readiness`` report the checkpointer (issue #774, W02.4).

Both payloads carry ``"checkpointer": {"kind": ..., "ok": bool}``. The field is
informational: it never changes the status code, ``status`` or ``ready``. The
route functions are called directly, so no lifespan or graph starts.
"""

from __future__ import annotations

import asyncio
import json
import time
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from cuga.backend.server import main
from cuga.backend.storage.checkpoint import CheckpointerHandle, create_checkpointer

pytestmark = pytest.mark.unit

SECRET = "s3cr3t-pw"


@pytest.fixture(autouse=True)
def _reset_handle():
    main.app_state.checkpointer_handle = None
    yield
    main.app_state.checkpointer_handle = None


def _handle(probe=None, kind: str = "postgres") -> CheckpointerHandle:
    kwargs = {"_probe": probe} if probe is not None else {}
    return CheckpointerHandle(saver=MagicMock(name="saver"), kind=kind, **kwargs)


def _body(response) -> dict:
    assert response.status_code == 200
    return json.loads(response.body)


async def _both() -> tuple[dict, dict]:
    return _body(await main.health()), _body(await main.readiness(subsystem=None))


async def test_ok_true_shape():
    async def probe() -> None:
        return None

    main.app_state.checkpointer_handle = _handle(probe, kind="postgres")

    health, ready = await _both()

    assert health["checkpointer"] == {"kind": "postgres", "ok": True}
    assert ready["checkpointer"] == {"kind": "postgres", "ok": True}
    assert health["status"] == "ok"


async def test_ok_false_shape_keeps_status_and_has_no_exception_text():
    async def probe() -> None:
        raise ConnectionError(f"could not connect: password={SECRET}")

    # Baseline with a healthy saver, so the comparison is not circular.
    main.app_state.checkpointer_handle = _handle(kind="postgres")
    healthy_health, healthy_ready = await _both()
    assert healthy_health["checkpointer"]["ok"] is True
    assert healthy_ready["checkpointer"]["ok"] is True

    main.app_state.checkpointer_handle = _handle(probe, kind="postgres")

    health, ready = await _both()

    assert health["checkpointer"] == {"kind": "postgres", "ok": False}
    assert ready["checkpointer"] == {"kind": "postgres", "ok": False}
    # Informational: a failing saver does not flip liveness or readiness.
    assert health["status"] == healthy_health["status"] == "ok"
    assert ready["status"] == healthy_ready["status"]
    assert ready["ready"] == healthy_ready["ready"]
    for payload in (health, ready):
        text = json.dumps(payload)
        assert SECRET not in text
        assert "ConnectionError" not in text


async def test_no_handle_reports_kind_none_not_ok():
    health, ready = await _both()

    assert health["checkpointer"] == {"kind": None, "ok": False}
    assert ready["checkpointer"] == {"kind": None, "ok": False}
    assert health["status"] == "ok"


async def test_slow_probe_times_out_quickly(monkeypatch):
    monkeypatch.setattr(main, "CHECKPOINTER_HEALTH_TIMEOUT_S", 0.05)

    async def probe() -> None:
        await asyncio.sleep(30)

    main.app_state.checkpointer_handle = _handle(probe, kind="sqlite")

    started = time.monotonic()
    health = _body(await main.health())
    elapsed = time.monotonic() - started

    assert health["checkpointer"] == {"kind": "sqlite", "ok": False}
    assert elapsed < 2.0


async def test_default_timeout_is_one_second():
    seen: list[float] = []

    class _Spy:
        kind = "postgres"

        async def ping(self, timeout: float = 99.0) -> bool:
            seen.append(timeout)
            return True

    main.app_state.checkpointer_handle = _Spy()

    assert (await main._checkpointer_health()) == {"kind": "postgres", "ok": True}
    assert seen == [1.0]


async def test_raising_ping_is_contained():
    class _Broken:
        kind = "postgres"

        async def ping(self, timeout: float = 1.0) -> bool:
            raise RuntimeError(SECRET)

    main.app_state.checkpointer_handle = _Broken()

    result = await main._checkpointer_health()

    assert result == {"kind": "postgres", "ok": False}


async def test_memory_handle_ok_while_open_then_not_after_close():
    handle = await create_checkpointer(SimpleNamespace(checkpointer="memory"))
    main.app_state.checkpointer_handle = handle

    assert (await main._checkpointer_health()) == {"kind": "memory", "ok": True}

    await handle.aclose()

    assert (await main._checkpointer_health()) == {"kind": "memory", "ok": False}


async def test_real_sqlite_handle_ok(tmp_path):
    storage = SimpleNamespace(
        checkpointer="durable",
        mode="local",
        checkpoint_db_path=str(tmp_path / "checkpoints.db"),
        postgres_url="",
    )
    handle = await create_checkpointer(storage)
    main.app_state.checkpointer_handle = handle
    try:
        assert (await main._checkpointer_health()) == {"kind": "sqlite", "ok": True}
    finally:
        await handle.aclose()
    assert (await main._checkpointer_health()) == {"kind": "sqlite", "ok": False}


async def test_subsystem_readiness_does_not_probe():
    calls: list[float] = []

    class _Spy:
        kind = "postgres"

        async def ping(self, timeout: float = 1.0) -> bool:
            calls.append(timeout)
            return True

    main.app_state.checkpointer_handle = _Spy()

    body = _body(await main.readiness(subsystem="knowledge"))

    assert "checkpointer" not in body
    assert calls == []
