"""ACP Protocol — outbound SDK wrapper.

Delegates a task to a remote ACP agent over the ACP SDK and returns a
normalised result dict compatible with the supervisor graph.
"""

from __future__ import annotations

import asyncio
import os
import time
from collections.abc import Callable, Mapping
from typing import Any

import httpx
from acp_sdk.client import Client
from acp_sdk.models import RunStatus
from acp_sdk.models.errors import ACPError
from loguru import logger

# ──────────────────────────────────────────────────────────────────────────────
# Constants
# ──────────────────────────────────────────────────────────────────────────────

# Maximum per-request HTTP timeout we ever pass to the SDK client.  The
# outer poll loop is bounded by the caller-supplied *timeout* parameter;
# individual HTTP calls use a smaller cap so they can't block a cancellation.
_HTTP_REQUEST_TIMEOUT = 10.0

# Terminal statuses — we stop polling immediately upon reaching any of these.
_TERMINAL_STATUSES = {RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED}

# Statuses that require us to keep polling.
_PENDING_STATUSES = {RunStatus.CREATED, RunStatus.IN_PROGRESS, RunStatus.CANCELLING}


# ──────────────────────────────────────────────────────────────────────────────
# Public function
# ──────────────────────────────────────────────────────────────────────────────


async def delegate_task_via_acp(
    *,
    endpoint: str,
    agent_name: str,
    task: str,
    auth: Mapping[str, Any] | None = None,
    timeout: float = 30.0,
    verify_tls: bool = True,
    poll_interval: float = 0.25,
    client_factory: Callable[..., Client] = Client,
) -> dict[str, Any]:
    """Delegate *task* to a remote ACP agent and return a normalised result.

    Parameters
    ----------
    endpoint:
        Base URL of the remote ACP server (e.g. ``"https://acp.example.com"``).
    agent_name:
        Name of the agent to run on the remote server.
    task:
        Plain-text task description sent to the agent as the run input.
    auth:
        Optional authentication configuration.  Supported forms:

        - ``{"type": "bearer", "token": "<token>"}`` — inline bearer token.
        - ``{"type": "bearer", "token_env_var": "<VAR>"}`` — read token from
          the named environment variable at call time.

        When *auth* is ``None`` the function checks the ``ACP_AUTH_TOKEN``
        environment variable; if set, it is used as a bearer token.
    timeout:
        Wall-clock budget in seconds for the entire operation (manifest
        discovery + run submission + polling).  When the budget is exhausted
        the remote run is cancelled and a timeout result is returned.
    verify_tls:
        Forward to the underlying HTTP client.  Set to ``False`` only for
        testing against self-signed certificates.
    poll_interval:
        Seconds to sleep between successive ``run_status`` calls.
    client_factory:
        Callable that accepts the same keyword arguments as
        ``acp_sdk.client.Client`` and returns a context-manager-compatible
        ACP client.  Exists primarily to allow test injection.

    Returns
    -------
    dict with keys ``result`` (str), ``status`` (``"success"`` or
    ``"failed"``), and ``variables`` (always ``{}``).
    """
    # ── 1. Validate configuration ────────────────────────────────────────────
    if not endpoint:
        raise ValueError("endpoint must not be empty")
    if not agent_name:
        raise ValueError("agent_name must not be empty")

    # ── 2. Resolve bearer token → headers ───────────────────────────────────
    headers: dict[str, str] = {}
    token: str | None = None

    if auth is not None:
        if auth.get("type") == "bearer":
            if auth.get("token"):
                token = str(auth["token"])
            elif auth.get("token_env_var"):
                token = os.environ.get(str(auth["token_env_var"]))
    else:
        # Fall back to well-known env variable
        token = os.environ.get("ACP_AUTH_TOKEN") or None

    if token:
        headers["Authorization"] = f"Bearer {token}"

    # ── 3. Instantiate client (bounded HTTP timeout) ─────────────────────────
    http_timeout = min(_HTTP_REQUEST_TIMEOUT, timeout)

    client = client_factory(
        base_url=endpoint,
        headers=headers,
        timeout=http_timeout,
        verify=verify_tls,
        follow_redirects=False,
    )

    run_id = None
    deadline = time.monotonic() + timeout

    async with client:
        try:
            # ── 4. Discover agent manifest ───────────────────────────────────
            try:
                manifest = await client.agent(name=agent_name)
            except LookupError:
                raise
            except Exception as exc:
                raise RuntimeError(f"Failed to look up agent '{agent_name}' on remote server.") from exc

            if manifest.name != agent_name:
                raise ValueError(
                    f"Remote server returned manifest for '{manifest.name}' but '{agent_name}' was requested."
                )

            # Verify text/plain capability (wildcards are accepted)
            def _accepts_text(types: list[str]) -> bool:
                return any(ct in ("text/plain", "*/*", "text/*") for ct in (types or []))

            if not _accepts_text(manifest.input_content_types):
                raise ValueError(f"Remote agent '{agent_name}' does not advertise text/plain input support.")
            if not _accepts_text(manifest.output_content_types):
                raise ValueError(f"Remote agent '{agent_name}' does not advertise text/plain output support.")

            # ── 5. Submit async run ──────────────────────────────────────────
            try:
                initial_run = await client.run_async(task, agent=agent_name)
            except ACPError as exc:
                logger.debug("ACP error during run_async: {}", type(exc).__name__)
                return {"result": "Remote ACP agent failed.", "status": "failed", "variables": {}}
            except httpx.HTTPError as exc:
                logger.debug("HTTP error during run_async: {}", type(exc).__name__)
                return {"result": "Remote ACP agent failed.", "status": "failed", "variables": {}}

            # ── 6. Save run_id immediately ───────────────────────────────────
            run_id = initial_run.run_id

            # ── 7. Poll until terminal, awaiting, or deadline ────────────────
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    # Total timeout elapsed
                    try:
                        await client.run_cancel(run_id=run_id)
                    except Exception:
                        pass
                    return {
                        "result": "Remote ACP agent timed out.",
                        "status": "failed",
                        "variables": {},
                    }

                try:
                    run = await client.run_status(run_id=run_id)
                except ACPError as exc:
                    logger.debug("ACP error during run_status: {}", type(exc).__name__)
                    return {
                        "result": "Remote ACP agent failed.",
                        "status": "failed",
                        "variables": {},
                    }
                except httpx.HTTPError as exc:
                    logger.debug("HTTP error during run_status: {}", type(exc).__name__)
                    return {
                        "result": "Remote ACP agent failed.",
                        "status": "failed",
                        "variables": {},
                    }

                status = run.status

                # ── 8 / 9. Completed → extract text/plain parts ──────────────
                if status == RunStatus.COMPLETED:
                    parts_text: list[str] = []
                    for msg in run.output or []:
                        msg_parts: list[str] = []
                        for part in msg.parts or []:
                            if part.content_type != "text/plain":
                                continue
                            content = part.content
                            if content is None:
                                continue
                            text_str = str(content).strip()
                            if text_str:
                                msg_parts.append(text_str)
                        if msg_parts:
                            parts_text.append(" ".join(msg_parts))
                    result_text = "\n".join(parts_text)
                    return {
                        "result": result_text,
                        "status": "success",
                        "variables": {},
                    }

                # ── 10. Failed ───────────────────────────────────────────────
                if status == RunStatus.FAILED:
                    return {
                        "result": "Remote ACP agent failed.",
                        "status": "failed",
                        "variables": {},
                    }

                # ── 11. Cancelled ────────────────────────────────────────────
                if status == RunStatus.CANCELLED:
                    return {
                        "result": "Remote ACP agent cancelled the run.",
                        "status": "failed",
                        "variables": {},
                    }

                # ── 12. Awaiting (interactive input unsupported) ─────────────
                if status == RunStatus.AWAITING:
                    return {
                        "result": (
                            "Remote ACP agent requires interactive input,"
                            " which supervisor delegation does not support."
                        ),
                        "status": "failed",
                        "variables": {},
                    }

                # Still pending (CREATED, IN_PROGRESS, CANCELLING) → sleep
                if poll_interval > 0:
                    sleep_secs = min(poll_interval, max(0.0, deadline - time.monotonic()))
                    if sleep_secs > 0:
                        await asyncio.sleep(sleep_secs)

        except asyncio.CancelledError:
            # ── 14. Coroutine cancellation ───────────────────────────────────
            if run_id is not None:
                try:
                    await client.run_cancel(run_id=run_id)
                except Exception:
                    pass
            raise
