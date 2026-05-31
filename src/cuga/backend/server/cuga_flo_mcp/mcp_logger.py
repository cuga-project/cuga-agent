"""
MCP debug logger — writes all MCP API call traces to mcp_debug.log at project root.

Usage in any component:
    from cuga.backend.server.cuga_flo_mcp.mcp_logger import mcp_in, mcp_out

    mcp_in("MCPFlowBridge", "execute_task", task_id=task_id, ctx_element=ctx.element_id)
    result = await ...
    mcp_out("MCPFlowBridge", "execute_task", task_id=task_id, result_keys=list(result))
"""

from __future__ import annotations

import json
from pathlib import Path

from loguru import logger as _root_logger

_MAX_VALUE_LEN = 600


def _find_project_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / "pyproject.toml").exists():
            return parent
    return Path.cwd()


_LOG_FILE = _find_project_root() / "mcp_debug.log"
_sink_added = False


def _ensure_sink() -> None:
    global _sink_added
    if _sink_added:
        return
    _root_logger.add(
        str(_LOG_FILE),
        level="DEBUG",
        format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level:<7} | {message}",
        rotation="10 MB",
        retention=3,
        filter=lambda record: record["extra"].get("mcp_debug"),
        enqueue=True,
    )
    _sink_added = True


_ensure_sink()
_logger = _root_logger.bind(mcp_debug=True)


def _fmt(value: object) -> str:
    try:
        s = json.dumps(value, default=str, ensure_ascii=False)
    except Exception:
        s = str(value)
    return s if len(s) <= _MAX_VALUE_LEN else s[:_MAX_VALUE_LEN] + "…"


def mcp_in(component: str, tool: str, **kwargs) -> None:
    """Log an inbound MCP tool call (request side)."""
    parts = "  ".join(f"{k}={_fmt(v)}" for k, v in kwargs.items())
    _logger.debug(f"{component:<22} → {tool:<28} | {parts}")


def mcp_out(component: str, tool: str, **kwargs) -> None:
    """Log an outbound MCP tool result (response side)."""
    parts = "  ".join(f"{k}={_fmt(v)}" for k, v in kwargs.items())
    _logger.debug(f"{component:<22} ← {tool:<28} | {parts}")
