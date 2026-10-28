"""Code sandbox (execd) settings: on / off.

The connection comes from the mounted sandbox secret (or ``execd_url`` /
``execd_api_key`` in the settings), never from the UI. The UI shows the switch
only when such a connection exists, and can always switch the sandbox off.

Independent of agent config versions — a change applies to the running server
immediately and is persisted so it survives a restart.
"""

import asyncio
from typing import Any, Optional

from fastapi import HTTPException
from loguru import logger
from pydantic import BaseModel

from cuga.backend.cuga_graph.nodes.cuga_lite.executors.execd import connection
from cuga.backend.server.manage_routes.router import router
from cuga.backend.server.sandbox_store import (
    delete_sandbox_settings,
    load_sandbox_settings,
    save_sandbox_settings,
)

_LOAD_ATTEMPTS = 3
_LOAD_RETRY_DELAY_S = 1.0


class SandboxSettingsBody(BaseModel):
    enabled: bool


async def load_sandbox_settings_into_runtime() -> None:
    """Install the stored choice at startup. Missing row → env defaults apply.

    A row that cannot be read is retried, then skipped with an error in the log:
    the server starts with the sandbox off (unless ``sandbox_mode = "execd"``
    requires it), the same as before it was ever switched on in the UI. It
    stays off until switched on again, or until the next restart reads the row.
    """
    last_exc: Optional[Exception] = None
    for attempt in range(_LOAD_ATTEMPTS):
        try:
            stored = await load_sandbox_settings()
            break
        except Exception as e:
            last_exc = e
            logger.warning(f"Loading sandbox settings failed (attempt {attempt + 1}/{_LOAD_ATTEMPTS}): {e}")
            if attempt + 1 < _LOAD_ATTEMPTS:
                await asyncio.sleep(_LOAD_RETRY_DELAY_S)
    else:
        logger.error(
            f"Could not load the code-sandbox settings from the DB ({last_exc}); starting with the "
            f"sandbox {'on, as sandbox_mode requires' if connection.required_by_config() else 'off'}"
        )
        return
    connection.set_runtime_config(stored)
    if stored:
        logger.info(f"Startup: applied saved sandbox settings (enabled={connection.is_enabled()})")


async def _probe(conn: Optional[connection.SandboxConnection]) -> dict[str, Any]:
    """Check that the sandbox at ``conn`` answers, with ``conn``'s own key.

    Returns ``reachable`` and, when something is wrong, ``probe_error``: a short
    reason for the UI. Status codes and exception text go to the log only.
    """
    if conn is None or not conn.url:
        return {"reachable": None}
    import httpx

    # Lazy: the executor is heavy and this module is imported with every server.
    from cuga.backend.cuga_graph.nodes.cuga_lite.executors.execd import ExecdExecutor

    # The headers real calls send (tenant and agent for sandbox-api's logs), with
    # the key of the connection under test: it is not the active one before switching on.
    headers = ExecdExecutor._identity_headers()
    if conn.api_key:
        headers["X-API-Key"] = conn.api_key
    try:
        # Same TLS verification as real calls.
        async with httpx.AsyncClient(
            timeout=5.0, headers=headers, verify=ExecdExecutor._verify_ssl()
        ) as client:
            r = await client.get(f"{conn.url.rstrip('/')}/status")
    except Exception as e:
        logger.warning(f"[execd:conn] Sandbox probe failed: {e}")
        return {"reachable": False, "probe_error": "The sandbox does not answer"}
    if r.status_code in (401, 403):
        logger.warning(f"[execd:conn] Sandbox probe: HTTP {r.status_code}")
        reason = "API key rejected by the sandbox" if conn.api_key else "The sandbox requires an API key"
        return {"reachable": True, "probe_error": reason}
    if r.status_code >= 400:
        logger.warning(f"[execd:conn] Sandbox probe: HTTP {r.status_code}")
        return {"reachable": True, "probe_error": "The sandbox returned an error"}
    return {"reachable": True}


@router.get("/sandbox")
async def get_sandbox_settings():
    return connection.status()


async def _status_with_probe() -> dict[str, Any]:
    result = connection.status()
    if result["enabled"] and not result["error"]:
        result.update(await _probe(connection.resolve()))
    return result


async def _release_kernels() -> None:
    """Free the kernels this process holds, before the sandbox goes off and they are forgotten.

    Their files stay in the sandbox. Kernels from before a restart, or held by
    another replica, are not known here and stay until sandbox-api's own TTL.
    """
    from cuga.backend.cuga_graph.nodes.cuga_lite.executors.execd import ExecdExecutor

    threads = list(ExecdExecutor._contexts)
    for thread_id in threads:
        await ExecdExecutor.release_thread(thread_id)
    if threads:
        logger.info(f"[execd:ctx] Released {len(threads)} kernels on switching the sandbox off")


@router.put("/sandbox")
async def put_sandbox_settings(body: SandboxSettingsBody):
    """Switch the sandbox on or off. On only once the sandbox answers; nothing is saved otherwise."""
    if not body.enabled and connection.required_by_config():
        raise HTTPException(
            status_code=422, detail='The deployment requires the sandbox (sandbox_mode = "execd")'
        )
    if body.enabled:
        if not connection.available():
            raise HTTPException(
                status_code=422, detail="No sandbox is configured: the sandbox secret is not mounted"
            )
        reason = connection.problem()
        if reason is None:
            reason = (await _probe(connection.configured())).get("probe_error")
        if reason:
            raise HTTPException(status_code=422, detail=f"The sandbox was not switched on: {reason}")
    elif connection.is_enabled():
        await _release_kernels()

    stored = {"enabled": body.enabled}
    await save_sandbox_settings(stored)
    connection.set_runtime_config(stored)
    logger.info(f"Sandbox settings updated: enabled={body.enabled}")
    return await _status_with_probe()


@router.delete("/sandbox")
async def reset_sandbox_settings():
    """Forget the UI choice and fall back to the env / settings.toml defaults."""
    await delete_sandbox_settings()
    connection.set_runtime_config(None)
    return connection.status()


@router.post("/sandbox/refresh")
async def refresh_sandbox_settings():
    """Re-read the cluster secret now and check the sandbox answers."""
    connection.refresh()
    return await _status_with_probe()
