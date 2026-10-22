"""Code sandbox (execd) settings: on / off, plus URL and key when no secret is mounted.

Independent of agent config versions — a change applies to the running server
immediately and is persisted so it survives a restart.
"""

import asyncio
from typing import Any, Optional

from fastapi import HTTPException
from loguru import logger
from pydantic import BaseModel

from cuga.backend.cuga_graph.nodes.cuga_lite.executors.execd import ExecdExecutor, connection
from cuga.backend.server.error_responses import safe_http_exception
from cuga.backend.server.manage_routes.router import router
from cuga.backend.server.sandbox_store import (
    API_KEY_SECRET_ID,
    delete_sandbox_settings,
    load_sandbox_settings,
    save_sandbox_settings,
)
from cuga.backend.storage import secrets_store

_LOAD_ATTEMPTS = 3
_LOAD_RETRY_DELAY_S = 1.0


class SandboxSettingsBody(BaseModel):
    enabled: bool
    # Used only when no secret is mounted.
    url: Optional[str] = None
    # Empty/None keeps the stored key; a value replaces it.
    api_key: Optional[str] = None
    clear_api_key: bool = False


async def load_sandbox_settings_into_runtime() -> None:
    """Install the stored choice at startup. Missing row → env defaults apply.

    A row that exists but cannot be read is not a missing row: starting without
    it could turn a sandbox the operator enabled in Manage off, and run code on
    the host with nothing to say so. Retry, then fail startup.
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
        raise RuntimeError("Could not load the code-sandbox settings from the DB") from last_exc
    connection.set_runtime_config(stored)
    try:
        await connection.load_manual_key()
    except Exception as e:
        logger.warning(f"Failed to load the sandbox API key from the secrets store: {e}")
    if stored:
        logger.info(f"Startup: applied saved sandbox settings (enabled={connection.is_enabled()})")


async def _probe(url: Optional[str]) -> dict[str, Any]:
    if not url:
        return {"reachable": None}
    try:
        # Same client as real calls: API key, tenant/agent headers, same TLS verification.
        async with ExecdExecutor._client(5.0) as client:
            r = await client.get(f"{url.rstrip('/')}/status")
        result: dict[str, Any] = {"reachable": True, "status_code": r.status_code}
        if r.status_code in (401, 403):
            conn = connection.resolve()
            result["auth_error"] = (
                "API key rejected by the sandbox"
                if conn and conn.api_key
                else "The sandbox requires an API key, but none is configured"
            )
        return result
    except Exception as e:
        return {"reachable": False, "probe_error": str(e)}


@router.get("/sandbox")
async def get_sandbox_settings():
    return connection.status()


async def _status_with_probe() -> dict[str, Any]:
    result = connection.status()
    if result["enabled"] and not result["error"]:
        result.update(await _probe(result.get("effective_url")))
    return result


@router.put("/sandbox")
async def put_sandbox_settings(body: SandboxSettingsBody):
    """Switch the sandbox on or off; when on, check right away that it answers."""
    if not body.enabled and connection.required_by_config():
        raise HTTPException(
            status_code=422, detail='The deployment requires the sandbox (sandbox_mode = "execd")'
        )

    previous = connection.get_runtime_config() or {}
    # Keep the URL/key so switching off and on again does not require re-entering them.
    stored: dict[str, Any] = {k: previous[k] for k in ("url", "api_key_ref") if k in previous}
    stored["enabled"] = body.enabled
    new_key: Optional[str] = None

    if body.enabled and not connection.uses_secret():
        url = (body.url if body.url is not None else connection.manual_config()["url"]).strip()
        if not url:
            raise HTTPException(
                status_code=422, detail="No sandbox secret is mounted; enter the URL and API key"
            )
        stored["url"] = url
        api_key_ref = "" if body.clear_api_key else connection.manual_config()["api_key_ref"]
        if body.api_key:
            try:
                await secrets_store.set_secret(
                    API_KEY_SECRET_ID, body.api_key, description="Code sandbox (execd) API key"
                )
            except RuntimeError as e:
                # No CUGA_SECRET_KEY: refuse rather than store the key in plain text.
                raise safe_http_exception(
                    e,
                    status=503,
                    log=logger,
                    message="Cannot store the API key: CUGA_SECRET_KEY is not set",
                )
            api_key_ref = f"db://{API_KEY_SECRET_ID}"
            new_key = body.api_key
        stored["api_key_ref"] = api_key_ref

    await save_sandbox_settings(stored)
    connection.set_runtime_config(stored)
    if new_key is not None:
        connection.prime_manual_key(stored["api_key_ref"], new_key)
    else:
        await connection.load_manual_key()
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
    """Re-read the cluster secret / stored key now and check the sandbox answers."""
    connection.refresh()
    await connection.load_manual_key()
    return await _status_with_probe()
