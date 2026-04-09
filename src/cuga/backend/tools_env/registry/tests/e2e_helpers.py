"""Shared helpers for registry subprocess E2E tests."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

import httpx

from cuga.config import PACKAGE_ROOT, settings

REPO_ROOT = Path(PACKAGE_ROOT).resolve().parent.parent


def get_registry_port() -> int:
    return settings.server_ports.registry


async def wait_for_http_ok(
    url: str,
    *,
    timeout_seconds: float = 120.0,
    interval: float = 1.0,
) -> None:
    deadline = time.monotonic() + timeout_seconds
    async with httpx.AsyncClient() as client:
        while time.monotonic() < deadline:
            try:
                response = await client.get(url)
                if response.status_code == 200:
                    return
            except Exception:
                pass
            await asyncio.sleep(interval)
    raise RuntimeError(f"No HTTP 200 from {url!r} within {timeout_seconds}s")
