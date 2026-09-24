"""Thin public orchestration facade for outbound ACP delegation."""

from __future__ import annotations

import asyncio
from typing import Any, Callable, Mapping
from uuid import uuid4

from .acp_client.callbacks import ACPClientCallbacks, LifecycleRegistrar, PermissionHandler
from .acp_client.config import ACPProcessConfig
from .acp_client.process import ACPStartupTimeoutError, open_acp_process_session
from .acp_client import result as normalized


async def delegate_task_via_acp(
    *,
    config: ACPProcessConfig,
    task: str,
    permission_handler: PermissionHandler | None = None,
    lifecycle_registrar: LifecycleRegistrar | None = None,
) -> dict[str, Any]:
    """Run exactly one prompt against one spawned ACP agent subprocess."""

    return await _delegate_task_via_acp(
        config=config,
        task=task,
        permission_handler=permission_handler,
        lifecycle_registrar=lifecycle_registrar,
    )


async def _delegate_task_via_acp(
    *,
    config: ACPProcessConfig,
    task: str,
    permission_handler: PermissionHandler | None = None,
    lifecycle_registrar: LifecycleRegistrar | None = None,
    process_factory: Callable[..., Any] | None = None,
    connection_factory: Callable[..., Any] | None = None,
    environ: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Dependency-injected implementation used by focused lifecycle tests."""

    if not isinstance(task, str) or not task.strip():
        raise ValueError("task must be a non-empty string")

    callbacks = ACPClientCallbacks(
        permission_handler=permission_handler,
        lifecycle_id=uuid4().hex,
        lifecycle_registrar=lifecycle_registrar,
    )
    try:
        async with open_acp_process_session(
            config,
            callbacks,
            process_factory=process_factory,
            connection_factory=connection_factory,
            environ=environ,
        ) as session:
            await callbacks.register_lifecycle(session)
            from acp.schema import TextContentBlock

            try:
                response = await asyncio.wait_for(
                    session.connection.prompt(
                        session.session_id,
                        [TextContentBlock(type="text", text=task)],
                    ),
                    timeout=config.prompt_timeout,
                )
            except (ConnectionError, EOFError, ValueError) as exc:
                if session.process.returncode is not None:
                    raise ChildProcessError("ACP subprocess exited during prompt") from exc
                raise

            if callbacks.permission_required and getattr(response, "stop_reason", None) == "cancelled":
                return normalized.permission_required()
            return normalized.success(callbacks.text)
    except asyncio.CancelledError:
        raise
    except ACPStartupTimeoutError:
        return normalized.startup_failure()
    except TimeoutError:
        return normalized.timeout()
    except ChildProcessError:
        return normalized.subprocess_exit()
    except (ConnectionError, EOFError, ValueError):
        return normalized.protocol_failure()
    except OSError:
        return normalized.startup_failure()
    except Exception:
        return normalized.protocol_failure()
